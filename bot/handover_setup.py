import re
from datetime import timedelta

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.timeutil import fmt_date
from config.timeutil import today as tz_today
from monitoring.constants import BLOCK_HANDOVER
from monitoring.handover_schedule import get_scheduled_handover_manager
from monitoring.managers import get_managers_for_market, get_markets_for_manager, is_handover_editor, is_owner
from monitoring.markets import get_effective_handover_time, get_market, list_markets, set_market_handover_time
from monitoring.shift_reports import get_report_chat

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

_STEPS = ["managers", "team_chat", "schedule", "final"]

# telegram_user_id (str) управляющего/владельца -> {"market_id": int} — ждём время запроса пересменки
_awaiting_time: dict[str, dict] = {}


def _available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"hosetup_market:{m['id']}")] for m in markets])


def _next_keyboard(market_id: int, next_step: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Дальше", callback_data=f"hosetup_next:{market_id}:{next_step}")]])


def _check_managers(market: dict) -> tuple[bool, str]:
    managers = get_managers_for_market(market["id"])
    ready = [m for m in managers if m["status"] == "active" and BLOCK_HANDOVER in (m.get("blocks") or "").split(",")]
    if ready:
        names = ", ".join(m["name"] for m in ready)
        return True, f"1️⃣ Сотрудники в боте — ✅\nМогут сдавать пересменку: {names}."
    return False, (
        "1️⃣ Сотрудники в боте — ❌\n"
        "Пока ни один активный сотрудник этой точки не может сдавать пересменку — без этого автоматический сбор "
        "просто не с кем будет вести.\n\n"
        "Что сделать: подтверди сотрудника в /employees и выдай блок «Пересменка», если он не выдан."
    )


def _check_team_chat(market: dict) -> tuple[bool, str]:
    chat = get_report_chat(market["id"], "team")
    if chat:
        return True, "2️⃣ Чат команды точки — ✅\nБот уже привязан и будет присылать туда пересменку."
    return False, (
        "2️⃣ Чат команды точки — ❌\n"
        "Ещё не привязан — без этого пересменку каждый день будет некуда отправлять. Тот же чат, что и для "
        "вечернего отчёта — если он уже настроен через /set_evening_report, повторно привязывать не нужно.\n\n"
        "Что сделать:\n1. Добавь меня в чат команды этой точки.\n"
        f"2. В этом чате отправь команду /register_report_chat, выбери рынок «{market['name']}» и роль «👥 Команда точки»."
    )


def _check_schedule(market: dict) -> tuple[bool, str]:
    today = tz_today()
    missing = [
        d for d in ((today + timedelta(days=i)).isoformat() for i in range(14)) if not get_scheduled_handover_manager(market["id"], d)
    ]
    if not missing:
        return True, "3️⃣ График пересменок — ✅\nЗаполнен минимум на 2 недели вперёд."
    return False, (
        f"3️⃣ График пересменок — ❌ (не хватает {len(missing)} дн., начиная с {fmt_date(missing[0])})\n\n"
        "Что сделать: команда /set_handover_schedule — пришли построчно «ДД.ММ.ГГГГ - @username» минимум на 2 недели вперёд."
    )


_CHECKS = {
    "managers": _check_managers,
    "team_chat": _check_team_chat,
    "schedule": _check_schedule,
}


def _final_text(market: dict) -> str:
    time_str = get_effective_handover_time(market)
    return (
        "4️⃣ Как это будет работать дальше\n\n"
        f"• Каждый день в {time_str} я сам напишу сотруднику по графику пересменок с кнопками "
        "«📝 Заполню сам» / «🚫 Не работаю сегодня».\n"
        "• Если сотрудник ответит «не работаю» (или недоступен) — передам управляющему, с теми же кнопками.\n"
        "• Как только анкета заполнена — пересменка уходит в чат команды точки сразу, без согласований.\n\n"
        "Настройка сохранена. Если что-то из пунктов выше ещё не готово — донастрой в любой момент и просто "
        "запусти /set_handover_report ещё раз, я проверю заново."
    )


async def _show_step(message, market: dict, step: str) -> None:
    if step == "final":
        await message.reply_text(_final_text(market))
        return
    ok, text = _CHECKS[step](market)
    next_step = _STEPS[_STEPS.index(step) + 1]
    await message.reply_text(text, reply_markup=_next_keyboard(market["id"], next_step))


async def _ask_time(message, user_id: str, market: dict) -> None:
    _awaiting_time[user_id] = {"market_id": market["id"]}
    current = get_effective_handover_time(market)
    await message.reply_text(
        f"Настраиваем пересменку для «{market['name']}».\n\n"
        "Во сколько на этой точке обычно нужно запрашивать пересменку?\n\n"
        f"Сейчас: {current}. Пришли новое время в формате ЧЧ:ММ, например 15:00 — или напиши «оставить», чтобы не менять."
    )


async def on_set_handover_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_handover_report — управляющий настраивает время запроса
    пересменки для своей точки и проходит чек-лист готовности (сотрудники
    в боте, чат команды точки, график пересменок)."""
    user = update.effective_user
    if not is_handover_editor(user.id):
        await update.effective_message.reply_text(
            "Настраивать пересменку может только владелец или Управляющий с выданным блоком «Пересменка»."
        )
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _ask_time(update.effective_message, str(user.id), markets[0])
        return

    await update.effective_message.reply_text("По какому рынку настраиваем пересменку?", reply_markup=_market_pick_keyboard(markets))


async def on_set_handover_report_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _ask_time(query.message, str(query.from_user.id), market)


async def on_set_handover_report_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает время запроса пересменки. Возвращает True, если сообщение
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
            await update.effective_message.reply_text("🤔 Нужен формат ЧЧ:ММ, например 15:00 — или «оставить», чтобы не менять.")
            return True
        set_market_handover_time(market["id"], text)
        market = get_market(market["id"])
        await update.effective_message.reply_text(f"✅ Время запроса пересменки для «{market['name']}»: {text}.")

    await _show_step(update.effective_message, market, "managers")
    return True


async def on_set_handover_report_next(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, market_id_str, step = query.data.split(":", 2)
    market = get_market(int(market_id_str))
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await _show_step(query.message, market, step)
