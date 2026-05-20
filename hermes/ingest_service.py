"""Ingest service: handle client data pushes."""
import logging
from typing import Any, Dict, List

import numpy as np

from hermes.db import execute, execute_one, get_conn, put_conn

logger = logging.getLogger(__name__)


def register_client(name: str, hostname: str = "", os_info: str = "") -> Dict[str, Any]:
    """Register a new client or return existing client_id."""
    row = execute_one("SELECT id FROM clients WHERE name = %s", (name,))
    if row:
        return {"client_id": str(row["id"]), "status": "already_registered"}
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO clients (name, hostname, os_info) VALUES (%s, %s, %s) RETURNING id",
                (name, hostname, os_info),
            )
            client_id = str(cur.fetchone()[0])
        conn.commit()
        return {"client_id": client_id, "status": "registered"}
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)


def ingest_sessions(client_id: str, sessions: List[Dict], sync_log: List[Dict]) -> Dict[str, Any]:
    """Batch ingest sessions from a client. Upserts on conflict."""
    accepted = 0
    duplicates = 0
    conn = get_conn()
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
        with conn.cursor() as cur:
            for s in sessions:
                emb = None
                raw_emb = s.get("embedding")
                if raw_emb:
                    try:
                        emb = np.array(raw_emb, dtype=np.float32)
                    except (ValueError, TypeError):
                        logger.warning("Skipping invalid embedding for session %s", s.get("session_id"))
                        emb = None
                cur.execute(
                    """
                    INSERT INTO sessions (client_id, project_id, session_id, project_path,
                        preview, message_count, token_input, token_output, embedding,
                        first_ts, last_ts, file_mtime, namespace)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s)
                    ON CONFLICT (client_id, project_id, session_id) DO UPDATE SET
                        project_path = EXCLUDED.project_path,
                        preview = EXCLUDED.preview,
                        message_count = EXCLUDED.message_count,
                        token_input = EXCLUDED.token_input,
                        token_output = EXCLUDED.token_output,
                        embedding = EXCLUDED.embedding,
                        first_ts = EXCLUDED.first_ts,
                        last_ts = EXCLUDED.last_ts,
                        file_mtime = EXCLUDED.file_mtime,
                        namespace = EXCLUDED.namespace
                    """,
                    (client_id, s.get("project_id", ""), s.get("session_id", ""),
                     s.get("project_path"), s.get("preview"), s.get("message_count", 0),
                     s.get("token_input", 0), s.get("token_output", 0), emb,
                     s.get("first_ts") or None, s.get("last_ts") or None,
                     s.get("file_mtime") or None,
                     s.get("namespace", "claude")),
                )
                accepted += 1
            for sl in sync_log:
                cur.execute(
                    """
                    INSERT INTO sync_log (client_id, file_path, file_size, file_mtime, session_count)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (client_id, file_path) DO UPDATE SET
                        file_size = EXCLUDED.file_size, file_mtime = EXCLUDED.file_mtime,
                        session_count = EXCLUDED.session_count, synced_at = now()
                    """,
                    (client_id, sl.get("file_path", ""), sl.get("file_size"),
                     sl.get("file_mtime") or None, sl.get("session_count", 1)),
                )
            cur.execute("UPDATE clients SET last_sync = now() WHERE id = %s", (client_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    return {"accepted": accepted, "duplicates_skipped": duplicates}


def ingest_episodic(client_id: str, memories: List[Dict], date: str = "") -> Dict[str, Any]:
    """Batch ingest episodic memories with precomputed embeddings."""
    from hermes.memory_service import write_memory
    accepted = 0
    for m in memories:
        try:
            write_memory(
                content=m.get("content", ""), layer="episodic", source="client_push",
                importance=m.get("importance", 3), tags=m.get("tags", []),
                summary=m.get("summary"), embedding=m.get("embedding"), auto_embed=False,
            )
            accepted += 1
        except Exception as e:
            logger.warning("Failed to ingest episodic memory: %s", e)
    return {"accepted": accepted}


def heartbeat(client_id: str) -> Dict[str, Any]:
    """Update client heartbeat timestamp."""
    execute("UPDATE clients SET last_heartbeat = now() WHERE id = %s", (client_id,))
    return {"status": "ok"}


def get_sync_status(client_id: str) -> Dict[str, Any]:
    """Get sync status for a client."""
    client = execute_one("SELECT last_sync FROM clients WHERE id = %s", (client_id,))
    if not client:
        return {"client_id": client_id, "last_sync": None, "synced_files": []}
    files = execute(
        "SELECT file_path, file_mtime FROM sync_log WHERE client_id = %s ORDER BY synced_at DESC",
        (client_id,), fetch=True,
    )
    return {
        "client_id": client_id,
        "last_sync": client.get("last_sync"),
        "synced_files": [{"file_path": f["file_path"], "file_mtime": f["file_mtime"]} for f in (files or [])],
    }
