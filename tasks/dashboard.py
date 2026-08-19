import json
from datetime import datetime

from config.projects import STATUSES
from config.settings import STALE_DAYS
from config.timeutil import now_naive, today
from monitoring.markets import list_market_names
from tasks.log import get_log_entries
from tasks.tasks import get_all_tasks

_STATUS_COLORS = {"новая": "#3D5A80", "в работе": "#F2CC8F", "выполнена": "#81B29A", "просрочена": "#E07A5F"}
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


def _render_status_table(counts: dict[str, int]) -> str:
    rows = "".join(f"<tr><td>{status}</td><td>{n}</td></tr>" for status, n in counts.items())
    return f"<table><thead><tr><th>Статус</th><th>Кол-во</th></tr></thead><tbody>{rows}</tbody></table>"


def _render_workload_table(rows: list[dict]) -> str:
    if not rows:
        return '<div class="notice">Нет открытых задач ни у одного сотрудника.</div>'
    body = "".join(
        f"<tr><td>{r['name']}</td><td>{r['open']}</td><td>{r['overdue']}</td><td>{r['needs_help']}</td></tr>"
        for r in rows
    )
    return (
        "<table><thead><tr><th>Сотрудник</th><th>Открытых задач</th><th>Просрочено</th>"
        f"<th>Нужна помощь</th></tr></thead><tbody>{body}</tbody></table>"
    )


def _render_task_list_table(rows: list[dict], *, with_reasons: bool) -> str:
    if not rows:
        return '<div class="notice">Пусто.</div>'
    extra_header = "<th>Причина</th>" if with_reasons else ""
    body = []
    for t in rows:
        extra_cell = f"<td>{t.get('reasons', '')}</td>" if with_reasons else ""
        assignee = t.get("assignee") or "—"
        body.append(
            f"<tr><td>[{t['project']}] {t['task_id']}</td><td>{t['task_text']}</td>"
            f"<td>{t['status']}</td><td>{assignee}</td>{extra_cell}</tr>"
        )
    return (
        "<table><thead><tr><th>Задача</th><th>Текст</th><th>Статус</th><th>Исполнитель</th>"
        f"{extra_header}</tr></thead><tbody>{''.join(body)}</tbody></table>"
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
</div>

<h2>1. Задачи по статусам</h2>
<div class="chart-wrap"><canvas id="statusChart"></canvas></div>
{status_table_html}

<h2>2. Задачи по проектам</h2>
<div class="chart-wrap"><canvas id="projectChart"></canvas></div>

<h2>3. Загрузка сотрудников</h2>
{workload_html}

<h2>4. Переносы сроков по проектам</h2>
<div class="chart-wrap"><canvas id="transfersChart"></canvas></div>

<h2>5. Подвисшие задачи ({stuck_reason_note})</h2>
{stuck_html}

<h2>6. Задачи, где нужна помощь</h2>
{needs_help_html}

<div class="cdn-note">Графики интерактивны (наведите курсор на точки) — для их отображения нужен доступ в интернет (библиотека графиков подгружается с CDN).</div>

<script>
const statusLabels = {status_labels};
const statusData = {status_data};
const statusColors = {status_colors};
const projectLabels = {project_labels};
const projectDatasets = {project_datasets};
const transferLabels = {transfer_labels};
const transferData = {transfer_data};

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

new Chart(document.getElementById('transfersChart'), {{
  type: 'bar',
  data: {{ labels: transferLabels, datasets: [{{ label: 'Переносов срока', data: transferData, backgroundColor: '#E07A5F' }}] }},
  options: {{ plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ beginAtZero: true }} }} }}
}});
</script>
</body>
</html>
"""


def generate_tasks_dashboard() -> tuple[str, str]:
    """Полная аналитика по трекеру задач сразу по всем проектам — сводный
    вид «владельца процессов»: статусы, загрузка сотрудников, переносы
    сроков, подвисшие задачи и запросы помощи."""
    tasks = _load_all_tasks()
    transfers = _transfers_by_project()

    status_counts = _status_breakdown(tasks)
    project_counts = _project_breakdown(tasks)
    workload = _employee_workload(tasks)
    stuck = _stuck_tasks(tasks, transfers)
    needs_help = _needs_help_tasks(tasks)

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

    transfer_totals = {project: sum(counts.values()) for project, counts in transfers.items()}
    transfer_projects = sorted(transfer_totals.keys())

    html = _HTML_TEMPLATE.format(
        title="Дашборд трекера задач",
        subtitle=f"Все проекты. Обновлено {today().isoformat()}.",
        open_count=open_count,
        overdue_count=overdue_count,
        needs_help_count=len(needs_help),
        stuck_count=len(stuck),
        status_table_html=_render_status_table(status_counts),
        workload_html=_render_workload_table(workload),
        stuck_reason_note="2+ переноса или давно без обновления",
        stuck_html=_render_task_list_table(stuck, with_reasons=True),
        needs_help_html=_render_task_list_table(needs_help, with_reasons=False),
        status_labels=json.dumps(list(status_counts.keys()), ensure_ascii=False),
        status_data=json.dumps(list(status_counts.values())),
        status_colors=json.dumps([_STATUS_COLORS[s] for s in status_counts.keys()]),
        project_labels=json.dumps(project_names, ensure_ascii=False),
        project_datasets=json.dumps(project_datasets, ensure_ascii=False),
        transfer_labels=json.dumps(transfer_projects, ensure_ascii=False),
        transfer_data=json.dumps([transfer_totals[p] for p in transfer_projects]),
    )

    filename = f"dashboard_tasks_{today().isoformat()}.html"
    return filename, html
