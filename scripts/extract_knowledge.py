#!/usr/bin/env python3
"""Client-side durable-knowledge extractor (full content).

Reads FULL raw session JSONL under ~/.claude/projects/ (where Claude Code runs),
summarizes each session's real content (user prompts + assistant text + tools),
groups by project, batches, and POSTs to mercury-server which extracts the 5
durable categories (DECISION/ARCH/BUGFIX/PREFERENCE/DISCOVERY) and reconciles
them against the project's existing active memories.

Why client-side: the server's sessions table only stores shallow previews
(~300 chars); the durable knowledge (decisions, architecture, root-caused
fixes) lives only in the full local JSONL. This script ships that deep content.

Usage:
  python extract_knowledge.py --month 2026-06            # all June sessions
  python extract_knowledge.py --month 2026-06 --project mercury   # one project (substring)
  python extract_knowledge.py --month 2026-06 --dry-run  # count only, no POST
  MERCURY_API=http://host:8788 python extract_knowledge.py
"""
import argparse
import calendar
import glob
import json
import os
import sys
import time
import urllib.request
from datetime import datetime


def summarize(messages):
    """Extract {user_prompts, assistant_summaries, tools_used} from raw JSONL messages."""
    user_prompts = []
    assistant_summaries = []
    tools_used = {}

    for msg in messages:
        mtype = msg.get("type")
        mdata = msg.get("message") or {}
        content = mdata.get("content", "")

        if mtype == "user":
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [c.get("text", "") for c in content
                         if isinstance(c, dict) and c.get("type") == "text"]
                text = " ".join(parts)
            t = text.strip()
            if t and len(user_prompts) < 40:
                user_prompts.append(t[:300])

        elif mtype == "assistant":
            blocks = content if isinstance(content, list) else []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    name = b.get("name", "")
                    if name:
                        tools_used[name] = tools_used.get(name, 0) + 1
            text_parts = [b.get("text", "") for b in blocks
                          if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
            s = " ".join(text_parts).strip()
            if s and len(assistant_summaries) < 30:
                assistant_summaries.append(s[:300])

    content_len = sum(len(p) for p in user_prompts) + sum(len(a) for a in assistant_summaries)
    return {
        "user_prompts": user_prompts,
        "assistant_summaries": assistant_summaries,
        "tools_used": tools_used,
        "content_len": content_len,
    }


def load_sessions(files, min_chars):
    """Read JSONL files -> list of (cwd, summary_dict)."""
    out = []
    for path in files:
        msgs = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if "message" in d:
                        msgs.append(d)
        except Exception:
            continue
        if not msgs:
            continue
        cwd = ""
        for m in msgs:
            cwd = m.get("cwd") or ""
            if cwd:
                break
        summ = summarize(msgs)
        if summ["content_len"] < min_chars:
            continue
        out.append((cwd, summ))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="target month YYYY-MM (default: current)")
    ap.add_argument("--project", help="only sessions whose cwd contains this substring")
    ap.add_argument("--batch-size", type=int, default=12, help="sessions per LLM call (default 12)")
    ap.add_argument("--max-per-project", type=int, default=40, help="cap sessions per project, longest first (default 40)")
    ap.add_argument("--min-chars", type=int, default=150, help="skip sessions with less combined content (default 150)")
    ap.add_argument("--dry-run", action="store_true", help="count and show plan, do not POST")
    ap.add_argument("--api", default=os.environ.get("MERCURY_API", "http://192.168.0.17:8788"))
    args = ap.parse_args()

    now = datetime.now()
    if args.month:
        y, mo = map(int, args.month.split("-"))
    else:
        y, mo = now.year, now.month
    month_start = datetime(y, mo, 1)
    last_day = calendar.monthrange(y, mo)[1]
    month_end = datetime(y, mo, last_day, 23, 59, 59)

    root = os.path.expanduser("~/.claude/projects")
    all_files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    files = [f for f in all_files if month_start.timestamp() <= os.path.getmtime(f) <= month_end.timestamp()]
    print(f"month {y}-{mo:02d}: {len(files)} session files in range (of {len(all_files)} total)")

    sessions = load_sessions(files, args.min_chars)
    if args.project:
        sessions = [(c, s) for c, s in sessions if args.project.lower() in (c or "").lower()]
    print(f"substantive sessions (>= {args.min_chars} chars): {len(sessions)}")

    by_project = {}
    for cwd, summ in sessions:
        by_project.setdefault(cwd or "(no-cwd)", []).append(summ)

    total_batches = 0
    for proj, sums in by_project.items():
        sums.sort(key=lambda s: s["content_len"], reverse=True)
        sums = sums[: args.max_per_project]
        by_project[proj] = sums
        total_batches += (len(sums) + args.batch_size - 1) // args.batch_size
    print(f"projects: {len(by_project)} | sessions after cap: {sum(len(v) for v in by_project.values())} | batches: {total_batches}")

    if args.dry_run:
        for proj, sums in sorted(by_project.items(), key=lambda kv: -len(kv[1])):
            print(f"  [{len(sums):3d}] {proj}")
        return

    grand = {"extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}
    done = 0
    for proj, sums in sorted(by_project.items(), key=lambda kv: -len(kv[1])):
        proj_counts = {"extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}
        for i in range(0, len(sums), args.batch_size):
            batch = sums[i: i + args.batch_size]
            payload = [{
                "user_prompts": s["user_prompts"],
                "assistant_summaries": s["assistant_summaries"],
                "tools_used": s["tools_used"],
                "project": proj,
            } for s in batch]
            body = json.dumps({"project_path": proj, "sessions": payload}).encode()
            req = urllib.request.Request(
                f"{args.api}/api/memory/extract-sessions", data=body,
                headers={"Content-Type": "application/json"},
            )
            try:
                t = json.loads(urllib.request.urlopen(req, timeout=300).read()).get("total", {})
                for k in proj_counts:
                    proj_counts[k] += t.get(k, 0)
            except Exception as e:
                print(f"  [{proj}] batch ERROR: {e}", file=sys.stderr)
            done += 1
            time.sleep(0.3)
        print(f"  [{len(sums):3d}] {proj} -> extracted={proj_counts['extracted']} "
              f"new={proj_counts['new']} dup={proj_counts['duplicates']} sup={proj_counts['superseded']}")
        for k in grand:
            grand[k] += proj_counts[k]

    print(f"DONE batches={done} | TOTAL extracted={grand['extracted']} "
          f"new={grand['new']} dup={grand['duplicates']} sup={grand['superseded']}")


if __name__ == "__main__":
    main()
