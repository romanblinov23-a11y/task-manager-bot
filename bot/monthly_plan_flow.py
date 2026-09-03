import re

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.timeutil import parse_date
from monitoring.managers import get_market_supervisor, get_markets_for_manager, is_owner, is_reports_editor
from monitoring.markets import get_market, list_markets
from monitoring.monthly_plan import set_monthly_plan

# telegram_user_id (str) управляющего/владельца -> {"market_id": int} — ждём вставки текста с планом
_awaiting_paste: dict[str, dict] = {}

# telegram_user_id (str) -> {"market_id": int, "entries": [(date_iso, revenue, checks), ...]}
_pending_confirm: dict[str, dict] = {}


def _parse_amount(text: str) -> float | None:
    cleaned = text.strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_int(text: str) -> int | None:
    cleaned = text.strip().replace(" ", "").replace("\xa0", "")
    return int(cleaned) if cleaned.isdigit() else None


def _available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"monthplan_market:{m['id']}")] for m in markets])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Подтвердить", callback_data="monthplan_confirm"), InlineKeyboardButton("❌ Отмена", callback_data="monthplan_cancel")]]
    )


def _instructions_text(market_name: str) -> str:
    return (
        f"Пришли план по выручке и чекам на месяц вперёд для «{market_name}» — по дням.\n\n"
        "Формат, по одной дате на строку:\n"
        "1.09.2026 - 350000 - 120\n"
        "2.09.2026 - 340000 - 115\n\n"
        "Средний чек считать не нужно — бот сам поделит выручку на чеки."
    )


async def _start_paste(message, user_id: str, market: dict) -> None:
    _awaiting_paste[user_id] = {"market_id": market["id"]}
    await message.reply_text(_instructions_text(market["name"]))


async def on_set_monthly_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_monthly_plan — управляющий (или владелец) грузит план по
    выручке/чекам на месяц вперёд. Тот же диалог запускается автоматически
    25-го числа (см. send_monthly_plan_requests); ручной запуск доступен в
    любой день, если план нужно поправить."""
    user = update.effective_user
    if not is_reports_editor(user.id):
        await update.effective_message.reply_text(
            "Загружать план по выручке/чекам может только владелец или Управляющий с выданным блоком «Отчёты по смене»."
        )
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _start_paste(update.effective_message, str(user.id), markets[0])
        return

    await update.effective_message.reply_text("По какому рынку загружаем план?", reply_markup=_market_pick_keyboard(markets))


async def on_set_monthly_plan_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _start_paste(query.message, str(query.from_user.id), market)


def _parse_plan_paste(text: str) -> tuple[list[tuple[str, float, int]], list[str]]:
    entries: list[tuple[str, float, int]] = []
    errors: list[str] = []
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = re.split(r"\s*[-—]\s*", line)
        if len(parts) != 3:
            errors.append(f"строка {i} «{line}»: ожидал «дата - выручка - чеки»")
            continue
        date_raw, revenue_raw, checks_raw = parts
        date_iso = parse_date(date_raw.strip())
        if not date_iso:
            errors.append(f"строка {i} «{line}»: не понял дату «{date_raw.strip()}»")
            continue
        revenue = _parse_amount(revenue_raw)
        if revenue is None:
            errors.append(f"строка {i} «{line}»: не понял выручку «{revenue_raw.strip()}»")
            continue
        checks = _parse_int(checks_raw)
        if checks is None:
            errors.append(f"строка {i} «{line}»: не понял количество чеков «{checks_raw.strip()}»")
            continue
        entries.append((date_iso, revenue, checks))
    return entries, errors


async def on_set_monthly_plan_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает вставленный текст плана. Возвращает True, если сообщение
    обработано — по конвенции остальных claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_paste.get(owner_id)
    if not state:
        return False

    text = update.effective_message.text or ""
    market_id = state["market_id"]
    market = get_market(market_id)
    del _awaiting_paste[owner_id]

    entries, errors = _parse_plan_paste(text)

    lines = []
    if entries:
        lines.append(f"Распознано дат: {len(entries)}")
        for date_iso, revenue, checks in sorted(entries, key=lambda e: e[0]):
            avg_check = revenue / checks if checks else 0
            lines.append(f"— {date_iso}: выручка {revenue:,.0f}, чеки {checks}, средний чек ≈{avg_check:,.0f}".replace(",", " "))
    if errors:
        lines.append("")
        lines.append(f"Не разобрал строк: {len(errors)}")
        for e in errors:
            lines.append(f"— {e}")

    if not entries:
        lines.append("")
        lines.append("Нечего сохранять. Проверьте формат и вставьте текст ещё раз.")
        _awaiting_paste[owner_id] = state
        await update.effective_message.reply_text("\n".join(lines))
        return True

    _pending_confirm[owner_id] = {"market_id": market_id, "entries": entries}
    lines.append("")
    lines.append(f"Сохранить план на «{market['name']}»?")
    await update.effective_message.reply_text("\n".join(lines), reply_markup=_confirm_keyboard())
    return True


async def on_set_monthly_plan_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    owner_id = str(query.from_user.id)
    state = _pending_confirm.pop(owner_id, None)
    if not state:
        await query.answer("Сессия неактуальна", show_alert=True)
        return

    await query.answer("Сохраняю…")
    set_monthly_plan(state["market_id"], state["entries"])
    await query.edit_message_text(f"✅ План сохранён: {len(state['entries'])} дат.")


async def on_set_monthly_plan_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _pending_confirm.pop(str(query.from_user.id), None)
    await query.answer()
    await query.edit_message_text("Отменено, план не сохранён.")


async def send_monthly_plan_requests(bot: Bot) -> None:
    """25-го числа (см. main.py) бот сам просит у управляющего каждого
    рынка план по выручке/чекам на следующий месяц."""
    for market in list_markets():
        supervisor = get_market_supervisor(market["id"])
        if not supervisor or not is_reports_editor(supervisor["telegram_user_id"]):
            continue
        supervisor_id = str(supervisor["telegram_user_id"])
        _awaiting_paste[supervisor_id] = {"market_id": market["id"]}
        try:
            await bot.send_message(chat_id=int(supervisor_id), text=_instructions_text(market["name"]))
        except Exception:
            pass
