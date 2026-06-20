#!/usr/bin/env python3
"""Client-side correction extractor.

Reads the FULL raw session JSONL under ~/.claude/projects/ (where Claude Code
runs), finds error->fix episodes (tool_result is_error=true + surrounding
turns), and sends them to mercury-server which extracts durable BUGFIX
memories (pitfall + root cause + fix) and reconciles them.

Why client-side: the error/debug/fix trajectory lives only in the raw JSONL
on the Claude-Code host; mercury-server only receives shallow summaries.
This script reads the deep content locally and ships the relevant windows.

Usage:
  python extract_corrections.py                # scan 10 most-recent sessions
  python extract_corrections.py --recent 50    # scan 50
  python extract_corrections.py --session PATH # one session file
  MERCURY_API=http://host:8788 python extract_corrections.py
"""
import argparse
import glob
import json
import os
import sys
import urllib.request


def flatten(m: dict) -> str:
    role = (m.get("message") or {}).get("role", "?")
    content = (m.get("message") or {}).get("content", "")
    parts = []
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                parts.append(b.get("text", ""))
            elif t == "tool_use":
                parts.append(f"[tool_use {b.get('name')}] {json.dumps(b.get('input', {}), ensure_ascii=False)[:400]}")
            elif t == "tool_result":
                c = b.get("content", "")
                if isinstance(c, list):
                    c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                err = " (ERROR)" if b.get("is_error") else ""
                parts.append(f"[tool_result{err}] {str(c)[:700]}")
    text = " ".join(p for p in parts if p).strip()
    return f"{role}: {text[:1200]}"


def session_episodes(path: str, max_per_session: int = 8):
    msgs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if "message" in d:
                msgs.append(d)
    episodes = []
    for i, m in enumerate(msgs):
        c = (m.get("message") or {}).get("content", [])
        if not isinstance(c, list):
            continue
        is_err = any(isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error") for b in c)
        if not is_err:
            continue
        window = msgs[max(0, i - 3): i + 7]
        text = "\n".join(flatten(x) for x in window)
        cwd = m.get("cwd") or (window[0].get("cwd") if window else "")
        episodes.append((cwd or "", text))
        if len(episodes) >= max_per_session:
            break
    return episodes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", help="single session .jsonl path")
    ap.add_argument("--recent", type=int, default=10, help="scan N most-recent sessions (default 10)")
    ap.add_argument("--api", default=os.environ.get("MERCURY_API", "http://192.168.0.17:8788"))
    args = ap.parse_args()

    if args.session:
        files = [args.session]
    else:
        root = os.path.expanduser("~/.claude/projects")
        files = sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True),
                       key=os.path.getmtime, reverse=True)[: args.recent]
    print(f"scanning {len(files)} session(s)...")

    by_project = {}
    for f in files:
        for cwd, ep in session_episodes(f):
            by_project.setdefault(cwd, []).append(ep)

    total_eps = sum(len(v) for v in by_project.values())
    print(f"found {total_eps} error episodes across {len(by_project)} project(s)")

    grand = {"episodes": 0, "extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}
    for proj, eps in by_project.items():
        if not proj:
            continue
        body = json.dumps({"project_path": proj, "episodes": eps}).encode()
        req = urllib.request.Request(
            f"{args.api}/api/memory/extract-corrections", data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=300).read())
            t = r.get("total", {})
            print(f"  [{proj}] eps={t.get('episodes', 0)} -> extracted={t.get('extracted', 0)} "
                  f"new={t.get('new', 0)} dup={t.get('duplicates', 0)} sup={t.get('superseded', 0)}")
            for k in grand:
                grand[k] += t.get(k, 0)
        except Exception as e:
            print(f"  [{proj}] ERROR: {e}", file=sys.stderr)

    print("TOTAL:", grand)


if __name__ == "__main__":
    main()
