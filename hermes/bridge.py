"""Bridge to Hermes memory system — PostgreSQL backend."""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _ensure_db():
    """Initialize DB pool if needed."""
    from hermes.db import init_pool
    init_pool()


def read_memory(target: str = "all") -> Dict[str, Any]:
    """Read memory entries from PostgreSQL."""
    _ensure_db()
    from hermes.memory_service import read_memories
    result = {}

    if target in ("memory", "all"):
        memories = read_memories(limit=100)
        result["memory"] = {
            "entries": [m["content"] for m in memories],
            "count": len(memories),
            "stages": list(set(m["stage"] for m in memories)),
        }

    if target in ("user", "all"):
        from hermes.db import execute
        rows = execute(
            "SELECT content FROM memories WHERE 'user-profile' = ANY(tags) LIMIT 50",
            fetch=True,
        ) or []
        result["user"] = {
            "entries": [r["content"] for r in rows],
            "count": len(rows),
        }

    return result


def write_memory(target: str, action: str, content: str, old_text: str = "") -> Dict[str, Any]:
    """Write to memory. Maps to PostgreSQL memory_service."""
    _ensure_db()
    from hermes.memory_service import write_memory as _write

    tags = []
    if target == "user":
        tags = ["user-profile"]

    mem = _write(
        content=content,
        stage="memory",
        source="agent",
        importance=4,
        tags=tags,
        auto_embed=True,
    )
    return {"success": True, "id": mem.get("id")}


def delete_memory(target: str, substring: str) -> Dict[str, Any]:
    """Delete a memory by substring match."""
    _ensure_db()
    from hermes.db import execute
    result = execute(
        "DELETE FROM memories WHERE content LIKE %s RETURNING id",
        (f"%{substring}%",),
        fetch=True,
    )
    deleted = len(result) if result else 0
    return {"success": True, "deleted": deleted}


def search_memory(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Hybrid search: vector + FTS."""
    _ensure_db()
    from hermes.memory_service import search_memories
    return search_memories(query, limit=limit)


def recall_memory(query: str, limit: int = 5, source: str = "") -> List[Dict[str, Any]]:
    """Semantic recall — same as search_memories."""
    _ensure_db()
    from hermes.memory_service import search_memories
    return search_memories(query, limit=limit)
