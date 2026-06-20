"""Daily recap generation engine.

DEPRECATED for automatic memory production: the scheduler no longer runs the
daily_recap job. The durable-knowledge extractor (hermes/extractor.py) replaced
the recap->observation pipeline. Kept for manual use via /api/recap/generate.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from recap_config import get_storage_config, get_memory_config
from recap.aggregator import DailySummary, aggregate_daily
from recap.llm_service import LLMService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个 AI 编程助手的工作日志分析器。你的任务是根据 Claude Code 的当日会话摘要，生成一份结构化的每日复盘报告。

请严格按照以下 JSON 格式输出（不要包含 markdown 代码块标记）：

{
  "summary": "今日概要，2-3 句话总结",
  "key_decisions": ["关键决策1及其原因", "关键决策2及其原因"],
  "knowledge_gained": [
    {"content": "学到的知识或发现", "importance": "high|medium|low"}
  ],
  "patterns_observed": ["观察到的工作模式或习惯"],
  "issues_encountered": ["遇到的问题或阻碍"],
  "todos_extracted": ["从对话中提取的待办事项"]
}

注意：
- knowledge_gained 中只有真正值得长期记住的知识才标为 "high"
- high 重要性的标准：技术发现、配置信息、踩坑经验、架构决策
- 不要编造不存在的信息，只基于提供的会话数据总结
- 如果某个类别没有内容，使用空数组"""


def _build_user_prompt(daily: DailySummary) -> str:
    """Build the user prompt from a DailySummary."""
    parts = [f"# {daily.date} Claude Code 工作记录\n"]
    parts.append(f"- 会话数: {daily.total_sessions}")
    parts.append(f"- Token 用量: {daily.total_tokens:,}")
    parts.append(f"- 涉及项目: {', '.join(daily.unique_projects)}")
    parts.append(f"- 修改文件: {len(daily.files_modified)} 个\n")

    for i, session in enumerate(daily.sessions, 1):
        parts.append(f"## 会话 {i}: {session.project}")
        parts.append(f"时间: {session.start_time} ~ {session.end_time}")
        parts.append(f"模型: {session.model}")
        parts.append(f"Token: in={session.token_usage.get('input', 0):,} out={session.token_usage.get('output', 0):,}")

        if session.tools_used:
            tool_str = ", ".join(f"{k}({v})" for k, v in session.tools_used.items())
            parts.append(f"工具: {tool_str}")

        if session.user_prompts:
            parts.append("\n用户消息:")
            for p in session.user_prompts[:5]:
                parts.append(f"  - {p}")

        if session.assistant_summaries:
            parts.append("\n助手回复摘要:")
            for s in session.assistant_summaries[:3]:
                parts.append(f"  - {s}")

        parts.append("")

    if daily.files_modified:
        parts.append("## 修改的文件")
        for f in daily.files_modified[:20]:
            parts.append(f"  - {f}")
        parts.append("")

    return "\n".join(parts)


def get_recap_dir() -> Path:
    config = get_storage_config()
    recap_dir = Path(os.path.expanduser(config.get("recap_dir", "~/.hermes/recaps")))
    recap_dir.mkdir(parents=True, exist_ok=True)
    return recap_dir


def load_recap(date: str) -> Optional[Dict[str, Any]]:
    """Load a previously generated recap."""
    recap_file = get_recap_dir() / f"{date}.json"
    if not recap_file.exists():
        return None
    return json.loads(recap_file.read_text(encoding="utf-8"))


def list_recaps() -> List[Dict[str, Any]]:
    """List all saved recaps, newest first."""
    recap_dir = get_recap_dir()
    recaps = []
    for f in sorted(recap_dir.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            recaps.append({
                "date": data.get("date", f.stem),
                "summary": data.get("summary", "")[:100],
                "model_used": data.get("model_used", ""),
                "generated_at": data.get("generated_at", ""),
            })
        except (json.JSONDecodeError, OSError):
            pass
    return recaps


def generate_recap(
    provider,
    date: str,
    llm_service: LLMService,
) -> Dict[str, Any]:
    """Generate a daily recap. Returns the recap dict."""
    daily = aggregate_daily(provider, date)
    if not daily:
        return {"error": f"No sessions found for {date}", "date": date}

    user_prompt = _build_user_prompt(daily)
    logger.info(f"Generating recap for {date}, prompt length: {len(user_prompt)}")

    try:
        llm_result = llm_service.generate_json(SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        logger.error(f"LLM generation failed: {e}")
        return {"error": f"LLM generation failed: {str(e)}", "date": date}

    knowledge = llm_result.get("knowledge_gained", [])
    high_knowledge = [k for k in knowledge if k.get("importance") == "high"]

    recap = {
        "date": date,
        "generated_at": datetime.now().isoformat(),
        "model_used": llm_service._default,
        "summary": llm_result.get("summary", ""),
        "key_decisions": llm_result.get("key_decisions", []),
        "knowledge_gained": knowledge,
        "patterns_observed": llm_result.get("patterns_observed", []),
        "issues_encountered": llm_result.get("issues_encountered", []),
        "todos_extracted": llm_result.get("todos_extracted", []),
        "high_knowledge_count": len(high_knowledge),
        "stats": {
            "sessions": daily.total_sessions,
            "tokens": daily.total_tokens,
            "files_modified": len(daily.files_modified),
            "projects": daily.unique_projects,
        },
        "sessions_detail": [
            {
                "session_id": s.session_id,
                "project": s.project,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "tools_used": s.tools_used,
            }
            for s in daily.sessions
        ],
    }

    recap_file = get_recap_dir() / f"{date}.json"
    recap_file.write_text(json.dumps(recap, ensure_ascii=False, indent=2), encoding="utf-8")

    return recap
