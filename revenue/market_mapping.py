"""
Мост между рынками Енисея (monitoring.markets, свои auto-increment id) и
точками Surf Coffee в модуле revenue/ (свои ключи spot_id, см.
surfcoffee_client.SPOTS) — это два независимых ID-пространства, общее
только имя. Используется в bot/accounting_flow.py, чтобы по рынку
Управляющего понять, какую точку в системе учёта ему показывать.
"""

from monitoring.markets import get_market

# Имя рынка в Енисее -> ключ точки в revenue.surfcoffee_client.SPOTS.
# Если Роман добавит новый рынок, которого нет в системе учёта Surf
# Coffee (или наоборот, переименует существующий) — просто обновить этот
# словарь, больше никаких изменений не требуется.
MARKET_NAME_TO_SPOT_KEY = {
    "Парк Горького": "park_gorkogo",
    "Окко": "okko",
    "Yandex": "yandex",
}


def spot_key_for_market(market_id: int) -> str | None:
    """None — если рынок ещё не подключён к системе учёта Surf Coffee
    (не заведён в MARKET_NAME_TO_SPOT_KEY)."""
    market = get_market(market_id)
    if not market:
        return None
    return MARKET_NAME_TO_SPOT_KEY.get(market["name"])
