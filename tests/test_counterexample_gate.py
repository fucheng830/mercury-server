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


def test_demote_normal_importance_one_refutation():
    from hermes.counterexample_service import add_refutation, demote_by_threshold
    from hermes.memory_service import get_memory
    pid = _proj("p-dem1")
    t = _write("old", pid, importance=3, seed=10)
    e = _write("contra", pid, seed=11)
    add_refutation(t["id"], e["id"], "r")
    r = demote_by_threshold(t["id"])
    assert r["demoted"] is True and r["count"] == 1 and r["threshold"] == 1
    assert get_memory(t["id"])["status"] == "superseded"


def test_demote_high_importance_needs_two():
    from hermes.counterexample_service import add_refutation, demote_by_threshold
    pid = _proj("p-dem2")
    t = _write("arch", pid, importance=4, seed=12)   # threshold 2
    e1 = _write("e1", pid, seed=13)
    add_refutation(t["id"], e1["id"], "r1")
    assert demote_by_threshold(t["id"])["demoted"] is False   # 1 < 2
    e2 = _write("e2", pid, seed=14)
    add_refutation(t["id"], e2["id"], "r2")
    assert demote_by_threshold(t["id"])["demoted"] is True    # 2 >= 2


def test_demote_decision_floor_two():
    from hermes.counterexample_service import add_refutation, demote_by_threshold
    pid = _proj("p-dem3")
    t = _write("dec", pid, importance=3, type="DECISION", seed=15)  # decision_min 2
    e1 = _write("e1", pid, seed=16)
    add_refutation(t["id"], e1["id"], "r1")
    assert demote_by_threshold(t["id"])["demoted"] is False
    e2 = _write("e2", pid, seed=17)
    add_refutation(t["id"], e2["id"], "r2")
    assert demote_by_threshold(t["id"])["demoted"] is True


def test_demote_skips_non_active():
    from hermes.counterexample_service import add_refutation, demote_by_threshold, restore_memory
    pid = _proj("p-dem4")
    t = _write("t", pid, importance=3, seed=18)
    e = _write("e", pid, seed=19)
    add_refutation(t["id"], e["id"], "r")
    demote_by_threshold(t["id"])          # now superseded
    again = demote_by_threshold(t["id"])  # should skip
    assert again["demoted"] is False


def test_demote_records_threshold_event():
    from hermes.counterexample_service import add_refutation, demote_by_threshold, list_events
    pid = _proj("p-dem5")
    t = _write("t", pid, importance=3, seed=20)
    e = _write("e", pid, seed=21)
    add_refutation(t["id"], e["id"], "r")
    demote_by_threshold(t["id"])
    evs = list_events(t["id"])
    assert any(ev["event"] == "demoted" and ev["trigger"] == "threshold" for ev in evs)


def test_restore_clears_superseded():
    from hermes.counterexample_service import add_refutation, demote_by_threshold, restore_memory
    from hermes.memory_service import get_memory
    pid = _proj("p-rest")
    t = _write("t", pid, importance=3, seed=22)
    e = _write("e", pid, seed=23)
    add_refutation(t["id"], e["id"], "r")
    demote_by_threshold(t["id"])
    assert get_memory(t["id"])["status"] == "superseded"
    assert restore_memory(t["id"]) is True
    m = get_memory(t["id"])
    assert m["status"] == "active" and m.get("valid_to") is None


def test_gate_status_report():
    from hermes.counterexample_service import add_refutation, gate_status
    pid = _proj("p-gate")
    t = _write("arch", pid, importance=4, seed=24)
    e = _write("e", pid, seed=25)
    add_refutation(t["id"], e["id"], "r")
    gs = gate_status(t["id"])
    assert gs["count"] == 1 and gs["threshold"] == 2 and gs["active"] is True


def test_channel1_llm_supersede_creates_refutation_and_event(monkeypatch):
    """When extractor reconcile supersedes target T in favor of new memory N,
    a memory_refutations(target=T, refuting=N, source='llm_reconcile') and a
    memory_events(T, 'superseded', 'llm_reconcile') must be recorded."""
    from hermes import extractor
    from hermes.memory_service import get_or_create_project
    from hermes.counterexample_service import count_refutations, list_events

    pid = get_or_create_project("p-ch1", "/p/ch1", "claude")["id"]
    # seed an existing active memory that reconcile will target
    existing = _write("use lib A for auth", pid, type="DECISION", importance=3, seed=30)
    # fake LLM reconcile output: supersede existing with the new item
    fake_items = [{"content": "switched to lib B", "type": "DECISION",
                   "importance": 3, "confidence": "high"}]
    fake_actions = [{"index": 0, "action": "supersede", "target": existing["id"]}]

    class _FakeLLM:
        def generate_json(self, prompt, text, max_tokens=None):
            # _reconcile_and_write makes a single reconcile call (items already
            # extracted). Return the supersede action when we see the reconcile
            # user-prompt markers.
            if "请对每条新记忆判定" in text or "现有活跃记忆" in text:
                return {"actions": fake_actions}
            return {"processed": fake_items}

    # _reconcile_and_write does both extract-style write and reconcile in one call;
    # it calls llm.generate_json once for reconcile only (items already extracted).
    counts = extractor._reconcile_and_write(
        fake_items, pid, "p-ch1", _FakeLLM(), namespace="claude")

    assert counts["superseded"] >= 1
    assert count_refutations(existing["id"]) == 1
    evs = list_events(existing["id"])
    assert any(e["event"] == "superseded" and e["trigger"] == "llm_reconcile" for e in evs)


def test_channel2_threshold_demotes_cumulative_refutations():
    """If an active memory accumulates >= threshold refutations across multiple
    ingest runs (not a single LLM supersede), channel 2 demotes it."""
    from hermes.counterexample_service import add_refutation, demote_by_threshold, list_events
    from hermes.memory_service import get_memory
    pid = _proj("p-ch2")
    t = _write("important arch", pid, importance=4, seed=40)  # threshold 2
    # two refutations from prior runs (no LLM supersede happened — LLM missed it)
    e1 = _write("contra1", pid, seed=41)
    e2 = _write("contra2", pid, seed=42)
    add_refutation(t["id"], e1["id"], "r1", source="agent")
    add_refutation(t["id"], e2["id"], "r2", source="agent")
    # channel 2 sweep on the involved target
    r = demote_by_threshold(t["id"])
    assert r["demoted"] is True
    assert get_memory(t["id"])["status"] == "superseded"
    evs = list_events(t["id"])
    assert any(e["trigger"] == "threshold" for e in evs)


def test_reconcile_apply_runs_channel2_on_involved_targets():
    from hermes import extractor
    from hermes.memory_service import get_or_create_project, get_memory
    from hermes.counterexample_service import add_refutation

    pid = get_or_create_project("p-ch2e2e", "/p/ch2e2e", "claude")["id"]
    target = _write("dup target importance 3", pid, importance=3, seed=50)
    ev = _write("prior evidence", pid, seed=51)
    add_refutation(target["id"], ev["id"], "prior", source="agent")  # at threshold 1

    fake_items = [{"content": "dup target importance 3", "type": "ARCH", "importance": 3}]

    class _FakeLLM:
        def generate_json(self, prompt, text, max_tokens=None):
            return {"actions": [{"index": 0, "action": "duplicate_of", "target": target["id"]}]}

    extractor._reconcile_and_write(fake_items, pid, "p-ch2e2e", _FakeLLM(), namespace="claude")
    assert get_memory(target["id"])["status"] == "superseded"  # channel 2 demoted


def test_endpoint_refute_then_refutations():
    from fastapi.testclient import TestClient
    from server import app
    from hermes.memory_service import get_or_create_project
    pid = get_or_create_project("p-api1", "/p/api1", "claude")["id"]
    t = _write("target", pid, seed=60)
    e = _write("evidence", pid, seed=61)
    client = TestClient(app)
    r = client.post(f"/api/memory/{t['id']}/refute", json={
        "refuting_id": e["id"], "reason": "contradicts", "source": "agent"})
    assert r.status_code == 200
    body = r.json()
    assert body["refuted"] is True
    assert body["gate"]["count"] == 1
    # importance defaults to 3 → threshold 1 → demoted immediately
    assert body["gate"]["demoted"] is True
    g = client.get(f"/api/memory/{t['id']}/refutations")
    assert g.status_code == 200
    gbody = g.json()
    assert len(gbody["refutations"]) == 1
    assert gbody["gate"]["active"] is False  # already demoted


def test_endpoint_refute_session_ref():
    from fastapi.testclient import TestClient
    from server import app
    from hermes.memory_service import get_or_create_project
    pid = get_or_create_project("p-api2", "/p/api2", "claude")["id"]
    t = _write("target2", pid, seed=62)
    client = TestClient(app)
    r = client.post(f"/api/memory/{t['id']}/refute", json={
        "session_ref": {"session_id": "s1", "span": [5, 9]},
        "reason": "session shows the opposite", "source": "agent"})
    assert r.status_code == 200 and r.json()["refuted"] is True
