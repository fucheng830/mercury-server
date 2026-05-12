"""Self-iteration engine with daily/weekly/monthly scheduled jobs."""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from recap_config import get_llm_config, get_storage_config
from recap.llm_service import LLMService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts (Chinese)
# ---------------------------------------------------------------------------

DEDUP_SYSTEM_PROMPT = """你是一个记忆去重与压缩引擎。你的任务是分析近期的情景记忆（episodic memories），进行去重和合并。

对于每一组合并后的记忆，请：
1. 生成一个简洁的摘要（summary）
2. 评估重要性（1-5，5最重要）
3. 提取 2-5 个标签（tags）
4. 记录合并了多少条原始记忆（source_count）

请严格按照以下 JSON 格式输出：
{
  "processed": [
    {
      "content": "合并后的完整内容",
      "summary": "简洁摘要",
      "importance": 4,
      "tags": ["tag1", "tag2"],
      "source_count": 3
    }
  ]
}

注意：
- 内容相似或相关的记忆应该合并
- 保留所有重要细节，不要丢失关键信息
- importance 评估标准：1=琐碎，2=一般，3=有用，4=重要，5=关键
- 如果所有记忆都是独立的，每条单独输出即可"""

CORE_REVIEW_SYSTEM_PROMPT = """你是一个核心记忆审查引擎。你的任务是审查语义记忆（semantic memories），判断哪些应该晋升为核心记忆（core）。

晋升标准：
- 用户偏好和习惯
- 架构决策和技术选型
- 工具配置和使用方式
- 错误解决方案和踩坑经验
- 反复出现的模式

请严格按照以下 JSON 格式输出：
{
  "promoted": [
    {
      "content": "核心记忆内容",
      "summary": "简洁摘要",
      "importance": 5,
      "tags": ["tag1", "tag2"],
      "original_ids": ["id1", "id2"]
    }
  ]
}

注意：
- 只选择真正值得长期保留的记忆
- 合并内容重复或高度相关的条目
- importance 应该是 4 或 5
- 如果没有值得晋升的，返回空数组
- 已有的核心记忆如下供参考去重"""

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
# Helper: fetch episodic memories from last N days
# ---------------------------------------------------------------------------

def _fetch_recent_memories(layer: str, days: int, limit: int = 50) -> List[Dict]:
    """Fetch memories from a specific layer within the last N days."""
    from hermes.memory_service import read_memories

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    all_mems = read_memories(layer=layer, limit=limit)

    # Filter by created_at
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
    """Run daily ingestion job: load recap, write memories, compress, promote.

    Args:
        date: Date string (YYYY-MM-DD). Defaults to today.

    Returns:
        Dict with status, date, ingested count, compressed count, promoted count.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    # Load recap file
    config = get_storage_config()
    recap_dir = Path(os.path.expanduser(config.get("recap_dir", "~/.hermes/recaps")))
    recap_file = recap_dir / f"{date}.json"

    if not recap_file.exists():
        logger.info("No recap file found for %s", date)
        return {"status": "no_recap", "date": date, "ingested": 0,
                "compressed_to_semantic": 0, "promoted_to_core": 0}

    recap_data = json.loads(recap_file.read_text(encoding="utf-8"))

    # Extract knowledge_gained items
    importance_map = {"high": 4, "medium": 3, "low": 2}
    ingested = 0

    for item in recap_data.get("knowledge_gained", []):
        raw_importance = item.get("importance", "medium")
        numeric_importance = importance_map.get(raw_importance, 3)

        _write_memory_safe(
            content=item.get("content", ""),
            layer="episodic",
            source="recap",
            importance=numeric_importance,
            tags=item.get("tags", []),
            auto_embed=True,
        )
        ingested += 1

    # Extract key_decisions as high-importance memories
    for decision in recap_data.get("key_decisions", []):
        _write_memory_safe(
            content=decision if isinstance(decision, str) else str(decision),
            layer="episodic",
            source="recap",
            importance=4,
            tags=["decision"],
            auto_embed=True,
        )
        ingested += 1

    # Compress episodic to semantic
    compressed = _compress_episodic_to_semantic()

    # Auto-promote qualified semantic to core
    promoted = _auto_promote_safe()

    logger.info("Daily ingestion for %s: ingested=%d, compressed=%d, promoted=%d",
                date, ingested, compressed, promoted)

    return {
        "status": "ok",
        "date": date,
        "ingested": ingested,
        "compressed_to_semantic": compressed,
        "promoted_to_core": promoted,
    }


def _write_memory_safe(**kwargs) -> None:
    """Write memory with error handling."""
    try:
        from hermes.memory_service import write_memory
        write_memory(**kwargs)
    except Exception as e:
        logger.warning("Failed to write memory: %s", e)


def _auto_promote_safe() -> int:
    """Run auto_promote with error handling."""
    try:
        from hermes.memory_service import auto_promote
        return auto_promote()
    except Exception as e:
        logger.warning("Failed to auto_promote: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Episodic → Semantic compression
# ---------------------------------------------------------------------------

def _compress_episodic_to_semantic() -> int:
    """Compress recent episodic memories into semantic memories via LLM.

    Returns:
        Number of compressed items written as semantic memories.
    """
    try:
        episodic = _fetch_recent_memories("episodic", days=7, limit=50)
    except Exception:
        episodic = []

    if not episodic:
        return 0

    # Build user prompt with episodic memories
    memories_text = "\n".join(
        f"[{m.get('id', '?')}] (importance={m.get('importance', 3)}): {m.get('content', '')}"
        for m in episodic
    )

    user_prompt = f"请分析以下 {len(episodic)} 条情景记忆，进行去重和合并：\n\n{memories_text}"

    try:
        llm = LLMService(get_llm_config())
        result = llm.generate_json(DEDUP_SYSTEM_PROMPT, user_prompt, max_tokens=262144)
    except Exception as e:
        logger.warning("LLM compression failed: %s", e)
        return 0

    processed = result.get("processed", [])
    count = 0
    for item in processed:
        try:
            from hermes.memory_service import write_memory
            write_memory(
                content=item.get("content", ""),
                layer="semantic",
                source="compression",
                importance=item.get("importance", 3),
                tags=item.get("tags", []),
                summary=item.get("summary"),
                auto_embed=True,
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to write compressed memory: %s", e)

    return count


# ---------------------------------------------------------------------------
# Weekly core review
# ---------------------------------------------------------------------------

def run_weekly_core_review() -> Dict[str, Any]:
    """Run weekly core review: promote high-importance semantic memories to core.

    Returns:
        Dict with status and promoted count.
    """
    try:
        semantic = _fetch_high_importance_semantic(limit=50)
    except Exception:
        semantic = []

    if not semantic:
        logger.info("No high-importance semantic memories to review")
        return {"status": "ok", "promoted": 0}

    # Fetch existing core for dedup context
    try:
        from hermes.memory_service import read_memories
        core = read_memories(layer="core", limit=50)
    except Exception:
        core = []

    # Build prompt
    semantic_text = "\n".join(
        f"[{m.get('id', '?')}] (importance={m.get('importance', 3)}): {m.get('content', '')}"
        for m in semantic
    )

    core_text = ""
    if core:
        core_text = "\n\n已有核心记忆（供去重参考）：\n" + "\n".join(
            f"- {m.get('content', '')[:100]}" for m in core
        )

    user_prompt = f"请审查以下 {len(semantic)} 条高重要性语义记忆，判断哪些应晋升为核心记忆：\n\n{semantic_text}{core_text}"

    try:
        llm = LLMService(get_llm_config())
        result = llm.generate_json(CORE_REVIEW_SYSTEM_PROMPT, user_prompt, max_tokens=262144)
    except Exception as e:
        logger.warning("LLM core review failed: %s", e)
        return {"status": "error", "promoted": 0}

    promoted_items = result.get("promoted", [])
    count = 0
    for item in promoted_items:
        try:
            from hermes.memory_service import write_memory
            write_memory(
                content=item.get("content", ""),
                layer="core",
                source="core_review",
                importance=item.get("importance", 5),
                tags=item.get("tags", []),
                summary=item.get("summary"),
                auto_embed=True,
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to write core memory: %s", e)

    logger.info("Weekly core review: promoted %d memories", count)
    return {"status": "ok", "promoted": count}


def _fetch_high_importance_semantic(limit: int = 50) -> List[Dict]:
    """Fetch semantic memories with importance >= 4."""
    from hermes.db import execute
    rows = execute(
        """
        SELECT id, layer, content, summary, source, importance, tags,
               recall_count, created_at, expires_at, updated_at
        FROM memories
        WHERE layer = 'semantic' AND importance >= 4
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
        fetch=True,
    )
    from hermes.memory_service import _serialize_memory
    return [_serialize_memory(r) for r in (rows or [])]


# ---------------------------------------------------------------------------
# Monthly graph maintenance
# ---------------------------------------------------------------------------

def run_monthly_graph_maintenance() -> Dict[str, Any]:
    """Run monthly graph maintenance: extract entities and relations, cleanup.

    Returns:
        Dict with status, entity count, relation count, expired cleaned count.
    """
    # Fetch recent memories (all layers)
    try:
        from hermes.memory_service import read_memories
        memories = read_memories(limit=50)
        # Filter to last 30 days
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
        # Build prompt with memory content
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

        # Upsert entities
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

        # Add relations
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

        # Link memories to entities by name matching
        for m in memories:
            content_lower = m.get("content", "").lower()
            for entity in result.get("entities", []):
                entity_name = entity.get("name", "")
                if entity_name.lower() in content_lower:
                    try:
                        link_memory_entity(m["id"], entity_name)
                    except Exception as e:
                        logger.debug("Failed to link memory to entity: %s", e)

    # Cleanup expired
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
