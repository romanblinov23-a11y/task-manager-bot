"""
Выгрузка плана (выручка + чеки по дням) из Surf Coffee API (НИМБ).
Используется командой /plan — пользователь выбирает месяц кнопками,
бот отправляет подневный план по каждой точке.
"""

import logging
from dataclasses import dataclass
from datetime import date

from revenue.surfcoffee_client import SurfCoffeeClient, SPOTS, DayData

logger = logging.getLogger("revenue.plan_report")

MONTH_NAMES_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}

DAYS_RU = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


@dataclass
class DayPlanRow:
    date: date
    revenue_plan: float
    count_plan: int | None


def get_spot_plan(client: SurfCoffeeClient, spot_key: str, month: str) -> list[DayPlanRow]:
    """
    Возвращает подневный план выручки и чеков по точке за месяц.
    month формата 'YYYY-MM'. Включает все дни месяца (в т.ч. будущие).
    """
    spot_id = SPOTS[spot_key]["id"]
    client.switch_spot(spot_id)
    raw = client.get_month_raw(month)

    revenue_data = raw["table"]["revenue"]["data"]

    # Пытаемся вытащить подневный план чеков из receipts.data
    receipts_by_date: dict[str, int | None] = {}
    receipts_data = raw["table"].get("receipts", {}).get("data", [])
    for row in receipts_data:
        raw_date = (row.get("date") or "").split(" ")[0]
        count = (
            row.get("count_plan")
            or row.get("planning_count")
            or row.get("plan_count")
        )
        if raw_date:
            receipts_by_date[raw_date] = int(count) if count else None

    rows: list[DayPlanRow] = []
    for row in revenue_data:
        date_str = row["date"].split(" ")[0]
        row_date = date.fromisoformat(date_str)
        revenue_plan = row.get("planning_revenue", 0) or 0
        count_plan = receipts_by_date.get(date_str)
        rows.append(DayPlanRow(date=row_date, revenue_plan=revenue_plan, count_plan=count_plan))

    return rows


def format_spot_plan_message(spot_title: str, month_label: str, rows: list[DayPlanRow]) -> str:
    has_count = any(r.count_plan is not None and r.count_plan > 0 for r in rows)

    lines = [f"☕️ *{spot_title}*", f"📅 {month_label} — план по дням", ""]

    total_rev = 0.0
    total_count = 0

    for r in rows:
        day_name = DAYS_RU[r.date.weekday()]
        rev_str = f"{r.revenue_plan:,.0f}".replace(",", " ")  # тонкий пробел
        line = f"{r.date.strftime('%d.%m')} {day_name} — {rev_str} ₽"
        if has_count:
            count_str = f"{r.count_plan:,}".replace(",", " ") if r.count_plan else "—"
            line += f" | {count_str} чек."
        lines.append(line)
        total_rev += r.revenue_plan
        if r.count_plan:
            total_count += r.count_plan

    total_rev_str = f"{total_rev:,.0f}".replace(",", " ")
    lines.append("━━━━━━━━━━━━━━━")
    total_line = f"Итого: *{total_rev_str} ₽*"
    if has_count and total_count:
        total_count_str = f"{total_count:,}".replace(",", " ")
        total_line += f" | *{total_count_str} чек.*"
    lines.append(total_line)

    return "\n".join(lines)


def run_plan_report(client: SurfCoffeeClient, month: str, spot_key: str | None = None) -> list[str]:
    """
    Возвращает список сообщений с подневным планом за месяц.
    spot_key=None — все три точки, иначе только указанная.
    month формата 'YYYY-MM'.
    """
    year, mon = map(int, month.split("-"))
    month_label = f"{MONTH_NAMES_RU[mon]} {year}"

    spots = {spot_key: SPOTS[spot_key]} if spot_key else SPOTS

    messages = []
    for key, spot_info in spots.items():
        spot_title = spot_info["title"]
        logger.info("Загружаю план для %s за %s", spot_title, month)
        try:
            rows = get_spot_plan(client, key, month)
            if not rows:
                messages.append(f"☕️ *{spot_title}*\n⚠️ Нет данных за {month_label}.")
            else:
                messages.append(format_spot_plan_message(spot_title, month_label, rows))
        except Exception as e:
            logger.error("Ошибка загрузки плана для %s: %s", key, e)
            messages.append(f"☕️ *{spot_title}*\n⚠️ Не удалось загрузить план: {e}")

    return messages


# ──────────────────────────────────────────────
# Подневный ФАКТ
# ──────────────────────────────────────────────

def format_spot_fact_message(spot_title: str, month_label: str, days: list[DayData]) -> str:
    has_count = any(d.count_fact is not None and d.count_fact > 0 for d in days)

    lines = [f"☕️ *{spot_title}*", f"📊 {month_label} — факт по дням", ""]

    total_rev = 0.0
    total_count = 0

    for d in days:
        day_name = DAYS_RU[d.date.weekday()]
        rev_str = f"{d.fact_revenue:,.0f}".replace(",", " ")
        line = f"{d.date.strftime('%d.%m')} {day_name} — {rev_str} ₽"
        if has_count and d.count_fact:
            avg_str = f"{d.average_fact:,.0f}".replace(",", " ") if d.average_fact else "—"
            line += f" | {d.count_fact} чек. | {avg_str} ₽ ср.чек"
        lines.append(line)
        total_rev += d.fact_revenue
        if d.count_fact:
            total_count += d.count_fact

    total_rev_str = f"{total_rev:,.0f}".replace(",", " ")
    lines.append("━━━━━━━━━━━━━━━")
    total_line = f"Итого: *{total_rev_str} ₽*"
    if has_count and total_count:
        avg_overall = total_rev / total_count if total_count else 0
        avg_str = f"{avg_overall:,.0f}".replace(",", " ")
        total_count_str = f"{total_count:,}".replace(",", " ")
        total_line += f" | *{total_count_str} чек.* | {avg_str} ₽ ср.чек"
    lines.append(total_line)

    return "\n".join(lines)


def run_fact_report(client: SurfCoffeeClient, month: str, spot_key: str | None = None) -> list[str]:
    """
    Возвращает список сообщений с подневным фактом за месяц.
    spot_key=None — все три точки, иначе только указанная.
    month формата 'YYYY-MM'.
    """
    year, mon = map(int, month.split("-"))
    month_label = f"{MONTH_NAMES_RU[mon]} {year}"

    spots = {spot_key: SPOTS[spot_key]} if spot_key else SPOTS

    messages = []
    for key, spot_info in spots.items():
        spot_title = spot_info["title"]
        logger.info("Загружаю факт для %s за %s", spot_title, month)
        try:
            days = client.get_month_days(key, month)
            if not days:
                messages.append(f"☕️ *{spot_title}*\n⚠️ Нет данных за {month_label}.")
            else:
                messages.append(format_spot_fact_message(spot_title, month_label, days))
        except Exception as e:
            logger.error("Ошибка загрузки факта для %s: %s", key, e)
            messages.append(f"☕️ *{spot_title}*\n⚠️ Не удалось загрузить факт: {e}")

    return messages
