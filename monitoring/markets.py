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
