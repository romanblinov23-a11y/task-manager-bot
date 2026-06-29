from datetime import date

from telegram import Bot

from config.projects import PROJECTS
from config.settings import ROMAN_TELEGRAM_ID
from sheets.tasks import get_all_tasks


def _task_label(task: dict) -> str:
    return f"{task['task_text']} ({task.get('assignee') or '—'})"


def build_daily_report() -> str:
    """Раздел 6 PROJECT_SPEC.md: прямой подсчёт по Листу 1, без Claude."""
    today_str = date.today().isoformat()
    sections = []

    for project in PROJECTS:
        tasks = get_all_tasks(project)
        due_today = [t for t in tasks if t.get("deadline_current") == today_str]
        overdue = [
            t
            for t in tasks
            if t.get("deadline_current")
            and t["deadline_current"] < today_str
            and t.get("status") != "выполнена"
        ]
        done_today = [t for t in due_today if t.get("status") == "выполнена"]
        pending_today = [t for t in due_today if t.get("status") != "выполнена"]
        needs_help = [t for t in tasks if t.get("needs_help") == "да" and t.get("status") != "выполнена"]

        lines = [f"🏢 {project}"]
        if not due_today and not overdue and not needs_help:
            lines.append("Без движения по срокам сегодня.")
        else:
            if done_today:
                lines.append("✅ Закрыто в срок: " + "; ".join(_task_label(t) for t in done_today))
            if pending_today:
                lines.append("⏳ Срок сегодня, не закрыто: " + "; ".join(_task_label(t) for t in pending_today))
            if overdue:
                lines.append("⚠️ Просрочено: " + "; ".join(_task_label(t) for t in overdue))
            if needs_help:
                lines.append("🆘 Нужна помощь: " + "; ".join(_task_label(t) for t in needs_help))
        sections.append("\n".join(lines))

    header = f"📋 Ежедневный отчёт ({date.today().strftime('%d.%m.%Y')})"
    return header + "\n\n" + "\n\n".join(sections)


async def send_daily_report(bot: Bot) -> None:
    await bot.send_message(chat_id=ROMAN_TELEGRAM_ID, text=build_daily_report())
