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
from portfolio import storage as portfolio_storage
from portfolio import service as portfolio_service
from config.timeutil import today as tz_today

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



async def _portfolio_authorized(request):
    token=request.headers.get('Authorization','').removeprefix('Bearer ') or request.query.get('token','')
    expected=await asyncio.to_thread(portfolio_storage.token)
    return bool(token and secrets.compare_digest(token,expected))


def _private_headers():
    return {'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"}


async def _handle_portfolio(request):
    if not await _portfolio_authorized(request):return web.Response(text=render_forbidden_page(),content_type='text/html',status=403,headers=_private_headers())
    html=await asyncio.to_thread((_STATIC_DIR/'portfolio.html').read_text,encoding='utf-8')
    return web.Response(text=html,content_type='text/html',headers=_private_headers())


async def _handle_portfolio_api(request):
    if not await _portfolio_authorized(request):return web.json_response({'error':'Ссылка недействительна. Открой портфель через бота.'},status=403,headers=_private_headers())
    month=request.query.get('month',tz_today().strftime('%Y-%m'))
    if not portfolio_service.valid_month(month) or month>tz_today().strftime('%Y-%m'):
        return web.json_response({'error':'Выбери существующий месяц, не позднее текущего.'},status=400,headers=_private_headers())
    refresh=request.query.get('refresh')=='1'
    try:
        mid=request.query.get('market_id')
        if mid:
            if not mid.isdigit():raise ValueError('Проект не найден')
            market=next((m for m in await asyncio.to_thread(list_markets) if m['id']==int(mid) and m.get('surf_spot_key')),None)
            if not market:raise ValueError('Проект не найден')
            value=await asyncio.to_thread(portfolio_service.request_project,market,month,refresh)
        else:value=await asyncio.to_thread(portfolio_service.request_portfolio,month,refresh)
        return web.json_response(value,headers=_private_headers())
    except ValueError as e:return web.json_response({'error':str(e)},status=400,headers=_private_headers())
    except Exception:
        logger.exception('Не удалось обновить портфель')
        return web.json_response({'error':'Данные временно недоступны. Попробуй обновить позже.'},status=503,headers=_private_headers())


async def _handle_portfolio_meta(request):
    if not await _portfolio_authorized(request):return web.json_response({'error':'Нет доступа'},status=403,headers=_private_headers())
    markets=await asyncio.to_thread(list_markets)
    result=[]
    for m in markets:
        if m.get('surf_spot_key'):result.append({'id':m['id'],'name':m['name'],'share':await asyncio.to_thread(portfolio_storage.ownership,m)})
    return web.json_response({'projects':result,'current_month':tz_today().strftime('%Y-%m')},headers=_private_headers())


def _build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/dashboard", _handle_dashboard)
    app.router.add_get("/portfolio", _handle_portfolio)
    app.router.add_get("/portfolio/api", _handle_portfolio_api)
    app.router.add_get("/portfolio/meta", _handle_portfolio_meta)
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
            runner = web.AppRunner(app, access_log=None)
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, "0.0.0.0", port)
            loop.run_until_complete(site.start())
            logger.info("Dashboard server listening on port %s", port)
            loop.run_forever()
        except Exception:
            logger.exception("Dashboard server failed to start")

    thread = threading.Thread(target=_run, name="dashboard-server", daemon=True)
    thread.start()
