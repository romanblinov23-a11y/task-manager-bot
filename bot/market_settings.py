from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.managers import is_owner
from monitoring.markets import get_market, list_markets, set_market_send_to_finance


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"msett_market:{m['id']}")] for m in markets])


def _actions_keyboard(market: dict) -> InlineKeyboardMarkup:
    """Только то, что реально принадлежит владельцу (юрлицо-оператор ПДн,
    отношения с финпартнёрами) — настройка конкурентов, смен, плана и
    собраний работает через свои отдельные команды, которыми пользуется
    Управляющий (у него они в его личном меню), не Рома. Каждая кнопка
    использует ровно тот же callback_data, что и шаг «рынок выбран»
    соответствующей отдельной команды (setop_market: и т.д.) — так права
    доступа переиспользуются без изменений."""
    market_id = market["id"]
    finance_on = market.get("send_to_finance", 1)
    finance_label = "💰 Финпартнёры: включено (нажми, чтобы выключить)" if finance_on else "💰 Финпартнёры: выключено (нажми, чтобы включить)"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏷 Реквизиты оператора (ПДн)", callback_data=f"setop_market:{market_id}")],
            [InlineKeyboardButton("📄 Текст согласия", callback_data=f"setconsent_market:{market_id}")],
            [InlineKeyboardButton("📤 Экспорт данных сотрудников", callback_data=f"expdata_market:{market_id}")],
            [InlineKeyboardButton("🔁 Запросить согласия у всех", callback_data=f"reqconsent_market:{market_id}")],
            [InlineKeyboardButton(finance_label, callback_data=f"msett_togglefinance:{market_id}")],
            [InlineKeyboardButton("💰 Чат финпартнёров", callback_data=f"shrc_view:{market_id}:finance")],
            [InlineKeyboardButton("👥 Чат команды точки", callback_data=f"shrc_view:{market_id}:team")],
            [InlineKeyboardButton("↩️ Другой рынок", callback_data="msett_back")],
        ]
    )


async def on_market_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/market_settings — то, что по-настоящему решает владелец: реквизиты
    юрлица-оператора ПДн, текст согласия, экспорт данных при передаче
    точки заказчику, и подключён ли рынок к чату финпартнёров. Настройку
    конкурентов/смен/плана/собраний ведёт Управляющий своими командами."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка. Сначала /add_project.")
        return
    if len(markets) == 1:
        await update.effective_message.reply_text(
            f"Рынок: {markets[0]['name']}. Что настраиваем?", reply_markup=_actions_keyboard(markets[0])
        )
        return
    await update.effective_message.reply_text("Какой рынок настраиваем?", reply_markup=_market_pick_keyboard(markets))


async def on_market_settings_market_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    await query.edit_message_text(f"Рынок: {market['name']}. Что настраиваем?", reply_markup=_actions_keyboard(market))


async def on_market_settings_toggle_finance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Рынок не найден", show_alert=True)
        return
    set_market_send_to_finance(market_id, not market.get("send_to_finance", 1))
    market = get_market(market_id)
    await query.answer("Включено" if market.get("send_to_finance", 1) else "Выключено")
    await query.edit_message_text(f"Рынок: {market['name']}. Что настраиваем?", reply_markup=_actions_keyboard(market))


async def on_market_settings_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    markets = list_markets()
    await query.answer()
    if not markets:
        await query.edit_message_text("Пока нет ни одного рынка.")
        return
    await query.edit_message_text("Какой рынок настраиваем?", reply_markup=_market_pick_keyboard(markets))
