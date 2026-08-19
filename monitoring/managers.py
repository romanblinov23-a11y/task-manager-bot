from config.settings import OWNER_TELEGRAM_IDS
from monitoring.db import get_connection


def is_owner(telegram_user_id: int) -> bool:
    return str(telegram_user_id) in OWNER_TELEGRAM_IDS


def get_manager(telegram_user_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM manager WHERE telegram_user_id = ?", (telegram_user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def register_manager(telegram_user_id: int, name: str, position: str, market_id: int) -> dict:
    """Онбординг менеджера: создаёт (или обновляет) запись manager со статусом
    'pending' и привязывает его к рынку. Доступ к боту менеджер получает
    только после подтверждения владельцем (см. approve_manager)."""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO manager (telegram_user_id, name, role, position, status)
            VALUES (?, ?, 'manager', ?, 'pending')
            ON CONFLICT (telegram_user_id) DO UPDATE SET name = excluded.name, position = excluded.position
            """,
            (telegram_user_id, name, position),
        )
        conn.execute(
            "INSERT OR IGNORE INTO manager_market (manager_telegram_user_id, market_id) VALUES (?, ?)",
            (telegram_user_id, market_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM manager WHERE telegram_user_id = ?", (telegram_user_id,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def is_active_manager(telegram_user_id: int) -> bool:
    """True, если пользователь подтверждён владельцем и может пользоваться
    модулем мониторинга (или является владельцем — у того доступ всегда)."""
    if is_owner(telegram_user_id):
        return True
    manager = get_manager(telegram_user_id)
    return bool(manager and manager["status"] == "active")


def list_managers() -> list[dict]:
    """Все менеджеры (любого статуса) с их рынками — для /managers."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM manager ORDER BY status, name").fetchall()
        managers = [dict(row) for row in rows]
    finally:
        conn.close()
    for manager in managers:
        manager["markets"] = get_markets_for_manager(manager["telegram_user_id"])
    return managers


def list_pending_managers() -> list[dict]:
    return [m for m in list_managers() if m["status"] == "pending"]


def approve_manager(telegram_user_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE manager SET status = 'active' WHERE telegram_user_id = ?", (telegram_user_id,)
        )
        conn.commit()
    finally:
        conn.close()


def reject_manager(telegram_user_id: int) -> None:
    """Отклонённая заявка ещё не успела ничего накопить (снятий/наблюдений
    у pending-менеджера быть не может) — поэтому удаляется полностью, а не
    помечается статусом, в отличие от remove_manager."""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM manager_market WHERE manager_telegram_user_id = ?", (telegram_user_id,)
        )
        conn.execute("DELETE FROM manager WHERE telegram_user_id = ?", (telegram_user_id,))
        conn.commit()
    finally:
        conn.close()


def remove_manager(telegram_user_id: int) -> None:
    """Отзывает доступ у уже активного менеджера. Запись и его исторические
    снятия/наблюдения не удаляются — это soft-delete через статус."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE manager SET status = 'removed' WHERE telegram_user_id = ?", (telegram_user_id,)
        )
        conn.commit()
    finally:
        conn.close()


def restore_manager(telegram_user_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE manager SET status = 'active' WHERE telegram_user_id = ?", (telegram_user_id,)
        )
        conn.commit()
    finally:
        conn.close()


def set_manager_position(telegram_user_id: int, position: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE manager SET position = ? WHERE telegram_user_id = ?", (position, telegram_user_id)
        )
        conn.commit()
    finally:
        conn.close()


def set_manager_market(telegram_user_id: int, market_id: int) -> None:
    """Переназначает менеджера на другой рынок/проект — заменяет все текущие
    привязки одной новой (в отличие от link_market, который добавляет)."""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM manager_market WHERE manager_telegram_user_id = ?", (telegram_user_id,)
        )
        conn.execute(
            "INSERT INTO manager_market (manager_telegram_user_id, market_id) VALUES (?, ?)",
            (telegram_user_id, market_id),
        )
        conn.commit()
    finally:
        conn.close()


def link_market(telegram_user_id: int, market_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO manager_market (manager_telegram_user_id, market_id) VALUES (?, ?)",
            (telegram_user_id, market_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_markets_for_manager(telegram_user_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT market.* FROM market
            JOIN manager_market ON manager_market.market_id = market.id
            WHERE manager_market.manager_telegram_user_id = ?
            ORDER BY market.name
            """,
            (telegram_user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_managers_for_market(market_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT manager.* FROM manager
            JOIN manager_market ON manager_market.manager_telegram_user_id = manager.telegram_user_id
            WHERE manager_market.market_id = ?
            """,
            (market_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
