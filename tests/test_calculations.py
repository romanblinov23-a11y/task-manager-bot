from monitoring.calculations import (
    compute_market_capacity,
    compute_share,
    is_anomaly,
    is_sudden_change,
    rolling_average,
)


def test_market_capacity_sums_all_readings():
    assert compute_market_capacity({1: 100.0, 2: 50.0, 3: 25.0}) == 175.0


def test_market_capacity_empty_is_zero():
    assert compute_market_capacity({}) == 0.0


def test_share_normal_case():
    assert compute_share(50.0, 200.0) == 25.0


def test_share_zero_capacity_does_not_divide_by_zero():
    assert compute_share(50.0, 0.0) == 0.0


def test_rolling_average_empty_history_is_none():
    assert rolling_average([]) is None


def test_rolling_average_uses_last_window_only():
    # ANOMALY_WINDOW_READINGS = 4 — старые значения за пределами окна не учитываются
    assert rolling_average([1000, 100, 110, 90, 100]) == 100.0


def test_is_anomaly_within_threshold_is_false():
    # норма 100, значение 115 -> отклонение 15% < 20%
    assert is_anomaly(115, [100, 100, 100, 100]) is False


def test_is_anomaly_beyond_threshold_is_true():
    # норма 100, значение 130 -> отклонение 30% >= 20%
    assert is_anomaly(130, [100, 100, 100, 100]) is True


def test_is_anomaly_boundary_20_percent_counts_as_anomaly():
    assert is_anomaly(120, [100, 100, 100, 100]) is True


def test_is_anomaly_with_no_history_is_false():
    assert is_anomaly(500, []) is False


def test_is_anomaly_with_zero_norm_is_false():
    assert is_anomaly(10, [0, 0, 0, 0]) is False


def test_is_sudden_change_detects_doubling_up():
    assert is_sudden_change(220, 100) is True


def test_is_sudden_change_detects_halving_down():
    assert is_sudden_change(45, 100) is True


def test_is_sudden_change_normal_variation_is_false():
    assert is_sudden_change(115, 100) is False


def test_is_sudden_change_with_no_previous_is_false():
    assert is_sudden_change(1000, None) is False
