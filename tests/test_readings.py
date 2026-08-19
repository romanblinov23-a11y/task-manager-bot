from config.timeutil import today
from monitoring.competitors import create_competitor
from monitoring.constants import MONITORING_CYCLE_DAYS
from monitoring.db import get_connection
from monitoring.markets import create_market
from monitoring.readings import get_competitors_pending_this_cycle, get_latest_reading, get_readings, record_reading


def _make_competitor():
    market = create_market("Тестовый рынок")
    return create_competitor(market["id"], "А", "Конкурент А", "адрес", "посадка")


def test_record_reading_creates_new_row():
    competitor = _make_competitor()
    reading = record_reading(competitor["id"], 100.0, created_by=1)
    assert reading["avg_checks_per_day"] == 100.0
    assert len(get_readings(competitor["id"])) == 1


def test_record_reading_within_same_cycle_updates_not_duplicates():
    """§7/§9: повторное снятие в тот же недельный цикл обновляет запись,
    а не плодит дубль — 'дельты' не ломаются при повторном запуске."""
    competitor = _make_competitor()
    first = record_reading(competitor["id"], 100.0, created_by=1)
    second = record_reading(competitor["id"], 120.0, created_by=1, note="уточнил")

    assert first["id"] == second["id"]
    assert len(get_readings(competitor["id"])) == 1
    assert get_latest_reading(competitor["id"])["avg_checks_per_day"] == 120.0


def test_record_reading_after_cycle_window_creates_new_row():
    competitor = _make_competitor()
    first = record_reading(competitor["id"], 100.0, created_by=1)

    # искусственно "состариваем" снятие за пределы окна цикла
    stale_date = today().fromordinal(today().toordinal() - MONITORING_CYCLE_DAYS - 1).isoformat()
    conn = get_connection()
    conn.execute("UPDATE daily_avg_reading SET reading_at = ? WHERE id = ?", (stale_date, first["id"]))
    conn.commit()
    conn.close()

    record_reading(competitor["id"], 130.0, created_by=1)
    readings = get_readings(competitor["id"])
    assert len(readings) == 2


def test_pending_this_cycle_tracks_missing_readings():
    market = create_market("Рынок с несколькими точками")
    comp_a = create_competitor(market["id"], "А", "Конкурент А", "адрес", "посадка")
    comp_b = create_competitor(market["id"], "Б", "Конкурент Б", "адрес", "полный")

    pending = get_competitors_pending_this_cycle(market["id"])
    assert {c["id"] for c in pending} == {comp_a["id"], comp_b["id"]}

    record_reading(comp_a["id"], 100.0, created_by=1)
    pending = get_competitors_pending_this_cycle(market["id"])
    assert {c["id"] for c in pending} == {comp_b["id"]}

    record_reading(comp_b["id"], 80.0, created_by=1)
    assert get_competitors_pending_this_cycle(market["id"]) == []
