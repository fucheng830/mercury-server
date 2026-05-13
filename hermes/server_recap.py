"""Server-side daily recap from sessions table (reads PostgreSQL, not local files)."""
import json
import logging
import os
from datetime import datetime

from hermes.db import execute, execute_one

logger = logging.getLogger(__name__)


def run_server_recap(date: str = "", llm_service=None):
    """Generate a daily recap from sessions in the database, write results as memories."""
    date = date or datetime.now().strftime("%Y-%m-%d")
    logger.info("Server recap for %s", date)

    # Read today's sessions from the DB
    rows = execute(
        """
        SELECT s.id, s.project_id, s.preview, s.message_count, s.first_ts, s.last_ts,
               s.project_path
        FROM sessions s
        WHERE s.last_ts IS NOT NULL
          AND s.last_ts::date = %s::date
        ORDER BY s.last_ts DESC
        LIMIT 200
        """,
        (date,),
        fetch=True,
    )
    if not rows:
        logger.info("Server recap: no sessions for %s", date)
        return []

    session_texts = []
    for r in (rows or []):
        preview = (r.get("preview") or "")[:300]
        project = r.get("project_path", "") or r.get("project_id", "")
        session_texts.append(f"[{project}] {preview}")

    logger.info("Server recap: %d sessions, calling LLM", len(session_texts))

    memories = _call_llm(date, session_texts, llm_service)
    if not memories:
        return []

    from hermes.memory_service import write_memory
    stored = []
    for m in memories:
        try:
            result = write_memory(
                content=m.get("content", ""),
                layer="episodic",
                source="server_recap",
                importance=m.get("importance", 3),
                tags=m.get("tags", []),
                summary=m.get("summary"),
                namespace="claude",
            )
            stored.append(result.get("id"))
        except Exception as e:
            logger.warning("Failed to store recap memory: %s", e)

    logger.info("Server recap: %d memories stored", len(stored))
    return stored


def _call_llm(date, texts, llm_service):
    """Use LLM to generate a recap. Falls back to simple aggregation if no LLM service."""
    system_prompt = (
        "你是一个AI助手每日复盘分析引擎。分析当天的Claude Code使用记录，生成每日复盘。"
        '请严格按照以下JSON格式输出：\n{"knowledge_gained": [{"content": "学到的知识", '
        '"importance": "high|medium|low", "tags": ["tag1"], "summary": "简短摘要"}], '
        '"key_decisions": ["决策1", "决策2"]}'
    )
    user_prompt = f"日期: {date}\n\n当日会话摘要:\n" + "\n".join(f"- {t}" for t in texts[:80])

    if llm_service is None:
        # Try to initialize from config
        try:
            from recap_config import get_llm_config
            from recap.llm_service import LLMService
            llm_service = LLMService(get_llm_config())
        except Exception:
            logger.warning("No LLM service available for recap")
            return _simple_recap(date, texts)

    try:
        result = llm_service.generate_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=262144,
        )
        if isinstance(result, str):
            result = json.loads(result)
        return _parse_recap_result(result)
    except Exception as e:
        logger.error("LLM recap failed: %s", e)
        return _simple_recap(date, texts)


def _parse_recap_result(data):
    memories = []
    importance_map = {"high": 4, "medium": 3, "low": 2}
    for item in data.get("knowledge_gained", []):
        memories.append({
            "content": item.get("content", ""),
            "summary": item.get("summary", ""),
            "importance": importance_map.get(item.get("importance", "medium"), 3),
            "tags": item.get("tags", []),
        })
    for d in data.get("key_decisions", []):
        memories.append({
            "content": d if isinstance(d, str) else str(d),
            "summary": "",
            "importance": 4,
            "tags": ["decision"],
        })
    return memories


def _simple_recap(date, texts):
    """Minimal recap without LLM — just stats."""
    from hermes.memory_service import write_memory
    content = f"Date: {date}\nTotal sessions: {len(texts)}\n\n" + "\n".join(f"- {t[:200]}" for t in texts[:30])
    r = write_memory(
        content=content, layer="episodic", source="server_recap",
        importance=2, tags=["daily-summary"], namespace="claude",
    )
    return [{"content": content, "summary": f"Daily recap {date}", "importance": 2, "tags": ["daily-summary"]}] if r else []
