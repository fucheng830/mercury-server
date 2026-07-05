"""Claude History Viewer - FastAPI Backend"""
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import sys

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager

from providers import get_provider, list_sources

from recap_config import get_llm_config, reload_config
from recap.aggregator import aggregate_daily, get_available_dates
from recap.llm_service import LLMService
from recap.recap_engine import generate_recap, load_recap, list_recaps
from recap.scheduler import start_scheduler, stop_scheduler


def get_base_path():
    """获取资源文件基础路径，兼容 PyInstaller 和普通运行"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


# ── Recap & Memory Init ──
try:
    _llm_service = LLMService(get_llm_config())
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(f"LLM service init failed: {e}")
    _llm_service = None


def _get_claude_provider():
    return get_provider("claude")


@asynccontextmanager
async def _lifespan(app):
    import logging
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)

    logger.info("Lifespan startup begin")

    try:
        from hermes.db import init_db
        init_db()
        logger.info("DB init done")
    except Exception as e:
        logger.warning(f"DB init failed: {e}")

    logger.info(f"LLM service: {_llm_service is not None}")
    if _llm_service:
        start_scheduler(_get_claude_provider(), _llm_service)
        logger.info("Scheduler started")
    else:
        logger.warning("No LLM service, scheduler not started")

    yield
    stop_scheduler()
    logger.info("Lifespan shutdown")


app = FastAPI(title="Claude History Viewer", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def provider_or_404(source: str):
    provider = get_provider(source)
    if provider is None or not provider.available():
        raise HTTPException(404, "Source not found/unavailable")
    return provider


def legacy_claude_provider_or_404():
    provider = get_provider("claude")
    if provider is None:
        raise HTTPException(404, "Source not found/unavailable")
    return provider


def route_provider(source: Optional[str] = None):
    if source is None:
        return legacy_claude_provider_or_404()
    return provider_or_404(source)


def call_provider(source: Optional[str], method_name: str, *args):
    provider = route_provider(source)
    return getattr(provider, method_name)(*args)


def _not_found_as_http404(callback):
    try:
        return callback()
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


# ── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/sources")
def get_sources():
    return list_sources()


@app.get("/api/stats")
def get_stats():
    """Get overview statistics."""
    return call_provider(None, "get_stats")


@app.get("/api/dashboard-stats")
def get_dashboard_stats(range: str = Query("30d", pattern="^(7d|30d|all)$")):
    """Get comprehensive dashboard statistics."""
    return call_provider(None, "get_dashboard_stats", range)


@app.get("/api/recent-sessions")
def get_recent_sessions(limit: int = Query(5, ge=1, le=20)):
    """Get most recent sessions across all projects."""
    return call_provider(None, "get_recent_sessions", limit)


@app.get("/api/history")
def get_history(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
):
    """Get command history with pagination and filtering."""
    return call_provider(None, "get_history", page, limit, search, project)


@app.get("/api/plans")
def get_plans():
    """List all plans."""
    return call_provider(None, "get_plans")


@app.get("/api/plans/{name}")
def get_plan(name: str):
    """Get a specific plan's content."""
    return _not_found_as_http404(lambda: call_provider(None, "get_plan", name))


@app.get("/api/projects")
def get_projects():
    """List all projects."""
    return call_provider(None, "list_projects")


@app.get("/api/projects/{project_id}")
def get_project_detail(project_id: str):
    """Get project details with sessions sorted by modification time."""
    return _not_found_as_http404(lambda: call_provider(None, "get_project", project_id))


@app.get("/api/projects/{project_id}/sessions")
def get_project_sessions(project_id: str):
    """List sessions for a project."""
    return _not_found_as_http404(lambda: call_provider(None, "list_sessions", project_id))


@app.get("/api/projects/{project_id}/sessions/{session_id}")
def get_session_conversation(project_id: str, session_id: str):
    """Get a session's conversation as a reconstructed thread."""
    return _not_found_as_http404(lambda: call_provider(None, "get_session", project_id, session_id))


@app.get("/api/projects/{project_id}/sessions/{session_id}/subagents/{agent_file}")
def get_subagent_conversation(project_id: str, session_id: str, agent_file: str):
    """Get a subagent's conversation."""
    return _not_found_as_http404(
        lambda: call_provider(None, "get_subagent", project_id, session_id, agent_file)
    )


# ── Memory API (PostgreSQL) ──────────────────────────────────────────────────
# NOTE: These MUST be registered before /api/{source}/* routes to avoid conflicts

@app.post("/api/memory/query")
def memory_query(body: dict):
    """Hybrid search across memories (RRF), with multi-dimension filters."""
    try:
        from hermes.memory_service import search_memories
        query = body.get("query", "")
        results = search_memories(
            query,
            stage=body.get("stage"),
            types=body.get("types"),
            scopes=body.get("scopes"),
            statuses=body.get("statuses"),
            project_id=body.get("project_id"),
            limit=body.get("limit", 20),
            offset=body.get("offset", 0),
        )
        return {"results": results}
    except Exception as e:
        raise HTTPException(503, f"Memory search error: {str(e)}")


@app.get("/api/memory/stats")
def memory_stats():
    try:
        from hermes.memory_service import get_memory_stats
        return get_memory_stats()
    except Exception as e:
        raise HTTPException(503, f"Memory stats error: {str(e)}")


@app.get("/api/memory/recent")
def memory_recent(limit: int = Query(20, ge=1, le=100), stage: Optional[str] = Query(None)):
    try:
        from hermes.db import execute
        if stage:
            rows = execute(
                "SELECT id, stage, type, scope, status, project_id, content, summary, source, "
                "importance, tags, recall_count, created_at, updated_at "
                "FROM memories WHERE stage = %s ORDER BY created_at DESC LIMIT %s",
                (stage, limit),
                fetch=True,
            )
        else:
            rows = execute(
                "SELECT id, stage, type, scope, status, project_id, content, summary, source, "
                "importance, tags, recall_count, created_at, updated_at "
                "FROM memories ORDER BY created_at DESC LIMIT %s",
                (limit,),
                fetch=True,
            )
        return rows or []
    except Exception as e:
        raise HTTPException(503, f"Memory recent error: {str(e)}")


@app.get("/api/memory/list")
def memory_list(
    stage: Optional[str] = Query(None),
    types: Optional[str] = Query(None),
    scopes: Optional[str] = Query(None),
    statuses: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    namespace: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    sort: str = Query("updated_at"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=200),
):
    """Multi-filter paginated list for the 'All memories' view."""
    try:
        from hermes.memory_service import list_memories

        def _split(v: Optional[str]):
            return [x.strip() for x in v.split(",") if x.strip()] if v else None

        return list_memories(
            stage=stage,
            types=_split(types),
            scopes=_split(scopes),
            statuses=_split(statuses),
            project_id=project_id,
            namespaces=_split(namespace),
            search=search,
            sort=sort,
            order=order,
            page=page,
            size=size,
        )
    except Exception as e:
        raise HTTPException(503, f"Memory list error: {str(e)}")


@app.get("/api/memory/candidates")
def memory_candidates(
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=200),
):
    """Candidate review queue (stage=candidate)."""
    try:
        from hermes.memory_service import list_memories
        return list_memories(stage="candidate", page=page, size=size)
    except Exception as e:
        raise HTTPException(503, f"Candidates error: {str(e)}")


@app.get("/api/memory/observations")
def memory_observations(
    page: int = Query(1, ge=1),
    size: int = Query(25, ge=1, le=200),
):
    """Raw observations (stage=observation)."""
    try:
        from hermes.memory_service import list_memories
        return list_memories(stage="observation", page=page, size=size)
    except Exception as e:
        raise HTTPException(503, f"Observations error: {str(e)}")


@app.post("/api/memory/{memory_id}/confirm")
def memory_confirm(memory_id: str, body: dict = None):
    """Human confirmation gate: candidate -> memory (may override type/scope/project)."""
    try:
        from hermes.memory_service import confirm_candidate
        body = body or {}
        ok = confirm_candidate(
            memory_id,
            type=body.get("type"),
            scope=body.get("scope"),
            project_id=body.get("project_id"),
        )
        return {"success": ok}
    except Exception as e:
        raise HTTPException(503, f"Confirm error: {str(e)}")


@app.post("/api/memory/{memory_id}/reject")
def memory_reject(memory_id: str):
    """Reject a candidate: status -> archived."""
    try:
        from hermes.memory_service import reject_candidate
        return {"success": reject_candidate(memory_id)}
    except Exception as e:
        raise HTTPException(503, f"Reject error: {str(e)}")


@app.post("/api/memory/{memory_id}/refute")
def memory_refute(memory_id: str, body: dict):
    """Link a refutation (memory or session span) to a memory, then re-run the
    threshold gate — demotes immediately if threshold is met."""
    from hermes.counterexample_service import add_refutation, demote_by_threshold
    namespace = (body or {}).get("namespace", "claude")
    ref = add_refutation(
        target_id=memory_id,
        reason=(body or {}).get("reason", ""),
        refuting_id=(body or {}).get("refuting_id"),
        session_ref=(body or {}).get("session_ref"),
        source=(body or {}).get("source", "agent"),
        confidence=(body or {}).get("confidence"),
        namespace=namespace,
    )
    gate = demote_by_threshold(memory_id, namespace=namespace)
    return {"refuted": ref is not None, "refutation": ref, "gate": gate}


@app.get("/api/memory/{memory_id}/refutations")
def memory_refutations(memory_id: str, namespace: str = "claude"):
    """List refutation links + current gate state for a memory."""
    from hermes.counterexample_service import list_refutations, gate_status
    return {"refutations": list_refutations(memory_id, namespace),
            "gate": gate_status(memory_id, namespace)}


@app.post("/api/memory/{memory_id}/demote")
def memory_demote(memory_id: str, body: dict = None):
    """Explicitly trigger the threshold gate. 409 if below threshold."""
    from hermes.counterexample_service import demote_by_threshold
    namespace = (body or {}).get("namespace", "claude")
    result = demote_by_threshold(memory_id, namespace=namespace)
    if not result["demoted"]:
        raise HTTPException(status_code=409, detail=result)
    return result


@app.post("/api/memory/{memory_id}/restore")
def memory_restore(memory_id: str, body: dict = None):
    """Reverse a demote (status→active, clear valid_to/superseded_by)."""
    from hermes.counterexample_service import restore_memory
    namespace = (body or {}).get("namespace", "claude")
    return {"restored": restore_memory(memory_id, namespace=namespace)}


@app.get("/api/memory/graph")
def memory_graph(
    entity: Optional[str] = Query(None),
    depth: int = Query(1, ge=1, le=3),
    entity_type: Optional[str] = Query(None),
):
    try:
        from hermes.graph_service import get_entity_graph
        return get_entity_graph(entity_name=entity, depth=depth, entity_type=entity_type)
    except Exception as e:
        raise HTTPException(503, f"Graph query error: {str(e)}")


@app.get("/api/memory/read")
def memory_read(target: str = Query("all", pattern="^(memory|user|all)$")):
    try:
        from hermes.memory_service import read_memories
        ns = "claude" if target == "memory" else ("user" if target == "user" else "claude")
        results = read_memories(limit=50, namespace=ns)
        return {"results": results, "target": target}
    except Exception as e:
        raise HTTPException(503, f"Memory read error: {str(e)}")


@app.post("/api/memory/write")
def memory_write(body: dict):
    try:
        from hermes.memory_service import write_memory, delete_memory
        target = body.get("target", "memory")
        action = body.get("action", "add")
        content = body.get("content", "")
        old_text = body.get("old_text", "")
        ns = "claude" if target == "memory" else "user"
        if action == "replace" and old_text:
            delete_memory(old_text, namespace=ns)
        result = write_memory(
            content=content,
            namespace=ns,
            source="mcp",
            stage=body.get("stage", "memory"),
            type=body.get("type", "NOTE"),
            scope=body.get("scope", "global"),
            project_id=body.get("project_id"),
        )
        return {"success": True, "id": result.get("id"), "stage": result.get("stage")}
    except Exception as e:
        raise HTTPException(503, f"Memory write error: {str(e)}")


@app.post("/api/memory/search")
def memory_search(body: dict):
    try:
        from hermes.memory_service import search_memories
        query = body.get("query", "")
        limit = body.get("limit", 5)
        results = search_memories(query, limit=limit)
        return {"results": results}
    except Exception as e:
        raise HTTPException(503, f"Memory search error: {str(e)}")


@app.post("/api/sessions/search")
def sessions_search(body: dict):
    """Message-level hybrid search over session_messages (RRF + context window).

    See docs/superpowers/specs/2026-07-05-message-level-search-design.md.
    """
    try:
        from hermes.messages_service import search_messages
        query = body.get("query", "")
        if not query:
            raise HTTPException(400, "query is required")
        results = search_messages(
            query,
            namespace=body.get("namespace", "claude"),
            limit=body.get("limit", 20),
            offset=body.get("offset", 0),
            context_window=body.get("context_window", 3),
        )
        return {"results": results}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Session search error: {str(e)}")


@app.delete("/api/memory/delete")
def memory_delete(target: str = Query(...), substring: str = Query(...)):
    try:
        from hermes.memory_service import delete_memory
        ns = "claude" if target == "memory" else "user"
        ok = delete_memory(substring, namespace=ns)
        return {"success": ok}
    except Exception as e:
        raise HTTPException(503, f"Memory delete error: {str(e)}")


# ── type_registry CRUD ─────────────────────────────────────────────────────

@app.get("/api/memory/types")
def memory_types(enabled_only: bool = Query(False)):
    try:
        from hermes.memory_service import list_types
        return {"types": list_types(enabled_only=enabled_only)}
    except Exception as e:
        raise HTTPException(503, f"Types error: {str(e)}")


@app.post("/api/memory/types")
def memory_type_upsert(body: dict):
    try:
        from hermes.memory_service import upsert_type
        return upsert_type(
            body.get("name"),
            body.get("label"),
            body.get("color"),
            body.get("sort_order", 0),
            body.get("enabled", True),
        )
    except Exception as e:
        raise HTTPException(503, f"Type upsert error: {str(e)}")


@app.delete("/api/memory/types/{name}")
def memory_type_delete(name: str):
    try:
        from hermes.memory_service import delete_type
        return {"success": delete_type(name)}
    except Exception as e:
        raise HTTPException(503, f"Type delete error: {str(e)}")


# ── projects CRUD ──────────────────────────────────────────────────────────

@app.get("/api/memory/projects")
def memory_projects(namespace: Optional[str] = Query(None)):
    try:
        from hermes.memory_service import list_projects
        return {"projects": list_projects(namespace=namespace)}
    except Exception as e:
        raise HTTPException(503, f"Projects error: {str(e)}")


@app.post("/api/memory/projects")
def memory_project_create(body: dict):
    try:
        from hermes.memory_service import get_or_create_project
        return get_or_create_project(
            body.get("name"),
            body.get("path"),
            body.get("namespace", "claude"),
        )
    except Exception as e:
        raise HTTPException(503, f"Project create error: {str(e)}")


@app.delete("/api/memory/projects/{project_id}")
def memory_project_delete(project_id: str):
    try:
        from hermes.memory_service import delete_project
        return {"success": delete_project(project_id)}
    except Exception as e:
        raise HTTPException(503, f"Project delete error: {str(e)}")


# ── Extract (capture) + Recall (inject) — agent-first ──────────────────────

@app.post("/api/memory/extract")
def memory_extract(body: dict = None):
    """Run the durable-knowledge extractor for a date (across all projects that day)."""
    try:
        from hermes.extractor import extract_for_date
        body = body or {}
        date = body.get("date")
        if not date:
            from datetime import datetime as _dt
            date = _dt.now().strftime("%Y-%m-%d")
        if _llm_service is None:
            raise HTTPException(503, "LLM service unavailable")
        return extract_for_date(date, _llm_service, _get_claude_provider())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Extract error: {str(e)}")


@app.get("/api/memory/recall")
def memory_recall(
    project: str = Query(...),
    namespace: str = Query("claude"),
    limit: int = Query(30, ge=1, le=100),
    min_importance: int = Query(1, ge=1, le=5),
):
    """Return a project's active, injectable memories (for SessionStart injection)."""
    try:
        from hermes.memory_service import recall_memories
        items = recall_memories(project, namespace=namespace, limit=limit, min_importance=min_importance)
        return {"project": project, "count": len(items), "items": items}
    except Exception as e:
        raise HTTPException(503, f"Recall error: {str(e)}")


@app.post("/api/memory/associate")
def memory_associate(body: dict):
    """Associative recall across the concept-hub graph (cross-project by default)."""
    try:
        from hermes.memory_service import associate
        query = body.get("query", "")
        if not query:
            raise HTTPException(400, "query required")
        return associate(
            query,
            limit=body.get("limit", 10),
            hops=body.get("hops", 1),
            cross_project=body.get("cross_project", True),
            min_importance=body.get("min_importance", 1),
            project=body.get("project"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Associate error: {str(e)}")


@app.post("/api/memory/extract-corrections")
def memory_extract_corrections(body: dict):
    """Extract BUGFIX memories from raw error→fix episodes (full session content,
    supplied by the client-side correction extractor that reads ~/.claude/ JSONL)."""
    try:
        from hermes.extractor import extract_corrections
        if _llm_service is None:
            raise HTTPException(503, "LLM service unavailable")
        project_path = body.get("project_path") or body.get("project") or ""
        episodes = body.get("episodes") or []
        if not isinstance(episodes, list) or not episodes:
            return {"project": project_path, "total": {"episodes": 0, "extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}}
        return {"project": project_path, "total": extract_corrections(episodes, project_path, _llm_service)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Correction extract error: {str(e)}")


@app.post("/api/memory/extract-sessions")
def memory_extract_sessions(body: dict):
    """Extract durable memories from client-supplied FULL session content (JSONL summaries).
    Client reads local ~/.claude/projects/*/*.jsonl and ships session summaries the server
    DB lacks (server stores only shallow previews). Body: {project_path, sessions:[...]}."""
    try:
        from hermes.extractor import extract_from_sessions
        if _llm_service is None:
            raise HTTPException(503, "LLM service unavailable")
        project_path = body.get("project_path") or body.get("project") or ""
        sessions = body.get("sessions") or []
        if not isinstance(sessions, list) or not sessions:
            return {"project": project_path, "total": {"extracted": 0, "new": 0, "duplicates": 0, "superseded": 0}}
        return {"project": project_path, "total": extract_from_sessions(sessions, project_path, _llm_service)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, f"Session extract error: {str(e)}")


# ── MCP (Streamable HTTP, stateless JSON-RPC) ───────────────────────────────

@app.post("/mcp")
async def mcp_post(request: Request):
    from hermes.mcp_server import handle_mcp
    return await handle_mcp(request)


@app.get("/mcp")
async def mcp_get():
    from fastapi.responses import Response
    return Response(status_code=405, headers={"Allow": "POST"})


@app.delete("/mcp")
async def mcp_delete():
    from fastapi.responses import Response
    return Response(status_code=200)


# ── Recap Ingest API ─────────────────────────────────────────────────────────

@app.post("/api/recap/ingest")
def recap_ingest(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")):
    try:
        from hermes.iteration import run_daily_ingestion
        return run_daily_ingestion(date)
    except Exception as e:
        raise HTTPException(500, f"Ingest error: {str(e)}")


@app.post("/api/recap/ingest-all")
def recap_ingest_all():
    try:
        from recap.recap_engine import list_recaps
        from hermes.iteration import run_daily_ingestion
        recaps = list_recaps()
        results = []
        for r in recaps:
            result = run_daily_ingestion(r["date"])
            results.append(result)
        return {"total": len(results), "results": results}
    except Exception as e:
        raise HTTPException(500, f"Bulk ingest error: {str(e)}")


# ── Client Ingest API (extracted to hermes.routers.ingest) ────────────────
from hermes.routers.ingest import router as ingest_router  # noqa: E402

app.include_router(ingest_router)


# ── A2A Protocol Routes ─────────────────────────────────────────────────

@app.get("/.well-known/agent-card.json")
def a2a_agent_card(request: Request):
    from hermes.a2a_service import get_agent_card
    base = str(request.base_url).rstrip("/")
    return get_agent_card(base)


@app.post("/a2a/message:send")
async def a2a_message_send(request: Request, body: dict):
    from hermes.a2a_service import validate_agent, handle_send_message

    lines = request.headers.get("authorization", "")
    token = lines.replace("bearer ", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(401, detail="Missing Authorization header")

    metadata = body.get("metadata", {})
    agent_id = metadata.get("agent_id", "")
    if not agent_id:
        msg = body.get("message", body)
        metadata = msg.get("metadata", {}) if isinstance(msg, dict) else {}
        agent_id = metadata.get("agent_id", "")

    if not agent_id:
        raise HTTPException(400, detail="Missing agent_id in message.metadata")

    agent = validate_agent(agent_id, token)
    if not agent:
        raise HTTPException(403, detail="Agent not registered or disabled")

    task_id = body.get("taskId", body.get("id", "")) or str(uuid.uuid4())

    msg = body.get("message", body)

    result = handle_send_message(msg, agent_id)

    if result.get("status") == "completed":
        return {
            "id": task_id,
            "status": {"state": "COMPLETED"},
            "artifacts": [{
                "parts": [{"text": json.dumps({k: v for k, v in result.items() if k != "status"}, ensure_ascii=False)}],
                "index": 0,
            }],
        }
    else:
        return {
            "id": task_id,
            "status": {"state": "FAILED", "message": result.get("error", "Unknown error")},
        }


@app.get("/a2a/tasks/{task_id}")
def a2a_get_task(task_id: str):
    return {
        "id": task_id,
        "status": {"state": "COMPLETED"},
    }


@app.post("/a2a/tasks/{task_id}:cancel")
def a2a_cancel_task(task_id: str):
    return {"id": task_id, "status": {"state": "CANCELED"}}


# ── A2A Agent Management API ──────────────────────────────────────────

@app.get("/api/a2a/agents")
def a2a_list_agents():
    from hermes.memory_service import list_agents
    return {"agents": list_agents()}


@app.post("/api/a2a/agents")
def a2a_register_agent(body: dict):
    from hermes.memory_service import register_agent
    import bcrypt
    agent_id = body.get("agent_id", "")
    name = body.get("name", "")
    if not agent_id or not name:
        raise HTTPException(400, detail="agent_id and name are required")

    token = body.get("auth_token", "")
    hashed = ""
    if token:
        hashed = bcrypt.hashpw(token.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    agent = register_agent(
        agent_id=agent_id,
        name=name,
        description=body.get("description", ""),
        namespace=body.get("namespace", agent_id),
        auth_credentials=hashed,
        agent_url=body.get("agent_url", ""),
    )
    # C-stage: provision wallet with initial token grant
    try:
        from hermes.bounty_service import get_or_create_wallet
        agent["wallet"] = get_or_create_wallet(body.get("namespace", agent_id))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"wallet provision failed: {e}")
    return agent


@app.delete("/api/a2a/agents/{agent_id}")
def a2a_delete_agent(agent_id: str):
    from hermes.db import execute_one
    existing = execute_one("SELECT id FROM a2a_agents WHERE agent_id = %s", (agent_id,))
    if not existing:
        raise HTTPException(404, detail="Agent not found")
    from hermes.db import execute
    execute("DELETE FROM a2a_agents WHERE agent_id = %s", (agent_id,))
    return {"status": "deleted", "agent_id": agent_id}


@app.get("/api/a2a/agents/{agent_id}/stats")
def a2a_agent_stats(agent_id: str):
    from hermes.memory_service import get_memory_stats
    return get_memory_stats(namespace=agent_id)


# ── Bounty Economy API (C-stage) ────────────────────────────────────────

@app.post("/api/bounty/create")
def bounty_create(body: dict):
    """Create a bounty: debits creator, locks amount in escrow."""
    from hermes.bounty_service import create_bounty
    try:
        bounty = create_bounty(
            question=body.get("question", ""),
            amount=int(body.get("amount", 0)),
            creator_namespace=body.get("creator_namespace", ""),
            framework=body.get("framework"),
            expires_in_hours=body.get("expires_in_hours", 24),
        )
        return {"bounty_id": str(bounty["id"]), "amount": bounty["amount"], "status": bounty["status"]}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/bounty/list")
def bounty_list(status: str = "open", framework: Optional[str] = None, limit: int = Query(20, ge=1, le=100)):
    from hermes.bounty_service import list_bounties
    return {"bounties": list_bounties(status=status, framework=framework, limit=limit)}


@app.post("/api/bounty/{bounty_id}/claim")
def bounty_claim(bounty_id: str, body: dict):
    from hermes.bounty_service import claim_bounty
    claimed = claim_bounty(bounty_id, body.get("claimer_namespace", ""))
    if not claimed:
        raise HTTPException(400, "Bounty not found or not open")
    return {"claimed": True, "bounty_id": str(claimed["id"])}


@app.post("/api/bounty/{bounty_id}/answer")
def bounty_answer(bounty_id: str, body: dict):
    from hermes.bounty_service import answer_bounty
    result = answer_bounty(bounty_id, body.get("solution", ""), body.get("solver_namespace", ""))
    if not result:
        raise HTTPException(400, "Bounty not claimed by you")
    return result


@app.get("/api/bounty/{bounty_id}/match")
def bounty_match(bounty_id: str, limit: int = Query(5, ge=1, le=20)):
    from hermes.bounty_service import match_bounty
    return {"matches": match_bounty(bounty_id, limit=limit)}


@app.post("/api/bounty/{bounty_id}/accept")
def bounty_accept(bounty_id: str, body: dict):
    """Creator accepts a pending answer → reward solver + promote memory."""
    from hermes.bounty_service import accept_bounty
    result = accept_bounty(bounty_id, body.get("creator_namespace", ""))
    if not result:
        raise HTTPException(400, "Bounty not in 'answered' state or you are not the creator")
    return result


@app.post("/api/bounty/{bounty_id}/reject")
def bounty_reject(bounty_id: str, body: dict):
    """Creator rejects a pending answer → reopen bounty, archive solution memory."""
    from hermes.bounty_service import reject_bounty
    result = reject_bounty(bounty_id, body.get("creator_namespace", ""))
    if not result:
        raise HTTPException(400, "Bounty not in 'answered' state or you are not the creator")
    return result


@app.post("/api/bounty/expire")
def bounty_expire():
    """Expire overdue bounties + refund creators (open 100%, answered 80%).
    Can be called manually or wired to the scheduler for periodic runs."""
    from hermes.bounty_service import expire_bounties
    return expire_bounties()


@app.post("/api/bounty/{bounty_id}/auto-answer")
def bounty_auto_answer(bounty_id: str, body: dict):
    """Auto-answer a bounty from a namespace's relevant memory (C2-3 simplified).
    Body: {namespace, threshold?}. Still goes through governance (pending accept)."""
    from hermes.bounty_service import auto_answer_bounty
    return auto_answer_bounty(
        bounty_id,
        body.get("namespace", ""),
        threshold=body.get("threshold", 0.3),
    )


@app.get("/api/wallet/{namespace}")
def wallet_balance(namespace: str):
    from hermes.bounty_service import get_or_create_wallet, get_transactions
    w = get_or_create_wallet(namespace)
    return {
        "namespace": w["namespace"],
        "token_balance": w["token_balance"],
        "tokens_earned": w["tokens_earned"],
        "tokens_spent": w["tokens_spent"],
        "recent_transactions": get_transactions(namespace, limit=10),
    }


# ── Memory Pricing API (C2-4 opt-in) ────────────────────────────────────

@app.post("/api/memory/priced-search")
def memory_priced_search(body: dict):
    """C2-4: search with opt-in pricing — caller pays for priced hits, owner receives."""
    from hermes.pricing_service import priced_search
    try:
        results = priced_search(
            query=body.get("query", ""),
            caller_namespace=body.get("caller_namespace", ""),
            limit=body.get("limit", 20),
            namespaces=body.get("namespaces"),
        )
        return {"results": results, "total": len(results)}
    except Exception as e:
        raise HTTPException(503, f"Priced search error: {str(e)}")


@app.post("/api/memory/{memory_id}/price")
def memory_set_price(memory_id: str, body: dict):
    """C2-4: owner sets a price on a memory (0 = free)."""
    from hermes.pricing_service import set_memory_price
    try:
        result = set_memory_price(
            memory_id,
            body.get("owner_namespace", ""),
            int(body.get("price", 0)),
        )
        if not result:
            raise HTTPException(400, "Memory not found or you are not the owner")
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── Source-Scoped Routes (with DB fallback) ────────────────────────────────


def _db_dashboard_stats(range_str: str) -> dict:
    from collections import defaultdict
    from hermes.db import execute

    now = datetime.now(timezone.utc)
    if range_str == "7d":
        cutoff = now - timedelta(days=7)
    elif range_str == "30d":
        cutoff = now - timedelta(days=30)
    else:
        cutoff = None

    rows = execute(
        "SELECT project_id, session_id, preview, message_count, "
        "token_input, token_output, first_ts, "
        "COALESCE(last_ts, file_mtime) as last_ts "
        "FROM sessions ORDER BY last_ts DESC",
        fetch=True,
    )
    if rows is None:
        rows = []

    total_input = 0
    total_output = 0
    daily_sessions = defaultdict(int)
    daily_tokens_in = defaultdict(int)
    daily_tokens_out = defaultdict(int)
    project_counts = defaultdict(lambda: {"count": 0, "name": ""})
    curr_sessions = 0

    for r in rows:
        inp = r.get("token_input") or 0
        out = r.get("token_output") or 0
        total_input += inp
        total_output += out

        last_ts = r.get("last_ts")
        if last_ts:
            if cutoff is None or last_ts >= cutoff:
                curr_sessions += 1
                day_str = last_ts.strftime("%Y-%m-%d") if hasattr(last_ts, "strftime") else str(last_ts)[:10]
                daily_sessions[day_str] += 1
                daily_tokens_in[day_str] += inp
                daily_tokens_out[day_str] += out

        pid = r.get("project_id", "")
        project_counts[pid]["count"] += 1
        ppath = r.get("project_path") or r.get("preview") or ""
        if ppath and not project_counts[pid]["name"]:
            project_counts[pid]["name"] = ppath

    top_projects = sorted(project_counts.values(), key=lambda x: x["count"], reverse=True)[:5]
    top_projects_out = [
        {"project_id": k, "project_name": v["name"] or k, "session_count": v["count"]}
        for k, v in sorted(project_counts.items(), key=lambda x: x[1]["count"], reverse=True)[:5]
    ]

    daily_series = [
        {
            "date": d,
            "sessions": daily_sessions[d],
            "commands": 0,
            "tokens": daily_tokens_in[d] + daily_tokens_out[d],
        }
        for d in sorted(daily_sessions.keys())
    ]

    return {
        "summary": {
            "total_commands": 0,
            "total_sessions": len(rows),
            "total_projects": len(project_counts),
            "total_tokens": {"input": total_input, "output": total_output},
        },
        "changes": {"commands_pct": 0, "sessions_pct": 0, "projects_new": 0, "tokens_pct": 0},
        "daily_series": daily_series,
        "message_types": {},
        "top_projects": top_projects_out,
        "hourly_distribution": [0] * 24,
        "session_durations": {},
    }


def _db_recent_sessions(limit: int) -> list:
    from hermes.db import execute

    rows = execute(
        "SELECT session_id, project_id, project_path, preview, "
        "message_count, token_input, token_output, "
        "COALESCE(last_ts, file_mtime) as last_ts "
        "FROM sessions ORDER BY COALESCE(last_ts, file_mtime) DESC LIMIT %s",
        (limit,),
        fetch=True,
    )
    if rows is None:
        return []
    return [
        {
            "session_id": r.get("session_id", ""),
            "project_id": r.get("project_id", ""),
            "project_path": r.get("project_path") or r.get("project_id", ""),
            "preview": r.get("preview", ""),
            "message_count": r.get("message_count", 0),
            "token_input": r.get("token_input", 0),
            "token_output": r.get("token_output", 0),
            "timestamp": r.get("last_ts").isoformat() if r.get("last_ts") else None,
            "size": 0,
        }
        for r in rows
    ]


def _db_list_projects() -> list:
    from hermes.db import execute

    rows = execute(
        "SELECT project_id, COUNT(*) as session_count, "
        "MAX(project_path) as project_path, SUM(token_input + token_output) as total_tokens "
        "FROM sessions GROUP BY project_id ORDER BY session_count DESC",
        fetch=True,
    )
    if rows is None:
        return []
    return [
        {
            "id": r["project_id"],
            "path": r.get("project_path") or r["project_id"],
            "display_name": r["project_id"],
            "session_count": r["session_count"],
            "size": 0,
        }
        for r in rows
    ]


def _db_get_history(page: int, limit: int, search: Optional[str], project: Optional[str]) -> dict:
    from hermes.db import execute

    conditions = []
    params = []
    if search:
        conditions.append("preview ILIKE %s")
        params.append(f"%{search}%")
    if project:
        conditions.append("project_id ILIKE %s")
        params.append(f"%{project}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total_row = execute(f"SELECT COUNT(*) as cnt FROM sessions {where}", tuple(params), fetch=True)
    total = total_row[0]["cnt"] if total_row else 0

    offset = (page - 1) * limit
    rows = execute(
        f"SELECT session_id, project_id, project_path, preview, message_count, "
        f"COALESCE(last_ts, file_mtime) as last_ts "
        f"FROM sessions {where} "
        f"ORDER BY COALESCE(last_ts, file_mtime) DESC "
        f"LIMIT %s OFFSET %s",
        tuple(params) + (limit, offset),
        fetch=True,
    )

    items = []
    for r in (rows or []):
        ts = r.get("last_ts")
        ts_val = int(ts.timestamp() * 1000) if ts else 0
        items.append({
            "display": r.get("preview", ""),
            "project": r.get("project_path") or r.get("project_id", ""),
            "sessionId": r.get("session_id", ""),
            "project_id": r.get("project_id", ""),
            "timestamp": ts_val,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
    }


def _try_provider_or_db(source: str, method_name: str, *args):
    provider = get_provider(source)
    if provider and provider.available():
        result = getattr(provider, method_name)(*args)
        has_data = result and (
            isinstance(result, list) and len(result) > 0
            or isinstance(result, dict) and (
                result.get("summary", {}).get("total_sessions", 0) > 0
                or result.get("total", 0) > 0
                or result.get("items") and len(result["items"]) > 0
            )
        )
        if has_data:
            return result
    db_fallbacks = {
        "get_dashboard_stats": _db_dashboard_stats,
        "get_recent_sessions": _db_recent_sessions,
        "list_projects": _db_list_projects,
        "get_history": _db_get_history,
    }
    fallback = db_fallbacks.get(method_name)
    if fallback:
        return fallback(*args)
    raise HTTPException(404, "Source not found/unavailable")


@app.get("/api/{source}/stats")
def get_source_stats(source: str):
    return call_provider(source, "get_stats")


@app.get("/api/{source}/dashboard-stats")
def get_source_dashboard_stats(source: str, range: str = Query("30d", pattern="^(7d|30d|all)$")):
    return _try_provider_or_db(source, "get_dashboard_stats", range)


@app.get("/api/{source}/recent-sessions")
def get_source_recent_sessions(source: str, limit: int = Query(5, ge=1, le=20)):
    return _try_provider_or_db(source, "get_recent_sessions", limit)


@app.get("/api/{source}/history")
def get_source_history(
    source: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
):
    return _try_provider_or_db(source, "get_history", page, limit, search, project)


@app.get("/api/{source}/projects")
def get_source_projects(source: str):
    return _try_provider_or_db(source, "list_projects")


@app.get("/api/{source}/projects/{project_id}")
def get_source_project_detail(source: str, project_id: str):
    return _not_found_as_http404(lambda: call_provider(source, "get_project", project_id))


@app.get("/api/{source}/projects/{project_id}/sessions")
def get_source_project_sessions(source: str, project_id: str):
    return _not_found_as_http404(lambda: call_provider(source, "list_sessions", project_id))


@app.get("/api/{source}/projects/{project_id}/sessions/{session_id}")
def get_source_session_conversation(source: str, project_id: str, session_id: str):
    return _not_found_as_http404(lambda: call_provider(source, "get_session", project_id, session_id))


@app.get("/api/{source}/projects/{project_id}/sessions/{session_id}/subagents/{agent_file}")
def get_source_subagent_conversation(source: str, project_id: str, session_id: str, agent_file: str):
    return _not_found_as_http404(
        lambda: call_provider(source, "get_subagent", project_id, session_id, agent_file)
    )


# ── Recap API ────────────────────────────────────────────────────────────────

@app.get("/api/recap/daily")
def get_daily_recap(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")):
    recap = load_recap(date)
    if recap:
        return recap
    return {"date": date, "generated": False}


@app.post("/api/recap/generate")
def trigger_recap(date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")):
    if not _llm_service:
        raise HTTPException(503, "LLM service not configured")
    provider = _get_claude_provider()
    result = generate_recap(provider, date, _llm_service)
    if "error" in result:
        raise HTTPException(422, result["error"])
    return result


@app.get("/api/recap/list")
def get_recap_list():
    return list_recaps()


@app.get("/api/recap/calendar")
def get_recap_calendar(months: int = Query(3, ge=1, le=12)):
    provider = _get_claude_provider()
    return get_available_dates(provider, months)


# ── Serve Frontend ───────────────────────────────────────────────────────────

# In development, Vite serves the frontend on port 5173
# In production, serve the built files
dist_dir = get_base_path() / "web" / "dist"
if dist_dir.exists():
    app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = dist_dir / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(dist_dir / "index.html")


if __name__ == "__main__":
    import argparse
    import threading
    import webbrowser

    parser = argparse.ArgumentParser(description="Claude History Viewer")
    parser.add_argument("--port", type=int, default=8787, help="服务端口 (默认: 8787)")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    parser.add_argument("--shared", action="store_true", help="允许局域网访问 (默认仅本机访问)")
    args = parser.parse_args()

    host = "0.0.0.0" if args.shared else "127.0.0.1"

    if not args.no_open:
        def open_browser():
            import time

            time.sleep(1.5)
            webbrowser.open("http://localhost:%s" % args.port)

        threading.Thread(target=open_browser, daemon=True).start()

    import uvicorn

    uvicorn.run(app, host=host, port=args.port)
