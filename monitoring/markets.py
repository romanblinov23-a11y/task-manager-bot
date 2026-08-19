from monitoring.db import get_connection


def list_markets() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM market ORDER BY name").fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


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
    """Добавляет новый рынок/проект — используется владельцем, когда открывается
    новая точка Surf сверх исходного списка PROJECTS."""
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
