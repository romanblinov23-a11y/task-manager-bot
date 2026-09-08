import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from monitoring.managers import is_owner
from monitoring.monitor_chats import (
    get_monitor_chat,
    list_monitor_chats,
    register_monitor_chat,
    remove_monitor_chat,
    set_monitor_chat_triggers,
)

# telegram_user_id (str) владельца -> {"chat_id": int, "title": str} — ждём список триггерных слов
_awaiting_triggers: dict[str, dict] = {}


def _parse_triggers(text: str) -> list[str]:
    parts = re.split(r"[,\n]+", text)
    return [p.strip() for p in parts if p.strip()]


async def on_register_monitor_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/register_monitor_chat — владелец вызывает внутри ЛЮБОГО группового
    чата, необязательно привязанного к проекту/рынку, чтобы бот следил там
    за упоминаниями владельца и настроенными триггерными словами и писал
    об этом ему в личку. Список слов настраивается следующим шагом в
    личке — так участники чата его не видят."""
    if not is_owner(update.effective_user.id):
        return
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("Эта команда работает только внутри группового чата.")
        return

    title = chat.title or str(chat.id)
    register_monitor_chat(chat.id, title)
    await update.effective_message.reply_text(f"✅ Чат «{title}» теперь под мониторингом. Донастрою триггерные слова в личке.")

    owner_id = update.effective_user.id
    _awaiting_triggers[str(owner_id)] = {"chat_id": chat.id, "title": title}
    try:
        await context.bot.send_message(
            chat_id=owner_id,
            text=(
                f"Какие триггерные слова отслеживать в чате «{title}» — кроме упоминаний тебя самого, это и так "
                "проверяется всегда? Пришли через запятую или каждое слово с новой строки. Если пока не нужны — напиши «нет»."
            ),
        )
    except Exception:
        await update.effective_message.reply_text(
            "Не смог написать тебе в личку — открой диалог со мной (/start) и повтори /register_monitor_chat."
        )


async def on_register_monitor_chat_triggers_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает список триггерных слов после /register_monitor_chat (или
    после «✏️ Изменить триггеры» в /monitor_chats). Возвращает True, если
    сообщение обработано — по конвенции остальных claim-хендлеров в
    on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_triggers.pop(owner_id, None)
    if not state:
        return False

    text = (update.effective_message.text or "").strip()
    triggers = [] if text.lower() in ("нет", "не надо", "-") else _parse_triggers(text)
    set_monitor_chat_triggers(state["chat_id"], triggers)
    if triggers:
        await update.effective_message.reply_text(f"✅ Буду следить в «{state['title']}» за: {', '.join(triggers)} (и упоминаниями тебя).")
    else:
        await update.effective_message.reply_text(f"✅ В «{state['title']}» буду следить только за упоминаниями тебя.")
    return True


def _monitor_list_keyboard(chats: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(c["title"], callback_data=f"monchat_select:{c['chat_id']}")] for c in chats])


def _monitor_card_text(chat: dict) -> str:
    triggers = ", ".join(chat["trigger_list"]) if chat["trigger_list"] else "нет (только упоминания тебя)"
    return f"«{chat['title']}»\nChat ID: {chat['chat_id']}\nТриггеры: {triggers}"


def _monitor_card_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✏️ Изменить триггеры", callback_data=f"monchat_edit:{chat_id}")],
            [InlineKeyboardButton("🗑 Убрать из мониторинга", callback_data=f"monchat_remove:{chat_id}")],
            [InlineKeyboardButton("↩️ К списку", callback_data="monchat_list")],
        ]
    )


async def on_monitor_chats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/monitor_chats — список чатов под мониторингом (см.
    /register_monitor_chat): посмотреть/поменять триггеры или снять чат с
    мониторинга."""
    if not is_owner(update.effective_user.id):
        return
    chats = list_monitor_chats()
    if not chats:
        await update.effective_message.reply_text(
            "Пока нет ни одного чата под мониторингом. Чтобы добавить — вызови /register_monitor_chat внутри нужной группы."
        )
        return
    await update.effective_message.reply_text("Чаты под мониторингом:", reply_markup=_monitor_list_keyboard(chats))


async def on_monitor_chats_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    await query.answer()
    chats = list_monitor_chats()
    if not chats:
        await query.edit_message_text("Пока нет ни одного чата под мониторингом.")
        return
    await query.edit_message_text("Чаты под мониторингом:", reply_markup=_monitor_list_keyboard(chats))


async def on_monitor_chats_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    chat_id = int(query.data.split(":", 1)[1])
    chat = get_monitor_chat(chat_id)
    if not chat:
        await query.answer("Не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(_monitor_card_text(chat), reply_markup=_monitor_card_keyboard(chat_id))


async def on_monitor_chats_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    chat_id = int(query.data.split(":", 1)[1])
    chat = get_monitor_chat(chat_id)
    if not chat:
        await query.answer("Не найден", show_alert=True)
        return
    _awaiting_triggers[str(query.from_user.id)] = {"chat_id": chat_id, "title": chat["title"]}
    await query.answer()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        f"Новый список триггерных слов для «{chat['title']}» — через запятую или каждое слово с новой строки. «нет» — очистить список."
    )


async def on_monitor_chats_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    chat_id = int(query.data.split(":", 1)[1])
    chat = get_monitor_chat(chat_id)
    if not chat:
        await query.answer("Не найден", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(
        f"Точно убрать «{chat['title']}» из мониторинга?",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🗑 Да, убрать", callback_data=f"monchat_removeconfirm:{chat_id}"),
                    InlineKeyboardButton("↩️ Отмена", callback_data=f"monchat_select:{chat_id}"),
                ]
            ]
        ),
    )


async def on_monitor_chats_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer()
        return
    chat_id = int(query.data.split(":", 1)[1])
    remove_monitor_chat(chat_id)
    await query.answer("Убрано")
    await query.edit_message_text("✅ Чат больше не под мониторингом.")
