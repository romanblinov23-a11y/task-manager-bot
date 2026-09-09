"""
Клиент для работы с внутренним API Surf Coffee (surfis.surfcoffee.ru).

Перенесено из отдельного бота "Аналитик Иван" (~/Documents/ivan-analyst-bot)
почти без изменений — это уже рабочий, проверенный код.

Логика:
- Логинимся один раз, получаем JWT-токен и кладём его в заголовок Authorization
- Переключаемся между точками через GET /user/changeSpot?spot_id=...
- Получаем дневные/месячные данные через POST /dashboard/v2/get

ВАЖНО: это неофициальный API, найденный через анализ браузерных запросов.
Если Surf Coffee изменит структуру своего сайта, эндпоинты могут перестать работать.

Синхронный клиент (обычный httpx.Client, не async) — вызывающий код в
bot/revenue_flow.py и джобы в main.py оборачивают обращения к нему в
asyncio.to_thread, чтобы не блокировать event loop бота.
"""

import time

import httpx
import logging
from datetime import date
from dataclasses import dataclass

logger = logging.getLogger("revenue.surfcoffee_client")

BASE_URL = "https://surfis.surfcoffee.ru"
LOGIN_URL = f"{BASE_URL}/api/web/v1/login"
CHANGE_SPOT_URL = f"{BASE_URL}/api/v3/user/changeSpot"
DASHBOARD_V2_URL = f"{BASE_URL}/api/v3/dashboard/v2/get"
PNL_URL = f"{BASE_URL}/api/v3/reports/pnl/get"

# Известные точки сети (ключ -> spot_id Surf Coffee). Ключи внутренние, к
# именам рынков в monitoring.markets не привязаны — "yandex" здесь и
# рынок "Yandex" там совпадают по смыслу, но это разные ID-пространства.
SPOTS = {
    "yandex": {"id": 71, "title": "Surf Coffee x Yandex"},
    "okko": {"id": 97, "title": "Surf Coffee x OKKO"},
    "park_gorkogo": {"id": 392, "title": "Surf Coffee x Park Gorkogo"},
}


@dataclass
class DayData:
    """Данные по одному дню из таблицы 'Анализ продаж'."""
    date: date
    plan_revenue: float
    fact_revenue: float
    percent: float  # отклонение факт/план, %
    nonfisc_revenue: float
    count_fact: int | None = None
    average_fact: float | None = None


@dataclass
class MonthSummary:
    """Агрегат по месяцу (из results)."""
    plan_sum: float
    fact_sum: float
    percent: float
    nonfisc_sum: float
    receipts_count_plan: int
    receipts_count_fact: int
    receipts_average_plan: float
    receipts_average_fact: float


class SurfCoffeeClient:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._client = httpx.Client(
            base_url=BASE_URL,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; EniseyRevenueModule/1.0)",
            },
            timeout=30.0,
        )
        self._logged_in = False

    def _with_retry(self, fn) -> httpx.Response:
        """
        Вызывает fn() (lambda без аргументов, возвращающая httpx.Response).
        При 5xx повторяет до 3 раз с паузами 5с и 15с.
        4xx и успешные ответы возвращаются сразу.
        """
        delays = [5, 15]
        for attempt, delay in enumerate(delays, start=1):
            resp = fn()
            if resp.status_code < 500:
                return resp
            logger.warning(
                "Surf Coffee API вернул %s, повтор через %dс (попытка %d/3)",
                resp.status_code, delay, attempt,
            )
            time.sleep(delay)
        # Последняя попытка — не перехватываем, пусть raise_for_status сам сообщит об ошибке
        return fn()

    def login(self) -> None:
        """Логинимся и сохраняем JWT-токен для дальнейших запросов."""
        logger.info("Логинимся в Surf Coffee как %s", self.email)
        resp = self._with_retry(
            lambda: self._client.post(LOGIN_URL, json={"email": self.email, "password": self.password})
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success", True):
            raise RuntimeError(f"Login failed: {data}")

        token = data["data"]["token"]
        # Все последующие запросы должны нести этот токен в заголовке Authorization
        self._client.headers["Authorization"] = f"Bearer {token}"

        self._logged_in = True
        logger.info("Успешный логин, токен сохранён")

    def _ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def switch_spot(self, spot_id: int) -> None:
        """Переключает активную точку в текущей сессии."""
        self._ensure_login()
        resp = self._with_retry(lambda: self._client.get(CHANGE_SPOT_URL, params={"spot_id": spot_id}))
        resp.raise_for_status()
        logger.info("Переключились на spot_id=%s", spot_id)

    def get_month_raw(self, month: str) -> dict:
        """
        Возвращает сырой JSON по месяцу для ТЕКУЩЕЙ активной точки.
        month в формате 'YYYY-MM', например '2026-06'.
        Перед вызовом обязательно сделать switch_spot().
        """
        self._ensure_login()
        resp = self._with_retry(lambda: self._client.post(DASHBOARD_V2_URL, json={"month": month}))
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"dashboard/v2/get failed: {data}")
        return data["data"]

    def get_month_days(self, spot_key: str, month: str) -> list[DayData]:
        """
        Возвращает список DayData по точке spot_key за месяц month.
        spot_key — один из ключей SPOTS ('yandex', 'okko', 'park_gorkogo').
        Дни с нулевой фактической выручкой и датой в будущем отбрасываются.
        """
        spot_id = SPOTS[spot_key]["id"]
        self.switch_spot(spot_id)
        raw = self.get_month_raw(month)

        revenue_table = raw["table"]["revenue"]["data"]  # [{date, planning_revenue, fact_revenue, percent, ...}]
        by_period = raw["by_period"]["data"]  # {average: [...], count: [...], revenue: [...]}
        period_dates = raw["by_period"]["period"]  # ["01.06.2026", ...]

        # индекс дата(строка) -> позиция в массивах by_period
        date_to_idx = {d: i for i, d in enumerate(period_dates)}

        today = date.today()
        days: list[DayData] = []

        for row in revenue_table:
            # row["date"] выглядит как "2026-06-01 00:00:00"
            row_date = date.fromisoformat(row["date"].split(" ")[0])
            if row_date > today:
                continue  # будущий день, данных ещё нет

            date_str_short = row_date.strftime("%d.%m.%Y")
            idx = date_to_idx.get(date_str_short)

            count_fact = None
            average_fact = None
            if idx is not None:
                count_fact = by_period["count"][idx]
                average_fact = by_period["average"][idx]

            days.append(
                DayData(
                    date=row_date,
                    plan_revenue=row.get("planning_revenue", 0) or 0,
                    fact_revenue=row.get("fact_revenue", 0) or 0,
                    percent=row.get("percent", 0) or 0,
                    nonfisc_revenue=row.get("nonfiscRevenue", 0) or 0,
                    count_fact=count_fact,
                    average_fact=average_fact,
                )
            )

        return days

    def get_month_summary(self, spot_key: str, month: str) -> MonthSummary:
        """Возвращает агрегированную сводку (results) по точке за месяц."""
        spot_id = SPOTS[spot_key]["id"]
        self.switch_spot(spot_id)
        raw = self.get_month_raw(month)

        revenue_results = raw["table"]["revenue"]["results"]
        receipts_results = raw["table"]["receipts"]["results"]

        return MonthSummary(
            plan_sum=revenue_results.get("plan_sum", 0),
            fact_sum=revenue_results.get("fact_sum", 0),
            percent=revenue_results.get("percent", 0),
            nonfisc_sum=revenue_results.get("nonfisc_sum", 0),
            receipts_count_plan=receipts_results.get("count_plan", 0),
            receipts_count_fact=receipts_results.get("count_fact", 0),
            receipts_average_plan=receipts_results.get("average_plan", 0),
            receipts_average_fact=receipts_results.get("average_fact", 0),
        )

    def get_pnl_year(self, spot_key: str, year: int) -> dict:
        """
        Возвращает сырой JSON P&L по точке за год.
        year — целое число, например 2026.
        """
        spot_id = SPOTS[spot_key]["id"]
        self.switch_spot(spot_id)
        resp = self._with_retry(
            lambda: self._client.post(PNL_URL, json={"period": str(year)})
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"reports/pnl/get failed: {data}")
        return data["data"]

    def close(self):
        self._client.close()
