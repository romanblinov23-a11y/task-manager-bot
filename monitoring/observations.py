from monitoring.db import get_connection


def create_observation(competitor_id: int, market_id: int, category: str, text: str, created_by: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO observation (competitor_id, market_id, category, text, created_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (competitor_id, market_id, category, text, created_by),
        )
        conn.commit()
    finally:
        conn.close()


def get_observations(market_id: int, since: str | None = None) -> list[dict]:
    conn = get_connection()
    try:
        query = "SELECT * FROM observation WHERE market_id = ?"
        params: list = [market_id]
        if since:
            query += " AND observed_at >= ?"
            params.append(since)
        query += " ORDER BY observed_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
