"""DB integration tests for counterexample gate (refutation links + threshold demotion).

Run with MERCURY_TEST_DB=hermes_test; skipped otherwise (db fixture gates it).
"""
import json

import pytest

pytestmark = pytest.mark.usefixtures("db")


def _vec(seed: int):
    import numpy as np
    v = np.zeros(1024, dtype="float32")
    v[seed % 1024] = 1.0
    return v.tolist()


def _write(content, project_id, **kw):
    from hermes.memory_service import write_memory
    return write_memory(
        content=content, stage="memory", type=kw.get("type", "ARCH"),
        importance=kw.get("importance", 3), status="active",
        project_id=project_id, embedding=_vec(kw.get("seed", 1)), auto_embed=False,
    )


def _proj(name):
    from hermes.memory_service import get_or_create_project
    return get_or_create_project(name, f"/{name}", "claude")["id"]


def test_v3_tables_exist():
    from hermes.db import execute_one
    for tbl in ("memory_refutations", "memory_events"):
        row = execute_one(
            "SELECT to_regclass(%s) AS c", (tbl,),
        )
        assert row and row["c"] == tbl, f"table {tbl} missing after init_db"


def test_threshold_for_boundaries():
    from hermes.counterexample_service import threshold_for
    assert threshold_for(3, "ARCH") == 1      # normal
    assert threshold_for(4, "ARCH") == 2      # high importance
    assert threshold_for(5, "ARCH") == 2
    assert threshold_for(3, "DECISION") == 2  # decision floor
    assert threshold_for(4, "DECISION") == 2


def test_add_and_count_refutation():
    from hermes.counterexample_service import add_refutation, count_refutations
    pid = _proj("p-add")
    t = _write("target", pid, seed=1)
    e = _write("evidence", pid, seed=2)
    ref = add_refutation(target_id=t["id"], refuting_id=e["id"], reason="contradicts")
    assert ref is not None and ref["target_id"] == t["id"]
    assert count_refutations(t["id"]) == 1


def test_namespace_isolation():
    from hermes.counterexample_service import add_refutation, count_refutations
    pid = _proj("p-ns")
    t = _write("t", pid, seed=3)
    e = _write("e", pid, seed=4)
    add_refutation(t["id"], e["id"], "r", namespace="claude")
    assert count_refutations(t["id"], namespace="claude") == 1
    assert count_refutations(t["id"], namespace="other") == 0


def test_unique_constraint_dedups():
    from hermes.counterexample_service import add_refutation, count_refutations
    pid = _proj("p-dedup")
    t = _write("t", pid, seed=5)
    e = _write("e", pid, seed=6)
    add_refutation(t["id"], e["id"], "r1")
    add_refutation(t["id"], e["id"], "r2")  # same (target, refuting, session)
    assert count_refutations(t["id"]) == 1


def test_add_refutation_requires_evidence():
    import pytest as _pytest
    from hermes.counterexample_service import add_refutation
    pid = _proj("p-req")
    t = _write("t", pid, seed=7)
    with _pytest.raises(ValueError):
        add_refutation(target_id=t["id"], reason="no evidence")


def test_add_refutation_session_ref():
    from hermes.counterexample_service import add_refutation, count_refutations
    pid = _proj("p-sess")
    t = _write("t", pid, seed=8)
    ref = add_refutation(
        target_id=t["id"], session_ref={"session_id": "s1", "span": [10, 20]},
        reason="session shows opposite", source="agent",
    )
    assert ref is not None
    assert count_refutations(t["id"]) == 1
    assert "session_id" in ref["session_ref"]  # TEXT stored, parsed back


def test_record_and_list_events():
    from hermes.counterexample_service import record_event, list_events
    pid = _proj("p-evt")
    t = _write("t", pid, seed=9)
    record_event(t["id"], "refuted", "agent", reason="manual", details={"k": "v"})
    evs = list_events(t["id"])
    assert any(e["event"] == "refuted" and e["trigger"] == "agent" for e in evs)
