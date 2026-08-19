import sqlite3
from pathlib import Path

from config.settings import MONITORING_DB_PATH

_DB_PATH = Path(MONITORING_DB_PATH)

# Рынок и проект — одна сущность (см. monitoring.markets.list_market_names).
# Это стартовый список для первого запуска на пустой базе; дальше новые
# проекты/рынки заводятся владельцем через /add_project, а не правкой кода.
_BOOTSTRAP_MARKETS = ["Парк Горького", "Окко", "Аврора"]

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
    blocks TEXT NOT NULL DEFAULT 'tasks,monitoring',
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
    format TEXT NOT NULL,
    is_own INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'closed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at TEXT,
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
    """Создаёт таблицы модуля мониторинга (если их ещё нет) и на пустой базе
    сидирует стартовые рынки/проекты (_BOOTSTRAP_MARKETS) — один рынок на
    точку Surf. На непустой базе ничего не досеивает: новые рынки заводятся
    только через /add_project."""
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        _ensure_column(conn, "manager", "status", "status TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(conn, "manager", "blocks", "blocks TEXT NOT NULL DEFAULT 'tasks,monitoring'")
        _ensure_column(conn, "competitor", "closed_at", "closed_at TEXT")
        for project in _BOOTSTRAP_MARKETS:
            conn.execute(
                "INSERT OR IGNORE INTO market (name, city, our_point_name) VALUES (?, '', ?)",
                (project, project),
            )
        conn.commit()
    finally:
        conn.close()


def reset_market_players(market_id: int) -> None:
    """Стирает данные по игрокам ОДНОГО рынка (конкуренты, их факторы,
    снятия, наблюдения) — менеджеры, их привязка к рынку и расписание
    мониторинга не трогаются, это отдельные сущности (сотрудники, а не
    игроки рынка). Вызывается через /reset_monitoring после выбора рынка
    владельцем."""
    conn = get_connection()
    try:
        competitor_ids = [row["id"] for row in conn.execute("SELECT id FROM competitor WHERE market_id = ?", (market_id,))]
        if competitor_ids:
            placeholders = ",".join("?" * len(competitor_ids))
            conn.execute(f"DELETE FROM competitor_factors WHERE competitor_id IN ({placeholders})", competitor_ids)
            conn.execute(f"DELETE FROM daily_avg_reading WHERE competitor_id IN ({placeholders})", competitor_ids)
        conn.execute("DELETE FROM observation WHERE market_id = ?", (market_id,))
        conn.execute("DELETE FROM competitor WHERE market_id = ?", (market_id,))
        conn.commit()
    finally:
        conn.close()
