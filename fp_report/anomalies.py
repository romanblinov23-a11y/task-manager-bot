"""Сигналы о подозрительных позициях инвентаризации. Это только проверка для
того, кто подаёт отчёт: флаги и короткая техническая причина, без текстов
для отчёта. Правила работают без сети; Клод добавляет свои флаги поверх и
при ошибке API просто не добавляет ничего."""

import json

from fp_report.inventory import THRESHOLD, Position
from prompts.client import ask_claude

RULE_MIN_RELATIVE = 0.3
RULE_MIN_SUM_ALWAYS = 20000.0

_SYSTEM_PROMPT = """Ты проверяешь позиции инвентаризации кофейни на возможные ошибки подсчёта или ввода.
Не пиши комментариев, выводов или текстов для отчёта — только сигналы для проверки.
Верни ТОЛЬКО JSON-массив вида [{"code": "...", "reason": "..."}], где reason — одна короткая
техническая фраза, почему позицию стоит перепроверить (например: расхождение не сходится
с книжным остатком, похоже на ошибку единиц измерения, значение выглядит невозможным).
Если подозрительных позиций нет — верни []."""


def rule_flags(positions: list[Position]) -> dict[str, str]:
    flags: dict[str, str] = {}
    for p in positions:
        if abs(p.diff_sum) < THRESHOLD or p.book_qty == 0:
            continue
        relative = abs(p.diff_qty) / abs(p.book_qty)
        if relative >= RULE_MIN_RELATIVE or abs(p.diff_sum) >= RULE_MIN_SUM_ALWAYS:
            flags[p.code] = (
                f"расхождение {p.diff_qty:+.2f} при книжном остатке {p.book_qty:.2f} "
                f"({relative:.0%} от книжного) — перепроверить подсчёт"
            )
    return flags


def claude_flags(positions: list[Position]) -> dict[str, str]:
    candidates = [p for p in positions if abs(p.diff_sum) >= THRESHOLD]
    if not candidates:
        return {}
    lines = [
        f"{p.code} | {p.name} | книжн. {p.book_qty} | факт {p.fact_qty} | "
        f"разн. {p.diff_qty} | сумма {p.diff_sum} ₽"
        for p in candidates
    ]
    try:
        raw = ask_claude("\n".join(lines), max_tokens=800, system=_SYSTEM_PROMPT).strip()
        start, end = raw.find("["), raw.rfind("]")
        items = json.loads(raw[start : end + 1]) if start != -1 else []
    except Exception:
        return {}
    known = {p.code for p in candidates}
    return {
        str(item["code"]): str(item.get("reason", "")).strip()
        for item in items
        if isinstance(item, dict) and str(item.get("code", "")) in known
    }


def find_suspicious(positions: list[Position]) -> dict[str, str]:
    flags = rule_flags(positions)
    flags.update(claude_flags(positions))
    return flags
