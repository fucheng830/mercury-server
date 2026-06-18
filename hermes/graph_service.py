"""Graph service for entity/relation CRUD and graph traversal queries."""
import logging
from collections import deque
from typing import Dict, List, Optional

import numpy as np
from psycopg2.extras import RealDictCursor

from hermes.db import execute, execute_one, get_conn, put_conn
from hermes.memory_service import _serialize_memory

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = {"person", "project", "tool", "concept", "technology"}
VALID_RELATIONS = {"uses", "depends_on", "prefers", "owns", "related_to"}


def upsert_entity(
    name: str,
    entity_type: str,
    description: str = "",
    auto_embed: bool = True,
) -> Dict:
    """Create or update an entity.

    Args:
        name: Unique entity name.
        entity_type: One of person, project, tool, concept, technology.
        description: Optional description text.
        auto_embed: Whether to generate embedding from name+description.

    Returns:
        Serialized entity dict.

    Raises:
        ValueError: If entity_type is invalid.
    """
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity_type '{entity_type}'. Must be one of: {', '.join(sorted(VALID_ENTITY_TYPES))}"
        )

    # Check if entity already exists
    existing = execute_one(
        "SELECT id, name, entity_type, description, embedding, created_at, updated_at FROM entities WHERE name = %s",
        (name,),
    )

    if existing:
        # Update existing entity
        embedding = None
        if auto_embed:
            embedding = _generate_entity_embedding(name, description)

        conn = get_conn()
        try:
            if embedding is not None:
                from pgvector.psycopg2 import register_vector
                register_vector(conn)
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        UPDATE entities
                        SET entity_type = %s, description = %s, embedding = %s::vector, updated_at = now()
                        WHERE name = %s
                        RETURNING id, name, entity_type, description, embedding, created_at, updated_at
                        """,
                        (entity_type, description, embedding, name),
                    )
                    row = dict(cur.fetchone())
            else:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                        UPDATE entities
                        SET entity_type = %s, description = %s, updated_at = now()
                        WHERE name = %s
                        RETURNING id, name, entity_type, description, embedding, created_at, updated_at
                        """,
                        (entity_type, description, name),
                    )
                    row = dict(cur.fetchone())
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)
        return _serialize_memory(row)

    # Insert new entity
    embedding = None
    if auto_embed:
        embedding = _generate_entity_embedding(name, description)

    conn = get_conn()
    try:
        if embedding is not None:
            from pgvector.psycopg2 import register_vector
            register_vector(conn)
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO entities (name, entity_type, description, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    RETURNING id, name, entity_type, description, embedding, created_at, updated_at
                    """,
                    (name, entity_type, description, embedding),
                )
                row = dict(cur.fetchone())
        else:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO entities (name, entity_type, description)
                    VALUES (%s, %s, %s)
                    RETURNING id, name, entity_type, description, embedding, created_at, updated_at
                    """,
                    (name, entity_type, description),
                )
                row = dict(cur.fetchone())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

    return _serialize_memory(row)


def _generate_entity_embedding(name: str, description: str) -> Optional[np.ndarray]:
    """Generate embedding for an entity from its name and description."""
    try:
        from hermes.embedding import generate_embedding
        text = f"{name} {description}".strip()
        vec = generate_embedding(text)
        return np.array(vec, dtype=np.float32)
    except Exception:
        logger.warning("Failed to generate entity embedding for '%s'", name)
        return None


def add_relation(
    source_name: str,
    target_name: str,
    relation: str,
    strength: float = 1.0,
) -> Dict:
    """Create or update a relation between two entities.

    Args:
        source_name: Name of the source entity.
        target_name: Name of the target entity.
        relation: One of uses, depends_on, prefers, owns, related_to.
        strength: Relation strength between 0 and 1.

    Returns:
        Dict with source, target, relation, strength.

    Raises:
        ValueError: If relation is invalid or entity not found.
    """
    if relation not in VALID_RELATIONS:
        raise ValueError(
            f"Invalid relation '{relation}'. Must be one of: {', '.join(sorted(VALID_RELATIONS))}"
        )

    source = execute_one("SELECT id FROM entities WHERE name = %s", (source_name,))
    target = execute_one("SELECT id FROM entities WHERE name = %s", (target_name,))

    if source is None:
        raise ValueError(f"Source entity '{source_name}' not found")
    if target is None:
        raise ValueError(f"Target entity '{target_name}' not found")

    source_id = source["id"]
    target_id = target["id"]

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO relations (source_id, target_id, relation, strength)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (source_id, target_id, relation)
                DO UPDATE SET strength = EXCLUDED.strength
                RETURNING id, source_id, target_id, relation, strength
                """,
                (source_id, target_id, relation, strength),
            )
            row = dict(cur.fetchone())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)

    return {
        "source": source_name,
        "target": target_name,
        "relation": row["relation"],
        "strength": row["strength"],
    }


def get_entity_graph(
    entity_name: Optional[str] = None,
    depth: int = 1,
    entity_type: Optional[str] = None,
) -> Dict:
    """Retrieve entity graph, optionally filtered or traversed from a node.

    Args:
        entity_name: Starting entity for BFS traversal. If None, returns all.
        depth: Number of hops for BFS traversal (default 1).
        entity_type: Filter entities by type. If None, no filter.

    Returns:
        Dict with 'entities' and 'relations' lists.
    """
    if entity_name is not None:
        return _traverse_graph(entity_name, depth)
    elif entity_type is not None:
        return _filter_by_type(entity_type)
    else:
        return _get_all()


def _get_all() -> Dict:
    """Return all entities and relations."""
    entity_rows = execute(
        "SELECT id, name, entity_type, description, embedding, created_at, updated_at FROM entities",
        fetch=True,
    )
    relation_rows = execute(
        """
        SELECT r.id, e1.name AS source_name, e2.name AS target_name,
               r.relation, r.strength, r.created_at
        FROM relations r
        JOIN entities e1 ON r.source_id = e1.id
        JOIN entities e2 ON r.target_id = e2.id
        """,
        fetch=True,
    )
    entities = [_serialize_memory(r) for r in (entity_rows or [])]
    relations = [_serialize_memory(r) for r in (relation_rows or [])]
    return {"entities": entities, "relations": relations}


def _filter_by_type(entity_type: str) -> Dict:
    """Return entities filtered by type and their relations."""
    entity_rows = execute(
        "SELECT id, name, entity_type, description, embedding, created_at, updated_at FROM entities WHERE entity_type = %s",
        (entity_type,),
        fetch=True,
    )
    if not entity_rows:
        return {"entities": [], "relations": []}

    entity_ids = [r["id"] for r in entity_rows]
    placeholders = ",".join(["%s"] * len(entity_ids))

    relation_rows = execute(
        f"""
        SELECT r.id, e1.name AS source_name, e2.name AS target_name,
               r.relation, r.strength, r.created_at
        FROM relations r
        JOIN entities e1 ON r.source_id = e1.id
        JOIN entities e2 ON r.target_id = e2.id
        WHERE r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders})
        """,
        tuple(entity_ids) * 2,
        fetch=True,
    )

    entities = [_serialize_memory(r) for r in entity_rows]
    relations = [_serialize_memory(r) for r in (relation_rows or [])]
    return {"entities": entities, "relations": relations}


def _traverse_graph(entity_name: str, depth: int) -> Dict:
    """BFS traversal from a given entity node up to `depth` hops."""
    start = execute_one("SELECT id FROM entities WHERE name = %s", (entity_name,))
    if start is None:
        return {"entities": [], "relations": []}

    start_id = start["id"]

    # BFS to collect entity IDs within depth hops
    visited_ids = {start_id}
    current_level = {start_id}

    for _ in range(depth):
        next_level = set()
        placeholders = ",".join(["%s"] * len(current_level))
        # Find all neighbors
        rows = execute(
            f"""
            SELECT source_id, target_id FROM relations
            WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
            """,
            tuple(current_level) * 2,
            fetch=True,
        )
        for r in (rows or []):
            sid = r["source_id"]
            tid = r["target_id"]
            if sid not in visited_ids:
                next_level.add(sid)
            if tid not in visited_ids:
                next_level.add(tid)
        visited_ids |= next_level
        current_level = next_level
        if not current_level:
            break

    # Fetch entities
    placeholders = ",".join(["%s"] * len(visited_ids))
    entity_rows = execute(
        f"SELECT id, name, entity_type, description, embedding, created_at, updated_at FROM entities WHERE id IN ({placeholders})",
        tuple(visited_ids),
        fetch=True,
    )

    # Fetch relations among visited entities
    relation_rows = execute(
        f"""
        SELECT r.id, e1.name AS source_name, e2.name AS target_name,
               r.relation, r.strength, r.created_at
        FROM relations r
        JOIN entities e1 ON r.source_id = e1.id
        JOIN entities e2 ON r.target_id = e2.id
        WHERE r.source_id IN ({placeholders}) OR r.target_id IN ({placeholders})
        """,
        tuple(visited_ids) * 2,
        fetch=True,
    )

    entities = [_serialize_memory(r) for r in (entity_rows or [])]
    relations = [_serialize_memory(r) for r in (relation_rows or [])]
    return {"entities": entities, "relations": relations}


def get_entity_memories(entity_name: str) -> List[Dict]:
    """Get all memories linked to an entity.

    Args:
        entity_name: Name of the entity.

    Returns:
        List of serialized memory dicts.
    """
    rows = execute(
        """
        SELECT m.id, m.stage, m.content, m.summary, m.source, m.importance,
               m.tags, m.embedding, m.recall_count, m.created_at, m.expires_at, m.updated_at
        FROM memories m
        JOIN memory_entities me ON m.id = me.memory_id
        JOIN entities e ON me.entity_id = e.id
        WHERE e.name = %s
        ORDER BY m.created_at DESC
        """,
        (entity_name,),
        fetch=True,
    )
    return [_serialize_memory(r) for r in (rows or [])]


def link_memory_entity(memory_id: str, entity_name: str) -> None:
    """Link a memory to an entity.

    Args:
        memory_id: UUID string of the memory.
        entity_name: Name of the entity.

    Raises:
        ValueError: If entity not found.
    """
    entity = execute_one("SELECT id FROM entities WHERE name = %s", (entity_name,))
    if entity is None:
        raise ValueError(f"Entity '{entity_name}' not found")

    execute(
        """
        INSERT INTO memory_entities (memory_id, entity_id)
        VALUES (%s, %s)
        ON CONFLICT (memory_id, entity_id) DO NOTHING
        """,
        (memory_id, entity["id"]),
    )
