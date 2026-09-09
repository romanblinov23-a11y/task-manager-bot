import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.revenue_flow import _run_fact_sync, _run_plan_sync, send_report_messages
from monitoring.managers import get_markets_for_manager, is_accounting_editor, is_owner
from monitoring.markets import get_market, list_markets
from revenue.market_mapping import spot_key_for_market
from revenue.plan_report import MONTH_NAMES_RU, fact_month_options, plan_month_options


def _available_markets(user_id: int) -> list[dict]:
    if is_owner(user_id):
        return list_markets()
    return get_markets_for_manager(user_id)


def _menu_keyboard(market_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 План", callback_data=f"acct_menu:{market_id}:plan")],
            [InlineKeyboardButton("📉 Факт", callback_data=f"acct_menu:{market_id}:fact")],
        ]
    )


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"acct_market:{m['id']}")] for m in markets])


def _plan_month_keyboard(market_id: int) -> InlineKeyboardMarkup:
    rows, row_buf = [], []
    for y, m in plan_month_options():
        row_buf.append(InlineKeyboardButton(f"{MONTH_NAMES_RU[m]} {y}", callback_data=f"acct_planm:{market_id}:{y}-{m:02d}"))
        if len(row_buf) == 2:
            rows.append(row_buf)
            row_buf = []
    if row_buf:
        rows.append(row_buf)
    return InlineKeyboardMarkup(rows)


def _fact_month_keyboard(market_id: int) -> InlineKeyboardMarkup:
    rows, row_buf = [], []
    for y, m in fact_month_options():
        row_buf.append(InlineKeyboardButton(MONTH_NAMES_RU[m], callback_data=f"acct_factm:{market_id}:{y}-{m:02d}"))
        if len(row_buf) == 3:
            rows.append(row_buf)
            row_buf = []
    if row_buf:
        rows.append(row_buf)
    return InlineKeyboardMarkup(rows)


async def _show_menu(message, market: dict) -> None:
    if not spot_key_for_market(market["id"]):
        await message.reply_text(f"«{market['name']}» ещё не подключена к системе учёта Surf Coffee.")
        return
    await message.reply_text(f"Рынок: {market['name']}. Что показать?", reply_markup=_menu_keyboard(market["id"]))


async def on_accounting_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/accounting — «Работа с системой учёта»: план и факт по выручке,
    но только по своей точке (в отличие от /revenue у владельца, который
    видит все точки и P&L). Доступно владельцу и Управляющему с выданным
    блоком «Работа с системой учёта»."""
    user = update.effective_user
    if not is_accounting_editor(user.id):
        await update.effective_message.reply_text(
            "Работа с системой учёта доступна только владельцу или Управляющему с выданным блоком «Работа с системой учёта»."
        )
        return
    markets = _available_markets(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return
    if len(markets) == 1:
        await _show_menu(update.effective_message, markets[0])
        return
    await update.effective_message.reply_text("По какому рынку?", reply_markup=_market_pick_keyboard(markets))


async def on_accounting_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_accounting_editor(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    allowed_ids = {m["id"] for m in _available_markets(query.from_user.id)}
    if not market or market_id not in allowed_ids:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await _show_menu(query.message, market)


async def on_accounting_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_accounting_editor(query.from_user.id):
        await query.answer()
        return
    _, market_id_str, kind = query.data.split(":")
    market_id = int(market_id_str)
    market = get_market(market_id)
    allowed_ids = {m["id"] for m in _available_markets(query.from_user.id)}
    if not market or market_id not in allowed_ids:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    if kind == "plan":
        await query.message.reply_text("Выбери месяц:", reply_markup=_plan_month_keyboard(market_id))
    else:
        await query.message.reply_text("Выбери месяц:", reply_markup=_fact_month_keyboard(market_id))


async def on_accounting_plan_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_accounting_editor(query.from_user.id):
        await query.answer()
        return
    _, market_id_str, month = query.data.split(":")
    market_id = int(market_id_str)
    allowed_ids = {m["id"] for m in _available_markets(query.from_user.id)}
    spot_key = spot_key_for_market(market_id) if market_id in allowed_ids else None
    if not spot_key:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("⏳ Загружаю план…")
    messages = await asyncio.to_thread(_run_plan_sync, month, spot_key)
    await send_report_messages(context.bot, query.from_user.id, messages)


async def on_accounting_fact_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_accounting_editor(query.from_user.id):
        await query.answer()
        return
    _, market_id_str, month = query.data.split(":")
    market_id = int(market_id_str)
    allowed_ids = {m["id"] for m in _available_markets(query.from_user.id)}
    spot_key = spot_key_for_market(market_id) if market_id in allowed_ids else None
    if not spot_key:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text("⏳ Загружаю факт…")
    messages = await asyncio.to_thread(_run_fact_sync, month, spot_key)
    await send_report_messages(context.bot, query.from_user.id, messages)
