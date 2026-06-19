"""Self-iteration engine with daily/weekly/monthly scheduled jobs.

Memory model v2 pipeline:
  session -> observation (daily ingest)
  observation -> candidate (LLM distillation, pre-fills type)
  candidate -> memory (human confirm; weekly auto_promote for high-confidence)
See docs/superpowers/specs/2026-06-18-memory-model-v2-design.md
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from recap_config import get_llm_config, get_storage_config
from recap.llm_service import LLMService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts (Chinese)
# ---------------------------------------------------------------------------

DEDUP_SYSTEM_PROMPT = """你是一个记忆提炼引擎。把多条原始观察记忆（observations）提炼成【少量高价值、具体、可复用】的候选记忆（candidates）。宁可少输出甚至不输出，也绝不产出空洞概括。

【只提取这几类知识（不满足就别输出）】
- DECISION 决策：做了什么选择 + 为什么（例："social-auto-upload 作为子模块通过 CLI/API 集成进万象平台，不直接嵌依赖，避免版本耦合"）
- ARCH/CONVENTION 架构或约定：模块划分、接口契约、数据流、命名/分层规则（例："万象运营是 Python 3.12 FastAPI monorepo，多平台自媒体运营工具，分阶段实施"）
- BUGFIX/GOTCHA 修复或坑：踩过的坑 + 根因 + 解法（例："代理管理批量导入曾因并发丢数据，已加分布式锁"）
- PREFERENCE 偏好：用户/团队的稳定习惯（例："提交前必须跑 lint，不接受 warning"）
- DISCOVERY 发现：具体的技术事实/数值/机制（例："bge-m3 在 0.13 Ollama 单次推理约 56s"）

【直接丢弃（放进 dropped 数组，不要硬凑成 candidate）】
- 学习目标/主观状态："掌握了…/理解了…/熟悉了…"
- 流水账/过程："推进了 Phase 3"、"今天做了 X、Y、Z"
- 泛泛项目描述/意图："X 是个 React 前端"、"计划迁移…"
- 不可复用的琐碎细节

【写法要求】
- 第三人称客观陈述句，带具体名词/数值/机制，不要主观词。
- 一条记忆只讲一个点，1-2 句说清。
- 内容相似/重复的观察合并成一条；互补细节拼进同一条。

【严格按以下 JSON 格式输出】
{
  "processed": [
    {
      "content": "具体的事实/决策/坑（带关键细节）",
      "summary": "≤20字摘要",
      "importance": 4,
      "tags": ["tag1", "tag2"],
      "type": "DECISION",
      "source_count": 3
    }
  ],
  "dropped": ["被丢弃的观察概述（说明为什么没提炼成候选）"]
}

importance：5=关键决策/严重坑，4=重要约定或事实，3=有用，2=边缘，1=琐碎
type：DECISION=决策，ARCH=架构/约定，BUGFIX=修复/坑，PREFERENCE=偏好，DISCOVERY=发现，NOTE=其他具体笔记
如果这批观察里没有高价值内容，processed 返回空数组，全部放进 dropped 并说明原因。"""

ENTITY_EXTRACT_SYSTEM_PROMPT = """你是一个实体和关系提取引擎。你的任务是从知识条目中提取实体和关系。

实体类型：person（人物）、project（项目）、tool（工具）、concept（概念）、technology（技术）
关系类型：uses（使用）、depends_on（依赖）、prefers（偏好）、owns（拥有）、related_to（关联）

请严格按照以下 JSON 格式输出：
{
  "entities": [
    {
      "name": "实体名称",
      "type": "technology",
      "description": "实体描述"
    }
  ],
  "relations": [
    {
      "source": "源实体名称",
      "target": "目标实体名称",
      "relation": "uses"
    }
  ]
}

注意：
- 提取所有明确提到的实体，不要遗漏
- 关系只在源文本中有明确依据时才提取
- 实体名称保持原文，不要翻译或改写"""


# ---------------------------------------------------------------------------
# Helper: fetch memories from a stage within last N days
# ---------------------------------------------------------------------------

def _fetch_recent_memories(stage: str, days: int, limit: int = 50) -> List[Dict]:
    """Fetch memories from a specific stage within the last N days."""
    from hermes.memory_service import read_memories

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    all_mems = read_memories(stage=stage, limit=limit)

    result = []
    for m in all_mems:
        created_str = m.get("created_at")
        if created_str:
            created = datetime.fromisoformat(created_str)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created >= cutoff:
                result.append(m)
    return result


# ---------------------------------------------------------------------------
# Daily ingestion
# ---------------------------------------------------------------------------

def run_daily_ingestion(date: str = None) -> Dict[str, Any]:
    """Run daily ingestion job: load recap, write observations, distill, promote.

    Args:
        date: Date string (YYYY-MM-DD). Defaults to today.

    Returns:
        Dict with status, date, ingested / distilled / promoted counts.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    config = get_storage_config()
    recap_dir = Path(os.path.expanduser(config.get("recap_dir", "~/.hermes/recaps")))
    recap_file = recap_dir / f"{date}.json"

    if not recap_file.exists():
        logger.info("No recap file found for %s", date)
        return {"status": "no_recap", "date": date, "ingested": 0,
                "distilled_to_candidate": 0, "promoted_to_memory": 0}

    recap_data = json.loads(recap_file.read_text(encoding="utf-8"))

    importance_map = {"high": 4, "medium": 3, "low": 2}
    ingested = 0

    for item in recap_data.get("knowledge_gained", []):
        raw_importance = item.get("importance", "medium")
        numeric_importance = importance_map.get(raw_importance, 3)

        _write_memory_safe(
            content=item.get("content", ""),
            stage="observation",
            source="recap",
            importance=numeric_importance,
            tags=item.get("tags", []),
            auto_embed=True,
        )
        ingested += 1

    for decision in recap_data.get("key_decisions", []):
        _write_memory_safe(
            content=decision if isinstance(decision, str) else str(decision),
            stage="observation",
            source="recap",
            importance=4,
            tags=["decision"],
            auto_embed=True,
        )
        ingested += 1

    distilled = _distill_observation_to_candidate()
    promoted = _auto_promote_safe()

    logger.info("Daily ingestion for %s: ingested=%d, distilled=%d, promoted=%d",
                date, ingested, distilled, promoted)

    return {
        "status": "ok",
        "date": date,
        "ingested": ingested,
        "distilled_to_candidate": distilled,
        "promoted_to_memory": promoted,
    }


def _write_memory_safe(**kwargs) -> None:
    """Write memory with error handling."""
    try:
        from hermes.memory_service import write_memory
        write_memory(**kwargs)
    except Exception as e:
        logger.warning("Failed to write memory: %s", e)


def _auto_promote_safe() -> int:
    """Run auto_promote (high-confidence candidate -> memory) with error handling."""
    try:
        from hermes.memory_service import auto_promote
        return auto_promote()
    except Exception as e:
        logger.warning("Failed to auto_promote: %s", e)
        return 0


# ---------------------------------------------------------------------------
# observation -> candidate distillation
# ---------------------------------------------------------------------------

def _distill_observation_to_candidate() -> int:
    """Distill recent observations into candidates via LLM (pre-fills type).

    Returns:
        Number of distilled items written as candidate memories.
    """
    try:
        observations = _fetch_recent_memories("observation", days=7, limit=50)
    except Exception:
        observations = []

    if not observations:
        return 0

    memories_text = "\n".join(
        f"[{m.get('id', '?')}] (importance={m.get('importance', 3)}): {m.get('content', '')}"
        for m in observations
    )

    user_prompt = f"请分析以下 {len(observations)} 条观察记忆，进行去重、合并与提炼：\n\n{memories_text}"

    try:
        llm = LLMService(get_llm_config())
        result = llm.generate_json(DEDUP_SYSTEM_PROMPT, user_prompt, max_tokens=262144)
    except Exception as e:
        logger.warning("LLM distillation failed: %s", e)
        return 0

    processed = result.get("processed", [])
    count = 0
    for item in processed:
        try:
            from hermes.memory_service import write_memory
            write_memory(
                content=item.get("content", ""),
                stage="candidate",
                type=item.get("type", "NOTE"),
                source="distillation",
                importance=item.get("importance", 3),
                tags=item.get("tags", []),
                summary=item.get("summary"),
                auto_embed=True,
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to write distilled candidate: %s", e)

    return count


# ---------------------------------------------------------------------------
# Weekly auto-promotion (candidate -> memory, high-confidence bypass)
# ---------------------------------------------------------------------------

def run_weekly_core_review() -> Dict[str, Any]:
    """Weekly job: auto-promote high-confidence candidates to memory.

    Per design Q2, candidate->memory is a human confirmation gate; this job is
    the optional high-confidence bypass (importance>=5 or recall>=5) handled by
    memory_service.auto_promote.
    """
    promoted = _auto_promote_safe()
    logger.info("Weekly auto-promote: promoted %d candidates to memory", promoted)
    return {"status": "ok", "promoted": promoted}


# ---------------------------------------------------------------------------
# Monthly graph maintenance
# ---------------------------------------------------------------------------

def run_monthly_graph_maintenance() -> Dict[str, Any]:
    """Run monthly graph maintenance: extract entities and relations, cleanup.

    Returns:
        Dict with status, entity count, relation count, expired cleaned count.
    """
    try:
        from hermes.memory_service import read_memories
        memories = read_memories(limit=50)
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        memories = [
            m for m in memories
            if m.get("created_at") and
            datetime.fromisoformat(m["created_at"]).replace(
                tzinfo=None if datetime.fromisoformat(m["created_at"]).tzinfo else None
            ) >= cutoff.replace(tzinfo=None)
            or not m.get("created_at")
        ]
    except Exception:
        memories = []

    entities_created = 0
    relations_created = 0

    if memories:
        memories_text = "\n".join(
            f"[{m.get('id', '?')}] {m.get('content', '')}"
            for m in memories
        )

        user_prompt = f"请从以下 {len(memories)} 条知识中提取实体和关系：\n\n{memories_text}"

        try:
            llm = LLMService(get_llm_config())
            result = llm.generate_json(ENTITY_EXTRACT_SYSTEM_PROMPT, user_prompt, max_tokens=262144)
        except Exception as e:
            logger.warning("LLM entity extraction failed: %s", e)
            result = {}

        from hermes.graph_service import upsert_entity, add_relation, link_memory_entity

        for entity in result.get("entities", []):
            try:
                upsert_entity(
                    name=entity.get("name", ""),
                    entity_type=entity.get("type", "concept"),
                    description=entity.get("description", ""),
                    auto_embed=False,
                )
                entities_created += 1
            except Exception as e:
                logger.warning("Failed to upsert entity: %s", e)

        for rel in result.get("relations", []):
            try:
                add_relation(
                    source_name=rel.get("source", ""),
                    target_name=rel.get("target", ""),
                    relation=rel.get("relation", "related_to"),
                )
                relations_created += 1
            except Exception as e:
                logger.warning("Failed to add relation: %s", e)

        for m in memories:
            content_lower = m.get("content", "").lower()
            for entity in result.get("entities", []):
                entity_name = entity.get("name", "")
                if entity_name.lower() in content_lower:
                    try:
                        link_memory_entity(m["id"], entity_name)
                    except Exception as e:
                        logger.debug("Failed to link memory to entity: %s", e)

    try:
        from hermes.memory_service import cleanup_expired
        expired = cleanup_expired()
    except Exception as e:
        logger.warning("Failed to cleanup expired: %s", e)
        expired = 0

    logger.info("Monthly graph maintenance: entities=%d, relations=%d, expired=%d",
                entities_created, relations_created, expired)

    return {
        "status": "ok",
        "entities": entities_created,
        "relations": relations_created,
        "expired_cleaned": expired,
    }
