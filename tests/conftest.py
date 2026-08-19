import pytest

import monitoring.db as monitoring_db
import tasks.db as tasks_db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Каждый тест получает свои чистые SQLite-базы — модуль-уровневые
    _DB_PATH в monitoring.db и tasks.db подменяются на временные файлы, так
    что тесты не видят данные друг друга и не трогают реальные data/*.db."""
    monkeypatch.setattr(monitoring_db, "_DB_PATH", tmp_path / "test_monitoring.db")
    monitoring_db.init_schema()
    monkeypatch.setattr(tasks_db, "_DB_PATH", tmp_path / "test_tasks.db")
    tasks_db.init_schema()
    yield
