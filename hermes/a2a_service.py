"""A2A protocol service: Agent Card, auth, and skill routing."""
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import bcrypt

from hermes.memory_service import (
    write_memory, read_memories, search_memories,
    share_memory, get_memory, get_memory_stats, get_agent,
    search_sessions, get_session, read_sessions, share_session,
)

logger = logging.getLogger(__name__)


# ── Agent Card ─────────────────────────────────────────────────────────────

def get_agent_card(base_url: str = "http://localhost:8788") -> Dict:
    """Return the Mercury A2A Agent Card."""
    return {
        "name": "Mercury Memory Agent",
        "description": (
            "Cross-agent long-term memory middleware. "
            "Provides memory write, search, read, and share capabilities."
        ),
        "url": base_url.rstrip("/"),
        "version": "2.0.0",
        "provider": {"name": "Mercury"},
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
        },
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "securitySchemes": {
            "bearer": {"scheme": "bearer"},
        },
        "skills": [
            {
                "id": "memory.write",
                "name": "Write Memory",
                "description": (
                    "Store a memory with stage (observation/candidate/memory), "
                    "importance (1-5), type, and optional tags."
                ),
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "memory.search",
                "name": "Search Memory",
                "description": (
                    "Hybrid search (vector + full-text) with Reciprocal Rank Fusion. "
                    "Searches own namespace + shared by default."
                ),
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "memory.read",
                "name": "Read Memories",
                "description": "Paginated read of memories, filterable by stage.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "memory.share",
                "name": "Share Memory",
                "description": "Move a memory to the shared namespace for all agents.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "session.search",
                "name": "Search Sessions",
                "description": "Search Claude Code sessions by content (vector similarity). Returns matching sessions with metadata.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "session.read",
                "name": "Read Sessions",
                "description": "Paginated read of session metadata, filterable by project.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "session.share",
                "name": "Share Session",
                "description": "Move a session to the shared namespace, making it visible to all agents.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "bounty.create",
                "name": "Create Bounty",
                "description": "Post a token bounty for an unanswered question. Locks amount from creator into escrow.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "bounty.list",
                "name": "List Bounties",
                "description": "List open bounties (optional framework filter).",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "bounty.claim",
                "name": "Claim Bounty",
                "description": "Claim an open bounty to work on answering it.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "bounty.answer",
                "name": "Answer Bounty",
                "description": "Submit an answer for a claimed bounty. Rewards solver (amount + 20% bonus) and sinks solution to memory.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "bounty.match",
                "name": "Match Bounty",
                "description": "Find namespaces with relevant memory for a bounty (RRF push-matching).",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "bounty.accept",
                "name": "Accept Bounty Answer",
                "description": "Creator accepts a pending answer. Rewards solver (amount + 20% bonus) and promotes the solution memory.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
            {
                "id": "bounty.reject",
                "name": "Reject Bounty Answer",
                "description": "Creator rejects a pending answer. No reward; solution memory archived; bounty reopens for re-claiming.",
                "inputModes": ["text"],
                "outputModes": ["text"],
            },
        ],
    }


# ── Auth ────────────────────────────────────────────────────────────────────

def validate_agent(agent_id: str, token: str) -> Optional[Dict]:
    """Authenticate agent by token and return agent record with permissions.

    Returns None if auth fails.
    """
    agent = get_agent(agent_id)
    if not agent:
        logger.warning("Agent not found: %s", agent_id)
        return None
    if not agent.get("enabled", True):
        logger.warning("Agent disabled: %s", agent_id)
        return None

    stored = agent.get("auth_credentials", "")
    if stored:
        try:
            if not bcrypt.checkpw(token.encode("utf-8"), stored.encode("utf-8")):
                logger.warning("Invalid token for agent %s", agent_id)
                return None
        except Exception:
            logger.warning("Bad auth_credentials for agent %s", agent_id)
            return None

    return agent


# ── Skill Routing ──────────────────────────────────────────────────────────

def handle_send_message(message: Dict, agent_id: str) -> Dict:
    """Parse A2A message, route to the correct skill, return result.

    Expects message.parts[0].text to be JSON with "skill" and "params" keys.

    Returns:
        {"status": "completed", "result": ...} or {"status": "failed", "error": ...}
    """
    parts = message.get("parts", [])
    if not parts:
        return _error("Empty message parts")

    text = ""
    for part in parts:
        if isinstance(part, dict):
            text += part.get("text", "")

    if not text:
        return _error("No text content in message parts")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return _error("Invalid JSON in message body")

    skill = payload.get("skill", "")
    params = payload.get("params", {})

    if not skill:
        return _error("Missing 'skill' field")

    try:
        if skill == "memory.write":
            return _handle_write(agent_id, params)
        elif skill == "memory.search":
            return _handle_search(agent_id, params)
        elif skill == "memory.read":
            return _handle_read(agent_id, params)
        elif skill == "memory.share":
            return _handle_share(agent_id, params)
        elif skill == "session.search":
            return _handle_session_search(agent_id, params)
        elif skill == "session.read":
            return _handle_session_read(agent_id, params)
        elif skill == "session.share":
            return _handle_session_share(agent_id, params)
        elif skill == "bounty.create":
            return _handle_bounty_create(agent_id, params)
        elif skill == "bounty.list":
            return _handle_bounty_list(agent_id, params)
        elif skill == "bounty.claim":
            return _handle_bounty_claim(agent_id, params)
        elif skill == "bounty.answer":
            return _handle_bounty_answer(agent_id, params)
        elif skill == "bounty.match":
            return _handle_bounty_match(agent_id, params)
        elif skill == "bounty.accept":
            return _handle_bounty_accept(agent_id, params)
        elif skill == "bounty.reject":
            return _handle_bounty_reject(agent_id, params)
        else:
            return _error(f"Unknown skill: {skill}. Available: memory.write/search/read/share, session.search/read/share, bounty.create/list/claim/answer/match/accept/reject")
    except ValueError as e:
        return _error(str(e))
    except Exception as e:
        logger.exception("Skill handler error for %s", skill)
        return _error(f"Internal error: {str(e)}")


def _handle_write(agent_id: str, params: Dict) -> Dict:
    content = params.get("content", "")
    if not content:
        raise ValueError("'content' is required for memory.write")

    memory = write_memory(
        content=content,
        stage=params.get("stage", "observation"),
        source="a2a",
        importance=params.get("importance", 3),
        type=params.get("type", "NOTE"),
        scope=params.get("scope", "global"),
        tags=params.get("tags", []),
        summary=params.get("summary"),
        auto_embed=params.get("auto_embed", True),
        namespace=agent_id,
    )
    return _ok({"memory_id": memory.get("id"), "stage": memory.get("stage"), "type": memory.get("type")})


def _handle_search(agent_id: str, params: Dict) -> Dict:
    query = params.get("query", "")
    if not query:
        raise ValueError("'query' is required for memory.search")

    namespaces = [agent_id, "shared"]
    results = search_memories(
        query_text=query,
        stage=params.get("stage"),
        limit=params.get("limit", 20),
        namespaces=namespaces,
    )
    return _ok({"memories": results, "total": len(results)})


def _handle_read(agent_id: str, params: Dict) -> Dict:
    memories = read_memories(
        stage=params.get("stage"),
        limit=params.get("limit", 50),
        offset=params.get("offset", 0),
        namespace=agent_id,
    )
    return _ok({"memories": memories, "total": len(memories)})


def _handle_share(agent_id: str, params: Dict) -> Dict:
    memory_id = params.get("memory_id", "")
    if not memory_id:
        raise ValueError("'memory_id' is required for memory.share")

    ok = share_memory(memory_id, owner_namespace=agent_id)
    return _ok({"shared": ok})


def _handle_session_search(agent_id: str, params: Dict) -> Dict:
    query = params.get("query", "")
    if not query:
        raise ValueError("'query' is required for session.search")

    results = search_sessions(
        query_text=query,
        namespace=agent_id,
        limit=params.get("limit", 20),
    )
    return _ok({"sessions": results, "total": len(results)})


def _handle_session_read(agent_id: str, params: Dict) -> Dict:
    results = read_sessions(
        namespace=agent_id,
        limit=params.get("limit", 50),
        offset=params.get("offset", 0),
        project_id=params.get("project_id"),
    )
    return _ok({"sessions": results, "total": len(results)})


def _handle_session_share(agent_id: str, params: Dict) -> Dict:
    session_id = params.get("session_id", "")
    if not session_id:
        raise ValueError("'session_id' is required for session.share")

    # Accept either DB UUID or session_id (JSONL filename stem)
    from hermes.db import execute_one
    row = execute_one("SELECT id FROM sessions WHERE id = %s", (session_id,))
    if not row:
        row = execute_one(
            "SELECT id FROM sessions WHERE session_id = %s AND namespace = %s",
            (session_id, agent_id),
        )
    if not row:
        return _ok({"shared": False, "error": "Session not found"})

    ok = share_session(row["id"], owner_namespace=agent_id)
    return _ok({"shared": ok})


# ── Bounty skill handlers (C-stage economy) ─────────────────────────────────

def _handle_bounty_create(agent_id: str, params: Dict) -> Dict:
    from hermes.bounty_service import create_bounty
    question = params.get("question", "")
    amount = params.get("amount") or params.get("bounty_amount") or 0
    if not question or not amount:
        raise ValueError("'question' and 'amount' are required for bounty.create")
    bounty = create_bounty(
        question=question,
        amount=int(amount),
        creator_namespace=agent_id,
        framework=params.get("framework"),
        expires_in_hours=params.get("expires_in_hours", 24),
    )
    return _ok({
        "bounty_id": str(bounty["id"]),
        "amount": bounty["amount"],
        "status": bounty["status"],
        "expires_at": bounty["expires_at"].isoformat() if bounty.get("expires_at") else None,
    })


def _handle_bounty_list(agent_id: str, params: Dict) -> Dict:
    from hermes.bounty_service import list_bounties
    bounties = list_bounties(
        status=params.get("status", "open"),
        framework=params.get("framework"),
        limit=params.get("limit", 20),
    )
    return _ok({"bounties": bounties, "total": len(bounties)})


def _handle_bounty_claim(agent_id: str, params: Dict) -> Dict:
    from hermes.bounty_service import claim_bounty
    bid = params.get("bounty_id", "")
    if not bid:
        raise ValueError("'bounty_id' is required for bounty.claim")
    claimed = claim_bounty(bid, agent_id)
    if not claimed:
        return _ok({"claimed": False, "error": "Bounty not found or not open"})
    return _ok({"claimed": True, "bounty_id": str(claimed["id"])})


def _handle_bounty_answer(agent_id: str, params: Dict) -> Dict:
    from hermes.bounty_service import answer_bounty
    bid = params.get("bounty_id", "")
    solution = params.get("solution", "")
    if not bid or not solution:
        raise ValueError("'bounty_id' and 'solution' are required for bounty.answer")
    result = answer_bounty(bid, solution, agent_id)
    if not result:
        return _ok({"resolved": False, "error": "Bounty not found or not claimed by you"})
    return _ok({"resolved": True, **result})


def _handle_bounty_match(agent_id: str, params: Dict) -> Dict:
    from hermes.bounty_service import match_bounty
    bid = params.get("bounty_id", "")
    if not bid:
        raise ValueError("'bounty_id' is required for bounty.match")
    matches = match_bounty(bid, limit=params.get("limit", 5))
    return _ok({"bounty_id": bid, "matches": matches, "total": len(matches)})


def _handle_bounty_accept(agent_id: str, params: Dict) -> Dict:
    from hermes.bounty_service import accept_bounty
    bid = params.get("bounty_id", "")
    if not bid:
        raise ValueError("'bounty_id' is required for bounty.accept")
    result = accept_bounty(bid, agent_id)  # creator-only (agent_id = creator namespace)
    if not result:
        return _ok({"accepted": False, "error": "Bounty not found, not in 'answered' state, or you are not the creator"})
    return _ok({"accepted": True, **result})


def _handle_bounty_reject(agent_id: str, params: Dict) -> Dict:
    from hermes.bounty_service import reject_bounty
    bid = params.get("bounty_id", "")
    if not bid:
        raise ValueError("'bounty_id' is required for bounty.reject")
    result = reject_bounty(bid, agent_id)
    if not result:
        return _ok({"rejected": False, "error": "Bounty not found, not in 'answered' state, or you are not the creator"})
    return _ok({"rejected": True, **result})


# ── Response Helpers ───────────────────────────────────────────────────────

def _ok(data: Dict) -> Dict:
    return {"status": "completed", **data}


def _error(message: str) -> Dict:
    return {"status": "failed", "error": message}
