"""End-to-end smoke check for the backfill pipeline against real local JSONL.

Picks a real Claude Code transcript under ~/.claude/projects, runs the full
extract → embed → ingest pipeline into hermes_test, re-runs for idempotency,
prints row count, then cleans up.

    python scripts/verify_backfill.py
"""
import os
for k, v in {
    "MERCURY_DB_NAME": "hermes_test",
    "MERCURY_DB_HOST": "192.168.0.17",
    "MERCURY_DB_USER": "hermes",
    "MERCURY_DB_PASSWORD": "hermes",
    "MERCURY_EMBEDDING_URL": "http://192.168.0.13:11434",
}.items():
    os.environ.setdefault(k, v)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.claude import ClaudeProvider
from hermes.db import execute
from hermes.messages_service import extract_text_turns, ingest_session_messages

provider = ClaudeProvider()
target = None
for f in (provider.root / "projects").rglob("*.jsonl"):
    msgs = provider._read_jsonl(f, limit=20)
    if len(msgs) >= 6:
        target = f
        break
assert target, "no JSONL with >=6 messages found under ~/.claude/projects"
msgs = provider._read_jsonl(target)
turns = extract_text_turns(msgs)
print(f"{target.name}: {len(msgs)} raw messages → {len(turns)} text turns")
for t in turns[:3]:
    print(f"  seq={t['seq']} role={t['role']} text={t['content_text'][:70]!r}")

# Seed a session row (CASCADE-safe cleanup via client delete)
execute("DELETE FROM clients WHERE name = 'verify-backfill'")
execute("INSERT INTO clients (name) VALUES ('verify-backfill')")
cid = execute("SELECT id FROM clients WHERE name = 'verify-backfill'", fetch=True)[0]["id"]
sid = execute(
    "INSERT INTO sessions (client_id, project_id, session_id, project_path, "
    "preview, message_count, namespace) "
    "VALUES (%s, 'verify-proj', %s, '/verify', 'preview', %s, 'claude') RETURNING id",
    (str(cid), target.stem, len(turns)), fetch=True,
)[0]["id"]
sid = str(sid)

first = ingest_session_messages(sid, "claude", msgs)
second = ingest_session_messages(sid, "claude", msgs)  # idempotent re-run
n = execute(
    "SELECT count(*) AS n FROM session_messages WHERE session_id = %s::uuid",
    (sid,), fetch=True,
)[0]["n"]
print(f"ingest first={first} second={second} rows={n}")
sample = execute(
    "SELECT seq, role, content_text FROM session_messages WHERE session_id = %s::uuid "
    "ORDER BY seq LIMIT 3",
    (sid,), fetch=True,
)
for r in (sample or []):
    print(f"  row seq={r['seq']} role={r['role']} text={r['content_text'][:70]!r}")

assert first["ingested"] == len(turns), f"first run should ingest all {len(turns)} turns"
assert second["ingested"] == 0, "second run must be a no-op (idempotency)"
assert n == len(turns), f"row count {n} != turn count {len(turns)}"
print(f"\nOK: real-JSONL backfill pipeline works ({n} rows), idempotent on re-run")

execute("DELETE FROM clients WHERE name = 'verify-backfill'")
print("cleaned up")
