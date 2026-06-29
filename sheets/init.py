import gspread

from config.projects import SPREADSHEET_IDS
from sheets.client import open_project_spreadsheet
from sheets.schema import (
    COMMENTS_COLUMNS,
    COMMENTS_SHEET,
    LOG_COLUMNS,
    LOG_SHEET,
    TASKS_COLUMNS,
    TASKS_SHEET,
)


def _ensure_worksheet(spreadsheet: gspread.Spreadsheet, title: str, columns: list[str]) -> gspread.Worksheet:
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=1000, cols=len(columns))
    if not ws.row_values(1):
        ws.update("A1", [columns])
    return ws


def ensure_project_sheets(project: str) -> None:
    """Создаёт листы "Задачи"/"Лог переносов и изменений"/"Комментарии" с
    заголовками, если их ещё нет — на случай, если таблица была создана
    с нуля, а не из готового XLSX-шаблона."""
    sh = open_project_spreadsheet(project)
    _ensure_worksheet(sh, TASKS_SHEET, TASKS_COLUMNS)
    _ensure_worksheet(sh, LOG_SHEET, LOG_COLUMNS)
    _ensure_worksheet(sh, COMMENTS_SHEET, COMMENTS_COLUMNS)


def ensure_all_sheets() -> None:
    for project in SPREADSHEET_IDS:
        ensure_project_sheets(project)
