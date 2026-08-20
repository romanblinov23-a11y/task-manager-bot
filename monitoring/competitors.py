from monitoring.db import get_connection

_CODE_ALPHABET = "АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЭЮЯ"


def list_competitors(market_id: int, *, include_closed: bool = False) -> list[dict]:
    conn = get_connection()
    try:
        query = "SELECT * FROM competitor WHERE market_id = ?"
        if not include_closed:
            query += " AND status = 'active'"
        query += " ORDER BY is_own DESC, code"
        rows = conn.execute(query, (market_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_competitor(competitor_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM competitor WHERE id = ?", (competitor_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_own_competitor(market_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM competitor WHERE market_id = ? AND is_own = 1 LIMIT 1", (market_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def next_code(market_id: int) -> str:
    conn = get_connection()
    try:
        used = {row["code"] for row in conn.execute("SELECT code FROM competitor WHERE market_id = ?", (market_id,))}
    finally:
        conn.close()
    for letter in _CODE_ALPHABET:
        if letter not in used:
            return letter
    return str(len(used) + 1)


def code_taken(market_id: int, code: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM competitor WHERE market_id = ? AND code = ?", (market_id, code)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def create_competitor(
    market_id: int, code: str, name: str, address: str, format_: str, is_own: bool = False
) -> dict:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO competitor (market_id, code, name, address, format, is_own)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (market_id, code, name, address, format_, 1 if is_own else 0),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM competitor WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def close_competitor(competitor_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE competitor SET status = 'closed', closed_at = datetime('now') WHERE id = ?", (competitor_id,)
        )
        conn.commit()
    finally:
        conn.close()


def reopen_competitor(competitor_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE competitor SET status = 'active', closed_at = NULL WHERE id = ?", (competitor_id,))
        conn.commit()
    finally:
        conn.close()


def set_own_competitor(market_id: int, competitor_id: int) -> None:
    """Переназначает, какая точка на рынке — наша (Surf): снимает флаг
    is_own со всех точек рынка и ставит его на выбранную. Единственный
    способ исправить ситуацию, если во время /add_competitor флаг случайно
    достался не той точке — раньше это можно было поправить только прямым
    запросом к базе (см. /set_own_point)."""
    conn = get_connection()
    try:
        conn.execute("UPDATE competitor SET is_own = 0 WHERE market_id = ?", (market_id,))
        conn.execute("UPDATE competitor SET is_own = 1 WHERE id = ? AND market_id = ?", (competitor_id, market_id))
        conn.commit()
    finally:
        conn.close()
