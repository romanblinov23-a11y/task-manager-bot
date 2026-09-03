from monitoring.db import get_connection


def set_meeting_schedule(market_id: int, meeting_type: str, weekday: int, time_str: str) -> None:
    """Задаёт повторяющийся еженедельный ритм собрания (день недели +
    время) — Управляющий может перезадать его в любой момент, новое
    значение затирает старое."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO meeting_schedule (market_id, meeting_type, weekday, time)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (market_id, meeting_type) DO UPDATE SET weekday = excluded.weekday, time = excluded.time
            """,
            (market_id, meeting_type, weekday, time_str),
        )
        conn.commit()
    finally:
        conn.close()


def get_meeting_schedule(market_id: int, meeting_type: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM meeting_schedule WHERE market_id = ? AND meeting_type = ?", (market_id, meeting_type)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_meeting_schedules_for_weekday(weekday: int) -> list[dict]:
    """Все настроенные ритмы собраний (по всем рынкам/типам), у которых
    день недели совпадает с указанным — используется джобом-напоминанием
    за сутки, чтобы найти, у кого завтра должно быть собрание."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM meeting_schedule WHERE weekday = ?", (weekday,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def create_or_get_instance(market_id: int, meeting_type: str, meeting_date: str, meeting_time: str) -> dict:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO meeting_instance (market_id, meeting_type, meeting_date, meeting_time)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (market_id, meeting_type, meeting_date) DO UPDATE SET meeting_type = excluded.meeting_type
            """,
            (market_id, meeting_type, meeting_date, meeting_time),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM meeting_instance WHERE market_id = ? AND meeting_type = ? AND meeting_date = ?",
            (market_id, meeting_type, meeting_date),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_instance(instance_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM meeting_instance WHERE id = ?", (instance_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_instance_status(instance_id: int, status: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE meeting_instance SET status = ?, updated_at = datetime('now') WHERE id = ?", (status, instance_id)
        )
        conn.commit()
    finally:
        conn.close()


def set_instance_invite_roman(instance_id: int, invite: bool) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE meeting_instance SET invite_roman = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if invite else 0, instance_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_instance_agenda(instance_id: int, agenda: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE meeting_instance SET agenda = ?, updated_at = datetime('now') WHERE id = ?", (agenda, instance_id)
        )
        conn.commit()
    finally:
        conn.close()


def reschedule_instance(instance_id: int, new_date: str, new_time: str) -> None:
    """Переносит собрание на другую дату/время — тот же экземпляр (id и
    вся дальнейшая история: приглашение Ромы, повестка) продолжает жить
    под новой датой, статус сбрасывается на дальнейшую обработку (вопрос
    про Рому/повестку) вызывающей стороной."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE meeting_instance SET meeting_date = ?, meeting_time = ?, rescheduled = 1, updated_at = datetime('now') WHERE id = ?",
            (new_date, new_time, instance_id),
        )
        conn.commit()
    finally:
        conn.close()
