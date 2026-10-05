from fp_report import questions as q
from fp_report import session as session_store
from fp_report.build import _split_pair, fin_period_keys
from fp_report.inventory import Position


def _inputs():
    return {
        "manager": {"fin_result": []},
        "pnl": {"fields": []},
        "komanda": {"employees": [{"name": "A"}, {"name": "B"}]},
        "shtat": {"staff_norm_value": 5, "staff_actual_value": 4},
    }


def _positions():
    return [
        Position(code="K1", name="Кофе", book_qty=10, fact_qty=2, diff_qty=-8, diff_sum=-12000),
        Position(code="K2", name="Молоко", book_qty=10, fact_qty=12, diff_qty=2, diff_sum=300),
    ]


def _no_claude(monkeypatch):
    monkeypatch.setattr(q, "find_suspicious", lambda positions: {})


def test_single_field_answer_keeps_whole_text_and_capitalizes(monkeypatch):
    question = {"fields": ["invest"]}
    assert q.apply_answer(question, "нет") == {"invest": "Нет"}
    assert q.apply_answer({"fields": ["tasks_prev"]}, "задача один\nзадача два") == {"tasks_prev": "Задача один\nзадача два"}


def test_multi_field_answer_splits_by_lines_and_strips_numbering():
    question = {"fields": ["inv_reason:K1", "inv_action:K1"]}
    assert q.apply_answer(question, "1) пересорт\n2) списали в порче") == {
        "inv_reason:K1": "пересорт",
        "inv_action:K1": "списали в порче",
    }


def test_single_line_answer_fills_first_field_only():
    question = {"fields": ["hired_official", "fired"]}
    assert q.apply_answer(question, "12") == {"hired_official": "12"}


def test_build_questions_follows_template_order_and_asks_only_gaps(monkeypatch):
    _no_claude(monkeypatch)
    items = q.build_questions(_inputs(), _positions(), "2026-09")
    keys = [item["key"] for item in items]
    # K2 (300 ₽) ниже порога и не попадает в объяснения, пока есть позиция выше порога
    # пустой P&L — значит все пропуски спрашиваются первым вопросом
    assert keys[0] == "gaps"
    assert keys[1:] == [
        "manager_name", "comment_fin", "comment_cogs", "inv:K1", "comment_inv", "staff", "comment_staff",
        "comment_fot", "invest", "payouts_plan", "ops", "comment_ops", "tasks_prev", "tasks_next", "summary",
    ]


def test_inventory_question_names_position_and_flags_suspicion(monkeypatch):
    monkeypatch.setattr(q, "find_suspicious", lambda positions: {"K1": "крупная недостача"})
    items = q.build_questions(_inputs(), _positions(), "2026-09")
    first = next(item for item in items if item["key"] == "inv:K1")
    assert "Кофе (K1)" in first["text"]
    assert "крупная недостача" in first["text"]
    assert first["fields"] == ["inv_reason:K1", "inv_action:K1"]


def test_split_pair_for_fact_and_previous_month():
    assert _split_pair("12 / 10") == ("12", "10")
    assert _split_pair("12") == ("12", "")
    assert _split_pair("") == ("", "")


def test_session_round_trip_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "FP_REPORTS_DIR", tmp_path)
    assert session_store.load_session(42) is None
    session_store.save_session(42, {"stage": "files"})
    session_store.save_inputs(42, {"pnl": {}})
    assert session_store.load_session(42) == {"stage": "files"}
    assert session_store.load_inputs(42) == {"pnl": {}}
    session_store.delete_session(42)
    assert session_store.load_session(42) is None


def test_fin_period_keys_follow_report_month():
    assert fin_period_keys("2026-09") == ("sep", "aug")
    assert fin_period_keys("2026-08") == ("aug", "jul")
    assert fin_period_keys("2026-01") == ("jan", "dec")
