from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.timeutil import fmt_date, parse_date, today
from monitoring.competitors import list_competitors
from monitoring.managers import is_owner
from monitoring.markets import get_market, list_markets
from monitoring.readings import get_latest_reading, update_reading

# telegram_user_id (str) владельца -> {"market_id": int, "competitor_id": int, "reading_id": int} —
# ждём новую дату для конкретного снятия
_awaiting_date: dict[str, dict] = {}


def _market_pick_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(m["name"], callback_data=f"fixr_market:{m['id']}")] for m in list_markets()]
    )


def _competitor_pick_keyboard(market_id: int) -> InlineKeyboardMarkup:
    buttons = []
    for c in list_competitors(market_id, include_closed=True):
        latest = get_latest_reading(c["id"])
        if not latest:
            continue
        flag = " ⚠️" if latest["reading_at"] > today().isoformat() else ""
        buttons.append(
            [InlineKeyboardButton(f"{c['name']} — {latest['reading_at']}{flag}", callback_data=f"fixr_pick:{market_id}:{c['id']}")]
        )
    return InlineKeyboardMarkup(buttons)


async def on_fix_reading_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/fix_reading — владелец точечно исправляет дату последнего снятия
    конкретной точки, не сбрасывая весь рынок через /reset_monitoring.
    Нужна, когда снятие однажды внесли с неверной датой (например, в
    будущем) — раньше это можно было поправить только прямым запросом
    к базе."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка.")
        return
    await update.effective_message.reply_text("На каком рынке исправить дату снятия?", reply_markup=_market_pick_keyboard())


async def on_fix_reading_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return

    keyboard = _competitor_pick_keyboard(market_id)
    await query.answer()
    if not keyboard.inline_keyboard:
        await query.edit_message_text(f"На рынке «{market['name']}» пока нет ни одного снятия.")
        return
    await query.edit_message_text(f"У какой точки на «{market['name']}» исправить дату последнего снятия?", reply_markup=keyboard)


async def on_fix_reading_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    _, market_id_str, competitor_id_str = query.data.split(":")
    market_id, competitor_id = int(market_id_str), int(competitor_id_str)
    latest = get_latest_reading(competitor_id)
    if not latest:
        await query.answer("Снятий не найдено", show_alert=True)
        return

    owner_id = str(query.from_user.id)
    _awaiting_date[owner_id] = {"market_id": market_id, "competitor_id": competitor_id, "reading_id": latest["id"]}
    await query.answer()
    await query.edit_message_text(
        f"Текущая дата снятия: {latest['reading_at']} (значение {latest['avg_checks_per_day']:g} чек/день).\n"
        "Введите правильную дату, например 15.08.2026:"
    )


async def on_fix_reading_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает исправленную дату снятия для /fix_reading. Возвращает True,
    если сообщение обработано — по конвенции остальных claim-хендлеров в
    on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_date.get(owner_id)
    if not state:
        return False

    text = (update.effective_message.text or "").strip()
    iso = parse_date(text)
    if not iso:
        await update.effective_message.reply_text(f"Не понял дату «{text}». Попробуйте ещё раз, например 15.08.2026:")
        return True
    if iso > today().isoformat():
        await update.effective_message.reply_text(f"Дата «{text}» — в будущем, так не может быть. Введите дату ещё раз:")
        return True

    del _awaiting_date[owner_id]
    update_reading(state["reading_id"], reading_at=iso)
    await update.effective_message.reply_text(f"✅ Дата снятия исправлена: {fmt_date(iso)}.")
    return True
