"""Client ingest routes — extracted from server.py as the start of route modularization.

Handles client registration and session/episodic sync pushes. All logic lives in
hermes.ingest_service; these are thin HTTP adapters.
"""
import logging

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.post("/api/ingest/register")
def ingest_register(body: dict):
    try:
        from hermes.ingest_service import register_client
        return register_client(body.get("name", ""), body.get("hostname", ""), body.get("os_info", ""))
    except Exception as e:
        logging.getLogger(__name__).exception("Register error")
        raise HTTPException(500, f"Register error: {str(e)}")


@router.post("/api/ingest/sessions")
def ingest_sessions_route(body: dict):
    try:
        from hermes.ingest_service import ingest_sessions as _ingest
        return _ingest(body.get("client_id", ""), body.get("sessions", []), body.get("sync_log", []))
    except Exception as e:
        logging.getLogger(__name__).exception("Session ingest error")
        raise HTTPException(500, f"Session ingest error: {str(e)}")


@router.post("/api/ingest/episodic")
def ingest_episodic_memories(body: dict):
    try:
        from hermes.ingest_service import ingest_episodic
        return ingest_episodic(body.get("client_id", ""), body.get("memories", []), body.get("date", ""))
    except Exception as e:
        logging.getLogger(__name__).exception("Episodic ingest error")
        raise HTTPException(500, f"Episodic ingest error: {str(e)}")


@router.post("/api/ingest/heartbeat")
def ingest_heartbeat(body: dict):
    try:
        from hermes.ingest_service import heartbeat as _heartbeat
        return _heartbeat(body.get("client_id", ""))
    except Exception as e:
        logging.getLogger(__name__).exception("Heartbeat error")
        raise HTTPException(500, f"Heartbeat error: {str(e)}")


@router.get("/api/ingest/sync-status")
def ingest_sync_status(client_id: str = Query(...)):
    try:
        from hermes.ingest_service import get_sync_status
        return get_sync_status(client_id)
    except Exception as e:
        raise HTTPException(500, f"Sync status error: {str(e)}")
