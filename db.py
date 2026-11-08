"""MySQL / MariaDB access (PyMySQL).

One short-lived connection per request (the right pattern for serverless hosting such as
Vercel). Outside a request (scripts, tests) a module-level connection is used instead.
All SQL uses %s placeholders. All timestamps are stored as integer unix epoch values.
"""
import threading

import pymysql
import pymysql.cursors
from flask import g, has_app_context

import config

IntegrityError = pymysql.err.IntegrityError

_local = threading.local()


def _connect():
    kwargs = dict(
        host=config.DB_HOST, port=config.DB_PORT, user=config.DB_USER, password=config.DB_PASSWORD,
        database=config.DB_NAME, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        autocommit=False, connect_timeout=30, read_timeout=60, write_timeout=60,
    )
    if config.DB_SSL:
        import certifi
        kwargs["ssl"] = {"ca": config.DB_SSL_CA or certifi.where()}
    return pymysql.connect(**kwargs)


def get():
    """Return the connection for the current request (or the thread, outside a request)."""
    if has_app_context():
        if "db" not in g:
            g.db = _connect()
        return g.db
    con = getattr(_local, "con", None)
    if con is None or not con.open:
        con = _local.con = _connect()
    return con


def commit():
    con = g.get("db") if has_app_context() else getattr(_local, "con", None)
    if con is not None and con.open:
        con.commit()


def rollback():
    con = g.get("db") if has_app_context() else getattr(_local, "con", None)
    if con is not None and con.open:
        con.rollback()


def close():
    if has_app_context():
        con = g.pop("db", None)
        if con is not None and con.open:
            con.close()
    else:
        con = getattr(_local, "con", None)
        if con is not None and con.open:
            con.close()
        _local.con = None


def query(sql, args=()):
    with get().cursor() as cur:
        cur.execute(sql, args)
        return list(cur.fetchall())


def one(sql, args=()):
    with get().cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()


def scalar(sql, args=(), default=None):
    row = one(sql, args)
    if not row:
        return default
    value = next(iter(row.values()))
    return default if value is None else value


def execute(sql, args=()):
    """Run a write statement. Returns (lastrowid, rowcount)."""
    with get().cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid, cur.rowcount


def insert(sql, args=()):
    return execute(sql, args)[0]


def update(sql, args=()):
    return execute(sql, args)[1]