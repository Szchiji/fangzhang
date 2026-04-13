import os
import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL")
_pool = None


def get_pool():
    global _pool
    if _pool is None:
        if DATABASE_URL:
            _pool = ThreadedConnectionPool(1, 10, DATABASE_URL)
        else:
            _pool = ThreadedConnectionPool(
                1, 10,
                host=os.getenv("PGHOST"),
                port=os.getenv("PGPORT", "5432"),
                dbname=os.getenv("PGDATABASE", "railway"),
                user=os.getenv("PGUSER"),
                password=os.getenv("PGPASSWORD"),
            )
    return _pool


def db_exec(sql, params=()):
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def db_query(sql, params=()):
    pool = get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        pool.putconn(conn)


def db_query_one(sql, params=()):
    rows = db_query(sql, params)
    return rows[0] if rows else None
