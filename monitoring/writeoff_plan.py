from monitoring.db import get_connection


def set_writeoff_plan(market_id: int, expiry_plan: float, compliment_plan: float, staff_meals_plan: float) -> None:
    """Дневная норма списаний по точке (себестоимость, ₽/день) — задаёт
    владелец через /set_writeoff_plan. Одна на рынок, не по дням, в
    отличие от revenue/чеков (см. monitoring.monthly_plan)."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO writeoff_plan (market_id, expiry_plan, compliment_plan, staff_meals_plan)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (market_id) DO UPDATE SET
                expiry_plan = excluded.expiry_plan,
                compliment_plan = excluded.compliment_plan,
                staff_meals_plan = excluded.staff_meals_plan
            """,
            (market_id, expiry_plan, compliment_plan, staff_meals_plan),
        )
        conn.commit()
    finally:
        conn.close()


def get_writeoff_plan(market_id: int) -> dict | None:
    """{"expiry_plan", "compliment_plan", "staff_meals_plan"} — или None,
    если владелец ещё не задавал норму для этой точки (тогда отчёт
    показывает списания без сравнения с планом)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT expiry_plan, compliment_plan, staff_meals_plan FROM writeoff_plan WHERE market_id = ?",
            (market_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
