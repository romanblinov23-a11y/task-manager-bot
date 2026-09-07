from config.settings import SHIFT_REPORT_START_TIME
from monitoring.db import get_connection


def list_markets() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM market ORDER BY name").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_market_names() -> list[str]:
    """Рынок и проект — одна и та же сущность (market 1:1 с проектом), это
    единственный источник правды для списка проектов в трекере задач."""
    return [m["name"] for m in list_markets()]


def get_market(market_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM market WHERE id = ?", (market_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_market_by_name(name: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM market WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_market_operator(market_id: int, name: str, inn: str, ogrn: str, address: str) -> None:
    """Реквизиты юрлица/ИП — юридического оператора персональных данных
    сотрудников этой точки (см. /set_operator, personal_data/consent.py).
    Не сам Роман и не бот — конкретное юрлицо, с которым у Романа договор
    оказания услуг на эту точку."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE market SET operator_name = ?, operator_inn = ?, operator_ogrn = ?, operator_address = ? WHERE id = ?",
            (name, inn, ogrn, address, market_id),
        )
        conn.commit()
    finally:
        conn.close()


def has_operator_info(market: dict) -> bool:
    return bool(market.get("operator_name"))


def set_market_consent_text(market_id: int, text: str) -> None:
    """Готовый текст согласия на обработку ПДн от юристов оператора —
    если задан, используется вместо автосгенерированного по реквизитам
    (см. personal_data.consent.render_consent_text). Пустая строка
    возвращает к автогенерации."""
    conn = get_connection()
    try:
        conn.execute("UPDATE market SET custom_consent_text = ? WHERE id = ?", (text, market_id))
        conn.commit()
    finally:
        conn.close()


def has_consent_text_ready(market: dict) -> bool:
    """Можно ли уже показать стажёру текст согласия — либо загружен
    готовый текст от юристов, либо заполнены реквизиты для автогенерации."""
    return bool(market.get("custom_consent_text")) or has_operator_info(market)


def set_market_shift_report_time(market_id: int, time_str: str) -> None:
    """Своё время начала сбора вечернего отчёта на точке (управляющий
    задаёт через /set_evening_report) — точки закрываются в разное время,
    единого времени на всех не бывает. Пустая строка возвращает к
    глобальному дефолту SHIFT_REPORT_START_TIME (см. get_effective_shift_report_time)."""
    conn = get_connection()
    try:
        conn.execute("UPDATE market SET shift_report_time = ? WHERE id = ?", (time_str, market_id))
        conn.commit()
    finally:
        conn.close()


def get_effective_shift_report_time(market: dict) -> str:
    return market.get("shift_report_time") or SHIFT_REPORT_START_TIME


def set_market_send_to_finance(market_id: int, enabled: bool) -> None:
    """Решает Рома (не Управляющий) — не у каждой точки есть отдельный чат
    финпартнёров. Когда выключено, отчёт финпартнёрам всё равно приходит
    Роме, но только для ознакомления — без обязательного согласования и
    без реальной отправки в чат (см. bot.shift_reports._send_for_owner_approval)."""
    conn = get_connection()
    try:
        conn.execute("UPDATE market SET send_to_finance = ? WHERE id = ?", (1 if enabled else 0, market_id))
        conn.commit()
    finally:
        conn.close()


def create_market(name: str, city: str = "", our_point_name: str | None = None) -> dict:
    """Добавляет новый рынок/проект — используется владельцем через /add_project.
    Рынок и проект в системе — одна сущность: как только он появляется здесь,
    он сразу доступен и в трекере задач (см. monitoring.markets.list_market_names)."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "INSERT INTO market (name, city, our_point_name) VALUES (?, ?, ?)",
            (name, city, our_point_name or name),
        )
        conn.commit()
        return get_market(cursor.lastrowid)
    finally:
        conn.close()
