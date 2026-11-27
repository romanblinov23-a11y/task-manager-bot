"""Черновики рассылок и доставка каждой части переживают перезапуск бота."""
import json
from uuid import uuid4
from monitoring.db import get_connection

SCHEMA='''
CREATE TABLE IF NOT EXISTS broadcast_draft (
 owner_id INTEGER PRIMARY KEY,token TEXT NOT NULL,state TEXT NOT NULL,
 audience TEXT NOT NULL,content TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS broadcast_delivery (
 token TEXT NOT NULL,recipient INTEGER NOT NULL,part INTEGER NOT NULL,
 PRIMARY KEY(token,recipient,part)
);
'''


def _load(row):
    if not row:return None
    value=dict(row)
    for key in ('audience','content'):value[key]=json.loads(value[key])
    return value


def get(owner):
    c=get_connection()
    try:return _load(c.execute('SELECT * FROM broadcast_draft WHERE owner_id=?',(owner,)).fetchone())
    finally:c.close()


def start(owner,audience):
    c=get_connection()
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            row=_load(c.execute('SELECT * FROM broadcast_draft WHERE owner_id=?',(owner,)).fetchone())
            if row and row['state'] in ('sending','failed','paused_failed'):return None
            content=row['content'] if row and row['state'] in ('composing','review','paused') else {'text':'','entities':[],'documents':[]}
            state='review' if content['text'] or content['documents'] else 'composing'
            c.execute('INSERT INTO broadcast_draft VALUES (?,?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET token=excluded.token,state=excluded.state,audience=excluded.audience,content=excluded.content',(owner,uuid4().hex[:12],state,json.dumps(audience,ensure_ascii=False),json.dumps(content,ensure_ascii=False)))
        return get(owner)
    finally:c.close()


def content(owner,token,value):
    c=get_connection()
    try:
        with c:
            changed=c.execute("UPDATE broadcast_draft SET token=?,content=?,state='review' WHERE owner_id=? AND token=? AND state IN ('composing','review')",(uuid4().hex[:12],json.dumps(value,ensure_ascii=False),owner,token)).rowcount
        return get(owner) if changed else None
    finally:c.close()


def claim(owner,token):
    c=get_connection()
    try:
        with c:return c.execute("UPDATE broadcast_draft SET state='sending' WHERE owner_id=? AND token=? AND state IN ('review','failed')",(owner,token)).rowcount==1
    finally:c.close()


def finish(owner,token,state):
    assert state in ('completed','failed')
    c=get_connection()
    try:
        with c:c.execute('UPDATE broadcast_draft SET state=? WHERE owner_id=? AND token=?',(state,owner,token))
    finally:c.close()


def cancel(owner,token):
    c=get_connection()
    try:
        with c:return c.execute("UPDATE broadcast_draft SET state='cancelled' WHERE owner_id=? AND token=? AND state IN ('composing','review','failed','paused','paused_failed')",(owner,token)).rowcount==1
    finally:c.close()


def delivered(token):
    c=get_connection()
    try:return {(r['recipient'],r['part']) for r in c.execute('SELECT recipient,part FROM broadcast_delivery WHERE token=?',(token,))}
    finally:c.close()


def record(token,recipient,part):
    c=get_connection()
    try:
        with c:c.execute('INSERT OR IGNORE INTO broadcast_delivery VALUES (?,?,?)',(token,recipient,part))
    finally:c.close()


def recover():
    c=get_connection()
    try:
        with c:c.execute("UPDATE broadcast_draft SET state='failed' WHERE state='sending'")
    finally:c.close()


def pause(owner):
    c=get_connection()
    try:
        with c:c.execute("UPDATE broadcast_draft SET state=CASE WHEN state='failed' THEN 'paused_failed' ELSE 'paused' END WHERE owner_id=? AND state IN ('composing','review','failed')",(owner,))
    finally:c.close()


def resume(owner):
    c=get_connection()
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            row=_load(c.execute('SELECT * FROM broadcast_draft WHERE owner_id=?',(owner,)).fetchone())
            if not row:return None
            if row['state']=='paused_failed':
                c.execute("UPDATE broadcast_draft SET state='failed' WHERE owner_id=?",(owner,))
            elif row['state']=='paused':
                state='review' if row['content']['text'] or row['content']['documents'] else 'composing'
                c.execute('UPDATE broadcast_draft SET state=?,token=? WHERE owner_id=?',(state,uuid4().hex[:12],owner))
        return get(owner)
    finally:c.close()
