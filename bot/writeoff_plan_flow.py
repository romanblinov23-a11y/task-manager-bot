import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.managers import is_owner
from monitoring.markets import get_market, list_markets
from monitoring.writeoff_plan import get_writeoff_plan, set_writeoff_plan

_FIELD_KEYS = {
    "срок годности": "expiry_pct",
    "комплимент": "compliment_pct",
    "питание": "staff_meals_pct",
}
_FIELD_LABELS = {"expiry_pct": "Срок годности", "compliment_pct": "Комплимент", "staff_meals_pct": "Питание"}

# telegram_user_id (str) владельца -> {"market_id": int} — ждём вставки норм списаний
_awaiting_paste: dict[str, dict] = {}

# telegram_user_id (str) -> {"market_id": int, "fields": dict} — нормы распознаны, ждём подтверждения
_pending_confirm: dict[str, dict] = {}


def _parse_percent(text: str) -> float | None:
    cleaned = re.sub(r"\s", "", text).replace("%", "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if 0 <= value < 100 else None


def _format_pct(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"writeoffplan_market:{m['id']}")] for m in markets])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Сохранить", callback_data="writeoffplan_confirm"), InlineKeyboardButton("❌ Отмена", callback_data="writeoffplan_cancel")]]
    )


def _instructions_text(market_name: str, current: dict | None) -> str:
    lines = [
        f"Пришли норму списаний для «{market_name}» — одним сообщением, по строке на поле, "
        "% от дневной выручки:",
        "",
        "Срок годности: 1%",
        "Комплимент: 0,5%",
        "Питание: 1,4%",
        "",
        "Плановую сумму в рублях бот сам посчитает от выручки — утром от плановой (для сообщения "
        "команде), вечером от фактической (для отчёта).",
    ]
    if current:
        lines.append("")
        lines.append(
            f"Сейчас: срок годности {_format_pct(current['expiry_pct'])} / "
            f"комплимент {_format_pct(current['compliment_pct'])} / "
            f"питание {_format_pct(current['staff_meals_pct'])}"
        )
    return "\n".join(lines)


async def _start_paste(message, user_id: str, market: dict) -> None:
    _awaiting_paste[user_id] = {"market_id": market["id"]}
    await message.reply_text(_instructions_text(market["name"], get_writeoff_plan(market["id"])))


async def on_set_writeoff_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_writeoff_plan — владелец задаёт норму списаний (срок годности/
    комплимент/питание) по точке — % от выручки, одна норма на рынок, не
    по дням. Плановая сумма в рублях считается от выручки конкретного дня
    (см. monitoring.writeoff_plan.writeoff_plan_amounts) — так она сама
    растёт/падает вместе с потоком, а не остаётся вчерашней фикс. суммой.
    От неё зависит отклонение факт/план в отчёте и утреннем сообщении
    команде (см. bot/shift_reports.py). В отличие от плана по выручке/
    чекам, доступна для любого рынка независимо от привязки к Surf Coffee."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка.")
        return
    if len(markets) == 1:
        await _start_paste(update.effective_message, str(update.effective_user.id), markets[0])
        return
    await update.effective_message.reply_text("По какому рынку задаём норму списаний?", reply_markup=_market_pick_keyboard(markets))


async def on_set_writeoff_plan_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await _start_paste(query.message, str(query.from_user.id), market)


async def on_set_writeoff_plan_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает вставленный текст норм списаний. Возвращает True, если
    сообщение обработано — по конвенции остальных claim-хендлеров в
    on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_paste.get(owner_id)
    if not state:
        return False

    text = update.effective_message.text or ""
    fields: dict[str, float] = {}
    for raw_line in text.splitlines():
        if ":" not in raw_line:
            continue
        label, value = raw_line.split(":", 1)
        key = _FIELD_KEYS.get(label.strip().lower())
        pct = _parse_percent(value)
        if key and pct is not None:
            fields[key] = pct
    del _awaiting_paste[owner_id]

    missing = [label for key, label in _FIELD_LABELS.items() if key not in fields]
    if missing:
        _awaiting_paste[owner_id] = state
        await update.effective_message.reply_text(
            f"🤔 Не хватает или не распознал полей: {', '.join(missing)}. "
            "Проверь формат (число от 0 до 99, например 1,4%) и вставь текст ещё раз."
        )
        return True

    market = get_market(state["market_id"])
    _pending_confirm[owner_id] = {"market_id": state["market_id"], "fields": fields}
    lines = [
        f"Норма списаний для «{market['name']}» (% от выручки):",
        f"Срок годности: {_format_pct(fields['expiry_pct'])}",
        f"Комплимент: {_format_pct(fields['compliment_pct'])}",
        f"Питание: {_format_pct(fields['staff_meals_pct'])}",
        "",
        "Сохранить?",
    ]
    await update.effective_message.reply_text("\n".join(lines), reply_markup=_confirm_keyboard())
    return True


async def on_set_writeoff_plan_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    owner_id = str(query.from_user.id)
    state = _pending_confirm.pop(owner_id, None)
    if not state:
        await query.answer("Сессия неактуальна", show_alert=True)
        return

    await query.answer("Сохраняю…")
    set_writeoff_plan(
        state["market_id"],
        state["fields"]["expiry_pct"],
        state["fields"]["compliment_pct"],
        state["fields"]["staff_meals_pct"],
    )
    await query.edit_message_text("✅ Норма списаний сохранена.")


async def on_set_writeoff_plan_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _pending_confirm.pop(str(query.from_user.id), None)
    await query.answer()
    await query.edit_message_text("Отменено, норма не сохранена.")
