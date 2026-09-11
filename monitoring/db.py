import sqlite3
from pathlib import Path

from config.settings import MONITORING_DB_PATH

_DB_PATH = Path(MONITORING_DB_PATH)

# Рынок и проект — одна сущность (см. monitoring.markets.list_market_names).
# Это стартовый список для первого запуска на пустой базе; дальше новые
# проекты/рынки заводятся владельцем через /add_project, а не правкой кода.
_BOOTSTRAP_MARKETS = ["Парк Горького", "Окко", "Yandex"]

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

CREATE TABLE IF NOT EXISTS shift_schedule (
    market_id INTEGER NOT NULL REFERENCES market(id),
    shift_date TEXT NOT NULL,
    manager_telegram_user_id INTEGER NOT NULL,
    PRIMARY KEY (market_id, shift_date)
);

CREATE TABLE IF NOT EXISTS shift_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL REFERENCES market(id),
    report_date TEXT NOT NULL,
    reporter_telegram_user_id INTEGER,
    status TEXT NOT NULL DEFAULT 'collecting',
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT,
    UNIQUE (market_id, report_date)
);

CREATE TABLE IF NOT EXISTS report_chat (
    market_id INTEGER NOT NULL REFERENCES market(id),
    role TEXT NOT NULL CHECK (role IN ('finance', 'team')),
    chat_id INTEGER NOT NULL,
    mention TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (market_id, role)
);

-- Ручной план по выручке/чекам — только для рынков БЕЗ привязки к Surf
-- Coffee (market.surf_spot_key пуст, см. /add_project). Для подключённых
-- рынков план забирается напрямую из Surf Coffee (см. revenue/daily_plan.py).
CREATE TABLE IF NOT EXISTS monthly_plan (
    market_id INTEGER NOT NULL REFERENCES market(id),
    plan_date TEXT NOT NULL,
    revenue_plan REAL NOT NULL,
    checks_plan INTEGER NOT NULL,
    PRIMARY KEY (market_id, plan_date)
);

-- Норма списаний по каждой из трёх статей — % от выручки (не фикс. сумма
-- в рублях, т.к. списания естественно растут/падают вместе с потоком),
-- одна на рынок, задаётся владельцем через /set_writeoff_plan. Плановая
-- сумма в рублях считается на лету от выручки — плановой (утреннее
-- сообщение команде) или фактической (вечерний отчёт), см. bot/shift_reports.py.
CREATE TABLE IF NOT EXISTS writeoff_plan (
    market_id INTEGER PRIMARY KEY REFERENCES market(id),
    expiry_pct REAL NOT NULL,
    compliment_pct REAL NOT NULL,
    staff_meals_pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS meeting_schedule (
    market_id INTEGER NOT NULL REFERENCES market(id),
    meeting_type TEXT NOT NULL CHECK (meeting_type IN ('team', 'managers')),
    weekday INTEGER NOT NULL,
    time TEXT NOT NULL,
    PRIMARY KEY (market_id, meeting_type)
);

CREATE TABLE IF NOT EXISTS meeting_instance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL REFERENCES market(id),
    meeting_type TEXT NOT NULL CHECK (meeting_type IN ('team', 'managers')),
    meeting_date TEXT NOT NULL,
    meeting_time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_confirmation',
    agenda TEXT NOT NULL DEFAULT '',
    invite_roman INTEGER NOT NULL DEFAULT 0,
    rescheduled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT,
    UNIQUE (market_id, meeting_type, meeting_date)
);

CREATE TABLE IF NOT EXISTS monitor_chat (
    chat_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    triggers TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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
        # Норма списаний перешла с фиксированной суммы в рублях на % от
        # выручки ещё до того, как владелец успел ввести реальные значения —
        # на базе со старой (рублёвой) версией таблицы просто пересоздаём её
        # под новую схему, так гораздо надёжнее, чем пытаться "перевести"
        # рубли в проценты без знания выручки на тот момент.
        old_cols = {row["name"] for row in conn.execute("PRAGMA table_info(writeoff_plan)")}
        if "expiry_plan" in old_cols:
            conn.execute("DROP TABLE writeoff_plan")
        conn.executescript(_SCHEMA)
        _ensure_column(conn, "manager", "status", "status TEXT NOT NULL DEFAULT 'pending'")
        _ensure_column(conn, "manager", "blocks", "blocks TEXT NOT NULL DEFAULT 'tasks,monitoring'")
        _ensure_column(conn, "manager", "blocks_ack", "blocks_ack TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "manager", "onboarding_stage", "onboarding_stage INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "competitor", "closed_at", "closed_at TEXT")
        # Реквизиты юрлица/ИП, которое юридически владеет точкой этого рынка —
        # именно оно, а не Роман и не бот, оператор персональных данных
        # стажёров этой точки (см. /set_operator, personal_data/consent.py).
        # Разные рынки могут принадлежать разным юрлицам по разным договорам.
        _ensure_column(conn, "market", "operator_name", "operator_name TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "market", "operator_inn", "operator_inn TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "market", "operator_ogrn", "operator_ogrn TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "market", "operator_address", "operator_address TEXT NOT NULL DEFAULT ''")
        # Готовый текст согласия от юристов оператора — если задан, используется
        # вместо текста, собранного автоматически из operator_* полей выше.
        _ensure_column(conn, "market", "custom_consent_text", "custom_consent_text TEXT NOT NULL DEFAULT ''")
        # Своё время сбора вечернего отчёта на точке (пусто — берём глобальный
        # дефолт SHIFT_REPORT_START_TIME) и решение Романа, уходит ли отчёт
        # этой точки в чат финпартнёров вообще (см. /set_evening_report,
        # bot.market_settings). Не у всех точек финпартнёры есть.
        _ensure_column(conn, "market", "shift_report_time", "shift_report_time TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "market", "send_to_finance", "send_to_finance INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "report_chat", "mention", "mention TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "report_chat", "message_thread_id", "message_thread_id INTEGER")
        # Разовое переименование: "Аврора" и "Yandex" — одна и та же точка,
        # называем её везде "Yandex" (совпадает с тем, как её называет
        # модуль revenue/, перенесённый из бота "Аналитик Иван"). Идемпотентно —
        # после первого запуска строк с "Аврора" уже не останется.
        conn.execute("UPDATE market SET name = 'Yandex' WHERE name = 'Аврора'")
        # Ключ точки в системе учёта Surf Coffee (см. revenue.surfcoffee_client.SPOTS)
        # — пусто, если рынок НЕ подключён (план вносится вручную, см.
        # /set_monthly_plan). Задаётся при /add_project; для трёх точек,
        # заведённых до появления этого вопроса, проставляем один раз явно —
        # идемпотентно, дальше эти рынки уже не будут пустыми.
        _ensure_column(conn, "market", "surf_spot_key", "surf_spot_key TEXT NOT NULL DEFAULT ''")
        for _name, _spot_key in (("Yandex", "yandex"), ("Окко", "okko"), ("Парк Горького", "park_gorkogo")):
            conn.execute(
                "UPDATE market SET surf_spot_key = ? WHERE name = ? AND surf_spot_key = ''",
                (_spot_key, _name),
            )
        # Разовая миграция данных: блок "Отчёты по смене" появился позже
        # tasks/monitoring, у уже активных сотрудников его нет в списке —
        # добавляем, чтобы функция сразу заработала без ручной правки
        # владельцем через /employees. Новые сотрудники получают его
        # автоматически через DEFAULT_BLOCKS (см. register_manager).
        for row in conn.execute("SELECT telegram_user_id, blocks FROM manager WHERE status = 'active'").fetchall():
            blocks = [b for b in (row["blocks"] or "").split(",") if b]
            if "reports" not in blocks:
                blocks.append("reports")
                conn.execute(
                    "UPDATE manager SET blocks = ? WHERE telegram_user_id = ?", (",".join(blocks), row["telegram_user_id"])
                )
        # Разовая чистка: remove_manager раньше делал soft-delete (status =
        # 'removed'), теперь удаляет запись полностью — доступ возвращается
        # только через новый онбординг. На базах, где остались старые
        # soft-deleted записи, подчищаем их здесь; на новых базах это no-op.
        conn.execute(
            "DELETE FROM manager_market WHERE manager_telegram_user_id IN "
            "(SELECT telegram_user_id FROM manager WHERE status = 'removed')"
        )
        conn.execute("DELETE FROM manager WHERE status = 'removed'")
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
