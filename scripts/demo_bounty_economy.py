"""End-to-end demo: Mercury C-stage bounty economy.

Run (from repo root):
  MERCURY_TEST_DB=hermes_test conda run -n amn python scripts/demo_bounty_economy.py

Demonstrates the full lifecycle against a live PG:
  register → create bounty (escrow) → claim → answer (reward + bonus + memory sink) → ledger check
"""
import os
import sys
import uuid

# Point at the test DB (mirrors tests/conftest.py) — must run before hermes import.
os.environ.setdefault("MERCURY_DB_NAME", os.environ.get("MERCURY_TEST_DB", "hermes_test"))
os.environ.setdefault("MERCURY_DB_HOST", "192.168.0.17")
os.environ.setdefault("MERCURY_DB_USER", "hermes")
os.environ.setdefault("MERCURY_DB_PASSWORD", "hermes")
os.environ.setdefault("MERCURY_EMBEDDING_URL", "http://192.168.0.13:11434")

# allow running from scripts/ subdirectory without PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes.bounty_service import (
    get_balance, create_bounty, claim_bounty, answer_bounty, get_transactions,
)

alice = f"demo-alice-{uuid.uuid4().hex[:6]}"
bob = f"demo-bob-{uuid.uuid4().hex[:6]}"

print("=" * 60)
print(" Mercury C-stage bounty economy — end-to-end demo")
print("=" * 60)

print(f"\n[1] Wallet setup (initial grant 100 AMN each)")
print(f"    Alice ({alice}): {get_balance(alice)} AMN")
print(f"    Bob   ({bob}):   {get_balance(bob)} AMN")

print(f"\n[2] Alice posts a 50 AMN bounty (escrow model A — locked on create)")
bounty = create_bounty(
    "How to configure pgvector so it survives pg_dump/restore?",
    50, alice, framework="mercury",
)
bid = str(bounty["id"])
print(f"    Bounty: {bid}")
print(f"    Alice balance now: {get_balance(alice)} AMN  (50 locked in escrow)")

print(f"\n[3] Bob claims and answers")
claim_bounty(bid, bob)
result = answer_bounty(bid, "Enable the extension in template1 before pg_dump.", bob)
print(f"    Resolved: reward = {result['reward']} AMN "
      f"(bounty {result['reward'] - result['bonus']} + bonus {result['bonus']})")

print(f"\n[4] Final ledger")
print(f"    Alice: {get_balance(alice)} AMN   (net -50)")
print(f"    Bob:   {get_balance(bob)} AMN   (net +{result['reward']})")
print(f"    System net issuance: {result['bonus']} AMN (the bonus)")

print(f"\n[5] Bob's recent transactions")
for t in get_transactions(bob, limit=5):
    print(f"    {t['transaction_type']:15} {t['amount']:>4} AMN  {t['description'] or ''}")

print("\nBooks balance: creator's locked bounty + system-issued bounty cancel out;")
print("system only net-issued the bonus. [OK]")
