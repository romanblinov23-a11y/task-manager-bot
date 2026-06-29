from datetime import datetime

from config.projects import EVENT_TYPES
from sheets.client import open_project_spreadsheet
from sheets.schema import LOG_COLUMNS, LOG_SHEET


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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

    ws = open_project_spreadsheet(project).worksheet(LOG_SHEET)
    row = {
        "task_id": task_id,
        "timestamp": _now(),
        "event_type": event_type,
        "old_value": old_value,
        "new_value": new_value,
        "reason_comment": reason_comment,
    }
    ws.append_row([row[col] for col in LOG_COLUMNS], value_input_option="USER_ENTERED")
