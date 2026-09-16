import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from dashboard.data import build_correlation_matrix, describe_correlation, fetch_dashboard_series, pearson
from monitoring.db import get_connection
from monitoring.markets import create_market
from monitoring.writeoff_plan import set_writeoff_plan


def _seed_report(market_id: int, report_date: str, data: dict, status: str = "dispatched") -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO shift_report (market_id, report_date, status, data) VALUES (?, ?, ?, ?)",
            (market_id, report_date, status, json.dumps(data, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def test_pearson_perfect_positive_correlation():
    xs = [1, 2, 3, 4, 5]
    ys = [10, 20, 30, 40, 50]
    assert pearson(xs, ys) == 1.0


def test_pearson_perfect_negative_correlation():
    xs = [1, 2, 3, 4, 5]
    ys = [50, 40, 30, 20, 10]
    assert pearson(xs, ys) == -1.0


def test_pearson_skips_none_pairs():
    xs = [1, 2, None, 4, 5]
    ys = [10, 20, 999, 40, 50]
    assert pearson(xs, ys) == 1.0


def test_pearson_none_when_too_few_pairs():
    assert pearson([1, 2], [1, 2]) is None


def test_pearson_none_on_zero_variance():
    assert pearson([1, 1, 1], [1, 2, 3]) is None


def test_describe_correlation_labels():
    assert "недостаточно" in describe_correlation(None)
    assert "прямая" in describe_correlation(0.9)
    assert "обратная" in describe_correlation(-0.9)


def test_build_correlation_matrix_only_uses_given_metrics():
    rows = [{"a": i, "b": i * 2} for i in range(5)]
    matrix = build_correlation_matrix(rows, ["a", "b"])
    assert matrix[("a", "b")] == pytest.approx(1.0)


def test_fetch_dashboard_series_computes_spmh_and_writeoffs():
    market = create_market("Тестовая точка", city="Москва")
    today = date.today()
    set_writeoff_plan(market["id"], expiry_pct=1.0, compliment_pct=0.5, staff_meals_pct=0.5)
    _seed_report(
        market["id"],
        today.isoformat(),
        {
            "revenue_total": "100000",
            "avg_check": "500",
            "guests": "200",
            "staff_hours": "20",
            "writeoff_expiry": "1000",
            "writeoff_compliment": "500",
            "writeoff_staff_meals": "500",
            "comment_events": "не было",
        },
    )

    with patch("dashboard.data.get_weather_range", return_value={}):
        rows = fetch_dashboard_series(market["id"], days=7)

    assert len(rows) == 1
    row = rows[0]
    assert row["spmh"] == 5000.0
    assert row["writeoff_total"] == 2000.0
    assert row["writeoff_total_plan"] == 2000.0  # 100000 * (1+0.5+0.5)/100
    assert row["events"] == ""  # "не было" отфильтровано


def test_fetch_dashboard_series_ignores_drafts_and_old_dates():
    market = create_market("Тестовая точка 2")
    today = date.today()
    old_date = (today - timedelta(days=40)).isoformat()
    _seed_report(market["id"], today.isoformat(), {"revenue_total": "1000"}, status="collecting")
    _seed_report(market["id"], old_date, {"revenue_total": "2000"})

    with patch("dashboard.data.get_weather_range", return_value={}):
        rows = fetch_dashboard_series(market["id"], days=30)

    assert rows == []


def test_fetch_dashboard_series_all_markets_when_market_id_none():
    m1 = create_market("Точка А")
    m2 = create_market("Точка Б")
    today = date.today().isoformat()
    _seed_report(m1["id"], today, {"revenue_total": "1000"})
    _seed_report(m2["id"], today, {"revenue_total": "2000"})

    with patch("dashboard.data.get_weather_range", return_value={}):
        rows = fetch_dashboard_series(None, days=7)

    assert {r["market_name"] for r in rows} == {"Точка А", "Точка Б"}
