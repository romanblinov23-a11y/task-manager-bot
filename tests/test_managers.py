from monitoring.managers import (
    approve_manager,
    get_manager_blocks,
    get_market_supervisor,
    is_active_manager,
    is_market_editor,
    register_manager,
    set_manager_blocks,
    set_manager_position,
)
from monitoring.markets import create_market


def test_register_manager_gets_both_blocks_by_default():
    market = create_market("Рынок блоков")
    register_manager(5001, "Тест", "Управляющий", market["id"])
    assert set(get_manager_blocks(5001)) == {"tasks", "monitoring"}


def test_is_active_manager_requires_monitoring_block():
    market = create_market("Рынок блоков")
    register_manager(5002, "Тест", "Менеджер", market["id"])
    approve_manager(5002)
    assert is_active_manager(5002) is True

    set_manager_blocks(5002, ["tasks"])
    assert is_active_manager(5002) is False


def test_is_market_editor_requires_supervisor_position_and_monitoring_block():
    market = create_market("Рынок блоков")
    register_manager(5003, "Тест", "Менеджер", market["id"])
    approve_manager(5003)
    assert is_market_editor(5003) is False

    set_manager_position(5003, "Управляющий")
    assert is_market_editor(5003) is True

    set_manager_blocks(5003, ["tasks"])
    assert is_market_editor(5003) is False


def test_get_market_supervisor_finds_active_supervisor():
    market = create_market("Рынок супервайзера")
    register_manager(5004, "Игорь", "Управляющий", market["id"])
    assert get_market_supervisor(market["id"]) is None  # ещё pending

    approve_manager(5004)
    supervisor = get_market_supervisor(market["id"])
    assert supervisor is not None
    assert supervisor["telegram_user_id"] == 5004


def test_get_market_supervisor_excludes_given_user():
    market = create_market("Рынок супервайзера")
    register_manager(5005, "Игорь", "Управляющий", market["id"])
    approve_manager(5005)
    assert get_market_supervisor(market["id"], exclude_telegram_user_id=5005) is None


def test_get_market_supervisor_ignores_non_supervisor_positions():
    market = create_market("Рынок супервайзера")
    register_manager(5006, "Маша", "Маркетолог", market["id"])
    approve_manager(5006)
    assert get_market_supervisor(market["id"]) is None


def test_get_market_supervisor_ignores_removed_supervisor():
    from monitoring.managers import remove_manager

    market = create_market("Рынок супервайзера")
    register_manager(5007, "Игорь", "Управляющий", market["id"])
    approve_manager(5007)
    remove_manager(5007)
    assert get_market_supervisor(market["id"]) is None
