"""Stateless Streamable-HTTP MCP server (JSON-RPC) for mercury memory tools.

Mounted at /mcp. Stateless: every request is handled independently — no session
storage, no Mcp-Session-Id header, no SSE channel. Each POST carries one
JSON-RPC message (or a batch); single Requests get a JSON-RPC Response with
Content-Type application/json, Notifications get 202 with no body.

Zero extra dependencies — only FastAPI/Starlette (already required). Avoids the
`mcp` SDK so no Docker image rebuild is needed (code is bind-mounted).

Tools:
  - recall_memory(project, limit, min_importance): a project's active memories (the
    SessionStart-injection set), resolved by path OR name.
  - search_memory(query, limit): hybrid FTS + vector (RRF) across all memories.
  - read_memory(memory_id): one memory by id.
  - list_memory(stage, type, search, size): filtered, paginated list.
  - memory_stats(): active/archived counts by stage.
"""
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from fastapi import Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "mercury-memory", "version": "1.0.0"}


# ── tool handlers (return JSON-serializable dicts) ──────────────────────────

def _recall(project: str, limit: int = 30, min_importance: int = 1) -> Dict[str, Any]:
    from hermes.memory_service import recall_memories
    items = recall_memories(project, limit=limit, min_importance=min_importance)
    return {"project": project, "count": len(items), "items": items}


def _search(query: str, limit: int = 10) -> Dict[str, Any]:
    from hermes.memory_service import search_memories
    return {"query": query, "results": search_memories(query, limit=limit)}


def _read(memory_id: str) -> Dict[str, Any]:
    from hermes.memory_service import get_memory
    m = get_memory(memory_id)
    return m if m is not None else {"error": "memory not found", "id": memory_id}


def _list(stage: str = "memory", type: str = "", search: str = "", size: int = 25) -> Dict[str, Any]:
    from hermes.memory_service import list_memories
    res = list_memories(
        stage=stage or None,
        types=[type] if type else None,
        search=search or None,
        size=size,
    )
    return res


def _stats() -> Dict[str, Any]:
    from hermes.memory_service import get_memory_stats
    return get_memory_stats()


def _associate(query: str, limit: int = 10, hops: int = 1,
               cross_project: bool = True, min_importance: int = 1,
               project: Optional[str] = None) -> Dict[str, Any]:
    from hermes.memory_service import associate
    return associate(query, limit=limit, hops=hops, cross_project=cross_project,
                     min_importance=min_importance, project=project)


TOOLS: List[Dict[str, Any]] = [
    {
        "name": "recall_memory",
        "description": (
            "Recall a project's active, injectable memories (Top N by importance) — the "
            "durable context an agent should see at session start. Project is resolved by "
            "path OR name, lookup only (never creates). Returns [] if the project is unknown."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "project path or name, e.g. 'mercury-server'"},
                "limit": {"type": "integer", "default": 30, "minimum": 1, "maximum": 100},
                "min_importance": {"type": "integer", "default": 1, "minimum": 1, "maximum": 5},
            },
            "required": ["project"],
        },
    },
    {
        "name": "search_memory",
        "description": "Hybrid search (full-text + vector, RRF) across all active memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_memory",
        "description": "Read a single memory by id (full content + metadata).",
        "inputSchema": {
            "type": "object",
            "properties": {"memory_id": {"type": "string"}},
            "required": ["memory_id"],
        },
    },
    {
        "name": "list_memory",
        "description": "Filtered, paginated memory list (default: active memory-stage).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stage": {"type": "string", "default": "memory", "enum": ["observation", "candidate", "memory"]},
                "type": {"type": "string", "description": "e.g. ARCH / BUGFIX / DECISION / DISCOVERY / PREFERENCE"},
                "search": {"type": "string"},
                "size": {"type": "integer", "default": 25, "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "memory_stats",
        "description": "Active/archived memory counts by stage (current namespace).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "associate_memory",
        "description": (
            "Associative recall across the concept-hub graph: match the query to concept hubs, "
            "return memories linked to them — crossing projects by default (knowledge compounding: "
            "recall A and trigger B/C/D via shared concepts). Pull-based: call when a concept/problem "
            "surfaces, unlike recall_memory (project push)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "concept / problem to associate on"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
                "hops": {"type": "integer", "default": 1, "minimum": 1, "maximum": 2},
                "cross_project": {"type": "boolean", "default": True},
                "project": {"type": "string", "description": "if cross_project=false, restrict to this project path/name"},
            },
            "required": ["query"],
        },
    },
]

_HANDLERS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "recall_memory": _recall,
    "search_memory": _search,
    "read_memory": _read,
    "list_memory": _list,
    "memory_stats": _stats,
    "associate_memory": _associate,
}


# ── JSON-RPC helpers ────────────────────────────────────────────────────────

def _result(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _is_request(msg: Any) -> bool:
    return isinstance(msg, dict) and "id" in msg and isinstance(msg.get("method"), str)


def _call_tool(req_id: Any, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    handler = _HANDLERS.get(name)
    if handler is None:
        return _error(req_id, -32601, f"Unknown tool: {name}")
    try:
        data = handler(**(args or {}))
    except TypeError as e:
        return _error(req_id, -32602, f"Invalid params: {e}")
    except Exception as e:
        logger.exception("MCP tool '%s' failed", name)
        return _error(req_id, -32603, f"Tool error: {e}")
    text = json.dumps(data, ensure_ascii=False, default=str)
    return _result(req_id, {"content": [{"type": "text", "text": text}]})


def dispatch(msg: Any) -> Optional[Dict[str, Any]]:
    """Dispatch one JSON-RPC message. Return a Response dict, or None for notifications."""
    if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
        if _is_request(msg):
            return _error(msg.get("id"), -32600, "Invalid Request")
        return None  # malformed notification / not a dict

    method = msg["method"]
    req_id = msg.get("id")  # None => notification (no response)
    params = msg.get("params") or {}

    if method == "initialize":
        return _result(req_id, {
            "protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _result(req_id, {})

    if req_id is None:
        return None  # notification for an unsupported method — no response

    if method == "tools/list":
        return _result(req_id, {"tools": TOOLS})
    if method == "tools/call":
        return _call_tool(req_id, params.get("name", ""), params.get("arguments") or {})
    return _error(req_id, -32601, f"Method not found: {method}")


# ── HTTP entrypoint ─────────────────────────────────────────────────────────

async def handle_mcp(request: Request) -> Response:
    """Handle a streamable-HTTP POST (single message or batch)."""
    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400, content="invalid JSON")

    if isinstance(payload, list):
        if not payload:
            return _jsonrpc(_error(None, -32600, "Invalid Request"))
        responses = [r for r in (dispatch(m) for m in payload) if r is not None]
        if not responses:
            return Response(status_code=202)
        return _jsonrpc(responses)

    resp = dispatch(payload)
    if resp is None:
        return Response(status_code=202)  # notification
    return _jsonrpc(resp)


def _jsonrpc(body: Any) -> JSONResponse:
    return JSONResponse(content=body, media_type="application/json")
