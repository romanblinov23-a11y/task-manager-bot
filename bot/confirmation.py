import uuid

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.onboarding import find_homonyms, find_telegram_id_for_assignee
from config.settings import ROMAN_TELEGRAM_ID
from sheets.comments import append_comment
from sheets.tasks import create_task

# confirmation_id -> {"task": dict, "project": str, "source": str, "source_chat": str, "source_link": str}
_pending: dict[str, dict] = {}
# telegram_user_id -> confirmation_id, пока ждём от Романа исправленный текст
_awaiting_edit: dict[int, str] = {}


def _build_card_text(entry: dict) -> str:
    task = entry["task"]
    lines = ["📋 Новая задача", f"Текст: {task.get('task_text', '')}"]

    assignee_line = f"Исполнитель: {task.get('assignee') or '—'}"
    if task.get("assignee_unclear"):
        assignee_line += " ⚠️ не уверен в исполнителе"
    lines.append(assignee_line)

    lines.append(f"Категория: {task.get('category', '—')}")
    lines.append(f"Срок: {task.get('deadline') or '—'}")

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
) -> None:
    """Отправляет Роману в личку по карточке на каждую извлечённую задачу
    (раздел 3 PROJECT_SPEC.md) — без батчинга, каждая отдельным сообщением
    с кнопками Да/Править/Отмена."""
    for task in tasks:
        confirmation_id = uuid.uuid4().hex[:8]
        entry = {
            "task": task,
            "project": project,
            "source": source,
            "source_chat": source_chat,
            "source_link": source_link,
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

    if assignee and "employee_resolved" not in entry:
        candidates = find_homonyms(project, assignee)
        if len(candidates) > 1:
            await query.edit_message_text(
                _build_card_text(entry)
                + f"\n\n⚠️ Нашёл несколько сотрудников с именем «{assignee}» в этом проекте. Кто из них?",
                reply_markup=_employee_keyboard(confirmation_id, candidates),
            )
            return

    telegram_id = entry.get("employee_resolved") or (
        find_telegram_id_for_assignee(project, assignee) if assignee else None
    )

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
