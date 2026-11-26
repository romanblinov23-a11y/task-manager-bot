"""Черновики, снимки планов и доставка: все переживает перезапуск бота."""
import json
from datetime import datetime, timezone

from monitoring.db import get_connection

SCHEMA = """
CREATE TABLE IF NOT EXISTS manager_guest_plan (
    market_id INTEGER NOT NULL REFERENCES market(id),
    effective_month TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_by INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (market_id, effective_month)
);
CREATE TABLE IF NOT EXISTS monthly_manager_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id INTEGER NOT NULL REFERENCES market(id),
    month TEXT NOT NULL,
    reporter_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'collecting',
    model TEXT NOT NULL,
    answers TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 1,
    pdf_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (market_id, month)
);
CREATE TABLE IF NOT EXISTS manager_report_dialog (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manager_report_delivery (
    report_id INTEGER NOT NULL REFERENCES monthly_manager_report(id),
    revision INTEGER NOT NULL,
    recipient INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY(report_id, revision, recipient)
);
"""


def stamp():
    return datetime.now(timezone.utc).isoformat()


def _dump(data):
    return json.dumps(data, ensure_ascii=False, allow_nan=False)


def _report(row):
    if row is None:
        return None
    result = dict(row)
    for key in ('model', 'answers'):
        result[key] = json.loads(result[key])
    return result


def save_plan(market_id, month, values, author):
    conn = get_connection()
    try:
        with conn:
            conn.execute('INSERT INTO manager_guest_plan VALUES (?, ?, ?, ?, ?) '
                         'ON CONFLICT(market_id,effective_month) DO UPDATE SET '
                         'data=excluded.data,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
                         (market_id, month, _dump(values), author, stamp()))
    finally:
        conn.close()


def get_plan(market_id, month):
    conn = get_connection()
    try:
        row = conn.execute('SELECT * FROM manager_guest_plan WHERE market_id=? AND effective_month<=? '
                           'ORDER BY effective_month DESC LIMIT 1', (market_id, month)).fetchone()
        return dict(values=json.loads(row['data']), effective_month=row['effective_month'],
                    updated_at=row['updated_at']) if row else None
    finally:
        conn.close()


def get_dialog(uid):
    conn = get_connection()
    try:
        row = conn.execute('SELECT data FROM manager_report_dialog WHERE user_id=?', (uid,)).fetchone()
        return json.loads(row['data']) if row else None
    finally:
        conn.close()


def set_dialog(uid, data):
    conn = get_connection()
    try:
        with conn:
            if data is None:
                conn.execute('DELETE FROM manager_report_dialog WHERE user_id=?', (uid,))
            else:
                conn.execute('INSERT INTO manager_report_dialog VALUES (?,?) '
                             'ON CONFLICT(user_id) DO UPDATE SET data=excluded.data', (uid, _dump(data)))
    finally:
        conn.close()


def find_report(market_id, month):
    conn = get_connection()
    try:
        return _report(conn.execute('SELECT * FROM monthly_manager_report WHERE market_id=? AND month=?',
                                   (market_id, month)).fetchone())
    finally:
        conn.close()


def get_report(report_id):
    conn = get_connection()
    try:
        return _report(conn.execute('SELECT * FROM monthly_manager_report WHERE id=?', (report_id,)).fetchone())
    finally:
        conn.close()


def create_report(market_id, month, uid, model):
    conn = get_connection()
    try:
        with conn:
            conn.execute('INSERT OR IGNORE INTO monthly_manager_report '
                         '(market_id,month,reporter_id,model,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                         (market_id, month, uid, _dump(model), stamp(), stamp()))
        return find_report(market_id, month)
    finally:
        conn.close()


def save_answers(report_id, answers, revision):
    """Не перезаписывает чужую/уже подтвержденную версию."""
    conn = get_connection()
    try:
        with conn:
            return conn.execute("UPDATE monthly_manager_report SET answers=?,revision=revision+1,"
                                "status='collecting',pdf_path=NULL,updated_at=? WHERE id=? AND revision=? "
                                "AND status IN ('collecting','review')",
                                (_dump(answers), stamp(), report_id, revision)).rowcount == 1
    finally:
        conn.close()


def mark_review(report_id, revision, path):
    conn = get_connection()
    try:
        with conn:
            return conn.execute("UPDATE monthly_manager_report SET status='review',pdf_path=?,updated_at=? "
                                "WHERE id=? AND revision=? AND status IN ('collecting','review')",
                                (str(path), stamp(), report_id, revision)).rowcount == 1
    finally:
        conn.close()


def claim_delivery(report_id, revision):
    conn = get_connection()
    try:
        with conn:
            return conn.execute("UPDATE monthly_manager_report SET status='sending',updated_at=? "
                                "WHERE id=? AND revision=? AND status IN ('review','delivery_failed')",
                                (stamp(), report_id, revision)).rowcount == 1
    finally:
        conn.close()


def set_delivery_status(report_id, status):
    if status not in ('completed', 'delivery_failed'):
        raise ValueError(status)
    conn = get_connection()
    try:
        with conn:
            conn.execute('UPDATE monthly_manager_report SET status=?,updated_at=? WHERE id=?',
                         (status, stamp(), report_id))
    finally:
        conn.close()


def delivered_to(report_id, revision):
    conn = get_connection()
    try:
        return {r['recipient'] for r in conn.execute('SELECT recipient FROM manager_report_delivery '
                                                   'WHERE report_id=? AND revision=?', (report_id, revision))}
    finally:
        conn.close()


def record_delivery(report_id, revision, uid):
    conn = get_connection()
    try:
        with conn:
            conn.execute('INSERT OR IGNORE INTO manager_report_delivery VALUES (?,?,?,?)',
                         (report_id, revision, uid, stamp()))
    finally:
        conn.close()


def recover_deliveries():
    """Состояние после остановки процесса не должно навсегда запрещать повтор."""
    conn = get_connection()
    try:
        with conn:
            conn.execute("UPDATE monthly_manager_report SET status='delivery_failed' WHERE status='sending'")
    finally:
        conn.close()
