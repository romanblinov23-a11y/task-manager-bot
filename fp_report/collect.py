"""Сбор входов для ежемесячного отчёта ФП из НИМБ (Surf Coffee).

Синхронный вызов — вызывающий код оборачивает в asyncio.to_thread. Каждый метод клиента
сам переключает точку и проверяет, что НИМБ вернул нужный месяц (см. revenue.surfcoffee_client).
"""

from config.settings import SURF_EMAIL, SURF_PASSWORD
from revenue.surfcoffee_client import SurfCoffeeClient


def fetch_nimba_inputs(spot_key: str, month: str) -> dict:
    """month — 'YYYY-MM'. Возвращает ключи, которые ждёт fp_report.build.build_report."""
    client = SurfCoffeeClient(SURF_EMAIL, SURF_PASSWORD)
    try:
        client.login()
        return {
            "pnl": client.get_pnl_year(spot_key, int(month[:4])),
            "manager": client.get_manager_report(spot_key, month),
            "vozn": client.get_awards_shifts(spot_key, month),
            "komanda": client.get_team(spot_key),
            "shtat": client.get_staff_norm(spot_key),
            "grafik": client.get_timetable(spot_key, month),
        }
    finally:
        client.close()
