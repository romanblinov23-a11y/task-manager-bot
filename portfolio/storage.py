import json
import secrets
from datetime import datetime,timezone
from monitoring.db import get_connection
from manager_report.model import number

SCHEMA='''
CREATE TABLE IF NOT EXISTS portfolio_ownership (
 market_id INTEGER PRIMARY KEY REFERENCES market(id),percent REAL NOT NULL CHECK(percent>=0 AND percent<=100),
 updated_by INTEGER NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_ownership_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT,market_id INTEGER NOT NULL,old_percent REAL NOT NULL,
 new_percent REAL NOT NULL,updated_by INTEGER NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_cache (
 cache_key TEXT PRIMARY KEY,data TEXT NOT NULL,updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_secret (id INTEGER PRIMARY KEY CHECK(id=1),token TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio_dialog (user_id INTEGER PRIMARY KEY,data TEXT NOT NULL);
'''


def stamp():return datetime.now(timezone.utc).isoformat()


def ownership(market):
    c=get_connection()
    try:
        row=c.execute('SELECT percent FROM portfolio_ownership WHERE market_id=?',(market['id'],)).fetchone()
        return row['percent'] if row else 50.0 if market.get('surf_spot_key')=='park_gorkogo' else 100.0
    finally:c.close()


def set_ownership(market,percent,author):
    value=number(percent)
    if value is None or not 0<=value<=100:raise ValueError('Доля должна быть от 0 до 100%.')
    c=get_connection()
    try:
        with c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT percent FROM portfolio_ownership WHERE market_id=?',(market['id'],)).fetchone()
            old=row['percent'] if row else 50.0 if market.get('surf_spot_key')=='park_gorkogo' else 100.0
            when=stamp()
            c.execute('INSERT INTO portfolio_ownership VALUES (?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET percent=excluded.percent,updated_by=excluded.updated_by,updated_at=excluded.updated_at',(market['id'],value,author,when))
            c.execute('INSERT INTO portfolio_ownership_history(market_id,old_percent,new_percent,updated_by,updated_at) VALUES (?,?,?,?,?)',(market['id'],old,value,author,when))
    finally:c.close()


def token():
    c=get_connection()
    try:
        with c:
            c.execute('INSERT OR IGNORE INTO portfolio_secret VALUES (1,?)',(secrets.token_urlsafe(32),))
            return c.execute('SELECT token FROM portfolio_secret WHERE id=1').fetchone()['token']
    finally:c.close()


def cache_get(key):
    c=get_connection()
    try:
        row=c.execute('SELECT * FROM portfolio_cache WHERE cache_key=?',(key,)).fetchone()
        return {'data':json.loads(row['data']),'updated_at':row['updated_at']} if row else None
    finally:c.close()


def cache_set(key,data):
    c=get_connection()
    try:
        with c:c.execute('INSERT INTO portfolio_cache VALUES (?,?,?) ON CONFLICT(cache_key) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at',(key,json.dumps(data,ensure_ascii=False,allow_nan=False),stamp()))
    finally:c.close()


def dialog(uid,value=...):
    c=get_connection()
    try:
        if value is ...:
            row=c.execute('SELECT data FROM portfolio_dialog WHERE user_id=?',(uid,)).fetchone()
            return json.loads(row['data']) if row else None
        with c:
            if value is None:c.execute('DELETE FROM portfolio_dialog WHERE user_id=?',(uid,))
            else:c.execute('INSERT INTO portfolio_dialog VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data',(uid,json.dumps(value,ensure_ascii=False)))
    finally:c.close()
