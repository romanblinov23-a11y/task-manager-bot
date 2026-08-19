from config.projects import EVENT_TYPES
from config.timeutil import now as tz_now
from tasks.db import get_connection


def _now() -> str:
    return tz_now().strftime("%Y-%m-%d %H:%M:%S")


def append_log_entry(
    project: str,
    task_id: str,
    event_type: str,
    old_value: str = "",
    new_value: str = "",
    reason_comment: str = "",
) -> None:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Неизвестный event_type: {event_type}")

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_log (task_id, project, timestamp, event_type, old_value, new_value, reason_comment)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, project, _now(), event_type, old_value, new_value, reason_comment),
        )
        conn.commit()
    finally:
        conn.close()


def get_log_entries(project: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM task_log WHERE project = ? ORDER BY id", (project,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
