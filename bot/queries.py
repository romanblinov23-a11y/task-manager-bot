from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from bot.onboarding import get_onboarded_employees
from config.chats import get_all_bindings, get_project_for_chat
from config.projects import PROJECTS
from config.settings import ROMAN_TELEGRAM_ID, STALE_DAYS
from config.timeutil import now_naive as tz_now_naive
from sheets.client import open_project_spreadsheet
from sheets.schema import LOG_SHEET
from sheets.tasks import get_all_tasks


def _is_roman(update: Update) -> bool:
    return str(update.effective_user.id) == str(ROMAN_TELEGRAM_ID)


def _task_line(task: dict, *, show_project: str | None = None) -> str:
    project_part = f"[{show_project}] " if show_project else ""
    deadline = task.get("deadline_current") or "—"
    help_mark = " 🆘" if task.get("needs_help") == "да" else ""
    assignee = task.get("assignee") or "—"
    return f"{project_part}{task['task_id']} «{task['task_text']}» — {task.get('status')}, срок {deadline}, исполнитель: {assignee}{help_mark}"


async def on_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status <проект> — открытые задачи проекта прямо сейчас."""
    if not _is_roman(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Укажите проект: /status <название>\nДоступные: " + ", ".join(PROJECTS)
        )
        return

    project = " ".join(context.args)
    if project not in PROJECTS:
        await update.effective_message.reply_text(f"Неизвестный проект «{project}». Доступные: " + ", ".join(PROJECTS))
        return

    tasks = [t for t in get_all_tasks(project) if t.get("status") != "выполнена"]
    if not tasks:
        await update.effective_message.reply_text(f"В проекте «{project}» нет открытых задач.")
        return

    lines = [f"📋 Открытые задачи — {project}"] + [_task_line(t) for t in tasks]
    await update.effective_message.reply_text("\n".join(lines))


async def on_employee_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/employee <имя> — все задачи сотрудника по всем проектам."""
    if not _is_roman(update):
        return

    if not context.args:
        await update.effective_message.reply_text("Укажите имя: /employee <имя>")
        return

    raw_name = " ".join(context.args)
    name_norm = raw_name.strip().lower()

    found = []
    for project in PROJECTS:
        for task in get_all_tasks(project):
            if (task.get("assignee") or "").strip().lower() == name_norm:
                found.append((project, task))

    if not found:
        await update.effective_message.reply_text(f"Не нашёл задач на «{raw_name}».")
        return

    lines = [f"📋 Задачи — {raw_name}"] + [_task_line(t, show_project=p) for p, t in found]
    await update.effective_message.reply_text("\n".join(lines))


def _count_transfers(project: str) -> dict[str, int]:
    log_entries = open_project_spreadsheet(project).worksheet(LOG_SHEET).get_all_records()
    counts: dict[str, int] = {}
    for entry in log_entries:
        if entry["event_type"] == "перенос_срока":
            counts[entry["task_id"]] = counts.get(entry["task_id"], 0) + 1
    return counts


async def on_stuck_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stuck — задачи с 2+ переносами или давно без обновления, по всем проектам."""
    if not _is_roman(update):
        return

    lines = ["📋 Подвисшие задачи"]
    found_any = False
    now = tz_now_naive()

    for project in PROJECTS:
        transfers_by_task = _count_transfers(project)
        for task in get_all_tasks(project):
            if task.get("status") == "выполнена":
                continue

            transfers = transfers_by_task.get(task["task_id"], 0)
            last_check_raw = task.get("last_status_check") or task.get("created_at")
            try:
                last_check = datetime.strptime(last_check_raw, "%Y-%m-%d %H:%M:%S")
                stale_days = (now - last_check).days
            except (ValueError, TypeError):
                stale_days = None

            reasons = []
            if transfers >= 2:
                reasons.append(f"{transfers} переноса")
            if stale_days is not None and stale_days >= STALE_DAYS:
                reasons.append(f"{stale_days} дн. без обновления")

            if reasons:
                lines.append(f"{_task_line(task, show_project=project)} ({', '.join(reasons)})")
                found_any = True

    if not found_any:
        await update.effective_message.reply_text("Подвисших задач не нашёл.")
        return
    await update.effective_message.reply_text("\n".join(lines))


async def on_needhelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/needhelp — открытые задачи, где сотрудник просил помощи, по всем проектам."""
    if not _is_roman(update):
        return

    lines = ["🆘 Задачи, где нужна помощь"]
    found_any = False
    for project in PROJECTS:
        for task in get_all_tasks(project):
            if task.get("needs_help") == "да" and task.get("status") != "выполнена":
                lines.append(_task_line(task, show_project=project))
                found_any = True

    if not found_any:
        await update.effective_message.reply_text("Запросов помощи сейчас нет.")
        return
    await update.effective_message.reply_text("\n".join(lines))


async def on_onboarded_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/onboarded — кто прошёл онбординг и какие чаты привязаны к каким проектам."""
    if not _is_roman(update):
        return

    lines = ["👥 Сотрудники, прошедшие онбординг:"]
    employees = get_onboarded_employees()
    if not employees:
        lines.append("Пока никто не онбордился.")
    else:
        for emp in employees:
            display_name = emp["real_name"] or emp["full_name"] or "(без имени)"
            username_part = f"@{emp['username']}" if emp["username"] else "без username"
            projects = sorted({p for c in emp["chats"] if (p := get_project_for_chat(c))})
            projects_part = ", ".join(projects) if projects else "не видели в привязанных чатах"
            lines.append(f"- {display_name} ({username_part}, ID {emp['user_id']}) — {projects_part}")

    lines.append("")
    lines.append("🔗 Привязки чатов к проектам:")
    bindings = get_all_bindings()
    if not bindings:
        lines.append("Нет привязанных чатов.")
    else:
        for chat_id, project, source in bindings:
            source_label = "из .env" if source == "env" else "через /register_project"
            lines.append(f"- {project}: chat_id {chat_id} ({source_label})")

    await update.effective_message.reply_text("\n".join(lines))
