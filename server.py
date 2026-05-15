"""Claude History Viewer - FastAPI Backend"""
import json
import uuid
from pathlib import Path
from typing import Optional
import sys

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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


app = FastAPI(title="Claude History Viewer")

# ── Recap & Memory Init ──
try:
    _llm_service = LLMService(get_llm_config())
except Exception as e:
    import logging
    logging.getLogger(__name__).warning(f"LLM service init failed: {e}")
    _llm_service = None


def _get_claude_provider():
    return get_provider("claude")


@app.on_event("startup")
async def _on_startup():
    try:
        from hermes.db import init_db
        init_db()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"DB init failed: {e}")
    if _llm_service:
        start_scheduler(_get_claude_provider(), _llm_service)


@app.on_event("shutdown")
async def _on_shutdown():
    stop_scheduler()


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
    """Hybrid search across memories."""
    try:
        from hermes.memory_service import search_memories
        query = body.get("query", "")
        layer = body.get("layer") or None
        limit = body.get("limit", 20)
        results = search_memories(query, layer=layer, limit=limit)
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
        result = write_memory(content=content, namespace=ns, source="mcp")
        return {"success": True, "id": result.get("id"), "layer": result.get("layer")}
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


@app.delete("/api/memory/delete")
def memory_delete(target: str = Query(...), substring: str = Query(...)):
    try:
        from hermes.memory_service import delete_memory
        ns = "claude" if target == "memory" else "user"
        ok = delete_memory(substring, namespace=ns)
        return {"success": ok}
    except Exception as e:
        raise HTTPException(503, f"Memory delete error: {str(e)}")


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


# ── Client Ingest API ───────────────────────────────────────────────────

@app.post("/api/ingest/register")
def ingest_register(body: dict):
    try:
        from hermes.ingest_service import register_client
        return register_client(body.get("name", ""), body.get("hostname", ""), body.get("os_info", ""))
    except Exception as e:
        raise HTTPException(500, f"Register error: {str(e)}")


@app.post("/api/ingest/sessions")
def ingest_sessions_route(body: dict):
    try:
        from hermes.ingest_service import ingest_sessions as _ingest
        return _ingest(body.get("client_id", ""), body.get("sessions", []), body.get("sync_log", []))
    except Exception as e:
        raise HTTPException(500, f"Session ingest error: {str(e)}")


@app.post("/api/ingest/episodic")
def ingest_episodic_memories(body: dict):
    try:
        from hermes.ingest_service import ingest_episodic
        return ingest_episodic(body.get("client_id", ""), body.get("memories", []), body.get("date", ""))
    except Exception as e:
        raise HTTPException(500, f"Episodic ingest error: {str(e)}")


@app.post("/api/ingest/heartbeat")
def ingest_heartbeat(body: dict):
    try:
        from hermes.ingest_service import heartbeat as _heartbeat
        return _heartbeat(body.get("client_id", ""))
    except Exception as e:
        raise HTTPException(500, f"Heartbeat error: {str(e)}")


@app.get("/api/ingest/sync-status")
def ingest_sync_status(client_id: str = Query(...)):
    try:
        from hermes.ingest_service import get_sync_status
        return get_sync_status(client_id)
    except Exception as e:
        raise HTTPException(500, f"Sync status error: {str(e)}")


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


# ── Source-Scoped Routes ─────────────────────────────────────────────────────

@app.get("/api/{source}/stats")
def get_source_stats(source: str):
    return call_provider(source, "get_stats")


@app.get("/api/{source}/dashboard-stats")
def get_source_dashboard_stats(source: str, range: str = Query("30d", pattern="^(7d|30d|all)$")):
    return call_provider(source, "get_dashboard_stats", range)


@app.get("/api/{source}/recent-sessions")
def get_source_recent_sessions(source: str, limit: int = Query(5, ge=1, le=20)):
    return call_provider(source, "get_recent_sessions", limit)


@app.get("/api/{source}/history")
def get_source_history(
    source: str,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    project: Optional[str] = Query(None),
):
    return call_provider(source, "get_history", page, limit, search, project)


@app.get("/api/{source}/projects")
def get_source_projects(source: str):
    return call_provider(source, "list_projects")


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
    if not provider or not provider.available():
        raise HTTPException(404, "Claude data not found")
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
    if not provider or not provider.available():
        return []
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
