from collections import defaultdict
from datetime import date, datetime, timedelta

from telegram import Bot, Update
from telegram.ext import ContextTypes

from config.settings import ROMAN_TELEGRAM_ID, STALE_DAYS
from config.timeutil import now_naive as tz_now_naive
from config.timeutil import today as tz_today
from monitoring.markets import list_market_names
from prompts.weekly_analytics import generate_weekly_report
from tasks.log import get_log_entries
from tasks.tasks import get_all_tasks


def _is_roman(update: Update) -> bool:
    return str(update.effective_user.id) == str(ROMAN_TELEGRAM_ID)


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _empty_employee_stats() -> dict:
    return {
        "assignee_telegram_id": "",
        "в_работе": 0,
        "закрыто_за_неделю": 0,
        "просрочено": 0,
        "переносов_за_неделю": 0,
        "причины_переносов": [],
        "запросов_помощи_за_неделю": 0,
    }


def _build_project_stats(project: str, stale_days: int, today: date, week_ago: datetime) -> dict:
    tasks = get_all_tasks(project)
    log_entries = get_log_entries(project)

    log_by_task: dict[str, list[dict]] = defaultdict(list)
    for entry in log_entries:
        log_by_task[entry["task_id"]].append(entry)

    employees: dict[str, dict] = defaultdict(_empty_employee_stats)
    stuck_tasks, stale_tasks, close_no_progress = [], [], []

    for task in tasks:
        assignee = task.get("assignee") or "—"
        stats = employees[assignee]
        if not stats["assignee_telegram_id"]:
            stats["assignee_telegram_id"] = task.get("assignee_telegram_id", "")

        status = task.get("status")
        deadline = _parse_date(task.get("deadline_current"))
        is_overdue = status == "просрочена" or (
            deadline is not None and deadline < today and status != "выполнена"
        )

        if status != "выполнена":
            stats["в_работе"] += 1
        if is_overdue:
            stats["просрочено"] += 1

        closed_at = _parse_dt(task.get("closed_at"))
        if closed_at and closed_at >= week_ago:
            stats["закрыто_за_неделю"] += 1

        entries = log_by_task.get(task["task_id"], [])
        transfers = [e for e in entries if e["event_type"] == "перенос_срока"]
        transfers_week = [e for e in transfers if (_parse_dt(e["timestamp"]) or datetime.min) >= week_ago]
        stats["переносов_за_неделю"] += len(transfers_week)
        stats["причины_переносов"].extend(e["reason_comment"] for e in transfers_week if e["reason_comment"])

        help_requests_week = [
            e
            for e in entries
            if e["event_type"] == "запрос_помощи" and (_parse_dt(e["timestamp"]) or datetime.min) >= week_ago
        ]
        stats["запросов_помощи_за_неделю"] += len(help_requests_week)

        if len(transfers) >= 2:
            stuck_tasks.append(
                {"task_id": task["task_id"], "task_text": task["task_text"], "переносов_всего": len(transfers)}
            )

        last_check = _parse_dt(task.get("last_status_check")) or _parse_dt(task.get("created_at"))
        if status != "выполнена" and last_check and (tz_now_naive() - last_check).days >= stale_days:
            stale_tasks.append(
                {
                    "task_id": task["task_id"],
                    "task_text": task["task_text"],
                    "дней_без_обновления": (tz_now_naive() - last_check).days,
                }
            )

        if status == "новая" and deadline is not None and 0 <= (deadline - today).days <= 3:
            close_no_progress.append(
                {"task_id": task["task_id"], "task_text": task["task_text"], "срок": task.get("deadline_current")}
            )

    return {
        "project_name": project,
        "employee_stats": [{"assignee": name, **stats} for name, stats in employees.items()],
        "task_stats": {
            "застрявшие_2_плюс_переноса": stuck_tasks,
            "без_обновления_дольше_stale_days": stale_tasks,
            "близкий_срок_без_прогресса": close_no_progress,
        },
    }


def _build_multi_project_employees(projects_stats: list[dict]) -> list[dict]:
    by_name: dict[str, list[dict]] = defaultdict(list)
    for proj_stat in projects_stats:
        for emp in proj_stat["employee_stats"]:
            by_name[emp["assignee"]].append({"project": proj_stat["project_name"], **emp})

    return [{"assignee": name, "projects": entries} for name, entries in by_name.items() if len(entries) > 1]


def build_weekly_report(stale_days: int = STALE_DAYS) -> str:
    today = tz_today()
    week_ago = tz_now_naive() - timedelta(days=7)

    projects_stats = [_build_project_stats(p, stale_days, today, week_ago) for p in list_market_names()]
    multi_project_employees = _build_multi_project_employees(projects_stats)

    return generate_weekly_report(projects_stats, multi_project_employees, stale_days=stale_days)


async def send_weekly_report(bot: Bot) -> None:
    await bot.send_message(chat_id=ROMAN_TELEGRAM_ID, text=build_weekly_report())


async def on_weekly_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Раздел 7: аналитика доступна по запросу в любой момент, не только по расписанию."""
    if not _is_roman(update):
        return
    await update.effective_message.reply_text("Считаю отчёт за неделю...")
    await send_weekly_report(context.bot)
