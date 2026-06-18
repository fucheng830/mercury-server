"""Aggregate Claude Code JSONL sessions by date."""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class SessionSummary:
    session_id: str
    project: str
    start_time: str
    end_time: str
    model: str
    token_usage: Dict[str, int]
    files_modified: List[str] = field(default_factory=list)
    commands_run: List[str] = field(default_factory=list)
    tools_used: Dict[str, int] = field(default_factory=dict)
    user_prompts: List[str] = field(default_factory=list)
    assistant_summaries: List[str] = field(default_factory=list)


@dataclass
class DailySummary:
    date: str
    total_sessions: int
    total_tokens: int
    sessions: List[SessionSummary]
    unique_projects: List[str]
    files_modified: List[str]
    key_operations: List[str]


def _parse_timestamp(ts) -> Optional[datetime]:
    """Parse timestamp from JSONL (ms epoch or ISO string). Returns naive datetime."""
    if not ts:
        return None
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
        elif isinstance(ts, str):
            dt = datetime.fromisoformat(ts)
        else:
            return None
        # Strip timezone info to ensure naive datetime for comparison
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except (ValueError, TypeError, OSError):
        pass
    return None


def _extract_tool_info(content_blocks: list) -> tuple:
    """Extract tool names, files, commands from assistant content blocks."""
    tools_used = {}
    files_modified = []
    commands_run = []

    for block in content_blocks:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name = block.get("name", "")
        tools_used[name] = tools_used.get(name, 0) + 1
        inp = block.get("input", {})

        if name == "Bash":
            cmd = inp.get("command", "")
            if cmd:
                commands_run.append(cmd[:200])
        elif name in ("Edit", "Write"):
            fp = inp.get("file_path", "")
            if fp:
                files_modified.append(fp)

    return tools_used, files_modified, commands_run


def summarize_session(
    messages: List[Dict[str, Any]],
    session_id: str,
    project_path: str,
) -> Optional[SessionSummary]:
    """Build a SessionSummary from raw JSONL messages for one session."""
    timestamps = []
    model = ""
    total_input = 0
    total_output = 0
    files_modified = []
    commands_run = []
    tools_used = {}
    user_prompts = []
    assistant_summaries = []

    for msg in messages:
        msg_type = msg.get("type")
        ts = _parse_timestamp(msg.get("timestamp"))
        if ts:
            timestamps.append(ts)

        if msg_type == "user":
            content = msg.get("message", {}).get("content", "")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        parts.append(c.get("text", ""))
                text = " ".join(parts)
            if text:
                user_prompts.append(text[:300])

        elif msg_type == "assistant":
            message_data = msg.get("message", {})
            model = message_data.get("model", model)
            usage = message_data.get("usage", {})
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)

            content_blocks = message_data.get("content", [])
            if isinstance(content_blocks, list):
                t, f, c = _extract_tool_info(content_blocks)
                for k, v in t.items():
                    tools_used[k] = tools_used.get(k, 0) + v
                files_modified.extend(f)
                commands_run.extend(c)

                text_parts = [
                    b.get("text", "") for b in content_blocks
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
                ]
                summary_text = " ".join(text_parts)[:300]
                if summary_text:
                    assistant_summaries.append(summary_text)

    if not timestamps:
        return None

    return SessionSummary(
        session_id=session_id,
        project=project_path,
        start_time=min(timestamps).isoformat(),
        end_time=max(timestamps).isoformat(),
        model=model,
        token_usage={"input": total_input, "output": total_output},
        files_modified=list(dict.fromkeys(files_modified)),
        commands_run=commands_run,
        tools_used=tools_used,
        user_prompts=user_prompts,
        assistant_summaries=assistant_summaries,
    )


def aggregate_daily(
    provider,  # ClaudeProvider instance
    target_date: str,
) -> Optional[DailySummary]:
    """Aggregate all sessions for a specific date (YYYY-MM-DD)."""
    # Try filesystem provider first
    daily = _aggregate_from_provider(provider, target_date)
    if daily:
        return daily

    # Fallback to database
    return _aggregate_from_db(target_date)


def _aggregate_from_provider(provider, target_date: str) -> Optional[DailySummary]:
    """Aggregate from filesystem provider."""
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    day_start = target_dt.replace(hour=0, minute=0, second=0)
    day_end = target_dt.replace(hour=23, minute=59, second=59)

    projects_dir = provider.root / "projects"
    if not projects_dir.exists():
        return None

    sessions = []
    all_files = []
    all_operations = []
    projects_set = set()

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        project_path = project_dir.name
        for sf in project_dir.glob("*.jsonl"):
            msgs = provider._read_jsonl(sf, limit=3)
            for m in msgs:
                cwd = m.get("cwd", "")
                if cwd:
                    project_path = cwd
                    break
            break

        for session_file in project_dir.glob("*.jsonl"):
            messages = provider._read_jsonl(session_file)
            has_match = False
            for m in messages:
                ts = _parse_timestamp(m.get("timestamp"))
                if ts and day_start <= ts <= day_end:
                    has_match = True
                    break

            if not has_match:
                continue

            summary = summarize_session(messages, session_file.stem, project_path)
            if summary:
                sessions.append(summary)
                all_files.extend(summary.files_modified)
                all_operations.extend(summary.commands_run)
                projects_set.add(summary.project)

    if not sessions:
        return None

    total_tokens = sum(
        s.token_usage.get("input", 0) + s.token_usage.get("output", 0)
        for s in sessions
    )

    return DailySummary(
        date=target_date,
        total_sessions=len(sessions),
        total_tokens=total_tokens,
        sessions=sessions,
        unique_projects=sorted(projects_set),
        files_modified=list(dict.fromkeys(all_files)),
        key_operations=all_operations,
    )


def _aggregate_from_db(target_date: str) -> Optional[DailySummary]:
    """Aggregate sessions from PostgreSQL for a given date."""
    from hermes.db import execute

    rows = execute(
        "SELECT session_id, project_id, project_path, preview, "
        "message_count, token_input, token_output, "
        "first_ts, last_ts "
        "FROM sessions "
        "WHERE ((last_ts AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai')::date = %s::date "
        "       OR (last_ts IS NULL AND (file_mtime AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai')::date = %s::date)) "
        "ORDER BY last_ts",
        (target_date, target_date),
        fetch=True,
    )
    if not rows:
        return None

    sessions = []
    projects_set = set()

    for r in rows:
        ts_in = r.get("token_input") or 0
        ts_out = r.get("token_output") or 0
        project = r.get("project_path") or r.get("project_id", "")
        projects_set.add(project)

        sessions.append(SessionSummary(
            session_id=r.get("session_id", ""),
            project=project,
            start_time=r.get("first_ts").isoformat() if r.get("first_ts") else "",
            end_time=r.get("last_ts").isoformat() if r.get("last_ts") else "",
            model="",
            token_usage={"input": ts_in, "output": ts_out},
            files_modified=[],
            commands_run=[],
            tools_used={},
            user_prompts=[r.get("preview", "")] if r.get("preview") else [],
            assistant_summaries=[],
        ))

    total_tokens = sum(
        s.token_usage.get("input", 0) + s.token_usage.get("output", 0)
        for s in sessions
    )

    return DailySummary(
        date=target_date,
        total_sessions=len(sessions),
        total_tokens=total_tokens,
        sessions=sessions,
        unique_projects=sorted(projects_set),
        files_modified=[],
        key_operations=[],
    )


def get_available_dates(provider, months: int = 3) -> List[Dict[str, Any]]:
    """Get list of dates that have sessions, for calendar view."""
    # Try filesystem first
    result = _get_dates_from_provider(provider, months)
    if result:
        return result
    # Fallback to database
    return _get_dates_from_db(months)


def _get_dates_from_provider(provider, months: int = 3) -> List[Dict[str, Any]]:
    from datetime import timedelta
    projects_dir = provider.root / "projects"
    if not projects_dir.exists():
        return []

    cutoff = datetime.now() - timedelta(days=months * 31)
    date_sessions = {}

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        for session_file in project_dir.glob("*.jsonl"):
            messages = provider._read_jsonl(session_file, limit=50)
            for m in messages:
                ts = _parse_timestamp(m.get("timestamp"))
                if ts and ts >= cutoff:
                    day_str = ts.strftime("%Y-%m-%d")
                    date_sessions[day_str] = date_sessions.get(day_str, 0) + 1

    return [
        {"date": d, "session_count": c}
        for d, c in sorted(date_sessions.items(), reverse=True)
    ]


def _get_dates_from_db(months: int = 3) -> List[Dict[str, Any]]:
    from hermes.db import execute

    rows = execute(
        "SELECT (COALESCE(last_ts, file_mtime) AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Shanghai')::date as day, COUNT(*) as cnt "
        "FROM sessions "
        "WHERE COALESCE(last_ts, file_mtime) >= now() - interval '%s months' "
        "GROUP BY day ORDER BY day DESC",
        (months,),
        fetch=True,
    )
    if not rows:
        return []
    return [
        {"date": str(r["day"]), "session_count": r["cnt"]}
        for r in rows
    ]
