"""
Ежедневный отчёт по выручке — только цифры, без аналитики (аналитика есть
в недельном/месячном отчёте, см. weekly_report.py/monthly_report.py). По
каждой из точек — одно сообщение (вчера + MTD + прогноз на конец месяца).

Перенесено из бота "Аналитик Иван" (daily_report.py) — run_daily_report
раньше сама слала сообщения через TelegramSender, теперь просто
возвращает list[str] (единообразно с run_plan_report/run_fact_report) —
кто вызывает (bot/revenue_flow.py или джоб в main.py), тот и решает,
куда и как отправить.

Логика дат:
- "вчера" = today - 1 день
- "неделю назад" = тот же день недели, то есть yesterday - 7 дней
- "MTD" = сумма с 1-го числа текущего месяца по yesterday включительно
- "MTD год назад" = тот же период (1-е число по тот же день) прошлого года,
  если данных нет — корректно показываем "нет данных", без падения скрипта
"""

import logging
from collections import defaultdict
from datetime import date, timedelta

from revenue.surfcoffee_client import SurfCoffeeClient, SPOTS, MonthSummary
from revenue.formatting import format_daily_numbers_message, DailyNumbersInput

logger = logging.getLogger("revenue.daily_report")


def _days_in_month(d: date) -> int:
    if d.month == 12:
        next_month = date(d.year + 1, 1, 1)
    else:
        next_month = date(d.year, d.month + 1, 1)
    return (next_month - date(d.year, d.month, 1)).days


def _forecast_weighted(current_month_days: list, mtd_fact: float, yesterday: date) -> float:
    """
    Прогноз на конец месяца с учётом дней недели.

    Считает среднюю выручку отдельно для каждого дня недели (пн, вт, ... вс)
    по уже прошедшим дням текущего месяца, затем суммирует ожидаемую выручку
    для каждого оставшегося дня. Если для какого-то дня недели ещё нет данных
    (например, сегодня понедельник 7-го, а первый понедельник был сегодня) —
    используется общая среднесуточная как запасной вариант.
    """
    dow_revenues: dict[int, list[float]] = defaultdict(list)
    for d in current_month_days:
        if d.fact_revenue > 0:
            dow_revenues[d.date.weekday()].append(d.fact_revenue)

    if not dow_revenues:
        return mtd_fact

    dow_avg = {dow: sum(vals) / len(vals) for dow, vals in dow_revenues.items()}
    overall_avg = sum(sum(v) for v in dow_revenues.values()) / sum(len(v) for v in dow_revenues.values())

    days_total = _days_in_month(yesterday)
    remaining = sum(
        dow_avg.get(yesterday.replace(day=day_num).weekday(), overall_avg)
        for day_num in range(yesterday.day + 1, days_total + 1)
    )
    return mtd_fact + remaining


def _safe_mtd_last_year(client: SurfCoffeeClient, spot_key: str, yesterday: date) -> float | None:
    """
    Пытается получить MTD-факт за тот же период прошлого года.
    Если данных нет (ошибка API, пустой ответ, месяц не существовал и т.д.) —
    возвращает None, а не бросает исключение, т.к. отсутствие истории — ожидаемый случай.
    """
    try:
        last_year_month = yesterday.replace(year=yesterday.year - 1).strftime("%Y-%m")
        last_year_days = client.get_month_days(spot_key, last_year_month)
        last_year_yesterday = yesterday.replace(year=yesterday.year - 1)
        relevant_days = [d for d in last_year_days if d.date <= last_year_yesterday]
        if not relevant_days:
            return None
        return sum(d.fact_revenue for d in relevant_days)
    except Exception as e:
        logger.warning("Не удалось получить MTD за прошлый год для %s: %s", spot_key, e)
        return None


def run_daily_report(
    surf_client: SurfCoffeeClient,
    today: date | None = None,
    spot_keys: list[str] | None = None,
) -> list[str]:
    """
    Возвращает сообщения с цифрами (без аналитики) по каждой точке.
    spot_keys=None — все три точки, spot_keys=[...] — только указанные.
    """
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    week_ago_date = yesterday - timedelta(days=7)
    current_month = yesterday.strftime("%Y-%m")

    active_spots = {k: v for k, v in SPOTS.items() if spot_keys is None or k in spot_keys}
    messages: list[str] = []

    for spot_key, spot_info in active_spots.items():
        spot_title = spot_info["title"]
        logger.info("Обрабатываю точку: %s", spot_title)

        days = surf_client.get_month_days(spot_key, current_month)
        summary: MonthSummary = surf_client.get_month_summary(spot_key, current_month)

        week_ago_month = week_ago_date.strftime("%Y-%m")
        if week_ago_month != current_month:
            try:
                days = days + surf_client.get_month_days(spot_key, week_ago_month)
            except Exception as e:
                logger.warning("Не удалось получить данные за %s для %s: %s", week_ago_month, spot_key, e)

        yesterday_data = next((d for d in days if d.date == yesterday), None)
        if yesterday_data is None:
            logger.warning("Нет данных за %s для точки %s, пропускаю", yesterday, spot_key)
            continue

        week_ago_data = next((d for d in days if d.date == week_ago_date), None)

        mtd_last_year_fact = _safe_mtd_last_year(surf_client, spot_key, yesterday)

        current_month_days = [d for d in days if d.date.strftime("%Y-%m") == current_month]
        forecast = _forecast_weighted(current_month_days, summary.fact_sum, yesterday)

        numbers_input = DailyNumbersInput(
            spot_key=spot_key,
            yesterday=yesterday_data,
            week_ago=week_ago_data,
            mtd_fact=summary.fact_sum,
            mtd_plan=summary.plan_sum,
            mtd_last_year_fact=mtd_last_year_fact,
            forecast_end_of_month=forecast,
        )
        messages.append(format_daily_numbers_message(numbers_input))

    logger.info("Ежедневный отчёт завершён")
    return messages
