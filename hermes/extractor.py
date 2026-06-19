"""Durable-knowledge extractor + reconciler (agent-first project memory).

Replaces recap→observation→distill for memory production. Runs on the raw
session content that aggregate_daily already loads, extracts ONLY the 5
durable categories, then reconciles against the project's existing active
memories (duplicate → merge, supersedes → retire old + add new, else new).

See docs/superpowers/specs/2026-06-20-memory-system-agent-first-design.md
"""
import logging
import os
from typing import Any, Dict, List

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

禁止提取：过程流水账("推进了X"、"今天做了")、学习态("掌握了/理解了")、泛项目描述、一次性任务意图、不可复用的琐碎。
没有持久知识就返回空 processed。

严格按 JSON 输出：
{"processed":[{"content":"具体的事实/决策/坑","type":"DECISION","importance":4,"confidence":"high","tags":["tag"]}]}
importance: 5=关键决策/严重坑, 4=重要约定或事实, 3=有用, 2=边缘, 1=琐碎
type: DECISION / ARCH / BUGFIX / PREFERENCE / DISCOVERY"""

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
    name = (project_path or "").rstrip("/").split("/")[-1]
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


def _write_extracted(item: Dict, project_id: str, namespace: str) -> None:
    from hermes.memory_service import write_memory
    conf = (item.get("confidence") or "low").lower()
    stage = "memory" if conf == "high" else "candidate"
    try:
        write_memory(
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


def extract_for_project(
    sessions,
    project_path: str,
    llm: LLMService,
    namespace: str = "claude",
) -> Dict[str, int]:
    from hermes.memory_service import (
        get_or_create_project, list_memories, bump_memory, supersede_memory,
    )

    text = _sessions_text_for_project(sessions)
    counts = {"extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}
    if len(text.strip()) < 40:
        return counts

    project_name = _project_name(project_path)
    project_id = get_or_create_project(project_name, project_path, namespace)["id"]

    try:
        result = llm.generate_json(EXTRACT_SYSTEM_PROMPT, text, max_tokens=8192)
    except Exception as e:
        logger.warning("extract LLM failed for %s: %s", project_name, e)
        return counts
    items = result.get("processed", []) if isinstance(result, dict) else []
    counts["extracted"] = len(items)
    if not items:
        return counts

    # Reconcile against existing active memories of this project.
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
            if tid:
                supersede_memory(tid, namespace=namespace)
                counts["superseded"] += 1
            _write_extracted(item, project_id, namespace)
            counts["new"] += 1
        else:
            _write_extracted(item, project_id, namespace)
            counts["new"] += 1
    return counts


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
