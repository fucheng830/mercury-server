"""Global dedupe of active memories via LLM.

Finds semantic duplicate groups among active memories and supersedes the
losers (keeps the highest-importance / most specific in each group). Same
supersede channel as the extractor (counterexample audit trail).

LLM provider: nvidia if MERCURY_LLM_API_KEY is set (faster, avoids ollama
contention with the embedding backfill); falls back to local ollama qwen3:8b.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recap.llm_service import LLMService
from hermes.db import execute
from hermes.memory_service import supersede_memory

if os.environ.get("MERCURY_LLM_API_KEY"):
    llm = LLMService({"default": "nvidia", "providers": {"nvidia": {
        "api_base": os.environ.get("MERCURY_LLM_API_BASE", "http://192.168.0.17:3000/v1"),
        "api_key": os.environ["MERCURY_LLM_API_KEY"],
        "model": "auto", "max_tokens": 6144}}})
else:
    llm = LLMService({"default": "ollama", "providers": {"ollama": {
        "api_base": "http://192.168.0.13:11434/v1", "api_key": "ollama",
        "model": "qwen3:8b", "max_tokens": 6144}}})

rows = execute(
    """SELECT id, type, importance, content FROM memories
       WHERE status = 'active' AND stage IN ('memory', 'candidate')
       ORDER BY type, importance DESC, created_at""",
    fetch=True,
) or []
print(f"active memories: {len(rows)}")

BATCH = 40
archived = 0
for start in range(0, len(rows), BATCH):
    batch = rows[start:start + BATCH]
    text = "\n".join(f"[{i}] {r['type']}*{r['importance']}: {r['content']}" for i, r in enumerate(batch))
    prompt = (
        "找出语义重复/同义的组（同主题同结论 = duplicate）。"
        "只列真正重复的组（>=2 条）。每组第一个保留，其余被它取代。\n"
        "严格 JSON：{\"groups\":[[索引,索引,...], ...]}，索引是 [i] 的数字。\n\n" + text
    )
    r = None
    for attempt in range(3):
        try:
            r = llm.generate_json("你是记忆去重器，只返回 JSON。", prompt, max_tokens=2048)
            break
        except Exception as e:
            if attempt == 2:
                print(f"batch {start} failed 3x: {e}, skip")
            else:
                time.sleep(2)
    if not r:
        continue
    groups = r.get("groups", []) if isinstance(r, dict) else []
    for grp in groups:
        if not isinstance(grp, list) or len(grp) < 2:
            continue
        idxs = [i for i in grp if isinstance(i, int) and 0 <= i < len(batch)]
        if len(idxs) < 2:
            continue
        keep_id = batch[idxs[0]]["id"]  # batch 已按 importance DESC
        for idx in idxs[1:]:
            try:
                supersede_memory(batch[idx]["id"], superseded_by=keep_id, namespace="claude")
                archived += 1
            except Exception as e:
                print(f"  archive fail: {e}")
    print(f"batch {start}-{start + len(batch) - 1}: {len(groups)} groups, cumulative archived={archived}")

print(f"DONE: archived {archived} duplicates from {len(rows)} active memories")
