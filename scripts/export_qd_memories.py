"""Export QuantDinger-Vue extracted memories to markdown (UTF-8)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hermes.db import execute

ms = execute(
    """SELECT type, importance, content FROM memories m
       JOIN projects p ON p.id = m.project_id
       WHERE p.path LIKE '%QuantDinger-Vue%'
       ORDER BY CASE type WHEN 'DECISION' THEN 1 WHEN 'DISCOVERY' THEN 2
                WHEN 'ARCH' THEN 3 WHEN 'BUGFIX' THEN 4
                WHEN 'PREFERENCE' THEN 5 ELSE 6 END,
                importance DESC, m.created_at""",
    fetch=True,
)

by = {}
for m in ms:
    by.setdefault(m["type"], []).append(m)

order = ["DECISION", "DISCOVERY", "ARCH", "BUGFIX", "PREFERENCE", "NOTE"]

lines = [
    f"# QuantDinger-Vue 提炼记忆（{len(ms)} 条）",
    "",
    "来源：extract_qd_memories.py（ollama qwen3:8b 从 386 个 session 对话提炼）",
    "时间：2026-07-29",
    "",
]
for t in order:
    if t not in by:
        continue
    lines += [f"## {t}（{len(by[t])}）", ""]
    for m in by[t]:
        s = "★" * (m["importance"] or 3)
        lines.append(f"- {s} {m['content']}")
    lines.append("")

out = Path(__file__).resolve().parent.parent / "qd_memories_export.md"
out.write_text("\n".join(lines), encoding="utf-8")
print(f"exported {len(ms)} memories -> {out.name}")

for t in order:
    if t not in by:
        continue
    print(f"\n-- {t} ({len(by[t])}) --")
    for m in by[t][:3]:
        print(f"  *{m['importance']} {m['content'][:88]}")
