import json
from pathlib import Path

from telegram import Update, User
from telegram.ext import ContextTypes

from config.chats import get_project_for_chat
from config.settings import ROMAN_TELEGRAM_ID
from sheets.tasks import get_all_tasks, update_task

_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "known_users.json"

# user_id (str) -> {"username": str|None, "full_name": str, "onboarded": bool}
_known_users: dict[str, dict] = {}
# user_id (str) -> [chat_id, ...] — группы, где этот человек писал сообщения
_seen_in_chats: dict[str, list[int]] = {}
# "{project}|{normalized_assignee}" -> telegram_id — уже разрешённые сопоставления
_resolved: dict[str, int] = {}


def _load() -> None:
    global _known_users, _seen_in_chats, _resolved
    if _STORE_PATH.exists():
        data = json.loads(_STORE_PATH.read_text())
        _known_users = data.get("known_users", {})
        _seen_in_chats = data.get("seen_in_chats", {})
        _resolved = data.get("resolved", {})


def _save() -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps(
            {"known_users": _known_users, "seen_in_chats": _seen_in_chats, "resolved": _resolved},
            ensure_ascii=False,
            indent=2,
        )
    )


_load()


def _normalize(name: str) -> str:
    return name.strip().lower()


def _name_matches(candidate: str, full_name: str, username: str | None) -> bool:
    candidate_norm = _normalize(candidate)
    if not candidate_norm:
        return False
    full_name_norm = _normalize(full_name)
    if candidate_norm == full_name_norm:
        return True
    first_name = full_name_norm.split()[0] if full_name_norm else ""
    if candidate_norm == first_name:
        return True
    if username and candidate_norm == _normalize(username):
        return True
    return False


def _try_match_and_backfill(user_id: int) -> list[tuple[str, str]]:
    """Сопоставляет онбордившегося сотрудника с именами assignee в таблицах
    проектов, где его видели в группе, и проставляет assignee_telegram_id
    на уже существующих задачах (раздел 4, 9.1 PROJECT_SPEC.md)."""
    user = _known_users.get(str(user_id))
    if not user:
        return []

    chats = _seen_in_chats.get(str(user_id), [])
    projects = {get_project_for_chat(chat_id) for chat_id in chats}
    projects.discard(None)

    matched: list[tuple[str, str]] = []
    for project in projects:
        for task in get_all_tasks(project):
            assignee = task.get("assignee", "")
            if not assignee or task.get("assignee_telegram_id"):
                continue
            if _name_matches(assignee, user.get("full_name", ""), user.get("username")):
                update_task(project, task["task_id"], assignee_telegram_id=str(user_id))
                matched.append((project, assignee))
                _resolved[f"{project}|{_normalize(assignee)}"] = user_id
    if matched:
        _save()
    return matched


def find_telegram_id_for_assignee(project: str, assignee_name: str) -> int | None:
    """Используется при создании новой задачи, чтобы сразу проставить
    assignee_telegram_id, если сотрудник уже онбордился ранее."""
    return _resolved.get(f"{project}|{_normalize(assignee_name)}")


def record_group_member(chat_id: int, user: User | None) -> None:
    """Пассивно запоминает, кто писал в группе — нужно для последующего
    сопоставления Telegram-аккаунта с именем в таблице при онбординге."""
    if user is None or user.is_bot:
        return

    user_id = str(user.id)
    is_new_chat = chat_id not in _seen_in_chats.get(user_id, [])

    _known_users.setdefault(user_id, {})
    _known_users[user_id]["username"] = user.username
    _known_users[user_id]["full_name"] = user.full_name

    chats = _seen_in_chats.setdefault(user_id, [])
    if chat_id not in chats:
        chats.append(chat_id)
    _save()

    if _known_users[user_id].get("onboarded") and is_new_chat:
        _try_match_and_backfill(user.id)


async def on_employee_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Любое личное сообщение сотрудника боту открывает возможность писать
    ему в личку (раздел 4: "/start или любое сообщение")."""
    user = update.effective_user
    user_id = str(user.id)
    already_onboarded = _known_users.get(user_id, {}).get("onboarded", False)

    _known_users.setdefault(user_id, {})
    _known_users[user_id]["username"] = user.username
    _known_users[user_id]["full_name"] = user.full_name
    _known_users[user_id]["onboarded"] = True
    _save()

    if already_onboarded:
        return

    matched = _try_match_and_backfill(user.id)
    if matched:
        await update.effective_message.reply_text("Готово — теперь буду писать тебе сюда по задачам.")
    else:
        await update.effective_message.reply_text(
            "Привет! Записал тебя — как только появится задача с твоим именем, "
            "смогу писать сюда о статусе."
        )


async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if str(update.effective_user.id) == str(ROMAN_TELEGRAM_ID):
        await update.effective_message.reply_text("Привет! Я готов принимать задачи и протоколы встреч.")
        return
    await on_employee_message(update, context)
