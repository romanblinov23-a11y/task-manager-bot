"""Сборка месячного отчёта для ФП по шаблону (шаблон — docx из примеров).

Бот не пишет текстов: комментарии, причины расхождений, разделы 5, 7–9 и всё,
чего нет в данных, помечаются как «[запросить у управляющего]». Числа берутся
из НИМБ (P&L, отчёт управляющего, вознаграждения, график, штат) и из файлов
(инвентаризация, команда).
"""

import copy
import json
from datetime import date
from pathlib import Path

import docx

from fp_report.anomalies import find_suspicious
from fp_report.inventory import summarize

ASK = "[запросить у управляющего]"
SPOT_TITLE = "Surf Coffee x Park Gorkogo"
_MONTH_RU = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

# Таблицы шаблона (индексы в docx.tables)
T_HEADER, T_SEC1, T_SEC2, T_INV_DAY, T_INV_EXPL, T_FOT, T_EMP, T_PERS, T_INVEST, T_PAYOUT, T_OPS, T_TASKS_PREV, T_TASKS_NEXT, T_ATTACH = range(14)


def _flatten(fields, out=None):
    out = {} if out is None else out
    for f in fields:
        out[f["code"]] = f
        if f.get("items"):
            _flatten(f["items"], out)
    return out


def _prev_ym(ym: str) -> str:
    y, m = map(int, ym.split("-"))
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def _pnl(flat: dict, code: str, ym: str) -> tuple:
    f = flat.get(code)
    if not f:
        return None, None
    for r in f.get("results") or []:
        if str(r.get("period", "")).startswith(ym):
            return r.get("plan"), r.get("fact")
    return None, None


def _money(v) -> str:
    return ASK if v is None else f"{v:,.2f}".replace(",", " ").replace(".", ",")


def _count(v) -> str:
    return ASK if v is None else f"{v:,.0f}".replace(",", " ")


def _pct(v) -> str:
    return ASK if v is None else f"{v:.2f}".replace(".", ",")


def _hours(v) -> str:
    return ASK if v is None else f"{v:g}".replace(".", ",")


def _set(cell, text: str) -> None:
    p = cell.paragraphs[0]
    if p.runs:
        p.runs[0].text = text
        for r in p.runs[1:]:
            r.text = ""
    else:
        p.add_run(text)


def _fill_row(table, label: str, values: list, fmt) -> None:
    for row in table.rows[1:]:
        if row.cells[0].text.strip() == label:
            for cell, v in zip(row.cells[1:], values):
                _set(cell, fmt(v))
            return
    raise KeyError(f"нет строки «{label}» в таблице шаблона")


def _ensure_rows(table, n_data: int) -> None:
    """Добавляет строки перед итоговой, чтобы вместить n_data строк данных."""
    last_data = table.rows[-2]._tr
    total_tr = table.rows[-1]._tr
    have = len(table.rows) - 2
    for _ in range(n_data - have):
        total_tr.addprevious(copy.deepcopy(last_data))


def _sum(values):
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def build_report(
    month: str,
    template_path: str,
    pnl: dict,
    manager: dict,
    vozn: dict,
    komanda: dict,
    shtat: dict,
    inventory_positions: list,
    out_dir: str,
    manager_name: str = ASK,
) -> str:
    """Возвращает путь к сохранённому docx."""
    flat = _flatten(pnl["fields"])
    prev = _prev_ym(month)
    doc = docx.Document(template_path)
    T = doc.tables

    income = _pnl(flat, "income", month)
    income_prev = _pnl(flat, "income", prev)[1]

    # Шапка
    _set(T[T_HEADER].rows[0].cells[1], SPOT_TITLE)
    y, m = map(int, month.split("-"))
    _set(T[T_HEADER].rows[1].cells[1], f"{_MONTH_RU[m]} {y}")
    _set(T[T_HEADER].rows[2].cells[1], manager_name)
    _set(T[T_HEADER].rows[3].cells[1], date.today().strftime("%d.%m.%Y"))

    # Раздел 1
    sec1 = {
        "Выручка, ₽": ("income", _money),
        "Расчётная прибыль, ₽": ("estimate", _money),
        "Рентабельность расчётной прибыли, %": ("estimate_percent", _pct),
        "Чистая прибыль, ₽": ("net_profit", _money),
        "Рентабельность чистой прибыли, %": ("net_profit_percent", _pct),
        "Количество чеков": ("araar_count_receipts", _count),
        "Средний чек, ₽": ("araar_avg_receipts", _money),
    }
    for label, (code, fmt) in sec1.items():
        plan, fact = _pnl(flat, code, month)
        _, prev_fact = _pnl(flat, code, prev)
        _fill_row(T[T_SEC1], label, [plan, fact, prev_fact], fmt)
    # Гости в шаблоне отдельной строкой; по договорённости совпадают с числом чеков.
    guests_plan, guests_fact = _pnl(flat, "araar_count_receipts", month)
    _fill_row(T[T_SEC1], "Количество гостей", [guests_plan, guests_fact, _pnl(flat, "araar_count_receipts", prev)[1]], _count)

    # Раздел 2
    def pct_of_income(code):
        out = []
        for ym, inc in ((month, income[1]), (prev, income_prev)):
            _, fact = _pnl(flat, code, ym)
            out.append(fact / inc * 100 if fact is not None and inc else None)
        plan_inc = income[0]
        plan_v = _pnl(flat, code, month)[0]
        plan_pct = plan_v / plan_inc * 100 if plan_v is not None and plan_inc else None
        return [plan_pct, out[0], out[1]]

    sec2 = {
        "Фудкост, %": ("food_cost", _pct),
        "Прямая себестоимость, %": ("expenses_direct_cost_percent", _pct),
        "Себестоимость напитков, %": ("expenses_direct_cost_percent_drinks", _pct),
        "Списания по сроку годности, ₽": ("expenses_direct_writeoffs_expiration", _money),
        "Списания в реализацию, ₽": ("expenses_direct_writeoffs_realise", _money),
        "Списания на порчу, ₽": ("expenses_direct_writeoffs_bad", _money),
        "Питание персонала, ₽": ("expenses_indirect_team_eatingIn", _money),
        "Комплименты гостям, ₽": ("expenses_indirect_marketing_thanks", _money),
    }
    for label, (code, fmt) in sec2.items():
        plan, fact = _pnl(flat, code, month)
        _, prev_fact = _pnl(flat, code, prev)
        _fill_row(T[T_SEC2], label, [plan, fact, prev_fact], fmt)
    _fill_row(T[T_SEC2], "Списания по сроку годности, % выручки", pct_of_income("expenses_direct_writeoffs_expiration"), _pct)

    # Раздел 3: инвентаризация (только SAVE)
    summary = summarize(inventory_positions)
    fact_revenue = income[1]
    total = summary["net_total"]
    _set(T[T_INV_DAY].rows[1].cells[0], "см. файл инвентаризации")
    _set(T[T_INV_DAY].rows[1].cells[1], _money(summary["surplus_total"]))
    _set(T[T_INV_DAY].rows[1].cells[2], _money(summary["shortage_total"]))
    _set(T[T_INV_DAY].rows[1].cells[3], _money(total))
    _set(T[T_INV_DAY].rows[1].cells[4], _pct(total / fact_revenue * 100 if fact_revenue else None))
    _set(T[T_INV_DAY].rows[-1].cells[1], _money(summary["surplus_total"]))
    _set(T[T_INV_DAY].rows[-1].cells[2], _money(summary["shortage_total"]))
    _set(T[T_INV_DAY].rows[-1].cells[3], _money(total))

    flags = find_suspicious(inventory_positions)
    explained = summary["explained"]
    _ensure_rows(T[T_INV_EXPL], len(explained))
    for row, p in zip(T[T_INV_EXPL].rows[1:-1], explained):
        reason = f"⚠️ перепроверить: {flags[p.code]}" if p.code in flags else ASK
        _set(row.cells[0], f"{p.name} ({p.code})")
        _set(row.cells[1], _money(p.diff_sum))
        _set(row.cells[2], reason)
        _set(row.cells[3], ASK)

    # Раздел 4: ФОТ
    fot_plan, fot_fact = _pnl(flat, "expenses_indirect_salary", month)
    fot_prev = _pnl(flat, "expenses_indirect_salary", prev)[1]
    emp_rows = vozn["awards"]
    hours = sum(r.get("duration") or 0 for r in emp_rows)
    manager_hours = sum(r.get("duration") or 0 for r in emp_rows if r.get("employee_post") == "manager")
    fot_pct = lambda fot, inc: fot / inc * 100 if fot is not None and inc else None  # noqa: E731
    _fill_row(T[T_FOT], "ФОТ всего, ₽", [fot_plan, fot_fact, fot_prev], _money)
    _fill_row(T[T_FOT], "ФОТ, % от выручки", [fot_pct(fot_plan, income[0]), fot_pct(fot_fact, income[1]), fot_pct(fot_prev, income_prev)], _pct)
    _fill_row(T[T_FOT], "Количество смен за месяц", [None, None, None], _count)
    _fill_row(T[T_FOT], "Количество отработанных часов", [None, hours, None], _hours)
    _fill_row(T[T_FOT], "Стоимость часа, ₽", [None, fot_fact / hours if fot_fact and hours else None, None], _money)
    _fill_row(T[T_FOT], "Выручка на час работы, ₽", [None, fact_revenue / hours if fact_revenue and hours else None, None], _money)
    _fill_row(T[T_FOT], "Часы управляющего в смене", [None, manager_hours, None], _hours)

    # Расшифровка по сотрудникам
    _ensure_rows(T[T_EMP], len(emp_rows))
    total_pay = 0.0
    for row, r in zip(T[T_EMP].rows[1:-1], emp_rows):
        accrued = (r.get("award") or 0) + (r.get("grade_bonus") or 0) + (r.get("holiday_bonus") or 0) + (r.get("night_bonus") or 0)
        total_pay += accrued
        _set(row.cells[0], r.get("user_title", ""))
        _set(row.cells[1], r.get("user_job_title", ""))
        _set(row.cells[2], ASK)
        _set(row.cells[3], f"{r.get('duration') or 0:g}".replace(".", ","))
        _set(row.cells[4], f"{r.get('rate') or 0:.2f}".replace(".", ","))
        _set(row.cells[5], _money(accrued))
    _set(T[T_EMP].rows[-1].cells[3], f"{hours:g}".replace(".", ","))
    _set(T[T_EMP].rows[-1].cells[5], _money(total_pay))

    # Движение персонала
    team = komanda["employees"]
    fired = sum(1 for r in emp_rows if "(уволен)" in (r.get("user_title") or ""))
    interns = sum(1 for e in team if e.get("job_title") == "Стажер")
    hired = sum(1 for e in team if str(e.get("first_working_day", "")).startswith(month))
    norm, actual = shtat["staff_norm_value"], shtat["staff_actual_value"]
    _set(T[T_PERS].rows[1].cells[1], str(len(team)))
    _set(T[T_PERS].rows[1].cells[2], "по разделу «Команда» НИМБ")
    _set(T[T_PERS].rows[2].cells[1], ASK)
    _set(T[T_PERS].rows[3].cells[1], str(hired))
    _set(T[T_PERS].rows[3].cells[2], "первый рабочий день в месяце по НИМБ")
    _set(T[T_PERS].rows[4].cells[1], str(fired))
    _set(T[T_PERS].rows[4].cells[2], "по вознаграждениям НИМБ (метка «уволен»)")
    _set(T[T_PERS].rows[5].cells[1], str(interns))
    _set(T[T_PERS].rows[6].cells[1], str(max(norm - actual, 0)))
    _set(T[T_PERS].rows[6].cells[2], f"штатная норма {norm}, факт {actual}")

    # Раздел 5 (инвестиции) — только от управляющего
    _set(T[T_INVEST].rows[1].cells[0], ASK)
    _set(T[T_PAYOUT].rows[1].cells[0], ASK)

    # Раздел 6
    fin = {r["name"]: r for r in manager.get("fin_result", [])}

    def fr(name):
        r = fin.get(name, {})
        return (r.get("sep") or {}).get("value"), (r.get("aug") or {}).get("value")

    ops = {
        "Количество лицензий": ("Количество выданных лицензий", _count),
        "Доля постоянных гостей, %": ("Доля постоянных гостей", _pct),
        "Достоверность учёта, %": ("Достоверность учета", _pct),
        "Нефискальная выручка, %": ("Нефискальная выручка %", _pct),
    }
    for label, (name, fmt) in ops.items():
        cur, pv = fr(name)
        _fill_row(T[T_OPS], label, [None, cur, pv], fmt)
    indirect = _pnl(flat, "expenses_indirect", month)[1]
    indirect_prev = _pnl(flat, "expenses_indirect", prev)[1]
    _fill_row(T[T_OPS], "Косвенные затраты, % выручки", [None, indirect / fact_revenue * 100 if indirect and fact_revenue else None, indirect_prev / income_prev * 100 if indirect_prev and income_prev else None], _pct)
    for label in ("Среднее время отдачи, мин", "Отзывов собрано", "Средняя оценка"):
        for row in T[T_OPS].rows[1:]:
            if row.cells[0].text.strip() == label:
                for cell in row.cells[1:]:
                    _set(cell, ASK)

    # Разделы 7 и 8 — только от управляющего
    _set(T[T_TASKS_PREV].rows[1].cells[0], ASK)
    _set(T[T_TASKS_NEXT].rows[1].cells[0], ASK)

    out = Path(out_dir) / f"report_park_{month.replace('-', '')}.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return str(out)


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
