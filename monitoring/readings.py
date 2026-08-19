from config.timeutil import today
from monitoring.constants import MONITORING_CYCLE_DAYS
from monitoring.db import get_connection


def _cutoff_date() -> str:
    return today().fromordinal(today().toordinal() - MONITORING_CYCLE_DAYS).isoformat()


def record_reading(
    competitor_id: int, avg_checks_per_day: float, created_by: int, note: str = "", reading_at: str | None = None
) -> dict:
    """Одно снятие на конкурента за цикл мониторинга (§7): если для этого
    конкурента уже есть снятие моложе MONITORING_CYCLE_DAYS дней — обновляет
    его, а не создаёт дубль."""
    conn = get_connection()
    try:
        cutoff = _cutoff_date()
        existing = conn.execute(
            """
            SELECT id FROM daily_avg_reading
            WHERE competitor_id = ? AND reading_at >= ?
            ORDER BY reading_at DESC LIMIT 1
            """,
            (competitor_id, cutoff),
        ).fetchone()
        reading_at = reading_at or today().isoformat()
        if existing:
            conn.execute(
                """
                UPDATE daily_avg_reading
                SET avg_checks_per_day = ?, reading_at = ?, created_by = ?, note = ?
                WHERE id = ?
                """,
                (avg_checks_per_day, reading_at, created_by, note, existing["id"]),
            )
            reading_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO daily_avg_reading (competitor_id, reading_at, avg_checks_per_day, created_by, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (competitor_id, reading_at, avg_checks_per_day, created_by, note),
            )
            reading_id = cursor.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM daily_avg_reading WHERE id = ?", (reading_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_latest_reading(competitor_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM daily_avg_reading WHERE competitor_id = ? ORDER BY reading_at DESC LIMIT 1",
            (competitor_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_readings(competitor_id: int, limit: int | None = None) -> list[dict]:
    conn = get_connection()
    try:
        query = "SELECT * FROM daily_avg_reading WHERE competitor_id = ? ORDER BY reading_at DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query, (competitor_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_last_market_cycle_date(market_id: int) -> str | None:
    """Дата последнего снятия по любому конкуренту этого рынка — используется
    для проверки «не чаще раза в неделю» на уровне рынка (§7, §5.3)."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT MAX(daily_avg_reading.reading_at) AS last_date
            FROM daily_avg_reading
            JOIN competitor ON competitor.id = daily_avg_reading.competitor_id
            WHERE competitor.market_id = ?
            """,
            (market_id,),
        ).fetchone()
        return row["last_date"] if row else None
    finally:
        conn.close()


def get_last_reading_dates_by_creator(market_id: int) -> dict[int, str]:
    """Для каждого telegram_user_id, вносившего снятия по конкурентам этого
    рынка — дата его последнего снятия. Используется для показа активности
    менеджеров рынка на дашборде."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT daily_avg_reading.created_by AS created_by, MAX(daily_avg_reading.reading_at) AS last_date
            FROM daily_avg_reading
            JOIN competitor ON competitor.id = daily_avg_reading.competitor_id
            WHERE competitor.market_id = ?
            GROUP BY daily_avg_reading.created_by
            """,
            (market_id,),
        ).fetchall()
        return {row["created_by"]: row["last_date"] for row in rows}
    finally:
        conn.close()


def get_competitors_pending_this_cycle(market_id: int) -> list[dict]:
    """Конкуренты (и точка Surf) рынка, у которых нет снятия за последние
    MONITORING_CYCLE_DAYS дней — это то, что ещё нужно пройти на текущем
    /monitoring (включая пропущенные в прошлый раз точки, §5.3)."""
    from monitoring.competitors import list_competitors

    cutoff = _cutoff_date()
    pending = []
    for competitor in list_competitors(market_id):
        latest = get_latest_reading(competitor["id"])
        if not latest or latest["reading_at"] < cutoff:
            pending.append(competitor)
    return pending
