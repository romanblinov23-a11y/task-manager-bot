from unittest.mock import patch

from dashboard.commentary import _cache, _trim_to_full_sentence, generate_commentary
from dashboard.data import aggregate_series, build_correlation_matrix


def _row(date, revenue, temp=None, precip=None):
    return {
        "report_date": date,
        "market_id": 1,
        "market_name": "Тест",
        "revenue": revenue,
        "avg_check": 500.0,
        "guests": 20,
        "staff_hours": 10.0,
        "spmh": revenue / 10 if revenue else None,
        "writeoff_expiry": 100.0,
        "writeoff_compliment": 50.0,
        "writeoff_staff_meals": 50.0,
        "writeoff_total": 200.0,
        "writeoff_total_plan": 180.0,
        "avg_service_minutes": 5.0,
        "temp_avg_c": temp,
        "precipitation_mm": precip,
        "weather_code": 0,
        "events": "",
    }


def setup_function():
    _cache.clear()


def test_generate_commentary_none_when_too_few_rows():
    assert generate_commentary("Тест", "неделя", [_row("2026-09-01", 1000)], [], {}) is None


def test_generate_commentary_calls_claude_and_caches():
    rows = [_row("2026-09-01", 1000, temp=10), _row("2026-09-02", 2000, temp=20)]
    matrix = build_correlation_matrix(rows)

    with patch("dashboard.commentary.ask_claude", return_value="  Всё стабильно.  ") as mock_ask:
        result1 = generate_commentary("Тест", "неделя", rows, [], matrix)
        result2 = generate_commentary("Тест", "неделя", rows, [], matrix)

    assert result1 == "Всё стабильно."
    assert result2 == "Всё стабильно."
    mock_ask.assert_called_once()  # второй вызов -- из кэша, без обращения к Claude


def test_generate_commentary_returns_none_on_claude_error():
    rows = [_row("2026-09-01", 1000), _row("2026-09-02", 2000)]
    with patch("dashboard.commentary.ask_claude", side_effect=RuntimeError("api down")):
        result = generate_commentary("Тест", "неделя", rows, [], {})
    assert result is None


def test_generate_commentary_cache_busts_on_new_revenue():
    rows_a = [_row("2026-09-01", 1000), _row("2026-09-02", 2000)]
    rows_b = [_row("2026-09-01", 1000), _row("2026-09-02", 2000), _row("2026-09-03", 3000)]

    with patch("dashboard.commentary.ask_claude", return_value="ответ") as mock_ask:
        generate_commentary("Тест", "неделя", rows_a, [], {})
        generate_commentary("Тест", "неделя", rows_b, [], {})

    assert mock_ask.call_count == 2  # разные данные -- разный кэш-ключ


def test_trim_to_full_sentence_leaves_complete_text_untouched():
    assert _trim_to_full_sentence("Всё хорошо.") == "Всё хорошо."
    assert _trim_to_full_sentence("Вопрос?") == "Вопрос?"


def test_trim_to_full_sentence_cuts_mid_word_tail():
    text = "Списания превысили норму на 11%, это стоит проверить. Погода давила на трафик, но ст"
    assert _trim_to_full_sentence(text) == "Списания превысили норму на 11%, это стоит проверить."


def test_trim_to_full_sentence_keeps_text_with_no_sentence_boundary():
    assert _trim_to_full_sentence("ответ без точки") == "ответ без точки"


def test_generate_commentary_trims_truncated_response():
    rows = [_row("2026-09-01", 1000), _row("2026-09-02", 2000)]
    truncated = "Выручка выросла заметно. А связь с погодой требует отдельной провер"
    with patch("dashboard.commentary.ask_claude", return_value=truncated):
        result = generate_commentary("Тест", "неделя", rows, [], {})
    assert result == "Выручка выросла заметно."


def test_aggregate_series_basic():
    rows = [_row("2026-09-01", 1000, temp=10, precip=0), _row("2026-09-02", 2000, temp=20, precip=5)]
    agg = aggregate_series(rows)
    assert agg["days_count"] == 2
    assert agg["revenue_total"] == 3000
    assert agg["rain_days"] == 1
    assert agg["temp_avg"] == 15
