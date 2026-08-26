from datetime import timedelta

from config.timeutil import today
from monitoring.db import get_connection


def _week_start(d):
    return d - timedelta(days=d.weekday())


def current_cycle_start() -> str:
    """Начало текущего цикла мониторинга — понедельник этой календарной
    недели. Раньше гейт "уже проведён на этой неделе" считался формулой
    "7 дней с последнего снятия", что было некорректно: если обход рынка в
    одну неделю прошёл, скажем, в четверг, а в другую — в понедельник, то
    ближайшая допустимая дата съезжала на день-два вперёд от реального
    начала новой недели. Понедельник — фиксированная точка отсчёта, не
    зависящая от того, в какой день недели фактически прошёл предыдущий
    обход."""
    return _week_start(today()).isoformat()


def next_cycle_start() -> str:
    """Понедельник следующей календарной недели — дата, с которой станет
    доступен новый цикл мониторинга."""
    return (_week_start(today()) + timedelta(days=7)).isoformat()


def record_reading(
    competitor_id: int, avg_checks_per_day: float, created_by: int, note: str = "", reading_at: str | None = None
) -> dict:
    """Одно снятие на конкурента за цикл мониторинга (§7): если для этого
    конкурента уже есть снятие в рамках текущей календарной недели —
    обновляет его, а не создаёт дубль."""
    conn = get_connection()
    try:
        cutoff = current_cycle_start()
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


def import_historical_reading(
    competitor_id: int, avg_checks_per_day: float, created_by: int, reading_at: str, note: str = ""
) -> dict:
    """Вставляет/обновляет снятие на КОНКРЕТНУЮ историческую дату — для
    массового импорта накопленных данных прошлых недель (/import_readings).
    В отличие от record_reading, дубль ищется по точной дате reading_at, а
    не по "последнему снятию за неделю от сегодня" — та логика рассчитана
    на обычный еженедельный цикл и при бэкфилле задним числом затёрла бы
    уже внесённые свежие снятия текущей недели."""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM daily_avg_reading WHERE competitor_id = ? AND reading_at = ?",
            (competitor_id, reading_at),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE daily_avg_reading SET avg_checks_per_day = ?, created_by = ?, note = ? WHERE id = ?",
                (avg_checks_per_day, created_by, note, existing["id"]),
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


def update_reading(reading_id: int, *, reading_at: str | None = None, avg_checks_per_day: float | None = None) -> None:
    """Точечно исправляет уже сохранённое снятие — дату и/или значение.
    Нужна владельцу, если снятие один раз внесли неверно (например, с
    датой в будущем — из-за такой записи гейт по календарной неделе
    считает точку вечно "уже пройденной") и чинить это приходится не через
    обычный цикл /monitoring, а вручную, см. /fix_reading."""
    updates = {}
    if reading_at is not None:
        updates["reading_at"] = reading_at
    if avg_checks_per_day is not None:
        updates["avg_checks_per_day"] = avg_checks_per_day
    if not updates:
        return
    conn = get_connection()
    try:
        set_clause = ", ".join(f"{field} = ?" for field in updates)
        params = [*updates.values(), reading_id]
        conn.execute(f"UPDATE daily_avg_reading SET {set_clause} WHERE id = ?", params)
        conn.commit()
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
    """Конкуренты (и точка Surf) рынка, у которых нет снятия на текущей
    календарной неделе — это то, что ещё нужно пройти на текущем
    /monitoring (включая пропущенные в прошлый раз точки, §5.3)."""
    from monitoring.competitors import list_competitors

    cutoff = current_cycle_start()
    pending = []
    for competitor in list_competitors(market_id):
        latest = get_latest_reading(competitor["id"])
        if not latest or latest["reading_at"] < cutoff:
            pending.append(competitor)
    return pending
