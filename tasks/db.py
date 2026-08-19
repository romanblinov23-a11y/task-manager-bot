import sqlite3
from pathlib import Path

from config.settings import TASKS_DB_PATH

_DB_PATH = Path(TASKS_DB_PATH)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    project TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    source_chat TEXT NOT NULL DEFAULT '',
    source_link TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    task_text TEXT NOT NULL DEFAULT '',
    assignee TEXT NOT NULL DEFAULT '',
    assignee_telegram_id TEXT NOT NULL DEFAULT '',
    deadline_original TEXT NOT NULL DEFAULT '',
    deadline_current TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'новая',
    last_comment TEXT NOT NULL DEFAULT '',
    needs_help TEXT NOT NULL DEFAULT 'нет',
    last_status_check TEXT NOT NULL DEFAULT '',
    closed_at TEXT NOT NULL DEFAULT '',
    UNIQUE (project, task_id)
);

CREATE TABLE IF NOT EXISTS task_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    project TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT '',
    reason_comment TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_comment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    project TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    author TEXT NOT NULL,
    comment_text TEXT NOT NULL DEFAULT '',
    related_status TEXT NOT NULL DEFAULT ''
);
"""


def get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    """Создаёт таблицы трекера задач (если их ещё нет). Отдельная база от
    monitoring.db — задачи и мониторинг конкурентов это две разные базы,
    общие только пользователи (monitoring.manager, по telegram_user_id)."""
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
