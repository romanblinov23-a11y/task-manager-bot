from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.messaging import on_broadcast_command, on_message_chat_command, on_message_command
from monitoring.managers import is_owner


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✉️ Написать сотруднику", callback_data="msgmenu:dm")],
            [InlineKeyboardButton("💬 Написать в чат", callback_data="msgmenu:chat")],
            [InlineKeyboardButton("📢 Рассылка группе", callback_data="msgmenu:broadcast")],
        ]
    )


# Каждый пункт вызывает ровно тот же хендлер, что и у соответствующей
# отдельной команды (/message, /message_chat, /broadcast) — им подходит и
# Update от CallbackQuery: effective_message/effective_user разрешаются PTB
# и оттуда тоже.
_DISPATCH = {
    "dm": on_message_command,
    "chat": on_message_chat_command,
    "broadcast": on_broadcast_command,
}


async def on_messaging_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/messaging — единая точка входа во все способы написать через бота
    (личное сообщение сотруднику, сообщение в зарегистрированный чат,
    рассылка группе), вместо трёх отдельных команд в меню владельца."""
    if not is_owner(update.effective_user.id):
        return
    await update.effective_message.reply_text("Что делаем?", reply_markup=_menu_keyboard())


async def on_messaging_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    action = query.data.split(":", 1)[1]
    handler = _DISPATCH.get(action)
    if not handler:
        await query.answer()
        return
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await handler(update, context)
