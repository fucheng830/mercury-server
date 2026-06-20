"""High-level memory CRUD service with auto-embedding and hybrid search.

Memory model v2: observation -> candidate -> memory workflow with
type / scope / status / project dimensions.
See docs/superpowers/specs/2026-06-18-memory-model-v2-design.md
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from psycopg2.extras import RealDictCursor

from hermes.db import execute, execute_one, get_conn, put_conn
from hermes.embedding import generate_embedding

logger = logging.getLogger(__name__)

STAGE_TTL_DAYS: Dict[str, Optional[int]] = {
    "observation": 30,
    "candidate": 90,
    "memory": None,
}

VALID_STAGES = ("observation", "candidate", "memory")
VALID_SCOPES = ("repo", "global", "user")
VALID_STATUSES = ("active", "archived", "superseded")

# Canonical column list for memory reads (kept in sync with schema.sql v2).
_MEM_COLUMNS = (
    "id, stage, type, scope, status, project_id, content, summary, source, "
    "importance, tags, embedding, namespace, recall_count, "
    "created_at, expires_at, updated_at"
)


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


def _stage_expiry(stage: str) -> Optional[datetime]:
    ttl_days = STAGE_TTL_DAYS.get(stage)
    if ttl_days is None:
        return None
    return datetime.now(timezone.utc) + timedelta(days=ttl_days)


def write_memory(
    content: str,
    stage: str = "observation",
    source: str = "recap",
    importance: int = 3,
    tags: Optional[List[str]] = None,
    summary: Optional[str] = None,
    type: str = "NOTE",
    scope: str = "global",
    status: str = "active",
    project_id: Optional[str] = None,
    auto_embed: bool = True,
    embedding: Optional[List[float]] = None,
    namespace: str = "claude",
) -> Dict:
    """Write a new memory with optional auto-embedding and namespace isolation.

    Args:
        content: The memory text content.
        stage: Lifecycle stage (observation, candidate, memory).
        source: Origin of the memory.
        importance: Importance score 1-5.
        tags: Optional list of tags.
        summary: Optional short summary.
        type: Memory type (NOTE/DISCOVERY/ARCH/DECISION/BUGFIX/PREFERENCE/...).
        scope: Applicability scope (repo, global, user).
        status: Lifecycle status (active, archived, superseded).
        project_id: Optional linked project UUID.
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

    expires_at = _stage_expiry(stage)
    tags = tags or []

    conn = get_conn()
    try:
        if emb is not None:
            from pgvector.psycopg2 import register_vector
            register_vector(conn)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"""
                    INSERT INTO memories
                        (stage, type, scope, status, project_id, content, summary,
                         source, importance, tags, embedding, expires_at, namespace)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
                    RETURNING {_MEM_COLUMNS}
                    """,
                    (stage, type, scope, status, project_id, content, summary,
                     source, importance, tags, emb, expires_at, namespace),
                )
                row = dict(cur.fetchone())
        else:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"""
                    INSERT INTO memories
                        (stage, type, scope, status, project_id, content, summary,
                         source, importance, tags, expires_at, namespace)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_MEM_COLUMNS}
                    """,
                    (stage, type, scope, status, project_id, content, summary,
                     source, importance, tags, expires_at, namespace),
                )
                row = dict(cur.fetchone())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

    return _serialize_memory(row)


def list_memories(
    stage: Optional[str] = None,
    types: Optional[List[str]] = None,
    scopes: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    namespaces: Optional[List[str]] = None,
    search: Optional[str] = None,
    sort: str = "updated_at",
    order: str = "desc",
    page: int = 1,
    size: int = 25,
) -> Dict:
    """Multi-filter, paginated memory list for the 'All memories' view."""
    conditions: List[str] = []
    params: List[Any] = []
    if stage:
        conditions.append("stage = %s")
        params.append(stage)
    if types:
        conditions.append("type = ANY(%s)")
        params.append(list(types))
    if scopes:
        conditions.append("scope = ANY(%s)")
        params.append(list(scopes))
    if statuses:
        conditions.append("status = ANY(%s)")
        params.append(list(statuses))
    if project_id:
        conditions.append("project_id = %s")
        params.append(project_id)
    if namespaces:
        conditions.append("namespace = ANY(%s)")
        params.append(list(namespaces))
    if search:
        conditions.append("fts @@ plainto_tsquery('simple', %s)")
        params.append(search)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    allowed_sorts = {"updated_at", "created_at", "importance", "recall_count", "stage", "type"}
    sort_col = sort if sort in allowed_sorts else "updated_at"
    order_dir = "DESC" if order.lower() == "desc" else "ASC"

    total_row = execute_one(f"SELECT COUNT(*) AS cnt FROM memories {where}", tuple(params))
    total = total_row["cnt"] if total_row else 0

    offset = max(0, (page - 1) * size)
    rows = execute(
        f"SELECT {_MEM_COLUMNS} FROM memories {where} "
        f"ORDER BY {sort_col} {order_dir} LIMIT %s OFFSET %s",
        tuple(params) + (size, offset),
        fetch=True,
    )
    return {
        "items": [_serialize_memory(r) for r in (rows or [])],
        "total": total,
        "page": page,
        "size": size,
        "pages": max(1, (total + size - 1) // size),
    }


def read_memories(
    stage: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    namespace: str = "claude",
) -> List[Dict]:
    """Read memories, optionally filtered by stage and namespace."""
    if stage:
        rows = execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE stage = %s AND namespace = %s "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (stage, namespace, limit, offset),
            fetch=True,
        )
    else:
        rows = execute(
            f"SELECT {_MEM_COLUMNS} FROM memories WHERE namespace = %s "
            "ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (namespace, limit, offset),
            fetch=True,
        )
    return [_serialize_memory(r) for r in (rows or [])]


def get_memory(memory_id: str, namespace: str = "claude") -> Optional[Dict]:
    row = execute_one(
        f"SELECT {_MEM_COLUMNS} FROM memories WHERE id = %s AND namespace = %s",
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
    check = execute_one("SELECT id FROM memories WHERE id = %s", (memory_id,))
    return check is None


def search_memories(
    query_text: str,
    stage: Optional[str] = None,
    types: Optional[List[str]] = None,
    scopes: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    namespaces: Optional[List[str]] = None,
) -> List[Dict]:
    """Hybrid search combining vector similarity and full-text search via RRF.

    Supports multi-dimension filtering (stage/type/scope/status/project/namespace)
    and pagination.
    """
    if namespaces is None:
        namespaces = ["claude"]
    if statuses is None:
        statuses = ["active"]

    embedding_vec = None
    try:
        vec = generate_embedding(query_text)
        embedding_vec = np.array(vec, dtype=np.float32)
    except Exception:
        logger.warning("Failed to generate query embedding, falling back to FTS")
        return _fts_search(query_text, stage, types, scopes, statuses, project_id,
                           limit, offset, namespaces)

    conn = get_conn()
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, content, summary, stage, type, scope, status, project_id,
                       source, importance, tags, namespace, recall_count,
                       created_at, updated_at, rrf_score
                FROM hybrid_search(%s, %s::vector, %s, %s, %s, %s, %s, %s, %s, %s, 60)
                """,
                (query_text, embedding_vec, stage, types, scopes, statuses,
                 project_id, namespaces, limit, offset),
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
    stage: Optional[str] = None,
    types: Optional[List[str]] = None,
    scopes: Optional[List[str]] = None,
    statuses: Optional[List[str]] = None,
    project_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    namespaces: Optional[List[str]] = None,
) -> List[Dict]:
    if namespaces is None:
        namespaces = ["claude"]
    conditions = ["fts @@ plainto_tsquery('simple', %s)"]
    params: List[Any] = [query_text]
    if stage:
        conditions.append("stage = %s")
        params.append(stage)
    if types:
        conditions.append("type = ANY(%s)")
        params.append(list(types))
    if scopes:
        conditions.append("scope = ANY(%s)")
        params.append(list(scopes))
    if statuses:
        conditions.append("status = ANY(%s)")
        params.append(list(statuses))
    if project_id:
        conditions.append("project_id = %s")
        params.append(project_id)
    conditions.append("namespace = ANY(%s)")
    params.append(namespaces)

    rows = execute(
        f"SELECT {_MEM_COLUMNS} FROM memories WHERE {' AND '.join(conditions)} "
        "ORDER BY ts_rank(fts, plainto_tsquery('simple', %s)) DESC LIMIT %s OFFSET %s",
        tuple(params) + (query_text, limit, offset),
        fetch=True,
    )
    return [_serialize_memory(r) for r in (rows or [])]


# ── Stage transitions (observation -> candidate -> memory) ────────────────

def distill_to_candidate(memory_id: str, namespace: str = "claude") -> bool:
    """observation -> candidate. Automatic LLM-distilled promotion."""
    now = datetime.now(timezone.utc)
    execute(
        """
        UPDATE memories
        SET stage = 'candidate', expires_at = %s, updated_at = %s
        WHERE id = %s AND namespace = %s AND stage = 'observation'
        """,
        (_stage_expiry("candidate"), now, memory_id, namespace),
    )
    row = execute_one(
        "SELECT stage FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
    )
    return row is not None and row["stage"] == "candidate"


def confirm_candidate(
    memory_id: str,
    namespace: str = "claude",
    type: Optional[str] = None,
    scope: Optional[str] = None,
    project_id: Optional[str] = None,
) -> bool:
    """candidate -> memory. Human confirmation gate (may override type/scope/project)."""
    now = datetime.now(timezone.utc)
    sets = ["stage = 'memory'", "expires_at = NULL", "updated_at = %s"]
    params: List[Any] = [now]
    if type:
        sets.append("type = %s")
        params.append(type)
    if scope:
        sets.append("scope = %s")
        params.append(scope)
    if project_id:
        sets.append("project_id = %s")
        params.append(project_id)
    params += [memory_id, namespace]
    execute(
        f"""
        UPDATE memories
        SET {', '.join(sets)}
        WHERE id = %s AND namespace = %s AND stage = 'candidate'
        """,
        tuple(params),
    )
    row = execute_one(
        "SELECT stage FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
    )
    return row is not None and row["stage"] == "memory"


def reject_candidate(memory_id: str, namespace: str = "claude") -> bool:
    """candidate -> archived. Human rejection at the confirmation gate."""
    now = datetime.now(timezone.utc)
    execute(
        """
        UPDATE memories
        SET status = 'archived', updated_at = %s
        WHERE id = %s AND namespace = %s AND stage = 'candidate'
        """,
        (now, memory_id, namespace),
    )
    row = execute_one(
        "SELECT status FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
    )
    return row is not None and row["status"] == "archived"


def auto_promote(namespace: Optional[str] = None) -> int:
    """Auto-promote high-confidence candidates to memory.

    Bypass for candidates with importance >= 5 or recall_count >= 5.
    If namespace is None, promotes across all namespaces.
    """
    now = datetime.now(timezone.utc)
    if namespace:
        result = execute(
            """
            UPDATE memories
            SET stage = 'memory', expires_at = NULL, updated_at = %s
            WHERE stage = 'candidate'
              AND status = 'active'
              AND (importance >= 5 OR recall_count >= 5)
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
            SET stage = 'memory', expires_at = NULL, updated_at = %s
            WHERE stage = 'candidate'
              AND status = 'active'
              AND (importance >= 5 OR recall_count >= 5)
            RETURNING id
            """,
            (now,),
            fetch=True,
        )
    return len(result) if result else 0


def get_memory_stats(namespace: str = "claude") -> Dict:
    stage_stats = execute(
        """
        SELECT stage, COUNT(*) AS count,
               ROUND(AVG(importance)::numeric, 1) AS avg_importance
        FROM memories WHERE namespace = %s AND status = 'active'
        GROUP BY stage
        """,
        (namespace,),
        fetch=True,
    )
    counts_by_stage: Dict[str, Dict] = {}
    total = 0
    for row in (stage_stats or []):
        counts_by_stage[row["stage"]] = {
            "count": row["count"],
            "avg_importance": float(row["avg_importance"]) if row["avg_importance"] else 0,
        }
        total += row["count"]

    entity_row = execute_one("SELECT COUNT(*) AS count FROM entities")
    relation_row = execute_one("SELECT COUNT(*) AS count FROM relations")
    archived_row = execute_one(
        "SELECT COUNT(*) AS count FROM memories WHERE namespace = %s AND status = 'archived'",
        (namespace,),
    )

    def _stage(name: str) -> Dict:
        return counts_by_stage.get(name, {"count": 0, "avg_importance": 0})

    return {
        "total": total,
        "observation": _stage("observation"),
        "candidate": _stage("candidate"),
        "memory": _stage("memory"),
        "archived": archived_row["count"] if archived_row else 0,
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
    check = execute_one("SELECT namespace FROM memories WHERE id = %s", (memory_id,))
    return check is not None and check["namespace"] == "shared"


# ── Recall + reconciliation helpers (agent-first) ─────────────────────────

def recall_memories(
    project_path: str,
    namespace: str = "claude",
    limit: int = 30,
    min_importance: int = 1,
) -> List[Dict]:
    """Return a project's active, injectable memories (Top N by importance).

    Resolves the project by path OR name (lookup only — does not create).
    Returns [] if the project is unknown or has no active memories.
    """
    proj = execute_one(
        "SELECT id FROM projects WHERE path = %s OR name = %s LIMIT 1",
        (project_path, project_path),
    )
    if proj is None:
        return []
    rows = execute(
        f"SELECT {_MEM_COLUMNS} FROM memories "
        "WHERE project_id = %s AND stage = 'memory' AND status = 'active' "
        "AND namespace = %s AND importance >= %s "
        "ORDER BY importance DESC, updated_at DESC LIMIT %s",
        (proj["id"], namespace, min_importance, limit),
        fetch=True,
    )
    return [_serialize_memory(r) for r in (rows or [])]


def associate(
    query: str,
    limit: int = 10,
    hops: int = 1,
    cross_project: bool = True,
    min_importance: int = 1,
    project: Optional[str] = None,
    namespace: str = "claude",
    top_k_entities: int = 5,
) -> Dict[str, Any]:
    """Associative recall: query -> matching concept hubs -> linked memories.

    Pull-based retrieval (triggered by the current problem): embed the query, match the
    top-k global concept entities by similarity, return active memories linked to them —
    crossing projects by default (cross-disciplinary compounding). hops>=2 also expands to
    entities related to the matched ones via the relations table.
    """
    try:
        from hermes.embedding import generate_embedding
        vec = np.array(generate_embedding(query), dtype=np.float32)
    except Exception:
        logger.warning("associate: embedding failed for query")
        return []

    conn = get_conn()
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT e.name FROM entities e
                   WHERE e.embedding IS NOT NULL
                   ORDER BY e.embedding <-> %s::vector LIMIT %s""",
                (vec, top_k_entities),
            )
            names = [r["name"] for r in cur.fetchall()]
    finally:
        put_conn(conn)

    if not names:
        return []

    if hops >= 2:
        extra = execute(
            """SELECT DISTINCT CASE WHEN source_id = e.id THEN te.name ELSE se.name END AS name
               FROM relations r
               JOIN entities e ON e.name = ANY(%s)
               JOIN entities se ON r.source_id = se.id
               JOIN entities te ON r.target_id = te.id
               WHERE r.source_id = e.id OR r.target_id = e.id""",
            (names,), fetch=True,
        )
        names = list({n for n in names + [r["name"] for r in (extra or [])]})

    project_cond, proj_params = "", []
    if not cross_project and project:
        proj = execute_one(
            "SELECT id FROM projects WHERE path = %s OR name = %s LIMIT 1",
            (project, project),
        )
        if proj is None:
            return {"query": query, "hubs": names, "count": 0, "items": []}
        project_cond = " AND m.project_id = %s"
        proj_params = [proj["id"]]

    mem_cols = ", ".join("m." + c.strip() for c in _MEM_COLUMNS.split(","))
    rows = execute(
        f"""SELECT DISTINCT ON (m.id) {mem_cols} FROM memories m
            JOIN memory_entities me ON m.id = me.memory_id
            JOIN entities e ON me.entity_id = e.id
            WHERE e.name = ANY(%s) AND m.status = 'active' AND m.namespace = %s
                  AND m.importance >= %s{project_cond}
            ORDER BY m.id, m.importance DESC, m.updated_at DESC
            LIMIT %s""",
        [names, namespace, min_importance] + proj_params + [limit],
        fetch=True,
    )
    items = [_serialize_memory(r) for r in (rows or [])]
    return [{"query": query, "hubs": names, "count": len(items), "items": items}]


def supersede_memory(memory_id: str, namespace: str = "claude") -> bool:
    """Mark a memory superseded (replaced by a newer one). Not injected on recall."""
    now = datetime.now(timezone.utc)
    execute(
        "UPDATE memories SET status = 'superseded', updated_at = %s "
        "WHERE id = %s AND namespace = %s",
        (now, memory_id, namespace),
    )
    row = execute_one(
        "SELECT status FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
    )
    return row is not None and row["status"] == "superseded"


def bump_memory(
    memory_id: str,
    content: Optional[str] = None,
    importance: Optional[int] = None,
    namespace: str = "claude",
) -> Optional[Dict]:
    """Reinforce an existing memory: recall_count+1, refresh updated_at,
    optionally overwrite content and/or take the higher importance (merge)."""
    now = datetime.now(timezone.utc)
    sets = ["recall_count = recall_count + 1", "updated_at = %s"]
    params: List[Any] = [now]
    if content is not None:
        sets.append("content = %s")
        params.append(content)
    if importance is not None:
        sets.append("importance = GREATEST(importance, %s)")
        params.append(importance)
    params += [memory_id, namespace]
    execute(
        f"UPDATE memories SET {', '.join(sets)} WHERE id = %s AND namespace = %s",
        tuple(params),
    )
    row = execute_one(
        f"SELECT {_MEM_COLUMNS} FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
    )
    return _serialize_memory(dict(row)) if row else None


# ── type_registry CRUD ────────────────────────────────────────────────────

def list_types(enabled_only: bool = False) -> List[Dict]:
    where = "WHERE enabled = true" if enabled_only else ""
    rows = execute(
        f"SELECT name, label, color, sort_order, enabled, created_at "
        f"FROM type_registry {where} ORDER BY sort_order, name",
        fetch=True,
    )
    return [_serialize_memory(r) for r in (rows or [])]


def upsert_type(name: str, label: str, color: Optional[str] = None,
                sort_order: int = 0, enabled: bool = True) -> Optional[Dict]:
    rows = execute(
        """
        INSERT INTO type_registry (name, label, color, sort_order, enabled)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (name) DO UPDATE SET
            label = EXCLUDED.label,
            color = EXCLUDED.color,
            sort_order = EXCLUDED.sort_order,
            enabled = EXCLUDED.enabled
        RETURNING name, label, color, sort_order, enabled, created_at
        """,
        (name, label, color, sort_order, enabled),
        fetch=True,
    )
    return _serialize_memory(rows[0]) if rows else None


def delete_type(name: str) -> bool:
    execute("DELETE FROM type_registry WHERE name = %s", (name,), fetch=False)
    check = execute_one("SELECT name FROM type_registry WHERE name = %s", (name,))
    return check is None


# ── projects CRUD ─────────────────────────────────────────────────────────

def list_projects(namespace: Optional[str] = None) -> List[Dict]:
    if namespace:
        rows = execute(
            "SELECT id, name, path, namespace, created_at, updated_at "
            "FROM projects WHERE namespace = %s ORDER BY name",
            (namespace,),
            fetch=True,
        )
    else:
        rows = execute(
            "SELECT id, name, path, namespace, created_at, updated_at "
            "FROM projects ORDER BY namespace, name",
            fetch=True,
        )
    return [_serialize_memory(r) for r in (rows or [])]


def get_or_create_project(name: str, path: Optional[str] = None,
                          namespace: str = "claude") -> Dict:
    rows = execute(
        """
        INSERT INTO projects (name, path, namespace)
        VALUES (%s, %s, %s)
        ON CONFLICT (namespace, name) DO UPDATE SET path = EXCLUDED.path
        RETURNING id, name, path, namespace, created_at, updated_at
        """,
        (name, path, namespace),
        fetch=True,
    )
    return _serialize_memory(rows[0]) if rows else {}


def delete_project(project_id: str) -> bool:
    execute("DELETE FROM projects WHERE id = %s", (project_id,), fetch=False)
    check = execute_one("SELECT id FROM projects WHERE id = %s", (project_id,))
    return check is None


# ── A2A Agents ────────────────────────────────────────────────────────────

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
                    auth_credentials = EXCLUDED.auth_credentials,
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
