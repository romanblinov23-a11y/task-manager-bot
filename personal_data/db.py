import sqlite3
from pathlib import Path

from config.settings import PERSONAL_DATA_DB_PATH

_DB_PATH = Path(PERSONAL_DATA_DB_PATH)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consent (
    telegram_user_id INTEGER PRIMARY KEY,
    market_id INTEGER NOT NULL,
    consent_text TEXT NOT NULL,
    agreed_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS trainee_profile (
    telegram_user_id INTEGER PRIMARY KEY,
    phone TEXT NOT NULL DEFAULT '',
    passport_series_number TEXT NOT NULL DEFAULT '',
    passport_issued_by TEXT NOT NULL DEFAULT '',
    passport_issued_date TEXT NOT NULL DEFAULT '',
    snils TEXT NOT NULL DEFAULT '',
    bank_account TEXT NOT NULL DEFAULT '',
    bank_bic TEXT NOT NULL DEFAULT '',
    bank_name TEXT NOT NULL DEFAULT '',
    updated_at TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    """Намеренно отдельная база от monitoring.db/tasks.db — свой файл, свой
    путь через PERSONAL_DATA_DB_PATH — чтобы персональные данные стажёров
    (согласие, паспорт/СНИЛС/банковские реквизиты) можно было позже
    физически перенести на сервер в РФ (152-ФЗ, ст.18 ч.5) одной заменой
    пути/DSN, не трогая остальной код бота."""
    conn = get_connection()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
