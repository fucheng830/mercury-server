"""DB integration tests for the SQL-bearing retrieval paths (recall / search / associate).

These guard the class of bug seen this session (ambiguous 'id' column in the associate
JOIN). Run with MERCURY_TEST_DB=hermes_test; skipped otherwise.
"""
import numpy as np

import pytest

pytestmark = pytest.mark.usefixtures("db")


def _vec(seed: int):
    v = np.zeros(1024, dtype="float32")
    v[seed % 1024] = 1.0
    return v.tolist()


def _write(content, project_id, **kw):
    from hermes.memory_service import write_memory
    return write_memory(
        content=content, stage="memory", type=kw.get("type", "ARCH"),
        importance=kw.get("importance", 4), status=kw.get("status", "active"),
        project_id=project_id, embedding=kw.get("embedding", _vec(kw.get("seed", 1))),
        auto_embed=False,
    )


def test_recall_returns_active_project_memory():
    from hermes.memory_service import get_or_create_project, recall_memories
    pid = get_or_create_project("proj-a", "/p/a", "claude")["id"]
    _write("decided to use X", pid, type="DECISION", seed=1)
    items = recall_memories("/p/a")
    assert any("decided to use X" in i["content"] for i in items)


def test_recall_excludes_archived():
    from hermes.memory_service import get_or_create_project, recall_memories
    pid = get_or_create_project("proj-b", "/p/b", "claude")["id"]
    _write("active one", pid, seed=2)
    _write("archived one", pid, status="archived", seed=3)
    contents = [i["content"] for i in recall_memories("/p/b")]
    assert "active one" in contents and "archived one" not in contents


def test_associate_cross_project_via_shared_hub(embedding_ok):
    from hermes.memory_service import associate, get_or_create_project
    from hermes.graph_service import upsert_entity, link_memory_entity
    pa = get_or_create_project("pa", "/p/a", "claude")["id"]
    pb = get_or_create_project("pb", "/p/b", "claude")["id"]
    ma = _write("A about redis caching", pa, seed=10)
    mb = _write("B about redis pubsub", pb, seed=11)
    upsert_entity("redis", "concept")
    link_memory_entity(ma["id"], "redis")
    link_memory_entity(mb["id"], "redis")
    res = associate("redis", limit=10)
    projs = {i["project_id"] for i in res["items"]}
    assert pa in projs and pb in projs  # cross-project compounding


def test_associate_returns_hubs_matched(embedding_ok):
    from hermes.memory_service import associate
    from hermes.graph_service import upsert_entity
    upsert_entity("redis", "concept")
    res = associate("redis", limit=5)
    assert isinstance(res, dict)
    assert "redis" in res["hubs"]


def test_search_returns_active_only(embedding_ok):
    from hermes.memory_service import get_or_create_project, search_memories
    pid = get_or_create_project("pc", "/p/c", "claude")["id"]
    _write("pgvector hybrid rrf ranking", pid, type="DISCOVERY", seed=20)
    _write("pgvector stale archived note", pid, type="NOTE", importance=2,
           status="archived", seed=21)
    results = search_memories("pgvector", limit=5)
    contents = [r["content"] for r in results]
    assert any("rrf" in c for c in contents)
    assert not any("archived" in c for c in contents)


def test_search_default_excludes_archived_even_without_status_filter():
    """Confirms the status='active' default fix (search used to surface archived noise)."""
    from hermes.db import execute
    from hermes.memory_service import get_or_create_project, search_memories
    pid = get_or_create_project("pd", "/p/d", "claude")["id"]
    _write("uniquephrase-zzz active", pid, seed=30)
    _write("uniquephrase-zzz archived", pid, status="archived", seed=31)
    # regardless of vector/FTS, archived must never appear
    results = search_memories("uniquephrase-zzz", limit=10)
    assert all(r["status"] == "active" for r in results)
