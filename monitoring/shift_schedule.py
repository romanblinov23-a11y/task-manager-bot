from monitoring.db import get_connection


def set_shift_schedule(market_id: int, entries: list[tuple[str, int]]) -> None:
    """Записывает график смен на рынке — кто сдаёт вечерний отчёт в какую
    дату. Каждая дата затирает предыдущее назначение (управляющий может
    перезалить график, если он поменялся)."""
    conn = get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO shift_schedule (market_id, shift_date, manager_telegram_user_id)
            VALUES (?, ?, ?)
            ON CONFLICT (market_id, shift_date) DO UPDATE SET manager_telegram_user_id = excluded.manager_telegram_user_id
            """,
            [(market_id, shift_date, telegram_user_id) for shift_date, telegram_user_id in entries],
        )
        conn.commit()
    finally:
        conn.close()


def get_scheduled_manager(market_id: int, date_iso: str) -> int | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT manager_telegram_user_id FROM shift_schedule WHERE market_id = ? AND shift_date = ?",
            (market_id, date_iso),
        ).fetchone()
        return row["manager_telegram_user_id"] if row else None
    finally:
        conn.close()


def list_markets_with_shift(date_iso: str) -> list[dict]:
    """Рынки (с id и назначенным менеджером), у которых есть запись графика
    на указанную дату — используется джобами кикоффа/эскалации отчёта."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT market.*, shift_schedule.manager_telegram_user_id AS scheduled_manager_id
            FROM shift_schedule
            JOIN market ON market.id = shift_schedule.market_id
            WHERE shift_schedule.shift_date = ?
            """,
            (date_iso,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
