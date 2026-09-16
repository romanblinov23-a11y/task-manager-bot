"""Веб-сервер дашборда — отдельный aiohttp-процесс внутри того же
контейнера, что и Telegram-бот, но с собственным потоком/event loop
(см. start_dashboard_server), чтобы не трогать жизненный цикл PTB и его
блокирующий app.run_polling(). Единственный маршрут — сама страница
дашборда, защищённая общим токеном (см. get_or_create_dashboard_token)."""

import asyncio
import logging
import secrets
import threading
from pathlib import Path

from aiohttp import web

from dashboard.commentary import generate_commentary
from dashboard.data import PERIODS, build_correlation_matrix, fetch_dashboard_series, resolve_period
from dashboard.html import render_dashboard_page, render_forbidden_page
from monitoring.db import get_connection
from monitoring.markets import list_markets

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


def get_or_create_dashboard_token() -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT token FROM dashboard_secret WHERE id = 1").fetchone()
        if row:
            return row["token"]
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO dashboard_secret (id, token) VALUES (1, ?)", (token,))
        conn.commit()
        return token
    finally:
        conn.close()


async def _handle_dashboard(request: web.Request) -> web.Response:
    expected_token = await asyncio.to_thread(get_or_create_dashboard_token)
    got_token = request.query.get("token", "")
    if not got_token or not secrets.compare_digest(got_token, expected_token):
        return web.Response(text=render_forbidden_page(), content_type="text/html", status=403)

    period = request.query.get("period", "week")
    if period not in PERIODS:
        period = "week"
    try:
        offset = int(request.query.get("offset", "0"))
    except ValueError:
        offset = 0

    market_id_raw = request.query.get("market_id")
    market_id = int(market_id_raw) if market_id_raw and market_id_raw.isdigit() else None

    start_iso, end_iso, label = resolve_period(period, offset)
    markets = await asyncio.to_thread(list_markets)
    rows = await asyncio.to_thread(fetch_dashboard_series, market_id, start_iso, end_iso)
    correlation_matrix = await asyncio.to_thread(build_correlation_matrix, rows)

    prev_rows = []
    if period in ("week", "month"):
        prev_start, prev_end, _ = resolve_period(period, offset - 1)
        prev_rows = await asyncio.to_thread(fetch_dashboard_series, market_id, prev_start, prev_end)

    scope_label = next((m["name"] for m in markets if m["id"] == market_id), "Все точки") if market_id else "Все точки"
    commentary = await asyncio.to_thread(generate_commentary, scope_label, label, rows, prev_rows, correlation_matrix)

    html = render_dashboard_page(markets, market_id, period, offset, label, rows, correlation_matrix, commentary, expected_token)
    return web.Response(text=html, content_type="text/html")


def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/dashboard", _handle_dashboard)
    app.router.add_static("/dashboard/static/", _STATIC_DIR)
    return app


def start_dashboard_server(port: int) -> None:
    """Поднимает aiohttp-сервер в отдельном daemon-потоке со своим циклом
    asyncio — полностью независимо от цикла python-telegram-bot, который
    занят блокирующим app.run_polling() в main(). Поток умирает вместе с
    процессом, отдельное грациозное выключение не нужно — страница только
    читает данные."""

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            app = _build_app()
            runner = web.AppRunner(app)
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, "0.0.0.0", port)
            loop.run_until_complete(site.start())
            logger.info("Dashboard server listening on port %s", port)
            loop.run_forever()
        except Exception:
            logger.exception("Dashboard server failed to start")

    thread = threading.Thread(target=_run, name="dashboard-server", daemon=True)
    thread.start()
