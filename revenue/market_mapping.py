"""
Мост между рынками Енисея (monitoring.markets, свои auto-increment id) и
точками Surf Coffee в модуле revenue/ (свои ключи spot_id, см.
surfcoffee_client.SPOTS) — это два независимых ID-пространства. Связь
хранится явно на самом рынке (market.surf_spot_key, см.
monitoring.markets.set_market_surf_spot_key) — задаётся один раз при
/add_project, а не выводится из имени, чтобы переименование рынка не
рвало привязку и наоборот. Используется в bot/accounting_flow.py и
revenue/daily_plan.py, чтобы понять, какую точку в системе учёта
показывать/тянуть для конкретного рынка.
"""

from monitoring.markets import get_market, list_markets
from revenue.surfcoffee_client import SPOTS


def spot_key_for_market(market_id: int) -> str | None:
    """None — если рынок не подключён к системе учёта Surf Coffee (план
    для него вносится вручную, см. /set_monthly_plan)."""
    market = get_market(market_id)
    if not market:
        return None
    return market.get("surf_spot_key") or None


def assigned_spot_keys() -> set[str]:
    """Ключи Surf Coffee, уже занятые каким-то рынком — чтобы при
    /add_project не привязать два рынка к одной и той же точке учёта."""
    return {m["surf_spot_key"] for m in list_markets() if m.get("surf_spot_key")}


def available_spot_keys() -> list[str]:
    """Ключи Surf Coffee, которые ещё можно привязать к новому рынку."""
    taken = assigned_spot_keys()
    return [key for key in SPOTS if key not in taken]
