"""Короткий аналитический комментарий от Claude на дашборде (см.
dashboard/server.py) — опирается на агрегаты периода, сравнение с
предыдущим таким же периодом (см. dashboard.data.aggregate_series) и уже
посчитанные корреляции (dashboard.data.build_correlation_matrix), а не на
построчный пересказ отчётов — иначе на длинном периоде (90 дней) промпт
распухает. Синхронный вызов (prompts.client.ask_claude — тот же клиент,
что и в revenue/claude_commentary.py) — вызывающий код (dashboard/server.py)
сам оборачивает в asyncio.to_thread, чтобы не блокировать event loop."""

from dashboard.data import METRIC_LABELS, aggregate_series, daily_revenue_totals, describe_correlation
from prompts.client import ask_claude

SYSTEM_PROMPT = """Ты — операционный аналитик сети кофеен Surf Coffee в Москве.
Дай короткий комментарий по дашборду отчётов смен: 2-4 коротких предложения.

Правила:
- НЕ пересказывай цифры дословно — их уже видно на дашборде рядом с текстом
- Фокус на: тренд относительно прошлого периода, реальные связи между
  показателями (погода, списания и т.п.), риски и на что стоит обратить внимание
- Если значимых связей или трендов нет — так и скажи, не выдумывай
- Обычный текст, без markdown, без списков и заголовков
"""

# Кэш по ключу (охват, период, кол-во дней, сумма выручки) — чтобы
# перезагрузка той же страницы/переключение вкладок не дёргало Claude
# заново на те же самые данные; при появлении нового отчёта (сумма
# выручки меняется) ключ меняется и комментарий пересчитывается сам.
_cache: dict[tuple, str | None] = {}


def _fmt(value: float | None, unit: str = "") -> str:
    return f"{value:.0f}{unit}" if value is not None else "нет данных"


def _trim_to_full_sentence(text: str) -> str:
    """Подстраховка от обрыва на середине фразы (если модель всё же
    упёрлась в max_tokens) — если текст не заканчивается на ./!/?, режем
    до последней завершённой фразы. Если завершённых фраз вообще нет
    (совсем короткий ответ без знака препинания в конце) — оставляем как
    есть, обрезать нечего."""
    text = text.strip()
    if not text or text[-1] in ".!?»":
        return text
    for i in range(len(text) - 1, -1, -1):
        if text[i] in ".!?":
            return text[: i + 1]
    return text


def generate_commentary(
    scope_label: str,
    period_label: str,
    rows: list[dict],
    prev_rows: list[dict],
    correlation_matrix: dict[tuple[str, str], float | None],
) -> str | None:
    if len(rows) < 2:
        return None

    cur = aggregate_series(rows)
    cache_key = (scope_label, period_label, cur["days_count"], round(cur["revenue_total"] or 0))
    if cache_key in _cache:
        return _cache[cache_key]

    prev = aggregate_series(prev_rows) if prev_rows else None
    daily = daily_revenue_totals(rows)

    lines = [
        f"Точка/охват: {scope_label}",
        f"Период: {period_label}, дней с отчётами: {cur['days_count']}",
        f"Выручка суммарно: {_fmt(cur['revenue_total'], ' руб')}",
        f"Средний чек (среднее за период): {_fmt(cur['avg_check_avg'], ' руб')}",
        f"Гости суммарно: {_fmt(cur['guests_total'])}",
        f"SPMH среднее: {_fmt(cur['spmh_avg'], ' руб/ч')}",
    ]
    writeoff_line = f"Списания суммарно: {_fmt(cur['writeoff_total'], ' руб')}"
    if cur["writeoff_total_plan"]:
        writeoff_line += f" (норма {_fmt(cur['writeoff_total_plan'], ' руб')})"
    lines.append(writeoff_line)

    if cur["temp_avg"] is not None:
        lines.append(
            f"Погода: средняя температура {cur['temp_avg']:.0f}°C, "
            f"дней с осадками {cur['rain_days']} из {cur['days_count']}"
        )

    if prev and prev["revenue_total"] and cur["revenue_total"] is not None:
        change = (cur["revenue_total"] / prev["revenue_total"] - 1) * 100
        lines.append(
            f"За предыдущий такой же период выручка была {_fmt(prev['revenue_total'], ' руб')} ({change:+.0f}%)"
        )

    if daily:
        best_date, best_value = max(daily.items(), key=lambda kv: kv[1])
        worst_date, worst_value = min(daily.items(), key=lambda kv: kv[1])
        if best_date != worst_date:
            lines.append(f"Лучший день по выручке: {best_date} ({best_value:.0f} руб), худший: {worst_date} ({worst_value:.0f} руб)")

    notable = [(a, b, r) for (a, b), r in correlation_matrix.items() if r is not None and abs(r) >= 0.4]
    if notable:
        lines.append("Заметные связи между показателями за этот период:")
        for a, b, r in notable:
            lines.append(f"- {METRIC_LABELS[a]} и {METRIC_LABELS[b]}: {describe_correlation(r)}")
    else:
        lines.append("Заметных связей между показателями за этот период не найдено.")

    lines.append("\nДай короткий аналитический комментарий по этим данным.")

    try:
        # 220 токенов резалось на середине фразы на русском тексте (кириллица
        # заметно "дороже" в токенах, чем латиница) — берём с запасом,
        # _trim_to_full_sentence всё равно подчищает хвост, если что.
        raw = ask_claude("\n".join(lines), max_tokens=500, system=SYSTEM_PROMPT).strip()
        commentary = _trim_to_full_sentence(raw)
    except Exception:
        commentary = None

    _cache[cache_key] = commentary
    return commentary
