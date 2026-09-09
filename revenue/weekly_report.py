"""
Недельный отчёт по выручке — отправляется по понедельникам, с аналитикой
от Claude (в отличие от ежедневного, который только цифры).

Логика дат:
- "прошедшая неделя" = семь дней, заканчивающихся вчера (если запускаем в понедельник,
  это пн-вс предыдущей календарной недели)
- "неделю до этого" = ещё 7 дней до прошедшей недели — для сравнения
- "та же неделя год назад" = тот же диапазон дат, минус 1 год — с фоллбэком на "нет данных"

Перенесено из бота "Аналитик Иван" (weekly_report.py) — run_weekly_report
раньше сама слала сообщения через TelegramSender, теперь возвращает
list[str] (см. revenue/daily_report.py — тот же принцип).
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from revenue.surfcoffee_client import SurfCoffeeClient, SPOTS, DayData, MonthSummary
from revenue.claude_commentary import ClaudeCommentary
from revenue.formatting import (
    fmt_money, fmt_int, fmt_percent, trend_emoji, plan_status_emoji,
    fmt_comparison_money, fmt_comparison_int,
)
from revenue.daily_report import _safe_mtd_last_year, _forecast_weighted

logger = logging.getLogger("revenue.weekly_report")


@dataclass
class WeekAggregate:
    fact_sum: float
    plan_sum: float
    days_count: int
    count_sum: int  # суммарное число чеков за период
    average_check: float  # средневзвешенный средний чек = fact_sum / count_sum


def _aggregate_days(days: list[DayData], date_from: date, date_to: date) -> WeekAggregate:
    """Суммирует факт/план/чеки по дням в диапазоне [date_from, date_to] включительно."""
    relevant = [d for d in days if date_from <= d.date <= date_to]
    fact_sum = sum(d.fact_revenue for d in relevant)
    count_sum = sum(d.count_fact or 0 for d in relevant)
    return WeekAggregate(
        fact_sum=fact_sum,
        plan_sum=sum(d.plan_revenue for d in relevant),
        days_count=len(relevant),
        count_sum=count_sum,
        average_check=(fact_sum / count_sum) if count_sum else 0,
    )


def _get_days_for_range(client: SurfCoffeeClient, spot_key: str, date_from: date, date_to: date) -> list[DayData]:
    """
    Достаёт DayData для произвольного диапазона дат, даже если он пересекает
    границу месяца — в этом случае делает два вызова get_month_days() и объединяет.
    """
    months_needed = set()
    months_needed.add(date_from.strftime("%Y-%m"))
    months_needed.add(date_to.strftime("%Y-%m"))

    all_days: list[DayData] = []
    for month in months_needed:
        all_days.extend(client.get_month_days(spot_key, month))

    return [d for d in all_days if date_from <= d.date <= date_to]


def _safe_week_aggregate(
    client: SurfCoffeeClient, spot_key: str, date_from: date, date_to: date
) -> WeekAggregate | None:
    """Аналог _aggregate_days, но с перехватом ошибок (для года назад, где данных может не быть)."""
    try:
        days = _get_days_for_range(client, spot_key, date_from, date_to)
        if not days:
            return None
        return _aggregate_days(days, date_from, date_to)
    except Exception as e:
        logger.warning("Не удалось получить данные за период %s..%s для %s: %s", date_from, date_to, spot_key, e)
        return None


def format_weekly_numbers_message(
    spot_title: str,
    week_from: date,
    week_to: date,
    current_week: WeekAggregate,
    prev_week: WeekAggregate | None,
    same_week_last_year: WeekAggregate | None,
) -> str:
    lines = []
    lines.append(f"☕️ *{spot_title}*")
    lines.append(f"_Итоги недели {week_from.strftime('%d.%m')} – {week_to.strftime('%d.%m.%Y')}_")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📅 *ИТОГИ НЕДЕЛИ*")
    lines.append("━━━━━━━━━━━━━━━")

    prev_label = "предыдущая неделя"

    status = plan_status_emoji(current_week.fact_sum, current_week.plan_sum)
    week_pct = (
        (current_week.fact_sum - current_week.plan_sum) / current_week.plan_sum * 100
        if current_week.plan_sum else 0
    )
    revenue_compare = fmt_comparison_money(
        current_week.fact_sum, prev_week.fact_sum if prev_week else None, prev_label
    )
    lines.append(f"{status} Факт: *{fmt_money(current_week.fact_sum)}*{revenue_compare}")
    lines.append(f"   План: {fmt_money(current_week.plan_sum)} ({fmt_percent(week_pct)})")

    count_compare = fmt_comparison_int(
        current_week.count_sum, prev_week.count_sum if prev_week else None, prev_label
    )
    lines.append(f"🧾 Чеков: *{fmt_int(current_week.count_sum)}* шт.{count_compare}")

    avg_compare = fmt_comparison_money(
        current_week.average_check, prev_week.average_check if prev_week else None, prev_label
    )
    lines.append(f"💳 Средний чек: *{fmt_money(current_week.average_check)}*{avg_compare}")

    lines.append("")
    if same_week_last_year and same_week_last_year.fact_sum:
        yoy_pct = (current_week.fact_sum - same_week_last_year.fact_sum) / same_week_last_year.fact_sum * 100
        lines.append(
            f"{trend_emoji(yoy_pct)} Та же неделя год назад: "
            f"{fmt_money(same_week_last_year.fact_sum)} ({fmt_percent(yoy_pct)})"
        )
        if same_week_last_year.count_sum:
            yoy_count_pct = (current_week.count_sum - same_week_last_year.count_sum) / same_week_last_year.count_sum * 100
            lines.append(
                f"   Чеков год назад: {fmt_int(same_week_last_year.count_sum)} ({fmt_percent(yoy_count_pct)})"
            )
    else:
        lines.append("⚪️ Та же неделя год назад: данных нет")

    return "\n".join(lines)


def format_combined_analytics_message(
    spot_analyses: list[tuple[str, str]],
    network_analysis: str,
) -> str:
    """
    Одно сообщение со всей аналитикой за неделю:
    spot_analyses = [(spot_title, analysis_text), ...]
    """
    lines = ["🧠 *Аналитика за неделю*", ""]
    for spot_title, text in spot_analyses:
        lines.append(f"☕️ *{spot_title}*: {text.strip()}")
        lines.append("")
    lines.append(f"🌐 *По сети*: {network_analysis.strip()}")
    return "\n".join(lines)


def format_weekly_network_message(spot_summaries: list[dict], analysis_text: str) -> str:
    lines = []
    lines.append("🌐 *СЕТЬ SURF COFFEE — ИТОГИ НЕДЕЛИ*")
    lines.append("━━━━━━━━━━━━━━━")

    total_fact = sum(s["fact"] for s in spot_summaries)
    total_plan = sum(s["plan"] for s in spot_summaries)

    for s in spot_summaries:
        status = plan_status_emoji(s["fact"], s["plan"])
        lines.append(f"{status} *{s['title']}*: {fmt_money(s['fact'])} (план {fmt_money(s['plan'])})")

    lines.append("")
    total_status = plan_status_emoji(total_fact, total_plan)
    total_pct = (total_fact - total_plan) / total_plan * 100 if total_plan else 0
    lines.append(f"{total_status} *ИТОГО ЗА НЕДЕЛЮ: {fmt_money(total_fact)}*")
    lines.append(f"   План: {fmt_money(total_plan)} ({fmt_percent(total_pct)})")

    lines.append("")
    lines.append("🧠 *Аналитика по сети*")
    lines.append(analysis_text.strip())

    return "\n".join(lines)


def format_monday_numbers_message(
    spot_title: str,
    week_from: date,
    week_to: date,
    yesterday_data: DayData,
    week_ago_data: DayData | None,
    mtd_fact: float,
    mtd_plan: float,
    mtd_last_year_fact: float | None,
    forecast: float,
    current_week: WeekAggregate,
    prev_week: WeekAggregate | None,
    same_week_last_year: WeekAggregate | None,
) -> str:
    y = yesterday_data
    wa = week_ago_data
    week_label = f"неделю назад {wa.date.strftime('%d.%m')}" if wa else "неделю назад"

    lines = []
    lines.append(f"☕️ *{spot_title}*")
    lines.append(f"_Сводка за {y.date.strftime('%d.%m.%Y')} + итоги недели {week_from.strftime('%d.%m')}–{week_to.strftime('%d.%m.%Y')}_")
    lines.append("")

    # --- ВЧЕРА ---
    lines.append("━━━━━━━━━━━━━━━")
    lines.append(f"📊 *ВЧЕРА ({y.date.strftime('%d.%m')})*")
    lines.append("━━━━━━━━━━━━━━━")

    status = plan_status_emoji(y.fact_revenue, y.plan_revenue)
    y_pct = (y.fact_revenue - y.plan_revenue) / y.plan_revenue * 100 if y.plan_revenue else 0
    lines.append(f"{status} Выручка: *{fmt_money(y.fact_revenue)}*{fmt_comparison_money(y.fact_revenue, wa.fact_revenue if wa else None, week_label)}")
    lines.append(f"   План: {fmt_money(y.plan_revenue)} ({fmt_percent(y_pct)})")

    if y.count_fact is not None:
        lines.append(f"🧾 Чеков: *{fmt_int(y.count_fact)}* шт.{fmt_comparison_int(y.count_fact, wa.count_fact if (wa and wa.count_fact) else None, week_label)}")
    if y.average_fact is not None:
        lines.append(f"💳 Средний чек: *{fmt_money(y.average_fact)}*{fmt_comparison_money(y.average_fact, wa.average_fact if (wa and wa.average_fact) else None, week_label)}")

    lines.append("")
    lines.append("📅 *С НАЧАЛА МЕСЯЦА*")
    mtd_status = plan_status_emoji(mtd_fact, mtd_plan)
    mtd_pct = (mtd_fact - mtd_plan) / mtd_plan * 100 if mtd_plan else 0
    lines.append(f"{mtd_status} Факт: *{fmt_money(mtd_fact)}*")
    lines.append(f"   План: {fmt_money(mtd_plan)} ({fmt_percent(mtd_pct)})")
    if mtd_last_year_fact and mtd_last_year_fact > 0:
        yoy_pct = (mtd_fact - mtd_last_year_fact) / mtd_last_year_fact * 100
        lines.append(f"{trend_emoji(yoy_pct)} Год назад: {fmt_money(mtd_last_year_fact)} ({fmt_percent(yoy_pct)})")
    else:
        lines.append("⚪️ Год назад: данных нет")
    lines.append(f"🔮 Прогноз: *{fmt_money(forecast)}*")

    # --- ИТОГИ НЕДЕЛИ ---
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append(f"📅 *ИТОГИ НЕДЕЛИ {week_from.strftime('%d.%m')}–{week_to.strftime('%d.%m')}*")
    lines.append("━━━━━━━━━━━━━━━")

    prev_label = "предыдущая неделя"
    week_status = plan_status_emoji(current_week.fact_sum, current_week.plan_sum)
    week_pct = (current_week.fact_sum - current_week.plan_sum) / current_week.plan_sum * 100 if current_week.plan_sum else 0
    lines.append(f"{week_status} Факт: *{fmt_money(current_week.fact_sum)}*{fmt_comparison_money(current_week.fact_sum, prev_week.fact_sum if prev_week else None, prev_label)}")
    lines.append(f"   План: {fmt_money(current_week.plan_sum)} ({fmt_percent(week_pct)})")
    lines.append(f"🧾 Чеков: *{fmt_int(current_week.count_sum)}* шт.{fmt_comparison_int(current_week.count_sum, prev_week.count_sum if prev_week else None, prev_label)}")
    lines.append(f"💳 Средний чек: *{fmt_money(current_week.average_check)}*{fmt_comparison_money(current_week.average_check, prev_week.average_check if prev_week else None, prev_label)}")

    lines.append("")
    if same_week_last_year and same_week_last_year.fact_sum:
        yoy_pct = (current_week.fact_sum - same_week_last_year.fact_sum) / same_week_last_year.fact_sum * 100
        lines.append(f"{trend_emoji(yoy_pct)} Та же неделя год назад: {fmt_money(same_week_last_year.fact_sum)} ({fmt_percent(yoy_pct)})")
    else:
        lines.append("⚪️ Та же неделя год назад: данных нет")

    return "\n".join(lines)


def run_weekly_report(
    surf_client: SurfCoffeeClient,
    claude: ClaudeCommentary,
    today: date | None = None,
) -> list[str]:
    """Возвращает сообщения полного недельного цикла (итоги прошедшей недели)."""
    today = today or date.today()

    # Прошедшая неделя: пн-вс, заканчивающаяся вчера (если сегодня понедельник —
    # это пн-вс предыдущей календарной недели)
    yesterday = today - timedelta(days=1)
    week_to = yesterday
    week_from = week_to - timedelta(days=6)

    prev_week_to = week_from - timedelta(days=1)
    prev_week_from = prev_week_to - timedelta(days=6)

    last_year_week_from = week_from.replace(year=week_from.year - 1)
    last_year_week_to = week_to.replace(year=week_to.year - 1)

    messages: list[str] = []
    network_summaries = []
    spot_analytics_data = []  # [(spot_key, spot_title, current_week, prev_week, same_week_last_year)]
    current_month = yesterday.strftime("%Y-%m")
    week_ago_date = yesterday - timedelta(days=7)

    # Шаг 1: все числа — по одному сообщению на точку
    for spot_key, spot_info in SPOTS.items():
        spot_title = spot_info["title"]
        logger.info("Понедельничный отчёт — обрабатываю точку: %s", spot_title)

        # Недельные данные
        current_days = _get_days_for_range(surf_client, spot_key, week_from, week_to)
        current_week = _aggregate_days(current_days, week_from, week_to)
        prev_week = _safe_week_aggregate(surf_client, spot_key, prev_week_from, prev_week_to)
        same_week_last_year = _safe_week_aggregate(
            surf_client, spot_key, last_year_week_from, last_year_week_to
        )

        # Дневные данные (вчера + MTD)
        month_days = surf_client.get_month_days(spot_key, current_month)
        summary: MonthSummary = surf_client.get_month_summary(spot_key, current_month)
        week_ago_month = week_ago_date.strftime("%Y-%m")
        if week_ago_month != current_month:
            try:
                month_days = month_days + surf_client.get_month_days(spot_key, week_ago_month)
            except Exception as e:
                logger.warning("Не удалось загрузить %s для %s: %s", week_ago_month, spot_key, e)

        yesterday_data = next((d for d in month_days if d.date == yesterday), None)
        week_ago_data = next((d for d in month_days if d.date == week_ago_date), None)
        mtd_last_year_fact = _safe_mtd_last_year(surf_client, spot_key, yesterday)
        current_month_days = [d for d in month_days if d.date.strftime("%Y-%m") == current_month]
        forecast = _forecast_weighted(current_month_days, summary.fact_sum, yesterday)

        if yesterday_data:
            combined_msg = format_monday_numbers_message(
                spot_title, week_from, week_to,
                yesterday_data, week_ago_data,
                summary.fact_sum, summary.plan_sum, mtd_last_year_fact, forecast,
                current_week, prev_week, same_week_last_year,
            )
        else:
            combined_msg = format_weekly_numbers_message(
                spot_title, week_from, week_to, current_week, prev_week, same_week_last_year
            )
        messages.append(combined_msg)

        network_summaries.append({"title": spot_title, "fact": current_week.fact_sum, "plan": current_week.plan_sum})
        spot_analytics_data.append((spot_key, spot_title, current_week, prev_week, same_week_last_year))

    # Шаг 2: одно сообщение со всей аналитикой
    if network_summaries:
        spot_analyses = []
        for spot_key, spot_title, current_week, prev_week, same_week_last_year in spot_analytics_data:
            try:
                text = claude.analyze_spot_week(
                    spot_title=spot_title,
                    week_from=week_from.strftime("%d.%m.%Y"),
                    week_to=week_to.strftime("%d.%m.%Y"),
                    week_fact=current_week.fact_sum,
                    week_plan=current_week.plan_sum,
                    prev_week_fact=prev_week.fact_sum if prev_week else None,
                    same_week_last_year_fact=same_week_last_year.fact_sum if same_week_last_year else None,
                    week_count=current_week.count_sum,
                    prev_week_count=prev_week.count_sum if prev_week else None,
                    week_average=current_week.average_check,
                    prev_week_average=prev_week.average_check if prev_week else None,
                )
            except Exception as e:
                logger.error("Ошибка аналитики за неделю для %s: %s", spot_key, e)
                text = "⚠️ Техническая ошибка."
            spot_analyses.append((spot_title, text))

        try:
            period_label = f"прошедшая неделя {week_from.strftime('%d.%m')}-{week_to.strftime('%d.%m.%Y')}"
            network_analysis = claude.analyze_network_period(network_summaries, period_label)
        except Exception as e:
            logger.error("Ошибка аналитики по сети за неделю: %s", e)
            network_analysis = "⚠️ Техническая ошибка."

        messages.append(format_combined_analytics_message(spot_analyses, network_analysis))

    logger.info("Недельный отчёт завершён")
    return messages
