from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.managers import get_markets_for_manager, is_owner, is_reports_editor
from monitoring.markets import get_market, list_markets
from monitoring.shift_reports import delete_report_chat, get_report_chat, list_report_chats, set_report_chat

_ROLE_LABELS = {"finance": "💰 Финпартнёры", "team": "👥 Команда точки"}

_TEAM_CHAT_GREETING = (
    "Привет, Серферы! Рад быть с вами в чате, я хоть и искусственный, но очень добрый 🙈\n\n"
    "Тут я для того, чтобы все оставались в едином информационном поле и буду помогать вам ничего не забыть. "
    "Я буду писать вам утром и вечером - очень прошу, не оставляйте мои сообщения без внимания, ведь единая "
    "информационная среда поможет нам достичь еще более крутых результатов и покорить самые большие волны 🌊"
)

# Управляющий (не владелец) может привязывать только чат команды точки —
# чат финпартнёров (с тегом и финансовыми данными) остаётся только владельцу.
_SUPERVISOR_ALLOWED_ROLES = ("team",)

# telegram_user_id (str) владельца -> {"market_id", "role", "chat_id", "market_name"} —
# ждём текст, кого тегнуть первой строкой в отчёте для этого чата
_awaiting_mention: dict[str, dict] = {}


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"shrc_market:{m['id']}")] for m in markets])


def _role_pick_keyboard(market_id: int, roles: tuple[str, ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(_ROLE_LABELS[role], callback_data=f"shrc_role:{market_id}:{role}")] for role in roles]
    )


def _allowed_markets(user_id: int) -> list[dict]:
    """Владелец видит и привязывает любой рынок; Управляющий — только
    рынок(и), где он сам Управляющий (см. is_reports_editor)."""
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


async def on_register_report_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/register_report_chat — владелец или Управляющий рынка вызывает
    внутри группы, чтобы привязать её как получателя ежедневного отчёта по
    смене (не как рабочий чат проекта — сюда бот только рассылает готовые
    отчёты, переписку не разбирает на задачи). Управляющий может привязать
    только чат команды точки, чат финпартнёров — только владелец."""
    if not is_reports_editor(update.effective_user.id):
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("Эта команда работает только внутри группового чата.")
        return

    markets = _allowed_markets(update.effective_user.id)
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного доступного вам рынка.")
        return

    await update.effective_message.reply_text(
        "Для какого рынка этот чат будет получать отчёты?", reply_markup=_market_pick_keyboard(markets)
    )


async def on_register_report_chat_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_reports_editor(query.from_user.id):
        await query.answer()
        return

    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    allowed_ids = {m["id"] for m in _allowed_markets(query.from_user.id)}
    if not market or market_id not in allowed_ids:
        await query.answer("Рынок не найден", show_alert=True)
        return

    roles = _ROLE_LABELS.keys() if is_owner(query.from_user.id) else _SUPERVISOR_ALLOWED_ROLES
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}. Какой это чат?", reply_markup=_role_pick_keyboard(market_id, tuple(roles)))


async def on_register_report_chat_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_reports_editor(query.from_user.id):
        await query.answer()
        return

    _, market_id_str, role = query.data.split(":")
    market_id = int(market_id_str)
    market = get_market(market_id)
    allowed_ids = {m["id"] for m in _allowed_markets(query.from_user.id)}
    is_supervisor = not is_owner(query.from_user.id)
    if not market or market_id not in allowed_ids or (is_supervisor and role not in _SUPERVISOR_ALLOWED_ROLES):
        await query.answer("Недоступно", show_alert=True)
        return

    chat_id = query.message.chat.id
    await query.answer()

    if role == "finance":
        # Спрашиваем в личке владельца, а не в самой группе — там сидят
        # сами финпартнёры, и настроечный диалог не должен идти у них на виду.
        owner_id = query.from_user.id
        _awaiting_mention[str(owner_id)] = {
            "market_id": market_id,
            "role": role,
            "chat_id": chat_id,
            "market_name": market["name"],
        }
        await query.edit_message_text(f"✅ Этот чат привязан для «{market['name']}» — {_ROLE_LABELS[role]}. Донастрою в личке.")
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=(
                    f"Кого тегнуть первой строкой в отчёте для чата «{market['name']}» (например, "
                    "@Motus_control_group_bot)? Если не нужно — напишите «нет»."
                ),
            )
        except Exception:
            await query.message.reply_text(
                "Не смог написать вам в личку — откройте диалог со мной (/start) и повторите /register_report_chat."
            )
        return

    set_report_chat(market_id, role, chat_id)
    await query.edit_message_text(f"✅ Этот чат будет получать отчёты «{market['name']}» — {_ROLE_LABELS[role]}.")
    if role == "team":
        # Представляемся команде сразу при привязке — это их первый контакт
        # с ботом в этом чате (см. bot.chat_registration для аналогичной
        # логики у рабочего чата проекта).
        try:
            await context.bot.send_message(chat_id=chat_id, text=_TEAM_CHAT_GREETING)
        except Exception:
            pass


async def on_register_report_chat_mention_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текст, кого тегать первой строкой отчёта для финпартнёров.
    Возвращает True, если сообщение обработано — по конвенции остальных
    claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_mention.pop(owner_id, None)
    if not state:
        return False

    text = (update.effective_message.text or "").strip()
    mention = "" if text.lower() in ("нет", "не надо", "-") else text
    if mention and not mention.startswith("@"):
        mention = f"@{mention}"

    set_report_chat(state["market_id"], state["role"], state["chat_id"], mention)
    suffix = f" Первой строкой будет: {mention}" if mention else ""
    await update.effective_message.reply_text(
        f"✅ Этот чат будет получать отчёты «{state['market_name']}» — {_ROLE_LABELS[state['role']]}.{suffix}"
    )
    return True


def _chat_list_keyboard(chats: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for c in chats:
        market = get_market(c["market_id"])
        market_name = market["name"] if market else f"#{c['market_id']}"
        buttons.append(
            [InlineKeyboardButton(f"{market_name} — {_ROLE_LABELS[c['role']]}", callback_data=f"shrc_view:{c['market_id']}:{c['role']}")]
        )
    return InlineKeyboardMarkup(buttons)


def _chat_card_text(chat_row: dict, market_name: str, role: str) -> str:
    mention_line = f"\nТег первой строкой: {chat_row['mention']}" if chat_row.get("mention") else ""
    return f"{market_name} — {_ROLE_LABELS[role]}\nChat ID: {chat_row['chat_id']}{mention_line}"


def _chat_card_keyboard(market_id: int, role: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑 Отвязать", callback_data=f"shrc_unbind:{market_id}:{role}")],
            [InlineKeyboardButton("↩️ К списку", callback_data="shrc_list")],
        ]
    )


async def on_report_chats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/report_chats — владелец смотрит и отвязывает чаты, привязанные к
    рассылке отчётов по сменам (финпартнёры/команда точки)."""
    if not is_owner(update.effective_user.id):
        return
    chats = list_report_chats()
    if not chats:
        await update.effective_message.reply_text("Пока нет привязанных чатов для отчётов по сменам.")
        return
    await update.effective_message.reply_text(
        "Привязанные чаты для отчётов по сменам:", reply_markup=_chat_list_keyboard(chats)
    )


async def on_report_chats_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    chats = list_report_chats()
    await query.answer()
    if not chats:
        await query.edit_message_text("Пока нет привязанных чатов для отчётов по сменам.")
        return
    await query.edit_message_text("Привязанные чаты для отчётов по сменам:", reply_markup=_chat_list_keyboard(chats))


async def on_report_chats_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    _, market_id_str, role = query.data.split(":")
    market_id = int(market_id_str)
    row = get_report_chat(market_id, role)
    market = get_market(market_id)
    if not row or not market:
        await query.answer("Не найдено — возможно, уже отвязано", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(_chat_card_text(row, market["name"], role), reply_markup=_chat_card_keyboard(market_id, role))


async def on_report_chats_unbind(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    _, market_id_str, role = query.data.split(":")
    market_id = int(market_id_str)
    market = get_market(market_id)
    delete_report_chat(market_id, role)
    await query.answer("Отвязано")
    market_name = market["name"] if market else f"#{market_id}"
    await query.edit_message_text(f"✅ Чат отвязан от отчётов «{market_name}» — {_ROLE_LABELS[role]}.")
