from telegram import Update
from telegram.ext import ContextTypes

from config.chats import register_chat
from monitoring.managers import is_owner
from monitoring.markets import list_market_names


async def on_register_project(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Самообслуживание для онбординга чата: владелец вызывает эту команду
    внутри рабочей группы, чтобы привязать её к проекту без правки .env
    и перезапуска бота. Посмотреть/сменить/отвязать привязки существующих
    чатов можно в /managers → «💬 Чаты»."""
    if not is_owner(update.effective_user.id):
        return

    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("Эта команда работает только внутри группового чата.")
        return

    projects = list_market_names()
    project = " ".join(context.args) if context.args else ""
    if project not in projects:
        await update.effective_message.reply_text(
            "Укажите проект: /register_project <название>\nДоступные: " + ", ".join(projects)
        )
        return

    register_chat(chat.id, project)
    await update.effective_message.reply_text(f"✅ Этот чат привязан к проекту «{project}».")
