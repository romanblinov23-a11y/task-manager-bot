"""HTML-шаблон дашборда (см. dashboard/server.py) — обычные f-строки, без
шаблонизатора, тем же приёмом, что тексты отчётов в bot/shift_reports.py.
Палитра и правила графиков — по skill dataviz (references/palette.md):
категориальный порядок фиксирован, у каждого графика одна ось (никаких
двух Y на одном графике — вместо этого связанные метрики рисуются как
пара графиков друг под другом с общей осью дат)."""

import json

from dashboard.data import CORRELATION_METRICS, METRIC_LABELS, describe_correlation
from dashboard.weather import describe_weather_code

# Категориальные слоты 1-3 (валидны все-пары в обоих режимах, см. palette.md) —
# используем по одному цвету на рынок, когда показываем "все точки" сразу.
_SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a"]
_SERIES_COLORS_DARK = ["#3987e5", "#d95926", "#199e70"]
_GOOD = "#0ca30c"
_CRITICAL = "#d03b3b"

_PERIOD_TABS = (("week", "Неделя"), ("month", "Месяц"), ("quarter", "90 дней"))


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}".replace(",", " ") + " ₽"


def _fmt_num(value: float | None, decimals: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}".replace(",", " ")


def _avg(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _total(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def _url(token: str, market_id: int | None, period: str, offset: int) -> str:
    market_part = f"&market_id={market_id}" if market_id is not None else ""
    return f"?token={token}&period={period}&offset={offset}{market_part}"


def _stat_tile(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<div class="tile-sub">{sub}</div>' if sub else ""
    return f'<div class="tile"><div class="tile-label">{label}</div><div class="tile-value">{value}</div>{sub_html}</div>'


def render_dashboard_page(
    markets: list[dict],
    selected_market_id: int | None,
    period: str,
    offset: int,
    period_label: str,
    rows: list[dict],
    token: str,
) -> str:
    dates = sorted({r["report_date"] for r in rows})
    market_ids = sorted({r["market_id"] for r in rows}, key=lambda mid: next(m["name"] for m in markets if m["id"] == mid))

    # Ряды по точкам — для графиков (цвет = категориальный слот по порядку точек)
    series_js = []
    for i, mid in enumerate(market_ids[:3]):
        market_rows = {r["report_date"]: r for r in rows if r["market_id"] == mid}
        name = next(m["name"] for m in markets if m["id"] == mid)
        series_js.append(
            {
                "name": name,
                "color": _SERIES_COLORS[i],
                "color_dark": _SERIES_COLORS_DARK[i],
                "dates": dates,
                "revenue": [market_rows[d]["revenue"] if d in market_rows else None for d in dates],
                "avg_check": [market_rows[d]["avg_check"] if d in market_rows else None for d in dates],
                "guests": [market_rows[d]["guests"] if d in market_rows else None for d in dates],
                "spmh": [market_rows[d]["spmh"] if d in market_rows else None for d in dates],
                "writeoff_total": [market_rows[d]["writeoff_total"] if d in market_rows else None for d in dates],
                "writeoff_total_plan": [market_rows[d]["writeoff_total_plan"] if d in market_rows else None for d in dates],
            }
        )

    # Погода — общая линия по датам (берём первую доступную запись на дату;
    # сейчас все 3 реальные точки в одном городе, так что это одна линия).
    weather_by_date: dict[str, dict] = {}
    for r in rows:
        d = r["report_date"]
        if d not in weather_by_date and (r.get("temp_avg_c") is not None or r.get("precipitation_mm") is not None):
            weather_by_date[d] = r
    weather_js = {
        "dates": dates,
        "temp": [weather_by_date[d]["temp_avg_c"] if d in weather_by_date else None for d in dates],
        "precip": [weather_by_date[d]["precipitation_mm"] if d in weather_by_date else None for d in dates],
    }

    events = [
        {"date": r["report_date"], "market_name": r["market_name"], "text": r["events"], "weather": describe_weather_code(r.get("weather_code"))}
        for r in rows
        if r["events"]
    ]

    correlation_matrix = _build_matrix(rows)
    correlation_rows = "".join(
        f'<tr><td>{METRIC_LABELS[a]} × {METRIC_LABELS[b]}</td><td>{describe_correlation(r)}</td></tr>'
        for (a, b), r in correlation_matrix.items()
        if r is not None
    ) or '<tr><td colspan="2">Пока мало данных для корреляций — нужно больше дней с отчётами.</td></tr>'

    # KPI: сравнение по точкам, если выбрано "все", иначе — плитки по одной точке
    if selected_market_id is None and len(series_js) > 1:
        kpi_rows = "".join(
            f"<tr><td>{s['name']}</td><td>{_fmt_money(_total(s['revenue']))}</td>"
            f"<td>{_fmt_money(_avg(s['avg_check']))}</td><td>{_fmt_num(_total(s['guests']))}</td>"
            f"<td>{_fmt_money(_avg(s['spmh']))}/ч</td><td>{_fmt_money(_total(s['writeoff_total']))}</td></tr>"
            for s in series_js
        )
        kpi_html = f"""
        <table class="kpi-table">
            <thead><tr><th>Точка</th><th>Выручка</th><th>Средний чек</th><th>Гости</th><th>SPMH</th><th>Списания</th></tr></thead>
            <tbody>{kpi_rows}</tbody>
        </table>
        """
    else:
        s = series_js[0] if series_js else {"revenue": [], "avg_check": [], "guests": [], "spmh": [], "writeoff_total": [], "writeoff_total_plan": []}
        writeoff_delta = ""
        total_wo, total_wo_plan = _total(s["writeoff_total"]), _total(s["writeoff_total_plan"])
        if total_wo is not None and total_wo_plan:
            pct = (total_wo / total_wo_plan - 1) * 100
            color = _CRITICAL if pct > 0 else _GOOD
            writeoff_delta = f'<span style="color:{color}">{pct:+.0f}% к норме</span>'
        kpi_html = f"""
        <div class="tiles">
            {_stat_tile("Выручка за период", _fmt_money(_total(s["revenue"])))}
            {_stat_tile("Средний чек", _fmt_money(_avg(s["avg_check"])))}
            {_stat_tile("Гости", _fmt_num(_total(s["guests"])))}
            {_stat_tile("SPMH", _fmt_money(_avg(s["spmh"])) + "/ч")}
            {_stat_tile("Списания", _fmt_money(total_wo), writeoff_delta)}
        </div>
        """

    market_tabs = "".join(
        f'<a class="tab{" active" if selected_market_id == m["id"] else ""}" href="{_url(token, m["id"], period, 0)}">{m["name"]}</a>'
        for m in markets
    )
    all_tab = f'<a class="tab{" active" if selected_market_id is None else ""}" href="{_url(token, None, period, 0)}">Все точки</a>'
    period_tabs = "".join(
        f'<a class="tab{" active" if period == p else ""}" href="{_url(token, selected_market_id, p, 0)}">{label}</a>'
        for p, label in _PERIOD_TABS
    )
    nav_html = ""
    if period in ("week", "month"):
        can_go_next = offset < 0
        next_arrow = (
            f'<a class="tab" href="{_url(token, selected_market_id, period, offset + 1)}">▶</a>'
            if can_go_next
            else '<span class="tab disabled">▶</span>'
        )
        nav_html = (
            f'<a class="tab" href="{_url(token, selected_market_id, period, offset - 1)}">◀</a>'
            f'<span class="period-label">{period_label}</span>{next_arrow}'
        )
    else:
        nav_html = f'<span class="period-label">{period_label}</span>'

    events_html = "".join(
        f'<li><b>{e["date"]}</b> ({e["market_name"]}, {e["weather"]}) — {e["text"]}</li>' for e in events
    ) or "<li>За период значимых событий не отмечали.</li>"

    empty_notice = "" if rows else '<p class="notice">Пока нет ни одного согласованного отчёта за выбранный период.</p>'

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Дашборд — Havana Standart</title>
<script src="/dashboard/static/chart.min.js"></script>
<style>
{_CSS}
</style>
</head>
<body>
<header>
    <h1>📊 Дашборд смен</h1>
    <div class="tabs">{all_tab}{market_tabs}</div>
    <div class="tabs">{period_tabs}</div>
    <div class="tabs">{nav_html}</div>
</header>
{empty_notice}
{kpi_html}
<section class="chart-pair">
    <h2>Выручка и температура</h2>
    <canvas id="chartRevenue" height="90"></canvas>
    <canvas id="chartTemp" height="60"></canvas>
</section>
<section class="chart-pair">
    <h2>Гости и осадки</h2>
    <canvas id="chartGuests" height="90"></canvas>
    <canvas id="chartPrecip" height="60"></canvas>
</section>
<section class="chart-single">
    <h2>Списания: факт и норма</h2>
    <canvas id="chartWriteoff" height="90"></canvas>
</section>
<section>
    <h2>Зависимости показателей</h2>
    <table class="corr-table"><tbody>{correlation_rows}</tbody></table>
</section>
<section>
    <h2>Значимые события за период</h2>
    <ul class="events">{events_html}</ul>
</section>
<script>
const series = {json.dumps(series_js, ensure_ascii=False)};
const weather = {json.dumps(weather_js, ensure_ascii=False)};
const dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
function seriesColor(s) {{ return dark ? s.color_dark : s.color; }}
function baseOpts(yLabel) {{
    return {{
        responsive: true,
        interaction: {{ mode: "index", intersect: false }},
        scales: {{ y: {{ title: {{ display: true, text: yLabel }}, grid: {{ color: dark ? "#2c2c2a" : "#e1e0d9" }} }},
                   x: {{ grid: {{ display: false }} }} }},
        plugins: {{ legend: {{ display: series.length > 1 }} }}
    }};
}}
new Chart(document.getElementById("chartRevenue"), {{
    type: "bar",
    data: {{ labels: weather.dates, datasets: series.map(s => ({{ label: s.name, data: s.revenue, backgroundColor: seriesColor(s), maxBarThickness: 24, borderRadius: 4 }})) }},
    options: baseOpts("Выручка, ₽")
}});
new Chart(document.getElementById("chartTemp"), {{
    type: "line",
    data: {{ labels: weather.dates, datasets: [{{ label: "Температура, °C", data: weather.temp, borderColor: "#eb6834", borderWidth: 2, pointRadius: 3, tension: 0.2 }}] }},
    options: baseOpts("°C")
}});
new Chart(document.getElementById("chartGuests"), {{
    type: "bar",
    data: {{ labels: weather.dates, datasets: series.map(s => ({{ label: s.name, data: s.guests, backgroundColor: seriesColor(s), maxBarThickness: 24, borderRadius: 4 }})) }},
    options: baseOpts("Гости")
}});
new Chart(document.getElementById("chartPrecip"), {{
    type: "bar",
    data: {{ labels: weather.dates, datasets: [{{ label: "Осадки, мм", data: weather.precip, backgroundColor: "#1baf7a", maxBarThickness: 24, borderRadius: 4 }}] }},
    options: baseOpts("мм")
}});
new Chart(document.getElementById("chartWriteoff"), {{
    type: "bar",
    data: {{ labels: weather.dates, datasets: [
        {{ label: "Списания факт", data: series[0] ? series[0].writeoff_total : [], backgroundColor: "#2a78d6", maxBarThickness: 24, borderRadius: 4 }},
        {{ label: "Норма", type: "line", data: series[0] ? series[0].writeoff_total_plan : [], borderColor: "#898781", borderDash: [4,4], borderWidth: 2, pointRadius: 0 }}
    ] }},
    options: baseOpts("₽")
}});
</script>
</body>
</html>"""


def _build_matrix(rows: list[dict]) -> dict[tuple[str, str], float | None]:
    from dashboard.data import build_correlation_matrix

    return build_correlation_matrix(rows, CORRELATION_METRICS)


def render_forbidden_page() -> str:
    return """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Доступ запрещён</title></head>
<body style="font-family: system-ui, sans-serif; padding: 40px;">
<h1>🔒 Доступ запрещён</h1>
<p>Ссылка неверна или устарела — запросите новую в боте командой /dashboard.</p>
</body></html>"""


_CSS = """
:root {
  color-scheme: light;
  --surface-1: #fcfcfb;
  --page: #f9f9f7;
  --text-primary: #0b0b0b;
  --text-secondary: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --border: rgba(11,11,11,0.10);
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --surface-1: #1a1a19;
    --page: #0d0d0d;
    --text-primary: #ffffff;
    --text-secondary: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --border: rgba(255,255,255,0.10);
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px; background: var(--page); color: var(--text-primary);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}
header { margin-bottom: 16px; }
h1 { font-size: 20px; margin: 0 0 12px; }
h2 { font-size: 15px; color: var(--text-secondary); margin: 0 0 8px; }
.tabs { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.tab {
  padding: 6px 12px; border-radius: 8px; text-decoration: none; color: var(--text-secondary);
  border: 1px solid var(--border); font-size: 13px;
}
.tab.active { background: var(--surface-1); color: var(--text-primary); border-color: var(--text-primary); }
.tab.disabled { opacity: 0.35; pointer-events: none; }
.period-label { padding: 6px 4px; font-size: 13px; color: var(--text-secondary); font-weight: 600; }
.notice { color: var(--text-secondary); }
.tiles { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
.tile {
  background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px;
  padding: 12px 16px; min-width: 140px; flex: 1;
}
.tile-label { font-size: 12px; color: var(--muted); }
.tile-value { font-size: 20px; font-weight: 600; margin-top: 4px; }
.tile-sub { font-size: 12px; margin-top: 4px; }
.kpi-table, .corr-table {
  width: 100%; border-collapse: collapse; background: var(--surface-1); border-radius: 12px; overflow: hidden;
  margin-bottom: 20px; font-size: 13px;
}
.kpi-table th, .kpi-table td, .corr-table td { padding: 8px 12px; border-bottom: 1px solid var(--grid); text-align: left; }
.kpi-table th { color: var(--muted); font-weight: 500; }
section { margin-bottom: 24px; max-width: 900px; }
.chart-pair canvas, .chart-single canvas { max-width: 100%; }
.events { padding-left: 18px; font-size: 13px; color: var(--text-secondary); }
.events li { margin-bottom: 4px; }
"""
