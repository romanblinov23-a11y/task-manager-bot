from monitoring.competitors import code_taken, create_competitor, get_own_competitor, next_code
from monitoring.markets import create_market


def test_next_code_starts_from_first_letter():
    market = create_market("Рынок кодов")
    assert next_code(market["id"]) == "А"


def test_next_code_skips_taken_letters():
    market = create_market("Рынок кодов")
    create_competitor(market["id"], "А", "Конкурент А", "адрес", "посадка")
    assert next_code(market["id"]) == "Б"


def test_code_taken_reflects_existing_competitors():
    market = create_market("Рынок кодов")
    assert code_taken(market["id"], "А") is False
    create_competitor(market["id"], "А", "Конкурент А", "адрес", "посадка")
    assert code_taken(market["id"], "А") is True


def test_get_own_competitor_finds_only_is_own():
    market = create_market("Рынок точек")
    create_competitor(market["id"], "А", "Конкурент А", "адрес", "посадка", is_own=False)
    assert get_own_competitor(market["id"]) is None

    own = create_competitor(market["id"], "S", "Наша точка", "адрес", "полный", is_own=True)
    found = get_own_competitor(market["id"])
    assert found["id"] == own["id"]
