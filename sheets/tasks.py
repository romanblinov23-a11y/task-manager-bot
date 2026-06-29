import gspread

from config.projects import CATEGORIES, PROJECTS, STATUSES
from config.timeutil import now as tz_now
from sheets.client import open_project_spreadsheet
from sheets.schema import TASKS_COLUMNS, TASKS_SHEET


def _now() -> str:
    return tz_now().strftime("%Y-%m-%d %H:%M:%S")


def _worksheet(project: str) -> gspread.Worksheet:
    return open_project_spreadsheet(project).worksheet(TASKS_SHEET)


def _find_row_index(ws: gspread.Worksheet, task_id: str) -> int | None:
    for i, value in enumerate(ws.col_values(1), start=1):
        if value == task_id:
            return i
    return None


def generate_task_id(project: str) -> str:
    ws = _worksheet(project)
    max_num = 0
    for task_id in ws.col_values(1)[1:]:  # пропускаем заголовок
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
    if project not in PROJECTS:
        raise ValueError(f"Неизвестный проект: {project}")
    if category not in CATEGORIES:
        raise ValueError(f"Неизвестная категория: {category}")
    if status not in STATUSES:
        raise ValueError(f"Неизвестный статус: {status}")

    ws = _worksheet(project)
    task_id = generate_task_id(project)
    row = {
        "task_id": task_id,
        "created_at": _now(),
        "source": source,
        "source_chat": source_chat,
        "source_link": source_link,
        "project": project,
        "category": category,
        "task_text": task_text,
        "assignee": assignee,
        "assignee_telegram_id": assignee_telegram_id,
        "deadline_original": deadline_original,
        "deadline_current": deadline_original,
        "status": status,
        "last_comment": "",
        "needs_help": "нет",
        "last_status_check": "",
        "closed_at": "",
    }
    ws.append_row([row[col] for col in TASKS_COLUMNS], value_input_option="USER_ENTERED")
    return task_id


def get_task(project: str, task_id: str) -> dict | None:
    for record in get_all_tasks(project):
        if record.get("task_id") == task_id:
            return record
    return None


def get_all_tasks(project: str) -> list[dict]:
    return _worksheet(project).get_all_records()


def update_task(project: str, task_id: str, **updates) -> None:
    if "category" in updates and updates["category"] not in CATEGORIES:
        raise ValueError(f"Неизвестная категория: {updates['category']}")
    if "status" in updates and updates["status"] not in STATUSES:
        raise ValueError(f"Неизвестный статус: {updates['status']}")

    ws = _worksheet(project)
    row_idx = _find_row_index(ws, task_id)
    if row_idx is None:
        raise ValueError(f"Задача {task_id} не найдена в проекте '{project}'")

    for field, value in updates.items():
        if field not in TASKS_COLUMNS:
            raise ValueError(f"Неизвестная колонка: {field}")
        col_idx = TASKS_COLUMNS.index(field) + 1
        ws.update_cell(row_idx, col_idx, value)
