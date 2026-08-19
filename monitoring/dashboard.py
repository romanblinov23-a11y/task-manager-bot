import json
from datetime import date, timedelta

from config.timeutil import today
from monitoring.calculations import compute_market_capacity, compute_share, is_anomaly
from monitoring.competitors import list_competitors
from monitoring.constants import ANOMALY_WINDOW_READINGS
from monitoring.markets import get_market, list_markets
from monitoring.observations import get_observations
from monitoring.readings import get_readings

_OWN_COLOR = "#2E8B7A"
_COMPETITOR_PALETTE = ["#E07A5F", "#3D5A80", "#F2CC8F", "#81B29A", "#9B5DE5", "#F4845F", "#577590", "#B56576"]

_RECOMMENDATIONS = {
    ("competitor", "up"): "Резкий рост у конкурента — стоит посетить точку лично и проверить все факторы формирования (продукт, атмосферу, сервис, бренд, персонал).",
    ("competitor", "down"): "Падение у конкурента — проверьте, не было ли смены персонала, ремонта, изменения меню или цен.",
    ("own", "up"): "Рост на нашей точке — зафиксируйте, что именно изменилось (акция, персонал, меню), чтобы повторить эффект.",
    ("own", "down"): "Падение на нашей точке — разберите причины наравне с конкурентами: персонал, меню, цены, сервис.",
    ("market", "up"): "Общий рост рынка — вероятны внешние факторы: сезон, погода, праздники, события в районе.",
    ("market", "down"): "Общее падение рынка — проверьте контекст: экономика, сезонный спад, закрытие точки-генератора трафика поблизости.",
}


def _color_for(index: int, is_own: bool) -> str:
    if is_own:
        return _OWN_COLOR
    return _COMPETITOR_PALETTE[index % len(_COMPETITOR_PALETTE)]


def _load_competitor_series(market_id: int) -> list[dict]:
    """Для каждого конкурента (и точки Surf) — восходящий по дате ряд снятий."""
    result = []
    for competitor in list_competitors(market_id):
        readings = list(reversed(get_readings(competitor["id"])))  # get_readings отдаёт DESC — переворачиваем в ASC
        result.append({"competitor": competitor, "readings": readings})
    return result


def _build_capacity_timeline(series: list[dict]) -> list[dict]:
    """Единая временная шкала по всем датам снятий рынка. На каждую дату —
    значение каждого конкурента "на текущий момент" (последнее известное на
    эту дату или раньше, без выдумывания данных для тех, у кого снятий ещё
    не было — §7, §8)."""
    all_dates = sorted({r["reading_at"] for s in series for r in s["readings"]})
    timeline = []
    for d in all_dates:
        values: dict[int, float] = {}
        for s in series:
            competitor_id = s["competitor"]["id"]
            known = [r for r in s["readings"] if r["reading_at"] <= d]
            if known:
                values[competitor_id] = known[-1]["avg_checks_per_day"]
        capacity = compute_market_capacity(values)
        shares = {cid: compute_share(v, capacity) for cid, v in values.items()}
        timeline.append({"date": d, "capacity": capacity, "values": values, "shares": shares})
    return timeline


def _detect_competitor_anomalies(series: list[dict], market_id: int) -> list[dict]:
    anomalies = []
    for s in series:
        competitor = s["competitor"]
        readings = s["readings"]
        for i in range(ANOMALY_WINDOW_READINGS, len(readings)):
            window = [r["avg_checks_per_day"] for r in readings[i - ANOMALY_WINDOW_READINGS : i]]
            current = readings[i]
            if not is_anomaly(current["avg_checks_per_day"], window):
                continue
            avg = sum(window) / len(window)
            direction = "up" if current["avg_checks_per_day"] > avg else "down"
            kind = "own" if competitor["is_own"] else "competitor"
            anomalies.append(
                {
                    "competitor_name": competitor["name"],
                    "competitor_code": competitor["code"],
                    "date": current["reading_at"],
                    "value": current["avg_checks_per_day"],
                    "norm": round(avg, 1),
                    "direction": direction,
                    "recommendation": _RECOMMENDATIONS[(kind, direction)],
                    "causes": _find_causes(market_id, competitor["id"], current["reading_at"]),
                }
            )
    return anomalies


def _detect_market_anomalies(timeline: list[dict]) -> list[dict]:
    anomalies = []
    capacities = [point["capacity"] for point in timeline]
    for i in range(ANOMALY_WINDOW_READINGS, len(timeline)):
        window = capacities[i - ANOMALY_WINDOW_READINGS : i]
        current = timeline[i]
        if not is_anomaly(current["capacity"], window):
            continue
        avg = sum(window) / len(window)
        direction = "up" if current["capacity"] > avg else "down"
        anomalies.append(
            {
                "date": current["date"],
                "value": round(current["capacity"], 1),
                "norm": round(avg, 1),
                "direction": direction,
                "recommendation": _RECOMMENDATIONS[("market", direction)],
            }
        )
    return anomalies


def _find_causes(market_id: int, competitor_id: int, anomaly_date: str, window_days: int = 7) -> list[str]:
    d = date.fromisoformat(anomaly_date)
    since = (d - timedelta(days=window_days)).isoformat()
    until = (d + timedelta(days=window_days)).isoformat()
    causes = []
    for obs in get_observations(market_id):
        obs_date = obs["observed_at"][:10]
        if not (since <= obs_date <= until):
            continue
        if obs["competitor_id"] != competitor_id:
            continue
        causes.append(f"[{obs['category']}] {obs['text']}" if obs["text"] else f"[{obs['category']}]")
    return causes


def _filter_timeline(timeline: list[dict], days: int) -> list[dict]:
    cutoff = (today() - timedelta(days=days)).isoformat()
    return [p for p in timeline if p["date"] >= cutoff]


def _dataset_for(series_item: dict, timeline: list[dict], color: str) -> dict:
    competitor_id = series_item["competitor"]["id"]
    data = []
    for point in timeline:
        share = point["shares"].get(competitor_id)
        data.append(round(share, 1) if share is not None else None)
    return {
        "label": series_item["competitor"]["name"],
        "data": data,
        "borderColor": color,
        "backgroundColor": color,
        "spanGaps": False,
        "tension": 0.2,
    }


_HTML_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; padding: 24px; background: #f7f7f8; color: #1a1a1a; }}
  h1 {{ font-size: 22px; margin-bottom: 4px; }}
  h2 {{ font-size: 16px; margin-top: 32px; }}
  .subtitle {{ color: #666; margin-bottom: 24px; }}
  .cards {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 8px; }}
  .card {{ background: #fff; border-radius: 12px; padding: 16px 20px; box-shadow: 0 1px 3px rgba(0,0,0,.08); min-width: 180px; }}
  .card .value {{ font-size: 24px; font-weight: 600; }}
  .card .label {{ color: #666; font-size: 13px; }}
  .chart-wrap {{ background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-bottom: 8px; }}
  canvas {{ max-height: 360px; }}
  .notice {{ background: #fff8e1; border: 1px solid #f0d97a; border-radius: 8px; padding: 12px 16px; margin: 16px 0; font-size: 14px; }}
  .anomaly {{ background: #fff; border-left: 4px solid #E07A5F; border-radius: 8px; padding: 12px 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .anomaly.up {{ border-left-color: #2E8B7A; }}
  .anomaly .meta {{ color: #666; font-size: 13px; margin-bottom: 6px; }}
  .anomaly .rec {{ margin-top: 8px; font-size: 14px; }}
  .anomaly .causes {{ margin-top: 6px; font-size: 13px; color: #444; }}
  .cdn-note {{ color: #999; font-size: 12px; margin-top: 40px; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="subtitle">{subtitle}</div>

{insufficient_data_banner}

<div class="cards">
  <div class="card"><div class="value">{capacity_now:g}</div><div class="label">Ёмкость рынка, чек/день (≈{capacity_week:g} чек/нед.)</div></div>
</div>

<h2>1. Ёмкость рынка во времени</h2>
<div class="chart-wrap"><canvas id="capacityChart"></canvas></div>

<h2>2. Доля рынка по каждому конкуренту (последний период)</h2>
<div class="chart-wrap"><canvas id="shareNowChart"></canvas></div>

<h2>3. Тенденция доли — за всё время</h2>
<div class="chart-wrap"><canvas id="shareAllChart"></canvas></div>

<h2>4. Тенденция доли — последние 3 месяца</h2>
<div class="chart-wrap"><canvas id="share3mChart"></canvas></div>

<h2>5. Тенденция доли — последний месяц</h2>
<div class="chart-wrap"><canvas id="share1mChart"></canvas></div>

<h2>6. Явные изменения, возможные причины и рекомендации</h2>
{anomalies_html}

<div class="cdn-note">Графики интерактивны (наведите курсор на точки) — для их отображения нужен доступ в интернет (библиотека графиков подгружается с CDN).</div>

<script>
const capacityLabels = {capacity_labels};
const capacityData = {capacity_data};
const shareLabelsAll = {share_labels_all};
const shareDatasetsAll = {share_datasets_all};
const shareLabels3m = {share_labels_3m};
const shareDatasets3m = {share_datasets_3m};
const shareLabels1m = {share_labels_1m};
const shareDatasets1m = {share_datasets_1m};
const shareNowLabels = {share_now_labels};
const shareNowData = {share_now_data};
const shareNowColors = {share_now_colors};

new Chart(document.getElementById('capacityChart'), {{
  type: 'line',
  data: {{ labels: capacityLabels, datasets: [{{ label: 'Ёмкость рынка (чек/день)', data: capacityData, borderColor: '#3D5A80', backgroundColor: '#3D5A80', tension: 0.2 }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

new Chart(document.getElementById('shareNowChart'), {{
  type: 'bar',
  data: {{ labels: shareNowLabels, datasets: [{{ label: 'Доля, %', data: shareNowData, backgroundColor: shareNowColors }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ beginAtZero: true, max: 100 }} }} }}
}});

function shareLineChart(id, labels, datasets) {{
  new Chart(document.getElementById(id), {{
    type: 'line',
    data: {{ labels: labels, datasets: datasets }},
    options: {{ scales: {{ y: {{ beginAtZero: true, max: 100, title: {{ display: true, text: 'Доля, %' }} }} }} }}
  }});
}}
shareLineChart('shareAllChart', shareLabelsAll, shareDatasetsAll);
shareLineChart('share3mChart', shareLabels3m, shareDatasets3m);
shareLineChart('share1mChart', shareLabels1m, shareDatasets1m);
</script>
</body>
</html>
"""


def _render_anomalies_html(competitor_anomalies: list[dict], market_anomalies: list[dict]) -> str:
    if not competitor_anomalies and not market_anomalies:
        return '<div class="notice">За доступный период аномалий (отклонений ≥20% от 4-недельной нормы) не обнаружено.</div>'

    blocks = []
    for a in sorted(market_anomalies, key=lambda x: x["date"], reverse=True):
        arrow = "📈" if a["direction"] == "up" else "📉"
        blocks.append(
            f'<div class="anomaly {a["direction"]}"><div class="meta">{arrow} Рынок в целом — {a["date"]}: '
            f'{a["value"]:g} чек/день (норма {a["norm"]:g})</div><div class="rec">{a["recommendation"]}</div></div>'
        )
    for a in sorted(competitor_anomalies, key=lambda x: x["date"], reverse=True):
        arrow = "📈" if a["direction"] == "up" else "📉"
        causes_html = ""
        if a["causes"]:
            causes_list = "".join(f"<li>{c}</li>" for c in a["causes"])
            causes_html = f'<div class="causes">Возможные причины (наблюдения рядом с этой датой):<ul>{causes_list}</ul></div>'
        blocks.append(
            f'<div class="anomaly {a["direction"]}"><div class="meta">{arrow} {a["competitor_code"]} — {a["competitor_name"]} — '
            f'{a["date"]}: {a["value"]:g} чек/день (норма {a["norm"]:g})</div>'
            f'<div class="rec">{a["recommendation"]}</div>{causes_html}</div>'
        )
    return "".join(blocks)


def generate_market_dashboard(market_id: int) -> tuple[str, str]:
    market = get_market(market_id)
    series = _load_competitor_series(market_id)
    timeline = _build_capacity_timeline(series)

    distinct_dates = sorted({p["date"] for p in timeline})
    insufficient_banner = ""
    if len(distinct_dates) < 2:
        insufficient_banner = (
            '<div class="notice">Данных пока меньше двух недель — тенденции ещё формируются. '
            "Устойчивые паттерны, по регламенту, проявляются через 2–3 месяца регулярного мониторинга.</div>"
        )

    capacity_now = timeline[-1]["capacity"] if timeline else 0.0
    colors = {s["competitor"]["id"]: _color_for(i, s["competitor"]["is_own"]) for i, s in enumerate(series)}

    timeline_3m = _filter_timeline(timeline, 90)
    timeline_1m = _filter_timeline(timeline, 30)

    def datasets_for(tl):
        return [_dataset_for(s, tl, colors[s["competitor"]["id"]]) for s in series]

    now_point = timeline[-1] if timeline else {"shares": {}}
    share_now_labels = [s["competitor"]["name"] for s in series]
    share_now_data = [round(now_point["shares"].get(s["competitor"]["id"], 0) or 0, 1) for s in series]
    share_now_colors = [colors[s["competitor"]["id"]] for s in series]

    competitor_anomalies = _detect_competitor_anomalies(series, market_id)
    market_anomalies = _detect_market_anomalies(timeline)

    html = _HTML_TEMPLATE.format(
        title=f"Дашборд рынка «{market['name']}»",
        subtitle=f"Наша точка: {market['our_point_name']}. Обновлено {today().isoformat()}.",
        insufficient_data_banner=insufficient_banner,
        capacity_now=capacity_now,
        capacity_week=capacity_now * 7,
        capacity_labels=json.dumps([p["date"] for p in timeline], ensure_ascii=False),
        capacity_data=json.dumps([round(p["capacity"], 1) for p in timeline]),
        share_labels_all=json.dumps([p["date"] for p in timeline], ensure_ascii=False),
        share_datasets_all=json.dumps(datasets_for(timeline), ensure_ascii=False),
        share_labels_3m=json.dumps([p["date"] for p in timeline_3m], ensure_ascii=False),
        share_datasets_3m=json.dumps(datasets_for(timeline_3m), ensure_ascii=False),
        share_labels_1m=json.dumps([p["date"] for p in timeline_1m], ensure_ascii=False),
        share_datasets_1m=json.dumps(datasets_for(timeline_1m), ensure_ascii=False),
        share_now_labels=json.dumps(share_now_labels, ensure_ascii=False),
        share_now_data=json.dumps(share_now_data),
        share_now_colors=json.dumps(share_now_colors),
        anomalies_html=_render_anomalies_html(competitor_anomalies, market_anomalies),
    )

    filename = f"dashboard_{market['name'].replace(' ', '_')}_{today().isoformat()}.html"
    return filename, html


def generate_aggregate_dashboard() -> tuple[str, str]:
    """Упрощённый сводный дашборд владельца по всем рынкам сразу: ёмкость
    каждого рынка на последнюю дату — детальные аномалии и тренды по
    конкурентам смотрятся через дашборд конкретного рынка."""
    markets = list_markets()
    rows = []
    for market in markets:
        series = _load_competitor_series(market["id"])
        timeline = _build_capacity_timeline(series)
        capacity_now = timeline[-1]["capacity"] if timeline else 0.0
        rows.append((market["name"], capacity_now))

    labels = json.dumps([r[0] for r in rows], ensure_ascii=False)
    data = json.dumps([round(r[1], 1) for r in rows])

    html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Дашборд — все рынки</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; padding: 24px; background: #f7f7f8; color: #1a1a1a; }}
  h1 {{ font-size: 22px; }}
  .chart-wrap {{ background: #fff; border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); max-width: 720px; }}
  .cdn-note {{ color: #999; font-size: 12px; margin-top: 24px; }}
</style>
</head>
<body>
<h1>Ёмкость рынков — сводно на {today().isoformat()}</h1>
<div class="chart-wrap"><canvas id="marketsChart"></canvas></div>
<div class="cdn-note">Для детальной аналитики (тенденции, аномалии, причины) по конкретному рынку используйте /dashboard_market и выберите рынок. Для отображения графика нужен доступ в интернет.</div>
<script>
new Chart(document.getElementById('marketsChart'), {{
  type: 'bar',
  data: {{ labels: {labels}, datasets: [{{ label: 'Ёмкость, чек/день', data: {data}, backgroundColor: '#3D5A80' }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});
</script>
</body>
</html>
"""
    filename = f"dashboard_all_markets_{today().isoformat()}.html"
    return filename, html
