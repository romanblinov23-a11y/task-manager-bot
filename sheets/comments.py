from config.projects import COMMENT_AUTHORS
from config.timeutil import now as tz_now
from sheets.client import open_project_spreadsheet
from sheets.schema import COMMENTS_COLUMNS, COMMENTS_SHEET


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

    ws = open_project_spreadsheet(project).worksheet(COMMENTS_SHEET)
    row = {
        "task_id": task_id,
        "timestamp": _now(),
        "author": author,
        "comment_text": comment_text,
        "related_status": related_status,
    }
    ws.append_row([row[col] for col in COMMENTS_COLUMNS], value_input_option="USER_ENTERED")
