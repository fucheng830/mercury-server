"""C2-4 memory access pricing (opt-in).

Owner tags a memory with price >= 0 (0 = free). Querier pays the price on a
priced-search hit; owner receives it (100% share, no platform cut in MVP).
Existing search_memories stays free/backward-compatible; pricing is a new layer.
"""
import logging
from typing import Any, Dict, List, Optional

from hermes.db import execute
from hermes.bounty_service import get_balance, transfer

logger = logging.getLogger(__name__)


def set_memory_price(memory_id: str, owner_namespace: str, price: int) -> Optional[Dict[str, Any]]:
    """Owner sets a price on their own memory (0 = free). Returns the updated row or None."""
    if price < 0:
        raise ValueError("price must be >= 0")
    rows = execute(
        "UPDATE memories SET price = %s WHERE id = %s AND namespace = %s "
        "RETURNING id, namespace, price",
        (price, memory_id, owner_namespace),
        fetch=True,
    )
    if not rows:
        return None
    r = rows[0]
    return {"id": str(r["id"]), "namespace": r["namespace"], "price": r["price"]}


def priced_search(
    query: str,
    caller_namespace: str,
    limit: int = 20,
    namespaces: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Search memories; charge caller for priced hits (owner receives).

    - Free memories (price=0): always returned.
    - Caller's own memories: always free.
    - Others' priced memories: returned only if caller can afford; fee transferred to owner.
    """
    from hermes.memory_service import search_memories

    if namespaces is None:
        # market semantics: search across all namespaces (caller looking for others' priced memory)
        ns_rows = execute(
            "SELECT DISTINCT namespace FROM memories WHERE status = 'active'",
            (),
            fetch=True,
        ) or []
        namespaces = [r["namespace"] for r in ns_rows] or None

    results = search_memories(
        query_text=query, limit=limit, namespaces=namespaces
    )
    if not results:
        return []

    ids = [r.get("id") for r in results if r.get("id")]
    if not ids:
        return results

    placeholders = ",".join(["%s"] * len(ids))
    price_rows = execute(
        f"SELECT id, namespace, price FROM memories WHERE id IN ({placeholders})",
        tuple(str(i) for i in ids),
        fetch=True,
    ) or []
    price_map = {str(r["id"]): (r["namespace"], r["price"] or 0) for r in price_rows}

    caller_balance = get_balance(caller_namespace)
    out: List[Dict[str, Any]] = []
    for r in results:
        mid = str(r.get("id"))
        owner, price = price_map.get(mid, (None, 0))
        if not price or price <= 0 or owner == caller_namespace:
            out.append({**r, "price": price or 0, "charged": 0})
            continue
        # priced memory from another namespace
        if caller_balance < price:
            continue  # skip — caller can't afford
        transfer(
            from_namespace=caller_namespace,
            to_namespace=owner,
            amount=price,
            transaction_type="memory_access",
            reference_id=mid,
            description=f"Memory access: {query[:50]}",
        )
        caller_balance -= price
        out.append({**r, "price": price, "charged": price})
    return out
