from monitoring.db import get_connection


def _with_trigger_list(row: dict) -> dict:
    row["trigger_list"] = [t for t in row["triggers"].split(",") if t]
    return row


def register_monitor_chat(chat_id: int, title: str) -> None:
    """Ставит чат под мониторинг триггерных слов и упоминаний владельца —
    не привязан ни к какому проекту/рынку, в отличие от рабочих чатов
    (/register_project) и чатов рассылок отчётов (/register_report_chat)."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO monitor_chat (chat_id, title) VALUES (?, ?) ON CONFLICT (chat_id) DO UPDATE SET title = excluded.title",
            (chat_id, title),
        )
        conn.commit()
    finally:
        conn.close()


def set_monitor_chat_triggers(chat_id: int, triggers: list[str]) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE monitor_chat SET triggers = ? WHERE chat_id = ?", (",".join(triggers), chat_id))
        conn.commit()
    finally:
        conn.close()


def get_monitor_chat(chat_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM monitor_chat WHERE chat_id = ?", (chat_id,)).fetchone()
        return _with_trigger_list(dict(row)) if row else None
    finally:
        conn.close()


def list_monitor_chats() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM monitor_chat ORDER BY title").fetchall()
        return [_with_trigger_list(dict(row)) for row in rows]
    finally:
        conn.close()


def remove_monitor_chat(chat_id: int) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM monitor_chat WHERE chat_id = ?", (chat_id,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
