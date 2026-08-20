import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

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

_CHART_JS_PATH = Path(__file__).resolve().parent / "vendor" / "chart.umd.min.js"


@lru_cache(maxsize=1)
def _chart_js() -> str:
    """Chart.js встроен прямо в файл дашборда, а не грузится с CDN — при
    открытии .html-документа в Telegram (особенно на мобильных, через
    системный просмотрщик) сторонние скрипты часто блокируются, и графики
    на CDN-версии выходили пустыми."""
    return _CHART_JS_PATH.read_text()


_OWN_COLOR = "#C1622D"
_COMPETITOR_PALETTE = ["#A97155", "#6B8E4E", "#D4A24C", "#8C6E54", "#C97B63", "#7A9E7E", "#B0846A", "#9C8265"]
_FORMAT_PALETTE = ["#C1622D", "#6B8E4E", "#D4A24C"]

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


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _line_style(is_own: bool, color: str) -> dict:
    """Своя точка (Surf) — фокус графика: полный цвет, линия вдвое толще,
    заметные точки на срезах. Конкуренты — тоньше и полупрозрачнее, без
    точек, чтобы не спорили за внимание с нашей линией."""
    if is_own:
        return {
            "borderColor": color,
            "backgroundColor": color,
            "pointBackgroundColor": color,
            "borderWidth": 4,
            "pointRadius": 2,
            "pointHoverRadius": 5,
        }
    return {
        "borderColor": _hex_to_rgba(color, 0.55),
        "backgroundColor": _hex_to_rgba(color, 0.55),
        "borderWidth": 1.5,
        "pointRadius": 0,
        "pointHoverRadius": 3,
    }


def _ordered_for_charts(series: list[dict]) -> list[dict]:
    """Рисуем свою точку последней в массиве datasets — в Chart.js более
    поздние datasets перекрывают более ранние на пересечениях линий, так
    наша точка всегда поверх конкурентов, а не под ними."""
    return sorted(series, key=lambda s: bool(s["competitor"]["is_own"]))


def _dataset_for(series_item: dict, timeline: list[dict], color: str) -> dict:
    """Ряд % доли рынка конкурента по датам timeline."""
    competitor = series_item["competitor"]
    data = []
    for point in timeline:
        share = point["shares"].get(competitor["id"])
        data.append(round(share, 1) if share is not None else None)
    return {
        "label": competitor["name"],
        "data": data,
        "spanGaps": False,
        "tension": 0.2,
        **_line_style(bool(competitor["is_own"]), color),
    }


def _raw_dataset_for(series_item: dict, timeline: list[dict], color: str) -> dict:
    """Ряд сырого показателя (чек/день, без нормировки в %) конкурента по
    датам timeline — для Раздела 1, хронологии всех точек рынка разом."""
    competitor = series_item["competitor"]
    data = []
    for point in timeline:
        value = point["values"].get(competitor["id"])
        data.append(round(value, 1) if value is not None else None)
    return {
        "label": competitor["name"],
        "data": data,
        "spanGaps": False,
        "tension": 0.2,
        **_line_style(bool(competitor["is_own"]), color),
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


def _hero_stats(timeline: list[dict], active_competitors: list[dict], own_competitor: dict | None) -> dict:
    dates = [p["date"] for p in timeline]
    return {
        "period_from": dates[0] if dates else None,
        "period_to": dates[-1] if dates else None,
        "snapshots": len(timeline),
        "points_total": len(active_competitors),
        "own_count": 1 if own_competitor else 0,
    }


def _own_point_summary(series: list[dict], timeline: list[dict], timeline_3m: list[dict]) -> dict | None:
    own_item = next((s for s in series if s["competitor"]["is_own"]), None)
    if not own_item:
        return None

    own_id = own_item["competitor"]["id"]
    cutoff_3m = timeline_3m[0]["date"] if timeline_3m else None
    readings_3m = [r for r in own_item["readings"] if cutoff_3m and r["reading_at"] >= cutoff_3m]
    avg_3m = sum(r["avg_checks_per_day"] for r in readings_3m) / len(readings_3m) if readings_3m else None

    now_point = timeline[-1] if timeline else None
    current_value = now_point["values"].get(own_id) if now_point else None
    current_share = now_point["shares"].get(own_id) if now_point else None
    share_start = timeline_3m[0]["shares"].get(own_id) if timeline_3m else None
    share_delta = (current_share - share_start) if (current_share is not None and share_start is not None) else None

    return {
        "competitor": own_item["competitor"],
        "readings_count": len(readings_3m),
        "avg_3m": avg_3m,
        "current_value": current_value,
        "current_share": current_share,
        "share_delta": share_delta,
    }


def _own_narrative(wow: dict | None, own_summary: dict | None) -> str:
    if not own_summary:
        return "На этом рынке пока не добавлена наша точка (Surf) — как читать раздел появится после первых снятий."
    parts = []
    if own_summary["current_share"] is not None:
        if own_summary["share_delta"] is not None:
            direction = "выросла" if own_summary["share_delta"] >= 0 else "снизилась"
            parts.append(
                f"Доля точки Surf за последние 3 месяца {direction} на {abs(own_summary['share_delta']):.1f} п.п. "
                f"и сейчас составляет {own_summary['current_share']:.1f}%."
            )
        else:
            parts.append(f"Текущая доля точки Surf на рынке — {own_summary['current_share']:.1f}%.")
    else:
        parts.append("Пока нет снятий по своей точке, чтобы оценить долю рынка.")
    if wow:
        direction = "выросла" if wow["direction"] == "up" else "снизилась"
        parts.append(f"Ёмкость рынка в целом {direction} на {abs(wow['delta_pct']):.1f}% к предыдущему снятию.")
    return " ".join(parts)


# ---------- HTML-рендеринг блоков ----------


def _render_hero_cards(hero: dict, capacity_now: float, wow_card_html: str) -> str:
    period_cell = f"{hero['period_from']} → {hero['period_to']}" if hero["period_from"] else "—"
    return (
        f'<div class="card"><div class="value">{period_cell}</div><div class="label">Период наблюдений</div></div>'
        f'<div class="card"><div class="value">{hero["snapshots"]}</div><div class="label">Снятий (срезов рынка)</div></div>'
        f'<div class="card"><div class="value">{hero["points_total"]}</div><div class="label">Точек на рынке</div></div>'
        f'<div class="card"><div class="value">{hero["own_count"]} из {hero["points_total"] or 0}</div><div class="label">Наша точка</div></div>'
        f'<div class="card"><div class="value">{capacity_now:g}</div><div class="label">Ёмкость рынка, чек/день</div></div>'
        f"{wow_card_html}"
    )


def _render_wow_card(wow: dict | None) -> str:
    if not wow:
        return '<div class="card"><div class="value">—</div><div class="label">Изменение к пред. снятию (недостаточно данных)</div></div>'
    arrow = "▲" if wow["direction"] == "up" else "▼"
    cls = "delta-up" if wow["direction"] == "up" else "delta-down"
    return (
        f'<div class="card"><div class="value {cls}">{arrow} {abs(wow["delta_pct"]):.1f}%</div>'
        '<div class="label">Изменение ёмкости к пред. снятию</div></div>'
    )


def _render_own_summary(own_summary: dict | None) -> str:
    if not own_summary:
        return ""
    c = own_summary["competitor"]
    avg_cell = f"{own_summary['avg_3m']:.1f} чек/день" if own_summary["avg_3m"] is not None else "—"
    current_cell = f"{own_summary['current_value']:g} чек/день" if own_summary["current_value"] is not None else "—"
    share_cell = f"{own_summary['current_share']:.1f}%" if own_summary["current_share"] is not None else "—"
    return (
        "<table><tbody>"
        f"<tr><td>Точка</td><td>{c['code']} — {c['name']}</td></tr>"
        f"<tr><td>Снятий за 3 месяца</td><td>{own_summary['readings_count']}</td></tr>"
        f"<tr><td>Средний показатель за 3 месяца</td><td>{avg_cell}</td></tr>"
        f"<tr><td>Последнее снятие</td><td>{current_cell}</td></tr>"
        f"<tr><td>Текущая доля рынка</td><td>{share_cell}</td></tr>"
        "</tbody></table>"
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
        own_tag = ' <span class="own-tag">(наша точка)</span>' if c["is_own"] else ""
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
        own_tag = ' <span class="own-tag">(наша точка)</span>' if c["is_own"] else ""
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
    return f'<div class="card-grid">{"".join(cards)}</div>'


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
    return f'<div class="feed">{"".join(items)}</div>{more_note}'


def _render_freshness_section(rows: list[dict], ok_count: int, total: int) -> str:
    if not rows:
        return '<div class="notice">На рынке пока нет активных точек.</div>'
    summary = f'<div class="notice">{ok_count} из {total} точек мониторятся вовремя (снятие не старше {MONITORING_CYCLE_DAYS} дней).</div>'
    body = []
    for r in rows:
        c = r["competitor"]
        badge = '<span class="badge overdue">просрочено</span>' if r["overdue"] else '<span class="badge ok">в порядке</span>'
        last = r["last_date"] or "нет данных"
        if r["days_since"] is None:
            days = "—"
        elif r["days_since"] < 0:
            days = "⚠️ дата в будущем"
        else:
            days = f'{r["days_since"]} дн. назад'
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
        own_tag = ' <span class="own-tag">(наша точка)</span>' if c["is_own"] else ""
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
    return f'<div class="card-grid">{"".join(blocks)}</div>'


_BASE_STYLE_CSS = """
  :root {
    --bg: #FAF6F1;
    --surface: #FFFDFB;
    --border: #EDE2D6;
    --text: #3B2E27;
    --text-muted: #8A7A6D;
    --accent: #C1622D;
    --accent-soft: #F3E4D3;
    --positive: #6B8E4E;
    --positive-soft: #EAF1E3;
    --negative: #B5533C;
    --negative-soft: #FBEAE3;
  }
  * { box-sizing: border-box; }
  body { font-family: "PT Sans", -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; padding: 24px; background: var(--bg); color: var(--text); font-variant-numeric: tabular-nums; }
  h1, h2, h3 { font-family: "Golos Text", "PT Sans", -apple-system, sans-serif; text-wrap: balance; }
  h1 { font-size: 24px; font-weight: 700; margin-bottom: 4px; }
  h2 { font-size: 17px; font-weight: 600; margin-top: 36px; margin-bottom: 12px; }
  h2 .fig { color: var(--accent); font-weight: 700; margin-right: 6px; }
  h3 { font-size: 14px; font-weight: 600; margin: 0 0 8px; }
  .subtitle { color: var(--text-muted); margin-bottom: 20px; }
  .cards { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 14px 18px; min-width: 160px; box-shadow: 0 2px 6px rgba(120, 72, 32, .05); }
  .card .value { font-size: 20px; font-weight: 700; font-family: "Golos Text", sans-serif; }
  .card .label { color: var(--text-muted); font-size: 12px; margin-top: 2px; }
  .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 16px; margin-bottom: 8px; align-items: start; }
  .card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
  .chart-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 16px; margin-bottom: 8px; box-shadow: 0 2px 6px rgba(120, 72, 32, .05); }
  canvas { max-height: 320px; }
  .notice { background: var(--accent-soft); border: 1px solid #E8C68A; border-radius: 12px; padding: 12px 16px; margin: 12px 0; font-size: 14px; }
  .narrative { font-size: 14px; line-height: 1.6; margin-bottom: 10px; color: var(--text); }
  .anomaly { background: var(--surface); border: 1px solid var(--border); border-left: 4px solid var(--negative); border-radius: 12px; padding: 12px 16px; box-shadow: 0 2px 6px rgba(120, 72, 32, .05); }
  .anomaly.up { border-left-color: var(--positive); }
  .anomaly .meta { color: var(--text-muted); font-size: 13px; margin-bottom: 6px; }
  .anomaly .rec { margin-top: 8px; font-size: 14px; }
  .anomaly .causes { margin-top: 6px; font-size: 13px; color: var(--text); }
  .cdn-note { color: var(--text-muted); font-size: 12px; margin-top: 40px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; background: var(--surface); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }
  th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  th { color: var(--text-muted); font-weight: 700; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
  .badge.ok { background: var(--positive-soft); color: #4C6B3C; }
  .badge.overdue { background: var(--negative-soft); color: var(--negative); }
  .feed { max-width: 640px; }
  .obs-item { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 10px 14px; margin-bottom: 8px; font-size: 14px; }
  .obs-item .meta { color: var(--text-muted); font-size: 12px; margin-bottom: 4px; }
  .factor-card { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 14px 18px; box-shadow: 0 2px 6px rgba(120, 72, 32, .05); }
  .factor-block-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .03em; color: var(--accent); margin: 10px 0 2px; }
  .factor-block-title:first-of-type { margin-top: 0; }
  .factor-row { font-size: 13px; margin: 3px 0; color: var(--text); }
  .factor-row b { color: var(--text-muted); }
  .delta-up { color: var(--positive); }
  .delta-down { color: var(--negative); }
  .own-tag { color: var(--accent); font-weight: 600; }
  .period-toggle { display: flex; gap: 8px; margin-bottom: 12px; }
  .toggle-btn { font-family: "PT Sans", sans-serif; border: 1px solid var(--border); background: var(--surface); border-radius: 999px; padding: 6px 16px; font-size: 13px; cursor: pointer; color: var(--text-muted); transition: background .15s, color .15s, border-color .15s; }
  .toggle-btn:hover { border-color: var(--accent); color: var(--accent); }
  .toggle-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .market-name { font-weight: 600; }
  .attention-row { background: var(--negative-soft); }
"""


_HTML_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script>
{chart_js}
</script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@500;600;700&family=PT+Sans:wght@400;700&display=swap">
<style>
{base_style}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="subtitle">{subtitle}</div>

{insufficient_data_banner}

<div class="cards">
  {hero_cards}
</div>

<div class="grid-2">
  <div>
    <h2><span class="fig">Раздел 1</span>Хронология показателей всех точек рынка</h2>
    <div class="chart-wrap"><canvas id="rawTimelineChart"></canvas></div>
  </div>
  <div>
    <h2><span class="fig">Раздел 2</span>Точка Surf — сводка периода</h2>
    <div class="narrative">{own_narrative}</div>
    {own_summary_html}
  </div>
</div>

<h2><span class="fig">Раздел 3</span>Ёмкость рынка и доля сейчас</h2>
<div class="grid-2">
  <div class="chart-wrap"><canvas id="capacityChart"></canvas></div>
  <div class="chart-wrap"><canvas id="shareNowChart"></canvas></div>
</div>

<div class="grid-2">
  <div>
    <h2><span class="fig">Раздел 4</span>Доля рынка во времени</h2>
    <div class="period-toggle">
      <button class="toggle-btn" data-period="1m">Месяц</button>
      <button class="toggle-btn" data-period="3m">3 месяца</button>
      <button class="toggle-btn active" data-period="all">Всё время</button>
    </div>
    <div class="chart-wrap"><canvas id="shareTrendChart"></canvas></div>
  </div>
  <div>
    <h2>Распределение по формату</h2>
    {format_html}
  </div>
</div>

<div class="grid-2">
  <div>
    <h2>Рейтинг точек</h2>
    {ranking_html}
  </div>
  <div>
    <h2>Явные изменения, возможные причины и рекомендации</h2>
    {anomalies_html}
  </div>
</div>

<h2>Профили конкурентов (факторы формирования)</h2>
{factors_html}

<h2>Лента наблюдений</h2>
{observations_html}

<div class="grid-2">
  <div>
    <h2>Свежесть данных / здоровье мониторинга</h2>
    {freshness_html}
  </div>
  <div>
    <h2>Активность менеджеров рынка</h2>
    {manager_activity_html}
  </div>
</div>

<div class="grid-2">
  <div>
    <h2>Закрывшиеся точки</h2>
    {closed_html}
  </div>
  <div></div>
</div>

<h2>Сводная таблица</h2>
{summary_html}

<div class="cdn-note">Графики интерактивны — наведите курсор на точки.</div>

<script>
const rawLabels = {raw_labels};
const rawDatasets = {raw_datasets};
const capacityLabels = {capacity_labels};
const capacityData = {capacity_data};
const shareTrendData = {{
  '1m': {{ labels: {share_labels_1m}, datasets: {share_datasets_1m} }},
  '3m': {{ labels: {share_labels_3m}, datasets: {share_datasets_3m} }},
  'all': {{ labels: {share_labels_all}, datasets: {share_datasets_all} }},
}};
const shareNowLabels = {share_now_labels};
const shareNowData = {share_now_data};
const shareNowColors = {share_now_colors};
const formatLabels = {format_labels};
const formatData = {format_data};
const formatColors = {format_colors};

const lineEndLabelsPlugin = {{
  id: 'lineEndLabels',
  afterDraw(chart) {{
    const ctx = chart.ctx;
    const entries = [];
    chart.data.datasets.forEach((dataset, i) => {{
      const meta = chart.getDatasetMeta(i);
      if (meta.hidden) return;
      let lastIndex = dataset.data.length - 1;
      while (lastIndex >= 0 && (dataset.data[lastIndex] === null || dataset.data[lastIndex] === undefined)) lastIndex--;
      if (lastIndex < 0) return;
      const point = meta.data[lastIndex];
      if (!point) return;
      entries.push({{ label: dataset.label, x: point.x, y: point.y, color: dataset.borderColor, own: dataset.borderWidth >= 3 }});
    }});
    entries.sort((a, b) => a.y - b.y);
    const minGap = 15;
    for (let i = 1; i < entries.length; i++) {{
      if (entries[i].y - entries[i - 1].y < minGap) entries[i].y = entries[i - 1].y + minGap;
    }}
    ctx.save();
    ctx.textBaseline = 'middle';
    entries.forEach(e => {{
      ctx.font = (e.own ? 'bold 12px' : '11px') + ' "PT Sans", -apple-system, sans-serif';
      ctx.fillStyle = e.color;
      ctx.fillText(e.label, e.x + 8, e.y);
    }});
    ctx.restore();
  }}
}};

new Chart(document.getElementById('rawTimelineChart'), {{
  type: 'line',
  data: {{ labels: rawLabels, datasets: rawDatasets }},
  plugins: [lineEndLabelsPlugin],
  options: {{
    layout: {{ padding: {{ right: 130 }} }},
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ beginAtZero: true, title: {{ display: true, text: 'Чек/день' }} }} }}
  }}
}});

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

const shareTrendChart = new Chart(document.getElementById('shareTrendChart'), {{
  type: 'line',
  data: shareTrendData['all'],
  plugins: [lineEndLabelsPlugin],
  options: {{
    layout: {{ padding: {{ right: 130 }} }},
    plugins: {{ legend: {{ display: false }} }},
    scales: {{ y: {{ beginAtZero: true, max: 100, title: {{ display: true, text: 'Доля, %' }} }} }}
  }}
}});
document.querySelectorAll('.toggle-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    shareTrendChart.data = shareTrendData[btn.dataset.period];
    shareTrendChart.update();
  }});
}});
</script>
</body>
</html>
"""


def generate_market_dashboard(market_id: int) -> tuple[str, str]:
    market = get_market(market_id)
    series = _load_competitor_series(market_id)
    timeline = _build_capacity_timeline(series)
    active_competitors = list_competitors(market_id)
    own_competitor = next((c for c in active_competitors if c["is_own"]), None)

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

    ordered_series = _ordered_for_charts(series)

    def datasets_for(tl):
        return [_dataset_for(s, tl, colors[s["competitor"]["id"]]) for s in ordered_series]

    def raw_datasets_for(tl):
        return [_raw_dataset_for(s, tl, colors[s["competitor"]["id"]]) for s in ordered_series]

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

    wow = _wow_delta(timeline)
    own_summary = _own_point_summary(series, timeline, timeline_3m)
    hero = _hero_stats(timeline, active_competitors, own_competitor)

    html = _HTML_TEMPLATE.format(
        base_style=_BASE_STYLE_CSS,
        chart_js=_chart_js(),
        title=f"Дашборд рынка «{market['name']}»",
        subtitle=f"Наша точка: {market['our_point_name']}. Обновлено {today().isoformat()}.",
        insufficient_data_banner=insufficient_banner,
        hero_cards=_render_hero_cards(hero, capacity_now, _render_wow_card(wow)),
        own_narrative=_own_narrative(wow, own_summary),
        own_summary_html=_render_own_summary(own_summary),
        format_html=_render_format_section(format_counts),
        ranking_html=_render_ranking_table(_ranking_rows(series, timeline)),
        factors_html=_render_factors_profile(series),
        observations_html=_render_observations_feed(market_id, series),
        freshness_html=_render_freshness_section(freshness_rows, freshness_ok, len(active_competitors)),
        closed_html=_render_closed_section(closed_competitors),
        manager_activity_html=_render_manager_activity(_manager_activity_rows(market_id)),
        summary_html=_render_summary_table(series, timeline),
        raw_labels=json.dumps([p["date"] for p in timeline], ensure_ascii=False),
        raw_datasets=json.dumps(raw_datasets_for(timeline), ensure_ascii=False),
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


def _market_summary_row(market: dict) -> dict:
    series = _load_competitor_series(market["id"])
    timeline = _build_capacity_timeline(series)
    active_competitors = list_competitors(market["id"])
    own_competitor = next((c for c in active_competitors if c["is_own"]), None)

    capacity_now = timeline[-1]["capacity"] if timeline else 0.0
    now_point = timeline[-1] if timeline else {"shares": {}}
    own_share = now_point["shares"].get(own_competitor["id"]) if own_competitor else None
    wow = _wow_delta(timeline)

    _, freshness_ok = _freshness_rows(active_competitors)

    recent_cutoff = (today() - timedelta(days=MONITORING_CYCLE_DAYS)).isoformat()
    competitor_anomalies = _detect_competitor_anomalies(series, market["id"])
    market_anomalies = _detect_market_anomalies(timeline)
    recent_anomalies = [a for a in competitor_anomalies if a["date"] >= recent_cutoff]
    recent_anomalies += [a for a in market_anomalies if a["date"] >= recent_cutoff]

    return {
        "market": market,
        "capacity_now": capacity_now,
        "own_competitor": own_competitor,
        "own_share": own_share,
        "wow": wow,
        "points_total": len(active_competitors),
        "freshness_ok": freshness_ok,
        "recent_anomalies_count": len(recent_anomalies),
    }


def _render_aggregate_hero(rows: list[dict]) -> str:
    markets_total = len(rows)
    points_total = sum(r["points_total"] for r in rows)
    needs_attention = sum(1 for r in rows if r["freshness_ok"] < r["points_total"])
    anomalies_total = sum(r["recent_anomalies_count"] for r in rows)
    return (
        f'<div class="card"><div class="value">{markets_total}</div><div class="label">Рынков под наблюдением</div></div>'
        f'<div class="card"><div class="value">{points_total}</div><div class="label">Точек всего</div></div>'
        f'<div class="card"><div class="value">{needs_attention}</div><div class="label">Рынков с просроченным мониторингом</div></div>'
        f'<div class="card"><div class="value">{anomalies_total}</div><div class="label">Аномалий за последние {MONITORING_CYCLE_DAYS} дней</div></div>'
    )


def _render_aggregate_table(rows: list[dict]) -> str:
    if not rows:
        return '<div class="notice">Пока нет ни одного рынка.</div>'
    body = []
    for r in rows:
        market = r["market"]
        own_cell = r["own_competitor"]["name"] if r["own_competitor"] else "не добавлена"
        share_cell = f"{r['own_share']:.1f}%" if r["own_share"] is not None else "—"
        if r["wow"]:
            arrow = "▲" if r["wow"]["direction"] == "up" else "▼"
            cls = "delta-up" if r["wow"]["direction"] == "up" else "delta-down"
            wow_cell = f'<span class="{cls}">{arrow} {abs(r["wow"]["delta_pct"]):.1f}%</span>'
        else:
            wow_cell = "—"
        overdue = r["freshness_ok"] < r["points_total"]
        freshness_cell = (
            f'<span class="badge overdue">{r["freshness_ok"]}/{r["points_total"]}</span>'
            if overdue
            else f'<span class="badge ok">{r["freshness_ok"]}/{r["points_total"]}</span>'
        )
        anomalies_cell = r["recent_anomalies_count"] or "—"
        row_class = ' class="attention-row"' if overdue else ""
        body.append(
            f"<tr{row_class}><td class=\"market-name\">{market['name']}</td><td>{own_cell}</td><td>{share_cell}</td>"
            f"<td>{wow_cell}</td><td>{r['capacity_now']:g} чек/день</td><td>{r['points_total']}</td>"
            f"<td>{freshness_cell}</td><td>{anomalies_cell}</td></tr>"
        )
    return (
        "<table><thead><tr><th>Рынок</th><th>Наша точка</th><th>Доля</th><th>Δ к пред. снятию</th>"
        f"<th>Ёмкость</th><th>Точек</th><th>Свежесть</th><th>Аномалий</th></tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


_AGGREGATE_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>Дашборд — все рынки</title>
<script>
{chart_js}
</script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Golos+Text:wght@500;600;700&family=PT+Sans:wght@400;700&display=swap">
<style>
{base_style}
</style>
</head>
<body>
<h1>Все рынки</h1>
<div class="subtitle">Сводно на {today}.</div>

<div class="cards">
  {hero_cards}
</div>

<h2>Наша доля по рынкам</h2>
<div class="chart-wrap"><canvas id="shareByMarketChart"></canvas></div>

<h2>Сравнение рынков</h2>
{table_html}

<div class="cdn-note">Для детальной аналитики (тренды, аномалии, причины, факторы, наблюдения) по конкретному рынку используйте /dashboard_market и выберите рынок.</div>

<script>
const marketLabels = {market_labels};
const shareData = {share_data};
const capacityData = {capacity_data};

new Chart(document.getElementById('shareByMarketChart'), {{
  type: 'bar',
  data: {{
    labels: marketLabels,
    datasets: [
      {{ label: 'Наша доля, %', data: shareData, backgroundColor: '#C1622D', yAxisID: 'y' }},
      {{ label: 'Ёмкость рынка, чек/день', data: capacityData, backgroundColor: '#D4A24C', yAxisID: 'y1' }}
    ]
  }},
  options: {{
    scales: {{
      y: {{ beginAtZero: true, max: 100, position: 'left', title: {{ display: true, text: 'Доля, %' }} }},
      y1: {{ beginAtZero: true, position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: 'Чек/день' }} }}
    }}
  }}
}});
</script>
</body>
</html>
"""


def generate_aggregate_dashboard() -> tuple[str, str]:
    """Сводный дашборд владельца по всем рынкам сразу: доля и ёмкость
    каждого рынка, изменение к предыдущему снятию, здоровье мониторинга и
    число недавних аномалий — детальные тренды и причины смотрятся через
    дашборд конкретного рынка."""
    rows = [_market_summary_row(market) for market in list_markets()]

    html = _AGGREGATE_TEMPLATE.format(
        base_style=_BASE_STYLE_CSS,
        chart_js=_chart_js(),
        today=today().isoformat(),
        hero_cards=_render_aggregate_hero(rows),
        table_html=_render_aggregate_table(rows),
        market_labels=json.dumps([r["market"]["name"] for r in rows], ensure_ascii=False),
        share_data=json.dumps([round(r["own_share"], 1) if r["own_share"] is not None else 0 for r in rows]),
        capacity_data=json.dumps([round(r["capacity_now"], 1) for r in rows]),
    )
    filename = f"dashboard_all_markets_{today().isoformat()}.html"
    return filename, html
