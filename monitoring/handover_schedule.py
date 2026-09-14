from monitoring.db import get_connection


def set_handover_schedule(market_id: int, entries: list[tuple[str, int]]) -> None:
    """Записывает график пересменок на рынке — кто сдаёт пересменку в
    какую дату. Отдельный от shift_schedule график: пересменку и вечерний
    отчёт по смене может сдавать не один и тот же человек. Каждая дата
    затирает предыдущее назначение."""
    conn = get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO handover_schedule (market_id, handover_date, manager_telegram_user_id)
            VALUES (?, ?, ?)
            ON CONFLICT (market_id, handover_date) DO UPDATE SET manager_telegram_user_id = excluded.manager_telegram_user_id
            """,
            [(market_id, handover_date, telegram_user_id) for handover_date, telegram_user_id in entries],
        )
        conn.commit()
    finally:
        conn.close()


def get_scheduled_handover_manager(market_id: int, date_iso: str) -> int | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT manager_telegram_user_id FROM handover_schedule WHERE market_id = ? AND handover_date = ?",
            (market_id, date_iso),
        ).fetchone()
        return row["manager_telegram_user_id"] if row else None
    finally:
        conn.close()


def list_markets_with_handover(date_iso: str) -> list[dict]:
    """Рынки (с id и назначенным менеджером), у которых есть запись графика
    пересменок на указанную дату — используется джобой кикоффа (см.
    bot/handover_reports.py)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT market.*, handover_schedule.manager_telegram_user_id AS scheduled_manager_id
            FROM handover_schedule
            JOIN market ON market.id = handover_schedule.market_id
            WHERE handover_schedule.handover_date = ?
            """,
            (date_iso,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
