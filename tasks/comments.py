from config.projects import COMMENT_AUTHORS
from config.timeutil import now as tz_now
from tasks.db import get_connection


def _now() -> str:
    return tz_now().strftime("%Y-%m-%d %H:%M:%S")


def append_comment(
    project: str,
    task_id: str,
    author: str,
    comment_text: str,
    related_status: str = "",
) -> None:
    if author not in COMMENT_AUTHORS:
        raise ValueError(f"Неизвестный author: {author}")

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_comment (task_id, project, timestamp, author, comment_text, related_status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, project, _now(), author, comment_text, related_status),
        )
        conn.commit()
    finally:
        conn.close()
