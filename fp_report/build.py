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


# Строки таблицы показателей, которых нет в данных НИМБ: вводит управляющий.
# Ключ — подпись строки в шаблоне, значение — название показателя в fin_result отчёта управляющего.
OPS_FROM_MANAGER = {
    "Количество лицензий": "Количество выданных лицензий",
    "Доля постоянных гостей, %": "Доля постоянных гостей",
    "Достоверность учёта, %": "Достоверность учета",
    "Нефискальная выручка, %": "Нефискальная выручка %",
}
MANUAL_OPS = ("Среднее время отдачи, мин", "Отзывов собрано", "Средняя оценка")


def _set_ops_cells(table, label: str, fact, prev) -> None:
    """fact/prev = None — колонку не трогаем."""
    for row in table.rows[1:]:
        if row.cells[0].text.strip() == label:
            if fact is not None:
                _set(row.cells[2], fact)
            if prev is not None:
                _set(row.cells[3], prev)


_FIN_MONTH_KEYS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def fin_period_keys(month: str) -> tuple[str, str]:
    """Ключи периодов во вкладке «Финансовый результат» отчёта управляющего: для сентябрьского отчёта
    это sep (текущий) и aug (прошлый), для августовского — aug и jul. Зашивать одну пару нельзя."""
    m = int(month.split("-")[1])
    return _FIN_MONTH_KEYS[m - 1], _FIN_MONTH_KEYS[(m - 2) % 12]


def _split_pair(text: str) -> tuple[str, str]:
    """«12 / 10» -> («12», «10»); «12» -> («12», «»)."""
    if not text:
        return "", ""
    left, _, right = text.partition("/")
    return left.strip(), right.strip()


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


# Комментарии к разделам: ключ ответа и начало абзаца-инструкции (или заголовка) раздела в шаблоне
COMMENT_SLOTS = [
    ("comment_fin", "Что повлияло на отклонение"),
    ("comment_cogs", "Что выросло и почему."),
    ("comment_inv", "Типовые причины:"),
    ("comment_staff", "Если оформлены не все"),
    ("comment_fot", "Отдельно выделите премии"),
    ("comment_ops", "Комментарий по операционке"),
    ("summary", "Пять–семь предложений"),
]


def _fill_after(doc, anchor: str, text: str) -> None:
    """Пишет текст в первый пустой абзац сразу после абзаца с anchor; если пустого нет — новым абзацем под ним."""
    from docx.oxml import OxmlElement
    from docx.text.paragraph import Paragraph

    for p in doc.paragraphs:
        if not p.text.strip().startswith(anchor):
            continue
        following = p._p.getnext()
        if following is not None and following.tag.endswith("}p") and not "".join(following.itertext()).strip():
            Paragraph(following, p._parent).add_run(text)
        else:
            new_p = OxmlElement("w:p")
            p._p.addnext(new_p)
            Paragraph(new_p, p._parent).add_run(text)
        return


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


def _shifts_by_user(grafik: dict) -> tuple[dict, int]:
    """Смены по сотрудникам (id) и общее число смен: день с непустым списком shift."""
    by_user: dict = {}
    for unit in grafik["spot_employees"].values():
        for e in unit["unit_employees"]:
            days = 0
            for day in e["results"]["dates"]:
                for v in day.values():
                    if v.get("shift"):
                        days += 1
            by_user[e["id"]] = days
    return by_user, sum(by_user.values())


def build_report(
    month: str,
    template_path: str,
    pnl: dict,
    manager: dict,
    vozn: dict,
    komanda: dict,
    shtat: dict,
    grafik: dict,
    inventory_positions: list,
    out_dir: str,
    manager_name: str = ASK,
    answers: dict | None = None,
    spot_code: str = "",
) -> str:
    """Возвращает путь к сохранённому docx.

    answers — ответы управляющего по ключам из fp_report.questions (inv_reason:КОД,
    inv_action:КОД, hired_official, fired, invest, payouts, ops:НАЗВАНИЕ, tasks_prev,
    tasks_next). Всё, чего нет в answers, остаётся «[запросить у управляющего]»."""
    ans = answers or {}
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
    _set(T[T_HEADER].rows[2].cells[1], ans.get("manager_name", manager_name))
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
    checks_plan, checks_fact = _pnl(flat, "araar_count_receipts", month)
    checks_prev = _pnl(flat, "araar_count_receipts", prev)[1]
    _fill_row(T[T_SEC1], "Количество гостей", [checks_plan, checks_fact, checks_prev], _count)

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
        flag = flags.get(p.code)
        given = ans.get(f"inv_reason:{p.code}")
        if given:
            reason = f"{given} (⚠️ перепроверить: {flag})" if flag else given
        else:
            reason = f"⚠️ перепроверить: {flag}" if flag else ASK
        _set(row.cells[0], f"{p.name} ({p.code})")
        _set(row.cells[1], _money(p.diff_sum))
        _set(row.cells[2], reason)
        _set(row.cells[3], ans.get(f"inv_action:{p.code}", ASK))

    # Раздел 4: ФОТ
    fot_plan, fot_fact = _pnl(flat, "expenses_indirect_salary", month)
    fot_prev = _pnl(flat, "expenses_indirect_salary", prev)[1]
    emp_rows = vozn["awards"]
    hours = sum(r.get("duration") or 0 for r in emp_rows)
    manager_hours = sum(r.get("duration") or 0 for r in emp_rows if r.get("employee_post") == "manager")
    fot_pct = lambda fot, inc: fot / inc * 100 if fot is not None and inc else None  # noqa: E731
    _fill_row(T[T_FOT], "ФОТ всего, ₽", [fot_plan, fot_fact, fot_prev], _money)
    _fill_row(T[T_FOT], "ФОТ, % от выручки", [fot_pct(fot_plan, income[0]), fot_pct(fot_fact, income[1]), fot_pct(fot_prev, income_prev)], _pct)
    shifts_by_user, shifts_total = _shifts_by_user(grafik)
    _fill_row(T[T_FOT], "Количество смен за месяц", [None, shifts_total, None], _count)
    _fill_row(T[T_FOT], "Количество отработанных часов", [None, hours, None], _hours)
    _fill_row(T[T_FOT], "Стоимость часа, ₽", [None, fot_fact / hours if fot_fact and hours else None, None], _money)
    _fill_row(T[T_FOT], "Выручка на час работы, ₽", [None, fact_revenue / hours if fact_revenue and hours else None, None], _money)
    _fill_row(T[T_FOT], "Часы управляющего в смене", [None, manager_hours, None], _hours)
    # Плана и прошлого месяца по сменам и часам в данных нет — «—», а не запрос управляющему
    for label in ("Количество смен за месяц", "Количество отработанных часов", "Стоимость часа, ₽", "Выручка на час работы, ₽", "Часы управляющего в смене"):
        for row in T[T_FOT].rows[1:]:
            if row.cells[0].text.strip() == label:
                _set(row.cells[1], "—")
                _set(row.cells[3], "—")

    # Расшифровка по сотрудникам
    _ensure_rows(T[T_EMP], len(emp_rows))
    total_pay = 0.0
    for row, r in zip(T[T_EMP].rows[1:-1], emp_rows):
        accrued = (r.get("award") or 0) + (r.get("grade_bonus") or 0) + (r.get("holiday_bonus") or 0) + (r.get("night_bonus") or 0)
        total_pay += accrued
        _set(row.cells[0], r.get("user_title", ""))
        _set(row.cells[1], r.get("user_job_title", ""))
        _set(row.cells[2], str(shifts_by_user.get(r.get("user_id"), 0)))
        _set(row.cells[3], f"{r.get('duration') or 0:g}".replace(".", ","))
        _set(row.cells[4], f"{r.get('rate') or 0:.2f}".replace(".", ","))
        _set(row.cells[5], _money(accrued))
    _set(T[T_EMP].rows[-1].cells[3], f"{hours:g}".replace(".", ","))
    _set(T[T_EMP].rows[-1].cells[5], _money(total_pay))

    # Движение персонала
    team = komanda["employees"]
    interns = sum(1 for e in team if e.get("job_title") == "Стажер")
    hired = sum(1 for e in team if str(e.get("first_working_day", "")).startswith(month))
    norm, actual = shtat["staff_norm_value"], shtat["staff_actual_value"]
    _set(T[T_PERS].rows[1].cells[1], str(len(team)))
    _set(T[T_PERS].rows[1].cells[2], "по разделу «Команда» НИМБ")
    _set(T[T_PERS].rows[2].cells[1], ans.get("hired_official", ASK))
    _set(T[T_PERS].rows[3].cells[1], str(hired))
    _set(T[T_PERS].rows[3].cells[2], "первый рабочий день в месяце по НИМБ")
    _set(T[T_PERS].rows[4].cells[1], ans.get("fired", ASK))
    _set(T[T_PERS].rows[5].cells[1], str(interns))
    _set(T[T_PERS].rows[6].cells[1], str(max(norm - actual, 0)))
    _set(T[T_PERS].rows[6].cells[2], f"штатная норма {norm}, факт {actual}")

    # Раздел 5 (инвестиции) — только от управляющего
    _set(T[T_INVEST].rows[1].cells[0], ans.get("invest", ASK))
    _set(T[T_PAYOUT].rows[1].cells[0], ans.get("payouts_plan", ASK))

    # Раздел 6
    fin = {r["name"]: r for r in manager.get("fin_result", [])}
    cur_key, prev_key = fin_period_keys(month)

    def fr(name):
        r = fin.get(name, {})
        return (r.get(cur_key) or {}).get("value"), (r.get(prev_key) or {}).get("value")

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
    # План по этим показателям не ведём — колонка «План» всегда «—».
    for row in T[T_OPS].rows[1:]:
        _set(row.cells[1], "—")
    # Ответ управляющего в формате «факт / прошлый месяц»: вручную заполняемые строки
    # и строки, которых не оказалось в отчёте управляющего из НИМБ. Пустое значение не трогаем.
    for label in MANUAL_OPS:
        fact, prev = _split_pair(ans.get(f"ops:{label}", ""))
        _set_ops_cells(T[T_OPS], label, fact or ASK, prev or ASK)
    for label in OPS_FROM_MANAGER:
        fact, prev = _split_pair(ans.get(f"ops:{label}", ""))
        if fact or prev:
            _set_ops_cells(T[T_OPS], label, fact or None, prev or None)

    # Разделы 7 и 8 — только от управляющего
    _set(T[T_TASKS_PREV].rows[1].cells[0], ans.get("tasks_prev", ASK))
    _set(T[T_TASKS_NEXT].rows[1].cells[0], ans.get("tasks_next", ASK))

    # Приложение: имена файлов пакета по шаблону (код точки и период в имени)
    ym = month.replace("-", "")
    code = spot_code.lower()
    attach_names = {
        "Выручка по дням": f"sales{code}{ym}.xlsx",
        "Типы оплат": f"pay{code}{ym}.xlsx",
        "Инвентаризации": f"inv{code}{ym}.xlsx",
        "Отчёт управляющего": f"report_{code}_{ym}.docx",
        "Банковская выписка": f"bank{code}{ym}.xlsx",
    }
    for row in T[T_ATTACH].rows[1:]:
        label = row.cells[0].text.strip()
        if label in attach_names:
            _set(row.cells[1], attach_names[label] if code else ASK)

    # Комментарии к разделам — в пустые абзацы под инструкцией раздела (или сразу под ней)
    for key, anchor in COMMENT_SLOTS:
        _fill_after(doc, anchor, ans.get(key, ASK))

    out = Path(out_dir) / f"report_{spot_code or 'park'}_{month.replace('-', '')}.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return str(out)


def count_open_fields(path: str) -> int:
    """Сколько мест остались с пометкой «[запросить у управляющего]» — ячейки таблиц и абзацы комментариев."""
    doc = docx.Document(path)
    in_tables = sum(cell.text.count(ASK) for table in doc.tables for row in table.rows for cell in row.cells)
    in_text = sum(p.text.count(ASK) for p in doc.paragraphs)
    return in_tables + in_text


def load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
