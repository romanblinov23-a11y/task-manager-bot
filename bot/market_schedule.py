from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.constants import WEEKDAY_LABELS
from monitoring.managers import get_markets_for_manager, is_active_manager, is_owner
from monitoring.markets import get_market, list_markets
from monitoring.schedule import get_schedule, set_schedule

# user_id (str) -> {"market_id": int, "selected": set[int]}
_pending: dict[str, dict] = {}


def _available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"sched_market:{m['id']}")] for m in markets])


def _schedule_keyboard(market_id: int, selected: set[int]) -> InlineKeyboardMarkup:
    day_buttons = [
        InlineKeyboardButton(
            f"{'✅ ' if day in selected else ''}{WEEKDAY_LABELS[day - 1]}",
            callback_data=f"sched_day:{market_id}:{day}",
        )
        for day in range(1, 8)
    ]
    rows = [day_buttons[:4], day_buttons[4:]]
    rows.append([InlineKeyboardButton("✔️ Готово", callback_data=f"sched_done:{market_id}")])
    return InlineKeyboardMarkup(rows)


async def _start_schedule_flow(message, user_id: str, market: dict) -> None:
    existing = get_schedule(market["id"])
    selected = set(existing["weekdays"]) if existing else set()
    _pending[user_id] = {"market_id": market["id"], "selected": selected}
    await message.reply_text(
        f"В какие дни недели присылать задание на мониторинг конкурентов по рынку «{market['name']}»?\n"
        "Выберите один или несколько дней и нажмите «Готово».",
        reply_markup=_schedule_keyboard(market["id"], selected),
    )


async def on_schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not (is_owner(user.id) or is_active_manager(user.id)):
        await update.effective_message.reply_text(
            "Эта команда доступна только подтверждённым владельцем менеджерам."
        )
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _start_schedule_flow(update.effective_message, str(user.id), markets[0])
        return

    await update.effective_message.reply_text("По какому рынку настроить расписание?", reply_markup=_market_pick_keyboard(markets))


async def on_schedule_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _start_schedule_flow(query.message, user_id, market)


async def on_schedule_day_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    _, market_id_str, day_str = query.data.split(":", 2)
    market_id, day = int(market_id_str), int(day_str)

    pending = _pending.get(user_id)
    if not pending or pending["market_id"] != market_id:
        await query.answer("Эта сессия настройки уже неактуальна, начните заново через /schedule", show_alert=True)
        return

    if day in pending["selected"]:
        pending["selected"].discard(day)
    else:
        pending["selected"].add(day)

    await query.answer()
    await query.edit_message_reply_markup(reply_markup=_schedule_keyboard(market_id, pending["selected"]))


async def on_schedule_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = str(query.from_user.id)
    market_id = int(query.data.split(":", 1)[1])

    pending = _pending.get(user_id)
    if not pending or pending["market_id"] != market_id:
        await query.answer("Эта сессия настройки уже неактуальна, начните заново через /schedule", show_alert=True)
        return

    if not pending["selected"]:
        await query.answer("Выберите хотя бы один день", show_alert=True)
        return

    set_schedule(market_id, list(pending["selected"]))
    del _pending[user_id]
    await query.answer()
    await query.edit_message_text("В выбранные дни я буду присылать задание на мониторинг.")
