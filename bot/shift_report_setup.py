import re
from datetime import timedelta

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.onboarding import get_display_name
from config.settings import ROMAN_TELEGRAM_ID, SHIFT_REPORT_ESCALATE_OFFSET_MINUTES
from config.timeutil import add_minutes_to_hhmm, fmt_date
from config.timeutil import today as tz_today
from monitoring.constants import BLOCK_REPORTS
from monitoring.managers import get_managers_for_market, get_markets_for_manager, is_owner, is_reports_editor
from monitoring.markets import get_effective_shift_report_time, get_market, list_markets, set_market_shift_report_time
from monitoring.shift_reports import get_report_chat
from monitoring.shift_schedule import get_scheduled_manager

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

# План на месяц ушёл из чек-листа — теперь бот сам забирает его из Surf
# Coffee (см. revenue.daily_plan), Управляющему вносить нечего.
_STEPS = ["managers", "team_chat", "schedule", "final"]

# telegram_user_id (str) управляющего/владельца -> {"market_id": int} — ждём время сбора отчёта
_awaiting_time: dict[str, dict] = {}


def _available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"evrep_market:{m['id']}")] for m in markets])


def _next_keyboard(market_id: int, next_step: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Дальше", callback_data=f"evrep_next:{market_id}:{next_step}")]])


def _check_managers(market: dict) -> tuple[bool, str]:
    managers = get_managers_for_market(market["id"])
    ready = [m for m in managers if m["status"] == "active" and BLOCK_REPORTS in (m.get("blocks") or "").split(",")]
    if ready:
        names = ", ".join(m["name"] for m in ready)
        return True, f"1️⃣ Сотрудники в боте — ✅\nМогут сдавать вечерний отчёт: {names}."
    return False, (
        "1️⃣ Сотрудники в боте — ❌\n"
        "Пока ни один активный сотрудник этой точки не может сдавать вечерний отчёт — без этого автоматический сбор "
        "просто не с кем будет вести.\n\n"
        "Что сделать: пусть сотрудник, который будет сдавать отчёты, напишет боту /start и пройдёт онбординг на эту "
        "точку, а дальше подтверди его в /employees (и выдай блок «Отчёты по смене», если он не выдан по умолчанию)."
    )


def _check_team_chat(market: dict) -> tuple[bool, str]:
    chat = get_report_chat(market["id"], "team")
    if chat:
        return True, "2️⃣ Чат команды точки — ✅\nБот уже привязан и будет присылать туда отчёт каждый день."
    return False, (
        "2️⃣ Чат команды точки — ❌\n"
        "Ещё не привязан — без этого вечерний отчёт для команды каждый день будет некуда отправлять.\n\n"
        "Что сделать:\n1. Добавь меня в чат команды этой точки.\n"
        f"2. В этом чате отправь команду /register_report_chat, выбери рынок «{market['name']}» и роль «👥 Команда точки»."
    )


def _check_schedule(market: dict) -> tuple[bool, str]:
    today = tz_today()
    missing = [d for d in ((today + timedelta(days=i)).isoformat() for i in range(14)) if not get_scheduled_manager(market["id"], d)]
    if not missing:
        return True, "3️⃣ График ответственных — ✅\nЗаполнен минимум на 2 недели вперёд."
    return False, (
        f"3️⃣ График ответственных — ❌ (не хватает {len(missing)} дн., начиная с {fmt_date(missing[0])})\n\n"
        "Что сделать: команда /set_shift_schedule — пришли построчно «ДД.ММ.ГГГГ - @username» минимум на 2 недели вперёд."
    )


_CHECKS = {
    "managers": _check_managers,
    "team_chat": _check_team_chat,
    "schedule": _check_schedule,
}

_STEP_WARN_ROMAN = {
    "managers": "ещё нет сотрудников с доступом к отчётам по смене",
    "team_chat": "ещё не привязал чат команды точки",
    "schedule": "ещё не заполнил график ответственных на 2 недели вперёд",
}


def _final_text(market: dict) -> str:
    time_str = get_effective_shift_report_time(market)
    escalate = add_minutes_to_hhmm(time_str, SHIFT_REPORT_ESCALATE_OFFSET_MINUTES)
    if market.get("send_to_finance", 1):
        finance_line = "Он реально уходит в чат финпартнёров этой точки, как только Рома его согласует."
    else:
        finance_line = (
            "Этот рынок сейчас не подключён к чату финпартнёров (это решает Рома) — отчёт для финпартнёров идёт "
            "только Роме для ознакомления, без обязательного согласования, но он может задать тебе уточняющий вопрос."
        )
    return (
        "4️⃣ Как это будет работать дальше\n\n"
        f"• Каждый день в {time_str} я сам напишу сотруднику по графику с кнопками «📝 Заполню сам» / «🚫 Не работаю сегодня».\n"
        f"• Если к {escalate} отчёт так и не начат — подключу тебя, с теми же двумя кнопками.\n"
        "• План на день (выручка/чеки) я сам беру из системы учёта Surf Coffee — вносить его больше не нужно.\n"
        "• Как только анкета заполнена — отчёт для команды точки уходит в чат сразу, без согласований, каждый день без исключений.\n"
        f"• Отчёт для финпартнёров сначала приходит на согласование тебе — можно поправить любое поле. {finance_line}\n\n"
        "Настройка сохранена. Если что-то из пунктов выше ещё не готово — донастрой в любой момент и просто "
        "запусти /set_evening_report ещё раз, я проверю заново."
    )


async def _notify_roman_if_not_ready(bot: Bot, user_id: int, market: dict, step: str) -> None:
    if is_owner(user_id):
        return
    try:
        await bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text=f"⚠️ {get_display_name(user_id)} настраивает вечерний отчёт на «{market['name']}», но {_STEP_WARN_ROMAN[step]}.",
        )
    except Exception:
        pass


async def _show_step(bot: Bot, message, user_id: int, market: dict, step: str) -> None:
    if step == "final":
        await message.reply_text(_final_text(market))
        return
    ok, text = _CHECKS[step](market)
    next_step = _STEPS[_STEPS.index(step) + 1]
    await message.reply_text(text, reply_markup=_next_keyboard(market["id"], next_step))
    if not ok:
        await _notify_roman_if_not_ready(bot, user_id, market, step)


async def _ask_time(message, user_id: str, market: dict) -> None:
    _awaiting_time[user_id] = {"market_id": market["id"]}
    current = get_effective_shift_report_time(market)
    await message.reply_text(
        f"Настраиваем вечерний отчёт для «{market['name']}».\n\n"
        "Во сколько на этой точке обычно нужно начинать сбор отчёта у ответственного (когда смена уже закончилась)?\n\n"
        f"Сейчас: {current}. Пришли новое время в формате ЧЧ:ММ, например 22:00 — или напиши «оставить», чтобы не менять."
    )


async def on_set_evening_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_evening_report — управляющий настраивает время сбора вечернего
    отчёта для своей точки и проходит чек-лист готовности (сотрудники в
    боте, чат команды точки, график ответственных). План на месяц в
    чек-лист не входит — бот сам забирает его из Surf Coffee."""
    user = update.effective_user
    if not is_reports_editor(user.id):
        await update.effective_message.reply_text(
            "Настраивать вечерний отчёт может только владелец или Управляющий с выданным блоком «Отчёты по смене»."
        )
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _ask_time(update.effective_message, str(user.id), markets[0])
        return

    await update.effective_message.reply_text("По какому рынку настраиваем вечерний отчёт?", reply_markup=_market_pick_keyboard(markets))


async def on_set_evening_report_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _ask_time(query.message, str(query.from_user.id), market)


async def on_set_evening_report_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает время сбора отчёта. Возвращает True, если сообщение
    обработано — по конвенции остальных claim-хендлеров в on_private_text."""
    user_id = str(update.effective_user.id)
    state = _awaiting_time.get(user_id)
    if not state:
        return False

    market = get_market(state["market_id"])
    del _awaiting_time[user_id]
    if not market:
        await update.effective_message.reply_text("Рынок больше недоступен.")
        return True

    text = (update.effective_message.text or "").strip()
    if text.lower() not in ("оставить", "-"):
        if not _TIME_RE.match(text):
            _awaiting_time[user_id] = state
            await update.effective_message.reply_text("🤔 Нужен формат ЧЧ:ММ, например 22:00 — или «оставить», чтобы не менять.")
            return True
        set_market_shift_report_time(market["id"], text)
        market = get_market(market["id"])
        await update.effective_message.reply_text(f"✅ Время сбора отчёта для «{market['name']}»: {text}.")

    await _show_step(context.bot, update.effective_message, update.effective_user.id, market, "managers")
    return True


async def on_set_evening_report_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, market_id_str, step = query.data.split(":", 2)
    market = get_market(int(market_id_str))
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await _show_step(context.bot, query.message, query.from_user.id, market, step)
