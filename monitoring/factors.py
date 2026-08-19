from monitoring.db import get_connection


def save_factors(
    competitor_id: int,
    product: str = "",
    atmosphere: str = "",
    service: str = "",
    brand_strength: str = "",
    labor_market: str = "",
) -> None:
    """Факторы формирования версионируются — каждое обновление это новая
    строка с текущим recorded_at, старые версии не перезаписываются (§4)."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO competitor_factors
                (competitor_id, product, atmosphere, service, brand_strength, labor_market)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (competitor_id, product, atmosphere, service, brand_strength, labor_market),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_factors(competitor_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM competitor_factors WHERE competitor_id = ? ORDER BY recorded_at DESC LIMIT 1",
            (competitor_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()
