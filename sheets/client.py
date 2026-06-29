import gspread
from google.oauth2.service_account import Credentials

from config.projects import SPREADSHEET_IDS
from config.settings import GOOGLE_SERVICE_ACCOUNT_FILE

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

_client: gspread.Client | None = None


def get_client() -> gspread.Client:
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        _client = gspread.authorize(creds)
    return _client


def open_project_spreadsheet(project: str) -> gspread.Spreadsheet:
    sheet_id = SPREADSHEET_IDS.get(project)
    if not sheet_id:
        raise ValueError(f"Нет spreadsheet_id для проекта '{project}' (проверь .env)")
    return get_client().open_by_key(sheet_id)
