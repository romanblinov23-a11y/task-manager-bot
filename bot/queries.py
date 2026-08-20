from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import ROMAN_TELEGRAM_ID, STALE_DAYS
from config.timeutil import fmt_date
from config.timeutil import now_naive as tz_now_naive
from monitoring.markets import list_market_names
from tasks.log import get_log_entries
from tasks.tasks import get_all_tasks


def _is_roman(update: Update) -> bool:
    return str(update.effective_user.id) == str(ROMAN_TELEGRAM_ID)


def task_line(task: dict, *, show_project: str | None = None) -> str:
    project_part = f"[{show_project}] " if show_project else ""
    deadline = fmt_date(task.get("deadline_current"))
    help_mark = " 🆘" if task.get("needs_help") == "да" else ""
    assignee = task.get("assignee") or "—"
    return f"{project_part}{task['task_id']} «{task['task_text']}» — {task.get('status')}, срок {deadline}, исполнитель: {assignee}{help_mark}"


def _count_transfers(project: str) -> dict[str, int]:
    log_entries = get_log_entries(project)
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

    for project in list_market_names():
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
                lines.append(f"{task_line(task, show_project=project)} ({', '.join(reasons)})")
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
    for project in list_market_names():
        for task in get_all_tasks(project):
            if task.get("needs_help") == "да" and task.get("status") != "выполнена":
                lines.append(task_line(task, show_project=project))
                found_any = True

    if not found_any:
        await update.effective_message.reply_text("Запросов помощи сейчас нет.")
        return
    await update.effective_message.reply_text("\n".join(lines))


async def on_mytasks_command(update, context):
    """/mytasks — сотрудник видит свои активные задачи по всем проектам."""
    user_id = str(update.effective_user.id)
    found = []
    for project in list_market_names():
        for task in get_all_tasks(project):
            if str(task.get("assignee_telegram_id")) == user_id and task.get("status") != "выполнена":
                found.append((project, task))

    if not found:
        await update.effective_message.reply_text("У тебя нет активных задач.")
        return

    lines = ["📋 Твои задачи в работе:"]
    for project, task in found:
        deadline = fmt_date(task.get("deadline_current"))
        status = task.get("status") or "—"
        lines.append(f"\n• [{project}] {task['task_text']}\n  Срок: {deadline} | Статус: {status}")
    await update.effective_message.reply_text("\n".join(lines))
