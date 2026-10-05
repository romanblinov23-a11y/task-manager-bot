"""Состояние сборки отчёта ФП по пользователю — файлами в папке рядом с основной БД.

Управляющий может отвечать на вопросы часами, поэтому состояние не держим в памяти
процесса: деплой или перезапуск не должен сбрасывать сбор. Файлы лежат в той же папке,
что и БД, — на томе Railway они переживают перезапуск.
"""

import json
import shutil
from pathlib import Path

from config.settings import MONITORING_DB_PATH

FP_REPORTS_DIR = Path(MONITORING_DB_PATH).parent / "fp_reports"


def session_dir(uid: int) -> Path:
    return FP_REPORTS_DIR / str(uid)


def load_session(uid: int) -> dict | None:
    path = session_dir(uid) / "session.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_session(uid: int, data: dict) -> None:
    directory = session_dir(uid)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "session.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_inputs(uid: int, inputs: dict) -> None:
    (session_dir(uid) / "inputs.json").write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")


def load_inputs(uid: int) -> dict:
    return json.loads((session_dir(uid) / "inputs.json").read_text(encoding="utf-8"))


def delete_session(uid: int) -> None:
    shutil.rmtree(session_dir(uid), ignore_errors=True)
