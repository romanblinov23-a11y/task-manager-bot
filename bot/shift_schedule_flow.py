import re

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.onboarding import find_user_id_by_username
from config.timeutil import parse_date
from monitoring.managers import get_market_supervisor, get_markets_for_manager, is_market_editor, is_owner
from monitoring.markets import get_market, list_markets
from monitoring.shift_schedule import set_shift_schedule

# telegram_user_id (str) управляющего/владельца -> {"market_id": int} — ждём вставки текста с графиком
_awaiting_paste: dict[str, dict] = {}

# telegram_user_id (str) -> {"market_id": int, "entries": [(date_iso, user_id, username), ...]}
_pending_confirm: dict[str, dict] = {}


def _available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"shsched_market:{m['id']}")] for m in markets])


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Подтвердить", callback_data="shsched_confirm"), InlineKeyboardButton("❌ Отмена", callback_data="shsched_cancel")]]
    )


def _instructions_text(market_name: str) -> str:
    return (
        f"Пришли график смен на 2 недели вперёд для «{market_name}» — кто сдаёт вечерний отчёт в какой день.\n\n"
        "Формат, по одной дате на строку:\n"
        "1.09.2026 - @сотрудник\n"
        "2.09.2026 - @сотрудник\n\n"
        "Username должен быть у того, кто хоть раз писал этому боту или в привязанном рабочем чате — "
        "иначе Telegram не даёт найти его по имени."
    )


async def _start_paste(message, user_id: str, market: dict) -> None:
    _awaiting_paste[user_id] = {"market_id": market["id"]}
    await message.reply_text(_instructions_text(market["name"]))


async def on_set_shift_schedule_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/set_shift_schedule — управляющий (или владелец) грузит график смен
    на 2 недели вперёд. Тот же диалог запускается автоматически 14-го и в
    последний день месяца (см. send_shift_schedule_requests)."""
    user = update.effective_user
    if not is_market_editor(user.id):
        await update.effective_message.reply_text("Загружать график смен может только владелец или Управляющий.")
        return

    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1:
        await _start_paste(update.effective_message, str(user.id), markets[0])
        return

    await update.effective_message.reply_text("По какому рынку загружаем график?", reply_markup=_market_pick_keyboard(markets))


async def on_set_shift_schedule_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Рынок: {market['name']}")
    await _start_paste(query.message, str(query.from_user.id), market)


def _parse_schedule_paste(text: str) -> tuple[list[tuple[str, int, str]], list[str]]:
    entries: list[tuple[str, int, str]] = []
    errors: list[str] = []
    for i, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = re.split(r"\s*[-—]\s*", line, maxsplit=1)
        if len(parts) != 2:
            errors.append(f"строка {i} «{line}»: не нашёл разделитель «-» между датой и @сотрудником")
            continue
        date_raw, username_raw = parts
        date_iso = parse_date(date_raw.strip())
        if not date_iso:
            errors.append(f"строка {i} «{line}»: не понял дату «{date_raw.strip()}»")
            continue
        username = username_raw.strip()
        if not username.startswith("@"):
            errors.append(f"строка {i} «{line}»: ожидал @username, получил «{username}»")
            continue
        user_id = find_user_id_by_username(username)
        if not user_id:
            errors.append(f"строка {i} «{line}»: не нашёл {username} — пусть сначала напишет боту хоть раз")
            continue
        entries.append((date_iso, user_id, username))
    return entries, errors


async def on_set_shift_schedule_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает вставленный текст графика. Возвращает True, если сообщение
    обработано — по конвенции остальных claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_paste.get(owner_id)
    if not state:
        return False

    text = update.effective_message.text or ""
    market_id = state["market_id"]
    market = get_market(market_id)
    del _awaiting_paste[owner_id]

    entries, errors = _parse_schedule_paste(text)

    lines = []
    if entries:
        lines.append(f"Распознано дат: {len(entries)}")
        for date_iso, _, username in sorted(entries, key=lambda e: e[0]):
            lines.append(f"— {date_iso}: {username}")
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
    lines.append(f"Сохранить график на «{market['name']}»?")
    await update.effective_message.reply_text("\n".join(lines), reply_markup=_confirm_keyboard())
    return True


async def on_set_shift_schedule_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    owner_id = str(query.from_user.id)
    state = _pending_confirm.pop(owner_id, None)
    if not state:
        await query.answer("Сессия неактуальна", show_alert=True)
        return

    await query.answer("Сохраняю…")
    set_shift_schedule(state["market_id"], [(date_iso, user_id) for date_iso, user_id, _ in state["entries"]])
    await query.edit_message_text(f"✅ График сохранён: {len(state['entries'])} дат.")


async def on_set_shift_schedule_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _pending_confirm.pop(str(query.from_user.id), None)
    await query.answer()
    await query.edit_message_text("Отменено, график не сохранён.")


async def send_shift_schedule_requests(bot: Bot) -> None:
    """Раз в две недели (14-е и последний день месяца, см. main.py) бот сам
    просит у управляющего каждого рынка график смен на ближайшие 2 недели."""
    for market in list_markets():
        supervisor = get_market_supervisor(market["id"])
        if not supervisor:
            continue
        supervisor_id = str(supervisor["telegram_user_id"])
        _awaiting_paste[supervisor_id] = {"market_id": market["id"]}
        try:
            await bot.send_message(chat_id=int(supervisor_id), text=_instructions_text(market["name"]))
        except Exception:
            pass
