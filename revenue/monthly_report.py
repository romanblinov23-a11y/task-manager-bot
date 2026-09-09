"""
Месячный отчёт по выручке — отправляется 1-го числа каждого месяца,
с итогами только что закончившегося месяца.

Сравнения:
- с предыдущим месяцем
- с тем же месяцем год назад (фоллбэк на "нет данных")

Перенесено из бота "Аналитик Иван" (monthly_report.py) — run_monthly_report
раньше сама слала сообщения через TelegramSender, теперь возвращает
list[str] (см. revenue/daily_report.py — тот же принцип).
"""

import logging
from datetime import date, timedelta

from revenue.surfcoffee_client import SurfCoffeeClient, SPOTS, MonthSummary
from revenue.claude_commentary import ClaudeCommentary
from revenue.formatting import fmt_money, fmt_int, fmt_percent, trend_emoji, plan_status_emoji, fmt_comparison_money, fmt_comparison_int

logger = logging.getLogger("revenue.monthly_report")

MONTH_NAMES_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def _prev_month_str(d: date) -> str:
    """Возвращает 'YYYY-MM' для месяца, предшествующего месяцу даты d."""
    first_of_this_month = d.replace(day=1)
    last_day_prev_month = first_of_this_month - timedelta(days=1)
    return last_day_prev_month.strftime("%Y-%m")


def _safe_month_summary(client: SurfCoffeeClient, spot_key: str, month: str) -> MonthSummary | None:
    try:
        return client.get_month_summary(spot_key, month)
    except Exception as e:
        logger.warning("Не удалось получить сводку за месяц %s для %s: %s", month, spot_key, e)
        return None


def _month_title(report_month: str) -> str:
    """'2026-06' -> 'Июнь 2026'"""
    year, month_num = report_month.split("-")
    return f"{MONTH_NAMES_RU[int(month_num)]} {year}"


def format_monthly_numbers_message(
    spot_title: str,
    report_month: str,  # "YYYY-MM" — месяц, по которому отчёт
    current: MonthSummary,
    prev_month: MonthSummary | None,
    same_month_last_year: MonthSummary | None,
) -> str:
    month_title = _month_title(report_month)
    prev_label = "предыдущий месяц"

    lines = []
    lines.append(f"☕️ *{spot_title}*")
    lines.append(f"_Итоги месяца: {month_title}_")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📅 *ИТОГИ МЕСЯЦА*")
    lines.append("━━━━━━━━━━━━━━━")

    status = plan_status_emoji(current.fact_sum, current.plan_sum)
    month_pct = (current.fact_sum - current.plan_sum) / current.plan_sum * 100 if current.plan_sum else 0
    revenue_compare = fmt_comparison_money(
        current.fact_sum, prev_month.fact_sum if prev_month else None, prev_label
    )
    lines.append(f"{status} Факт: *{fmt_money(current.fact_sum)}*{revenue_compare}")
    lines.append(f"   План: {fmt_money(current.plan_sum)} ({fmt_percent(month_pct)})")

    count_compare = fmt_comparison_int(
        current.receipts_count_fact, prev_month.receipts_count_fact if prev_month else None, prev_label
    )
    lines.append(f"🧾 Чеков: *{fmt_int(current.receipts_count_fact)}* шт.{count_compare}")

    avg_compare = fmt_comparison_money(
        current.receipts_average_fact, prev_month.receipts_average_fact if prev_month else None, prev_label
    )
    lines.append(f"💳 Средний чек: *{fmt_money(current.receipts_average_fact)}*{avg_compare}")

    lines.append("")
    if same_month_last_year and same_month_last_year.fact_sum:
        yoy_pct = (current.fact_sum - same_month_last_year.fact_sum) / same_month_last_year.fact_sum * 100
        lines.append(
            f"{trend_emoji(yoy_pct)} Тот же месяц год назад: "
            f"{fmt_money(same_month_last_year.fact_sum)} ({fmt_percent(yoy_pct)})"
        )
        if same_month_last_year.receipts_count_fact:
            yoy_count_pct = (
                (current.receipts_count_fact - same_month_last_year.receipts_count_fact)
                / same_month_last_year.receipts_count_fact * 100
            )
            lines.append(
                f"   Чеков год назад: {fmt_int(same_month_last_year.receipts_count_fact)} ({fmt_percent(yoy_count_pct)})"
            )
    else:
        lines.append("⚪️ Тот же месяц год назад: данных нет")

    return "\n".join(lines)


def format_monthly_analytics_message(spot_title: str, analysis_text: str) -> str:
    return f"🧠 *Аналитика за месяц: {spot_title}*\n\n{analysis_text.strip()}"


def format_monthly_network_message(spot_summaries: list[dict], analysis_text: str) -> str:
    lines = []
    lines.append("🌐 *СЕТЬ SURF COFFEE — ИТОГИ МЕСЯЦА*")
    lines.append("━━━━━━━━━━━━━━━")

    total_fact = sum(s["fact"] for s in spot_summaries)
    total_plan = sum(s["plan"] for s in spot_summaries)

    for s in spot_summaries:
        status = plan_status_emoji(s["fact"], s["plan"])
        lines.append(f"{status} *{s['title']}*: {fmt_money(s['fact'])} (план {fmt_money(s['plan'])})")

    lines.append("")
    total_status = plan_status_emoji(total_fact, total_plan)
    total_pct = (total_fact - total_plan) / total_plan * 100 if total_plan else 0
    lines.append(f"{total_status} *ИТОГО ЗА МЕСЯЦ: {fmt_money(total_fact)}*")
    lines.append(f"   План: {fmt_money(total_plan)} ({fmt_percent(total_pct)})")

    lines.append("")
    lines.append("🧠 *Аналитика по сети*")
    lines.append(analysis_text.strip())

    return "\n".join(lines)


def run_monthly_report(
    surf_client: SurfCoffeeClient,
    claude: ClaudeCommentary,
    today: date | None = None,
) -> list[str]:
    """
    Возвращает сообщения полного месячного цикла.
    Вызывается 1-го числа месяца — отчёт идёт по только что закончившемуся месяцу.
    """
    today = today or date.today()
    report_month_date = today.replace(day=1) - timedelta(days=1)  # последний день прошлого месяца
    report_month = report_month_date.strftime("%Y-%m")

    prev_month = _prev_month_str(report_month_date)
    same_month_last_year = report_month_date.replace(year=report_month_date.year - 1).strftime("%Y-%m")
    month_title = _month_title(report_month)

    messages: list[str] = []
    network_summaries = []

    for spot_key, spot_info in SPOTS.items():
        spot_title = spot_info["title"]
        logger.info("Месячный отчёт — обрабатываю точку: %s (%s)", spot_title, report_month)

        current = surf_client.get_month_summary(spot_key, report_month)
        prev = _safe_month_summary(surf_client, spot_key, prev_month)
        last_year = _safe_month_summary(surf_client, spot_key, same_month_last_year)

        numbers_message = format_monthly_numbers_message(
            spot_title, report_month, current, prev, last_year
        )
        messages.append(numbers_message)

        try:
            analysis_text = claude.analyze_spot_month(
                spot_title=spot_title,
                month_title=month_title,
                month_fact=current.fact_sum,
                month_plan=current.plan_sum,
                prev_month_fact=prev.fact_sum if prev else None,
                same_month_last_year_fact=last_year.fact_sum if last_year else None,
                month_count=current.receipts_count_fact,
                prev_month_count=prev.receipts_count_fact if prev else None,
                month_average=current.receipts_average_fact,
                prev_month_average=prev.receipts_average_fact if prev else None,
            )
        except Exception as e:
            logger.error("Ошибка аналитики за месяц для %s: %s", spot_key, e)
            analysis_text = "⚠️ Не удалось получить аналитику (техническая ошибка)."

        messages.append(format_monthly_analytics_message(spot_title, analysis_text))

        network_summaries.append({
            "title": spot_title,
            "fact": current.fact_sum,
            "plan": current.plan_sum,
        })

    if network_summaries:
        try:
            network_analysis = claude.analyze_network_period(network_summaries, f"месяц {month_title}")
        except Exception as e:
            logger.error("Ошибка аналитики по сети за месяц: %s", e)
            network_analysis = "⚠️ Не удалось получить аналитику по сети (техническая ошибка)."

        messages.append(format_monthly_network_message(network_summaries, network_analysis))

    logger.info("Месячный отчёт завершён")
    return messages
