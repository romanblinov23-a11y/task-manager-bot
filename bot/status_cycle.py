import logging
from datetime import date, datetime

from telegram import Bot, Update
from telegram.ext import ContextTypes

from config.projects import PROJECTS
from config.settings import ROMAN_TELEGRAM_ID
from prompts.status_reply import parse_status_reply
from sheets.comments import append_comment
from sheets.log import append_log_entry
from sheets.tasks import get_all_tasks, update_task

# telegram_user_id -> очередь {"project":, "task": dict, "bot_question": str}
_pending: dict[int, list[dict]] = {}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _is_due(task: dict, today: date) -> bool:
    deadline = task.get("deadline_current")
    if not deadline:
        return False
    try:
        deadline_date = date.fromisoformat(deadline)
    except ValueError:
        return False
    if deadline_date > today:
        return False
    if task.get("status") == "выполнена":
        return False
    last_check = task.get("last_status_check") or ""
    if last_check.startswith(today.isoformat()):
        return False
    return True


async def run_status_check(bot: Bot) -> None:
    """APScheduler job — раздел 4 PROJECT_SPEC.md. Проверяет все три
    таблицы на задачи с подошедшим/просроченным deadline_current и
    запускает опрос исполнителя в личке."""
    logging.info("Запуск ежедневной проверки статусов задач")
    today = date.today()
    skipped_no_telegram_id: list[tuple[str, dict]] = []

    for project in PROJECTS:
        for task in get_all_tasks(project):
            if not _is_due(task, today):
                continue

            telegram_id = task.get("assignee_telegram_id")
            if not telegram_id:
                skipped_no_telegram_id.append((project, task))
                continue

            update_task(project, task["task_id"], last_status_check=_now())
            await _enqueue_question(bot, int(telegram_id), project, task)

    if skipped_no_telegram_id:
        lines = [
            f"— {p}: «{t['task_text']}» (исполнитель: {t.get('assignee') or '—'})"
            for p, t in skipped_no_telegram_id
        ]
        await bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text="⚠️ Не смог запросить статус — у исполнителя нет привязанного Telegram "
            "(не онбордился):\n" + "\n".join(lines),
        )


async def _enqueue_question(bot: Bot, telegram_id: int, project: str, task: dict) -> None:
    question = f"Привет! Как дела с задачей «{task['task_text']}»? Срок: {task.get('deadline_current') or '—'}."
    entry = {"project": project, "task": task, "bot_question": question}
    queue = _pending.setdefault(telegram_id, [])
    queue.append(entry)
    if len(queue) == 1:
        await bot.send_message(chat_id=telegram_id, text=question)


async def on_employee_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Если у сотрудника есть открытый вопрос о статусе — разбирает его
    ответ Промптом 2 и, при необходимости, повторяет уточняющий вопрос.
    Возвращает True, если ответ обработан в рамках этого цикла."""
    user_id = update.effective_user.id
    queue = _pending.get(user_id)
    if not queue:
        return False

    entry = queue[0]
    project, task, bot_question = entry["project"], entry["task"], entry["bot_question"]
    employee_reply = update.effective_message.text

    result = parse_status_reply(
        task_text=task["task_text"],
        deadline_current=task.get("deadline_current") or "",
        status=task.get("status") or "",
        bot_question=bot_question,
        employee_reply=employee_reply,
    )

    append_comment(
        project,
        task["task_id"],
        "сотрудник",
        result["comment_summary"],
        related_status=result.get("new_status") or task.get("status", ""),
    )

    if not result["status_clear"]:
        _log_help_request_if_new(project, task, result)
        entry["bot_question"] = result["clarifying_question"]
        await update.effective_message.reply_text(result["clarifying_question"])
        await _maybe_signal_roman(context.bot, project, task, result)
        return True

    await _apply_status_update(context.bot, project, task, result)

    queue.pop(0)
    if queue:
        await context.bot.send_message(chat_id=user_id, text=queue[0]["bot_question"])
    else:
        del _pending[user_id]

    return True


def _log_help_request_if_new(project: str, task: dict, result: dict) -> None:
    old_needs_help = task.get("needs_help", "нет")
    if result.get("needs_help") and old_needs_help != "да":
        append_log_entry(
            project,
            task["task_id"],
            "запрос_помощи",
            old_value=old_needs_help,
            new_value="да",
            reason_comment=result.get("comment_summary", ""),
        )
        task["needs_help"] = "да"


async def _apply_status_update(bot: Bot, project: str, task: dict, result: dict) -> None:
    old_status = task.get("status", "")
    old_deadline = task.get("deadline_current") or ""
    _log_help_request_if_new(project, task, result)

    updates = {
        "status": result["new_status"],
        "last_comment": result["comment_summary"],
        "needs_help": "да" if result["needs_help"] else "нет",
    }
    if result["deadline_changed"] and result.get("new_deadline"):
        updates["deadline_current"] = result["new_deadline"]
    if result["new_status"] == "выполнена":
        updates["closed_at"] = _now()

    update_task(project, task["task_id"], **updates)

    if result["deadline_changed"] and result.get("new_deadline"):
        append_log_entry(
            project,
            task["task_id"],
            "перенос_срока",
            old_value=old_deadline,
            new_value=result["new_deadline"],
            reason_comment=result.get("reason", ""),
        )
    elif result["new_status"] == "выполнена":
        append_log_entry(
            project,
            task["task_id"],
            "завершение",
            old_value=old_status,
            new_value="выполнена",
            reason_comment=result.get("reason", ""),
        )
    elif result["new_status"] != old_status:
        append_log_entry(
            project,
            task["task_id"],
            "смена_статуса",
            old_value=old_status,
            new_value=result["new_status"],
            reason_comment=result.get("reason", ""),
        )

    await _maybe_signal_roman(bot, project, task, result)


async def _maybe_signal_roman(bot: Bot, project: str, task: dict, result: dict) -> None:
    """Раздел 5: мгновенные сигналы Роману, без батчинга. Приоритет уже
    разрешён внутри Промпта 2 (запрос_помощи > перенос_срока > завершение) —
    signal_type приходит как единственное значение."""
    signal_type = result.get("signal_type")
    if not signal_type or signal_type == "нет":
        return

    assignee = task.get("assignee") or "—"
    messages = {
        "запрос_помощи": (
            f"🆘 {assignee} просит помощи по задаче «{task['task_text']}» ({project}).\n"
            f"Комментарий: {result.get('comment_summary')}"
        ),
        "перенос_срока": (
            f"📅 Перенос срока по задаче «{task['task_text']}» ({project}), исполнитель: {assignee}.\n"
            f"Новый срок: {result.get('new_deadline') or '—'}. Причина: {result.get('reason') or '—'}"
        ),
        "завершение": f"✅ Задача «{task['task_text']}» ({project}) выполнена, исполнитель: {assignee}.",
    }
    text = messages.get(signal_type)
    if text:
        await bot.send_message(chat_id=ROMAN_TELEGRAM_ID, text=text)
