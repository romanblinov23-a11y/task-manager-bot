from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.chats import register_chat
from monitoring.managers import is_owner
from monitoring.markets import get_market, list_markets


def _project_pick_keyboard(markets: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(m["name"], callback_data=f"regchat:{m['id']}")] for m in markets]
    )


def _scope_keyboard(market_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Только эту ветку", callback_data=f"regchatscope:{market_id}:thread")],
            [InlineKeyboardButton("Весь чат целиком", callback_data=f"regchatscope:{market_id}:chat")],
        ]
    )


async def on_register_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Самообслуживание для онбординга чата: владелец вызывает эту команду
    внутри рабочей группы и выбирает проект кнопкой — без ввода названия
    вручную. Посмотреть/сменить/отвязать привязки существующих чатов можно
    в /managers → «💬 Чаты»."""
    if not is_owner(update.effective_user.id):
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("Эта команда работает только внутри группового чата.")
        return

    markets = list_markets()
    if not markets:
        await update.effective_message.reply_text("Пока нет ни одного проекта — сначала /add_project в личке боту.")
        return

    await update.effective_message.reply_text(
        "К какому проекту привязать этот чат?", reply_markup=_project_pick_keyboard(markets)
    )


async def _finish_registration(query, context: ContextTypes.DEFAULT_TYPE, market: dict, message_thread_id: int | None) -> None:
    chat_id = query.message.chat.id
    register_chat(chat_id, market["name"], message_thread_id=message_thread_id)
    bot_username = context.bot.username
    await query.edit_message_text(
        f"👋 Привет! Я бот Енисей, помогаю Роме в этом чате по проекту «{market['name']}».\n\n"
        f"Тегните меня (@{bot_username}) или ответьте на моё сообщение — соберу из переписки "
        "договорённости и пришлю задачи на подтверждение."
    )


async def on_register_project_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return

    market_id = int(query.data.split(":", 1)[1])
    market = get_market(market_id)
    if not market:
        await query.answer("Проект не найден", show_alert=True)
        return

    # Если /register_project запускали внутри конкретной ветки форума —
    # телеграм передаёт её id на всей цепочке сообщений бота (PTB
    # Message.reply_text по умолчанию отвечает в ту же ветку), так что
    # query.message уже несёт нужный message_thread_id к этому шагу. Раз
    # ветка есть — уточняем, привязывать только её (мульти-проектный форум)
    # или весь чат целиком, как для обычной группы без топиков.
    message_thread_id = query.message.message_thread_id
    await query.answer()

    if message_thread_id is None:
        await _finish_registration(query, context, market, None)
        return

    await query.edit_message_text(
        f"Эта команда запущена внутри ветки форума. Привязать к проекту «{market['name']}» только эту ветку, "
        "или весь чат целиком?",
        reply_markup=_scope_keyboard(market_id),
    )


async def on_register_project_scope_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return

    _, market_id_str, scope = query.data.split(":")
    market_id = int(market_id_str)
    market = get_market(market_id)
    if not market:
        await query.answer("Проект не найден", show_alert=True)
        return

    await query.answer()
    message_thread_id = query.message.message_thread_id if scope == "thread" else None
    await _finish_registration(query, context, market, message_thread_id)
