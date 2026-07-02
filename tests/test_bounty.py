"""Bounty economy integration tests (C1 core + C2-1 governance). Requires MERCURY_TEST_DB."""
import uuid

import pytest


def _ns():
    return f"test-{uuid.uuid4().hex[:8]}"


# ── C1: wallet / ledger / create ──────────────────────────────────────────

def test_wallet_initial_grant(db):
    from hermes.bounty_service import get_or_create_wallet, INITIAL_GRANT
    ns = _ns()
    w = get_or_create_wallet(ns)
    assert w["token_balance"] == INITIAL_GRANT


def test_transfer_updates_balance_and_accumulators(db):
    from hermes.bounty_service import transfer, get_balance, get_or_create_wallet
    ns = _ns()
    transfer(None, ns, 50, "memory_reward")
    transfer(ns, None, 20, "bounty_create")
    assert get_balance(ns) == 100 + 50 - 20
    w = get_or_create_wallet(ns)
    assert w["tokens_earned"] == 50
    assert w["tokens_spent"] == 20


def test_create_bounty_debits_creator(db):
    from hermes.bounty_service import create_bounty, get_balance, get_transactions
    ns = _ns()
    bounty = create_bounty("How to X?", 50, ns, framework="test")
    assert bounty["status"] == "open"
    assert get_balance(ns) == 50
    txns = get_transactions(ns)
    assert any(t["transaction_type"] == "bounty_create" for t in txns)


def test_create_bounty_insufficient(db):
    from hermes.bounty_service import create_bounty
    with pytest.raises(ValueError, match="Insufficient balance"):
        create_bounty("too rich", 200, _ns())


def test_create_bounty_below_minimum(db):
    from hermes.bounty_service import create_bounty, MIN_BOUNTY
    with pytest.raises(ValueError, match="minimum"):
        create_bounty("tiny", MIN_BOUNTY - 1, _ns())


def test_claim_already_claimed_fails(db):
    from hermes.bounty_service import create_bounty, claim_bounty
    creator, s1, s2 = _ns(), _ns(), _ns()
    bounty = create_bounty("Q", 50, creator)
    bid = str(bounty["id"])
    assert claim_bounty(bid, s1) is not None
    assert claim_bounty(bid, s2) is None


# ── C2-1: governance (answer→pending, accept/reject) ──────────────────────

def test_answer_enters_pending_no_reward(db):
    """C2-1: answer → status=answered, NO reward yet (pending acceptance)."""
    from hermes.bounty_service import (
        create_bounty, claim_bounty, answer_bounty, get_balance,
    )
    creator, solver = _ns(), _ns()
    bounty = create_bounty("Q", 50, creator)
    bid = str(bounty["id"])
    claim_bounty(bid, solver)
    result = answer_bounty(bid, "the answer", solver)
    assert result["status"] == "answered"
    assert get_balance(solver) == 100   # NOT rewarded yet
    assert get_balance(creator) == 50   # escrow still locked


def test_accept_rewards_solver(db):
    from hermes.bounty_service import (
        create_bounty, claim_bounty, answer_bounty, accept_bounty, get_balance,
    )
    creator, solver, other = _ns(), _ns(), _ns()
    bounty = create_bounty("Q", 50, creator)
    bid = str(bounty["id"])
    claim_bounty(bid, solver)
    answer_bounty(bid, "ans", solver)

    assert accept_bounty(bid, other) is None              # non-creator cannot accept
    result = accept_bounty(bid, creator)
    assert result["reward"] == 60                          # 50 + 10
    assert get_balance(solver) == 160
    assert get_balance(creator) == 50


def test_reject_reopens_no_reward(db):
    from hermes.bounty_service import (
        create_bounty, claim_bounty, answer_bounty, reject_bounty, get_balance, list_bounties,
    )
    creator, solver = _ns(), _ns()
    bounty = create_bounty("Q", 50, creator)
    bid = str(bounty["id"])
    claim_bounty(bid, solver)
    answer_bounty(bid, "bad ans", solver)

    result = reject_bounty(bid, creator)
    assert result["reopened"] is True
    assert get_balance(solver) == 100                      # NOT rewarded
    opens = list_bounties(status="open")
    assert any(b["id"] == bid for b in opens)              # back to open


def test_reject_only_creator(db):
    from hermes.bounty_service import (
        create_bounty, claim_bounty, answer_bounty, reject_bounty,
    )
    creator, solver, other = _ns(), _ns(), _ns()
    bounty = create_bounty("Q", 50, creator)
    bid = str(bounty["id"])
    claim_bounty(bid, solver)
    answer_bounty(bid, "ans", solver)
    assert reject_bounty(bid, other) is None               # non-creator cannot reject


def test_economy_roundtrip_with_accept(db):
    """Full lifecycle with governance: create → claim → answer(pending) → accept(reward)."""
    from hermes.bounty_service import (
        create_bounty, claim_bounty, answer_bounty, accept_bounty, get_balance,
    )
    creator, solver = _ns(), _ns()
    bounty = create_bounty("Roundtrip", 50, creator)
    bid = str(bounty["id"])
    claim_bounty(bid, solver)
    answer_bounty(bid, "ans", solver)
    assert get_balance(solver) == 100                      # pending: no reward
    accept_bounty(bid, creator)
    assert get_balance(creator) == 50
    assert get_balance(solver) == 160                      # 100 + 60


def test_answer_not_claimer_fails(db):
    from hermes.bounty_service import create_bounty, claim_bounty, answer_bounty
    creator, solver, other = _ns(), _ns(), _ns()
    bounty = create_bounty("Q", 50, creator)
    bid = str(bounty["id"])
    claim_bounty(bid, solver)
    assert answer_bounty(bid, "wrong", other) is None


# ── C2-2: expiry / refund ─────────────────────────────────────────────────

def test_expire_open_full_refund(db):
    """open bounty (no claim) expired → 100% refund; creator not at fault."""
    from hermes.bounty_service import create_bounty, expire_bounties, get_balance
    from hermes.db import execute
    creator = _ns()
    bounty = create_bounty("Q", 50, creator)
    execute("UPDATE bounties SET expires_at = now() - interval '1 hour' WHERE id = %s",
            (str(bounty["id"]),))
    result = expire_bounties()
    assert result["expired"] >= 1
    assert get_balance(creator) == 100  # full refund (50 escrowed + 50 back)


def test_expire_answered_80_refund(db):
    """answered bounty (no accept) expired → 80% refund; creator didn't judge in time."""
    from hermes.bounty_service import (
        create_bounty, claim_bounty, answer_bounty, expire_bounties, get_balance,
    )
    from hermes.db import execute
    creator, solver = _ns(), _ns()
    bounty = create_bounty("Q", 50, creator)
    bid = str(bounty["id"])
    claim_bounty(bid, solver)
    answer_bounty(bid, "ans", solver)
    execute("UPDATE bounties SET expires_at = now() - interval '1 hour' WHERE id = %s", (bid,))
    expire_bounties()
    assert get_balance(creator) == 90   # 100 - 50 + 40 (80% of 50)
    assert get_balance(solver) == 100   # solver never rewarded (no accept)


def test_expire_skips_non_overdue(db):
    from hermes.bounty_service import create_bounty, expire_bounties
    creator = _ns()
    bounty = create_bounty("Q", 50, creator)  # expires in 24h
    result = expire_bounties()
    # this bounty must NOT be expired
    from hermes.db import execute_one
    row = execute_one("SELECT status FROM bounties WHERE id = %s", (str(bounty["id"]),))
    assert row["status"] == "open"

