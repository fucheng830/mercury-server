"""C-stage bounty economy integration tests (requires MERCURY_TEST_DB)."""
import uuid

import pytest


def _ns():
    """Unique namespace per test to avoid collisions."""
    return f"test-{uuid.uuid4().hex[:8]}"


def test_wallet_initial_grant(db):
    from hermes.bounty_service import get_or_create_wallet, INITIAL_GRANT
    ns = _ns()
    w = get_or_create_wallet(ns)
    assert w["namespace"] == ns
    assert w["token_balance"] == INITIAL_GRANT


def test_transfer_updates_balance_and_accumulators(db):
    from hermes.bounty_service import get_or_create_wallet, transfer, get_balance
    ns = _ns()
    transfer(None, ns, 50, "memory_reward")
    transfer(ns, None, 20, "bounty_create")
    assert get_balance(ns) == 100 + 50 - 20
    w = get_or_create_wallet(ns)
    assert w["tokens_earned"] == 50
    assert w["tokens_spent"] == 20


def test_transactions_recorded(db):
    from hermes.bounty_service import transfer, get_transactions
    ns = _ns()
    transfer(None, ns, 50, "memory_reward", description="r")
    txns = get_transactions(ns)
    assert len(txns) >= 1
    assert any(t["transaction_type"] == "memory_reward" and t["amount"] == 50 for t in txns)


def test_create_bounty_debits_creator(db):
    from hermes.bounty_service import create_bounty, get_balance, get_transactions
    ns = _ns()
    bounty = create_bounty("How to X?", 50, ns, framework="test")
    assert bounty["amount"] == 50
    assert bounty["status"] == "open"
    assert get_balance(ns) == 50  # 100 - 50
    txns = get_transactions(ns)
    assert any(t["transaction_type"] == "bounty_create" and t["amount"] == 50 for t in txns)


def test_create_bounty_insufficient(db):
    from hermes.bounty_service import create_bounty
    ns = _ns()
    with pytest.raises(ValueError, match="Insufficient balance"):
        create_bounty("too rich", 200, ns)


def test_create_bounty_below_minimum(db):
    from hermes.bounty_service import create_bounty, MIN_BOUNTY
    ns = _ns()
    with pytest.raises(ValueError, match="minimum"):
        create_bounty("tiny", MIN_BOUNTY - 1, ns)


def test_claim_and_answer_reward(db):
    from hermes.bounty_service import (
        create_bounty, claim_bounty, answer_bounty, get_balance,
    )
    creator = _ns()
    solver = _ns()
    bounty = create_bounty("Hard q", 50, creator)
    bid = str(bounty["id"])

    claimed = claim_bounty(bid, solver)
    assert claimed is not None
    assert claimed["status"] == "claimed"

    result = answer_bounty(bid, "the answer", solver)
    assert result is not None
    assert result["reward"] == 60  # 50 + round(50*0.2)=10
    assert result["bonus"] == 10

    assert get_balance(solver) == 100 + 60
    assert get_balance(creator) == 50  # unchanged since create (already debited)


def test_claim_already_claimed_fails(db):
    from hermes.bounty_service import create_bounty, claim_bounty
    creator = _ns()
    solver1 = _ns()
    solver2 = _ns()
    bounty = create_bounty("Q", 50, creator)
    bid = str(bounty["id"])

    assert claim_bounty(bid, solver1) is not None
    assert claim_bounty(bid, solver2) is None  # second claim fails


def test_answer_not_claimer_fails(db):
    from hermes.bounty_service import create_bounty, claim_bounty, answer_bounty
    creator = _ns()
    solver = _ns()
    other = _ns()
    bounty = create_bounty("Q", 50, creator)
    bid = str(bounty["id"])
    claim_bounty(bid, solver)
    # other (not claimer) tries to answer
    assert answer_bounty(bid, "wrong", other) is None


def test_economy_roundtrip_bookkeeping(db):
    """Full lifecycle: creator -bounty, solver +(bounty+bonus), system net issuance = bonus."""
    from hermes.bounty_service import (
        create_bounty, claim_bounty, answer_bounty, get_balance,
    )
    creator = _ns()
    solver = _ns()
    bounty = create_bounty("Roundtrip", 50, creator)
    claim_bounty(str(bounty["id"]), solver)
    answer_bounty(str(bounty["id"]), "ans", solver)

    assert get_balance(creator) == 50     # 100 - 50
    assert get_balance(solver) == 160     # 100 + 60
    # system net issuance = bonus (10): creator's 50 destroyed in escrow, system paid 60
