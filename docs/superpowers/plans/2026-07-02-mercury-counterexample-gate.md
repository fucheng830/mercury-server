# Mercury Counterexample Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic refutation gate to mercury's memory system — `memory_refutations` evidence links + `memory_events` audit + dual-channel demotion (LLM reconcile audit + threshold backstop) — enhancing the existing `supersede` mechanism without regressing current behavior.

**Architecture:** Two new append-only tables in PostgreSQL alongside `memories`. A new focused service module `hermes/counterexample_service.py` owns refutation CRUD, threshold calculation, and demotion logic (reusing the existing `supersede_memory()` bi-temporal close). Extractor's reconcile apply step gains two integration points: channel 1 (audit on LLM supersede) and channel 2 (threshold sweep after apply). Four REST endpoints expose manual refute/demote/refutations/restore.

**Tech Stack:** Python 3, FastAPI, PostgreSQL + pgvector, psycopg2 (`execute`/`execute_one` from `hermes.db`), pytest with `db` fixture (requires `MERCURY_TEST_DB=hermes_test`).

**Spec:** `docs/superpowers/specs/2026-07-02-mercury-counterexample-gate-design.md`

**Spec deviation (plan self-review):** `session_ref` type changed from `JSONB` → `TEXT` (stores JSON string). Reason: PostgreSQL btree UNIQUE constraints cannot include `JSONB` columns; spec §3.1's `UNIQUE(target_id, refuting_id, session_ref)` would fail. TEXT keeps the dedup guarantee and the JSON content is parsed on read.

---

## File Structure

- **Create** `hermes/counterexample_service.py` — refutation CRUD + threshold calc + demotion + audit. Single responsibility: the counterexample gate.
- **Create** `tests/test_counterexample_gate.py` — DB integration tests (threshold boundaries, namespace isolation, dedup, audit).
- **Modify** `hermes/schema.sql` — append v3 migration block (two tables).
- **Modify** `hermes/extractor.py` — channel 1 audit (around `_reconcile_and_write` supersede branch, lines 176-181) + channel 2 sweep (before `return counts`).
- **Modify** `server.py` — four new routes under `/api/memory/{memory_id}/`.

`hermes/memory_service.py` is **not modified** — the gate calls its existing `supersede_memory()` rather than wrapping or duplicating it. This keeps the bi-temporal logic in one place.

---

## Task 1: Schema v3 migration (two tables)

**Files:**
- Modify: `hermes/schema.sql` (append after line 334, the bi-temporal block)
- Test: `tests/test_counterexample_gate.py` (new file, migration check)

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_counterexample_gate.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py::test_v3_tables_exist -v`
Expected: FAIL — `memory_refutations` / `memory_events` not found (`to_regclass` returns NULL).

- [ ] **Step 3: Add the v3 migration to schema.sql**

Append to `hermes/schema.sql` (after the bi-temporal block ending at line 334):

```sql

-- ════════════════════════════════════════════════════════════════════════
-- v3: Counterexample gate (2026-07-02)
-- memory_refutations: many-to-many evidence chain (why a memory is refuted).
-- memory_events: append-only demotion audit.
-- See docs/superpowers/specs/2026-07-02-mercury-counterexample-gate-design.md
-- NOTE: session_ref is TEXT (JSON string), not JSONB — btree UNIQUE cannot
-- include JSONB columns. Content is parsed as JSON on read.
-- ════════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS memory_refutations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  target_id   UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  refuting_id UUID REFERENCES memories(id) ON DELETE SET NULL,
  session_ref TEXT,
  reason      TEXT NOT NULL,
  source      VARCHAR(20) NOT NULL,
  confidence  VARCHAR(10),
  namespace   VARCHAR(50) NOT NULL DEFAULT 'claude',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (refuting_id IS NOT NULL OR session_ref IS NOT NULL),
  UNIQUE(target_id, refuting_id, session_ref)
);
CREATE INDEX IF NOT EXISTS idx_refutations_target     ON memory_refutations(target_id);
CREATE INDEX IF NOT EXISTS idx_refutations_ns_target  ON memory_refutations(namespace, target_id);

CREATE TABLE IF NOT EXISTS memory_events (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  memory_id  UUID NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  event      VARCHAR(20) NOT NULL,
  trigger    VARCHAR(20) NOT NULL,
  reason     TEXT,
  details    JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_memory_events_memory ON memory_events(memory_id, created_at DESC);
```

The migration is idempotent (`IF NOT EXISTS`) and runs via the existing `init_db()` on container start (no manual step).

- [ ] **Step 4: Run test to verify it passes**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py::test_v3_tables_exist -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server add hermes/schema.sql tests/test_counterexample_gate.py
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server commit -m "feat(schema): v3 counterexample gate tables (memory_refutations, memory_events)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: counterexample_service — refutation CRUD + threshold + audit helpers

**Files:**
- Create: `hermes/counterexample_service.py`
- Test: `tests/test_counterexample_gate.py` (append)

- [ ] **Step 1: Write failing tests for the helpers**

Append to `tests/test_counterexample_gate.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "threshold_for or add_and_count or namespace_isolation or unique_constraint or requires_evidence or session_ref or record_and_list" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hermes.counterexample_service'`.

- [ ] **Step 3: Implement counterexample_service.py**

Create `hermes/counterexample_service.py`:

```python
"""Counterexample gate: refutation links + deterministic demotion threshold.

Enhances mercury's existing supersede (LLM reconcile) with:
- memory_refutations: many-to-many evidence chain (why a memory is refuted)
- memory_events: append-only demotion audit
- dual-channel demotion: LLM reconcile (immediate, channel 1) + threshold
  backstop (cumulative, channel 2)

The gate reuses memory_service.supersede_memory() for the bi-temporal close
(status/valid_to/superseded_by) so all supersede semantics stay in one place.

See docs/superpowers/specs/2026-07-02-mercury-counterexample-gate-design.md
"""
import json
import logging
from typing import Any, Dict, List, Optional

from hermes.db import execute, execute_one

logger = logging.getLogger(__name__)

# Threshold defaults (spec §5). Move to config.yaml if tuning is needed.
HIGH_IMPORTANCE = 4
THRESHOLD_HIGH = 2      # importance >= HIGH_IMPORTANCE
THRESHOLD_NORMAL = 1    # importance < HIGH_IMPORTANCE
DECISION_MIN = 2        # DECISION type floor


def threshold_for(importance: int, mtype: str) -> int:
    """Deterministic demotion threshold for a memory (spec §5)."""
    base = THRESHOLD_HIGH if (importance or 3) >= HIGH_IMPORTANCE else THRESHOLD_NORMAL
    if (mtype or "").upper() == "DECISION":
        base = max(base, DECISION_MIN)
    return base


# ── refutation links ───────────────────────────────────────────────────────

def add_refutation(
    target_id: str,
    reason: str,
    refuting_id: Optional[str] = None,
    session_ref: Optional[Dict] = None,
    source: str = "agent",
    confidence: Optional[str] = None,
    namespace: str = "claude",
) -> Optional[Dict]:
    """Link one piece of evidence (a memory or a session span) as a refutation
    of target_id. Idempotent via UNIQUE(target_id, refuting_id, session_ref).
    Returns the refutation row, or None if deduped/dropped."""
    if not refuting_id and not session_ref:
        raise ValueError("add_refutation requires refuting_id or session_ref")
    session_text = json.dumps(session_ref) if session_ref else None
    execute(
        """INSERT INTO memory_refutations
                 (target_id, refuting_id, session_ref, reason, source, confidence, namespace)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (target_id, refuting_id, session_ref) DO NOTHING""",
        (target_id, refuting_id, session_text, reason, source, confidence, namespace),
    )
    row = execute_one(
        """SELECT id, target_id, refuting_id, session_ref, reason, source, confidence, created_at
           FROM memory_refutations
           WHERE target_id = %s
             AND refuting_id IS NOT DISTINCT FROM %s::uuid
             AND session_ref IS NOT DISTINCT FROM %s
           ORDER BY created_at DESC LIMIT 1""",
        (target_id, refuting_id, session_text),
    )
    if not row:
        return None
    out = dict(row)
    if out.get("session_ref"):
        try:
            out["session_ref"] = json.loads(out["session_ref"])
        except (TypeError, ValueError):
            pass
    return out


def count_refutations(target_id: str, namespace: str = "claude") -> int:
    row = execute_one(
        "SELECT count(*) AS c FROM memory_refutations WHERE target_id = %s AND namespace = %s",
        (target_id, namespace),
    )
    return row["c"] if row else 0


def list_refutations(target_id: str, namespace: str = "claude") -> List[Dict]:
    rows = execute(
        """SELECT id, target_id, refuting_id, session_ref, reason, source, confidence, created_at
           FROM memory_refutations
           WHERE target_id = %s AND namespace = %s
           ORDER BY created_at""",
        (target_id, namespace),
        fetch=True,
    )
    out = []
    for r in (rows or []):
        d = dict(r)
        if d.get("session_ref"):
            try:
                d["session_ref"] = json.loads(d["session_ref"])
            except (TypeError, ValueError):
                pass
        out.append(d)
    return out


# ── audit events ───────────────────────────────────────────────────────────

def record_event(
    memory_id: str,
    event: str,
    trigger: str,
    reason: Optional[str] = None,
    details: Optional[Dict] = None,
) -> Optional[Dict]:
    execute(
        """INSERT INTO memory_events (memory_id, event, trigger, reason, details)
           VALUES (%s, %s, %s, %s, %s::jsonb)""",
        (memory_id, event, trigger, reason,
         json.dumps(details) if details else None),
    )
    row = execute_one(
        "SELECT id, memory_id, event, trigger, reason, details, created_at "
        "FROM memory_events WHERE memory_id = %s ORDER BY created_at DESC LIMIT 1",
        (memory_id,),
    )
    return dict(row) if row else None


def list_events(memory_id: str) -> List[Dict]:
    rows = execute(
        "SELECT id, memory_id, event, trigger, reason, details, created_at "
        "FROM memory_events WHERE memory_id = %s ORDER BY created_at DESC",
        (memory_id,),
        fetch=True,
    )
    return [dict(r) for r in (rows or [])]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "threshold_for or add_and_count or namespace_isolation or unique_constraint or requires_evidence or session_ref or record_and_list" -v`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server add hermes/counterexample_service.py tests/test_counterexample_gate.py
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server commit -m "feat(counterexample): refutation CRUD + threshold + audit helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Demotion logic — demote_by_threshold + restore + gate_status

**Files:**
- Modify: `hermes/counterexample_service.py` (append demotion functions)
- Test: `tests/test_counterexample_gate.py` (append)

- [ ] **Step 1: Write failing tests for demotion boundaries**

Append to `tests/test_counterexample_gate.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "demote or restore or gate_status" -v`
Expected: FAIL — `ImportError: cannot import name 'demote_by_threshold'` (and restore_memory, gate_status).

- [ ] **Step 3: Append demotion functions to counterexample_service.py**

Append to `hermes/counterexample_service.py` (after `list_events`):

```python
from datetime import datetime, timezone

from hermes.memory_service import supersede_memory  # noqa: E402 (deferred import avoids cycles)


def demote_by_threshold(memory_id: str, namespace: str = "claude") -> Dict[str, Any]:
    """Channel 2: supersede an active memory if its refutation count >= threshold.
    Reuses supersede_memory() for the bi-temporal close. Idempotent — returns
    demoted=False without error if already non-active or below threshold."""
    row = execute_one(
        "SELECT id, importance, type, status FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
    )
    if not row:
        return {"demoted": False, "reason": "not found", "count": 0, "threshold": None}
    if row["status"] != "active":
        return {"demoted": False, "reason": "not active",
                "count": 0, "threshold": None}
    count = count_refutations(memory_id, namespace)
    thr = threshold_for(row["importance"] or 3, row["type"] or "NOTE")
    if count < thr:
        return {"demoted": False, "reason": "below threshold",
                "count": count, "threshold": thr}
    supersede_memory(memory_id, superseded_by=None, namespace=namespace)
    record_event(
        memory_id, "demoted", "threshold",
        reason=f"{count} refutations >= threshold {thr}",
        details={"count": count, "threshold": thr,
                 "importance": row["importance"], "type": row["type"]},
    )
    return {"demoted": True, "count": count, "threshold": thr,
            "importance": row["importance"], "type": row["type"]}


def gate_status(memory_id: str, namespace: str = "claude") -> Optional[Dict[str, Any]]:
    """Read-only snapshot of a memory's current gate state."""
    row = execute_one(
        "SELECT importance, type, status FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
    )
    if not row:
        return None
    count = count_refutations(memory_id, namespace)
    thr = threshold_for(row["importance"] or 3, row["type"] or "NOTE")
    return {"count": count, "threshold": thr,
            "active": row["status"] == "active",
            "importance": row["importance"], "type": row["type"]}


def restore_memory(memory_id: str, namespace: str = "claude") -> bool:
    """Reverse a demote: status→active, clear valid_to + superseded_by. Only
    applies to currently-superseded rows; records a 'restored' audit event."""
    now = datetime.now(timezone.utc)
    execute(
        "UPDATE memories SET status = 'active', valid_to = NULL, superseded_by = NULL, "
        "updated_at = %s WHERE id = %s AND namespace = %s AND status = 'superseded'",
        (now, memory_id, namespace),
    )
    row = execute_one(
        "SELECT status FROM memories WHERE id = %s AND namespace = %s",
        (memory_id, namespace),
    )
    restored = bool(row and row["status"] == "active")
    if restored:
        record_event(memory_id, "restored", "manual", reason="manual restore")
    return restored
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "demote or restore or gate_status" -v`
Expected: PASS (all 7).

- [ ] **Step 5: Commit**

```bash
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server add hermes/counterexample_service.py tests/test_counterexample_gate.py
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server commit -m "feat(counterexample): threshold demotion + restore + gate_status

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Channel 1 — extractor audit on LLM supersede

**Files:**
- Modify: `hermes/extractor.py` (supersede branch ~lines 176-181; `_write_extracted` must return the new memory)
- Test: `tests/test_counterexample_gate.py` (append)

- [ ] **Step 1: Confirm `_write_extracted` return value**

Run: `grep -n "def _write_extracted" hermes/extractor.py` and read the function body.
If it does not already `return` the result of `write_memory(...)`, plan to modify it to do so (Task 4 Step 3 includes this). The function is short and calls `write_memory`, which returns the memory dict.

- [ ] **Step 2: Write failing test for channel 1 audit**

Append to `tests/test_counterexample_gate.py`:

```python
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
            # First call (extract) returns the items; second (reconcile) returns actions.
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py::test_channel1_llm_supersede_creates_refutation_and_event -v`
Expected: FAIL — refutation count is 0 (current extractor calls `supersede_memory` but records no refutation/event).

- [ ] **Step 4: Modify extractor.py**

(a) Ensure `_write_extracted` returns the new memory. Locate `def _write_extracted` (around line 100-130) and make its final line `return write_memory(...)` if it isn't already (capture the dict).

(b) Replace the supersede branch in `_reconcile_and_write` (currently lines 176-181):

```python
        elif action == "supersede":
            tid = act.get("target")
            new_mem = None
            if tid:
                supersede_memory(tid, namespace=namespace)
                counts["superseded"] += 1
            new_mem = _write_extracted(item, project_id, namespace)
            counts["new"] += 1
            # Channel 1 audit: link refutation + record event
            if tid and new_mem:
                from hermes.counterexample_service import add_refutation, record_event
                add_refutation(
                    target_id=tid, refuting_id=new_mem["id"], namespace=namespace,
                    reason="LLM reconcile: superseded by newer memory",
                    source="llm_reconcile", confidence=item.get("confidence"),
                )
                record_event(
                    tid, "superseded", "llm_reconcile",
                    reason="LLM reconcile: superseded",
                    details={"superseded_by": new_mem["id"]},
                )
```

Leave the `duplicate_of` and `new` branches unchanged.

- [ ] **Step 5: Run test to verify it passes**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py::test_channel1_llm_supersede_creates_refutation_and_event -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server add hermes/extractor.py tests/test_counterexample_gate.py
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server commit -m "feat(extractor): channel 1 — audit refutation + event on LLM supersede

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Channel 2 — threshold sweep after reconcile apply

**Files:**
- Modify: `hermes/extractor.py` (`_reconcile_and_write`, before `return counts`)
- Test: `tests/test_counterexample_gate.py` (append)

- [ ] **Step 1: Write failing test for channel 2**

Append to `tests/test_counterexample_gate.py`:

```python
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


def test_reconcile_apply_runs_channel2_on_involved_targets(monkeypatch):
    """End-to-end: _reconcile_and_write supersede branch feeds involved targets
    into channel 2; a target already at threshold gets demoted even without
    a fresh LLM supersede on this run."""
    from hermes import extractor
    from hermes.memory_service import get_or_create_project, get_memory
    from hermes.counterexample_service import add_refutation, count_refutations

    pid = get_or_create_project("p-ch2e2e", "/p/ch2e2e", "claude")["id"]
    # target with importance=3 (threshold 1) and one pre-existing refutation
    target = _write("will be demoted", pid, importance=3, seed=50)
    ev = _write("prior evidence", pid, seed=51)
    add_refutation(target["id"], ev["id"], "prior", source="agent")
    assert count_refutations(target["id"]) == 1  # already at threshold 1

    # new ingest that does NOT supersede target (action=new), but channel 2
    # should still scan involved targets and demote this one.
    fake_items = [{"content": "unrelated new", "type": "NOTE", "importance": 2}]

    class _FakeLLM:
        def generate_json(self, prompt, text, max_tokens=None):
            return {"actions": [{"index": 0, "action": "new"}]}

    extractor._reconcile_and_write(fake_items, pid, "p-ch2e2e", _FakeLLM(), namespace="claude")
    # channel 2 demote happens when target appears in involved_targets.
    # Since this run didn't touch `target`, the sweep only covers run-involved ids.
    # The dedicated demote endpoint / next ingest touching target closes the loop.
    # (This test documents the scoped-sweep contract: not a global scan.)
    assert get_memory(target["id"])["status"] in ("active", "superseded")
```

Note: the second test documents that channel 2 is a **scoped** sweep (only run-involved targets), not a global scan — per spec §4.2 and §10. A target outside the current run stays active until `/refute` or a future run touches it. Adjust the assertion comment if the implementation chooses full-scan; spec §10 says scoped is the design.

- [ ] **Step 2: Run tests to verify they fail (partially)**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "channel2 or reconcile_apply_runs_channel2" -v`
Expected: `test_channel2_threshold_demotes_cumulative_refutations` PASSES already (it calls `demote_by_threshold` directly, which exists from Task 3). `test_reconcile_apply_runs_channel2` may PASS vacuously — the assertion permits both states. This test is a contract doc; the real integration is Task 5 Step 3 wiring `demote_by_threshold` into `_reconcile_and_write`.

- [ ] **Step 3: Wire channel 2 into _reconcile_and_write**

In `hermes/extractor.py`, modify `_reconcile_and_write`:
(a) Collect involved target ids during the apply loop. Before the `for i, item in enumerate(items):` loop, add:
```python
    involved_targets: List[str] = []
```
(b) In the supersede branch (Task 4 modified version), after `supersede_memory(tid, ...)`, append `tid`:
```python
            if tid:
                supersede_memory(tid, namespace=namespace)
                involved_targets.append(tid)
                counts["superseded"] += 1
```
Also append `tid` for `duplicate_of` targets (they're "involved" — a duplicate may be the tipping refutation). In the `duplicate_of` branch after `bump_memory(...)`:
```python
            if tid:
                involved_targets.append(tid)
```
(c) Before `return counts`, add the channel 2 sweep:
```python
    # Channel 2: threshold backstop — demote any involved active target whose
    # cumulative refutations now meet the gate. Catches LLM-missed cumulative
    # contradictions. Scoped to involved targets (spec §4.2) for performance.
    from hermes.counterexample_service import demote_by_threshold
    for tid in involved_targets:
        result = demote_by_threshold(tid, namespace=namespace)
        if result.get("demoted"):
            counts["threshold_demoted"] = counts.get("threshold_demoted", 0) + 1
    return counts
```

- [ ] **Step 4: Add a channel-2-via-reconcile test that asserts demotion**

Replace the body of `test_reconcile_apply_runs_channel2` with a version that makes the target an involved one (a `duplicate_of` action against it bumps it into `involved_targets`):

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "channel2 or reconcile_apply" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server add hermes/extractor.py tests/test_counterexample_gate.py
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server commit -m "feat(extractor): channel 2 — threshold sweep on involved targets

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: REST endpoints — refute + refutations

**Files:**
- Modify: `server.py` (add two routes near the existing `/api/memory/{memory_id}/confirm` at line 320)
- Test: `tests/test_counterexample_gate.py` (append, HTTP-level via FastAPI TestClient)

- [ ] **Step 1: Write failing HTTP tests**

Append to `tests/test_counterexample_gate.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "endpoint_refute" -v`
Expected: FAIL — 404 (routes don't exist yet).

- [ ] **Step 3: Add the two routes to server.py**

Insert near the existing memory routes (after `memory_reject` around line 346):

```python
@app.post("/api/memory/{memory_id}/refute")
def memory_refute(memory_id: str, body: dict):
    """Link a refutation (memory or session span) to a memory, then re-run the
    threshold gate — demotes immediately if threshold is met."""
    from hermes.counterexample_service import add_refutation, demote_by_threshold
    namespace = (body or {}).get("namespace", "claude")
    ref = add_refutation(
        target_id=memory_id,
        reason=(body or {}).get("reason", ""),
        refuting_id=(body or {}).get("refuting_id"),
        session_ref=(body or {}).get("session_ref"),
        source=(body or {}).get("source", "agent"),
        confidence=(body or {}).get("confidence"),
        namespace=namespace,
    )
    gate = demote_by_threshold(memory_id, namespace=namespace)
    return {"refuted": ref is not None, "refutation": ref, "gate": gate}


@app.get("/api/memory/{memory_id}/refutations")
def memory_refutations(memory_id: str, namespace: str = "claude"):
    """List refutation links + current gate state for a memory."""
    from hermes.counterexample_service import list_refutations, gate_status
    return {"refutations": list_refutations(memory_id, namespace),
            "gate": gate_status(memory_id, namespace)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "endpoint_refute" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server add server.py tests/test_counterexample_gate.py
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server commit -m "feat(api): POST /refute + GET /refutations endpoints

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: REST endpoints — demote + restore

**Files:**
- Modify: `server.py` (two more routes)
- Test: `tests/test_counterexample_gate.py` (append)

- [ ] **Step 1: Write failing HTTP tests**

Append to `tests/test_counterexample_gate.py`:

```python
def test_endpoint_demote_below_threshold_returns_409():
    from fastapi.testclient import TestClient
    from server import app
    from hermes.memory_service import get_or_create_project
    pid = get_or_create_project("p-api3", "/p/api3", "claude")["id"]
    t = _write("arch importance 4", pid, importance=4, seed=70)  # threshold 2, 0 refs
    client = TestClient(app)
    r = client.post(f"/api/memory/{t['id']}/demote", json={})
    assert r.status_code == 409
    assert "below threshold" in r.json()["detail"]["reason"]


def test_endpoint_restore_after_demote():
    from fastapi.testclient import TestClient
    from server import app
    from hermes.memory_service import get_or_create_project, get_memory
    pid = get_or_create_project("p-api4", "/p/api4", "claude")["id"]
    t = _write("will demote", pid, importance=3, seed=71)
    e = _write("ev", pid, seed=72)
    client = TestClient(app)
    client.post(f"/api/memory/{t['id']}/refute", json={"refuting_id": e["id"], "reason": "r"})
    assert get_memory(t["id"])["status"] == "superseded"
    r = client.post(f"/api/memory/{t['id']}/restore", json={})
    assert r.status_code == 200 and r.json()["restored"] is True
    assert get_memory(t["id"])["status"] == "active"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "endpoint_demote or endpoint_restore" -v`
Expected: FAIL — 404.

- [ ] **Step 3: Add demote + restore routes to server.py**

Insert after the `memory_refutations` route from Task 6:

```python
@app.post("/api/memory/{memory_id}/demote")
def memory_demote(memory_id: str, body: dict = None):
    """Explicitly trigger the threshold gate. 409 if below threshold."""
    from fastapi import HTTPException
    from hermes.counterexample_service import demote_by_threshold
    namespace = (body or {}).get("namespace", "claude")
    result = demote_by_threshold(memory_id, namespace=namespace)
    if not result["demoted"]:
        raise HTTPException(status_code=409, detail=result)
    return result


@app.post("/api/memory/{memory_id}/restore")
def memory_restore(memory_id: str, body: dict = None):
    """Reverse a demote (status→active, clear valid_to/superseded_by)."""
    from hermes.counterexample_service import restore_memory
    namespace = (body or {}).get("namespace", "claude")
    return {"restored": restore_memory(memory_id, namespace=namespace)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `MERCURY_TEST_DB=hermes_test python -m pytest tests/test_counterexample_gate.py -k "endpoint_demote or endpoint_restore" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server add server.py tests/test_counterexample_gate.py
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server commit -m "feat(api): POST /demote (409 on below-threshold) + /restore endpoints

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Regression + full suite + docs touch

**Files:**
- Verify: existing `tests/` still green
- Modify: `hermes/CLAUDE.md` or `README` (note the new gate) — optional, single line

- [ ] **Step 1: Run the full test suite (logic + DB)**

Run: `cd /d/workspace/projects/ai/agents/ai-agents/mercury-server && python -m pytest tests/ -q`
Then: `MERCURY_TEST_DB=hermes_test python -m pytest tests/ -q`
Expected: both green. Existing extractor / memory_service / supersede tests must still pass — confirming zero regression to LLM reconcile behavior.

- [ ] **Step 2: If any existing test fails, fix before proceeding**

Likely culprit: if `_write_extracted` signature changed (Task 4 Step 1) and other callers depend on old behavior. Restore the original return contract for other callers while exposing the new memory to `_reconcile_and_write`.

- [ ] **Step 3: Add a one-line note to CLAUDE.md data-model table**

In `mercury-server/CLAUDE.md`, append to the data-model table (after the `a2a_agents` row):

```markdown
| `memory_refutations` / `memory_events` | 反例证据链 + 降级审计（counterexample gate，确定性阈值补漏 supersede）|
```

- [ ] **Step 4: Commit docs**

```bash
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server add CLAUDE.md
git -C /d/workspace/projects/ai/agents/ai-agents/mercury-server commit -m "docs: note counterexample gate tables in data model

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Mark task complete**

Stage 1 implementation done. Deployment follows the existing chain (local commit → push → 0.17 `git pull && docker build && docker compose up -d`); `init_db()` runs the v3 migration automatically on container start.

---

## Self-Review (completed during authoring)

**1. Spec coverage:**
- §3.1 memory_refutations → Task 1 (schema) + Task 2 (CRUD) ✓
- §3.2 memory_events → Task 1 + Task 2 (record_event/list_events) ✓
- §3.3 superseded_by 分工 → preserved (gate calls supersede_memory, doesn't touch superseded_by directly; threshold channel passes superseded_by=NULL which schema allows) ✓
- §4.1 channel 1 → Task 4 ✓
- §4.2 channel 2 → Task 5 ✓
- §5 threshold rule → Task 2 (threshold_for) + Task 3 (demote_by_threshold) ✓
- §6 API (refute/demote/refutations/restore) → Task 6 + Task 7 ✓
- §7 migration → Task 1 ✓
- §8 testing → every task TDD + Task 8 regression ✓
- §9 acceptance criteria → covered by tests (建链/计数/降级/namespace/审计/restore/幂等/回归)
- §10 risks → session_ref TEXT deviation noted; scoped sweep documented in Task 5

**2. Placeholder scan:** No TBD/TODO. Every code step has runnable code. Task 4 Step 1 has a verification action (`grep` + read) rather than assumed code — explicit, not a placeholder.

**3. Type consistency:** `add_refutation(target_id, reason, refuting_id?, session_ref?, source, confidence?, namespace)` signature consistent across Task 2 def, Task 4 channel-1 call, Task 6 endpoint. `demote_by_threshold(memory_id, namespace)` consistent across Task 3 def, Task 5 channel-2 call, Task 6/7 endpoints. `threshold_for(importance, mtype)` consistent. `record_event(memory_id, event, trigger, reason?, details?)` consistent.

**Open item for execution:** Task 4 Step 1 requires confirming `_write_extracted` returns the memory dict. If it doesn't, Step 4(a) modifies it — and Task 8 Step 2 guards against caller regressions. This is the one place the plan depends on reading existing code at execution time; it's flagged as an explicit verification step, not a placeholder.
