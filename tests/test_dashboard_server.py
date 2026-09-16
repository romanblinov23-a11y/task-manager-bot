import asyncio
import json
from datetime import date
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from dashboard.server import _build_app, get_or_create_dashboard_token
from monitoring.db import get_connection
from monitoring.markets import create_market


def _seed_report(market_id: int, report_date: str, data: dict) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO shift_report (market_id, report_date, status, data) VALUES (?, ?, 'dispatched', ?)",
            (market_id, report_date, json.dumps(data, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def _run(coro):
    return asyncio.run(coro)


def test_dashboard_rejects_wrong_token():
    async def scenario():
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get("/dashboard", params={"token": "wrong"})
            assert resp.status == 403
            text = await resp.text()
            assert "Доступ запрещён" in text

    _run(scenario())


def test_dashboard_rejects_missing_token():
    async def scenario():
        async with TestClient(TestServer(_build_app())) as client:
            resp = await client.get("/dashboard")
            assert resp.status == 403

    _run(scenario())


def test_dashboard_renders_with_correct_token():
    market = create_market("Тестовая точка")
    _seed_report(market["id"], date.today().isoformat(), {"revenue_total": "12345"})
    token = get_or_create_dashboard_token()

    async def scenario():
        with patch("dashboard.server.fetch_dashboard_series") as mock_fetch:
            mock_fetch.return_value = [
                {
                    "report_date": date.today().isoformat(),
                    "market_id": market["id"],
                    "market_name": market["name"],
                    "revenue": 12345.0,
                    "avg_check": None,
                    "guests": None,
                    "staff_hours": None,
                    "spmh": None,
                    "writeoff_expiry": None,
                    "writeoff_compliment": None,
                    "writeoff_staff_meals": None,
                    "writeoff_total": None,
                    "writeoff_total_plan": None,
                    "avg_service_minutes": None,
                    "temp_avg_c": None,
                    "precipitation_mm": None,
                    "weather_code": None,
                    "events": "",
                }
            ]
            async with TestClient(TestServer(_build_app())) as client:
                resp = await client.get(
                    "/dashboard", params={"token": token, "market_id": str(market["id"]), "days": "7"}
                )
                assert resp.status == 200
                text = await resp.text()
                assert "12 345" in text or "12345" in text
                assert "Дашборд" in text

    _run(scenario())


def test_token_persists_across_calls():
    token1 = get_or_create_dashboard_token()
    token2 = get_or_create_dashboard_token()
    assert token1 == token2
    assert len(token1) > 20
