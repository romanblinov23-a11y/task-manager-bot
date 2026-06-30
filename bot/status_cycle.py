import logging
from datetime import date

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.onboarding import get_display_name, get_username, is_onboarded
from config.projects import PROJECTS
from config.settings import MAX_CLARIFYING_ROUNDS, ROMAN_TELEGRAM_ID
from config.timeutil import now as tz_now
from config.timeutil import today as tz_today
from prompts.status_reply import parse_status_reply
from sheets.comments import append_comment
from sheets.log import append_log_entry
from sheets.tasks import get_all_tasks, update_task

# telegram_user_id -> очередь {"project":, "task": dict, "bot_question": str}
_pending: dict[int, list[dict]] = {}


def _now() -> str:
    return tz_now().strftime("%Y-%m-%d %H:%M:%S")


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
    запускает опрос исполнителя в личке.

    Три случая для задачи с подошедшим сроком:
    - есть telegram_id И онбордился → бот пишет исполнителю сам
    - есть telegram_id, но НЕ онбордился → бот просит Романа запросить статус вручную
    - нет telegram_id вообще → бот сообщает Роману, что исполнитель не идентифицирован"""
    logging.info("Запуск ежедневной проверки статусов задач")
    today = tz_today()
    skipped_no_telegram_id: list[tuple[str, dict]] = []
    skipped_not_onboarded: list[tuple[str, dict, int]] = []

    for project in PROJECTS:
        for task in get_all_tasks(project):
            if not _is_due(task, today):
                continue

            telegram_id_str = task.get("assignee_telegram_id")
            if not telegram_id_str:
                skipped_no_telegram_id.append((project, task))
                continue

            telegram_id = int(telegram_id_str)
            if not is_onboarded(telegram_id):
                skipped_not_onboarded.append((project, task, telegram_id))
                continue

            update_task(project, task["task_id"], last_status_check=_now())
            await _enqueue_question(bot, telegram_id, project, task)

    if skipped_no_telegram_id:
        lines = [
            f"— {p}: «{t['task_text']}» (исполнитель: {t.get('assignee') or '—'})"
            for p, t in skipped_no_telegram_id
        ]
        await bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text="⚠️ Исполнитель не идентифицирован — нет Telegram-привязки:\n" + "\n".join(lines),
        )

    if skipped_not_onboarded:
        lines = []
        for project, task, tid in skipped_not_onboarded:
            username = get_username(tid)
            name = get_display_name(tid)
            tg_ref = f"@{username}" if username else f"(ID {tid}, username неизвестен)"
            lines.append(
                f"📌 {project}: «{task['task_text']}»\n"
                f"   Срок: {task.get('deadline_current') or '—'}\n"
                f"   Исполнитель: {name} — {tg_ref}"
            )
        await bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text=(
                "💬 Нужно запросить статус вручную — эти сотрудники ещё не открыли диалог с ботом:\n\n"
                + "\n\n".join(lines)
            ),
        )


def _status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Сделал", callback_data="status:done"),
        InlineKeyboardButton("📝 Прокомментировать", callback_data="status:comment"),
    ]])


async def _enqueue_question(bot: Bot, telegram_id: int, project: str, task: dict) -> None:
    question = f"Привет! Как дела с задачей «{task['task_text']}»? Срок: {task.get('deadline_current') or '—'}."
    entry = {"project": project, "task": task, "bot_question": question, "unclear_count": 0}
    queue = _pending.setdefault(telegram_id, [])
    queue.append(entry)
    if len(queue) == 1:
        await bot.send_message(chat_id=telegram_id, text=question, reply_markup=_status_keyboard())


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
        entry["unclear_count"] = entry.get("unclear_count", 0) + 1

        if entry["unclear_count"] >= MAX_CLARIFYING_ROUNDS:
            await _escalate_to_roman(context.bot, project, task, result)
        else:
            entry["bot_question"] = result["clarifying_question"]
            await update.effective_message.reply_text(result["clarifying_question"])
            await _maybe_signal_roman(context.bot, project, task, result)
            return True
    else:
        await _apply_status_update(context.bot, project, task, result)

    queue.pop(0)
    if queue:
        await context.bot.send_message(chat_id=user_id, text=queue[0]["bot_question"])
    else:
        del _pending[user_id]

    return True


async def _escalate_to_roman(bot: Bot, project: str, task: dict, result: dict) -> None:
    """Раздел 10: после MAX_CLARIFYING_ROUNDS неясных ответов подряд бот
    перестаёт уточнять у сотрудника и передаёт решение Роману."""
    assignee = task.get("assignee") or "—"
    await bot.send_message(
        chat_id=ROMAN_TELEGRAM_ID,
        text=(
            f"⚠️ Не получилось прояснить статус задачи «{task['task_text']}» ({project}, "
            f"исполнитель: {assignee}) после {MAX_CLARIFYING_ROUNDS} уточнений.\n"
            f"Последний ответ: {result.get('comment_summary') or '—'}"
        ),
    )


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


async def on_status_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопок «✅ Сделал» и «📝 Прокомментировать» в вопросе о статусе."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    queue = _pending.get(user_id)

    if not queue:
        await query.edit_message_text(query.message.text + "\n\n(ответ уже получен)")
        return

    entry = queue[0]
    project, task = entry["project"], entry["task"]
    original_text = query.message.text

    if query.data == "status:done":
        done_result = {
            "status_clear": True,
            "new_status": "выполнена",
            "clarifying_question": None,
            "deadline_changed": False,
            "new_deadline": None,
            "reason": "",
            "needs_help": False,
            "comment_summary": "Сотрудник подтвердил выполнение задачи.",
            "signal_type": "завершение",
        }
        append_comment(project, task["task_id"], "сотрудник", done_result["comment_summary"], related_status="выполнена")
        await query.edit_message_text(original_text + "\n\n✅ Принято, отмечу как выполненное.")
        await _apply_status_update(context.bot, project, task, done_result)

        queue.pop(0)
        if queue:
            await context.bot.send_message(chat_id=user_id, text=queue[0]["bot_question"], reply_markup=_status_keyboard())
        else:
            del _pending[user_id]

    elif query.data == "status:comment":
        follow_up = "Напиши подробнее: что сделано, нужен ли перенос срока или есть сложности?"
        entry["bot_question"] = follow_up
        await query.edit_message_text(original_text + "\n\n📝 Жду твой комментарий:")
        await context.bot.send_message(chat_id=user_id, text=follow_up)
