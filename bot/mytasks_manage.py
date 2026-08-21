from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import ROMAN_TELEGRAM_ID
from config.timeutil import fmt_date, parse_date
from config.timeutil import now as tz_now
from monitoring.markets import get_market, get_market_by_name, list_market_names
from tasks.comments import append_comment
from tasks.log import append_log_entry
from tasks.tasks import get_all_tasks, get_task, update_task

# telegram_user_id (str) сотрудника -> {"action": "deadline_date"|"deadline_reason"|
# "close_reason"|"help_reason", "market_id": int, "task_id": str, "new_deadline"?: str}
_pending: dict[str, dict] = {}


def _now() -> str:
    return tz_now().strftime("%Y-%m-%d %H:%M:%S")


def _is_assignee(task: dict, user_id: int) -> bool:
    return bool(task.get("assignee_telegram_id")) and str(task["assignee_telegram_id"]) == str(user_id)


def _find_my_tasks(user_id: int) -> list[tuple[str, dict]]:
    found = []
    for project in list_market_names():
        for task in get_all_tasks(project):
            if _is_assignee(task, user_id) and task.get("status") != "выполнена":
                found.append((project, task))
    return found


def _short_title(task_text: str, limit: int = 40) -> str:
    text = task_text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _mytasks_view(user_id: int) -> tuple[str, InlineKeyboardMarkup | None]:
    found = _find_my_tasks(user_id)
    if not found:
        return "У тебя нет активных задач.", None

    lines = ["📋 Твои задачи в работе:"]
    buttons = []
    for project, task in found:
        deadline = fmt_date(task.get("deadline_current"))
        status = task.get("status") or "—"
        help_mark = " 🆘" if task.get("needs_help") == "да" else ""
        lines.append(f"\n• [{project}] {task['task_text']}\n  Срок: {deadline} | Статус: {status}{help_mark}")
        market = get_market_by_name(project)
        buttons.append(
            [
                InlineKeyboardButton(
                    f"⚙️ {_short_title(task['task_text'])}", callback_data=f"myt:task:{market['id']}:{task['task_id']}"
                )
            ]
        )
    lines.append("\n\nНажми на задачу ниже, чтобы перенести срок, отметить прогресс или попросить помощи.")
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def _task_card_text(project: str, task: dict) -> str:
    deadline = fmt_date(task.get("deadline_current"))
    help_mark = " 🆘 нужна помощь" if task.get("needs_help") == "да" else ""
    return (
        f"📋 {task['task_id']} — {project}\n"
        f"Задача: {task['task_text']}\n"
        f"Статус: {task.get('status')}{help_mark}\n"
        f"Срок: {deadline}"
    )


def _task_card_keyboard(market_id: int, task_id: str, status: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("📅 Перенести срок", callback_data=f"myt:deadline:{market_id}:{task_id}")]]
    if status != "в работе":
        rows.append([InlineKeyboardButton("▶️ Взял в работу", callback_data=f"myt:inprogress:{market_id}:{task_id}")])
    rows.append(
        [
            InlineKeyboardButton("✅ Завершить", callback_data=f"myt:close:{market_id}:{task_id}"),
            InlineKeyboardButton("🆘 Нужна помощь", callback_data=f"myt:help:{market_id}:{task_id}"),
        ]
    )
    rows.append([InlineKeyboardButton("↩️ К списку", callback_data="myt:list")])
    return InlineKeyboardMarkup(rows)


async def _notify_owner(bot, text: str) -> None:
    try:
        await bot.send_message(chat_id=ROMAN_TELEGRAM_ID, text=text)
    except Exception:
        pass


async def on_mytasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/mytasks — сотрудник видит свои активные задачи по всем проектам и
    может ими управлять: перенести срок, отметить прогресс, попросить
    помощи или закрыть задачу — без участия владельца."""
    text, keyboard = _mytasks_view(update.effective_user.id)
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def on_mytasks_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    parts = query.data.split(":")
    action = parts[1]

    if action == "list":
        await query.answer()
        text, keyboard = _mytasks_view(user_id)
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    market_id, task_id = int(parts[2]), parts[3]
    market = get_market(market_id)
    task = get_task(market["name"], task_id) if market else None
    if not task:
        await query.answer("Задача не найдена", show_alert=True)
        return
    if not _is_assignee(task, user_id):
        await query.answer("Это не твоя задача", show_alert=True)
        return
    project = market["name"]

    if action == "task":
        await query.answer()
        await query.edit_message_text(
            _task_card_text(project, task), reply_markup=_task_card_keyboard(market_id, task_id, task.get("status"))
        )
        return

    if action == "deadline":
        _pending[str(user_id)] = {"action": "deadline_date", "market_id": market_id, "task_id": task_id}
        await query.answer()
        await query.edit_message_text(f"Новый срок для «{task['task_text']}» (например, 15.09.2026 или «через неделю»):")
        return

    if action == "inprogress":
        if task.get("status") == "в работе":
            await query.answer("Уже в работе")
            return
        await query.answer()
        old_status = task.get("status", "")
        update_task(project, task_id, status="в работе")
        append_log_entry(project, task_id, "смена_статуса", old_value=old_status, new_value="в работе", reason_comment="сотрудник взял в работу")
        updated = get_task(project, task_id)
        await query.edit_message_text(
            _task_card_text(project, updated), reply_markup=_task_card_keyboard(market_id, task_id, updated.get("status"))
        )
        return

    if action == "close":
        _pending[str(user_id)] = {"action": "close_reason", "market_id": market_id, "task_id": task_id}
        await query.answer()
        await query.edit_message_text(f"Что сделано по «{task['task_text']}»? Напиши комментарий к закрытию:")
        return

    if action == "help":
        _pending[str(user_id)] = {"action": "help_reason", "market_id": market_id, "task_id": task_id}
        await query.answer()
        await query.edit_message_text(f"Опиши коротко, в чём нужна помощь по «{task['task_text']}»:")
        return

    await query.answer()


async def on_mytasks_manage_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текстовый ввод для управления своими задачами из /mytasks
    (перенос срока, комментарий к закрытию, запрос помощи). Возвращает True,
    если сообщение обработано — по конвенции остальных claim-хендлеров в
    on_private_text."""
    user_id = update.effective_user.id
    state = _pending.get(str(user_id))
    if not state:
        return False

    market = get_market(state["market_id"])
    task_id = state["task_id"]
    task = get_task(market["name"], task_id) if market else None
    if not task or not _is_assignee(task, user_id):
        del _pending[str(user_id)]
        await update.effective_message.reply_text("Задача больше недоступна.")
        return True
    project = market["name"]
    text = (update.effective_message.text or "").strip()

    if state["action"] == "deadline_date":
        iso = parse_date(text)
        if not iso:
            await update.effective_message.reply_text(f"Не понял дату «{text}». Попробуйте ещё раз, например ДД.ММ.ГГГГ.")
            return True
        state["action"] = "deadline_reason"
        state["new_deadline"] = iso
        await update.effective_message.reply_text("Коротко: почему переносим срок?")
        return True

    if state["action"] == "deadline_reason":
        if not text:
            await update.effective_message.reply_text("Комментарий не может быть пустым, напишите ещё раз:")
            return True
        del _pending[str(user_id)]
        old_deadline = task.get("deadline_current") or ""
        new_deadline = state["new_deadline"]
        update_task(project, task_id, deadline_current=new_deadline)
        append_log_entry(project, task_id, "перенос_срока", old_value=old_deadline, new_value=new_deadline, reason_comment=text)
        append_comment(project, task_id, "сотрудник", text, related_status=task.get("status", ""))
        await _notify_owner(
            context.bot,
            f"📅 Перенос срока по задаче «{task['task_text']}» ({project}).\n"
            f"Новый срок: {fmt_date(new_deadline)}. Причина: {text}",
        )
        updated = get_task(project, task_id)
        await update.effective_message.reply_text(
            f"✅ Срок перенесён на {fmt_date(new_deadline)}.\n\n{_task_card_text(project, updated)}",
            reply_markup=_task_card_keyboard(state["market_id"], task_id, updated.get("status")),
        )
        return True

    if state["action"] == "close_reason":
        if not text:
            await update.effective_message.reply_text("Комментарий не может быть пустым, напишите ещё раз:")
            return True
        del _pending[str(user_id)]
        old_status = task.get("status", "")
        update_task(project, task_id, status="выполнена", closed_at=_now(), last_comment=text)
        append_log_entry(project, task_id, "завершение", old_value=old_status, new_value="выполнена", reason_comment=text)
        append_comment(project, task_id, "сотрудник", text, related_status="выполнена")
        await _notify_owner(context.bot, f"✅ Задача «{task['task_text']}» ({project}) выполнена.\nКомментарий: {text}")
        await update.effective_message.reply_text("✅ Готово, отметил задачу выполненной.")
        return True

    if state["action"] == "help_reason":
        if not text:
            await update.effective_message.reply_text("Комментарий не может быть пустым, напишите ещё раз:")
            return True
        del _pending[str(user_id)]
        old_needs_help = task.get("needs_help", "нет")
        if old_needs_help != "да":
            update_task(project, task_id, needs_help="да")
            append_log_entry(project, task_id, "запрос_помощи", old_value=old_needs_help, new_value="да", reason_comment=text)
        append_comment(project, task_id, "сотрудник", text, related_status=task.get("status", ""))
        await _notify_owner(context.bot, f"🆘 Просьба о помощи по задаче «{task['task_text']}» ({project}).\nКомментарий: {text}")
        updated = get_task(project, task_id)
        await update.effective_message.reply_text(
            f"✅ Передал Роме, что нужна помощь.\n\n{_task_card_text(project, updated)}",
            reply_markup=_task_card_keyboard(state["market_id"], task_id, updated.get("status")),
        )
        return True

    del _pending[str(user_id)]
    return True
