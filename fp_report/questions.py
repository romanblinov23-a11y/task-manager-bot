"""Вопросы управляющему для ежемесячного отчёта ФП — то, чего нет в НИМБ и файлах, и все комментарии по шаблону.

Порядок — как в шаблоне. Перед вопросами по разделам показываем короткую сводку из НИМБ,
чтобы управляющий видел, о чём пишет, и мог поправить неверные данные. Ответ раскладывается
по полям (fields) — это ключи, которые понимает fp_report.build.build_report через answers.
"""

import re
from dataclasses import asdict, dataclass

from fp_report.anomalies import find_suspicious
from fp_report.build import MANUAL_OPS, OPS_FROM_MANAGER, _flatten, _pnl, _prev_ym, fin_period_keys, num_answer, pnl_value
from fp_report.inventory import Position, summarize


@dataclass
class Question:
    key: str
    text: str
    fields: list[str]


def _money(v) -> str:
    return "—" if v is None else f"{v:,.0f} ₽".replace(",", " ")


def _pct(v) -> str:
    return "—" if v is None else f"{v:.2f}%".replace(".", ",")


def _count(v) -> str:
    return "—" if v is None else f"{v:,.0f}".replace(",", " ")


def _plain(v) -> str:
    return "—" if v is None else f"{v:g}".replace(".", ",")


# (подпись, код в P&L, форматтер)
_FIN_ROWS = [
    ("Выручка", "income", _money),
    ("Расчётная прибыль", "estimate", _money),
    ("Рентабельность расчётной прибыли", "estimate_percent", _pct),
    ("Чистая прибыль", "net_profit", _money),
    ("Рентабельность чистой прибыли", "net_profit_percent", _pct),
    ("Количество чеков", "araar_count_receipts", _count),
    ("Средний чек", "araar_avg_receipts", _money),
]
_COST_ROWS = [
    ("Фудкост", "food_cost", _pct),
    ("Прямая себестоимость", "expenses_direct_cost_percent", _pct),
    ("Себестоимость напитков", "expenses_direct_cost_percent_drinks", _pct),
    ("Списания по сроку годности", "expenses_direct_writeoffs_expiration", _money),
    ("Списания в реализацию", "expenses_direct_writeoffs_realise", _money),
    ("Списания на порчу", "expenses_direct_writeoffs_bad", _money),
    ("Питание персонала", "expenses_indirect_team_eatingIn", _money),
    ("Комплименты гостям", "expenses_indirect_marketing_thanks", _money),
]


def _pnl_lines(inputs: dict, month: str, rows: list) -> list[str]:
    flat = _flatten(inputs["pnl"]["fields"])
    prev = _prev_ym(month)
    lines = []
    for label, code, fmt in rows:
        plan, fact = _pnl(flat, code, month)
        prev_fact = _pnl(flat, code, prev)[1]
        lines.append(f"{label}: план {fmt(plan)} · факт {fmt(fact)} · прошлый {fmt(prev_fact)}")
    return lines


def _ops_values(inputs: dict, month: str) -> dict[str, tuple]:
    """Значения показателей из отчёта управляющего: label -> (факт, прошлый месяц), None — нет в данных."""
    fin = {r["name"]: r for r in inputs["manager"].get("fin_result", [])}
    cur, prev = fin_period_keys(month)
    out = {}
    for label, name in OPS_FROM_MANAGER.items():
        r = fin.get(name, {})
        out[label] = ((r.get(cur) or {}).get("value"), (r.get(prev) or {}).get("value"))
    return out


# Показатели P&L, по которым в отчёте есть план/факт/прошлый месяц и которые могут быть пустыми в НИМБ
_GAP_LABELS = {code: label for label, code, _ in _FIN_ROWS + _COST_ROWS}
_GAP_LABELS.update({"expenses_indirect_salary": "ФОТ, ₽", "araar_count_receipts": "Количество гостей (чеки)"})
_GAP_COLS = {"plan": "план", "fact": "факт", "prev": "прошлый месяц"}


def build_questions(inputs: dict, positions: list[Position], month: str) -> list[dict]:
    """inputs — словарь из fp_report.collect.fetch_nimba_inputs, month — 'YYYY-MM'.
    Возвращает список dict (для хранения в сессии)."""
    questions: list[Question] = []

    def add(key: str, text: str, fields: list[str]) -> None:
        questions.append(Question(key, text, fields))

    flat = _flatten(inputs["pnl"]["fields"])
    gaps = [
        (code, col)
        for code in _GAP_LABELS
        for col in _GAP_COLS
        if pnl_value(flat, code, col, month) is None
    ]
    if gaps:
        add(
            "gaps",
            "В НИМБ нет этих значений. Пришли по одному числу в строке, в том же порядке:\n"
            + "\n".join(f"{i}) {_GAP_LABELS[code]}, {_GAP_COLS[col]}" for i, (code, col) in enumerate(gaps, 1)),
            [f"gap:{code}:{col}" for code, col in gaps],
        )

    add(
        "manager_name",
        "Шапка отчёта. Напиши ФИО управляющего, как он должен быть указан в отчёте. "
        "Данные в боте могут быть неверными, поэтому спрашиваю.",
        ["manager_name"],
    )

    add(
        "comment_fin",
        "Раздел 1. Финансовый результат. Цифры из НИМБ:\n"
        + "\n".join(_pnl_lines(inputs, month, _FIN_ROWS))
        + "\n\nКомментарий: что повлияло на отклонение от плана — сезон, погода, конкуренты, мероприятия, ремонт, кадры. "
        "С датами и величиной эффекта, не общими словами.",
        ["comment_fin"],
    )
    add(
        "comment_cogs",
        "Раздел 2. Себестоимость и списания. Цифры из НИМБ:\n"
        + "\n".join(_pnl_lines(inputs, month, _COST_ROWS))
        + "\n\nКомментарий: что выросло и почему, какие позиции лидируют в списаниях, что с этим делаете. "
        "Если фудкост выше плана — конкретные причины и срок исправления.",
        ["comment_cogs"],
    )

    summary = summarize(positions)
    flags = find_suspicious(positions)
    for p in summary["explained"]:
        lines = [f"Раздел 3. Инвентаризация: {p.name} ({p.code}), разница {_money(p.diff_sum)}."]
        if p.code in flags:
            lines.append(f"⚠️ Перепроверь: {flags[p.code]}")
        lines.append("Пришли 2 строки: 1) причина расхождения 2) что сделано, чтобы не повторилось.")
        add(f"inv:{p.code}", "\n".join(lines), [f"inv_reason:{p.code}", f"inv_action:{p.code}"])
    add(
        "comment_inv",
        f"Раздел 3. Итог инвентаризации: излишки {_money(summary['surplus_total'])}, "
        f"недостачи {_money(summary['shortage_total'])}, итог {_money(summary['net_total'])}.\n"
        "Комментарий по инвентаризации в целом. Типовые причины: пересорт, неоприходованная поставка, "
        "ошибка в накладной, неучтённое списание, брак, кража. «Разберёмся» не принимается. Или «нет».",
        ["comment_inv"],
    )

    team = inputs["komanda"]["employees"]
    shtat = inputs["shtat"]
    add(
        "staff",
        f"Раздел 4. Движение персонала. В команде сейчас {len(team)}, штатная норма {shtat['staff_norm_value']}, "
        f"факт {shtat['staff_actual_value']}.\n"
        "Пришли 2 строки: 1) официально трудоустроено, сколько человек 2) уволено за месяц, сколько человек.",
        ["hired_official", "fired"],
    )
    add(
        "comment_staff",
        "Движение персонала. Если оформлены не все — поясни, кто и почему: расхождение между общим числом "
        "и официальным оформлением — юридический риск. Или «нет».",
        ["comment_staff"],
    )
    add(
        "comment_fot",
        "Фонд оплаты труда. Цифры из НИМБ:\n"
        + "\n".join(_pnl_lines(inputs, month, [("ФОТ, ₽", "expenses_indirect_salary", _money)]))
        + "\n\nКомментарий: премии, выплаты наставникам за обучение стажёров, компенсации отпусков при увольнении — "
        "что объясняет превышение планового ФОТ. Или «нет».",
        ["comment_fot"],
    )

    add(
        "invest",
        "Раздел 5. Выплаты инвесторам за месяц по факту. Одна строка — одна выплата: дата, инвестор, тело, проценты, всего.\n"
        "Или «нет», если выплат не было.",
        ["invest"],
    )
    add(
        "payouts_plan",
        "План выплат на следующий месяц. Одна строка — одна выплата: дата, инвестор, сумма, основание.\n"
        "Если не планируются — напиши «выплаты не планируются» и причину.",
        ["payouts_plan"],
    )

    values = _ops_values(inputs, month)
    missing_ops = [label for label, (fact, prev) in values.items() if fact is None or prev is None]
    ops_labels = list(MANUAL_OPS) + missing_ops
    present = [f"{label}: факт {_plain(fact)} · прошлый {_plain(prev)}" for label, (fact, prev) in values.items() if label not in missing_ops]
    add(
        "ops",
        "Раздел 6. Операционные показатели. Из НИМБ:\n"
        + ("\n".join(present) + "\n" if present else "")
        + "Пришли строки в формате «факт / прошлый месяц», по одной на показатель:\n"
        + "\n".join(f"{i}) {label}" for i, label in enumerate(ops_labels, 1)),
        [f"ops:{label}" for label in ops_labels],
    )
    add(
        "comment_ops",
        "Комментарий по операционке: что изменилось и почему. Или «нет».",
        ["comment_ops"],
    )

    add(
        "tasks_prev",
        "Раздел 7. Задачи прошлого месяца. По каждой: выполнена, не выполнена или перенесена — и почему. "
        "Каждую задачу с новой строки.",
        ["tasks_prev"],
    )
    add(
        "tasks_next",
        "Раздел 8. Задачи на следующий месяц. Не больше пяти, каждая: задача — измеримый результат — срок. "
        "Каждую с новой строки.",
        ["tasks_next"],
    )
    add(
        "summary",
        "Раздел 9. Общий итог месяца: пять–семь предложений — что получилось, что нет, главный вызов на следующий месяц. "
        "Пиши так, чтобы человек, не знающий точку, понял состояние дел.",
        ["summary"],
    )

    return [asdict(q) for q in questions]


_NUMBERING = re.compile(r"^\s*\d+[\).]\s*")


def apply_answer(question: dict, text: str) -> dict[str, str]:
    """Раскладывает ответ по полям вопроса. Если поле одно — весь ответ целиком (многострочный текст или список).
    Если полей несколько — по строкам по порядку, нумерацию «1)» / «2.» срезаем."""
    fields = question["fields"]
    value = text.strip()
    if len(fields) == 1:
        return {fields[0]: value[:1].upper() + value[1:]}
    lines = [_NUMBERING.sub("", line).strip() for line in value.splitlines() if line.strip()]
    return {field: line for field, line in zip(fields, lines)}


def missing_fields(question: dict, answers: dict) -> list[str]:
    """Поля вопроса, по которым ответа ещё нет. Для пропусков НИМБ ответ должен быть числом."""
    out = []
    for field in question["fields"]:
        value = answers.get(field)
        if not value or (field.startswith("gap:") and num_answer(value) is None):
            out.append(field)
    return out


def describe_field(field: str) -> str:
    """Человеческая подпись поля — для сообщения «не хватает: …»."""
    if field.startswith("inv_reason:"):
        return "причина расхождения (инвентаризация)"
    if field.startswith("inv_action:"):
        return "что сделано (инвентаризация)"
    if field.startswith("ops:"):
        return field[len("ops:"):]
    if field.startswith("gap:"):
        _, code, col = field.split(":")
        return f"{_GAP_LABELS.get(code, code)}, {_GAP_COLS.get(col, col)}"
    return {
        "hired_official": "официально трудоустроено",
        "fired": "уволено за месяц",
        "manager_name": "ФИО управляющего",
    }.get(field, field)
