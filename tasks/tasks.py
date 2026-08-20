from config.projects import CATEGORIES, STATUSES
from config.timeutil import now as tz_now
from monitoring.markets import list_market_names
from tasks.db import get_connection

_UPDATABLE_COLUMNS = {
    "created_at",
    "source",
    "source_chat",
    "source_link",
    "category",
    "task_text",
    "assignee",
    "assignee_telegram_id",
    "deadline_original",
    "deadline_current",
    "status",
    "last_comment",
    "needs_help",
    "last_status_check",
    "closed_at",
}


def _now() -> str:
    return tz_now().strftime("%Y-%m-%d %H:%M:%S")


def generate_task_id(project: str) -> str:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT task_id FROM task WHERE project = ?", (project,)).fetchall()
    finally:
        conn.close()
    max_num = 0
    for row in rows:
        task_id = row["task_id"]
        if task_id.startswith("TASK-"):
            try:
                max_num = max(max_num, int(task_id.split("-")[1]))
            except (IndexError, ValueError):
                continue
    return f"TASK-{max_num + 1:04d}"


def create_task(
    project: str,
    *,
    source: str,
    task_text: str,
    category: str,
    assignee: str = "",
    assignee_telegram_id: str = "",
    source_chat: str = "",
    source_link: str = "",
    deadline_original: str = "",
    status: str = "новая",
) -> str:
    if project not in list_market_names():
        raise ValueError(f"Неизвестный проект: {project}")
    if category not in CATEGORIES:
        raise ValueError(f"Неизвестная категория: {category}")
    if status not in STATUSES:
        raise ValueError(f"Неизвестный статус: {status}")

    task_id = generate_task_id(project)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task (
                task_id, project, created_at, source, source_chat, source_link,
                category, task_text, assignee, assignee_telegram_id,
                deadline_original, deadline_current, status, needs_help
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'нет')
            """,
            (
                task_id,
                project,
                _now(),
                source,
                source_chat,
                source_link,
                category,
                task_text,
                assignee,
                assignee_telegram_id,
                deadline_original,
                deadline_original,
                status,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return task_id


def get_task(project: str, task_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM task WHERE project = ? AND task_id = ?", (project, task_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_all_tasks(project: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM task WHERE project = ? ORDER BY id", (project,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_task(project: str, task_id: str, **updates) -> None:
    if "category" in updates and updates["category"] not in CATEGORIES:
        raise ValueError(f"Неизвестная категория: {updates['category']}")
    if "status" in updates and updates["status"] not in STATUSES:
        raise ValueError(f"Неизвестный статус: {updates['status']}")
    for field in updates:
        if field not in _UPDATABLE_COLUMNS:
            raise ValueError(f"Неизвестная колонка: {field}")

    conn = get_connection()
    try:
        set_clause = ", ".join(f"{field} = ?" for field in updates)
        params = [*updates.values(), project, task_id]
        cursor = conn.execute(f"UPDATE task SET {set_clause} WHERE project = ? AND task_id = ?", params)
        if cursor.rowcount == 0:
            raise ValueError(f"Задача {task_id} не найдена в проекте '{project}'")
        conn.commit()
    finally:
        conn.close()


def move_task(old_project: str, task_id: str, new_project: str) -> str:
    """Переносит задачу в другой проект. task_id уникален только в пределах
    проекта (UNIQUE(project, task_id)), поэтому переносу нужен новый task_id
    в целевом проекте — вместе с задачей переносится её лог и комментарии."""
    if new_project not in list_market_names():
        raise ValueError(f"Неизвестный проект: {new_project}")

    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM task WHERE project = ? AND task_id = ?", (old_project, task_id)
        ).fetchone()
        if not exists:
            raise ValueError(f"Задача {task_id} не найдена в проекте '{old_project}'")

        new_task_id = generate_task_id(new_project)
        conn.execute(
            "UPDATE task SET project = ?, task_id = ? WHERE project = ? AND task_id = ?",
            (new_project, new_task_id, old_project, task_id),
        )
        conn.execute(
            "UPDATE task_log SET project = ?, task_id = ? WHERE project = ? AND task_id = ?",
            (new_project, new_task_id, old_project, task_id),
        )
        conn.execute(
            "UPDATE task_comment SET project = ?, task_id = ? WHERE project = ? AND task_id = ?",
            (new_project, new_task_id, old_project, task_id),
        )
        conn.commit()
        return new_task_id
    finally:
        conn.close()
