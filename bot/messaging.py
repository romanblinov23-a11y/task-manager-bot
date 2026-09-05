from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.chats import get_all_bindings, get_all_thread_bindings
from monitoring.constants import AVAILABLE_BLOCKS, BLOCK_LABELS
from monitoring.managers import get_manager_blocks, is_owner, list_managers
from monitoring.markets import get_market
from monitoring.shift_reports import list_report_chats

# telegram_user_id (str) владельца -> {"target_id": int, "target_name": str} —
# ждём текст личного сообщения для конкретного сотрудника
_awaiting_dm: dict[str, dict] = {}

# telegram_user_id (str) владельца -> {"chat_id": int, "chat_label": str} —
# ждём текст сообщения для конкретного зарегистрированного чата
_awaiting_chat_message: dict[str, dict] = {}

# telegram_user_id (str) владельца -> [{"telegram_user_id", "name"}, ...] —
# аудитория выбрана, ждём текст рассылки
_awaiting_broadcast_text: dict[str, list[dict]] = {}

# telegram_user_id (str) владельца -> {"audience": [...], "text": str} —
# текст рассылки набран, ждём подтверждения
_pending_broadcast: dict[str, dict] = {}


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
    await update.effective_message.reply_text("Кому шлём рассылку?", reply_markup=_scope_keyboard())


async def _start_broadcast_text(message, owner_id: str, audience: list[dict]) -> None:
    if not audience:
        await message.reply_text("По этому критерию не нашлось ни одного активного сотрудника.")
        return
    _awaiting_broadcast_text[owner_id] = audience
    names = ", ".join(m["name"] for m in audience)
    await message.reply_text(f"Получатели ({len(audience)}): {names}\n\nНапиши текст рассылки:")


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


def _broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Отправить", callback_data="bcast_confirm"), InlineKeyboardButton("❌ Отмена", callback_data="bcast_cancel")]]
    )


async def on_broadcast_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текст рассылки после того, как аудитория уже выбрана.
    Возвращает True, если сообщение обработано — по конвенции остальных
    claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    audience = _awaiting_broadcast_text.pop(owner_id, None)
    if audience is None:
        return False

    text = update.effective_message.text or ""
    _pending_broadcast[owner_id] = {"audience": audience, "text": text}
    names = ", ".join(m["name"] for m in audience)
    await update.effective_message.reply_text(
        f"Получатели ({len(audience)}): {names}\n\nТекст:\n{text}\n\nОтправляем?",
        reply_markup=_broadcast_confirm_keyboard(),
    )
    return True


async def on_broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    owner_id = str(query.from_user.id)
    state = _pending_broadcast.pop(owner_id, None)
    if not state:
        await query.answer("Сессия неактуальна", show_alert=True)
        return

    await query.answer("Рассылаю…")
    text = f"На связи Havana Standart!\n\n{state['text']}"
    failed = []
    for manager in state["audience"]:
        try:
            await context.bot.send_message(chat_id=manager["telegram_user_id"], text=text)
        except Exception:
            failed.append(manager["name"])

    sent_count = len(state["audience"]) - len(failed)
    result = f"✅ Отправлено {sent_count} из {len(state['audience'])}."
    if failed:
        result += f"\nНе доставлено: {', '.join(failed)}."
    await query.edit_message_text(result)


async def on_broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _pending_broadcast.pop(str(query.from_user.id), None)
    await query.answer()
    await query.edit_message_text("Отменено, рассылка не отправлена.")
