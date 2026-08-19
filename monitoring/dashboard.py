import json
from datetime import date, timedelta

from config.timeutil import today
from monitoring.calculations import compute_market_capacity, compute_share, is_anomaly
from monitoring.competitors import list_competitors
from monitoring.constants import ANOMALY_WINDOW_READINGS, COMPETITOR_FORMATS, MONITORING_CYCLE_DAYS
from monitoring.factor_schema import FACTOR_BLOCKS, render_block_lines
from monitoring.factors import get_latest_factors
from monitoring.managers import get_managers_for_market
from monitoring.markets import get_market, list_markets
from monitoring.observations import get_observations
from monitoring.readings import get_last_reading_dates_by_creator, get_latest_reading, get_readings

_OWN_COLOR = "#2E8B7A"
_COMPETITOR_PALETTE = ["#E07A5F", "#3D5A80", "#F2CC8F", "#81B29A", "#9B5DE5", "#F4845F", "#577590", "#B56576"]
_FORMAT_PALETTE = ["#3D5A80", "#E07A5F", "#81B29A"]

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


def _wow_delta(timeline: list[dict]) -> dict | None:
    if len(timeline) < 2 or timeline[-2]["capacity"] <= 0:
        return None
    current, previous = timeline[-1]["capacity"], timeline[-2]["capacity"]
    delta_pct = (current - previous) / previous * 100
    return {"delta_pct": delta_pct, "direction": "up" if delta_pct >= 0 else "down"}


def _format_distribution(active_competitors: list[dict]) -> dict[str, int]:
    counts = {fmt: 0 for fmt in COMPETITOR_FORMATS}
    for c in active_competitors:
        counts[c["format"]] = counts.get(c["format"], 0) + 1
    return counts


def _ranking_rows(series: list[dict], timeline: list[dict]) -> list[dict]:
    if not timeline:
        return []

    def ranks_at(point: dict) -> dict[int, int]:
        ranked = sorted(point["shares"].items(), key=lambda kv: kv[1], reverse=True)
        return {cid: i + 1 for i, (cid, _) in enumerate(ranked)}

    current_ranks = ranks_at(timeline[-1])
    previous_ranks = ranks_at(timeline[-2]) if len(timeline) >= 2 else {}

    rows = []
    for s in series:
        cid = s["competitor"]["id"]
        if cid not in current_ranks:
            continue
        prev_place = previous_ranks.get(cid)
        place = current_ranks[cid]
        rows.append(
            {
                "competitor": s["competitor"],
                "place": place,
                "share": round(timeline[-1]["shares"].get(cid, 0), 1),
                "prev_place": prev_place,
                "delta": (prev_place - place) if prev_place is not None else None,
            }
        )
    rows.sort(key=lambda r: r["place"])
    return rows


def _freshness_rows(active_competitors: list[dict]) -> tuple[list[dict], int]:
    cutoff = (today() - timedelta(days=MONITORING_CYCLE_DAYS)).isoformat()
    rows = []
    ok_count = 0
    for c in active_competitors:
        latest = get_latest_reading(c["id"])
        if latest:
            days_since = (today() - date.fromisoformat(latest["reading_at"])).days
            overdue = latest["reading_at"] < cutoff
        else:
            days_since = None
            overdue = True
        if not overdue:
            ok_count += 1
        rows.append(
            {
                "competitor": c,
                "last_date": latest["reading_at"] if latest else None,
                "days_since": days_since,
                "overdue": overdue,
            }
        )
    return rows, ok_count


def _manager_activity_rows(market_id: int) -> list[dict]:
    last_dates = get_last_reading_dates_by_creator(market_id)
    rows = []
    for m in get_managers_for_market(market_id):
        if m["status"] != "active":
            continue
        rows.append({"name": m["name"], "position": m["position"] or "—", "last_date": last_dates.get(m["telegram_user_id"])})
    rows.sort(key=lambda r: r["last_date"] or "", reverse=True)
    return rows


# ---------- HTML-рендеринг новых блоков ----------


def _render_wow_card(wow: dict | None) -> str:
    if not wow:
        return '<div class="card"><div class="value">—</div><div class="label">Изменение к пред. снятию (недостаточно данных)</div></div>'
    arrow = "▲" if wow["direction"] == "up" else "▼"
    cls = "delta-up" if wow["direction"] == "up" else "delta-down"
    return (
        f'<div class="card"><div class="value {cls}">{arrow} {abs(wow["delta_pct"]):.1f}%</div>'
        '<div class="label">Изменение ёмкости к пред. снятию</div></div>'
    )


def _render_format_section(counts: dict[str, int]) -> str:
    if not any(counts.values()):
        return '<div class="notice">Нет активных конкурентов с указанным форматом.</div>'
    rows = "".join(f"<tr><td>{fmt}</td><td>{n}</td></tr>" for fmt, n in counts.items() if n)
    return f'<table><thead><tr><th>Формат</th><th>Точек</th></tr></thead><tbody>{rows}</tbody></table><div class="chart-wrap"><canvas id="formatChart"></canvas></div>'


def _render_ranking_table(rows: list[dict]) -> str:
    if not rows:
        return '<div class="notice">Пока недостаточно данных для рейтинга.</div>'
    body = []
    for r in rows:
        c = r["competitor"]
        if r["delta"] is None:
            delta_html = "—"
        elif r["delta"] > 0:
            delta_html = f'<span class="delta-up">▲ {r["delta"]}</span>'
        elif r["delta"] < 0:
            delta_html = f'<span class="delta-down">▼ {abs(r["delta"])}</span>'
        else:
            delta_html = "="
        own_tag = " (наша точка)" if c["is_own"] else ""
        body.append(
            f"<tr><td>{r['place']}</td><td>{c['code']} — {c['name']}{own_tag}</td>"
            f"<td>{r['share']:g}%</td><td>{delta_html}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Место</th><th>Точка</th><th>Доля</th><th>Δ места</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _render_factors_profile(series: list[dict]) -> str:
    cards = []
    for s in series:
        c = s["competitor"]
        factors = get_latest_factors(c["id"])
        own_tag = " (наша точка)" if c["is_own"] else ""
        if not factors:
            cards.append(f'<div class="factor-card"><h3>{c["code"]} — {c["name"]}{own_tag}</h3><div class="factor-row">Факторы ещё не заполнены.</div></div>')
            continue
        block_sections = []
        for block_key, block_title, _ in FACTOR_BLOCKS:
            lines = render_block_lines(block_key, factors.get(block_key))
            if not lines:
                continue
            rows = "".join(f'<div class="factor-row"><b>{label}:</b> {value}</div>' for label, value in lines)
            block_sections.append(f'<div class="factor-block-title">{block_title}</div>{rows}')
        body = "".join(block_sections) or '<div class="factor-row">Факторы ещё не заполнены.</div>'
        cards.append(f'<div class="factor-card"><h3>{c["code"]} — {c["name"]}{own_tag}</h3>{body}</div>')
    return "".join(cards)


def _render_observations_feed(market_id: int, series: list[dict], limit: int = 50) -> str:
    competitors_by_id = {s["competitor"]["id"]: s["competitor"] for s in series}
    obs = get_observations(market_id)
    if not obs:
        return '<div class="notice">Наблюдений по этому рынку пока нет.</div>'
    items = []
    for o in obs[:limit]:
        c = competitors_by_id.get(o["competitor_id"])
        label = f"{c['code']} — {c['name']}" if c else "—"
        text = f": {o['text']}" if o["text"] else ""
        items.append(
            f'<div class="obs-item"><div class="meta">{o["observed_at"][:10]} · {label} · {o["category"]}</div>{text}</div>'
        )
    more_note = f'<div class="notice">Показаны последние {limit} из {len(obs)} наблюдений.</div>' if len(obs) > limit else ""
    return "".join(items) + more_note


def _render_freshness_section(rows: list[dict], ok_count: int, total: int) -> str:
    if not rows:
        return '<div class="notice">На рынке пока нет активных точек.</div>'
    summary = f'<div class="notice">{ok_count} из {total} точек мониторятся вовремя (снятие не старше {MONITORING_CYCLE_DAYS} дней).</div>'
    body = []
    for r in rows:
        c = r["competitor"]
        badge = '<span class="badge overdue">просрочено</span>' if r["overdue"] else '<span class="badge ok">в порядке</span>'
        last = r["last_date"] or "нет данных"
        days = f'{r["days_since"]} дн. назад' if r["days_since"] is not None else "—"
        body.append(f"<tr><td>{c['code']} — {c['name']}</td><td>{last}</td><td>{days}</td><td>{badge}</td></tr>")
    table = (
        "<table><thead><tr><th>Точка</th><th>Последнее снятие</th><th>Давность</th><th>Статус</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )
    return summary + table


def _render_closed_section(closed: list[dict]) -> str:
    if not closed:
        return '<div class="notice">Закрывшихся точек на этом рынке нет.</div>'
    body = "".join(
        f"<tr><td>{c['code']} — {c['name']}</td><td>{c.get('closed_at') or '—'}</td></tr>" for c in closed
    )
    return f"<table><thead><tr><th>Точка</th><th>Закрыта</th></tr></thead><tbody>{body}</tbody></table>"


def _render_manager_activity(rows: list[dict]) -> str:
    if not rows:
        return '<div class="notice">На этом рынке пока нет подтверждённых менеджеров.</div>'
    body = "".join(
        f"<tr><td>{r['name']}</td><td>{r['position']}</td><td>{r['last_date'] or 'ещё не проводил(а)'}</td></tr>"
        for r in rows
    )
    return f"<table><thead><tr><th>Менеджер</th><th>Роль</th><th>Последний мониторинг</th></tr></thead><tbody>{body}</tbody></table>"


def _render_summary_table(series: list[dict], timeline: list[dict]) -> str:
    if not timeline:
        return '<div class="notice">Пока нет ни одного снятия.</div>'
    now_point = timeline[-1]
    body = []
    for s in series:
        c = s["competitor"]
        value = now_point["values"].get(c["id"])
        share = now_point["shares"].get(c["id"])
        own_tag = " (наша точка)" if c["is_own"] else ""
        status = "закрыт" if c["status"] == "closed" else "активен"
        value_cell = f"{value:g} чек/день" if value is not None else "нет данных"
        share_cell = f"{share:.1f}%" if share is not None else "—"
        body.append(
            f"<tr><td>{c['code']}</td><td>{c['name']}{own_tag}</td><td>{c['format']}</td>"
            f"<td>{value_cell}</td><td>{share_cell}</td><td>{status}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Код</th><th>Название</th><th>Формат</th><th>Показатель</th>"
        f"<th>Доля</th><th>Статус</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


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
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #eee; }}
  th {{ color: #666; font-weight: 600; font-size: 12px; text-transform: uppercase; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; }}
  .badge.ok {{ background: #e3f4ee; color: #1f6f5c; }}
  .badge.overdue {{ background: #fdeceb; color: #a13c30; }}
  .obs-item {{ background: #fff; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; font-size: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .obs-item .meta {{ color: #888; font-size: 12px; margin-bottom: 4px; }}
  .factor-card {{ background: #fff; border-radius: 12px; padding: 14px 18px; margin-bottom: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .factor-card h3 {{ margin: 0 0 8px; font-size: 15px; }}
  .factor-block-title {{ font-size: 12px; font-weight: 600; text-transform: uppercase; color: #888; margin: 10px 0 2px; }}
  .factor-block-title:first-of-type {{ margin-top: 0; }}
  .factor-row {{ font-size: 13px; margin: 3px 0; color: #333; }}
  .factor-row b {{ color: #555; }}
  .delta-up {{ color: #2E8B7A; }}
  .delta-down {{ color: #E07A5F; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="subtitle">{subtitle}</div>

{insufficient_data_banner}

<div class="cards">
  <div class="card"><div class="value">{capacity_now:g}</div><div class="label">Ёмкость рынка, чек/день (≈{capacity_week:g} чек/нед.)</div></div>
  {wow_card}
</div>

<h2>1. Ёмкость рынка во времени</h2>
<div class="chart-wrap"><canvas id="capacityChart"></canvas></div>

<h2>2. Доля рынка по каждому конкуренту (последний период)</h2>
<div class="chart-wrap"><canvas id="shareNowChart"></canvas></div>

<h2>Распределение по формату</h2>
{format_html}

<h2>3. Тенденция доли — за всё время</h2>
<div class="chart-wrap"><canvas id="shareAllChart"></canvas></div>

<h2>4. Тенденция доли — последние 3 месяца</h2>
<div class="chart-wrap"><canvas id="share3mChart"></canvas></div>

<h2>5. Тенденция доли — последний месяц</h2>
<div class="chart-wrap"><canvas id="share1mChart"></canvas></div>

<h2>Рейтинг точек и его динамика</h2>
{ranking_html}

<h2>6. Явные изменения, возможные причины и рекомендации</h2>
{anomalies_html}

<h2>Профили конкурентов (факторы формирования)</h2>
{factors_html}

<h2>Лента наблюдений</h2>
{observations_html}

<h2>Свежесть данных / здоровье мониторинга</h2>
{freshness_html}

<h2>Закрывшиеся точки</h2>
{closed_html}

<h2>Активность менеджеров рынка</h2>
{manager_activity_html}

<h2>Сводная таблица</h2>
{summary_html}

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
const formatLabels = {format_labels};
const formatData = {format_data};
const formatColors = {format_colors};

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

if (formatLabels.length) {{
  new Chart(document.getElementById('formatChart'), {{
    type: 'pie',
    data: {{ labels: formatLabels, datasets: [{{ data: formatData, backgroundColor: formatColors }}] }}
  }});
}}

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
    active_competitors = list_competitors(market_id)

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

    format_counts = _format_distribution(active_competitors)
    format_items = [(fmt, n) for fmt, n in format_counts.items() if n]
    freshness_rows, freshness_ok = _freshness_rows(active_competitors)
    closed_competitors = [c for c in list_competitors(market_id, include_closed=True) if c["status"] == "closed"]

    html = _HTML_TEMPLATE.format(
        title=f"Дашборд рынка «{market['name']}»",
        subtitle=f"Наша точка: {market['our_point_name']}. Обновлено {today().isoformat()}.",
        insufficient_data_banner=insufficient_banner,
        capacity_now=capacity_now,
        capacity_week=capacity_now * 7,
        wow_card=_render_wow_card(_wow_delta(timeline)),
        format_html=_render_format_section(format_counts),
        ranking_html=_render_ranking_table(_ranking_rows(series, timeline)),
        factors_html=_render_factors_profile(series),
        observations_html=_render_observations_feed(market_id, series),
        freshness_html=_render_freshness_section(freshness_rows, freshness_ok, len(active_competitors)),
        closed_html=_render_closed_section(closed_competitors),
        manager_activity_html=_render_manager_activity(_manager_activity_rows(market_id)),
        summary_html=_render_summary_table(series, timeline),
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
        format_labels=json.dumps([fmt for fmt, _ in format_items], ensure_ascii=False),
        format_data=json.dumps([n for _, n in format_items]),
        format_colors=json.dumps(_FORMAT_PALETTE[: len(format_items)]),
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
<div class="cdn-note">Для детальной аналитики (тенденции, аномалии, причины, факторы, наблюдения) по конкретному рынку используйте /dashboard_market и выберите рынок. Для отображения графика нужен доступ в интернет.</div>
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
