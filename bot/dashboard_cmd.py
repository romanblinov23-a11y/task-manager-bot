from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import ContextTypes

from monitoring.dashboard import generate_aggregate_dashboard, generate_market_dashboard
from monitoring.managers import get_markets_for_manager, is_active_manager, is_owner
from monitoring.markets import get_market, list_markets


def _market_pick_keyboard(markets: list[dict], *, offer_aggregate: bool) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(m["name"], callback_data=f"dash_market:{m['id']}")] for m in markets]
    if offer_aggregate:
        buttons.append([InlineKeyboardButton("📊 Агрегированно по всем рынкам", callback_data="dash_all")])
    return InlineKeyboardMarkup(buttons)


async def _send_dashboard(message, filename: str, html: str) -> None:
    await message.reply_document(document=InputFile(BytesIO(html.encode("utf-8")), filename=filename))


async def on_dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    owner = is_owner(user.id)
    if not (owner or is_active_manager(user.id)):
        await update.effective_message.reply_text("Эта команда доступна только подтверждённым владельцем менеджерам.")
        return

    markets = list_markets() if owner else get_markets_for_manager(user.id)
    if not markets:
        await update.effective_message.reply_text("Нет доступных рынков — сначала пройдите онбординг через /start.")
        return

    if len(markets) == 1 and not owner:
        filename, html = generate_market_dashboard(markets[0]["id"])
        await _send_dashboard(update.effective_message, filename, html)
        return

    await update.effective_message.reply_text(
        "По какому рынку сформировать дашборд?", reply_markup=_market_pick_keyboard(markets, offer_aggregate=owner)
    )


async def on_dashboard_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(f"Собираю дашборд по рынку «{market['name']}»…")
    filename, html = generate_market_dashboard(market_id)
    await _send_dashboard(query.message, filename, html)


async def on_dashboard_aggregate_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    await query.answer()
    await query.edit_message_text("Собираю сводный дашборд по всем рынкам…")
    filename, html = generate_aggregate_dashboard()
    await _send_dashboard(query.message, filename, html)
