"""Extract durable memories from QuantDinger-Vue sessions via local ollama LLM.

Reads ~/.claude/projects/*QuantDinger-Vue*/*.jsonl, builds session summaries
(user_prompts / assistant_summaries / tools_used), and runs extract_from_sessions
in batches (each batch = one LLM call over the concatenated session text).
Idempotent via reconcile (duplicate / supersede / new).

Usage:
    python scripts/extract_qd_memories.py [--batch 10] [--batch-limit 0]
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("extract-qd")

PROJECT_PATH = r"D:\workspace\projects\quant\QuantDinger-Vue"
OLLAMA = "http://192.168.0.13:11434/v1"
MODEL = "qwen3:8b"
JSONL_DIRS = [
    "D--workspace-projects-quant-QuantDinger-Vue",
    "D--workspace-projects-quant-QuantDinger-Vue-backend-scripts",
]


def session_from_jsonl(provider, path):
    msgs = provider._read_jsonl(path)
    user_prompts, assistant_summaries, tools_used = [], [], {}
    for msg in msgs:
        t = msg.get("type")
        content = msg.get("message", {}).get("content", "")
        if t == "user":
            if isinstance(content, str):
                if content.strip():
                    user_prompts.append(content[:300])
            elif isinstance(content, list):
                if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
                    continue  # tool_result wrapper — skip
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").strip():
                        user_prompts.append(c["text"][:300])
        elif t == "assistant" and isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text", "").strip():
                    assistant_summaries.append(c["text"][:400])
                elif c.get("type") == "tool_use":
                    name = c.get("name", "")
                    tools_used[name] = tools_used.get(name, 0) + 1
    if not user_prompts and not assistant_summaries:
        return None
    return {
        "project": PROJECT_PATH,
        "user_prompts": user_prompts[:20],
        "assistant_summaries": assistant_summaries[:20],
        "tools_used": tools_used,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=int, default=10)
    ap.add_argument("--batch-limit", type=int, default=0, help="max batches (0=all)")
    args = ap.parse_args()

    from recap.llm_service import LLMService
    from hermes.extractor import extract_from_sessions
    from providers.claude import ClaudeProvider

    llm = LLMService({"default": "ollama", "providers": {"ollama": {
        "api_base": OLLAMA, "api_key": "ollama", "model": MODEL, "max_tokens": 8192}}})
    provider = ClaudeProvider()

    jsonls = []
    for d in JSONL_DIRS:
        p = Path.home() / ".claude" / "projects" / d
        if p.exists():
            jsonls.extend(sorted(p.glob("*.jsonl")))
    log.info("found %d jsonl files", len(jsonls))

    sessions = []
    for path in jsonls:
        s = session_from_jsonl(provider, path)
        if s:
            sessions.append(s)
    log.info("constructed %d session summaries", len(sessions))
    if not sessions:
        return

    total = {"extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}
    nbatches = (len(sessions) + args.batch - 1) // args.batch
    done = 0
    for i in range(0, len(sessions), args.batch):
        if args.batch_limit and done >= args.batch_limit:
            break
        batch = sessions[i:i + args.batch]
        try:
            r = extract_from_sessions(batch, PROJECT_PATH, llm)
        except Exception as e:
            log.error("batch %d failed: %s", done + 1, e)
            r = {"extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}
        for k in total:
            total[k] += r.get(k, 0)
        done += 1
        log.info("batch %d/%d: %s | cumulative=%s", done, nbatches, r, total)
    log.info("DONE: %d batches, %s", done, total)


if __name__ == "__main__":
    main()
