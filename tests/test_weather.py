from unittest.mock import MagicMock, patch

from dashboard.weather import describe_weather_code, get_weather_range
from monitoring.db import get_connection


def _mock_geocode_response():
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"results": [{"latitude": 55.75, "longitude": 37.62}]}
    return resp


def _mock_archive_response(dates, temps, precs, codes):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "daily": {
            "time": dates,
            "temperature_2m_mean": temps,
            "precipitation_sum": precs,
            "weathercode": codes,
        }
    }
    return resp


def test_get_weather_range_fetches_and_caches():
    geocode_resp = _mock_geocode_response()
    archive_resp = _mock_archive_response(
        ["2026-09-01", "2026-09-02"], [20.5, 18.0], [0.0, 3.2], [0, 61]
    )

    with patch("dashboard.weather.httpx.get", side_effect=[geocode_resp, archive_resp]) as mock_get:
        result = get_weather_range("Москва", "2026-09-01", "2026-09-02")

    assert mock_get.call_count == 2
    assert result["2026-09-01"]["temp_avg_c"] == 20.5
    assert result["2026-09-02"]["precipitation_mm"] == 3.2

    # Второй вызов на тот же диапазон должен полностью взяться из кэша — без сети.
    with patch("dashboard.weather.httpx.get") as mock_get_cached:
        result2 = get_weather_range("Москва", "2026-09-01", "2026-09-02")

    mock_get_cached.assert_not_called()
    assert result2["2026-09-01"]["temp_avg_c"] == 20.5

    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) AS c FROM city_geocode").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM weather_cache").fetchone()["c"] == 2
    finally:
        conn.close()


def test_get_weather_range_returns_empty_on_geocode_failure():
    with patch("dashboard.weather.httpx.get", side_effect=Exception("network down")):
        result = get_weather_range("Незнакомый город", "2026-09-01", "2026-09-01")
    assert result == {}


def test_describe_weather_code():
    assert "ясно" in describe_weather_code(0)
    assert describe_weather_code(None) == "—"
    assert describe_weather_code(9999) == "—"
