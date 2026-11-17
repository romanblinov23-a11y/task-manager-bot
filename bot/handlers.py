import logging

from telegram import Bot, Chat, Update
from telegram.ext import ContextTypes

from bot.onboarding import record_group_member
from config.settings import ROMAN_CHAT_NAME, ROMAN_TELEGRAM_ID
from monitoring.monitor_chats import get_monitor_chat


def _message_link(chat: Chat, message_id: int) -> str:
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    chat_id_str = str(chat.id)
    internal_id = chat_id_str.removeprefix("-100") if chat_id_str.startswith("-100") else chat_id_str.lstrip("-")
    return f"https://t.me/c/{internal_id}/{message_id}"


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
    logging.debug("Сообщение из группы %r (chat_id=%s): %r", chat.title, chat.id, message.text)
    sender = update.effective_user
    if sender and str(sender.id) == str(ROMAN_TELEGRAM_ID):
        sender_name = ROMAN_CHAT_NAME  # используем имя из конфига, чтобы оно совпадало с тем, как его называют в чатах
    else:
        sender_name = sender.full_name if sender else "Неизвестный"
    record_group_member(chat.id, sender)

    await _check_monitor_chat(context.bot, chat, message, sender, sender_name)
