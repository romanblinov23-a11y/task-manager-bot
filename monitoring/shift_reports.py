import json
from datetime import date, timedelta

from config.timeutil import now as tz_now
from monitoring.db import get_connection


def _now() -> str:
    return tz_now().strftime("%Y-%m-%d %H:%M:%S")


def create_or_get_draft(market_id: int, report_date: str, reporter_telegram_user_id: int) -> dict:
    """Создаёт черновик отчёта на дату (или возвращает уже существующий,
    если сбор начинали и прервали) — UNIQUE(market_id, report_date)
    не даёт завести два отчёта на одну смену."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO shift_report (market_id, report_date, reporter_telegram_user_id, status, created_at)
            VALUES (?, ?, ?, 'collecting', ?)
            ON CONFLICT (market_id, report_date) DO UPDATE SET reporter_telegram_user_id = excluded.reporter_telegram_user_id
            """,
            (market_id, report_date, reporter_telegram_user_id, _now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM shift_report WHERE market_id = ? AND report_date = ?", (market_id, report_date)
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
            "UPDATE shift_report SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(data, ensure_ascii=False), _now(), report_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_report_status(report_id: int, status: str) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE shift_report SET status = ?, updated_at = ? WHERE id = ?", (status, _now(), report_id))
        conn.commit()
    finally:
        conn.close()


def get_report(report_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM shift_report WHERE id = ?", (report_id,)).fetchone()
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
            "SELECT * FROM shift_report WHERE market_id = ? AND report_date = ?", (market_id, report_date)
        ).fetchone()
        if not row:
            return None
        report = dict(row)
        report["data"] = json.loads(report["data"])
        return report
    finally:
        conn.close()


def delete_report(market_id: int, report_date: str) -> bool:
    """Удаляет отчёт целиком — владельцу нужно, если сбор начали по
    ошибке (не тот рынок, тестовый прогон) и надо начать заново тем же
    днём. Возвращает False, если отчёта на эту дату и не было."""
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM shift_report WHERE market_id = ? AND report_date = ?", (market_id, report_date))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_previous_week_report(market_id: int, report_date: str) -> dict | None:
    """Отчёт того же рынка за тот же день недели неделей раньше, только
    если он дошёл до конца (согласован или уже разослан) — используется
    для сравнения выручки/среднего чека/гостей в текущем отчёте."""
    prev_date = (date.fromisoformat(report_date) - timedelta(days=7)).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM shift_report WHERE market_id = ? AND report_date = ? AND status IN ('approved', 'dispatched')",
            (market_id, prev_date),
        ).fetchone()
        if not row:
            return None
        report = dict(row)
        report["data"] = json.loads(report["data"])
        return report
    finally:
        conn.close()


def list_reports_by_status_and_date(report_date: str, status: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM shift_report WHERE report_date = ? AND status = ?", (report_date, status)
        ).fetchall()
        reports = []
        for row in rows:
            report = dict(row)
            report["data"] = json.loads(report["data"])
            reports.append(report)
        return reports
    finally:
        conn.close()


def set_report_chat(market_id: int, role: str, chat_id: int, mention: str = "") -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO report_chat (market_id, role, chat_id, mention) VALUES (?, ?, ?, ?)
            ON CONFLICT (market_id, role) DO UPDATE SET chat_id = excluded.chat_id, mention = excluded.mention
            """,
            (market_id, role, chat_id, mention),
        )
        conn.commit()
    finally:
        conn.close()


def get_report_chat(market_id: int, role: str) -> dict | None:
    """Возвращает {"chat_id":, "mention":} — mention (например,
    @Motus_control_group_bot) ставится первой строкой перед отчётом, чтобы
    бот финпартнёров подхватил сообщение из чата (см. /register_report_chat)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT chat_id, mention FROM report_chat WHERE market_id = ? AND role = ?", (market_id, role)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_report_chats() -> list[dict]:
    """Все привязанные чаты отчётов по сменам — для /report_chats."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM report_chat ORDER BY market_id, role").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_report_chat(market_id: int, role: str) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM report_chat WHERE market_id = ? AND role = ?", (market_id, role))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
