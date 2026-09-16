"""Сборка данных для дашборда (см. dashboard/server.py) — превращает
отчёты по сменам за период в ряд чисел по дням + погоду, и считает
корреляции между ними. Чистая логика, без HTTP и без Telegram."""

import calendar
import re
from datetime import date, timedelta

from dashboard.weather import get_weather_range
from monitoring.markets import list_markets
from monitoring.shift_reports import list_reports_in_range
from monitoring.writeoff_plan import get_writeoff_plan, writeoff_plan_amounts

_MONTH_NAMES_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]

PERIODS = ("week", "month", "quarter")


def resolve_period(period: str, offset: int) -> tuple[str, str, str]:
    """(start_date, end_date, label) для периода на дашборде.
    'week' — календарная неделя пн-вс, 'month' — календарный месяц, оба
    навигируются offset'ом (0 — текущий, -1 — предыдущий и т.д., в будущее
    уйти нельзя). 'quarter' — скользящие 90 дней от сегодня, без
    навигации (offset игнорируется) — просто длинный тренд."""
    today = date.today()
    offset = min(offset, 0)

    if period == "month":
        month0 = today.month - 1 + offset
        year = today.year + month0 // 12
        month = month0 % 12 + 1
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
        label = f"{_MONTH_NAMES_RU[month]} {year}"
    elif period == "week":
        monday = today - timedelta(days=today.weekday())
        start = monday + timedelta(weeks=offset)
        end = start + timedelta(days=6)
        label = f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}"
    else:
        start = today - timedelta(days=89)
        end = today
        label = "последние 90 дней"

    end = min(end, today)
    return start.isoformat(), end.isoformat(), label


METRIC_LABELS = {
    "revenue": "Выручка",
    "avg_check": "Средний чек",
    "guests": "Гости",
    "spmh": "SPMH",
    "writeoff_total": "Списания",
    "temp_avg_c": "Температура",
    "precipitation_mm": "Осадки",
}
CORRELATION_METRICS = list(METRIC_LABELS.keys())


def _parse_amount(text: str) -> float | None:
    cleaned = re.sub(r"\s", "", text or "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return value if value >= 0 else None


def _parse_int(text: str) -> int | None:
    cleaned = re.sub(r"\s", "", text or "")
    return int(cleaned) if cleaned.isdigit() else None


def _parse_service_minutes(text: str) -> float | None:
    """"5:35" -> 5.58 минут. Формат тот же, что в анкете (bot/shift_reports.py)."""
    match = re.match(r"^(\d+):(\d{2})$", (text or "").strip())
    if not match:
        return None
    minutes, seconds = int(match.group(1)), int(match.group(2))
    return minutes + seconds / 60


def fetch_dashboard_series(market_id: int | None, start_iso: str, end_iso: str) -> list[dict]:
    """Один ряд на день (или несколько рядов, если market_id is None — все
    точки, каждая строка помечена своим market_id/market_name). Каждая
    строка: report_date, market_id, market_name, revenue, avg_check,
    guests, staff_hours, spmh, writeoff_expiry/compliment/staff_meals/total
    (+ *_plan, если норма задана), avg_service_minutes, temp_avg_c,
    precipitation_mm, weather_code, events (текст, если не пустой/«не было»).
    Границы периода (start_iso/end_iso) считает resolve_period."""
    markets = list_markets()
    if market_id is not None:
        markets = [m for m in markets if m["id"] == market_id]

    rows: list[dict] = []
    for market in markets:
        reports = list_reports_in_range(market["id"], start_iso, end_iso)
        if not reports:
            continue
        city = market.get("city") or _default_city()
        weather_by_date = get_weather_range(city, start_iso, end_iso)
        writeoff_plan = get_writeoff_plan(market["id"])

        for report in reports:
            data = report["data"]
            revenue = _parse_amount(data.get("revenue_total", ""))
            hours = _parse_amount(data.get("staff_hours", ""))
            spmh = revenue / hours if revenue is not None and hours else None

            expiry = _parse_amount(data.get("writeoff_expiry", ""))
            compliment = _parse_amount(data.get("writeoff_compliment", ""))
            staff_meals = _parse_amount(data.get("writeoff_staff_meals", ""))
            writeoff_total = sum(v for v in (expiry, compliment, staff_meals) if v is not None) or None

            writeoff_total_plan = None
            if writeoff_plan and revenue is not None:
                amounts = writeoff_plan_amounts(writeoff_plan, revenue)
                writeoff_total_plan = amounts["expiry"] + amounts["compliment"] + amounts["staff_meals"]

            weather = weather_by_date.get(report["report_date"], {})

            events_text = (data.get("comment_events") or "").strip()
            if events_text.lower() in ("", "не было", "нет"):
                events_text = ""

            rows.append(
                {
                    "report_date": report["report_date"],
                    "market_id": market["id"],
                    "market_name": market["name"],
                    "revenue": revenue,
                    "avg_check": _parse_amount(data.get("avg_check", "")),
                    "guests": _parse_int(data.get("guests", "")),
                    "staff_hours": hours,
                    "spmh": spmh,
                    "writeoff_expiry": expiry,
                    "writeoff_compliment": compliment,
                    "writeoff_staff_meals": staff_meals,
                    "writeoff_total": writeoff_total,
                    "writeoff_total_plan": writeoff_total_plan,
                    "avg_service_minutes": _parse_service_minutes(data.get("avg_service_time", "")),
                    "temp_avg_c": weather.get("temp_avg_c"),
                    "precipitation_mm": weather.get("precipitation_mm"),
                    "weather_code": weather.get("weather_code"),
                    "events": events_text,
                }
            )

    rows.sort(key=lambda r: (r["report_date"], r["market_name"]))
    return rows


def _sum_or_none(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def _avg_or_none(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def aggregate_series(rows: list[dict]) -> dict:
    """Итоги по периоду — общие и для KPI-плиток (dashboard/html.py), и
    для промпта Клоду (dashboard/commentary.py), чтобы считать их
    одинаково в обоих местах."""
    return {
        "days_count": len({r["report_date"] for r in rows}),
        "revenue_total": _sum_or_none([r["revenue"] for r in rows]),
        "avg_check_avg": _avg_or_none([r["avg_check"] for r in rows]),
        "guests_total": _sum_or_none([r["guests"] for r in rows]),
        "spmh_avg": _avg_or_none([r["spmh"] for r in rows]),
        "writeoff_total": _sum_or_none([r["writeoff_total"] for r in rows]),
        "writeoff_total_plan": _sum_or_none([r["writeoff_total_plan"] for r in rows]),
        "temp_avg": _avg_or_none([r["temp_avg_c"] for r in rows]),
        "rain_days": sum(1 for r in rows if (r.get("precipitation_mm") or 0) > 0),
    }


def daily_revenue_totals(rows: list[dict]) -> dict[str, float]:
    """Выручка по дате, просуммированная по всем точкам в rows — для
    поиска лучшего/худшего дня периода."""
    totals: dict[str, float] = {}
    for r in rows:
        if r["revenue"] is None:
            continue
        totals[r["report_date"]] = totals.get(r["report_date"], 0) + r["revenue"]
    return totals


def _default_city() -> str:
    from config.settings import DEFAULT_WEATHER_CITY

    return DEFAULT_WEATHER_CITY


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Коэффициент корреляции Пирсона по параллельным спискам, пары со
    значением None пропускаются. None, если после этого пар меньше 3 или
    дисперсия одного из рядов нулевая (корреляция не определена)."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    n = len(pairs)
    xs_clean = [p[0] for p in pairs]
    ys_clean = [p[1] for p in pairs]
    mean_x = sum(xs_clean) / n
    mean_y = sum(ys_clean) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    var_x = sum((x - mean_x) ** 2 for x in xs_clean)
    var_y = sum((y - mean_y) ** 2 for y in ys_clean)
    if var_x == 0 or var_y == 0:
        return None
    return cov / (var_x**0.5 * var_y**0.5)


def build_correlation_matrix(rows: list[dict], metric_keys: list[str] | None = None) -> dict[tuple[str, str], float | None]:
    keys = metric_keys or CORRELATION_METRICS
    matrix: dict[tuple[str, str], float | None] = {}
    for i, key_a in enumerate(keys):
        for key_b in keys[i + 1 :]:
            xs = [r.get(key_a) for r in rows]
            ys = [r.get(key_b) for r in rows]
            matrix[(key_a, key_b)] = pearson(xs, ys)
    return matrix


def describe_correlation(r: float | None) -> str:
    if r is None:
        return "недостаточно данных"
    strength = abs(r)
    if strength < 0.2:
        label = "почти нет связи"
    elif strength < 0.4:
        label = "слабая связь"
    elif strength < 0.6:
        label = "заметная связь"
    elif strength < 0.8:
        label = "сильная связь"
    else:
        label = "очень сильная связь"
    direction = "прямая" if r >= 0 else "обратная"
    return f"{label} ({direction}, r={r:.2f})"
