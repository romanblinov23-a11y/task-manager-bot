from monitoring.db import get_connection


def set_monthly_plan(market_id: int, entries: list[tuple[str, float, int]]) -> None:
    """Записывает план по выручке/чекам на месяц по дням — каждая дата
    затирает предыдущее значение (управляющий может перезалить план,
    например 25-го на новый месяц, или поправить его вручную в любой день)."""
    conn = get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO monthly_plan (market_id, plan_date, revenue_plan, checks_plan)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (market_id, plan_date) DO UPDATE SET
                revenue_plan = excluded.revenue_plan, checks_plan = excluded.checks_plan
            """,
            [(market_id, plan_date, revenue, checks) for plan_date, revenue, checks in entries],
        )
        conn.commit()
    finally:
        conn.close()


def get_daily_plan(market_id: int, date_iso: str) -> dict | None:
    """{"revenue_plan": float, "checks_plan": int} на конкретный день, или
    None, если план на эту дату не загружен."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT revenue_plan, checks_plan FROM monthly_plan WHERE market_id = ? AND plan_date = ?",
            (market_id, date_iso),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
