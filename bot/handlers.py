import logging

from telegram import Bot, Chat, Update
from telegram.ext import ContextTypes

from bot.buffer import BufferedMessage, MessageBuffer, format_context
from bot.confirmation import send_confirmation_cards
from bot.onboarding import record_group_member
from config.chats import get_project_for_chat
from config.settings import EXTRACTION_CONTEXT_HOURS, ROMAN_CHAT_NAME, ROMAN_TELEGRAM_ID
from monitoring.monitor_chats import get_monitor_chat
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


def _monitor_trigger_match(text: str, triggers: list[str]) -> str | None:
    """Упоминание владельца проверяется всегда, вне зависимости от того,
    настроены ли для чата свои триггерные слова (см. bot.monitor_chats).
    Совпадение — по вхождению подстроки, не по целому слову: у русского
    языка богатая морфология («жалоба»/«жалобу»/«жалобы»), и точное
    совпадение слова целиком пропустило бы почти все реальные упоминания,
    кроме именительного падежа. Разумная плата за это — считать вхождением
    и часть другого слова (например, триггер «иск» поймает «искать»); если
    это будет мешать, стоит выбирать более длинные/специфичные слова."""
    low = text.lower()
    if ROMAN_CHAT_NAME.lower() in low:
        return f"упоминание «{ROMAN_CHAT_NAME}»"
    for trigger in triggers:
        if trigger.lower() in low:
            return f"триггер «{trigger}»"
    return None


async def _check_monitor_chat(bot: Bot, chat: Chat, message, sender, sender_name: str) -> None:
    if sender and str(sender.id) == str(ROMAN_TELEGRAM_ID):
        return
    monitor_chat = get_monitor_chat(chat.id)
    if not monitor_chat:
        return
    matched = _monitor_trigger_match(message.text, monitor_chat["trigger_list"])
    if not matched:
        return
    try:
        await bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text=(
                f"👀 «{monitor_chat['title']}» — сработал {matched}\n\n"
                f"{sender_name}: {message.text}\n\n{_message_link(chat, message.message_id)}"
            ),
        )
    except Exception:
        pass


async def on_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return

    chat = update.effective_chat
    # (chat_id, message_thread_id) — в форуме с топиками разные ветки одного
    # chat_id могут относиться к разным проектам (см. config.chats), поэтому
    # буфер переписки и привязка к проекту учитывают ветку, а не только чат.
    buffer_key = (chat.id, message.message_thread_id)
    logging.debug("Сообщение из группы %r (chat_id=%s): %r", chat.title, chat.id, message.text)
    sender = update.effective_user
    if sender and str(sender.id) == str(ROMAN_TELEGRAM_ID):
        sender_name = ROMAN_CHAT_NAME  # используем имя из конфига, чтобы оно совпадало с тем, как его называют в чатах
    else:
        sender_name = sender.full_name if sender else "Неизвестный"
    record_group_member(chat.id, sender)

    buffered = BufferedMessage(
        message_id=message.message_id,
        timestamp=message.date,  # timezone-aware UTC, как отдаёт Telegram API
        sender_name=sender_name,
        sender_id=sender.id if sender else 0,
        text=message.text,
        link=_message_link(chat, message.message_id),
    )
    _buffer.add(buffer_key, buffered)

    await _check_monitor_chat(context.bot, chat, message, sender, sender_name)

    if not _is_trigger(update, context.bot.username):
        return

    project = get_project_for_chat(chat.id, message.message_thread_id)
    if project is None:
        return

    await message.reply_text("Кажется, есть работёнка — забрал 👀")

    context_messages = _buffer.get_context(buffer_key, hours=EXTRACTION_CONTEXT_HOURS)
    text_blob = format_context(context_messages)

    tasks = extract_tasks(text_blob, project_name=project)
    if not tasks:
        await message.reply_text("Поглядел — не нашёл конкретных задач в переписке.")
        return

    # Карта "нормализованное_имя → telegram_id" для всех отправителей из буфера.
    # Используется при подтверждении задачи для идентификации "по двум параметрам":
    # сначала имя совпало → потом проверяем, кто именно с таким именем писал в чате.
    buffer_id_hints: dict[str, int] = {
        m.sender_name.strip().lower(): m.sender_id
        for m in context_messages
        if m.sender_id
    }

    await send_confirmation_cards(
        context.bot,
        tasks,
        project=project,
        source="chat",
        source_chat=chat.title or str(chat.id),
        source_link=buffered.link,
        buffer_id_hints=buffer_id_hints,
    )
