from io import BytesIO

from telegram import InputFile, Update
from telegram.ext import ContextTypes

from monitoring.managers import is_owner
from tasks.dashboard import generate_tasks_dashboard


async def on_dashboard_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/dashboard_tasks — полная аналитика по трекеру задач сразу по всем
    проектам: статусы, загрузка сотрудников, переносы сроков, подвисшие
    задачи, запросы помощи. Только для владельца."""
    if not is_owner(update.effective_user.id):
        return
    await update.effective_message.reply_text("Собираю дашборд по задачам…")
    filename, html = generate_tasks_dashboard()
    await update.effective_message.reply_document(document=InputFile(BytesIO(html.encode("utf-8")), filename=filename))
