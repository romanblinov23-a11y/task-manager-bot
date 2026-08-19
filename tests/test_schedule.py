from monitoring.markets import create_market
from monitoring.schedule import get_schedule, list_markets_scheduled_for_weekday, set_schedule


def test_set_and_get_schedule_roundtrip():
    market = create_market("Рынок расписания")
    set_schedule(market["id"], [1, 3, 5])
    schedule = get_schedule(market["id"])
    assert schedule["weekdays"] == [1, 3, 5]
    assert schedule["active"] == 1


def test_set_schedule_is_idempotent_upsert():
    market = create_market("Рынок расписания")
    set_schedule(market["id"], [1, 3, 5])
    set_schedule(market["id"], [2, 4])
    schedule = get_schedule(market["id"])
    assert schedule["weekdays"] == [2, 4]


def test_list_markets_scheduled_for_weekday_filters_correctly():
    market_a = create_market("Рынок А")
    market_b = create_market("Рынок Б")
    set_schedule(market_a["id"], [1, 3])
    set_schedule(market_b["id"], [2])

    assert list_markets_scheduled_for_weekday(1) == [market_a["id"]]
    assert list_markets_scheduled_for_weekday(2) == [market_b["id"]]
    assert list_markets_scheduled_for_weekday(7) == []


def test_get_schedule_missing_returns_none():
    market = create_market("Рынок без расписания")
    assert get_schedule(market["id"]) is None
