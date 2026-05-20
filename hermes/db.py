"""PostgreSQL connection pool and query helpers for Hermes memory."""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

_pool: Optional[pool.ThreadedConnectionPool] = None


def _get_db_config() -> Dict[str, Any]:
    from recap_config import get_config
    hermes = get_config().get("hermes", {})
    return hermes.get("db", {})


def init_pool() -> None:
    global _pool
    if _pool is not None:
        return

    cfg = _get_db_config()
    _pool = pool.ThreadedConnectionPool(
        minconn=1,
        maxconn=cfg.get("pool_size", 5),
        host=cfg.get("host", "192.168.0.17"),
        port=cfg.get("port", 5432),
        database=cfg.get("database", "hermes_memory"),
        user=cfg.get("user", "hermes"),
        password=cfg.get("password", ""),
    )
    logger.info("DB connection pool initialized")


def get_conn():
    if _pool is None:
        init_pool()
    return _pool.getconn()


def put_conn(conn):
    if _pool is not None:
        _pool.putconn(conn)


def init_db() -> None:
    """Run schema.sql to create tables and indexes."""
    init_pool()
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
        logger.info("Database schema initialized")
    finally:
        put_conn(conn)


def execute(query: str, params: tuple = None, fetch: bool = False) -> Optional[List[Dict[str, Any]]]:
    """Execute a query and optionally return results as list of dicts."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            if fetch:
                return [dict(row) for row in cur.fetchall()]
            conn.commit()
            return None
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def execute_one(query: str, params: tuple = None) -> Optional[Dict[str, Any]]:
    """Execute a query and return first row as dict, or None."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        put_conn(conn)
