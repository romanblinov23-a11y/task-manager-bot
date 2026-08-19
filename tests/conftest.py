import pytest

import monitoring.db as db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Каждый тест получает свою чистую SQLite-базу — модуль-уровневый
    _DB_PATH в monitoring.db подменяется на временный файл, так что тесты
    не видят данные друг друга и не трогают реальную data/monitoring.db."""
    monkeypatch.setattr(db, "_DB_PATH", tmp_path / "test_monitoring.db")
    db.init_schema()
    yield
