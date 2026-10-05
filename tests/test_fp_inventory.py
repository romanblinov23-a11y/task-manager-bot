from unittest.mock import patch

from fp_report.anomalies import find_suspicious, rule_flags
from fp_report.inventory import Position, summarize


def _p(code, name, book, fact, diff_qty, diff_sum):
    return Position(code=code, name=name, book_qty=book, fact_qty=fact, diff_qty=diff_qty, diff_sum=diff_sum)


def test_summarize_splits_surplus_and_shortage():
    positions = [
        _p("A", "a", 10, 12, 2, 1000),
        _p("B", "b", 10, 9, -1, -500),
    ]
    s = summarize(positions)
    assert s["surplus_total"] == 1000
    assert s["shortage_total"] == -500
    assert s["net_total"] == 500
    assert s["fallback_top_n"] is True


def test_explained_only_over_threshold_when_any_exist():
    positions = [
        _p("BIG", "big", 10, 20, 10, 7000),
        _p("SMALL", "small", 10, 11, 1, 1000),
        _p("NEG", "neg", 10, 0, -10, -9000),
    ]
    s = summarize(positions)
    codes = [p.code for p in s["explained"]]
    assert codes == ["NEG", "BIG"]
    assert s["fallback_top_n"] is False


def test_top_three_fallback_when_nothing_over_threshold():
    positions = [_p(f"S{i}", f"s{i}", 10, 10 - i, -i, -100 * i) for i in range(1, 6)]
    positions += [_p(f"P{i}", f"p{i}", 10, 10 + i, i, 100 * i) for i in range(1, 6)]
    s = summarize(positions)
    assert s["fallback_top_n"] is True
    shortage_codes = [p.code for p in s["explained"] if p.diff_sum < 0]
    surplus_codes = [p.code for p in s["explained"] if p.diff_sum > 0]
    assert shortage_codes == ["S5", "S4", "S3"]
    assert surplus_codes == ["P5", "P4", "P3"]


def test_rule_flags_large_relative_difference():
    positions = [
        _p("COFFEE", "coffee", 79.4, 117.0, 37.6, 75860),
        _p("SMALL", "small", 10, 11, 1, 300),
    ]
    flags = rule_flags(positions)
    assert "COFFEE" in flags
    assert "SMALL" not in flags


def test_find_suspicious_survives_claude_failure():
    positions = [_p("COFFEE", "coffee", 79.4, 117.0, 37.6, 75860)]
    with patch("fp_report.anomalies.ask_claude", side_effect=RuntimeError("no key")):
        flags = find_suspicious(positions)
    assert "COFFEE" in flags


def test_claude_flags_only_known_codes_and_no_report_text():
    positions = [_p("MILK", "milk", 59.7, 113.4, 53.7, 5488)]
    fake = '[{"code": "MILK", "reason": "расхождение почти равно книжному остатку"}, {"code": "GHOST", "reason": "x"}]'
    with patch("fp_report.anomalies.ask_claude", return_value=fake):
        flags = find_suspicious(positions)
    assert flags["MILK"] == "расхождение почти равно книжному остатку"
    assert "GHOST" not in flags
