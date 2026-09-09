import asyncio
from datetime import date

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from config.settings import ROMAN_TELEGRAM_ID, SURF_EMAIL, SURF_PASSWORD
from monitoring.managers import is_owner
from revenue.claude_commentary import ClaudeCommentary
from revenue.daily_report import run_daily_report
from revenue.monthly_report import run_monthly_report
from revenue.plan_report import MONTH_NAMES_RU, run_fact_report, run_plan_report
from revenue.sheets_exporter import export_pnl_spot, export_pnl_to_sheets
from revenue.surfcoffee_client import SurfCoffeeClient
from revenue.weekly_report import run_weekly_report

_SPOT_LABELS = {"yandex": "Yandex", "okko": "OKKO", "park_gorkogo": "Парк Горького"}


def _is_owner_update(update: Update) -> bool:
    user = update.effective_user
    return bool(user) and is_owner(user.id)


async def send_report_messages(bot: Bot, chat_id: int, messages: list[str]) -> None:
    """Шлёт сообщения отчёта по одному, с разметкой Markdown (жирный/курсив
    в формате revenue/formatting.py) — при невалидной разметке (например,
    случайный '*' в свободном тексте комментария Claude) отправляет то же
    сообщение обычным текстом, а не молчит. Небольшая пауза между
    сообщениями — чтобы не словить flood control Telegram, как было у
    "Аналитика Ивана" (telegram_sender.py)."""
    for text in messages:
        try:
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except BadRequest:
            await bot.send_message(chat_id=chat_id, text=text)
        await asyncio.sleep(1)


def _new_client() -> SurfCoffeeClient:
    return SurfCoffeeClient(SURF_EMAIL, SURF_PASSWORD)


def _run_daily_sync() -> list[str]:
    client = _new_client()
    try:
        client.login()
        return run_daily_report(client)
    finally:
        client.close()


def _run_weekly_sync() -> list[str]:
    client = _new_client()
    try:
        client.login()
        return run_weekly_report(client, ClaudeCommentary())
    finally:
        client.close()


def _run_monthly_sync() -> list[str]:
    client = _new_client()
    try:
        client.login()
        return run_monthly_report(client, ClaudeCommentary())
    finally:
        client.close()


def _run_plan_sync(month: str, spot_key: str | None) -> list[str]:
    client = _new_client()
    try:
        client.login()
        return run_plan_report(client, month, spot_key)
    finally:
        client.close()


def _run_fact_sync(month: str, spot_key: str | None) -> list[str]:
    client = _new_client()
    try:
        client.login()
        return run_fact_report(client, month, spot_key)
    finally:
        client.close()


def _run_pnl_sync(spot_key: str | None) -> str:
    client = _new_client()
    try:
        client.login()
        year = date.today().year
        if spot_key:
            return export_pnl_spot(client, spot_key, year)
        return export_pnl_to_sheets(client, year)
    finally:
        client.close()


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📊 Отчёт за сегодня", callback_data="revenue:daily")],
            [InlineKeyboardButton("📈 Отчёт за неделю", callback_data="revenue:weekly")],
            [InlineKeyboardButton("🗓 Отчёт за месяц", callback_data="revenue:monthly")],
            [InlineKeyboardButton("💹 P&L", callback_data="revenue:pnl")],
            [InlineKeyboardButton("📋 План", callback_data="revenue:plan")],
            [InlineKeyboardButton("📉 Факт", callback_data="revenue:fact")],
        ]
    )


async def on_revenue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/revenue — единая точка входа в отчёты по выручке (перенесено из
    отдельного бота "Аналитик Иван"): ежедневный/недельный/месячный отчёт,
    план/факт по дням, экспорт P&L в Google Sheets. Только для владельца —
    Управляющим эта функция не нужна."""
    if not _is_owner_update(update):
        return
    await update.effective_message.reply_text("Выручка — что показать?", reply_markup=_menu_keyboard())


def _pnl_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Все точки", callback_data="rev_pnl:all")],
            [
                InlineKeyboardButton("Yandex", callback_data="rev_pnl:yandex"),
                InlineKeyboardButton("OKKO", callback_data="rev_pnl:okko"),
            ],
            [InlineKeyboardButton("Парк Горького", callback_data="rev_pnl:park_gorkogo")],
        ]
    )


def _plan_month_keyboard() -> InlineKeyboardMarkup:
    """Те же 4 месяца, что предлагал /plan у Ивана: 2 назад, текущий, 1 вперёд —
    план обычно вносят заранее, поэтому есть и будущий месяц."""
    today = date.today()
    months = []
    for delta in (-2, -1, 0, 1):
        m = today.month + delta
        y = today.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        months.append((y, m))

    rows, row_buf = [], []
    for y, m in months:
        row_buf.append(InlineKeyboardButton(f"{MONTH_NAMES_RU[m]} {y}", callback_data=f"rev_planm:{y}-{m:02d}"))
        if len(row_buf) == 2:
            rows.append(row_buf)
            row_buf = []
    if row_buf:
        rows.append(row_buf)
    return InlineKeyboardMarkup(rows)


def _fact_month_keyboard() -> InlineKeyboardMarkup:
    """Факт есть только за уже прошедшие месяцы текущего года — от января до текущего."""
    today = date.today()
    rows, row_buf = [], []
    for m in range(1, today.month + 1):
        row_buf.append(InlineKeyboardButton(MONTH_NAMES_RU[m], callback_data=f"rev_factm:{today.year}-{m:02d}"))
        if len(row_buf) == 3:
            rows.append(row_buf)
            row_buf = []
    if row_buf:
        rows.append(row_buf)
    return InlineKeyboardMarkup(rows)


def _spot_keyboard(prefix: str, month: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Все точки", callback_data=f"{prefix}:{month}_all")],
            [
                InlineKeyboardButton("Yandex", callback_data=f"{prefix}:{month}_yandex"),
                InlineKeyboardButton("OKKO", callback_data=f"{prefix}:{month}_okko"),
            ],
            [InlineKeyboardButton("Парк Горького", callback_data=f"{prefix}:{month}_park_gorkogo")],
        ]
    )


async def on_revenue_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner_update(update):
        await query.answer()
        return
    action = query.data.split(":", 1)[1]
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)

    if action == "daily":
        await query.message.reply_text("⏳ Загружаю ежедневный отчёт…")
        messages = await asyncio.to_thread(_run_daily_sync)
        await send_report_messages(context.bot, query.from_user.id, messages)
        return
    if action == "weekly":
        await query.message.reply_text("⏳ Загружаю недельный отчёт, это может занять минуту (аналитика Claude)…")
        messages = await asyncio.to_thread(_run_weekly_sync)
        await send_report_messages(context.bot, query.from_user.id, messages)
        return
    if action == "monthly":
        await query.message.reply_text("⏳ Загружаю месячный отчёт, это может занять минуту (аналитика Claude)…")
        messages = await asyncio.to_thread(_run_monthly_sync)
        await send_report_messages(context.bot, query.from_user.id, messages)
        return
    if action == "pnl":
        await query.message.reply_text("По какой точке?", reply_markup=_pnl_keyboard())
        return
    if action == "plan":
        await query.message.reply_text("Шаг 1/2 — выбери месяц:", reply_markup=_plan_month_keyboard())
        return
    if action == "fact":
        await query.message.reply_text("Шаг 1/2 — выбери месяц:", reply_markup=_fact_month_keyboard())
        return


async def on_revenue_pnl_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner_update(update):
        await query.answer()
        return
    spot_key = query.data.split(":", 1)[1]
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    label = "все точки" if spot_key == "all" else _SPOT_LABELS[spot_key]
    await query.message.reply_text(f"⏳ Выгружаю P&L ({label}) в Google Sheets…")
    result = await asyncio.to_thread(_run_pnl_sync, None if spot_key == "all" else spot_key)
    await query.message.reply_text(result, parse_mode=None)


async def on_revenue_plan_month_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner_update(update):
        await query.answer()
        return
    month = query.data.split(":", 1)[1]
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("Шаг 2/2 — выбери точку:", reply_markup=_spot_keyboard("rev_plans", month))


async def on_revenue_fact_month_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner_update(update):
        await query.answer()
        return
    month = query.data.split(":", 1)[1]
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("Шаг 2/2 — выбери точку:", reply_markup=_spot_keyboard("rev_facts", month))


async def on_revenue_plan_spot_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner_update(update):
        await query.answer()
        return
    month, spot_key = query.data.split(":", 1)[1].split("_", 1)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("⏳ Загружаю план…")
    messages = await asyncio.to_thread(_run_plan_sync, month, None if spot_key == "all" else spot_key)
    await send_report_messages(context.bot, query.from_user.id, messages)


async def on_revenue_fact_spot_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_owner_update(update):
        await query.answer()
        return
    month, spot_key = query.data.split(":", 1)[1].split("_", 1)
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("⏳ Загружаю факт…")
    messages = await asyncio.to_thread(_run_fact_sync, month, None if spot_key == "all" else spot_key)
    await send_report_messages(context.bot, query.from_user.id, messages)


async def _send_scheduled(bot: Bot, run_sync, label: str) -> None:
    """Общая обёртка для трёх джобов ниже (main.py): считает отчёт в
    отдельном потоке, шлёт Роме, при ошибке — одно ⚠️-сообщение вместо
    падения джоба (как было у "Аналитика Ивана" — там каждый job_* сам
    ловил исключение и слал его текстом)."""
    try:
        messages = await asyncio.to_thread(run_sync)
        await send_report_messages(bot, int(ROMAN_TELEGRAM_ID), messages)
    except Exception as e:
        try:
            await bot.send_message(chat_id=int(ROMAN_TELEGRAM_ID), text=f"⚠️ Не удалось сформировать {label}.\nОшибка: {e}")
        except Exception:
            pass


async def send_daily_revenue_report(bot: Bot) -> None:
    """Вт-Вс в REVENUE_REPORT_TIME (см. main.py) — по понедельникам не
    запускается, недельный отчёт его покрывает."""
    await _send_scheduled(bot, _run_daily_sync, "ежедневный отчёт по выручке")


async def send_weekly_revenue_report(bot: Bot) -> None:
    """По понедельникам в REVENUE_REPORT_TIME — итоги прошедшей недели."""
    await _send_scheduled(bot, _run_weekly_sync, "недельный отчёт по выручке")


async def send_monthly_revenue_report(bot: Bot) -> None:
    """1-го числа каждого месяца в REVENUE_REPORT_TIME — итоги прошедшего
    месяца. День месяца фильтруется в main.py, здесь безусловно."""
    await _send_scheduled(bot, _run_monthly_sync, "месячный отчёт по выручке")
