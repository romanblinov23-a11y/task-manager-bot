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


def _load_runtime() -> None:
    global _runtime_map
    if _RUNTIME_STORE_PATH.exists():
        data = json.loads(_RUNTIME_STORE_PATH.read_text())
        _runtime_map = {int(chat_id): project for chat_id, project in data.items()}


def _save_runtime() -> None:
    _RUNTIME_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RUNTIME_STORE_PATH.write_text(
        json.dumps({str(chat_id): project for chat_id, project in _runtime_map.items()}, ensure_ascii=False, indent=2)
    )


_load_runtime()


def get_project_for_chat(chat_id: int) -> str | None:
    return _runtime_map.get(chat_id) or _ENV_MAP.get(chat_id)


def get_chats_for_project(project: str) -> list[int]:
    chats = [chat_id for chat_id, p in _ENV_MAP.items() if p == project]
    chats += [chat_id for chat_id, p in _runtime_map.items() if p == project and chat_id not in chats]
    return chats


def register_chat(chat_id: int, project: str) -> None:
    """Привязывает чат к проекту немедленно, без перезапуска бота (команда /register_project)."""
    _runtime_map[chat_id] = project
    _save_runtime()


def get_all_bindings() -> list[tuple[int, str, str]]:
    """Все привязки chat_id -> project, с указанием источника, для /onboarded."""
    bindings = [(chat_id, project, "env") for chat_id, project in _ENV_MAP.items()]
    bindings += [
        (chat_id, project, "runtime") for chat_id, project in _runtime_map.items() if chat_id not in _ENV_MAP
    ]
    return bindings
