from monitoring.db import get_connection


def get_schedule(market_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM monitoring_schedule WHERE market_id = ?", (market_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["weekdays"] = [int(d) for d in data["weekdays"].split(",") if d]
        return data
    finally:
        conn.close()


def set_schedule(market_id: int, weekdays: list[int]) -> None:
    """weekdays: список 1 (пн) … 7 (вс), см. §4."""
    weekdays_str = ",".join(str(d) for d in sorted(set(weekdays)))
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO monitoring_schedule (market_id, weekdays, active)
            VALUES (?, ?, 1)
            ON CONFLICT (market_id) DO UPDATE SET weekdays = excluded.weekdays, active = 1
            """,
            (market_id, weekdays_str),
        )
        conn.commit()
    finally:
        conn.close()


def list_markets_scheduled_for_weekday(weekday: int) -> list[int]:
    """Все market_id, у которых сегодняшний день недели (1=пн…7=вс) входит
    в активное расписание — используется ежедневным job'ом напоминаний."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT market_id, weekdays FROM monitoring_schedule WHERE active = 1").fetchall()
    finally:
        conn.close()
    result = []
    for row in rows:
        days = [int(d) for d in row["weekdays"].split(",") if d]
        if weekday in days:
            result.append(row["market_id"])
    return result
