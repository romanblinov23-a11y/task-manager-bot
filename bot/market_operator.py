from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.managers import get_managers_for_market, is_owner
from monitoring.markets import get_market, list_markets, set_market_operator
from personal_data.consent import has_consent

_FIELD_KEYS = {
    "название": "operator_name",
    "наименование": "operator_name",
    "инн": "operator_inn",
    "огрн": "operator_ogrn",
    "огрнип": "operator_ogrn",
    "адрес": "operator_address",
}

# telegram_user_id (str) владельца -> {"market_id": int} — ждём вставки реквизитов
_awaiting_paste: dict[str, dict] = {}

# telegram_user_id (str) -> {"market_id": int, "fields": dict} — реквизиты распознаны, ждём подтверждения
_pending_confirm: dict[str, dict] = {}


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"setop_market:{m['id']}")] for m in markets])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Сохранить", callback_data="setop_confirm"), InlineKeyboardButton("❌ Отмена", callback_data="setop_cancel")]]
    )


def _instructions_text(market_name: str) -> str:
    return (
        f"Пришли реквизиты юрлица/ИП — оператора персональных данных для «{market_name}» — "
        "одним сообщением, по строке на поле:\n\n"
        "Название: ООО «Ромашка»\n"
        "ИНН: 1234567890\n"
        "ОГРН: 1234567890123\n"
        "Адрес: г. Москва, ул. Примерная, д. 1\n\n"
        "Это нужно для текста согласия на обработку персональных данных, который увидят стажёры "
        "этой точки — оператором является само юрлицо/ИП, а не Рома и не бот."
    )


async def on_set_operator_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_operator — владелец указывает реквизиты юрлица/ИП, которое
    юридически владеет точкой (оператор персональных данных стажёров этой
    точки) — нужно для текста согласия на обработку ПДн."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка.")
        return
    if len(markets) == 1:
        _awaiting_paste[str(update.effective_user.id)] = {"market_id": markets[0]["id"]}
        await update.effective_message.reply_text(_instructions_text(markets[0]["name"]))
        return
    await update.effective_message.reply_text("По какому рынку указываем реквизиты?", reply_markup=_market_pick_keyboard(markets))


async def on_set_operator_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    _awaiting_paste[str(query.from_user.id)] = {"market_id": market_id}
    await query.message.reply_text(_instructions_text(market["name"]))


def _parse_operator_paste(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in text.splitlines():
        if ":" not in raw_line:
            continue
        label, value = raw_line.split(":", 1)
        key = _FIELD_KEYS.get(label.strip().lower())
        if key and value.strip():
            fields[key] = value.strip()
    return fields


async def on_set_operator_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает вставленный текст реквизитов. Возвращает True, если
    сообщение обработано — по конвенции остальных claim-хендлеров в
    on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_paste.get(owner_id)
    if not state:
        return False

    text = update.effective_message.text or ""
    fields = _parse_operator_paste(text)
    del _awaiting_paste[owner_id]

    missing = [label for label, key in {"Название": "operator_name", "ИНН": "operator_inn", "Адрес": "operator_address"}.items() if key not in fields]
    if missing:
        _awaiting_paste[owner_id] = state
        await update.effective_message.reply_text(
            f"🤔 Не хватает полей: {', '.join(missing)}. Проверь формат и вставь текст ещё раз."
        )
        return True

    market = get_market(state["market_id"])
    _pending_confirm[owner_id] = {"market_id": state["market_id"], "fields": fields}
    lines = [f"Реквизиты для «{market['name']}»:"]
    lines.append(f"Название: {fields.get('operator_name', '—')}")
    lines.append(f"ИНН: {fields.get('operator_inn', '—')}")
    lines.append(f"ОГРН(ИП): {fields.get('operator_ogrn', '—')}")
    lines.append(f"Адрес: {fields.get('operator_address', '—')}")
    lines.append("")
    lines.append("Сохранить?")
    await update.effective_message.reply_text("\n".join(lines), reply_markup=_confirm_keyboard())
    return True


async def _unblock_waiting_trainees(bot, market_id: int) -> None:
    """После того как реквизиты заполнены, стажёры этой точки, которые уже
    были подтверждены владельцем, но ждали текст согласия (см.
    bot.trainee_onboarding.start_trainee_track), получают его сейчас же —
    не нужно ничего донбордивать вручную."""
    from bot.trainee_onboarding import start_trainee_track

    for manager in get_managers_for_market(market_id):
        if manager["status"] != "active" or manager["position"] != "Стажёр":
            continue
        if has_consent(manager["telegram_user_id"]):
            continue
        await start_trainee_track(bot, manager["telegram_user_id"])


async def on_set_operator_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    owner_id = str(query.from_user.id)
    state = _pending_confirm.pop(owner_id, None)
    if not state:
        await query.answer("Сессия неактуальна", show_alert=True)
        return

    await query.answer("Сохраняю…")
    fields = state["fields"]
    set_market_operator(
        state["market_id"],
        fields.get("operator_name", ""),
        fields.get("operator_inn", ""),
        fields.get("operator_ogrn", ""),
        fields.get("operator_address", ""),
    )
    market = get_market(state["market_id"])
    await query.edit_message_text(f"✅ Реквизиты сохранены для «{market['name']}».")
    await _unblock_waiting_trainees(context.bot, state["market_id"])


async def on_set_operator_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _pending_confirm.pop(str(query.from_user.id), None)
    await query.answer()
    await query.edit_message_text("Отменено, реквизиты не сохранены.")
