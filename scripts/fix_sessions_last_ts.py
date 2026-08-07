"""Backfill sessions.last_ts / first_ts from local JSONL (NULL rows only).

For sessions where last_ts is NULL and the JSONL still exists locally, read
the first/last record timestamps and UPDATE. Rows without JSONL (historical,
deleted transcripts) can't be filled and stay NULL.

    python scripts/fix_sessions_last_ts.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes.db import execute

jsonl_map = {}
for d in Path.home().glob(".claude/projects/*"):
    if d.is_dir():
        for f in d.glob("*.jsonl"):
            jsonl_map[f.stem] = f

nulls = execute(
    "SELECT id, session_id FROM sessions WHERE last_ts IS NULL AND session_id IS NOT NULL",
    fetch=True,
) or []
print(f"NULL last_ts rows: {len(nulls)}; local JSONL available: {len(jsonl_map)}")

updated = 0
skipped = 0
for r in nulls:
    f = jsonl_map.get(r["session_id"])
    if not f or not f.exists():
        skipped += 1
        continue
    first_ts = last_ts = None
    try:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = json.loads(line).get("timestamp")
                except Exception:
                    continue
                if ts:
                    if not first_ts:
                        first_ts = ts
                    last_ts = ts
    except Exception:
        continue
    if last_ts:
        execute(
            "UPDATE sessions SET last_ts=%s, first_ts=COALESCE(first_ts,%s) WHERE id=%s",
            (last_ts, first_ts, r["id"]),
        )
        updated += 1

print(f"updated {updated} rows; skipped {skipped} (no JSONL, historical)")
