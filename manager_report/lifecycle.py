"""Сроки, неизменяемые версии, решения Романа и очередь уведомлений."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from monitoring.db import get_connection
from manager_report.storage import stamp, _dump, _report

MOSCOW=ZoneInfo('Europe/Moscow')
SCHEMA='''
CREATE TABLE IF NOT EXISTS manager_report_cycle (
 month TEXT PRIMARY KEY,activated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manager_report_workflow (
 report_id INTEGER PRIMARY KEY REFERENCES monthly_manager_report(id),
 first_submitted_at TEXT NOT NULL,
 submitted_at TEXT NOT NULL,
 revision INTEGER NOT NULL,
 state TEXT NOT NULL,
 feedback TEXT,
 rework_due TEXT,
 accepted_at TEXT
);
CREATE TABLE IF NOT EXISTS manager_report_version (
 report_id INTEGER NOT NULL, revision INTEGER NOT NULL,
 snapshot TEXT NOT NULL, submitted_at TEXT NOT NULL,
 PRIMARY KEY(report_id,revision)
);
CREATE TABLE IF NOT EXISTS manager_report_decision (
 id INTEGER PRIMARY KEY AUTOINCREMENT,report_id INTEGER NOT NULL,revision INTEGER NOT NULL,
 action TEXT NOT NULL,comment TEXT NOT NULL,deadline TEXT,decided_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manager_report_lifecycle_dialog (
 user_id INTEGER PRIMARY KEY,data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manager_report_status (
 market_id INTEGER NOT NULL,month TEXT NOT NULL,reporter_id INTEGER NOT NULL,
 data TEXT NOT NULL,answered_at TEXT NOT NULL,PRIMARY KEY(market_id,month)
);
CREATE TABLE IF NOT EXISTS manager_report_notice (
 notice_key TEXT PRIMARY KEY,recipient INTEGER NOT NULL,text TEXT NOT NULL,
 buttons TEXT NOT NULL, sent_at TEXT
);
'''


def now():return datetime.now(MOSCOW)


def deadline(month):
    year,mon=map(int,month.split('-'))
    return datetime(year+(mon==12),mon%12+1,5,18,tzinfo=MOSCOW)


def workflow(report_id):
    c=get_connection()
    try:
        r=c.execute('SELECT * FROM manager_report_workflow WHERE report_id=?',(report_id,)).fetchone()
        return dict(r) if r else None
    finally:c.close()


def dialog(uid,data=...):
    c=get_connection()
    try:
        if data is ...:
            r=c.execute('SELECT data FROM manager_report_lifecycle_dialog WHERE user_id=?',(uid,)).fetchone()
            return json.loads(r['data']) if r else None
        with c:
            if data is None:c.execute('DELETE FROM manager_report_lifecycle_dialog WHERE user_id=?',(uid,))
            else:c.execute('INSERT INTO manager_report_lifecycle_dialog VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data',(uid,_dump(data)))
    finally:c.close()


def _queue(c,key,recipient,text,buttons=()):
    c.execute('INSERT OR IGNORE INTO manager_report_notice VALUES (?,?,?,?,NULL)',(key,int(recipient),text,_dump(buttons)))


def queue(key,recipient,text,buttons=()):
    c=get_connection()
    try:
        with c:_queue(c,key,recipient,text,buttons)
    finally:c.close()


def pending():
    c=get_connection()
    try:return [dict(r) for r in c.execute('SELECT * FROM manager_report_notice WHERE sent_at IS NULL ORDER BY rowid')]
    finally:c.close()


def mark_sent(key):
    c=get_connection()
    try:
        with c:c.execute('UPDATE manager_report_notice SET sent_at=? WHERE notice_key=?',(stamp(),key))
    finally:c.close()


def submit(report_id,revision):
    """Дата предоставления фиксируется при подтверждении, до сетевой доставки."""
    c=get_connection()
    try:
        with c:
            r=_report(c.execute('SELECT * FROM monthly_manager_report WHERE id=?',(report_id,)).fetchone())
            if not r or r['revision']!=revision or r['status']!='sending':return False
            when=stamp()
            c.execute('INSERT OR IGNORE INTO manager_report_version VALUES (?,?,?,?)',(report_id,revision,_dump(r),when))
            c.execute("INSERT INTO manager_report_workflow(report_id,first_submitted_at,submitted_at,revision,state) VALUES (?,?,?,?,'submitted') ON CONFLICT(report_id) DO UPDATE SET submitted_at=CASE WHEN revision=excluded.revision THEN submitted_at ELSE excluded.submitted_at END,revision=excluded.revision,state='submitted',feedback=NULL,rework_due=NULL,accepted_at=NULL",(report_id,when,when,revision))
            return True
    finally:c.close()


def decide(report_id,revision,action,comment='',due=None,recipients=(),label=''):
    if action not in ('accept','feedback','rework'):raise ValueError(action)
    if action in ('feedback','rework') and not comment.strip():raise ValueError('Нужен комментарий')
    if action=='rework' and due is None:raise ValueError('Нужен срок')
    c=get_connection()
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            r=_report(c.execute('SELECT * FROM monthly_manager_report WHERE id=?',(report_id,)).fetchone())
            w=c.execute('SELECT * FROM manager_report_workflow WHERE report_id=?',(report_id,)).fetchone()
            if not r or r['revision']!=revision or r['status']!='completed' or not w or w['revision']!=revision or w['state']!='submitted':return False
            accepted=action!='rework'
            c.execute('UPDATE manager_report_workflow SET state=?,feedback=?,rework_due=?,accepted_at=? WHERE report_id=?',('accepted' if accepted else 'rework',comment,due,stamp() if accepted else None,report_id))
            c.execute('INSERT INTO manager_report_decision(report_id,revision,action,comment,deadline,decided_at) VALUES (?,?,?,?,?,?)',(report_id,revision,action,comment,due,stamp()))
            if not accepted:
                c.execute("UPDATE monthly_manager_report SET status='collecting',revision=revision+1,pdf_path=NULL,updated_at=? WHERE id=?",(stamp(),report_id))
            text=f'{label}\n'+('✅ Отчет принят. Пять фокусных задач закреплены.' if accepted else '↩️ Отчет возвращен на доработку. Сохраненные ответы доступны для изменения.')
            if comment:text+='\nОбратная связь Романа: '+comment
            if due:text+='\nСрок исправления (МСК): '+datetime.fromisoformat(due).astimezone(MOSCOW).strftime('%d.%m.%Y %H:%M')
            buttons=[] if accepted else [('Изменить ответы',f'mr:edit:{report_id}')]
            for uid in set(recipients):_queue(c,f'decision:{report_id}:{revision}:{uid}',uid,text,buttons)
            return True
    finally:c.close()


def accepted_tasks(market_id,month):
    c=get_connection()
    try:
        row=c.execute("SELECT v.snapshot FROM monthly_manager_report r JOIN manager_report_workflow w ON w.report_id=r.id JOIN manager_report_version v ON v.report_id=r.id AND v.revision=w.revision WHERE r.market_id=? AND r.month=? AND w.state='accepted'",(market_id,month)).fetchone()
        return json.loads(row['snapshot'])['answers'].get('next_tasks',[]) if row else []
    finally:c.close()


def save_status(market_id,month,uid,data,roman,label):
    c=get_connection()
    try:
        with c:
            when=stamp()
            c.execute('INSERT INTO manager_report_status VALUES (?,?,?,?,?) ON CONFLICT(market_id,month) DO UPDATE SET reporter_id=excluded.reporter_id,data=excluded.data,answered_at=excluded.answered_at',(market_id,month,uid,_dump(data),when))
            text=f'Статус отчета · {label} · {month}\nУправляющий: {data["name"]}\nНИМБ: {data["closure"]}\nОтвет управляющего: {data["answer"]}'
            _queue(c,f'status:{market_id}:{month}:{when}',roman,text)
    finally:c.close()


def reworks():
    c=get_connection()
    try:return [dict(r) for r in c.execute("SELECT w.*,r.market_id,r.month,r.reporter_id FROM manager_report_workflow w JOIN monthly_manager_report r ON r.id=w.report_id WHERE w.state='rework'")]
    finally:c.close()


def active_cycle(month,at):
    """При первом включении после срока не рассылаем старые просрочки."""
    c=get_connection()
    try:
        with c:
            if at.day<=5:
                c.execute('INSERT OR IGNORE INTO manager_report_cycle VALUES (?,?)',(month,at.isoformat()))
            return c.execute('SELECT 1 FROM manager_report_cycle WHERE month=?',(month,)).fetchone() is not None
    finally:c.close()


def notice_is_current(row):
    parts=row['notice_key'].split(':')
    if parts[0]=='month':
        _,mid,month,kind,uid=parts
        from monitoring.managers import get_market_supervisor
        from config import settings
        supervisor=get_market_supervisor(int(mid))
        if not supervisor:return False
        if kind in ('reminder','status') and supervisor['telegram_user_id']!=int(uid):return False
        if kind=='overdue' and int(uid) not in {supervisor['telegram_user_id'],int(settings.ROMAN_TELEGRAM_ID or 0)}:return False
        from manager_report.storage import find_report
        report=find_report(int(mid),month)
        w=workflow(report['id']) if report else None
        if kind=='overdue' and w:
            return datetime.fromisoformat(w['first_submitted_at'])>deadline(month)
        return w is None
    if parts[0]=='rework':
        w=workflow(int(parts[1]))
        return bool(w and w['state']=='rework' and w['revision']==int(parts[2]))
    return True
