"""Backfill session_messages from local Claude Code JSONL transcripts.

For each session already in the `sessions` table, locate its JSONL via
ClaudeProvider (~/.claude/projects/<proj>/<session_id>.jsonl), extract text
turns, embed in batches, and upsert into session_messages. Idempotent: re-running
is a no-op (UNIQUE(session_id, seq) ON CONFLICT DO NOTHING).

Usage:
    python scripts/backfill_session_messages.py [--limit N] [--project SUBSTR] [--dry-run]
"""
import argparse
import logging
import sys
from pathlib import Path

# Make `hermes` and `providers` importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("backfill")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="max sessions to process (0=all)")
    ap.add_argument("--project", default="", help="filter by project_path substring")
    ap.add_argument("--dry-run", action="store_true", help="extract + count, do not write")
    ap.add_argument("--root", default="", help="override ClaudeProvider root (default ~/.claude)")
    args = ap.parse_args()

    from providers.claude import ClaudeProvider
    from hermes.db import execute
    from hermes.messages_service import ingest_session_messages, extract_text_turns

    provider = ClaudeProvider(root=Path(args.root) if args.root else None)
    if not provider.available():
        log.error("Claude root not found: %s", provider.root)
        sys.exit(1)

    # session_id (varchar, JSONL stem) → on-disk jsonl path
    sid_to_path = {}
    projects_dir = provider.root / "projects"
    if projects_dir.exists():
        for d in projects_dir.iterdir():
            if not d.is_dir():
                continue
            for sf in d.glob("*.jsonl"):
                sid_to_path[sf.stem] = sf

    rows = execute(
        "SELECT id, session_id, project_path, namespace FROM sessions "
        "WHERE session_id IS NOT NULL AND session_id <> '' "
        "ORDER BY last_ts DESC NULLS LAST",
        fetch=True,
    ) or []
    if args.project:
        rows = [r for r in rows if args.project in (r.get("project_path") or "")]

    total = len(rows)
    processed = ingested_sum = skipped_sum = missing = 0
    for r in rows:
        if args.limit and processed >= args.limit:
            break
        sid = r["session_id"]
        path = sid_to_path.get(sid)
        if not path or not path.exists():
            missing += 1
            continue
        msgs = provider._read_jsonl(path)
        if not msgs:
            continue
        if args.dry_run:
            n = len(extract_text_turns(msgs))
            log.info("[dry-run] %s: %d turns", sid, n)
            ingested_sum += n
        else:
            res = ingest_session_messages(str(r["id"]), r.get("namespace") or "claude", msgs)
            ingested_sum += res["ingested"]
            skipped_sum += res["skipped"]
        processed += 1
        if processed % 25 == 0:
            log.info("progress %d/%d (ingested=%d skipped=%d missing=%d)",
                     processed, total, ingested_sum, skipped_sum, missing)

    log.info("done: processed=%d/%d ingested=%d skipped=%d missing=%d",
             processed, total, ingested_sum, skipped_sum, missing)


if __name__ == "__main__":
    main()
