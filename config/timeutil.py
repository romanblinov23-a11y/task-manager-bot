from datetime import date, datetime

from config.settings import TZ


def now() -> datetime:
    """Текущее время в TIMEZONE, а не в системном часовом поясе хоста."""
    return datetime.now(TZ)


def now_naive() -> datetime:
    """Текущее время в TIMEZONE без tzinfo — для сравнения с таймстемпами
    из таблиц, которые хранятся как обычные строки без часового пояса."""
    return now().replace(tzinfo=None)


def today() -> date:
    return now().date()
