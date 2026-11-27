from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, Update
from telegram.ext import ContextTypes

from config.chats import get_all_bindings, get_all_thread_bindings
from monitoring.constants import AVAILABLE_BLOCKS, BLOCK_LABELS
from monitoring import broadcasts
from monitoring.managers import get_manager_blocks, is_owner, list_managers
from monitoring.markets import get_market
from monitoring.shift_reports import list_report_chats

# telegram_user_id (str) владельца -> {"target_id": int, "target_name": str} —
# ждём текст личного сообщения для конкретного сотрудника
_awaiting_dm: dict[str, dict] = {}

# telegram_user_id (str) владельца -> {"chat_id": int, "chat_label": str} —
# ждём текст сообщения для конкретного зарегистрированного чата
_awaiting_chat_message: dict[str, dict] = {}



def _active_managers() -> list[dict]:
    return [m for m in list_managers() if m["status"] == "active"]


def _managers_pick_keyboard(managers: list[dict], prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"{m['name']} ({m['position'] or '—'})", callback_data=f"{prefix}:{m['telegram_user_id']}")] for m in managers]
    )


async def on_message_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/message — владелец выбирает активного сотрудника и пишет ему личное
    сообщение через бота (полезно, если у самого владельца нет с ним личной
    переписки в Telegram)."""
    if not is_owner(update.effective_user.id):
        return
    broadcasts.pause(update.effective_user.id)
    managers = _active_managers()
    if not managers:
        await update.effective_message.reply_text("Пока нет ни одного активного сотрудника.")
        return
    await update.effective_message.reply_text("Кому написать?", reply_markup=_managers_pick_keyboard(managers, "msg_pick"))


async def on_message_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    target_id = int(query.data.split(":", 1)[1])
    managers = {m["telegram_user_id"]: m for m in _active_managers()}
    manager = managers.get(target_id)
    if not manager:
        await query.answer("Сотрудник не найден или больше не активен", show_alert=True)
        return
    _awaiting_dm[str(query.from_user.id)] = {"target_id": target_id, "target_name": manager["name"]}
    await query.answer()
    await query.edit_message_text(f"Напиши сообщение для {manager['name']} — перешлю от твоего имени:")


async def on_message_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текст личного сообщения владельца сотруднику. Возвращает
    True, если сообщение обработано — по конвенции остальных
    claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_dm.pop(owner_id, None)
    if not state:
        return False

    text = update.effective_message.text or ""
    try:
        await context.bot.send_message(chat_id=state["target_id"], text=f"На связи Havana Standart!\n\n{text}")
        await update.effective_message.reply_text(f"✅ Отправлено {state['target_name']}.")
    except Exception as e:
        await update.effective_message.reply_text(f"⚠️ Не смог отправить {state['target_name']}: {e}")
    return True


def _registered_chats() -> list[dict]:
    """Рабочие чаты проектов (/register_project) и чаты команды точки
    (/register_report_chat, роль team) — куда владелец может написать
    напрямую. Чат финпартнёров сюда не входит: там внешние люди, писать
    туда через эту команду не предполагается."""
    chats = [
        {"chat_id": chat_id, "message_thread_id": None, "label": f"📋 {project} — рабочий чат"}
        for chat_id, project, _source in get_all_bindings()
    ]
    chats += [
        {"chat_id": chat_id, "message_thread_id": thread_id, "label": f"📋 {project} — рабочий чат (ветка)"}
        for chat_id, thread_id, project in get_all_thread_bindings()
    ]
    for row in list_report_chats():
        if row["role"] != "team":
            continue
        market = get_market(row["market_id"])
        market_name = market["name"] if market else f"#{row['market_id']}"
        chats.append(
            {"chat_id": row["chat_id"], "message_thread_id": row.get("message_thread_id"), "label": f"👥 {market_name} — команда точки"}
        )
    return chats


def _chat_key(chat: dict) -> str:
    """chat_id один в один не годится в ключ: у чата с ветками разные
    записи (рабочий чат проекта / чат команды точки) могут физически жить
    в одном chat_id, но в разных топиках — различаем по паре chat_id+ветка."""
    thread = chat.get("message_thread_id")
    return f"{chat['chat_id']}:{thread if thread is not None else 0}"


def _chats_pick_keyboard(chats: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(c["label"], callback_data=f"msgchat_pick:{_chat_key(c)}")] for c in chats])


async def on_message_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/message_chat — владелец выбирает зарегистрированный чат (рабочий
    чат проекта или чат команды точки) и пишет туда сообщение через бота."""
    if not is_owner(update.effective_user.id):
        return
    broadcasts.pause(update.effective_user.id)
    chats = _registered_chats()
    if not chats:
        await update.effective_message.reply_text("Пока нет ни одного зарегистрированного чата.")
        return
    await update.effective_message.reply_text("В какой чат написать?", reply_markup=_chats_pick_keyboard(chats))


async def on_message_chat_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    key = query.data.split(":", 1)[1]
    chats = {_chat_key(c): c for c in _registered_chats()}
    chat = chats.get(key)
    if not chat:
        await query.answer("Чат больше не зарегистрирован", show_alert=True)
        return
    _awaiting_chat_message[str(query.from_user.id)] = {
        "chat_id": chat["chat_id"],
        "message_thread_id": chat.get("message_thread_id"),
        "chat_label": chat["label"],
    }
    await query.answer()
    await query.edit_message_text(f"Напиши сообщение для «{chat['label']}» — отправлю от твоего имени:")


async def on_message_chat_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текст сообщения владельца для зарегистрированного чата.
    Возвращает True, если сообщение обработано — по конвенции остальных
    claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_chat_message.pop(owner_id, None)
    if not state:
        return False

    text = update.effective_message.text or ""
    try:
        await context.bot.send_message(
            chat_id=state["chat_id"], text=f"На связи Havana Standart!\n\n{text}", message_thread_id=state.get("message_thread_id")
        )
        await update.effective_message.reply_text(f"✅ Отправлено в «{state['chat_label']}».")
    except Exception as e:
        await update.effective_message.reply_text(f"⚠️ Не смог отправить в «{state['chat_label']}»: {e}")
    return True


def _scope_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👥 Все активные сотрудники", callback_data="bcast_scope:all")],
            [InlineKeyboardButton("🏪 По рынку/проекту", callback_data="bcast_scope:market")],
            [InlineKeyboardButton("🎓 По должности", callback_data="bcast_scope:position")],
            [InlineKeyboardButton("🧩 По блоку бота", callback_data="bcast_scope:block")],
        ]
    )


async def on_broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast — владелец шлёт одно сообщение сразу группе сотрудников,
    аудитория настраивается на месте: все, по рынку, по должности или по
    выданному блоку бота."""
    if not is_owner(update.effective_user.id):
        return
    if not _active_managers():
        await update.effective_message.reply_text("Пока нет ни одного активного сотрудника.")
        return
    draft=broadcasts.resume(update.effective_user.id)
    if draft and draft['state'] in ('review','failed'):
        await _preview_broadcast(update.effective_message,context,draft);return
    if draft and draft['state']=='composing':
        names=', '.join(m['name'] for m in draft['audience'])
        await update.effective_message.reply_text('Продолжаем черновик. Получатели: '+names+'\nПришли текст и/или PDF; перед отправкой покажу предпросмотр.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отмена',callback_data='bcast_cancel:'+draft['token'])]]));return
    if draft and draft['state']=='sending':
        await update.effective_message.reply_text('Рассылка уже отправляется.');return
    await update.effective_message.reply_text("Кому шлём рассылку?", reply_markup=_scope_keyboard())


async def _start_broadcast_text(message, owner_id: str, audience: list[dict]) -> None:
    if not audience:
        await message.reply_text("По этому критерию не нашлось ни одного активного сотрудника.")
        return
    _awaiting_dm.pop(owner_id,None)
    _awaiting_chat_message.pop(owner_id,None)
    draft=broadcasts.start(int(owner_id),[{'telegram_user_id':m['telegram_user_id'],'name':m['name']} for m in audience])
    if draft is None:
        await message.reply_text('Сначала заверши текущую отправку или отмени неотправленный остаток.');return
    names = ", ".join(m["name"] for m in audience)
    await message.reply_text(f"Получатели ({len(audience)}): {names}\n\nПришли текст и/или PDF. Можно одним сообщением с подписью или отдельно. Затем покажу предпросмотр. Пока ты не нажмешь «Отправить», получателям ничего не уйдет.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отменить рассылку',callback_data='bcast_cancel:'+draft['token'])]]))


async def on_broadcast_scope_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    scope = query.data.split(":", 1)[1]
    await query.answer()

    if scope == "all":
        await query.edit_message_text("Аудитория: все активные сотрудники")
        await _start_broadcast_text(query.message, str(query.from_user.id), _active_managers())
        return

    if scope == "market":
        markets = {mk["id"]: mk for m in _active_managers() for mk in m["markets"]}
        if not markets:
            await query.edit_message_text("Ни у одного активного сотрудника нет привязанного рынка.")
            return
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(mk["name"], callback_data=f"bcast_market:{mk['id']}")] for mk in markets.values()]
        )
        await query.edit_message_text("По какому рынку?", reply_markup=keyboard)
        return

    if scope == "position":
        positions = sorted({m["position"] for m in _active_managers() if m["position"]})
        if not positions:
            await query.edit_message_text("Ни у одного активного сотрудника не указана должность.")
            return
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(p, callback_data=f"bcast_position:{p}")] for p in positions])
        await query.edit_message_text("По какой должности?", reply_markup=keyboard)
        return

    if scope == "block":
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(BLOCK_LABELS[b], callback_data=f"bcast_block:{b}")] for b in AVAILABLE_BLOCKS]
        )
        await query.edit_message_text("По какому блоку бота?", reply_markup=keyboard)
        return


async def on_broadcast_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    audience = [m for m in _active_managers() if any(mk["id"] == market_id for mk in m["markets"])]
    market_name = next((mk["name"] for m in audience for mk in m["markets"] if mk["id"] == market_id), f"#{market_id}")
    await query.answer()
    await query.edit_message_text(f"Аудитория: рынок «{market_name}»")
    await _start_broadcast_text(query.message, str(query.from_user.id), audience)


async def on_broadcast_position_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    position = query.data.split(":", 1)[1]
    audience = [m for m in _active_managers() if m["position"] == position]
    await query.answer()
    await query.edit_message_text(f"Аудитория: должность «{position}»")
    await _start_broadcast_text(query.message, str(query.from_user.id), audience)


async def on_broadcast_block_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    block = query.data.split(":", 1)[1]
    audience = [m for m in _active_managers() if block in get_manager_blocks(m["telegram_user_id"])]
    await query.answer()
    await query.edit_message_text(f"Аудитория: блок «{BLOCK_LABELS.get(block, block)}»")
    await _start_broadcast_text(query.message, str(query.from_user.id), audience)


def _broadcast_confirm_keyboard(draft):
    token=draft['token']
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('✅ Отправить' if draft['state']!='failed' else 'Повторить недоставленное',callback_data='bcast_confirm:'+token)],
        [InlineKeyboardButton('❌ Отмена',callback_data='bcast_cancel:'+token)],
    ])


def _parts(draft):
    value=draft['content'];parts=[]
    if value['text']:parts.append(('text',value))
    parts.extend(('document',doc) for doc in value['documents'])
    return parts


async def _send_part(bot,chat_id,part):
    kind,value=part
    if kind=='text':
        entities=[MessageEntity.de_json(e,bot) for e in value['entities']]
        return await bot.send_message(chat_id=chat_id,text=value['text'],entities=entities,parse_mode=None)
    return await bot.send_document(chat_id=chat_id,document=value['file_id'],filename=value['file_name'])


async def _preview_broadcast(message,context,draft):
    names=', '.join(m['name'] for m in draft['audience'])
    if draft['state']=='failed':
        await message.reply_text('Предыдущая отправка завершилась частично. Уже доставленные части повторно не отправляем. Получатели: '+names,reply_markup=_broadcast_confirm_keyboard(draft));return
    await message.reply_text(f"Предпросмотр рассылки. Получатели ({len(draft['audience'])}): {names}\nНиже текст и файлы — пока только тебе.")
    for part in _parts(draft):await _send_part(context.bot,draft['owner_id'],part)
    await message.reply_text('Можно дописать/заменить текст или прикрепить PDF отдельным сообщением. После изменения старая кнопка отправки перестанет действовать. Отправить текущую версию?',reply_markup=_broadcast_confirm_keyboard(draft))


async def on_broadcast_text_reply(update,context):
    return await _broadcast_content(update,context)


async def on_broadcast_document(update,context):
    return await _broadcast_content(update,context)


async def _broadcast_content(update,context):
    uid=update.effective_user.id
    if not is_owner(uid):return False
    draft=broadcasts.get(uid)
    if not draft or draft['state'] not in ('composing','review','failed','sending'):return False
    message=update.effective_message
    if draft['state'] in ('failed','sending'):
        await message.reply_text('Сначала заверши повторную доставку или отмени текущую рассылку. Текст и файлы не изменены.');return True
    text=getattr(message,'text',None)
    if text and text.strip().lower() in ('отмена','/cancel'):
        broadcasts.cancel(uid,draft['token']);await message.reply_text('Рассылка отменена. Ничего нового не отправлено.');return True
    value=dict(draft['content']);value['documents']=list(value['documents'])
    document=getattr(message,'document',None)
    caption=getattr(message,'caption',None)
    if document:
        if getattr(message,'media_group_id',None):
            await message.reply_text('Пришли PDF отдельным файлом, не альбомом. Черновик сохранен.');return True
        if len(value['documents'])>=10 and all(d['file_id']!=document.file_id for d in value['documents']):
            await message.reply_text('В одной рассылке можно прикрепить до 10 документов.');return True
        if all(d['file_id']!=document.file_id for d in value['documents']):value['documents'].append({'file_id':document.file_id,'file_name':document.file_name or 'document.pdf'})
    if text is not None or caption is not None:
        value['text']=text if text is not None else caption
        if len(value['text'].encode('utf-16-le'))//2>4096:
            await message.reply_text('Текст слишком длинный для одного сообщения Telegram. Сократи его до 4096 символов. Черновик не изменен.');return True
        entities=getattr(message,'entities',None) if text is not None else getattr(message,'caption_entities',None)
        value['entities']=[entity.to_dict() for entity in (entities or [])]
    if not value['text'] and not value['documents']:return False
    draft=broadcasts.content(uid,draft['token'],value)
    if not draft:
        await message.reply_text('Версия уже изменилась. Открой /broadcast.');return True
    try:await _preview_broadcast(message,context,draft)
    except Exception:
        await message.reply_text('Черновик сохранен, но предпросмотр не доставлен. Открой /broadcast и проверь его перед отправкой.')
    return True


async def on_broadcast_confirm(update,context):
    query=update.callback_query;uid=query.from_user.id
    if not is_owner(uid):await query.answer('Нет доступа',show_alert=True);return
    token=query.data.partition(':')[2]
    draft=broadcasts.get(uid)
    if not token or not draft or draft['token']!=token or draft['state'] not in ('review','failed'):
        await query.answer('Предпросмотр устарел или уже отправлен',show_alert=True);return
    current={m['telegram_user_id'] for m in _active_managers()}
    removed=[m['name'] for m in draft['audience'] if m['telegram_user_id'] not in current]
    if removed:
        await query.answer()
        await query.message.reply_text('Доступ получателей изменился: '+', '.join(removed)+'. Отмени эту рассылку и выбери получателей заново. Ничего не отправлено.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Отмена',callback_data='bcast_cancel:'+token)]]));return
    if not broadcasts.claim(uid,token):await query.answer('Эта версия уже отправляется',show_alert=True);return
    try:await query.answer('Рассылаю…')
    except Exception:
        broadcasts.finish(uid,token,'failed');return
    done=broadcasts.delivered(token);parts=_parts(draft);failed=[]
    for manager in draft['audience']:
        target=manager['telegram_user_id']
        for index,part in enumerate(parts):
            if (target,index) in done:continue
            try:
                await _send_part(context.bot,target,part)
                broadcasts.record(token,target,index);done.add((target,index))
            except Exception:
                failed.append(manager['name']);break
    broadcasts.finish(uid,token,'failed' if failed else 'completed')
    sent_count=len(draft['audience'])-len(failed)
    result=f"✅ Полностью доставлено {sent_count} из {len(draft['audience'])}."
    if failed:result+='\nНе все части доставлены: '+', '.join(failed)+'. Уже доставленное при повторе не дублируется.'
    await query.edit_message_text(result,reply_markup=_broadcast_confirm_keyboard(broadcasts.get(uid)) if failed else None)


async def on_broadcast_cancel(update,context):
    query=update.callback_query;uid=query.from_user.id
    if not is_owner(uid):await query.answer('Нет доступа',show_alert=True);return
    token=query.data.partition(':')[2]
    if not token or not broadcasts.cancel(uid,token):
        await query.answer('Версия устарела или уже отправляется',show_alert=True);return
    await query.answer()
    await query.edit_message_text('Отменено. Новых отправок не будет; если ранее была частичная доставка, доставленные сообщения у получателей сохраняются.')


async def on_command_navigation(update,context):
    """Новая команда не должна превращать ответы другого сценария в текст рассылки."""
    text=(update.effective_message.text or '').split(maxsplit=1)[0]
    command=text.split('@')[0].lower()
    from portfolio.storage import dialog as portfolio_dialog
    portfolio_dialog(update.effective_user.id,None)
    if command not in ('/broadcast','/cancel'):
        broadcasts.pause(update.effective_user.id)
