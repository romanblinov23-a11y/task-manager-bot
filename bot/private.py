from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.competitors import on_add_competitor_reply
from bot.confirmation import on_edit_reply, send_confirmation_cards
from bot.manager_admin import on_manager_admin_reply
from bot.monitoring_flow import on_monitoring_reply
from bot.onboarding import on_employee_message
from bot.status_cycle import on_employee_reply
from config.projects import PROJECTS
from config.settings import ROMAN_CHAT_NAME, ROMAN_TELEGRAM_ID
from config.timeutil import now as tz_now
from prompts.extraction import extract_tasks
from sheets.tasks import get_all_tasks


def _is_roman(update: Update) -> bool:
    return str(update.effective_user.id) == str(ROMAN_TELEGRAM_ID)


# telegram_user_id -> очередь задач, ожидающих выбора проекта Романом
_awaiting_project: dict[int, list[dict]] = {}


def _project_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(p, callback_data=f"project:{p}")] for p in PROJECTS])


def _find_project_by_assignee(name: str) -> str | None:
    """Код-сторонняя проверка: если исполнитель уже встречается ровно в
    одном из трёх проектов — определяем проект по факту, без участия Claude
    (раздел 2.2: "по исполнителю, если он уже встречается в одной из трёх таблиц")."""
    if not name:
        return None
    found = {
        project
        for project in PROJECTS
        for task in get_all_tasks(project)
        if task.get("assignee", "").strip().lower() == name.strip().lower()
    }
    return next(iter(found)) if len(found) == 1 else None


async def on_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раздел 2.2 PROJECT_SPEC.md: личное сообщение Романа — разовая задача
    или протокол встречи, Claude сам решает по содержанию. Сообщения от
    кого-либо ещё — это сотрудники.

    Открытый вопрос о статусе (раздел 4) проверяется первым для ВСЕХ,
    включая Романа — он тоже может быть исполнителем задачи, и его ответ
    на статус-вопрос не должен попасть в обработку как новая задача."""
    if await on_employee_reply(update, context):
        return

    if await on_manager_admin_reply(update, context):
        return

    if await on_add_competitor_reply(update, context):
        return

    if await on_monitoring_reply(update, context):
        return

    if not _is_roman(update):
        await on_employee_message(update, context)
        return

    if await on_edit_reply(update, context):
        return

    message = update.effective_message
    now = tz_now().strftime("%Y-%m-%d %H:%M")
    tasks = extract_tasks(f"[{now}] {ROMAN_CHAT_NAME}: {message.text}", project_name=None)
    if not tasks:
        await message.reply_text("Не нашёл в этом сообщении задач для фиксации.")
        return

    await _route_extracted_tasks(context.bot, update.effective_user.id, tasks, source="manual")


async def on_private_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раздел 2.3: текстовый файл протокола встречи — тот же путь обработки,
    может дать задачи на несколько разных проектов одновременно. Загрузка
    протоколов — функция Романа, не сотрудников."""
    if not _is_roman(update):
        return

    document = update.effective_message.document
    file = await document.get_file()
    raw_bytes = await file.download_as_bytearray()
    text = bytes(raw_bytes).decode("utf-8", errors="replace")

    tasks = extract_tasks(text, project_name=None)
    if not tasks:
        await update.effective_message.reply_text("Не нашёл в файле задач для фиксации.")
        return

    await _route_extracted_tasks(context.bot, update.effective_user.id, tasks, source="protocol")


async def _route_extracted_tasks(bot: Bot, user_id: int, tasks: list[dict], *, source: str) -> None:
    by_project: dict[str, list[dict]] = {}
    unclear: list[dict] = []

    for task in tasks:
        project = task.get("project")
        if project and not task.get("project_unclear"):
            by_project.setdefault(project, []).append(task)
            continue
        resolved = _find_project_by_assignee(task.get("assignee", ""))
        if resolved:
            by_project.setdefault(resolved, []).append(task)
        else:
            unclear.append(task)

    for project, project_tasks in by_project.items():
        await send_confirmation_cards(bot, project_tasks, project=project, source=source)

    if unclear:
        for task in unclear:
            task["_source"] = source
        _awaiting_project.setdefault(user_id, []).extend(unclear)
        await _prompt_next_project(bot, user_id)


async def _prompt_next_project(bot: Bot, user_id: int) -> None:
    pending = _awaiting_project.get(user_id)
    if not pending:
        return
    task = pending[0]
    await bot.send_message(
        chat_id=user_id,
        text=f"⚠️ Не могу определить проект для задачи:\n«{task.get('task_text')}»\n\nК какому проекту она относится?",
        reply_markup=_project_keyboard(),
    )


async def on_project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    _, project = query.data.split(":", 1)
    pending = _awaiting_project.get(user_id)
    if not pending:
        await query.edit_message_text("Эта задача уже неактуальна.")
        return

    task = pending.pop(0)
    source = task.pop("_source", "manual")
    await query.edit_message_text(f"Проект: {project}")
    await send_confirmation_cards(context.bot, [task], project=project, source=source)

    if pending:
        await _prompt_next_project(context.bot, user_id)
    else:
        del _awaiting_project[user_id]
