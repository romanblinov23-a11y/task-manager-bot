from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.managers import is_owner
from monitoring.markets import get_market, list_markets


def _market_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(m["name"], callback_data=f"msett_market:{m['id']}")] for m in markets])


def _actions_keyboard(market_id: int) -> InlineKeyboardMarkup:
    """Каждая кнопка использует ровно тот же callback_data, что и шаг
    «рынок выбран» соответствующей отдельной команды (setop_market:,
    sched_market: и т.д.) — так все существующие хендлеры и их права
    доступа переиспользуются без изменений, здесь просто пропускается
    отдельный экран выбора рынка."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏷 Реквизиты оператора (ПДн)", callback_data=f"setop_market:{market_id}")],
            [InlineKeyboardButton("📄 Текст согласия", callback_data=f"setconsent_market:{market_id}")],
            [InlineKeyboardButton("📤 Экспорт данных сотрудников", callback_data=f"expdata_market:{market_id}")],
            [InlineKeyboardButton("🔁 Запросить согласия у всех", callback_data=f"reqconsent_market:{market_id}")],
            [InlineKeyboardButton("🎯 Своя точка (мониторинг)", callback_data=f"ownpt_market:{market_id}")],
            [InlineKeyboardButton("🗓 Дни опроса конкурентов", callback_data=f"sched_market:{market_id}")],
            [InlineKeyboardButton("♻️ Сбросить конкурентов", callback_data=f"reset_monitoring_market:{market_id}")],
            [InlineKeyboardButton("📅 График смен", callback_data=f"shsched_market:{market_id}")],
            [InlineKeyboardButton("💰 Финплан на месяц", callback_data=f"monthplan_market:{market_id}")],
            [InlineKeyboardButton("🤝 Ритм собраний", callback_data=f"meetsched_market:{market_id}")],
            [InlineKeyboardButton("💰 Чат финпартнёров", callback_data=f"shrc_view:{market_id}:finance")],
            [InlineKeyboardButton("👥 Чат команды точки", callback_data=f"shrc_view:{market_id}:team")],
            [InlineKeyboardButton("↩️ Другой рынок", callback_data="msett_back")],
        ]
    )


async def on_market_settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/market_settings — единая точка входа во все настройки конкретного
    рынка (ПДн/согласия, мониторинг конкурентов, смены и план, встречи,
    чаты отчётов), вместо десятка отдельных команд в меню владельца."""
    if not is_owner(update.effective_user.id):
        return
    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного рынка. Сначала /add_project.")
        return
    if len(markets) == 1:
        await update.effective_message.reply_text(
            f"Рынок: {markets[0]['name']}. Что настраиваем?", reply_markup=_actions_keyboard(markets[0]["id"])
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
    await query.edit_message_text(f"Рынок: {market['name']}. Что настраиваем?", reply_markup=_actions_keyboard(market_id))


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
