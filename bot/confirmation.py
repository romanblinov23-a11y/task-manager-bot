import logging
import uuid

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.onboarding import (
    find_homonyms,
    find_telegram_id_for_assignee,
    get_display_name,
    get_username,
    is_onboarded,
)
from config.projects import CATEGORIES
from config.settings import ROMAN_TELEGRAM_ID
from config.timeutil import fmt_date, parse_date
from tasks.comments import append_comment
from tasks.tasks import create_task, update_task

# confirmation_id -> {"task": dict, "project": str, "source": str, ...}
_pending: dict[str, dict] = {}
# user_id -> (confirmation_id, field) — ожидаем текстовый ввод конкретного поля
_awaiting_field_edit: dict[int, tuple[str, str]] = {}
# telegram_id исполнителя -> {"project":, "task_id":} — ждём срок для только
# что созданной задачи без дедлайна (см. _ask_assignee_for_deadline)
_awaiting_deadline: dict[int, dict] = {}


def _assignee_onboarding_info(assignee: str, project: str | None, buffer_hints: dict) -> str:
    """Возвращает строку-подсказку об исполнителе для карточки подтверждения:
    кто именно из онбордившихся это, или предупреждение что никто не найден."""
    if not assignee or not project:
        return ""

    # 1. Прямое совпадение по буферу чата (кто физически написал сообщение)
    tid = buffer_hints.get(assignee.strip().lower())
    if tid and is_onboarded(tid):
        name = get_display_name(tid)
        uname = get_username(tid)
        ref = f"@{uname}" if uname else f"ID {tid}"
        return f"\n   → {name} ({ref}) ✅ статусы запрошу сам"
    if tid and not is_onboarded(tid):
        uname = get_username(tid)
        ref = f"@{uname}" if uname else f"ID {tid}"
        return f"\n   → {ref} ⚠️ не онбордился — статус нужно запросить вручную"

    # 2. Разрешение через кэш онбординга
    resolved_tid = find_telegram_id_for_assignee(project, assignee)
    if resolved_tid:
        name = get_display_name(resolved_tid)
        uname = get_username(resolved_tid)
        ref = f"@{uname}" if uname else f"ID {resolved_tid}"
        return f"\n   → {name} ({ref}) ✅ статусы запрошу сам"

    # 3. Поиск по onboarded-участникам проекта
    candidates = find_homonyms(project, assignee)
    if len(candidates) == 1:
        c = candidates[0]
        name = c.get("full_name") or ""
        uname = c.get("username") or ""
        ref = f"@{uname}" if uname else f"ID {c['user_id']}"
        return f"\n   → {name} ({ref}) ✅ статусы запрошу сам"
    if len(candidates) > 1:
        return "\n   → ⚠️ несколько кандидатов — уточню при нажатии «Да»"

    # 4. Никого не нашли среди онбордившихся
    return "\n   → ⚠️ не онбордился — статус нужно будет запросить вручную"


def _build_card_text(entry: dict) -> str:
    task = entry["task"]
    lines = ["📋 Новая задача", f"Текст: {task.get('task_text', '')}"]

    assignee = task.get("assignee") or "—"
    assignee_line = f"Исполнитель: {assignee}"
    if task.get("assignee_unclear"):
        assignee_line += " ⚠️ не уверен в исполнителе"
    elif assignee != "—":
        # Используем entry["project"] — он всегда является итоговым разрешённым проектом,
        # даже если Claude пометил task["project_unclear"]=True (бот разрешил программно).
        resolved_project = entry.get("project") or (
            task.get("project") if not task.get("project_unclear") else None
        )
        if resolved_project:
            buffer_hints = entry.get("buffer_id_hints", {})
            assignee_line += _assignee_onboarding_info(assignee, resolved_project, buffer_hints)
    lines.append(assignee_line)

    lines.append(f"Категория: {task.get('category', '—')}")
    lines.append(f"Срок: {fmt_date(task.get('deadline'))}")

    project = entry.get("project") or task.get("project")
    project_line = f"Проект: {project or '—'}"
    if task.get("project_unclear"):
        project_line += " ⚠️ проект не определён"
    lines.append(project_line)

    if task.get("confidence") == "low":
        lines.append("⚠️ низкая уверенность в этой задаче")

    if task.get("source_excerpt"):
        lines.append(f"Источник: «{task['source_excerpt']}»")

    return "\n".join(lines)


def _build_keyboard(confirmation_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да", callback_data=f"confirm:yes:{confirmation_id}"),
        InlineKeyboardButton("✏️ Править", callback_data=f"confirm:edit:{confirmation_id}"),
        InlineKeyboardButton("❌ Отмена", callback_data=f"confirm:cancel:{confirmation_id}"),
    ]])


def _edit_field_keyboard(confirmation_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Текст задачи", callback_data=f"edit_field:text:{confirmation_id}"),
            InlineKeyboardButton("🏷️ Категория", callback_data=f"edit_field:category:{confirmation_id}"),
        ],
        [
            InlineKeyboardButton("👤 Исполнитель", callback_data=f"edit_field:assignee:{confirmation_id}"),
            InlineKeyboardButton("📅 Срок", callback_data=f"edit_field:deadline:{confirmation_id}"),
        ],
        [InlineKeyboardButton("↩️ Назад к карточке", callback_data=f"confirm:back:{confirmation_id}")],
    ])


def _category_keyboard(confirmation_id: str) -> InlineKeyboardMarkup:
    # Используем числовой индекс вместо полного названия категории в callback_data:
    # названия вроде "Управленческая отчётность" занимают >50 байт кириллицей,
    # и вместе с префиксом и confirmation_id превышают лимит Telegram в 64 байта.
    buttons = []
    for i in range(0, len(CATEGORIES), 2):
        row = [InlineKeyboardButton(CATEGORIES[i], callback_data=f"set_cat:{i}:{confirmation_id}")]
        if i + 1 < len(CATEGORIES):
            row.append(InlineKeyboardButton(CATEGORIES[i + 1], callback_data=f"set_cat:{i + 1}:{confirmation_id}"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton("↩️ Назад", callback_data=f"confirm:edit:{confirmation_id}")])
    return InlineKeyboardMarkup(buttons)


async def send_confirmation_cards(
    bot: Bot,
    tasks: list[dict],
    *,
    project: str,
    source: str,
    source_chat: str = "",
    source_link: str = "",
    buffer_id_hints: dict[str, int] | None = None,
) -> None:
    """Отправляет Роману в личку по карточке на каждую извлечённую задачу
    (раздел 3 PROJECT_SPEC.md) — без батчинга, каждая отдельным сообщением
    с кнопками Да/Править/Отмена.

    buffer_id_hints: нормализованное_имя → telegram_id отправителя из буфера группового чата.
    Если задача пришла из чата, это позволяет идентифицировать исполнителя по двум параметрам:
    имя из экстракции + фактический telegram_id того, кто написал сообщение, — без
    нечёткого сопоставления и без disambiguation-диалога при совпадении имён."""
    for task in tasks:
        confirmation_id = uuid.uuid4().hex[:8]
        entry = {
            "task": task,
            "project": project,
            "source": source,
            "source_chat": source_chat,
            "source_link": source_link,
            "buffer_id_hints": buffer_id_hints or {},
        }
        _pending[confirmation_id] = entry
        await bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text=_build_card_text(entry),
            reply_markup=_build_keyboard(confirmation_id),
        )


async def on_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    _, action, confirmation_id = query.data.split(":", 2)
    entry = _pending.get(confirmation_id)
    if entry is None:
        await query.edit_message_text("Эта карточка уже неактуальна.")
        return

    if action == "yes":
        await _confirm_task(query, confirmation_id, entry)
    elif action == "cancel":
        _pending.pop(confirmation_id, None)
        await query.edit_message_text("❌ Отменено.")
    elif action == "edit":
        await query.edit_message_text(
            _build_card_text(entry) + "\n\n✏️ Что хотите исправить?",
            reply_markup=_edit_field_keyboard(confirmation_id),
        )
    elif action == "back":
        await query.edit_message_text(
            _build_card_text(entry),
            reply_markup=_build_keyboard(confirmation_id),
        )


def _employee_keyboard(confirmation_id: str, candidates: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    c["full_name"] or c["username"] or str(c["user_id"]),
                    callback_data=f"employee:{confirmation_id}:{c['user_id']}",
                )
            ]
            for c in candidates
        ]
    )


async def _confirm_task(query, confirmation_id: str, entry: dict) -> None:
    task = entry["task"]
    project = entry["project"] or task.get("project")
    assignee = task.get("assignee") or ""

    # Приоритет идентификации исполнителя:
    # 1. Ручной выбор Романа (disambiguation-кнопка) — наивысший приоритет
    # 2. Buffer hint: имя совпало + фактический telegram_id из Telegram (2 параметра)
    # 3. _resolved: имя уже было сопоставлено с ID при предыдущем онбординге
    # 4. Disambiguation: несколько кандидатов с одним именем → спросить Романа

    if "employee_resolved" in entry:
        telegram_id = entry["employee_resolved"]
    else:
        buffer_hints = entry.get("buffer_id_hints", {})
        buffer_telegram_id = buffer_hints.get(assignee.strip().lower()) if assignee else None

        if buffer_telegram_id:
            # Имя ИЗ буфера совпало с именем отправителя → используем его ID напрямую
            telegram_id = buffer_telegram_id
        else:
            if assignee:
                candidates = find_homonyms(project, assignee)
                if len(candidates) > 1:
                    await query.edit_message_text(
                        _build_card_text(entry)
                        + f"\n\n⚠️ Нашёл несколько сотрудников с именем «{assignee}» в этом проекте. Кто из них?",
                        reply_markup=_employee_keyboard(confirmation_id, candidates),
                    )
                    return
            telegram_id = find_telegram_id_for_assignee(project, assignee) if assignee else None

    task_id = create_task(
        project,
        source=entry["source"],
        task_text=task["task_text"],
        category=task["category"],
        assignee=assignee,
        assignee_telegram_id=str(telegram_id) if telegram_id else "",
        source_chat=entry.get("source_chat", ""),
        source_link=entry.get("source_link", ""),
        deadline_original=task.get("deadline") or "",
    )
    append_comment(project, task_id, "бот", "Задача зафиксирована и подтверждена Романом.")

    _pending.pop(confirmation_id, None)
    await query.edit_message_text(_build_card_text(entry) + f"\n\n✅ Добавлено как {task_id}.")

    reply_note = f"✅ Задача {task_id} добавлена в таблицу «{project}»."
    if not task.get("deadline") and not telegram_id:
        reply_note += (
            " ⚠️ Без срока и без известного исполнителя — спросить срок будет не у кого, "
            "проставьте вручную, иначе цикл проверки статусов её не подхватит."
        )
    await query.message.reply_text(reply_note)

    if telegram_id:
        if task.get("deadline"):
            await _notify_assignee(query.get_bot(), telegram_id, project, task)
        else:
            await _ask_assignee_for_deadline(query.get_bot(), telegram_id, project, task_id, task["task_text"])


async def _ask_assignee_for_deadline(bot: Bot, telegram_id: int, project: str, task_id: str, task_text: str) -> None:
    """Задача создана без срока — без него цикл проверки статусов
    (bot/status_cycle.py: _is_due) никогда её не подхватит, поэтому вместо
    обычного уведомления сразу спрашиваем срок у исполнителя."""
    _awaiting_deadline[telegram_id] = {"project": project, "task_id": task_id}
    await bot.send_message(
        chat_id=telegram_id,
        text=(
            f"📌 Тебе назначена новая задача:\n«{task_text}»\n\n"
            f"Проект: {project}\n\n"
            "Срок не указан — когда нужно сделать? Ответь датой (например, 15.09.2026 или «через неделю»)."
        ),
    )


async def on_deadline_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Ловит ответ исполнителя на вопрос о сроке для задачи, созданной без
    дедлайна (см. _ask_assignee_for_deadline). Возвращает True, если
    сообщение обработано — по конвенции остальных claim-хендлеров
    в on_private_text."""
    user_id = update.effective_user.id
    state = _awaiting_deadline.get(user_id)
    if not state:
        return False

    text = update.effective_message.text.strip() if update.effective_message.text else ""
    iso = parse_date(text)
    if not iso:
        await update.effective_message.reply_text(
            f"Не понял дату «{text}». Попробуйте в формате ДД.ММ.ГГГГ или «через неделю»."
        )
        return True

    del _awaiting_deadline[user_id]
    project, task_id = state["project"], state["task_id"]
    update_task(project, task_id, deadline_original=iso, deadline_current=iso)
    await update.effective_message.reply_text(f"Записал срок: {fmt_date(iso)}. Буду напоминать, когда подойдёт время.")
    try:
        await context.bot.send_message(
            chat_id=ROMAN_TELEGRAM_ID,
            text=f"📅 {get_display_name(user_id)} указал(а) срок для {task_id} («{project}»): {fmt_date(iso)}.",
        )
    except Exception:
        pass
    return True


async def _notify_assignee(bot: Bot, telegram_id: int, project: str, task: dict) -> None:
    deadline = fmt_date(task.get("deadline"))
    text = (
        f"📌 Тебе назначена новая задача\n\n"
        f"Проект: {project}\n"
        f"Категория: {task.get('category', '—')}\n"
        f"Задача: {task['task_text']}\n"
        f"Срок: {deadline}"
    )
    await bot.send_message(chat_id=telegram_id, text=text)


async def on_employee_disambiguation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Роман выбрал, кому из нескольких одноимённых сотрудников принадлежит
    задача — продолжаем подтверждение с уже известным telegram_id."""
    query = update.callback_query
    await query.answer()

    _, confirmation_id, user_id = query.data.split(":", 2)
    entry = _pending.get(confirmation_id)
    if entry is None:
        await query.edit_message_text("Эта карточка уже неактуальна.")
        return

    entry["employee_resolved"] = int(user_id)
    await _confirm_task(query, confirmation_id, entry)


async def on_edit_field_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Роман выбрал, какое поле задачи хочет исправить."""
    query = update.callback_query
    await query.answer()
    _, field, confirmation_id = query.data.split(":", 2)
    entry = _pending.get(confirmation_id)
    if entry is None:
        await query.edit_message_text("Карточка уже неактуальна.")
        return

    if field == "category":
        await query.edit_message_text(
            _build_card_text(entry) + "\n\nВыберите новую категорию:",
            reply_markup=_category_keyboard(confirmation_id),
        )
    else:
        prompts = {
            "text": "Введите новый текст задачи:",
            "assignee": "Введите имя исполнителя:",
            "deadline": f"Введите новый срок (ДД.ММ.ГГГГ, например {fmt_date(entry['task'].get('deadline'))}):",
        }
        _awaiting_field_edit[query.from_user.id] = (confirmation_id, field)
        await query.edit_message_text(
            _build_card_text(entry) + f"\n\n✏️ {prompts.get(field, 'Введите значение:')}",
        )


async def on_set_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Роман выбрал новую категорию для задачи (callback_data содержит индекс)."""
    query = update.callback_query
    await query.answer()
    _, index_str, confirmation_id = query.data.split(":", 2)
    entry = _pending.get(confirmation_id)
    if entry is None:
        await query.edit_message_text("Карточка уже неактуальна.")
        return

    try:
        category = CATEGORIES[int(index_str)]
    except (IndexError, ValueError):
        await query.answer("Неизвестная категория.")
        return

    entry["task"]["category"] = category
    await query.edit_message_text(
        _build_card_text(entry),
        reply_markup=_build_keyboard(confirmation_id),
    )


async def on_edit_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Перехватывает текстовый ввод от Романа, если он редактирует конкретное
    поле карточки. Возвращает True, если сообщение обработано как правка."""
    user_id = update.effective_user.id
    pending_edit = _awaiting_field_edit.get(user_id)
    if pending_edit is None:
        return False

    del _awaiting_field_edit[user_id]
    confirmation_id, field = pending_edit
    entry = _pending.get(confirmation_id)
    if entry is None:
        await update.effective_message.reply_text("Карточка устарела — создайте задачу заново.")
        return True

    new_value = update.effective_message.text.strip()
    task = entry["task"]

    try:
        if field == "text":
            task["task_text"] = new_value
        elif field == "assignee":
            task["assignee"] = new_value
            task["assignee_unclear"] = False
            entry.pop("employee_resolved", None)
        elif field == "deadline":
            iso = parse_date(new_value)
            if iso:
                task["deadline"] = iso
            else:
                await update.effective_message.reply_text(
                    f"Не понял формат даты «{new_value}». Попробуйте ДД.ММ.ГГГГ, например 07.07.2026."
                )
                _awaiting_field_edit[user_id] = (confirmation_id, field)
                return True

        await update.effective_message.reply_text(
            _build_card_text(entry),
            reply_markup=_build_keyboard(confirmation_id),
        )
    except Exception as exc:
        logging.error("Ошибка в on_edit_reply (field=%s): %s", field, exc, exc_info=True)
        await update.effective_message.reply_text(
            f"Что-то пошло не так при сохранении «{field}»: {exc}\nПопробуйте ещё раз."
        )
    return True
