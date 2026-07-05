"""Message-level ingest + hybrid search (FTS + vector via RRF).

Independent recall channel alongside memories. See design:
docs/superpowers/specs/2026-07-05-message-level-search-design.md

The SQL side lives in schema.sql (table session_messages, function
search_session_messages). This module is the Python surface: turn extraction
from raw Claude Code JSONL, batched upsert with embedding, and search with a
context window. Vector-bearing calls register pgvector on a hand-checked-out
connection (the execute() helper does not).
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
from psycopg2.extras import RealDictCursor

from hermes.db import execute, get_conn, put_conn

logger = logging.getLogger(__name__)


# ── Text-turn extraction (pure, unit-testable) ────────────────────────────

def extract_text_turns(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract user/assistant text turns from raw Claude Code JSONL messages.

    Drops tool_use / tool_result / thinking blocks — signal-rich text only.
    A user message whose content carries a tool_result is dropped entirely
    (matches providers/claude.py:_reconstruct_conversation semantics). Returns
    [{seq, role, content_text, created_ts}] with seq incrementing only over
    kept turns, so neighbors in a context window are text neighbors.
    """
    turns: List[Dict[str, Any]] = []
    seq = 0
    for msg in messages:
        msg_type = msg.get("type")
        if msg_type not in ("user", "assistant"):
            continue
        content = msg.get("message", {}).get("content", "")
        ts = _parse_ts(msg.get("timestamp"))

        if msg_type == "user":
            if isinstance(content, str):
                text = content.strip()
            elif isinstance(content, list):
                if any(isinstance(c, dict) and c.get("type") == "tool_result"
                       for c in content):
                    continue  # tool_result wrapper (with or without text) — drop
                text = " ".join(
                    c.get("text", "") for c in content
                    if isinstance(c, dict) and c.get("type") == "text"
                ).strip()
            else:
                continue
            role = "user"
        else:  # assistant
            if not isinstance(content, list):
                continue
            text = " ".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()
            role = "assistant"

        if not text:
            continue
        turns.append({"seq": seq, "role": role,
                      "content_text": text, "created_ts": ts})
        seq += 1
    return turns


def _parse_ts(ts: Any) -> Optional[str]:
    """Normalize a Claude Code timestamp to an ISO string TIMESTAMPTZ accepts.

    Passes ISO strings through; converts epoch-ms numbers; returns None otherwise.
    """
    if not ts:
        return None
    if isinstance(ts, str):
        return ts
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return None
    return None


# ── Ingest ────────────────────────────────────────────────────────────────

def ingest_session_messages(
    session_db_id: str,
    namespace: str,
    messages: List[Dict[str, Any]],
    batch_size: int = 16,
) -> Dict[str, int]:
    """Extract text turns, embed in batches, upsert into session_messages.

    Idempotent via UNIQUE(session_id, seq) ON CONFLICT DO NOTHING — re-running
    on the same JSONL is a no-op. To refresh, delete the session's rows first.
    Embedding failure degrades gracefully: rows still land (FTS-recallable).
    Returns {"ingested": N, "skipped": M} (skipped = pre-existing or no text).
    """
    turns = extract_text_turns(messages)
    if not turns:
        return {"ingested": 0, "skipped": 0}

    texts = [t["content_text"] for t in turns]
    embeddings: List[Optional[List[float]]] = [None] * len(texts)
    try:
        from hermes.embedding import generate_embeddings_batch
        vectors = generate_embeddings_batch(texts, batch_size=batch_size)
        for i, v in enumerate(vectors):
            embeddings[i] = v
    except Exception as e:
        logger.warning("Embedding failed for session %s: %s (FTS-only fallback)",
                       session_db_id, e)

    conn = get_conn()
    ingested = 0
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
        with conn.cursor() as cur:
            for turn, emb in zip(turns, embeddings):
                if emb is None:
                    cur.execute(
                        """
                        INSERT INTO session_messages
                            (session_id, namespace, seq, role, content_text, created_ts)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (session_id, seq) DO NOTHING
                        """,
                        (session_db_id, namespace, turn["seq"], turn["role"],
                         turn["content_text"], turn["created_ts"]),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO session_messages
                            (session_id, namespace, seq, role, content_text, embedding, created_ts)
                        VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
                        ON CONFLICT (session_id, seq) DO NOTHING
                        """,
                        (session_db_id, namespace, turn["seq"], turn["role"],
                         turn["content_text"], np.array(emb, dtype=np.float32),
                         turn["created_ts"]),
                    )
                if cur.rowcount > 0:
                    ingested += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        put_conn(conn)
    return {"ingested": ingested, "skipped": len(turns) - ingested}


# ── Search ────────────────────────────────────────────────────────────────

def search_messages(
    query: str,
    namespace: str = "claude",
    limit: int = 20,
    offset: int = 0,
    context_window: int = 3,
) -> List[Dict[str, Any]]:
    """Hybrid (RRF) search over session_messages, with neighboring turns.

    Each hit: {id, session_id, seq, role, content_text, namespace, created_ts,
    rrf_score, session{...}, prev[], next[]}. Falls back to FTS-only if the
    embedding service is unreachable. `shared` namespace is always included.
    """
    namespaces = [namespace, "shared"] if namespace != "shared" else ["shared"]
    embedding_vec = None
    try:
        from hermes.embedding import generate_embedding
        embedding_vec = np.array(generate_embedding(query), dtype=np.float32)
    except Exception:
        logger.warning("search_messages: embedding failed, FTS-only fallback")

    conn = get_conn()
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if embedding_vec is not None:
                cur.execute(
                    """
                    SELECT id, session_id, seq, role, content_text, namespace,
                           created_ts, rrf_score
                    FROM search_session_messages(%s, %s::vector, %s, %s, %s, 60)
                    """,
                    (query, embedding_vec, namespaces, limit, offset),
                )
            else:
                cur.execute(
                    """
                    SELECT m.id, m.session_id, m.seq, m.role, m.content_text,
                           m.namespace, m.created_ts, 0.0::real AS rrf_score
                    FROM session_messages m
                    WHERE m.namespace = ANY(%s)
                      AND m.fts @@ plainto_tsquery('simple', %s)
                    ORDER BY ts_rank(m.fts, plainto_tsquery('simple', %s)) DESC
                    LIMIT %s OFFSET %s
                    """,
                    (namespaces, query, query, limit, offset),
                )
            hits = [dict(r) for r in cur.fetchall()]
    finally:
        put_conn(conn)

    _attach_session_and_context(hits, context_window)
    return hits


def _attach_session_and_context(hits: List[Dict[str, Any]], window: int) -> None:
    """Mutate each hit in place: add `session` dict and `prev`/`next` turn lists.

    session_id arrives as a str (psycopg2 returns UUID columns as text in this
    setup), so we cast on the SQL side rather than rely on driver adaptation.
    """
    if not hits:
        return
    session_ids = [str(h["session_id"]) for h in hits]
    sessions = execute(
        "SELECT id, session_id, project_id, project_path, namespace, "
        "first_ts, last_ts, message_count FROM sessions WHERE id = ANY(%s::uuid[])",
        (session_ids,), fetch=True,
    )
    sess_by_id = {str(s["id"]): s for s in (sessions or [])}
    for h in hits:
        h["session"] = sess_by_id.get(str(h["session_id"]))
        if window <= 0:
            h["prev"] = []
            h["next"] = []
    if window <= 0:
        return
    for h in hits:
        sid, seq = str(h["session_id"]), h["seq"]
        prev_rows = execute(
            "SELECT seq, role, content_text FROM session_messages "
            "WHERE session_id = %s::uuid AND seq < %s ORDER BY seq DESC LIMIT %s",
            (sid, seq, window), fetch=True,
        )
        next_rows = execute(
            "SELECT seq, role, content_text FROM session_messages "
            "WHERE session_id = %s::uuid AND seq > %s ORDER BY seq ASC LIMIT %s",
            (sid, seq, window), fetch=True,
        )
        h["prev"] = [dict(r) for r in (prev_rows or [])][::-1]  # chronological
        h["next"] = [dict(r) for r in (next_rows or [])]
