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


def fmt_date(iso_date: str | None) -> str:
    """Конвертирует ISO-дату YYYY-MM-DD в читаемый ДД.ММ.ГГГГ для показа людям.
    Внутреннее хранение в Sheets и передача в Claude остаются в ISO-формате."""
    if not iso_date:
        return "—"
    try:
        return date.fromisoformat(iso_date).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return iso_date
