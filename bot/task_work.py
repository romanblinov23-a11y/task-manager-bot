from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.dashboard_tasks_cmd import on_dashboard_tasks_command
from bot.queries import on_needhelp_command, on_stuck_command
from bot.task_manage import on_employee_command, on_status_command
from bot.weekly_report import on_weekly_command
from config.settings import ROMAN_TELEGRAM_ID


def _is_roman(update: Update) -> bool:
    return str(update.effective_user.id) == str(ROMAN_TELEGRAM_ID)


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📌 Задачи по проекту", callback_data="taskwork:status")],
            [InlineKeyboardButton("👤 Задачи сотрудника", callback_data="taskwork:employee")],
            [InlineKeyboardButton("🐌 Подвисшие задачи", callback_data="taskwork:stuck")],
            [InlineKeyboardButton("🆘 Нужна помощь", callback_data="taskwork:needhelp")],
            [InlineKeyboardButton("📊 Дашборд по задачам", callback_data="taskwork:dashboard")],
            [InlineKeyboardButton("🗓 Недельный отчёт", callback_data="taskwork:weekly")],
        ]
    )


# Каждый пункт вызывает ровно тот же хендлер, что и у соответствующей
# отдельной команды (status/employee и т.д.) — им подходит и Update от
# CallbackQuery: effective_message/effective_user разрешаются PTB и оттуда
# тоже, а context.args (используется в status/employee) для колбэка просто
# пуст, что даёт тот же путь, что и вызов команды без аргумента (кнопки
# выбора проекта/сотрудника).
_DISPATCH = {
    "status": on_status_command,
    "employee": on_employee_command,
    "stuck": on_stuck_command,
    "needhelp": on_needhelp_command,
    "dashboard": on_dashboard_tasks_command,
    "weekly": on_weekly_command,
}


async def on_task_work_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/task_work — единая точка входа во все запросы по трекеру задач
    (статус проекта, задачи сотрудника, подвисшие, нужна помощь, дашборд,
    недельный отчёт), вместо шести отдельных команд в меню владельца."""
    if not _is_roman(update):
        return
    await update.effective_message.reply_text("Что показать по задачам?", reply_markup=_menu_keyboard())


async def on_task_work_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _is_roman(update):
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
