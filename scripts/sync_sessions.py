#!/usr/bin/env python3
"""Client-side session sync: upload Claude Code + Codex conversation records to mercury-server.

Reads local session JSONL, builds per-session summaries, and POSTs to
/api/ingest/sessions. Upsert-on-conflict, so re-runs are safe. Populates the
sessions table the dashboard / calendar / server-side recap read from.

Sources:
  claude : ~/.claude/projects/**/*.jsonl   (messages with type=user/assistant, cwd, usage)
  codex  : ~/.codex/**/rollout-*.jsonl       (session_meta + response_item messages)

Usage:
  python sync_sessions.py --source all --since 2026-06-10   # fill the gap
  python sync_sessions.py --source claude
  python sync_sessions.py --source codex --client-name archie-laptop
  MERCURY_API=http://host:8788 python sync_sessions.py
"""
import argparse
import glob
import json
import os
import sys
import urllib.request
from datetime import datetime


def _encode_pid(path: str) -> str:
    s = (path or "").strip().replace("\\", "/")
    out = "".join(c if c.isalnum() or c in "-_." else "-" for c in s)
    return out.strip("-").lower() or "unknown"


def _ts(v):
    if not v:
        return None
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(v / 1000 if v > 1e12 else v).isoformat()
        except Exception:
            return None
    return v if isinstance(v, str) else None


def _mtime_iso(path):
    return datetime.fromtimestamp(os.path.getmtime(path)).isoformat()


def parse_claude(path):
    msgs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if "message" in d or d.get("type") in ("user", "assistant"):
                msgs.append(d)
    if not msgs:
        return None
    cwd = preview = ""
    msg_count = tin = tout = 0
    times = []
    for m in msgs:
        ts = _ts(m.get("timestamp"))
        if ts:
            times.append(ts)
        c = m.get("cwd")
        if c and not cwd:
            cwd = c
        mtype = m.get("type")
        mdata = m.get("message") or {}
        if mtype == "user":
            content = mdata.get("content", "")
            text = content if isinstance(content, str) else " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text")
            text = text.strip()
            if text and not preview and not text.startswith(("<", "[")):
                preview = text[:300]
            msg_count += 1
        elif mtype == "assistant":
            msg_count += 1
            u = mdata.get("usage", {})
            tin += (u.get("input_tokens") or 0)
            tout += (u.get("output_tokens") or 0)
    times.sort()
    return {
        "session_id": os.path.splitext(os.path.basename(path))[0],
        "project_id": _encode_pid(cwd or os.path.basename(os.path.dirname(path))),
        "project_path": cwd or None,
        "preview": preview,
        "message_count": msg_count,
        "token_input": tin, "token_output": tout,
        "first_ts": times[0] if times else None,
        "last_ts": times[-1] if times else None,
        "file_mtime": _mtime_iso(path),
        "namespace": "claude",
    }


def parse_codex(path):
    cwd = ""
    sid = os.path.splitext(os.path.basename(path))[0]
    preview = ""
    msg_count = 0
    times = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = _ts(d.get("timestamp"))
            if ts:
                times.append(ts)
            typ = d.get("type")
            p = d.get("payload") or {}
            if typ == "session_meta":
                cwd = p.get("cwd") or cwd
                sid = p.get("id") or sid
            elif typ == "response_item" and p.get("type") == "message":
                role = p.get("role")
                content = p.get("content") or []
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict)).strip()
                if role == "user" and text and not preview and not text.startswith(("#", "<")):
                    preview = text[:300]
                msg_count += 1
    if not msg_count:
        return None
    times.sort()
    return {
        "session_id": sid,
        "project_id": _encode_pid(cwd) if cwd else "codex-" + sid[:8],
        "project_path": cwd or None,
        "preview": preview,
        "message_count": msg_count,
        "token_input": 0, "token_output": 0,
        "first_ts": times[0] if times else None,
        "last_ts": times[-1] if times else None,
        "file_mtime": _mtime_iso(path),
        "namespace": "codex",
    }


SOURCES = {
    "claude": (os.path.expanduser("~/.claude/projects"), "**/*.jsonl", parse_claude),
    "codex": (os.path.expanduser("~/.codex"), "**/rollout-*.jsonl", parse_codex),
}


def _post(api, client_id, sessions):
    body = json.dumps({"client_id": client_id, "sessions": sessions, "sync_log": []}).encode()
    req = urllib.request.Request(f"{api}/api/ingest/sessions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=120).read()).get("accepted", 0)
    except Exception as e:
        print(f"  POST error: {e}", file=sys.stderr)
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["claude", "codex", "all"], default="all")
    ap.add_argument("--since", help="only files modified since YYYY-MM-DD")
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--client-name", default="archie-desktop-sync")
    ap.add_argument("--api", default=os.environ.get("MERCURY_API", "http://192.168.0.17:8788"))
    args = ap.parse_args()

    reg = json.dumps({"name": args.client_name, "hostname": os.environ.get("COMPUTERNAME", ""),
                      "os_info": "windows"}).encode()
    client_id = json.loads(urllib.request.urlopen(urllib.request.Request(
        f"{args.api}/api/ingest/register", data=reg,
        headers={"Content-Type": "application/json"}), timeout=15).read())["client_id"]
    print(f"client_id: {client_id}")

    since = datetime.fromisoformat(args.since).timestamp() if args.since else 0
    for src in (["claude", "codex"] if args.source == "all" else [args.source]):
        root, pat, parser = SOURCES[src]
        files = [f for f in glob.glob(os.path.join(root, pat), recursive=True)
                 if os.path.getmtime(f) >= since]
        batch, total = [], 0
        for f in files:
            try:
                s = parser(f)
            except Exception:
                continue
            if s:
                batch.append(s)
                if len(batch) >= args.batch:
                    total += _post(args.api, client_id, batch)
                    batch = []
        if batch:
            total += _post(args.api, client_id, batch)
        print(f"[{src}] files={len(files)} uploaded={total}")


if __name__ == "__main__":
    main()
