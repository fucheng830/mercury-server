"""High-level memory CRUD service with auto-embedding and hybrid search."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np
from psycopg2.extras import RealDictCursor

from hermes.db import execute, execute_one, get_conn, put_conn
from hermes.embedding import generate_embedding

logger = logging.getLogger(__name__)

LAYER_TTL_DAYS: Dict[str, Optional[int]] = {
    "episodic": 30,
    "semantic": 180,
    "core": None,
}


def _serialize_memory(row: Dict) -> Dict:
    """Convert a DB row dict into a JSON-friendly dict."""
    result = {}
    for key, value in row.items():
        if value is None:
            result[key] = None
        elif isinstance(value, datetime):
            result[key] = value.isoformat()
        elif isinstance(value, np.ndarray):
            result[key] = value.tolist()
        else:
            # UUID, str, int, float, list, etc.
            result[key] = str(value) if hasattr(value, "hex") else value
    return result


def write_memory(
    content: str,
    layer: str = "episodic",
    source: str = "recap",
    importance: int = 3,
    tags: Optional[List[str]] = None,
    summary: Optional[str] = None,
    auto_embed: bool = True,
    embedding: Optional[List[float]] = None,
    namespace: str = "claude",
) -> Dict:
    """Write a new memory with optional auto-embedding and namespace isolation.

    Args:
        content: The memory text content.
        layer: Memory layer (episodic, semantic, core).
        source: Origin of the memory.
        importance: Importance score 1-5.
        tags: Optional list of tags.
        summary: Optional short summary.
        auto_embed: Whether to auto-generate an embedding.
        namespace: Namespace for agent isolation (default: claude).

    Returns:
        Serialized memory dict.
    """
    if embedding is not None:
        emb = np.array(embedding, dtype=np.float32)
    elif auto_embed:
        try:
            vec = generate_embedding(content)
            emb = np.array(vec, dtype=np.float32)
        except Exception:
            logger.warning("Failed to generate embedding, storing without vector")
            emb = None
    else:
        emb = None

    # Calculate expires_at based on layer TTL
    ttl_days = LAYER_TTL_DAYS.get(layer)
    expires_at = None
    if ttl_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)

    tags = tags or []

    conn = get_conn()
    try:
        if emb is not None:
            from pgvector.psycopg2 import register_vector
            register_vector(conn)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO memories (layer, content, summary, source, importance, tags, embedding, expires_at, namespace)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                    RETURNING id, layer, content, summary, source, importance, tags,
                              embedding, namespace, recall_count, created_at, expires_at, updated_at
                    """,
                    (layer, content, summary, source, importance, tags, emb, expires_at, namespace),
                )
                row = dict(cur.fetchone())
        else:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO memories (layer, content, summary, source, importance, tags, expires_at, namespace)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id, layer, content, summary, source, importance, tags,
                              embedding, namespace, recall_count, created_at, expires_at, updated_at
                    """,
                    (layer, content, summary, source, importance, tags, expires_at, namespace),
                )
                row = dict(cur.fetchone())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

    return _serialize_memory(row)


def read_memories(
    layer: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    namespace: str = "claude",
) -> List[Dict]:
    """Read memories, optionally filtered by layer and namespace.

    Args:
        layer: Optional layer filter.
        limit: Max results.
        offset: Result offset for pagination.
        namespace: Namespace filter (default: claude).

    Returns:
        List of serialized memory dicts.
    """
    if layer:
        rows = execute(
            """
            SELECT id, layer, content, summary, source, importance, tags,
                   embedding, namespace, recall_count, created_at, expires_at, updated_at
            FROM memories
            WHERE layer = %s AND namespace = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (layer, namespace, limit, offset),
            fetch=True,
        )
    else:
        rows = execute(
            """
            SELECT id, layer, content, summary, source, importance, tags,
                   embedding, namespace, recall_count, created_at, expires_at, updated_at
            FROM memories
            WHERE namespace = %s
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            (namespace, limit, offset),
            fetch=True,
        )
    return [_serialize_memory(r) for r in (rows or [])]


def get_memory(memory_id: str, namespace: str = "claude") -> Optional[Dict]:
    row = execute_one(
        """
        SELECT id, layer, content, summary, source, importance, tags,
               embedding, namespace, recall_count, created_at, expires_at, updated_at
        FROM memories
        WHERE id = %s AND namespace = %s
        """,
        (memory_id, namespace),
    )
    if row is None:
        return None
    return _serialize_memory(row)


def delete_memory(memory_id: str, namespace: str = "claude") -> bool:
    execute(
        "DELETE FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
        fetch=False,
    )
    check = execute_one(
        "SELECT id FROM memories WHERE id = %s",
        (memory_id,),
    )
    return check is None


def search_memories(
    query_text: str,
    layer: Optional[str] = None,
    limit: int = 20,
    namespaces: Optional[List[str]] = None,
) -> List[Dict]:
    """Hybrid search combining vector similarity and full-text search via RRF.
    Supports multi-namespace search for cross-agent queries.

    Args:
        query_text: The search query.
        layer: Optional layer filter.
        limit: Max results.
        namespaces: Namespaces to search. Default: ["claude"].

    Returns:
        List of serialized memory dicts with rrf_score.
    """
    if namespaces is None:
        namespaces = ["claude"]

    embedding_vec = None
    try:
        vec = generate_embedding(query_text)
        embedding_vec = np.array(vec, dtype=np.float32)
    except Exception:
        logger.warning("Failed to generate query embedding, falling back to FTS")
        return _fts_search(query_text, layer, limit, namespaces)

    conn = get_conn()
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, content, summary, layer, source, importance, tags,
                       namespace, recall_count, created_at, rrf_score
                FROM hybrid_search(%s, %s::vector, %s, %s, 60, %s::varchar[])
                """,
                (query_text, embedding_vec, layer, limit, namespaces),
            )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)

    for row in rows:
        try:
            execute(
                "UPDATE memories SET recall_count = recall_count + 1 WHERE id = %s",
                (row["id"],),
            )
        except Exception:
            logger.debug("Failed to increment recall_count for %s", row.get("id"))

    return [_serialize_memory(r) for r in rows]


def _fts_search(
    query_text: str,
    layer: Optional[str] = None,
    limit: int = 20,
    namespaces: Optional[List[str]] = None,
) -> List[Dict]:
    if namespaces is None:
        namespaces = ["claude"]
    if layer:
        rows = execute(
            """
            SELECT id, layer, content, summary, source, importance, tags,
                   namespace, recall_count, created_at, expires_at, updated_at
            FROM memories
            WHERE fts @@ plainto_tsquery('simple', %s)
              AND layer = %s
              AND namespace = ANY(%s::varchar[])
            ORDER BY ts_rank(fts, plainto_tsquery('simple', %s)) DESC
            LIMIT %s
            """,
            (query_text, layer, namespaces, query_text, limit),
            fetch=True,
        )
    else:
        rows = execute(
            """
            SELECT id, layer, content, summary, source, importance, tags,
                   namespace, recall_count, created_at, expires_at, updated_at
            FROM memories
            WHERE fts @@ plainto_tsquery('simple', %s)
              AND namespace = ANY(%s::varchar[])
            ORDER BY ts_rank(fts, plainto_tsquery('simple', %s)) DESC
            LIMIT %s
            """,
            (query_text, namespaces, query_text, limit),
            fetch=True,
        )
    return [_serialize_memory(r) for r in (rows or [])]


def promote_memory(memory_id: str, target_layer: str = "core", namespace: str = "claude") -> bool:
    now = datetime.now(timezone.utc)
    ttl_days = LAYER_TTL_DAYS.get(target_layer)
    new_expires = None
    if ttl_days is not None:
        new_expires = now + timedelta(days=ttl_days)

    execute(
        """
        UPDATE memories
        SET layer = %s, expires_at = %s, updated_at = %s
        WHERE id = %s AND namespace = %s
        """,
        (target_layer, new_expires, now, memory_id, namespace),
    )
    row = execute_one(
        "SELECT layer FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
    )
    return row is not None and row["layer"] == target_layer


def auto_promote(namespace: Optional[str] = None) -> int:
    """Auto-promote semantic memories meeting criteria to core layer.
    If namespace is None, promotes across all namespaces.

    Returns:
        Number of memories promoted.
    """
    now = datetime.now(timezone.utc)
    if namespace:
        result = execute(
            """
            UPDATE memories
            SET layer = 'core', expires_at = NULL, updated_at = %s
            WHERE layer = 'semantic'
              AND (importance >= 4 OR recall_count >= 3)
              AND namespace = %s
            RETURNING id
            """,
            (now, namespace),
            fetch=True,
        )
    else:
        result = execute(
            """
            UPDATE memories
            SET layer = 'core', expires_at = NULL, updated_at = %s
            WHERE layer = 'semantic'
              AND (importance >= 4 OR recall_count >= 3)
            RETURNING id
            """,
            (now,),
            fetch=True,
        )
    return len(result) if result else 0


def get_memory_stats(namespace: str = "claude") -> Dict:
    layer_stats = execute(
        """
        SELECT layer, COUNT(*) as count, ROUND(AVG(importance)::numeric, 1) as avg_importance
        FROM memories WHERE namespace = %s
        GROUP BY layer
        """,
        (namespace,),
        fetch=True,
    )
    counts_by_layer = {}
    total = 0
    for row in (layer_stats or []):
        counts_by_layer[row["layer"]] = {
            "count": row["count"],
            "avg_importance": float(row["avg_importance"]) if row["avg_importance"] else 0,
        }
        total += row["count"]

    entity_row = execute_one("SELECT COUNT(*) as count FROM entities")
    relation_row = execute_one("SELECT COUNT(*) as count FROM relations")

    return {
        "total": total,
        "episodic": counts_by_layer.get("episodic", {"count": 0, "avg_importance": 0}),
        "semantic": counts_by_layer.get("semantic", {"count": 0, "avg_importance": 0}),
        "core": counts_by_layer.get("core", {"count": 0, "avg_importance": 0}),
        "entities": entity_row["count"] if entity_row else 0,
        "relations": relation_row["count"] if relation_row else 0,
    }


def share_memory(memory_id: str, owner_namespace: str) -> bool:
    """Move a memory to the shared namespace, making it visible to all agents."""
    now = datetime.now(timezone.utc)
    execute(
        """
        UPDATE memories
        SET namespace = 'shared', updated_at = %s
        WHERE id = %s AND namespace = %s
        """,
        (now, memory_id, owner_namespace),
    )
    check = execute_one(
        "SELECT namespace FROM memories WHERE id = %s",
        (memory_id,),
    )
    return check is not None and check["namespace"] == "shared"


def register_agent(
    agent_id: str,
    name: str,
    description: str = "",
    namespace: str = "",
    auth_credentials: str = "",
    agent_url: str = "",
) -> Dict:
    """Register a new A2A agent. Returns the agent record."""
    ns = namespace or agent_id
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO a2a_agents (agent_id, name, description, namespace, auth_credentials, agent_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (agent_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    last_active = now()
                RETURNING id, agent_id, name, namespace, created_at
                """,
                (agent_id, name, description, ns, auth_credentials, agent_url),
            )
            row = dict(cur.fetchone())
        conn.commit()
        return _serialize_memory(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def list_agents() -> List[Dict]:
    """List all registered A2A agents."""
    rows = execute(
        """
        SELECT id, agent_id, name, description, namespace, agent_url,
               auth_scheme, capabilities, permissions, rate_limit,
               created_at, last_active, enabled
        FROM a2a_agents
        ORDER BY last_active DESC NULLS LAST
        """,
        fetch=True,
    )
    return [_serialize_memory(r) for r in (rows or [])]


def get_agent(agent_id: str) -> Optional[Dict]:
    row = execute_one(
        "SELECT * FROM a2a_agents WHERE agent_id = %s",
        (agent_id,),
    )
    return _serialize_memory(dict(row)) if row else None


# ── Session CRUD (for A2A session sharing) ─────────────────────────

def search_sessions(
    query_text: str,
    namespace: str = "claude",
    limit: int = 20,
) -> List[Dict]:
    """Search sessions by FTS + vector across namespaces [namespace, shared]."""
    embedding_vec = None
    try:
        vec = generate_embedding(query_text)
        embedding_vec = np.array(vec, dtype=np.float32)
    except Exception:
        logger.warning("Session search embedding failed")

    conn = get_conn()
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
        namespaces = [namespace, "shared"]
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if embedding_vec is not None:
                cur.execute(
                    """
                    SELECT s.id, s.client_id, s.project_id, s.session_id,
                           s.project_path, s.preview, s.message_count,
                           s.first_ts, s.last_ts, s.namespace,
                           1 - (s.embedding <=> %s::vector) AS similarity
                    FROM sessions s
                    WHERE s.embedding IS NOT NULL
                      AND s.namespace = ANY(%s::varchar[])
                    ORDER BY s.embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (embedding_vec, namespaces, embedding_vec, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT id, client_id, project_id, session_id,
                           project_path, preview, message_count,
                           first_ts, last_ts, namespace
                    FROM sessions
                    WHERE to_tsvector('simple', COALESCE(preview, '')) @@ plainto_tsquery('simple', %s)
                      AND namespace = ANY(%s::varchar[])
                    ORDER BY last_ts DESC
                    LIMIT %s
                    """,
                    (query_text, namespaces, limit),
                )
            rows = [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)

    return [_serialize_memory(r) for r in rows]


def get_session(session_id: str, namespace: str = "claude") -> Optional[Dict]:
    """Get a single session by id, scoped to namespace + shared."""
    row = execute_one(
        """
        SELECT id, client_id, project_id, session_id,
               project_path, preview, message_count,
               first_ts, last_ts, namespace
        FROM sessions
        WHERE id = %s AND namespace = ANY(%s::varchar[])
        """,
        (session_id, [namespace, "shared"]),
    )
    return _serialize_memory(dict(row)) if row else None


def read_sessions(
    namespace: str = "claude",
    limit: int = 50,
    offset: int = 0,
    project_id: Optional[str] = None,
) -> List[Dict]:
    """Paginated read of session metadata."""
    namespaces = [namespace, "shared"]
    if project_id:
        rows = execute(
            """
            SELECT id, client_id, project_id, session_id,
                   project_path, preview, message_count,
                   first_ts, last_ts, namespace
            FROM sessions
            WHERE namespace = ANY(%s::varchar[]) AND project_id = %s
            ORDER BY last_ts DESC
            LIMIT %s OFFSET %s
            """,
            (namespaces, project_id, limit, offset),
            fetch=True,
        )
    else:
        rows = execute(
            """
            SELECT id, client_id, project_id, session_id,
                   project_path, preview, message_count,
                   first_ts, last_ts, namespace
            FROM sessions
            WHERE namespace = ANY(%s::varchar[])
            ORDER BY last_ts DESC
            LIMIT %s OFFSET %s
            """,
            (namespaces, limit, offset),
            fetch=True,
        )
    return [_serialize_memory(r) for r in (rows or [])]


def share_session(session_id: str, owner_namespace: str) -> bool:
    """Move a session to the shared namespace."""
    now = datetime.now(timezone.utc)
    execute(
        """
        UPDATE sessions SET namespace = 'shared' WHERE id = %s AND namespace = %s
        """,
        (session_id, owner_namespace),
    )
    check = execute_one("SELECT namespace FROM sessions WHERE id = %s", (session_id,))
    return check is not None and check["namespace"] == "shared"


def cleanup_expired() -> int:
    """Delete expired memories.

    Returns:
        Number of deleted memories.
    """
    now = datetime.now(timezone.utc)
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                DELETE FROM memories
                WHERE expires_at IS NOT NULL AND expires_at < %s
                RETURNING id
                """,
                (now,),
            )
            rows = cur.fetchall()
        conn.commit()
        return len(rows)
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
