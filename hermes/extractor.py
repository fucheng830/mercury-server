"""Durable-knowledge extractor + reconciler + correction-trajectory extractor.

Replaces recap→observation→distill for memory production.
- extract_for_date: 5 durable categories from raw sessions (via aggregate_daily).
- extract_corrections: BUGFIX memories from error→fix trajectories (raw episodes).
Both reconcile against the project's existing active memories (duplicate/supersede/new)
so memories stay self-consistent across sessions.

See docs/superpowers/specs/2026-06-20-memory-system-agent-first-design.md
"""
import logging
from typing import Any, Dict, List, Optional

from recap.llm_service import LLMService

logger = logging.getLogger(__name__)

EXTRACT_SYSTEM_PROMPT = """你是一个持久知识提取器。从下面的开发会话记录中提取【将来再用得上的持久知识】，只这 5 类：
- DECISION 决策：做了什么选择 + 为什么
- ARCH 架构/约定：模块划分、接口契约、数据流、命名/分层规则
- BUGFIX 坑：踩过的坑 + 根因 + 解法
- PREFERENCE 偏好：用户/团队的稳定习惯
- DISCOVERY 发现：具体技术事实/数值/不变量

每条要具体（带理由/根因/路径/数值），第三人称客观陈述，1-2 句，一条只讲一个点。
每条给 confidence: high(明确、可复用、有把握) / low(存疑、推测、待确认)。
每条必须给 concepts：1-4 个该知识"关于什么"的核心概念名词（工具名/算法/技术/领域术语），小写、用规范名（如 pgvector、oauth、rrf、celery），能跨其他记忆复现——用于把这条知识挂到全局概念枢纽上。

禁止提取：过程流水账("推进了X"、"今天做了")、学习态("掌握了/理解了")、泛项目描述、一次性任务意图、不可复用的琐碎。
没有持久知识就返回空 processed。

严格按 JSON 输出：
{"processed":[{"content":"具体的事实/决策/坑","type":"DECISION","importance":4,"confidence":"high","tags":["tag"],"concepts":["pgvector","rrf"]}]}
importance: 5=关键决策/严重坑, 4=重要约定或事实, 3=有用, 2=边缘, 1=琐碎
type: DECISION / ARCH / BUGFIX / PREFERENCE / DISCOVERY"""

CORRECTION_SYSTEM_PROMPT = """你是一个"纠错轨迹提取器"。下面是开发会话中 AI/开发者 碰到的若干错误及随后的排查与修正过程（原始片段）。
请为【真正有复用价值】的错误提取一条持久的 BUGFIX 记忆。每条 content 必须包含三段：
【坑】发生了什么错误（具体）
【根因】为什么会错（真正的根因，不是表面现象）
【正确做法】怎么改对的（可复用的解法/规避方式）

type 固定为 BUGFIX。
importance: 5=严重/高频/会造成数据丢失或部署失败的坑, 4=重要的坑, 3=一般
confidence: high(根因和正确做法都明确) / low(根因不确定或修正未完成)

只提取"别人下次也会踩、且有明确根因和解法"的坑。跳过：一次性环境小问题(命令拼错且无普遍意义)、无根因的随机失败、与项目领域无关的琐碎报错、已修复的拼写笔误。
没有值得记的纠错就返回空 processed。

严格按 JSON 输出：
{"processed":[{"content":"【坑】...\\n【根因】...\\n【正确做法】...","type":"BUGFIX","importance":4,"confidence":"high","tags":["tag"]}]}"""

RECONCILE_SYSTEM_PROMPT = """你是记忆对账器。给定若干【新提取的记忆】(带序号) 和该项目【现有活跃记忆】(带 id)，对每条新记忆判定其一：
- new：现有没有同类，应新增。
- duplicate_of：<id>：与现有某条同义/重申（结论一致），合并即可，不新建。
- supersedes：<id>：与现有某条同主题但结论已变化/被取代，现有那条应作废，用新这条。

判定依据语义：相同主题且结论一致 = duplicate；相同主题但结论变化或更优 = supersedes；无相关 = new。
每条新记忆必须有一个判定。

严格按 JSON 输出：
{"actions":[{"index":0,"action":"new"},{"index":1,"action":"duplicate_of","target":"<现有id>"},{"index":2,"action":"supersedes","target":"<现有id>"}]}
index 是新记忆的序号(从 0 开始)。"""


def _project_name(project_path: str) -> str:
    name = (project_path or "").rstrip("/\\").replace("\\", "/").rstrip("/").split("/")[-1]
    return name or project_path or "unknown"


def _sessions_text_for_project(sessions) -> str:
    parts: List[str] = []
    for i, s in enumerate(sessions, 1):
        parts.append(f"## 会话 {i}: {s.project}")
        if getattr(s, "user_prompts", None):
            parts.append("用户消息:")
            for p in s.user_prompts:
                parts.append(f"  - {p}")
        if getattr(s, "assistant_summaries", None):
            parts.append("助手要点:")
            for a in s.assistant_summaries:
                parts.append(f"  - {a}")
        if getattr(s, "tools_used", None):
            parts.append("工具: " + ", ".join(f"{k}({v})" for k, v in s.tools_used.items()))
        parts.append("")
    return "\n".join(parts)


def _link_concepts(memory_id: str, concepts, tags=None) -> None:
    """Link a memory to its concept-hub entities (creates+embeds new entities only)."""
    if not memory_id:
        return
    from hermes.db import execute_one
    from hermes.graph_service import upsert_entity, link_memory_entity

    names = [str(c).strip().lower() for c in (concepts or []) if str(c).strip()]
    if not names and tags:
        names = [str(t).strip().lower() for t in tags if str(t).strip()]
    for name in names[:6]:
        try:
            existing = execute_one("SELECT id FROM entities WHERE name = %s", (name,))
            if existing is None:
                upsert_entity(name, "concept")  # embeds once, on first creation
            link_memory_entity(memory_id, name)
        except Exception as e:
            logger.warning("link concept '%s' failed: %s", name, e)


def _write_extracted(item: Dict, project_id: str, namespace: str) -> Optional[Dict]:
    from hermes.memory_service import write_memory
    conf = (item.get("confidence") or "low").lower()
    stage = "memory" if conf == "high" else "candidate"
    try:
        mem = write_memory(
            content=item.get("content", ""),
            stage=stage,
            source="extractor",
            type=item.get("type", "NOTE"),
            importance=int(item.get("importance", 3) or 3),
            tags=item.get("tags", []) or [],
            project_id=project_id,
            namespace=namespace,
            auto_embed=False,
        )
    except Exception as e:
        logger.warning("write extracted memory failed: %s", e)
        return None
    _link_concepts(mem.get("id"), item.get("concepts"), item.get("tags"))
    return mem


def _reconcile_and_write(
    items: List[Dict], project_id: str, project_name: str, llm: LLMService, namespace: str = "claude",
) -> Dict[str, int]:
    """Reconcile extracted items against the project's existing active memories,
    then apply: duplicate→bump, supersede→retire+add, new→add. Returns counts."""
    from hermes.memory_service import list_memories, bump_memory, supersede_memory

    counts = {"extracted": len(items), "new": 0, "duplicates": 0, "superseded": 0}
    if not items:
        return counts

    existing = list_memories(
        stage="memory", project_id=project_id, statuses=["active"], size=50,
    ).get("items", [])
    action_map: Dict[int, Dict] = {}
    if existing:
        new_text = "\n".join(
            f"[{i}] {(it.get('type') or 'NOTE')}: {it.get('content', '')}"
            for i, it in enumerate(items)
        )
        ex_text = "\n".join(
            f"(id={e['id']}) [{e.get('type', 'NOTE')}★{e.get('importance', 3)}] {e.get('content', '')[:160]}"
            for e in existing
        )
        prompt = (
            f"【新提取的记忆】\n{new_text}\n\n"
            f"【该项目现有活跃记忆】\n{ex_text}\n\n"
            "请对每条新记忆判定 new / duplicate_of:<id> / supersedes:<id>。"
        )
        try:
            r = llm.generate_json(RECONCILE_SYSTEM_PROMPT, prompt, max_tokens=4096)
            for a in (r.get("actions", []) if isinstance(r, dict) else []):
                if isinstance(a, dict) and "index" in a:
                    action_map[int(a["index"])] = a
        except Exception as e:
            logger.warning("reconcile LLM failed for %s: %s", project_name, e)

    for i, item in enumerate(items):
        act = action_map.get(i, {"action": "new"})
        action = (act.get("action") or "new").lower()
        if action == "duplicate_of":
            tid = act.get("target")
            if tid:
                bump_memory(tid, content=item.get("content"), importance=item.get("importance"), namespace=namespace)
            counts["duplicates"] += 1
        elif action == "supersede":
            tid = act.get("target")
            new_mem = None
            if tid:
                supersede_memory(tid, namespace=namespace)
                counts["superseded"] += 1
            new_mem = _write_extracted(item, project_id, namespace)
            counts["new"] += 1
            # Channel 1 audit: link refutation + record event
            if tid and new_mem:
                from hermes.counterexample_service import add_refutation, record_event
                add_refutation(
                    target_id=tid, refuting_id=new_mem["id"], namespace=namespace,
                    reason="LLM reconcile: superseded by newer memory",
                    source="llm_reconcile", confidence=item.get("confidence"),
                )
                record_event(
                    tid, "superseded", "llm_reconcile",
                    reason="LLM reconcile: superseded",
                    details={"superseded_by": new_mem["id"]},
                )
        else:
            _write_extracted(item, project_id, namespace)
            counts["new"] += 1
    return counts


def extract_for_project(
    sessions, project_path: str, llm: LLMService, namespace: str = "claude",
) -> Dict[str, int]:
    from hermes.memory_service import get_or_create_project

    text = _sessions_text_for_project(sessions)
    if len(text.strip()) < 40:
        return {"extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}

    project_name = _project_name(project_path)
    project_id = get_or_create_project(project_name, project_path, namespace)["id"]

    try:
        result = llm.generate_json(EXTRACT_SYSTEM_PROMPT, text, max_tokens=8192)
    except Exception as e:
        logger.warning("extract LLM failed for %s: %s", project_name, e)
        return {"extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}
    items = result.get("processed", []) if isinstance(result, dict) else []
    return _reconcile_and_write(items, project_id, project_name, llm, namespace)


def extract_from_sessions(
    sessions_data: List[Dict], project_path: str, llm: LLMService, namespace: str = "claude",
) -> Dict[str, int]:
    """Extract durable memories from client-supplied FULL session summaries (JSON).

    Each item: {user_prompts, assistant_summaries, tools_used, project}. The client reads
    the local ~/.claude/projects/*/*.jsonl (full content the server's DB lacks — it only
    stores shallow previews) and ships substantive batches. Thin wrapper over
    extract_for_project; the client controls batch size per request.
    """
    from recap.aggregator import SessionSummary

    summaries: List[Any] = []
    for d in sessions_data or []:
        if not isinstance(d, dict):
            continue
        summaries.append(SessionSummary(
            session_id="", project=d.get("project") or project_path,
            start_time="", end_time="", model="", token_usage={},
            user_prompts=d.get("user_prompts", []) or [],
            assistant_summaries=d.get("assistant_summaries", []) or [],
            tools_used=d.get("tools_used", {}) or {},
        ))
    return extract_for_project(summaries, project_path, llm, namespace)


def extract_for_date(date: str, llm: LLMService, provider) -> Dict[str, Any]:
    """Extract durable memories for all sessions on a given date, grouped by project."""
    from recap.aggregator import aggregate_daily

    daily = aggregate_daily(provider, date)
    if not daily:
        return {"date": date, "error": f"no sessions found for {date}"}

    by_project: Dict[str, list] = {}
    for s in daily.sessions:
        by_project.setdefault(s.project, []).append(s)

    results: Dict[str, Any] = {}
    total = {"extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}
    for project_path, sessions in by_project.items():
        r = extract_for_project(sessions, project_path, llm)
        results[_project_name(project_path)] = r
        for k in total:
            total[k] += r.get(k, 0)
    logger.info("extract_for_date(%s): %s projects, %s", date, len(by_project), total)
    return {"date": date, "projects": results, "total": total}


def extract_corrections(
    episodes: List[str], project_path: str, llm: LLMService, namespace: str = "claude",
) -> Dict[str, int]:
    """Extract BUGFIX memories from raw error→fix episodes (client-supplied, full content).

    episodes: list of raw text windows, each = a failed action + error + the fix turns.
    """
    from hermes.memory_service import get_or_create_project

    counts = {"episodes": len(episodes), "extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}
    if not episodes:
        return counts

    project_name = _project_name(project_path)
    project_id = get_or_create_project(project_name, project_path, namespace)["id"]

    ep_text = "\n\n".join(f"### 错误片段 {i + 1}\n{e}" for i, e in enumerate(episodes))
    try:
        result = llm.generate_json(CORRECTION_SYSTEM_PROMPT, ep_text, max_tokens=8192)
    except Exception as e:
        logger.warning("correction extract LLM failed for %s: %s", project_name, e)
        return counts
    items = result.get("processed", []) if isinstance(result, dict) else []
    applied = _reconcile_and_write(items, project_id, project_name, llm, namespace)
    counts.update({k: applied.get(k, 0) for k in ("extracted", "new", "duplicates", "superseded")})
    return counts
