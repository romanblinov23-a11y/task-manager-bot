"""
План на конкретный день для конкретного рынка Енисея — забирается из
Surf Coffee (НИМБ) напрямую, а не хранится в базе: Управляющим больше не
нужно вручную загружать план по выручке и чекам (см. /set_monthly_plan,
удалено) — бот сам знает актуальный план по тем точкам, что подключены к
системе учёта (см. revenue.market_mapping)."""

import logging

from config.settings import SURF_EMAIL, SURF_PASSWORD
from revenue.market_mapping import spot_key_for_market
from revenue.plan_report import get_spot_plan
from revenue.surfcoffee_client import SurfCoffeeClient

logger = logging.getLogger("revenue.daily_plan")


def fetch_daily_plan(market_id: int, date_iso: str) -> dict | None:
    """{"revenue_plan": float, "checks_plan": int | None} на конкретный
    день, или None — если рынок не подключён к системе учёта Surf Coffee,
    или запрос не удался (сеть, авторизация и т.п.). В обоих случаях
    вызывающий код просто не показывает план в отчёте — то же поведение,
    что раньше было для рынка с незагруженным планом. Синхронная функция —
    делает сетевой запрос, вызывающий асинхронный код должен обернуть
    вызов в asyncio.to_thread (см. bot/shift_reports.py)."""
    spot_key = spot_key_for_market(market_id)
    if not spot_key:
        return None
    client = SurfCoffeeClient(SURF_EMAIL, SURF_PASSWORD)
    try:
        client.login()
        rows = get_spot_plan(client, spot_key, date_iso[:7])
        row = next((r for r in rows if r.date.isoformat() == date_iso), None)
        if not row:
            return None
        return {"revenue_plan": row.revenue_plan, "checks_plan": row.count_plan}
    except Exception as e:
        logger.warning("Не удалось получить план на %s для рынка %s: %s", date_iso, market_id, e)
        return None
    finally:
        client.close()
