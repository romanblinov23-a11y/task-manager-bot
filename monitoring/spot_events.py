"""Недельные планы мероприятий на спотах."""
from datetime import date, timedelta

from monitoring.db import get_connection


def replace_week(market_id: int, week_start: str, events: list[dict], author_id: int) -> None:
    end = (date.fromisoformat(week_start) + timedelta(days=7)).isoformat()
    conn = get_connection()
    try:
        with conn:
            conn.execute('DELETE FROM spot_event WHERE market_id = ? AND event_date >= ? AND event_date < ?',
                         (market_id, week_start, end))
            _mark_submitted(conn, market_id, week_start, author_id)
            conn.executemany(
                'INSERT INTO spot_event (market_id, event_date, event_time, title, participants, created_by) VALUES (?, ?, ?, ?, ?, ?)',
                [(market_id, e['date'], e['time'], e['title'], e['participants'], author_id) for e in events],
            )
    finally:
        conn.close()


def events_for_day(market_id: int, date_iso: str) -> list[dict]:
    conn = get_connection()
    try:
        return [dict(row) for row in conn.execute(
            'SELECT * FROM spot_event WHERE market_id = ? AND event_date = ? ORDER BY event_time, id',
            (market_id, date_iso),
        )]
    finally:
        conn.close()


def add_events(market_id: int, events: list[dict], author_id: int) -> int:
    """Добавляет события, сохраняя план; повторная отправка не создаёт дубли."""
    conn = get_connection()
    added = 0
    try:
        with conn:
            for event in events:
                existing = conn.execute(
                    'SELECT title, event_time FROM spot_event WHERE market_id = ? AND event_date = ?',
                    (market_id, event['date']),
                ).fetchall()
                if any(row['event_time'] == event['time'] and row['title'].casefold() == event['title'].casefold()
                       for row in existing):
                    continue
                if len(existing) >= 8:
                    raise ValueError(f"На {event['date']} уже запланировано 8 мероприятий. Используйте замену плана недели.")
                conn.execute(
                    'INSERT INTO spot_event (market_id, event_date, event_time, title, participants, created_by) VALUES (?, ?, ?, ?, ?, ?)',
                    (market_id, event['date'], event['time'], event['title'], event['participants'], author_id),
                )
                added += 1
            for week in { (date.fromisoformat(e['date']) - timedelta(days=date.fromisoformat(e['date']).weekday())).isoformat() for e in events }:
                _mark_submitted(conn, market_id, week, author_id)
        return added
    finally:
        conn.close()


def _mark_submitted(conn, market_id: int, week: str, author_id: int) -> None:
    conn.execute(
        "INSERT INTO spot_event_week (market_id, week_start, submitted_by) VALUES (?, ?, ?) "
        "ON CONFLICT (market_id, week_start) DO UPDATE SET submitted_by=excluded.submitted_by, submitted_at=datetime('now')",
        (market_id, week, author_id),
    )


def week_submitted(market_id: int, week: str) -> bool:
    conn = get_connection()
    try:
        return conn.execute('SELECT 1 FROM spot_event_week WHERE market_id=? AND week_start=?', (market_id, week)).fetchone() is not None
    finally:
        conn.close()


def notice_sent(market_id: int, week: str, stage: str, recipient: int) -> bool:
    conn = get_connection()
    try:
        return conn.execute('SELECT 1 FROM spot_event_notice WHERE market_id=? AND week_start=? AND stage=? AND recipient=?',
                            (market_id, week, stage, recipient)).fetchone() is not None
    finally:
        conn.close()


def mark_notice_sent(market_id: int, week: str, stage: str, recipient: int) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute('INSERT OR IGNORE INTO spot_event_notice (market_id, week_start, stage, recipient) VALUES (?, ?, ?, ?)',
                         (market_id, week, stage, recipient))
    finally:
        conn.close()


def marketer_regulation_read(uid: int) -> bool:
    conn = get_connection()
    try:
        return conn.execute('SELECT 1 FROM spot_event_regulation_ack WHERE telegram_user_id=?', (uid,)).fetchone() is not None
    finally:
        conn.close()


def acknowledge_marketer_regulation(uid: int) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute('INSERT OR IGNORE INTO spot_event_regulation_ack (telegram_user_id) VALUES (?)', (uid,))
    finally:
        conn.close()
