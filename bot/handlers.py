import logging

from telegram import Chat, Update
from telegram.ext import ContextTypes

from bot.buffer import BufferedMessage, MessageBuffer, format_context
from bot.confirmation import send_confirmation_cards
from bot.onboarding import record_group_member
from config.chats import get_project_for_chat
from config.settings import EXTRACTION_CONTEXT_HOURS, ROMAN_TELEGRAM_ID
from prompts.extraction import extract_tasks

_buffer = MessageBuffer()


def _message_link(chat: Chat, message_id: int) -> str:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    chat_id_str = str(chat.id)
    internal_id = chat_id_str.removeprefix("-100") if chat_id_str.startswith("-100") else chat_id_str.lstrip("-")
    return f"https://t.me/c/{internal_id}/{message_id}"


def _is_trigger(update: Update, bot_username: str) -> bool:
    message = update.effective_message
    reply_to = message.reply_to_message
    if reply_to and reply_to.from_user and reply_to.from_user.username == bot_username:
        return True
    if message.entities and message.text:
        for entity in message.entities:
            if entity.type == "mention":
                mention = message.text[entity.offset : entity.offset + entity.length]
                if mention.lstrip("@") == bot_username:
                    return True
    return False


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return

    chat = update.effective_chat
    logging.debug("Сообщение из группы %r (chat_id=%s): %r", chat.title, chat.id, message.text)
    sender = update.effective_user
    if sender and str(sender.id) == str(ROMAN_TELEGRAM_ID):
        sender_name = "Роман"  # нормализуем имя независимо от того, как оно записано в Telegram-профиле
    else:
        sender_name = sender.full_name if sender else "Неизвестный"
    record_group_member(chat.id, sender)

    buffered = BufferedMessage(
        message_id=message.message_id,
        timestamp=message.date,  # timezone-aware UTC, как отдаёт Telegram API
        sender_name=sender_name,
        text=message.text,
        link=_message_link(chat, message.message_id),
    )
    _buffer.add(chat.id, buffered)

    if not _is_trigger(update, context.bot.username):
        return

    project = get_project_for_chat(chat.id)
    if project is None:
        return

    context_messages = _buffer.get_context(chat.id, hours=EXTRACTION_CONTEXT_HOURS)
    text_blob = format_context(context_messages)

    tasks = extract_tasks(text_blob, project_name=project)
    if not tasks:
        return

    await send_confirmation_cards(
        context.bot,
        tasks,
        project=project,
        source="chat",
        source_chat=chat.title or str(chat.id),
        source_link=buffered.link,
    )
