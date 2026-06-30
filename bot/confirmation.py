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
from config.settings import ROMAN_TELEGRAM_ID
from config.timeutil import fmt_date
from sheets.comments import append_comment
from sheets.tasks import create_task

# confirmation_id -> {"task": dict, "project": str, "source": str, "source_chat": str, "source_link": str}
_pending: dict[str, dict] = {}
# telegram_user_id -> confirmation_id, пока ждём от Романа исправленный текст
_awaiting_edit: dict[int, str] = {}


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
    elif assignee != "—" and not task.get("project_unclear"):
        project = entry.get("project") or task.get("project")
        buffer_hints = entry.get("buffer_id_hints", {})
        assignee_line += _assignee_onboarding_info(assignee, project, buffer_hints)
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
        _awaiting_edit[query.from_user.id] = confirmation_id
        await query.edit_message_text(
            _build_card_text(entry) + "\n\n✏️ Пришлите исправленный текст задачи следующим сообщением."
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
    await query.message.reply_text(f"✅ Задача {task_id} добавлена в таблицу «{project}».")

    if telegram_id:
        await _notify_assignee(query.get_bot(), telegram_id, project, task)


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


async def on_edit_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Если Роман сейчас правит карточку — подхватывает следующее текстовое
    сообщение как исправленный текст задачи и пересылает карточку заново.
    Возвращает True, если сообщение обработано как правка (и дальше его
    не нужно передавать в обычную логику личных сообщений)."""
    user_id = update.effective_user.id
    confirmation_id = _awaiting_edit.get(user_id)
    if confirmation_id is None:
        return False

    del _awaiting_edit[user_id]
    entry = _pending.get(confirmation_id)
    if entry is None:
        return True

    entry["task"]["task_text"] = update.effective_message.text
    await update.effective_message.reply_text(
        _build_card_text(entry),
        reply_markup=_build_keyboard(confirmation_id),
    )
    return True
