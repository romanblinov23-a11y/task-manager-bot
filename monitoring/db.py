import sqlite3
from pathlib import Path

from config.projects import PROJECTS
from config.settings import MONITORING_DB_PATH

_DB_PATH = Path(MONITORING_DB_PATH)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL DEFAULT '',
    our_point_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manager (
    telegram_user_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('manager', 'owner')),
    position TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'removed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS manager_market (
    manager_telegram_user_id INTEGER NOT NULL REFERENCES manager(telegram_user_id),
    market_id INTEGER NOT NULL REFERENCES market(id),
    PRIMARY KEY (manager_telegram_user_id, market_id)
);

CREATE TABLE IF NOT EXISTS competitor (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL REFERENCES market(id),
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    address TEXT NOT NULL DEFAULT '',
    format TEXT NOT NULL CHECK (format IN ('навынос', 'посадка', 'полный')),
    is_own INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (market_id, code)
);

CREATE TABLE IF NOT EXISTS competitor_factors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_id INTEGER NOT NULL REFERENCES competitor(id),
    recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
    product TEXT NOT NULL DEFAULT '',
    atmosphere TEXT NOT NULL DEFAULT '',
    service TEXT NOT NULL DEFAULT '',
    brand_strength TEXT NOT NULL DEFAULT '',
    labor_market TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS daily_avg_reading (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_id INTEGER NOT NULL REFERENCES competitor(id),
    reading_at TEXT NOT NULL,
    avg_checks_per_day REAL NOT NULL,
    created_by INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS observation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    competitor_id INTEGER NOT NULL REFERENCES competitor(id),
    market_id INTEGER NOT NULL REFERENCES market(id),
    observed_at TEXT NOT NULL DEFAULT (datetime('now')),
    category TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    created_by INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL UNIQUE REFERENCES market(id),
    weekdays TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);
"""


def get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Простая идемпотентная миграция: добавляет колонку, если её ещё нет.
    В проекте нет Alembic — миграции делаются такими точечными ALTER'ами."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_schema() -> None:
    """Создаёт таблицы модуля мониторинга (если их ещё нет) и сидирует
    рынки из существующих проектов (config.projects.PROJECTS) — один рынок
    на точку Surf, привязанную к проекту в task-manager'е."""
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        _ensure_column(conn, "manager", "status", "status TEXT NOT NULL DEFAULT 'pending'")
        for project in PROJECTS:
            conn.execute(
                "INSERT OR IGNORE INTO market (name, city, our_point_name) VALUES (?, '', ?)",
                (project, project),
            )
        conn.commit()
    finally:
        conn.close()


def reset_all() -> None:
    """Необратимо стирает все данные модуля мониторинга (менеджеров,
    конкурентов, снятия, наблюдения, расписания на всех рынках) и заново
    сидирует market из PROJECTS — чистый старт. Только для владельца,
    вызывается через /reset_monitoring в bot/manager_admin.py."""
    conn = get_connection()
    try:
        for table in (
            "observation",
            "daily_avg_reading",
            "competitor_factors",
            "competitor",
            "manager_market",
            "manager",
            "monitoring_schedule",
            "market",
        ):
            conn.execute(f"DELETE FROM {table}")
        for project in PROJECTS:
            conn.execute(
                "INSERT INTO market (name, city, our_point_name) VALUES (?, '', ?)",
                (project, project),
            )
        conn.commit()
    finally:
        conn.close()
