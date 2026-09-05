import json
import os
from pathlib import Path

# Базовая привязка chat_id -> проект из .env (раздел 1 PROJECT_SPEC.md).
_raw = os.getenv("CHAT_PROJECT_MAP", "{}")
try:
    _ENV_MAP: dict[int, str] = {int(chat_id): project for chat_id, project in json.loads(_raw).items()}
except (json.JSONDecodeError, ValueError):
    _ENV_MAP = {}

# Доп. привязки, сделанные через /register_project — не требуют перезапуска бота.
_RUNTIME_STORE_PATH = Path(__file__).resolve().parent.parent / "data" / "chat_project_map.json"
_runtime_map: dict[int, str] = {}

# Привязки конкретной ветки (топика) форума к своему проекту — на случай,
# если один физический чат (chat_id) обслуживает несколько проектов в
# разных топиках. Ключ — "chat_id:thread_id".
_runtime_thread_map: dict[str, str] = {}


def _thread_key(chat_id: int, message_thread_id: int) -> str:
    return f"{chat_id}:{message_thread_id}"


def _load_runtime() -> None:
    global _runtime_map, _runtime_thread_map
    if not _RUNTIME_STORE_PATH.exists():
        return
    data = json.loads(_RUNTIME_STORE_PATH.read_text())
    if "chats" in data or "threads" in data:
        _runtime_map = {int(chat_id): project for chat_id, project in data.get("chats", {}).items()}
        _runtime_thread_map = dict(data.get("threads", {}))
    else:
        # Старый формат файла — плоский {chat_id: project} без веток.
        _runtime_map = {int(chat_id): project for chat_id, project in data.items()}


def _save_runtime() -> None:
    _RUNTIME_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RUNTIME_STORE_PATH.write_text(
        json.dumps(
            {
                "chats": {str(chat_id): project for chat_id, project in _runtime_map.items()},
                "threads": dict(_runtime_thread_map),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


_load_runtime()


def get_project_for_chat(chat_id: int, message_thread_id: int | None = None) -> str | None:
    """Если указана ветка (форум с топиками) и для неё есть отдельная
    привязка — используем её; иначе (или без ветки) — привязка на весь
    чат целиком, как раньше."""
    if message_thread_id is not None:
        thread_project = _runtime_thread_map.get(_thread_key(chat_id, message_thread_id))
        if thread_project:
            return thread_project
    return _runtime_map.get(chat_id) or _ENV_MAP.get(chat_id)


def get_chats_for_project(project: str) -> list[int]:
    chats = [chat_id for chat_id, p in _ENV_MAP.items() if p == project]
    chats += [chat_id for chat_id, p in _runtime_map.items() if p == project and chat_id not in chats]
    return chats


def register_chat(chat_id: int, project: str, message_thread_id: int | None = None) -> None:
    """Привязывает чат к проекту немедленно, без перезапуска бота (команда
    /register_project). Если команду запустили внутри конкретной ветки
    форума — привязывается именно эта ветка, а не весь чат: так один
    форум может обслуживать несколько проектов в разных топиках. Без
    ветки — весь чат целиком, как раньше."""
    if message_thread_id is not None:
        _runtime_thread_map[_thread_key(chat_id, message_thread_id)] = project
    else:
        _runtime_map[chat_id] = project
    _save_runtime()


def unregister_chat(chat_id: int, message_thread_id: int | None = None) -> bool:
    """Отвязывает чат (или конкретную его ветку), привязанный через
    /register_project. Привязки из CHAT_PROJECT_MAP так не снимаются — их
    меняют только через переменную окружения. Возвращает False, если
    рантайм-привязки не было."""
    if message_thread_id is not None:
        key = _thread_key(chat_id, message_thread_id)
        if key not in _runtime_thread_map:
            return False
        del _runtime_thread_map[key]
        _save_runtime()
        return True
    if chat_id not in _runtime_map:
        return False
    del _runtime_map[chat_id]
    _save_runtime()
    return True


def get_all_bindings() -> list[tuple[int, str, str]]:
    """Все привязки chat_id -> project НА ВЕСЬ ЧАТ, с указанием источника,
    для /managers. Привязки конкретных веток форума сюда не входят —
    смотри get_all_thread_bindings."""
    bindings = [(chat_id, project, "env") for chat_id, project in _ENV_MAP.items()]
    bindings += [
        (chat_id, project, "runtime") for chat_id, project in _runtime_map.items() if chat_id not in _ENV_MAP
    ]
    return bindings


def get_all_thread_bindings() -> list[tuple[int, int, str]]:
    """Все привязки конкретной ветки (топика) форума к проекту —
    (chat_id, message_thread_id, project)."""
    bindings = []
    for key, project in _runtime_thread_map.items():
        chat_id_str, thread_id_str = key.split(":", 1)
        bindings.append((int(chat_id_str), int(thread_id_str), project))
    return bindings
