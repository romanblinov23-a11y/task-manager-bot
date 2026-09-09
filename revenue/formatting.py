"""
Форматирование данных Surf Coffee в красивые, читаемые Telegram-сообщения.

Все суммы — в рублях с разделителем тысяч (пробел), без копеек.
Используется Telegram Markdown (parse_mode="Markdown").
"""

from dataclasses import dataclass
from datetime import date

from revenue.surfcoffee_client import DayData, MonthSummary, SPOTS


def fmt_money(value: float) -> str:
    """1234567.89 -> '1 234 568 ₽'"""
    rounded = round(value)
    s = f"{rounded:,}".replace(",", " ")
    return f"{s} ₽"


def fmt_percent(value: float, with_sign: bool = True) -> str:
    """28.78 -> '+28.78%' или '-5.2%'"""
    sign = "+" if value > 0 and with_sign else ""
    return f"{sign}{value:.1f}%".replace(".", ",")


def fmt_int(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def trend_emoji(percent_diff: float) -> str:
    """Эмодзи-индикатор в зависимости от знака отклонения."""
    if percent_diff > 2:
        return "📈"
    elif percent_diff < -2:
        return "📉"
    else:
        return "➡️"


def plan_status_emoji(fact: float, plan: float) -> str:
    """Эмодзи статуса выполнения плана."""
    if plan == 0:
        return "⚪️"
    ratio = fact / plan
    if ratio >= 1.0:
        return "✅"
    elif ratio >= 0.9:
        return "🟡"
    else:
        return "🔴"


def fmt_comparison_money(current: float, compare: float | None, compare_label: str) -> str:
    """
    Строка сравнения денежной метрики с предыдущим периодом.
    Возвращает '' если compare is None (используется как доп. текст в скобках).
    Пример: '(неделю назад: 730 235 ₽, -3,8%)'
    """
    if compare is None or compare == 0:
        return ""
    diff_pct = (current - compare) / compare * 100
    return f" ({compare_label}: {fmt_money(compare)}, {fmt_percent(diff_pct)})"


def fmt_comparison_int(current: int, compare: int | None, compare_label: str) -> str:
    """Аналог fmt_comparison_money, но для целочисленных метрик (число чеков)."""
    if compare is None or compare == 0:
        return ""
    diff_pct = (current - compare) / compare * 100
    return f" ({compare_label}: {fmt_int(compare)}, {fmt_percent(diff_pct)})"


@dataclass
class DailyNumbersInput:
    """Всё, что нужно для сообщения с цифрами за день + MTD."""
    spot_key: str
    yesterday: DayData
    week_ago: DayData | None  # тот же день недели, неделю назад
    mtd_fact: float
    mtd_plan: float
    mtd_last_year_fact: float | None  # тот же период прошлого года, для сравнения
    forecast_end_of_month: float


def format_daily_numbers_message(data: DailyNumbersInput) -> str:
    """Формирует сообщение №1 (цифры) для одной точки за день."""
    spot_title = SPOTS[data.spot_key]["title"]
    y = data.yesterday
    wa = data.week_ago

    lines = []
    lines.append(f"☕️ *{spot_title}*")
    lines.append(f"_Сводка за {y.date.strftime('%d.%m.%Y')}_")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📊 *ВЧЕРА*")
    lines.append("━━━━━━━━━━━━━━━")

    week_label = f"неделю назад {wa.date.strftime('%d.%m')}" if wa else "неделю назад"

    status = plan_status_emoji(y.fact_revenue, y.plan_revenue)
    y_pct = (y.fact_revenue - y.plan_revenue) / y.plan_revenue * 100 if y.plan_revenue else 0
    revenue_compare = fmt_comparison_money(y.fact_revenue, wa.fact_revenue if wa else None, week_label)
    lines.append(f"{status} Выручка: *{fmt_money(y.fact_revenue)}*{revenue_compare}")
    lines.append(f"   План: {fmt_money(y.plan_revenue)} ({fmt_percent(y_pct)})")

    if y.count_fact is not None:
        count_compare = fmt_comparison_int(
            y.count_fact, wa.count_fact if (wa and wa.count_fact is not None) else None, week_label
        )
        lines.append(f"🧾 Чеков: *{fmt_int(y.count_fact)}* шт.{count_compare}")

    if y.average_fact is not None:
        avg_compare = fmt_comparison_money(
            y.average_fact, wa.average_fact if (wa and wa.average_fact is not None) else None, week_label
        )
        lines.append(f"💳 Средний чек: *{fmt_money(y.average_fact)}*{avg_compare}")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━")
    lines.append("📅 *С НАЧАЛА МЕСЯЦА*")
    lines.append("━━━━━━━━━━━━━━━")

    mtd_status = plan_status_emoji(data.mtd_fact, data.mtd_plan)
    mtd_pct = (data.mtd_fact - data.mtd_plan) / data.mtd_plan * 100 if data.mtd_plan else 0
    lines.append(f"{mtd_status} Факт: *{fmt_money(data.mtd_fact)}*")
    lines.append(f"   План: {fmt_money(data.mtd_plan)} ({fmt_percent(mtd_pct)})")

    if data.mtd_last_year_fact is not None and data.mtd_last_year_fact > 0:
        yoy_pct = (data.mtd_fact - data.mtd_last_year_fact) / data.mtd_last_year_fact * 100
        lines.append(
            f"{trend_emoji(yoy_pct)} Год назад за этот же период: "
            f"{fmt_money(data.mtd_last_year_fact)} ({fmt_percent(yoy_pct)})"
        )
    else:
        lines.append("⚪️ Год назад: данных нет")

    lines.append("")
    lines.append(f"🔮 Прогноз на конец месяца: *{fmt_money(data.forecast_end_of_month)}*")

    return "\n".join(lines)


def format_analytics_message(spot_title: str, analysis_text: str) -> str:
    """Формирует сообщение №2 (аналитика от Claude) для одной точки."""
    lines = [
        f"🧠 *Аналитика: {spot_title}*",
        "",
        analysis_text.strip(),
    ]
    return "\n".join(lines)


def format_network_message(spot_summaries: list[dict], analysis_text: str) -> str:
    """
    Формирует единое сообщение по всей сети (цифры + аналитика вместе).
    spot_summaries — список словарей вида:
        {"title": ..., "fact": ..., "plan": ..., "percent": ...}
    """
    lines = []
    lines.append("🌐 *СЕТЬ SURF COFFEE — ИТОГИ ПО ВСЕМ ТОЧКАМ*")
    lines.append("━━━━━━━━━━━━━━━")

    total_fact = sum(s["fact"] for s in spot_summaries)
    total_plan = sum(s["plan"] for s in spot_summaries)

    for s in spot_summaries:
        status = plan_status_emoji(s["fact"], s["plan"])
        lines.append(f"{status} *{s['title']}*: {fmt_money(s['fact'])} (план {fmt_money(s['plan'])})")

    lines.append("")
    total_status = plan_status_emoji(total_fact, total_plan)
    total_pct = (total_fact - total_plan) / total_plan * 100 if total_plan else 0
    lines.append(f"{total_status} *ИТОГО: {fmt_money(total_fact)}*")
    lines.append(f"   План: {fmt_money(total_plan)} ({fmt_percent(total_pct)})")

    lines.append("")
    lines.append("🧠 *Аналитика по сети*")
    lines.append(analysis_text.strip())

    return "\n".join(lines)
