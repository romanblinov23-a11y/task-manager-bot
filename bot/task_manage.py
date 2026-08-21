from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.onboarding import get_display_name, get_onboarded_employees
from bot.queries import task_line
from bot.status_cycle import ask_task_status_now
from config.settings import ROMAN_TELEGRAM_ID
from config.timeutil import fmt_date, parse_date
from monitoring.markets import get_market, get_market_by_name, list_markets, list_market_names
from tasks.comments import append_comment
from tasks.log import append_log_entry
from tasks.tasks import get_all_tasks, get_task, move_task, update_task

# telegram_user_id (str) владельца -> список имён для последнего показа /employee (по индексу в кнопках)
_employee_options: dict[str, list[str]] = {}

# telegram_user_id (str) владельца -> {"field": "deadline"|"delegate_comment", "market_id": int, "task_id": str,
# "target_id"?: int, "target_name"?: str}
_awaiting_edit: dict[str, dict] = {}


def _is_roman(update: Update) -> bool:
    return str(update.effective_user.id) == str(ROMAN_TELEGRAM_ID)


def _project_picker_keyboard(projects: list[str]) -> InlineKeyboardMarkup:
    buttons = []
    for name in projects:
        market = get_market_by_name(name)
        buttons.append([InlineKeyboardButton(name, callback_data=f"tmg:openproj:{market['id']}")])
    return InlineKeyboardMarkup(buttons)


def _build_project_tasks_view(project: str) -> tuple[str, InlineKeyboardMarkup | None]:
    market = get_market_by_name(project)
    tasks = [t for t in get_all_tasks(project) if t.get("status") != "выполнена"]
    if not tasks:
        return f"В проекте «{project}» нет открытых задач.", None
    lines = [f"📋 Открытые задачи — {project}"] + [task_line(t) for t in tasks]
    buttons = [
        [InlineKeyboardButton(f"{t['task_id']} — {t.get('assignee') or '—'}", callback_data=f"tmg:task:{market['id']}:{t['task_id']}")]
        for t in tasks
    ]
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def _distinct_assignees() -> list[str]:
    names = set()
    for project in list_market_names():
        for t in get_all_tasks(project):
            name = (t.get("assignee") or "").strip()
            if name:
                names.add(name)
    return sorted(names)


def _build_employee_tasks_view(raw_name: str) -> tuple[str, InlineKeyboardMarkup | None]:
    name_norm = raw_name.strip().lower()
    found = []
    for project in list_market_names():
        for task in get_all_tasks(project):
            if (task.get("assignee") or "").strip().lower() == name_norm:
                found.append((project, task))
    if not found:
        return f"Не нашёл задач на «{raw_name}».", None
    lines = [f"📋 Задачи — {raw_name}"] + [task_line(t, show_project=p) for p, t in found]
    buttons = []
    for project, t in found:
        market = get_market_by_name(project)
        buttons.append([InlineKeyboardButton(f"[{project}] {t['task_id']}", callback_data=f"tmg:task:{market['id']}:{t['task_id']}")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


async def on_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status [проект] — открытые задачи проекта. Без аргумента — кнопки
    выбора проекта вместо текстовой подсказки."""
    if not _is_roman(update):
        return
    projects = list_market_names()
    if not projects:
        await update.effective_message.reply_text("Пока нет ни одного проекта.")
        return

    arg_project = " ".join(context.args) if context.args else ""
    if not arg_project:
        await update.effective_message.reply_text(
            "По какому проекту показать задачи?", reply_markup=_project_picker_keyboard(projects)
        )
        return
    if arg_project not in projects:
        await update.effective_message.reply_text(f"Неизвестный проект «{arg_project}». Доступные: " + ", ".join(projects))
        return

    text, keyboard = _build_project_tasks_view(arg_project)
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def on_employee_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/employee [имя] — задачи сотрудника по всем проектам. Без аргумента —
    кнопки выбора среди тех, на кого сейчас есть хоть одна задача."""
    if not _is_roman(update):
        return

    raw_name = " ".join(context.args) if context.args else ""
    if not raw_name:
        names = _distinct_assignees()
        if not names:
            await update.effective_message.reply_text("Пока ни одна задача никому не назначена.")
            return
        owner_id = str(update.effective_user.id)
        _employee_options[owner_id] = names
        buttons = [[InlineKeyboardButton(n, callback_data=f"tmg:openemp:{i}")] for i, n in enumerate(names)]
        await update.effective_message.reply_text("Чьи задачи показать?", reply_markup=InlineKeyboardMarkup(buttons))
        return

    text, keyboard = _build_employee_tasks_view(raw_name)
    await update.effective_message.reply_text(text, reply_markup=keyboard)


def _task_card_text(project: str, task: dict) -> str:
    deadline = fmt_date(task.get("deadline_current"))
    help_mark = " 🆘" if task.get("needs_help") == "да" else ""
    return (
        f"📋 {task['task_id']} — {project}\n"
        f"Текст: {task['task_text']}\n"
        f"Статус: {task.get('status')}{help_mark}\n"
        f"Срок: {deadline}\n"
        f"Исполнитель: {task.get('assignee') or '—'}"
    )


def _task_card_keyboard(market_id: int, task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📅 Срок", callback_data=f"tmg:deadline:{market_id}:{task_id}"),
                InlineKeyboardButton("🤝 Делегировать", callback_data=f"tmg:delegate:{market_id}:{task_id}"),
            ],
            [
                InlineKeyboardButton("📨 Запросить статус", callback_data=f"tmg:askstatus:{market_id}:{task_id}"),
                InlineKeyboardButton("🔀 Проект", callback_data=f"tmg:moveproj:{market_id}:{task_id}"),
            ],
            [InlineKeyboardButton("↩️ К списку", callback_data=f"tmg:openproj:{market_id}")],
        ]
    )


def _employee_label(employee: dict) -> str:
    return employee["real_name"] or employee["full_name"] or (f"@{employee['username']}" if employee["username"] else str(employee["user_id"]))


def _delegate_pick_keyboard(market_id: int, task_id: str) -> InlineKeyboardMarkup | None:
    employees = get_onboarded_employees()
    if not employees:
        return None
    buttons = [
        [InlineKeyboardButton(_employee_label(e), callback_data=f"tmg:delegatepick:{market_id}:{task_id}:{e['user_id']}")]
        for e in employees
    ]
    buttons.append([InlineKeyboardButton("↩️ Отмена", callback_data=f"tmg:task:{market_id}:{task_id}")])
    return InlineKeyboardMarkup(buttons)


async def _show_task_card(query, market_id: int, task_id: str) -> None:
    market = get_market(market_id)
    if not market:
        await query.edit_message_text("Проект не найден.")
        return
    task = get_task(market["name"], task_id)
    if not task:
        await query.edit_message_text("Задача не найдена — возможно, уже перенесена в другой проект.")
        return
    await query.edit_message_text(_task_card_text(market["name"], task), reply_markup=_task_card_keyboard(market_id, task_id))


async def _apply_delegation(bot, reply_target, market_id: int, task_id: str, target_id: int, target_name: str, comment: str) -> None:
    market = get_market(market_id)
    project = market["name"]
    update_task(project, task_id, assignee=target_name, assignee_telegram_id=str(target_id))
    append_comment(project, task_id, "Роман", comment, related_status=get_task(project, task_id).get("status", ""))
    task = get_task(project, task_id)
    try:
        await bot.send_message(
            chat_id=target_id,
            text=f"📌 Тебе делегирована задача «{task['task_text']}» (проект «{project}»).\nКомментарий: {comment}",
        )
    except Exception:
        pass
    await reply_target(
        f"✅ Делегировано: {target_name}.\n\n{_task_card_text(project, task)}", reply_markup=_task_card_keyboard(market_id, task_id)
    )


async def on_task_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if str(query.from_user.id) != str(ROMAN_TELEGRAM_ID):
        await query.answer()
        return

    parts = query.data.split(":")
    action = parts[1]

    if action == "openproj":
        market = get_market(int(parts[2]))
        await query.answer()
        if not market:
            await query.edit_message_text("Проект не найден.")
            return
        text, keyboard = _build_project_tasks_view(market["name"])
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if action == "openemp":
        idx = int(parts[2])
        names = _employee_options.get(str(query.from_user.id)) or []
        await query.answer()
        if idx >= len(names):
            await query.edit_message_text("Список устарел, повторите /employee.")
            return
        text, keyboard = _build_employee_tasks_view(names[idx])
        await query.edit_message_text(text, reply_markup=keyboard)
        return

    if action == "task":
        market_id, task_id = int(parts[2]), parts[3]
        await query.answer()
        await _show_task_card(query, market_id, task_id)
        return

    if action == "deadline":
        market_id, task_id = int(parts[2]), parts[3]
        owner_id = str(query.from_user.id)
        _awaiting_edit[owner_id] = {"field": "deadline", "market_id": market_id, "task_id": task_id}
        await query.answer()
        await query.edit_message_text(f"Новый срок для {task_id} (например, 15.09.2026 или «через неделю»):")
        return

    if action == "delegate":
        market_id, task_id = int(parts[2]), parts[3]
        await query.answer()
        keyboard = _delegate_pick_keyboard(market_id, task_id)
        if not keyboard:
            await query.message.reply_text("Пока нет ни одного онбордившегося сотрудника, чтобы делегировать.")
            return
        await query.edit_message_text(f"Кому делегировать {task_id}?", reply_markup=keyboard)
        return

    if action == "delegatepick":
        market_id, task_id, target_id = int(parts[2]), parts[3], int(parts[4])
        owner_id = str(query.from_user.id)
        target_name = get_display_name(target_id)
        _awaiting_edit[owner_id] = {
            "field": "delegate_comment",
            "market_id": market_id,
            "task_id": task_id,
            "target_id": target_id,
            "target_name": target_name,
        }
        await query.answer()
        await query.edit_message_text(f"Комментарий для {target_name} — почему делегируете {task_id}?")
        return

    if action == "askstatus":
        market_id, task_id = int(parts[2]), parts[3]
        market = get_market(market_id)
        task = get_task(market["name"], task_id) if market else None
        if not task:
            await query.answer("Задача не найдена", show_alert=True)
            return
        await query.answer("Запрашиваю…")
        result = await ask_task_status_now(context.bot, market["name"], task)
        messages = {
            "asked": "📨 Запросил статус у исполнителя.",
            "not_onboarded": "⚠️ Исполнитель ещё не открыл диалог с ботом — спросите вручную.",
            "no_telegram_id": "⚠️ Исполнитель не идентифицирован — спросить некого.",
        }
        await query.message.reply_text(messages.get(result, "Не получилось."))
        return

    if action == "moveproj":
        market_id, task_id = int(parts[2]), parts[3]
        await query.answer()
        buttons = [
            [InlineKeyboardButton(m["name"], callback_data=f"tmg:setproj:{market_id}:{task_id}:{m['id']}")]
            for m in list_markets()
            if m["id"] != market_id
        ]
        if not buttons:
            await query.message.reply_text("Больше нет других проектов, чтобы перенести задачу.")
            return
        await query.edit_message_text(f"В какой проект перенести {task_id}?", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if action == "setproj":
        market_id, task_id, new_market_id = int(parts[2]), parts[3], int(parts[4])
        market = get_market(market_id)
        new_market = get_market(new_market_id)
        if not market or not new_market:
            await query.answer("Проект не найден", show_alert=True)
            return
        await query.answer("Переношу…")
        new_task_id = move_task(market["name"], task_id, new_market["name"])
        await query.edit_message_text(f"✅ Задача перенесена: {task_id} → «{new_market['name']}» / {new_task_id}.")
        return

    await query.answer()


async def on_task_manage_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Забирает текстовый ввод для правки срока/исполнителя задачи из
    карточки управления. Возвращает True, если сообщение обработано — по
    конвенции остальных claim-хендлеров в on_private_text."""
    owner_id = str(update.effective_user.id)
    state = _awaiting_edit.get(owner_id)
    if not state:
        return False
    del _awaiting_edit[owner_id]

    market = get_market(state["market_id"])
    task_id = state["task_id"]
    if not market:
        await update.effective_message.reply_text("Проект не найден.")
        return True
    project = market["name"]
    task = get_task(project, task_id)
    if not task:
        await update.effective_message.reply_text("Задача не найдена.")
        return True

    text = update.effective_message.text.strip() if update.effective_message.text else ""

    if state["field"] == "deadline":
        iso = parse_date(text)
        if not iso:
            await update.effective_message.reply_text(f"Не понял дату «{text}». Попробуйте ещё раз, например ДД.ММ.ГГГГ.")
            _awaiting_edit[owner_id] = state
            return True

        old_deadline = task.get("deadline_current") or ""
        update_task(project, task_id, deadline_current=iso)
        if old_deadline != iso:
            append_log_entry(
                project, task_id, "перенос_срока", old_value=old_deadline, new_value=iso, reason_comment="изменено владельцем вручную"
            )
        telegram_id_str = task.get("assignee_telegram_id")
        if telegram_id_str:
            try:
                await context.bot.send_message(
                    chat_id=int(telegram_id_str),
                    text=f"📅 Срок задачи «{task['task_text']}» изменён на {fmt_date(iso)}.",
                )
            except Exception:
                pass
        updated = get_task(project, task_id)
        await update.effective_message.reply_text(
            _task_card_text(project, updated), reply_markup=_task_card_keyboard(state["market_id"], task_id)
        )
        return True

    if state["field"] == "delegate_comment":
        if not text:
            await update.effective_message.reply_text("Комментарий не может быть пустым, напишите ещё раз:")
            _awaiting_edit[owner_id] = state
            return True

        await _apply_delegation(
            context.bot, update.effective_message.reply_text, state["market_id"], task_id, state["target_id"], state["target_name"], text
        )
        return True

    return True
