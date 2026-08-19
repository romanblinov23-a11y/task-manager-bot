from config.timeutil import now as tz_now
from tasks.db import get_connection


def purge_closed_tasks() -> int:
    """Удаляет задачи со статусом «выполнена», закрытые ДО начала текущего
    календарного месяца — вместе с их логом и комментариями. Держим
    завершённые задачи до конца месяца, в котором их закрыли (видны в
    /weekly, на дашборде), дальше они не нужны и просто занимают место.
    Возвращает число удалённых задач."""
    cutoff = tz_now().strftime("%Y-%m-01 00:00:00")
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT task_id, project FROM task WHERE status = 'выполнена' AND closed_at != '' AND closed_at < ?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            conn.execute("DELETE FROM task_log WHERE task_id = ? AND project = ?", (row["task_id"], row["project"]))
            conn.execute("DELETE FROM task_comment WHERE task_id = ? AND project = ?", (row["task_id"], row["project"]))
            conn.execute("DELETE FROM task WHERE task_id = ? AND project = ?", (row["task_id"], row["project"]))
        conn.commit()
        return len(rows)
    finally:
        conn.close()
