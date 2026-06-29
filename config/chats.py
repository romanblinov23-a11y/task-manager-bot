import json
import os

# Привязка group chat_id -> проект (PROJECT_SPEC.md, раздел 1).
# Один чат всегда относится ровно к одному проекту, смешанных чатов нет.
# Заполняется либо через переменную окружения CHAT_PROJECT_MAP (JSON-объект
# вида {"-1001234567890": "Окко"}), либо напрямую в словаре ниже.
_raw = os.getenv("CHAT_PROJECT_MAP", "{}")
try:
    CHAT_PROJECT_MAP: dict[int, str] = {
        int(chat_id): project for chat_id, project in json.loads(_raw).items()
    }
except (json.JSONDecodeError, ValueError):
    CHAT_PROJECT_MAP = {}


def get_project_for_chat(chat_id: int) -> str | None:
    return CHAT_PROJECT_MAP.get(chat_id)
