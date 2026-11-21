"""Request-scoped MySQL connection. One explicit transaction per domain command."""

from contextlib import contextmanager

import pymysql
from flask import current_app, g
from pymysql.cursors import DictCursor


def connect():
    cfg = current_app.config
    if "mysql" not in g:
        g.mysql = pymysql.connect(
            host=cfg["DB_HOST"],
            port=int(cfg["DB_PORT"]),
            user=cfg["DB_USER"],
            password=cfg["DB_PASSWORD"],
            database=cfg["DB_NAME"],
            unix_socket=cfg.get("DB_SOCKET") or None,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=True,
            connect_timeout=5,
            init_command="SET time_zone = '+00:00'",
        )
    return g.mysql


def close(_exception=None):
    connection = g.pop("mysql", None)
    if connection is not None:
        connection.close()


def one(sql, args=()):
    with connect().cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()


def all(sql, args=()):
    with connect().cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchall()


def execute(sql, args=()):
    with connect().cursor() as cur:
        cur.execute(sql, args)
        return cur.lastrowid


@contextmanager
def transaction():
    connection = connect()
    if g.get("in_transaction"):
        raise RuntimeError("Вложенная транзакция")
    connection.begin()
    g.in_transaction = True
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        g.in_transaction = False
