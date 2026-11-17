import pytest

import monitoring.db as monitoring_db


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Изолированная база мониторинга: реальные данные тесты не трогают."""
    monkeypatch.setattr(monitoring_db, "_DB_PATH", tmp_path / "test_monitoring.db")
    monitoring_db.init_schema()
    yield
