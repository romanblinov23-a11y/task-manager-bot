from monitoring.constants import ANOMALY_THRESHOLD_PCT, ANOMALY_WINDOW_READINGS


def compute_market_capacity(readings: dict[int, float]) -> float:
    """Ёмкость рынка (§7): сумма среднего числа чеков в день по всем
    активным конкурентам, включая точку Surf."""
    return sum(readings.values())


def compute_share(value: float, capacity: float) -> float:
    """Доля сущности в рынке, в процентах (§7)."""
    if capacity <= 0:
        return 0.0
    return value / capacity * 100


def rolling_average(previous_values: list[float]) -> float | None:
    """Скользящее среднее по последним ANOMALY_WINDOW_READINGS снятиям (§7).
    previous_values — история в хронологическом порядке (старые → новые),
    без учёта текущего снятия."""
    if not previous_values:
        return None
    window = previous_values[-ANOMALY_WINDOW_READINGS:]
    return sum(window) / len(window)


def is_anomaly(current: float, previous_values: list[float], threshold_pct: float = ANOMALY_THRESHOLD_PCT) -> bool:
    """Аномалия — отклонение ±threshold_pct% от 4-недельной нормы (§7)."""
    avg = rolling_average(previous_values)
    if not avg:
        return False
    deviation = abs(current - avg) / avg * 100
    return deviation >= threshold_pct


def is_sudden_change(current: float, previous: float | None, factor: float = 2.0) -> bool:
    """Мягкая проверка «точно?» (§9): разница в factor и более раз с
    предыдущим снятием — не блокирует ввод, только предупреждает."""
    if not previous:
        return False
    ratio = current / previous
    return ratio >= factor or ratio <= 1 / factor
