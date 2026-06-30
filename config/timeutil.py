from datetime import date, datetime, timedelta

from config.settings import TZ

# Первые три буквы → номер месяца (покрывает полные и сокращённые русские названия)
_RU_MONTH_MAP: dict[str, int] = {
    'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4,
    'май': 5, 'мая': 5, 'июн': 6, 'июл': 7, 'авг': 8,
    'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12,
}

_RU_WEEKDAYS: dict[str, int] = {
    'понедельник': 0, 'вторник': 1, 'среда': 2, 'среду': 2,
    'четверг': 3, 'пятница': 4, 'пятницу': 4, 'суббота': 5, 'субботу': 5,
    'воскресенье': 6,
}


def now() -> datetime:
    """Текущее время в TIMEZONE, а не в системном часовом поясе хоста."""
    return datetime.now(TZ)


def now_naive() -> datetime:
    """Текущее время в TIMEZONE без tzinfo — для сравнения с таймстемпами
    из таблиц, которые хранятся как обычные строки без часового пояса."""
    return now().replace(tzinfo=None)


def today() -> date:
    return now().date()


def _next_weekday(weekday: int) -> date:
    """Ближайший следующий день недели (0=пн … 6=вс), не раньше завтра."""
    t = today()
    days_ahead = (weekday - t.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return t + timedelta(days=days_ahead)


def parse_date(user_input: str) -> str | None:
    """Парсит дату в любом разумном формате, возвращает ISO YYYY-MM-DD или None.

    Поддерживает:
    - ДД.ММ.ГГГГ / ГГГГ-ММ-ДД / ДД/ММ/ГГГГ
    - ДД.ММ (год текущий/следующий)
    - «09 сентября», «9 сент 2026», «9 сентября 2026»
    - завтра, послезавтра, через N дней/неделю/месяц
    - в понедельник/пятницу/… (ближайший следующий)
    """
    if not user_input:
        return None
    text = user_input.strip()
    low = text.lower()

    # 1. Стандартные форматы с явным годом
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue

    # 2. ДД.ММ без года → текущий год (или следующий, если дата уже прошла)
    parts2 = text.split(".")
    if len(parts2) == 2 and all(p.isdigit() for p in parts2):
        try:
            t = today()
            d = date(t.year, int(parts2[1]), int(parts2[0]))
            if d < t:
                d = d.replace(year=t.year + 1)
            return d.isoformat()
        except ValueError:
            pass

    # 3. Ключевые слова
    if low in ("завтра", "tomorrow"):
        return (today() + timedelta(days=1)).isoformat()
    if low in ("послезавтра",):
        return (today() + timedelta(days=2)).isoformat()
    if low in ("через неделю",):
        return (today() + timedelta(weeks=1)).isoformat()
    if low in ("через месяц",):
        t = today()
        m = t.month % 12 + 1
        y = t.year + (1 if t.month == 12 else 0)
        return t.replace(year=y, month=m).isoformat()

    # 4. «через N дней»
    if low.startswith("через ") and low.endswith(" дн"):
        try:
            n = int(low.split()[1])
            return (today() + timedelta(days=n)).isoformat()
        except (ValueError, IndexError):
            pass

    # 5. «в понедельник», «в пятницу» и т.д.
    for wd_name, wd_num in _RU_WEEKDAYS.items():
        if wd_name in low:
            return _next_weekday(wd_num).isoformat()

    # 6. «DD месяц [YYYY]» — русские названия месяцев
    parts = text.split()
    if len(parts) >= 2:
        try:
            day = int(parts[0])
            month_key = parts[1].lower()[:3]
            month = _RU_MONTH_MAP.get(month_key)
            if month:
                if len(parts) >= 3 and parts[2].isdigit() and len(parts[2]) == 4:
                    year = int(parts[2])
                else:
                    year = today().year
                    d = date(year, month, day)
                    if d < today():
                        year += 1
                return date(year, month, day).isoformat()
        except (ValueError, TypeError, IndexError):
            pass

    return None


def fmt_date(iso_date: str | None) -> str:
    """Конвертирует ISO-дату YYYY-MM-DD в читаемый ДД.ММ.ГГГГ для показа людям.
    Внутреннее хранение в Sheets и передача в Claude остаются в ISO-формате."""
    if not iso_date:
        return "—"
    try:
        return date.fromisoformat(iso_date).strftime("%d.%m.%Y")
    except (ValueError, TypeError):
        return iso_date
