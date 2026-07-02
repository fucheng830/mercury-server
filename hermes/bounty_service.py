"""C-stage bounty economy service: wallet, ledger, bounty lifecycle.

Layered on top of Mercury memory/sharing. Wallet keyed by namespace (= agent_id).
Escrow = system (to_namespace = NULL). Infinite-issuance model (MVP).
See docs/superpowers/specs/2026-07-02-c-stage-knowledge-market-design.md
"""
import logging
import uuid
from typing import Any, Dict, List, Optional

from hermes.db import execute, execute_one

logger = logging.getLogger(__name__)

# Economy constants
INITIAL_GRANT = 100
MIN_BOUNTY = 10
BOUNTY_BONUS_RATE = 0.2  # 20% solver bonus


# ── Wallet ────────────────────────────────────────────────────────────────

def get_or_create_wallet(namespace: str) -> Dict[str, Any]:
    """Get a namespace's wallet; create with INITIAL_GRANT + record grant txn if new."""
    row = execute_one(
        "SELECT namespace, token_balance, tokens_earned, tokens_spent "
        "FROM wallets WHERE namespace = %s",
        (namespace,),
    )
    if row:
        return row
    execute(
        "INSERT INTO wallets (namespace, token_balance, tokens_earned, tokens_spent) "
        "VALUES (%s, %s, 0, 0)",
        (namespace, INITIAL_GRANT),
    )
    execute(
        "INSERT INTO transactions (id, from_namespace, to_namespace, amount, transaction_type, description) "
        "VALUES (%s, NULL, %s, %s, 'initial_grant', 'Initial token grant')",
        (str(uuid.uuid4()), namespace, INITIAL_GRANT),
    )
    return execute_one(
        "SELECT namespace, token_balance, tokens_earned, tokens_spent FROM wallets WHERE namespace = %s",
        (namespace,),
    )


def get_balance(namespace: str) -> int:
    return get_or_create_wallet(namespace)["token_balance"]


# ── Ledger ────────────────────────────────────────────────────────────────

def transfer(
    from_namespace: Optional[str],
    to_namespace: Optional[str],
    amount: int,
    transaction_type: str,
    reference_id: Optional[str] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """Move tokens between namespaces (None = system/escrow). Records a transaction."""
    txn_id = str(uuid.uuid4())
    execute(
        "INSERT INTO transactions (id, from_namespace, to_namespace, amount, transaction_type, reference_id, description) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (txn_id, from_namespace, to_namespace, amount, transaction_type, reference_id, description),
    )
    if from_namespace:
        get_or_create_wallet(from_namespace)
        execute(
            "UPDATE wallets SET token_balance = token_balance - %s, tokens_spent = tokens_spent + %s "
            "WHERE namespace = %s",
            (amount, amount, from_namespace),
        )
    if to_namespace:
        get_or_create_wallet(to_namespace)
        execute(
            "UPDATE wallets SET token_balance = token_balance + %s, tokens_earned = tokens_earned + %s "
            "WHERE namespace = %s",
            (amount, amount, to_namespace),
        )
    return {"id": txn_id, "from": from_namespace, "to": to_namespace, "amount": amount, "type": transaction_type}


def get_transactions(namespace: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    if namespace:
        return execute(
            "SELECT id, from_namespace, to_namespace, amount, transaction_type, reference_id, description, created_at "
            "FROM transactions WHERE from_namespace = %s OR to_namespace = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (namespace, namespace, limit),
            fetch=True,
        ) or []
    return execute(
        "SELECT id, from_namespace, to_namespace, amount, transaction_type, reference_id, description, created_at "
        "FROM transactions ORDER BY created_at DESC LIMIT %s",
        (limit,),
        fetch=True,
    ) or []


# ── Bounty lifecycle ──────────────────────────────────────────────────────

def create_bounty(
    question: str,
    amount: int,
    creator_namespace: str,
    framework: Optional[str] = None,
    expires_in_hours: int = 24,
) -> Dict[str, Any]:
    """Create a bounty: debit creator → escrow (escrow model A, lock on create)."""
    if amount < MIN_BOUNTY:
        raise ValueError(f"Bounty amount below minimum ({MIN_BOUNTY})")
    balance = get_balance(creator_namespace)
    if balance < amount:
        raise ValueError(f"Insufficient balance: have {balance}, need {amount}")

    rows = execute(
        "INSERT INTO bounties (question, amount, framework, creator_namespace, expires_at) "
        "VALUES (%s, %s, %s, %s, now() + %s * interval '1 hour') RETURNING *",
        (question, amount, framework, creator_namespace, expires_in_hours),
        fetch=True,
    )
    bounty = rows[0] if rows else None
    transfer(
        from_namespace=creator_namespace,
        to_namespace=None,
        amount=amount,
        transaction_type="bounty_create",
        reference_id=str(bounty["id"]),
        description=f"Bounty escrow: {question[:50]}",
    )
    return bounty


def list_bounties(
    status: Optional[str] = "open",
    framework: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    conditions, params = [], []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if framework:
        conditions.append("framework = %s")
        params.append(framework)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    rows = execute(
        f"SELECT id, question, amount, framework, status, creator_namespace, created_at, expires_at "
        f"FROM bounties {where} ORDER BY amount DESC, created_at DESC LIMIT %s",
        tuple(params),
        fetch=True,
    ) or []
    # serialize for JSON (UUID/datetimes)
    out = []
    for r in rows:
        out.append({
            **r,
            "id": str(r["id"]),
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            "expires_at": r["expires_at"].isoformat() if r.get("expires_at") else None,
        })
    return out


def claim_bounty(bounty_id: str, claimer_namespace: str) -> Optional[Dict[str, Any]]:
    """Atomically claim an open bounty (open → claimed). Returns None if not open."""
    rows = execute(
        "UPDATE bounties SET status = 'claimed', claimer_namespace = %s, claimed_at = now() "
        "WHERE id = %s AND status = 'open' RETURNING *",
        (claimer_namespace, bounty_id),
        fetch=True,
    )
    return rows[0] if rows else None


def answer_bounty(
    bounty_id: str,
    solution: str,
    solver_namespace: str,
) -> Optional[Dict[str, Any]]:
    """Submit answer for a claimed bounty → enters PENDING (status=answered).

    C2-1 governance: NO reward yet. Solution sunk as an observation memory;
    creator must call accept_bounty() to reward + promote, or reject_bounty() to reopen.
    """
    bounty = execute_one(
        "SELECT * FROM bounties WHERE id = %s AND claimer_namespace = %s AND status = 'claimed'",
        (bounty_id, solver_namespace),
    )
    if not bounty:
        return None

    # Sink solution as observation (pending acceptance — not promoted to memory yet)
    memory_id = None
    try:
        from hermes.memory_service import write_memory
        mem = write_memory(
            content=f"Q: {bounty['question']}\n\nA: {solution}",
            stage="observation",
            source="bounty",
            importance=4,
            type="DISCOVERY",
            scope="global",
            summary=bounty["question"][:200],
            auto_embed=True,
            namespace=solver_namespace,
        )
        memory_id = str(mem["id"]) if mem and mem.get("id") else None
    except Exception as e:
        logger.warning("write_memory for bounty answer failed: %s", e)

    execute(
        "UPDATE bounties SET status = 'answered', solution = %s, resolved_memory_id = %s "
        "WHERE id = %s",
        (solution, memory_id, bounty_id),
    )
    return {"bounty_id": bounty_id, "memory_id": memory_id, "status": "answered"}


def accept_bounty(
    bounty_id: str,
    creator_namespace: str,
) -> Optional[Dict[str, Any]]:
    """Creator accepts a pending answer → reward solver (amount + bonus) + promote memory."""
    bounty = execute_one(
        "SELECT * FROM bounties WHERE id = %s AND creator_namespace = %s AND status = 'answered'",
        (bounty_id, creator_namespace),
    )
    if not bounty:
        return None

    bonus = round(bounty["amount"] * BOUNTY_BONUS_RATE)
    reward = bounty["amount"] + bonus
    solver = bounty["claimer_namespace"]
    transfer(
        from_namespace=None,
        to_namespace=solver,
        amount=reward,
        transaction_type="bounty_reward",
        reference_id=bounty_id,
        description=f"Bounty reward + {bonus} bonus (accepted)",
    )

    # Promote solution memory: observation → memory
    mid = bounty.get("resolved_memory_id")
    if mid:
        try:
            execute("UPDATE memories SET stage = 'memory' WHERE id = %s", (mid,))
        except Exception as e:
            logger.warning("memory promote on accept failed: %s", e)

    execute(
        "UPDATE bounties SET status = 'resolved', accepted_at = now(), resolved_at = now() "
        "WHERE id = %s",
        (bounty_id,),
    )
    return {"bounty_id": bounty_id, "solver": solver, "reward": reward, "bonus": bonus}


def reject_bounty(
    bounty_id: str,
    creator_namespace: str,
) -> Optional[Dict[str, Any]]:
    """Creator rejects a pending answer → no reward, archive solution memory, reopen bounty."""
    bounty = execute_one(
        "SELECT * FROM bounties WHERE id = %s AND creator_namespace = %s AND status = 'answered'",
        (bounty_id, creator_namespace),
    )
    if not bounty:
        return None

    # Archive the rejected solution memory
    mid = bounty.get("resolved_memory_id")
    if mid:
        try:
            execute("UPDATE memories SET status = 'archived' WHERE id = %s", (mid,))
        except Exception as e:
            logger.warning("memory archive on reject failed: %s", e)

    # Reopen: clear claimer/solution, back to open for re-claiming
    execute(
        "UPDATE bounties SET status = 'open', claimer_namespace = NULL, claimed_at = NULL, "
        "solution = NULL, resolved_memory_id = NULL, rejected_at = now() WHERE id = %s",
        (bounty_id,),
    )
    return {"bounty_id": bounty_id, "reopened": True}


def match_bounty(bounty_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Find namespaces with relevant memory for a bounty (RRF push-matching).

    Best-effort: returns [] if search/embedding is unavailable.
    Excludes the creator's own namespace.
    """
    bounty = execute_one("SELECT * FROM bounties WHERE id = %s", (bounty_id,))
    if not bounty:
        return []
    try:
        from hermes.memory_service import search_memories
        results = search_memories(
            query_text=bounty["question"],
            limit=limit * 3,
            namespaces=None,
        )
    except Exception as e:
        logger.warning("bounty.match search failed: %s", e)
        return []

    seen: Dict[str, float] = {}
    for r in results:
        ns = r.get("namespace")
        score = r.get("rrf_score") or r.get("score") or 0
        if ns and ns != bounty["creator_namespace"] and ns not in seen:
            seen[ns] = max(seen.get(ns, 0), float(score))
    ranked = sorted(
        [{"namespace": k, "score": v} for k, v in seen.items()],
        key=lambda x: x["score"],
        reverse=True,
    )
    return ranked[:limit]


# ── Expiry / refund (C2-2) ─────────────────────────────────────────────────

EXPIRY_REFUND_RATE = {"open": 1.0, "answered": 0.8}


def expire_bounties() -> Dict[str, Any]:
    """Expire overdue bounties and refund creators.

    Rate depends on status at expiry (creator-responsibility based):
      - 'open'     → 100% refund (no one claimed; creator not at fault)
      - 'answered' → 80% refund (someone answered but creator didn't accept in time)
    Returns {expired, refunded_total}.
    """
    rows = execute(
        "SELECT * FROM bounties WHERE expires_at < now() AND status IN ('open', 'answered')",
        (),
        fetch=True,
    ) or []
    expired = 0
    refunded_total = 0
    for b in rows:
        rate = EXPIRY_REFUND_RATE.get(b["status"], 0.0)
        refund = round(b["amount"] * rate)
        if refund > 0:
            transfer(
                from_namespace=None,
                to_namespace=b["creator_namespace"],
                amount=refund,
                transaction_type="bounty_refund",
                reference_id=str(b["id"]),
                description=f"Bounty expired ({b['status']}): {int(rate * 100)}% refund",
            )
            refunded_total += refund
        execute("UPDATE bounties SET status = 'expired' WHERE id = %s", (b["id"],))
        expired += 1
    return {"expired": expired, "refunded_total": refunded_total}


# ── Auto-answer (C2-3 simplified) ──────────────────────────────────────────

AUTO_ANSWER_THRESHOLD = 0.0  # RRF scores are tiny (~0.01-0.05 at rrf_k=60); 0 = answer on any match, governance gates quality


def auto_answer_bounty(
    bounty_id: str,
    namespace: str,
    threshold: float = AUTO_ANSWER_THRESHOLD,
) -> Dict[str, Any]:
    """Auto-answer a bounty if the namespace has sufficiently relevant memory.

    Simplified C2-3 (semi-automatic; not a fully autonomous agent):
      1. search the namespace's memories for the bounty question
      2. if best match score >= threshold → claim + answer with that memory's content
    The answer still goes through governance (status=answered, awaits creator accept).
    Returns {auto_answered, reason, ...}.
    """
    bounty = execute_one(
        "SELECT * FROM bounties WHERE id = %s AND status = 'open'", (bounty_id,)
    )
    if not bounty:
        return {"auto_answered": False, "reason": "bounty not found or not open"}

    try:
        from hermes.memory_service import search_memories
        results = search_memories(
            query_text=bounty["question"], limit=3, namespaces=[namespace]
        )
    except Exception as e:
        logger.warning("auto_answer search failed: %s", e)
        return {"auto_answered": False, "reason": f"search failed: {e}"}

    if not results:
        return {"auto_answered": False, "reason": "no relevant memory in namespace"}

    top = results[0]
    score = float(top.get("rrf_score") or top.get("score") or 0)
    if score < threshold:
        return {"auto_answered": False, "reason": f"best score {score:.3f} below threshold {threshold}"}

    claimed = claim_bounty(bounty_id, namespace)
    if not claimed:
        return {"auto_answered": False, "reason": "claim failed (already claimed?)"}

    solution = top.get("summary") or top.get("content") or "(auto-generated from prior memory)"
    ans = answer_bounty(bounty_id, solution, namespace)
    return {
        "auto_answered": True,
        "bounty_id": bounty_id,
        "memory_used": top.get("id"),
        "score": score,
        **(ans or {}),
    }
