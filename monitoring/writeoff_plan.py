from monitoring.db import get_connection


def set_writeoff_plan(market_id: int, expiry_pct: float, compliment_pct: float, staff_meals_pct: float) -> None:
    """Норма списаний по точке — % от выручки, задаёт владелец через
    /set_writeoff_plan. Одна на рынок, не по дням, в отличие от
    revenue/чеков (см. monitoring.monthly_plan). Плановая сумма в рублях
    считается на лету от выручки конкретного дня (см. writeoff_plan_amounts)
    — так норма сама масштабируется под поток гостей, а не остаётся
    вчерашней фиксированной суммой."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO writeoff_plan (market_id, expiry_pct, compliment_pct, staff_meals_pct)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (market_id) DO UPDATE SET
                expiry_pct = excluded.expiry_pct,
                compliment_pct = excluded.compliment_pct,
                staff_meals_pct = excluded.staff_meals_pct
            """,
            (market_id, expiry_pct, compliment_pct, staff_meals_pct),
        )
        conn.commit()
    finally:
        conn.close()


def get_writeoff_plan(market_id: int) -> dict | None:
    """{"expiry_pct", "compliment_pct", "staff_meals_pct"} — или None, если
    владелец ещё не задавал норму для этой точки (тогда отчёт/утреннее
    сообщение показывают списания без плана)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT expiry_pct, compliment_pct, staff_meals_pct FROM writeoff_plan WHERE market_id = ?",
            (market_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def writeoff_plan_amounts(plan: dict, revenue: float) -> dict:
    """{"expiry", "compliment", "staff_meals"} в рублях на конкретный день
    = % нормы × выручка этого дня. Выручка передаётся вызывающим кодом —
    плановая (утреннее сообщение команде, факта дня ещё нет) или
    фактическая (вечерний отчёт), см. bot/shift_reports.py."""
    return {
        "expiry": revenue * plan["expiry_pct"] / 100,
        "compliment": revenue * plan["compliment_pct"] / 100,
        "staff_meals": revenue * plan["staff_meals_pct"] / 100,
    }
