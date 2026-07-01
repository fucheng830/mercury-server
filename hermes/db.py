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


def _split_schema_statements(sql: str) -> List[str]:
    """Split SQL into statements on ';', respecting $$ ... $$ dollar-quoted blocks.

    psycopg2's execute() runs multi-statement SQL as one simple query, where a
    single ERROR aborts the rest. Schema migrations must be resilient: one
    failing statement (e.g. CREATE UNIQUE INDEX hitting duplicate keys) must
    not block later ones. We split into individual statements (keeping DO $$
    ... $$ blocks intact) so init_db can run each independently.
    """
    statements: List[str] = []
    buf: List[str] = []
    in_dollar = False
    for line in sql.splitlines():
        if line.count("$$") % 2 == 1:
            in_dollar = not in_dollar
        buf.append(line)
        if not in_dollar and line.strip().endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s.strip()]


def init_db() -> None:
    """Run schema.sql statement-by-statement so one failure doesn't block the rest.

    Each statement runs in autocommit mode; a failing statement is logged and
    skipped (e.g. CREATE UNIQUE INDEX on a table with pre-existing duplicates),
    so subsequent migrations still apply. Callers should verify expected
    columns/indexes exist after startup — a WARNING here does not stop the server.
    """
    init_pool()
    schema_path = Path(__file__).parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    conn = get_conn()
    total = 0
    failed = 0
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for stmt in _split_schema_statements(sql):
                total += 1
                try:
                    cur.execute(stmt)
                except Exception as e:
                    failed += 1
                    logger.warning("Schema statement failed (continuing): %s", e)
        logger.info("Database schema initialized (%d statements, %d failed)", total, failed)
    finally:
        conn.autocommit = False
        put_conn(conn)


def execute(query: str, params: tuple = None, fetch: bool = False) -> Optional[List[Dict[str, Any]]]:
    """Execute a query and optionally return results as list of dicts."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            rows = [dict(row) for row in cur.fetchall()] if fetch else None
        conn.commit()
        return rows
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
