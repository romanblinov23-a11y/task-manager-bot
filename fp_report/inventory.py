"""Раздел 3 месячного отчёта для ФП — разбор итогов инвентаризации (Excel).

В выгрузке учитываются только строки со статусом «Сохранение» = SAVE; RECALC
не попадает ни в суммы, ни в объяснения. Порог обязательного объяснения —
позиция с |разница| > THRESHOLD ₽. Если таких позиций нет вообще, берём
топ-TOP_N недостач и топ-TOP_N излишков."""

import openpyxl
from dataclasses import dataclass

SAVE_STATUS = "SAVE"
THRESHOLD = 5000.0
TOP_N = 3

# Номера колонок (0-based) в файле инвентаризации.
_COL_CODE = 1
_COL_NAME = 2
_COL_BOOK_QTY = 10
_COL_FACT_QTY = 9
_COL_DIFF_QTY = 11
_COL_DIFF_SUM = 13
_COL_STATUS = 14


@dataclass
class Position:
    code: str
    name: str
    book_qty: float
    fact_qty: float
    diff_qty: float
    diff_sum: float


def _num(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace("*", "").replace(" ", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def load_save_positions(path: str) -> list[Position]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    positions: list[Position] = []
    for row in ws.iter_rows(min_row=1, values_only=True):
        if not isinstance(row[0], (int, float)):
            continue
        if str(row[_COL_STATUS] or "").strip().upper() != SAVE_STATUS:
            continue
        positions.append(
            Position(
                code=str(row[_COL_CODE] or "").strip(),
                name=str(row[_COL_NAME] or "").strip(),
                book_qty=_num(row[_COL_BOOK_QTY]),
                fact_qty=_num(row[_COL_FACT_QTY]),
                diff_qty=_num(row[_COL_DIFF_QTY]),
                diff_sum=_num(row[_COL_DIFF_SUM]),
            )
        )
    return positions


def summarize(positions: list[Position]) -> dict:
    surplus = [p for p in positions if p.diff_sum > 0]
    shortage = [p for p in positions if p.diff_sum < 0]
    over_threshold = sorted(
        (p for p in positions if abs(p.diff_sum) > THRESHOLD),
        key=lambda p: -abs(p.diff_sum),
    )
    if over_threshold:
        explained = over_threshold
        fallback = False
    else:
        explained = (
            sorted(shortage, key=lambda p: p.diff_sum)[:TOP_N]
            + sorted(surplus, key=lambda p: -p.diff_sum)[:TOP_N]
        )
        fallback = True
    return {
        "positions_count": len(positions),
        "surplus_total": round(sum(p.diff_sum for p in surplus), 2),
        "shortage_total": round(sum(p.diff_sum for p in shortage), 2),
        "net_total": round(sum(p.diff_sum for p in positions), 2),
        "explained": explained,
        "fallback_top_n": fallback,
    }
