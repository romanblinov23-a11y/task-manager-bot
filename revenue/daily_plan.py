"""
План на конкретный день для конкретного рынка Енисея. Для рынков,
подключённых к Surf Coffee (см. revenue.market_mapping, /add_project) —
забирается напрямую из системы учёта, вносить его вручную не нужно. Для
остальных — используется план, который Управляющий загрузил сам через
/set_monthly_plan (см. monitoring.monthly_plan)."""

import logging

from config.settings import SURF_EMAIL, SURF_PASSWORD
from monitoring.monthly_plan import get_daily_plan
from revenue.market_mapping import spot_key_for_market
from revenue.plan_report import get_spot_plan
from revenue.surfcoffee_client import SurfCoffeeClient

logger = logging.getLogger("revenue.daily_plan")


def _fetch_from_surfcoffee(spot_key: str, date_iso: str) -> dict | None:
    client = SurfCoffeeClient(SURF_EMAIL, SURF_PASSWORD)
    try:
        client.login()
        rows = get_spot_plan(client, spot_key, date_iso[:7])
        row = next((r for r in rows if r.date.isoformat() == date_iso), None)
        if not row:
            return None
        return {"revenue_plan": row.revenue_plan, "checks_plan": row.count_plan}
    except Exception as e:
        logger.warning("Не удалось получить план на %s из Surf Coffee (spot=%s): %s", date_iso, spot_key, e)
        return None
    finally:
        client.close()


def fetch_daily_plan(market_id: int, date_iso: str) -> dict | None:
    """{"revenue_plan": float, "checks_plan": int | None} на конкретный
    день, или None — если план не найден ни одним из способов. Для
    рынков без привязки к Surf Coffee просто читает ручной план из базы
    (быстро, без сети); для подключённых — делает сетевой запрос, поэтому
    вызывающий асинхронный код должен обернуть вызов в asyncio.to_thread
    (см. bot/shift_reports.py) на случай сетевого пути."""
    spot_key = spot_key_for_market(market_id)
    if spot_key:
        return _fetch_from_surfcoffee(spot_key, date_iso)
    return get_daily_plan(market_id, date_iso)
