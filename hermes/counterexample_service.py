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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from hermes.db import execute, execute_one
from hermes.memory_service import supersede_memory

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
    refuting_id: Optional[str] = None,
    reason: str = "",
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
    try:
        execute(
            """INSERT INTO memory_refutations
                     (target_id, refuting_id, session_ref, reason, source, confidence, namespace)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (target_id, refuting_id, session_text, reason, source, confidence, namespace),
        )
    except Exception as e:
        # Dedup collision on ux_refutations_dedup (NULL-aware COALESCE index)
        # — the row already exists; fall through to the SELECT to return it.
        from psycopg2.errors import UniqueViolation  # type: ignore
        if not isinstance(e, UniqueViolation):
            raise
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


# ── threshold demotion (channel 2) ──────────────────────────────────────────

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
    """Reverse a demote: status->active, clear valid_to + superseded_by. Only
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
