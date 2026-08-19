import json
from datetime import date, datetime, timedelta

from config.projects import CATEGORIES, STATUSES
from config.settings import STALE_DAYS
from config.timeutil import fmt_date, now_naive, today
from monitoring.markets import list_market_names
from tasks.log import get_log_entries
from tasks.tasks import get_all_tasks

_STATUS_COLORS = {"новая": "#3D5A80", "в работе": "#F2CC8F", "выполнена": "#81B29A", "просрочена": "#E07A5F"}
_CATEGORY_PALETTE = ["#3D5A80", "#E07A5F", "#81B29A", "#F2CC8F", "#9B5DE5", "#F4845F", "#577590", "#B56576"]
_STATUS_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"


def _load_all_tasks() -> list[dict]:
    tasks = []
    for project in list_market_names():
        tasks.extend(get_all_tasks(project))
    return tasks


def _transfers_by_project() -> dict[str, dict[str, int]]:
    """project -> {task_id: кол-во переносов срока}."""
    result: dict[str, dict[str, int]] = {}
    for project in list_market_names():
        counts: dict[str, int] = {}
        for entry in get_log_entries(project):
            if entry["event_type"] == "перенос_срока":
                counts[entry["task_id"]] = counts.get(entry["task_id"], 0) + 1
        result[project] = counts
    return result


def _status_breakdown(tasks: list[dict]) -> dict[str, int]:
    counts = {s: 0 for s in STATUSES}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    return counts


def _project_breakdown(tasks: list[dict]) -> dict[str, dict[str, int]]:
    by_project: dict[str, dict[str, int]] = {}
    for t in tasks:
        row = by_project.setdefault(t["project"], {s: 0 for s in STATUSES})
        row[t["status"]] = row.get(t["status"], 0) + 1
    return by_project


def _category_breakdown(tasks: list[dict]) -> dict[str, int]:
    counts = {c: 0 for c in CATEGORIES}
    for t in tasks:
        if t["status"] == "выполнена":
            continue
        counts[t.get("category", "")] = counts.get(t.get("category", ""), 0) + 1
    return counts


def _overdue_aging(tasks: list[dict]) -> dict[str, int]:
    buckets = {"1-3 дня": 0, "4-7 дней": 0, "8+ дней": 0}
    t0 = today()
    for t in tasks:
        if t["status"] != "просрочена":
            continue
        deadline_raw = t.get("deadline_current")
        if not deadline_raw:
            continue
        try:
            d = date.fromisoformat(deadline_raw)
        except ValueError:
            continue
        age = (t0 - d).days
        if age <= 0:
            continue
        if age <= 3:
            buckets["1-3 дня"] += 1
        elif age <= 7:
            buckets["4-7 дней"] += 1
        else:
            buckets["8+ дней"] += 1
    return buckets


def _closure_durations(tasks: list[dict]) -> list[dict]:
    """(project, assignee, days) для каждой закрытой задачи с валидными
    датами — только те, что ещё не удалены политикой хранения (§ retention),
    т.е. закрытые в этом или прошлом месяце."""
    result = []
    for t in tasks:
        if t["status"] != "выполнена":
            continue
        created, closed = t.get("created_at"), t.get("closed_at")
        if not created or not closed:
            continue
        try:
            d_created = datetime.strptime(created, _STATUS_TIMESTAMP_FMT)
            d_closed = datetime.strptime(closed, _STATUS_TIMESTAMP_FMT)
        except ValueError:
            continue
        days = (d_closed - d_created).total_seconds() / 86400
        result.append({"project": t["project"], "assignee": t.get("assignee") or "(не назначено)", "days": days})
    return result


def _avg_by_key(durations: list[dict], key: str) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for d in durations:
        buckets.setdefault(d[key], []).append(d["days"])
    return {k: sum(v) / len(v) for k, v in buckets.items()}


def _employee_workload(tasks: list[dict]) -> list[dict]:
    workload: dict[str, dict[str, int]] = {}
    for t in tasks:
        if t["status"] == "выполнена":
            continue
        name = t.get("assignee") or "(не назначено)"
        entry = workload.setdefault(name, {"open": 0, "overdue": 0, "needs_help": 0})
        entry["open"] += 1
        if t["status"] == "просрочена":
            entry["overdue"] += 1
        if t.get("needs_help") == "да":
            entry["needs_help"] += 1
    rows = [{"name": name, **counts} for name, counts in workload.items()]
    rows.sort(key=lambda r: r["open"], reverse=True)
    return rows


def _stuck_tasks(tasks: list[dict], transfers: dict[str, dict[str, int]]) -> list[dict]:
    now = now_naive()
    stuck = []
    for t in tasks:
        if t["status"] == "выполнена":
            continue
        n_transfers = transfers.get(t["project"], {}).get(t["task_id"], 0)
        last_check_raw = t.get("last_status_check") or t.get("created_at")
        stale_days = None
        try:
            last_check = datetime.strptime(last_check_raw, _STATUS_TIMESTAMP_FMT)
            stale_days = (now - last_check).days
        except (ValueError, TypeError):
            pass

        reasons = []
        if n_transfers >= 2:
            reasons.append(f"{n_transfers} переноса")
        if stale_days is not None and stale_days >= STALE_DAYS:
            reasons.append(f"{stale_days} дн. без обновления")
        if reasons:
            stuck.append({**t, "reasons": ", ".join(reasons)})
    return stuck


def _needs_help_tasks(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if t.get("needs_help") == "да" and t["status"] != "выполнена"]


def _top_transferred_tasks(tasks: list[dict], transfers: dict[str, dict[str, int]], limit: int = 5) -> list[dict]:
    rows = []
    for t in tasks:
        n = transfers.get(t["project"], {}).get(t["task_id"], 0)
        if n > 0:
            rows.append({**t, "transfers": n})
    rows.sort(key=lambda r: r["transfers"], reverse=True)
    return rows[:limit]


def _week_start(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        d = datetime.strptime(raw, _STATUS_TIMESTAMP_FMT).date()
    except (ValueError, TypeError):
        return None
    return (d - timedelta(days=d.weekday())).isoformat()


def _weekly_throughput(tasks: list[dict], weeks: int = 8) -> tuple[list[str], list[int], list[int]]:
    t0 = today()
    current_monday = t0 - timedelta(days=t0.weekday())
    week_starts = [(current_monday - timedelta(weeks=i)).isoformat() for i in range(weeks - 1, -1, -1)]
    created_counts = {w: 0 for w in week_starts}
    closed_counts = {w: 0 for w in week_starts}

    for t in tasks:
        ws = _week_start(t.get("created_at"))
        if ws in created_counts:
            created_counts[ws] += 1
        if t["status"] == "выполнена":
            ws2 = _week_start(t.get("closed_at"))
            if ws2 in closed_counts:
                closed_counts[ws2] += 1

    return week_starts, [created_counts[w] for w in week_starts], [closed_counts[w] for w in week_starts]


def _project_summary_rows(
    project_counts: dict[str, dict[str, int]], avg_days_by_project: dict[str, float]
) -> list[dict]:
    rows = []
    for project in sorted(project_counts.keys()):
        counts = project_counts[project]
        open_n = sum(n for s, n in counts.items() if s != "выполнена")
        avg_days = avg_days_by_project.get(project)
        rows.append(
            {
                "project": project,
                "open": open_n,
                "closed_this_month": counts.get("выполнена", 0),
                "overdue": counts.get("просрочена", 0),
                "avg_close_days": round(avg_days, 1) if avg_days is not None else None,
            }
        )
    return rows


def _render_status_table(counts: dict[str, int]) -> str:
    rows = "".join(f"<tr><td>{status}</td><td>{n}</td></tr>" for status, n in counts.items())
    return f"<table><thead><tr><th>Статус</th><th>Кол-во</th></tr></thead><tbody>{rows}</tbody></table>"


def _render_project_share_bars(project_counts: dict[str, dict[str, int]]) -> str:
    rows = []
    for project in sorted(project_counts.keys()):
        counts = project_counts[project]
        total = sum(counts.values())
        if not total:
            continue
        segments, legend = [], []
        for status in STATUSES:
            n = counts.get(status, 0)
            if not n:
                continue
            pct = n / total * 100
            color = _STATUS_COLORS[status]
            segments.append(f'<div style="width:{pct:.1f}%;background:{color}" title="{status}: {n} ({pct:.0f}%)"></div>')
            legend.append(f'<span class="share-legend-item"><span class="dot" style="background:{color}"></span>{status} {pct:.0f}%</span>')
        rows.append(
            f'<div class="share-row"><div class="share-label">{project}</div>'
            f'<div class="share-bar">{"".join(segments)}</div>'
            f'<div class="share-legend">{"".join(legend)}</div></div>'
        )
    return "".join(rows) or '<div class="notice">Нет задач.</div>'


def _render_employee_profiles(tasks: list[dict], workload: list[dict], avg_days_by_employee: dict[str, float]) -> str:
    if not workload:
        return '<div class="notice">Нет открытых задач ни у одного сотрудника.</div>'
    by_employee: dict[str, list[dict]] = {}
    for t in tasks:
        if t["status"] == "выполнена":
            continue
        name = t.get("assignee") or "(не назначено)"
        by_employee.setdefault(name, []).append(t)

    blocks = []
    for row in workload:
        name = row["name"]
        avg_days = avg_days_by_employee.get(name)
        avg_part = f", ср. время закрытия {avg_days:.1f} дн." if avg_days is not None else ""
        items = "".join(
            f"<li>[{t['project']}] {t['task_id']} — {t['task_text']} "
            f"(статус: {t['status']}, срок: {fmt_date(t.get('deadline_current'))})</li>"
            for t in by_employee.get(name, [])
        )
        blocks.append(
            f"<details class='employee-profile'><summary>{name} — {row['open']} откр., "
            f"{row['overdue']} просрочено, {row['needs_help']} нужна помощь{avg_part}</summary>"
            f"<ul>{items}</ul></details>"
        )
    return "".join(blocks)


def _render_task_list_table(rows: list[dict], *, extra_column: str | None = None) -> str:
    if not rows:
        return '<div class="notice">Пусто.</div>'
    extra_header = f"<th>{extra_column}</th>" if extra_column else ""
    body = []
    for t in rows:
        extra_cell = ""
        if extra_column == "Причина":
            extra_cell = f"<td>{t.get('reasons', '')}</td>"
        elif extra_column == "Переносов":
            extra_cell = f"<td>{t.get('transfers', '')}</td>"
        assignee = t.get("assignee") or "—"
        body.append(
            f"<tr><td>[{t['project']}] {t['task_id']}</td><td>{t['task_text']}</td>"
            f"<td>{t['status']}</td><td>{assignee}</td>{extra_cell}</tr>"
        )
    return (
        "<table><thead><tr><th>Задача</th><th>Текст</th><th>Статус</th><th>Исполнитель</th>"
        f"{extra_header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _render_project_summary_table(rows: list[dict]) -> str:
    if not rows:
        return '<div class="notice">Нет проектов.</div>'
    body = "".join(
        f"<tr><td>{r['project']}</td><td>{r['open']}</td><td>{r['closed_this_month']}</td>"
        f"<td>{r['overdue']}</td><td>{r['avg_close_days'] if r['avg_close_days'] is not None else '—'}</td></tr>"
        for r in rows
    )
    return (
        "<table><thead><tr><th>Проект</th><th>Открыто</th><th>Закрыто (в этом месяце)</th>"
        f"<th>Просрочено</th><th>Ср. время закрытия, дн.</th></tr></thead><tbody>{body}</tbody></table>"
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
  .cdn-note {{ color: #999; font-size: 12px; margin-top: 40px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  th, td {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #eee; }}
  th {{ color: #666; font-weight: 600; font-size: 12px; text-transform: uppercase; }}
  .share-row {{ display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }}
  .share-label {{ width: 140px; font-size: 13px; flex-shrink: 0; }}
  .share-bar {{ flex: 1; display: flex; height: 18px; border-radius: 4px; overflow: hidden; background: #eee; }}
  .share-bar > div {{ height: 100%; }}
  .share-legend {{ font-size: 12px; color: #666; display: flex; gap: 8px; flex-wrap: wrap; width: 260px; flex-shrink: 0; }}
  .share-legend-item {{ display: inline-flex; align-items: center; }}
  .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 3px; }}
  .employee-profile {{ background: #fff; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
  .employee-profile summary {{ cursor: pointer; font-weight: 600; font-size: 14px; }}
  .employee-profile ul {{ margin: 8px 0 0; padding-left: 18px; font-size: 13px; color: #333; }}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="subtitle">{subtitle}</div>

<div class="cards">
  <div class="card"><div class="value">{open_count}</div><div class="label">Открытых задач</div></div>
  <div class="card"><div class="value">{overdue_count}</div><div class="label">Просрочено</div></div>
  <div class="card"><div class="value">{needs_help_count}</div><div class="label">Нужна помощь</div></div>
  <div class="card"><div class="value">{stuck_count}</div><div class="label">Подвисших</div></div>
  <div class="card"><div class="value">{avg_close_days}</div><div class="label">Ср. время закрытия, дн. (за хранимый период)</div></div>
</div>

<h2>1. Задачи по статусам</h2>
<div class="chart-wrap"><canvas id="statusChart"></canvas></div>
{status_table_html}

<h2>2. Задачи по проектам</h2>
<div class="chart-wrap"><canvas id="projectChart"></canvas></div>
<h2>Доли статусов внутри каждого проекта</h2>
{share_bars_html}

<h2>3. Загрузка и профиль сотрудников</h2>
{workload_html}

<h2>4. Категории задач</h2>
<div class="chart-wrap"><canvas id="categoryChart"></canvas></div>

<h2>5. Просрочка по возрасту</h2>
<div class="chart-wrap"><canvas id="agingChart"></canvas></div>

<h2>6. Переносы сроков по проектам</h2>
<div class="chart-wrap"><canvas id="transfersChart"></canvas></div>
<h3>Топ по числу переносов</h3>
{top_transfers_html}

<h2>7. Динамика по неделям</h2>
<div class="notice">История ограничена политикой хранения: закрытые задачи удаляются в начале месяца, следующего за месяцем закрытия, поэтому «Закрыто» достоверно за этот и прошлый месяц, а «Создано» может быть занижено для более старых недель.</div>
<div class="chart-wrap"><canvas id="throughputChart"></canvas></div>

<h2>8. Сводная таблица по проектам</h2>
{project_summary_html}

<h2>9. Подвисшие задачи ({stuck_reason_note})</h2>
{stuck_html}

<h2>10. Задачи, где нужна помощь</h2>
{needs_help_html}

<div class="cdn-note">Графики интерактивны (наведите курсор на точки) — для их отображения нужен доступ в интернет (библиотека графиков подгружается с CDN).</div>

<script>
const statusLabels = {status_labels};
const statusData = {status_data};
const statusColors = {status_colors};
const projectLabels = {project_labels};
const projectDatasets = {project_datasets};
const categoryLabels = {category_labels};
const categoryData = {category_data};
const categoryColors = {category_colors};
const agingLabels = {aging_labels};
const agingData = {aging_data};
const transferLabels = {transfer_labels};
const transferData = {transfer_data};
const throughputLabels = {throughput_labels};
const throughputCreated = {throughput_created};
const throughputClosed = {throughput_closed};

new Chart(document.getElementById('statusChart'), {{
  type: 'bar',
  data: {{ labels: statusLabels, datasets: [{{ label: 'Задач', data: statusData, backgroundColor: statusColors }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

new Chart(document.getElementById('projectChart'), {{
  type: 'bar',
  data: {{ labels: projectLabels, datasets: projectDatasets }},
  options: {{ scales: {{ x: {{ stacked: true }}, y: {{ stacked: true, beginAtZero: true }} }} }}
}});

if (categoryLabels.length) {{
  new Chart(document.getElementById('categoryChart'), {{
    type: 'pie',
    data: {{ labels: categoryLabels, datasets: [{{ data: categoryData, backgroundColor: categoryColors }}] }}
  }});
}}

new Chart(document.getElementById('agingChart'), {{
  type: 'bar',
  data: {{ labels: agingLabels, datasets: [{{ label: 'Просроченных задач', data: agingData, backgroundColor: '#E07A5F' }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

new Chart(document.getElementById('transfersChart'), {{
  type: 'bar',
  data: {{ labels: transferLabels, datasets: [{{ label: 'Переносов срока', data: transferData, backgroundColor: '#E07A5F' }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});

new Chart(document.getElementById('throughputChart'), {{
  type: 'line',
  data: {{ labels: throughputLabels, datasets: [
    {{ label: 'Создано', data: throughputCreated, borderColor: '#3D5A80', backgroundColor: '#3D5A80', tension: 0.2 }},
    {{ label: 'Закрыто', data: throughputClosed, borderColor: '#81B29A', backgroundColor: '#81B29A', tension: 0.2 }}
  ] }},
  options: {{ scales: {{ y: {{ beginAtZero: true }} }} }}
}});
</script>
</body>
</html>
"""


def generate_tasks_dashboard() -> tuple[str, str]:
    """Полная аналитика по трекеру задач сразу по всем проектам — сводный
    вид «владельца процессов»: статусы (в целом и по проектам, с долями),
    загрузка и профиль сотрудников, категории, просрочка по возрасту,
    переносы сроков, динамика по неделям, сводка по проектам, подвисшие
    задачи и запросы помощи."""
    tasks = _load_all_tasks()
    transfers = _transfers_by_project()

    status_counts = _status_breakdown(tasks)
    project_counts = _project_breakdown(tasks)
    category_counts = _category_breakdown(tasks)
    aging = _overdue_aging(tasks)
    workload = _employee_workload(tasks)
    stuck = _stuck_tasks(tasks, transfers)
    needs_help = _needs_help_tasks(tasks)
    top_transfers = _top_transferred_tasks(tasks, transfers)
    durations = _closure_durations(tasks)
    avg_days_by_employee = _avg_by_key(durations, "assignee")
    avg_days_by_project = _avg_by_key(durations, "project")
    overall_avg_days = round(sum(d["days"] for d in durations) / len(durations), 1) if durations else "—"

    open_count = sum(n for status, n in status_counts.items() if status != "выполнена")
    overdue_count = status_counts.get("просрочена", 0)

    project_names = sorted(project_counts.keys())
    project_datasets = [
        {
            "label": status,
            "data": [project_counts[p][status] for p in project_names],
            "backgroundColor": _STATUS_COLORS[status],
        }
        for status in STATUSES
    ]

    category_items = [(c, n) for c, n in category_counts.items() if n]

    transfer_totals = {project: sum(counts.values()) for project, counts in transfers.items()}
    transfer_projects = sorted(transfer_totals.keys())

    throughput_labels, throughput_created, throughput_closed = _weekly_throughput(tasks)

    html = _HTML_TEMPLATE.format(
        title="Дашборд трекера задач",
        subtitle=f"Все проекты. Обновлено {today().isoformat()}.",
        open_count=open_count,
        overdue_count=overdue_count,
        needs_help_count=len(needs_help),
        stuck_count=len(stuck),
        avg_close_days=overall_avg_days,
        status_table_html=_render_status_table(status_counts),
        share_bars_html=_render_project_share_bars(project_counts),
        workload_html=_render_employee_profiles(tasks, workload, avg_days_by_employee),
        stuck_reason_note="2+ переноса или давно без обновления",
        stuck_html=_render_task_list_table(stuck, extra_column="Причина"),
        needs_help_html=_render_task_list_table(needs_help),
        top_transfers_html=_render_task_list_table(top_transfers, extra_column="Переносов"),
        project_summary_html=_render_project_summary_table(_project_summary_rows(project_counts, avg_days_by_project)),
        status_labels=json.dumps(list(status_counts.keys()), ensure_ascii=False),
        status_data=json.dumps(list(status_counts.values())),
        status_colors=json.dumps([_STATUS_COLORS[s] for s in status_counts.keys()]),
        project_labels=json.dumps(project_names, ensure_ascii=False),
        project_datasets=json.dumps(project_datasets, ensure_ascii=False),
        category_labels=json.dumps([c for c, _ in category_items], ensure_ascii=False),
        category_data=json.dumps([n for _, n in category_items]),
        category_colors=json.dumps(_CATEGORY_PALETTE[: len(category_items)]),
        aging_labels=json.dumps(list(aging.keys()), ensure_ascii=False),
        aging_data=json.dumps(list(aging.values())),
        transfer_labels=json.dumps(transfer_projects, ensure_ascii=False),
        transfer_data=json.dumps([transfer_totals[p] for p in transfer_projects]),
        throughput_labels=json.dumps(throughput_labels, ensure_ascii=False),
        throughput_created=json.dumps(throughput_created),
        throughput_closed=json.dumps(throughput_closed),
    )

    filename = f"dashboard_tasks_{today().isoformat()}.html"
    return filename, html
