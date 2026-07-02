"""C2-4 memory pricing integration tests. Requires MERCURY_TEST_DB + embedding service."""
import uuid

import pytest


def _ns():
    return f"test-{uuid.uuid4().hex[:8]}"


def _write(content, namespace):
    from hermes.memory_service import write_memory
    return write_memory(
        content=content, stage="memory", source="test", importance=3,
        type="DISCOVERY", scope="global", namespace=namespace, auto_embed=True,
    )


def test_set_memory_price_owner_only(db, embedding_ok):
    from hermes.pricing_service import set_memory_price
    owner, other = _ns(), _ns()
    mem = _write("priced knowledge", owner)
    mid = str(mem["id"])
    # non-owner cannot set price
    assert set_memory_price(mid, other, 5) is None
    # owner can
    result = set_memory_price(mid, owner, 5)
    assert result["price"] == 5


def test_priced_search_free_memory_not_charged(db, embedding_ok):
    from hermes.pricing_service import priced_search
    from hermes.bounty_service import get_balance
    owner, caller = _ns(), _ns()
    _write("free knowledge about pgvector", owner)  # price defaults to 0
    balance_before = get_balance(caller)
    results = priced_search("pgvector", caller, limit=5)
    # caller not charged for free memory
    assert get_balance(caller) == balance_before


def test_priced_search_charges_and_pays_owner(db, embedding_ok):
    from hermes.pricing_service import set_memory_price, priced_search
    from hermes.bounty_service import get_balance
    owner, caller = _ns(), _ns()
    mem = _write("premium pgvector tuning guide", owner)
    mid = str(mem["id"])
    set_memory_price(mid, owner, 5)

    owner_before = get_balance(owner)
    caller_before = get_balance(caller)

    results = priced_search("pgvector tuning", caller, limit=5)
    charged = [r for r in results if r.get("charged")]
    assert len(charged) >= 1
    assert get_balance(caller) == caller_before - 5
    assert get_balance(owner) == owner_before + 5


def test_priced_search_insufficient_skips(db, embedding_ok):
    from hermes.pricing_service import set_memory_price, priced_search
    from hermes.bounty_service import get_balance, transfer
    owner, caller = _ns(), _ns()
    mem = _write("very expensive knowledge", owner)
    set_memory_price(str(mem["id"]), owner, 200)  # more than default 100
    caller_before = get_balance(caller)
    results = priced_search("expensive", caller, limit=5)
    # caller balance unchanged (priced memory skipped, not charged)
    assert get_balance(caller) == caller_before
    # the priced memory is NOT in results (filtered out)
    assert not any(r.get("charged") for r in results)


def test_owner_queries_own_priced_memory_free(db, embedding_ok):
    from hermes.pricing_service import set_memory_price, priced_search
    from hermes.bounty_service import get_balance
    owner = _ns()
    mem = _write("my own priced knowledge", owner)
    set_memory_price(str(mem["id"]), owner, 5)
    before = get_balance(owner)
    results = priced_search("own priced", owner, limit=5)
    # owner queries own memory — free
    assert get_balance(owner) == before
