import json

from config.timeutil import now as tz_now
from monitoring.db import get_connection


def _now() -> str:
    return tz_now().strftime("%Y-%m-%d %H:%M:%S")


def create_or_get_draft(market_id: int, report_date: str, reporter_telegram_user_id: int) -> dict:
    """Создаёт черновик пересменки на дату (или возвращает уже существующий,
    если сбор начинали и прервали) — UNIQUE(market_id, report_date) не даёт
    завести две пересменки на одну дату."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO handover_report (market_id, report_date, reporter_telegram_user_id, status, created_at)
            VALUES (?, ?, ?, 'collecting', ?)
            ON CONFLICT (market_id, report_date) DO UPDATE SET reporter_telegram_user_id = excluded.reporter_telegram_user_id
            """,
            (market_id, report_date, reporter_telegram_user_id, _now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM handover_report WHERE market_id = ? AND report_date = ?", (market_id, report_date)
        ).fetchone()
        report = dict(row)
        report["data"] = json.loads(report["data"])
        return report
    finally:
        conn.close()


def save_report_data(report_id: int, data: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE handover_report SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), _now(), report_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_report_status(report_id: int, status: str) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE handover_report SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), report_id))
        conn.commit()
    finally:
        conn.close()


def get_report(report_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM handover_report WHERE id = ?", (report_id,)).fetchone()
        if not row:
            return None
        report = dict(row)
        report["data"] = json.loads(report["data"])
        return report
    finally:
        conn.close()


def get_report_by_date(market_id: int, report_date: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM handover_report WHERE market_id = ? AND report_date = ?", (market_id, report_date)
        ).fetchone()
        if not row:
            return None
        report = dict(row)
        report["data"] = json.loads(report["data"])
        return report
    finally:
        conn.close()


def delete_report(market_id: int, report_date: str) -> bool:
    """Удаляет пересменку целиком — управляющему нужно, если сбор начали по
    ошибке и надо начать заново тем же днём. Возвращает False, если
    пересменки на эту дату и не было."""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM handover_report WHERE market_id = ? AND report_date = ?", (market_id, report_date))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
