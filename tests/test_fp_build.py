from fp_report.build import ASK, _flatten, _hours, _money, _pnl, _prev_ym


def test_prev_ym_crosses_year():
    assert _prev_ym("2026-01") == "2025-12"
    assert _prev_ym("2026-09") == "2026-08"


def test_pnl_picks_month_plan_and_fact():
    fields = [{"code": "income", "results": [{"period": "2026-09-01", "plan": 100, "fact": 90}]}]
    flat = _flatten(fields)
    assert _pnl(flat, "income", "2026-09") == (100, 90)
    assert _pnl(flat, "income", "2026-08") == (None, None)
    assert _pnl(flat, "missing", "2026-09") == (None, None)


def test_formatters_use_russian_separators_and_placeholder():
    assert _money(1234.5) == "1 234,50"
    assert _money(None) == ASK
    assert _hours(103.5) == "103,5"
