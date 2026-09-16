"""Реальная погода для дашборда (см. dashboard/data.py) — Open-Meteo,
бесплатно и без ключа. Два шага: один раз геокодируем город в координаты
(кэш в city_geocode), дальше по координатам тянем архив дневной погоды за
диапазон дат одним запросом и кэшируем каждый день отдельно (weather_cache),
чтобы повторный показ дашборда не бил по внешнему API заново."""

import httpx

from monitoring.db import get_connection

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_TIMEOUT = 10.0

_WEATHER_CODE_RU = {
    0: "☀️ ясно",
    1: "🌤 малооблачно",
    2: "⛅️ облачно",
    3: "☁️ пасмурно",
    45: "🌫 туман",
    48: "🌫 изморозь",
    51: "🌦 морось",
    53: "🌦 морось",
    55: "🌧 морось",
    56: "🌧 ледяная морось",
    57: "🌧 ледяная морось",
    61: "🌧 небольшой дождь",
    63: "🌧 дождь",
    65: "🌧 сильный дождь",
    66: "🌧 ледяной дождь",
    67: "🌧 ледяной дождь",
    71: "🌨 небольшой снег",
    73: "🌨 снег",
    75: "❄️ сильный снег",
    77: "❄️ снежная крупа",
    80: "🌦 ливень",
    81: "🌧 ливень",
    82: "⛈ сильный ливень",
    85: "🌨 снежный ливень",
    86: "❄️ сильный снежный ливень",
    95: "⛈ гроза",
    96: "⛈ гроза с градом",
    99: "⛈ сильная гроза с градом",
}


def describe_weather_code(code: int | None) -> str:
    if code is None:
        return "—"
    return _WEATHER_CODE_RU.get(code, "—")


def _geocode_city(city: str) -> tuple[float, float] | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT lat, lon FROM city_geocode WHERE city = ?", (city,)).fetchone()
        if row:
            return row["lat"], row["lon"]
    finally:
        conn.close()

    try:
        resp = httpx.get(_GEOCODE_URL, params={"name": city, "count": 1, "language": "ru"}, timeout=_TIMEOUT)
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except Exception:
        return None
    if not results:
        return None
    lat, lon = results[0]["latitude"], results[0]["longitude"]

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO city_geocode (city, lat, lon) VALUES (?, ?, ?) ON CONFLICT (city) DO NOTHING",
            (city, lat, lon),
        )
        conn.commit()
    finally:
        conn.close()
    return lat, lon


def get_weather_range(city: str, start_date: str, end_date: str) -> dict[str, dict]:
    """{report_date: {"temp_avg_c", "precipitation_mm", "weather_code"}} за
    диапазон дат включительно. Пропущенные/недоступные дни просто
    отсутствуют в результате — вызывающий код (dashboard/data.py) сам
    решает, как показать пробел."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM weather_cache WHERE city = ? AND report_date BETWEEN ? AND ?",
            (city, start_date, end_date),
        ).fetchall()
        result = {row["report_date"]: dict(row) for row in rows}
    finally:
        conn.close()

    all_dates = _date_range(start_date, end_date)
    missing = [d for d in all_dates if d not in result]
    if not missing:
        return result

    coords = _geocode_city(city)
    if not coords:
        return result
    lat, lon = coords

    try:
        resp = httpx.get(
            _ARCHIVE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": min(missing),
                "end_date": max(missing),
                "daily": "temperature_2m_mean,precipitation_sum,weathercode",
                "timezone": "auto",
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        daily = resp.json().get("daily") or {}
    except Exception:
        return result

    dates = daily.get("time") or []
    temps = daily.get("temperature_2m_mean") or []
    precs = daily.get("precipitation_sum") or []
    codes = daily.get("weathercode") or []

    conn = get_connection()
    try:
        for i, day in enumerate(dates):
            temp = temps[i] if i < len(temps) else None
            prec = precs[i] if i < len(precs) else None
            code = codes[i] if i < len(codes) else None
            conn.execute(
                """
                INSERT INTO weather_cache (city, report_date, temp_avg_c, precipitation_mm, weather_code)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (city, report_date) DO UPDATE SET
                    temp_avg_c = excluded.temp_avg_c,
                    precipitation_mm = excluded.precipitation_mm,
                    weather_code = excluded.weather_code
                """,
                (city, day, temp, prec, code),
            )
            if day in all_dates:
                result[day] = {
                    "city": city,
                    "report_date": day,
                    "temp_avg_c": temp,
                    "precipitation_mm": prec,
                    "weather_code": code,
                }
        conn.commit()
    finally:
        conn.close()

    return result


def _date_range(start_date: str, end_date: str) -> list[str]:
    from datetime import date, timedelta

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    days = []
    current = start
    while current <= end:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days
