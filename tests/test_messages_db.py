"""DB integration tests for session_messages ingest + search_messages.

Run with MERCURY_TEST_DB=hermes_test; skipped otherwise. Mirrors the
test_memory_db.py style. These tests do NOT require the embedding service —
ingest degrades to FTS-only rows when it is unreachable, and search falls
back to FTS, so the assertions (row counts, content, context window) hold
either way. The vector path is exercised manually end-to-end (see spec §验收).
"""
import numpy as np
import pytest

pytestmark = pytest.mark.usefixtures("db")


def _seed_session(execute, suffix):
    """Create a client + session row, return the session's db id (str)."""
    execute(
        "INSERT INTO clients (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
        ("client-" + suffix,),
    )
    cid = execute(
        "SELECT id FROM clients WHERE name = %s", ("client-" + suffix,), fetch=True
    )[0]["id"]
    sid = execute(
        """
        INSERT INTO sessions (client_id, project_id, session_id, project_path,
            preview, message_count, namespace)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (str(cid), "proj-" + suffix, "sess-" + suffix, "/test/" + suffix,
         "preview", 0, "claude"),
        fetch=True,
    )[0]["id"]
    return str(sid)


def _seed_msg(session_db_id, seq, role, text, seed):
    """Insert one session_messages row with a unit vector (seed picks the dim)."""
    from hermes.db import get_conn, put_conn
    v = np.zeros(1024, dtype="float32")
    v[seed % 1024] = 1.0
    conn = get_conn()
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO session_messages
                    (session_id, namespace, seq, role, content_text, embedding)
                VALUES (%s, 'claude', %s, %s, %s, %s::vector)
                """,
                (session_db_id, seq, role, text, v),
            )
        conn.commit()
    finally:
        put_conn(conn)


def test_ingest_extracts_and_inserts_text_turns():
    from hermes.db import execute
    from hermes.messages_service import ingest_session_messages

    sid = _seed_session(execute, "ingest1")
    msgs = [
        {"type": "user", "message": {"content": "how does search work"},
         "timestamp": "2026-07-05T10:00:00Z"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "it uses RRF hybrid"},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
        ]}, "timestamp": "2026-07-05T10:00:01Z"},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "file output"},
        ]}, "timestamp": "2026-07-05T10:00:02Z"},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "done"},
        ]}, "timestamp": "2026-07-05T10:00:03Z"},
    ]
    res = ingest_session_messages(sid, "claude", msgs)
    assert res["ingested"] == 3  # user text + 2 assistant text; tool_result dropped

    rows = execute(
        "SELECT seq, role, content_text FROM session_messages "
        "WHERE session_id = %s ORDER BY seq",
        (sid,), fetch=True,
    )
    assert [r["role"] for r in rows] == ["user", "assistant", "assistant"]
    assert rows[1]["content_text"] == "it uses RRF hybrid"  # tool_use input not indexed


def test_ingest_is_idempotent_on_rerun():
    from hermes.db import execute
    from hermes.messages_service import ingest_session_messages

    sid = _seed_session(execute, "ingest2")
    msgs = [{"type": "user", "message": {"content": "same message"},
             "timestamp": "2026-07-05T10:00:00Z"}]
    first = ingest_session_messages(sid, "claude", msgs)
    second = ingest_session_messages(sid, "claude", msgs)
    assert first["ingested"] == 1
    assert second["ingested"] == 0  # ON CONFLICT (session_id, seq) DO NOTHING

    n = execute(
        "SELECT count(*) AS n FROM session_messages WHERE session_id = %s",
        (sid,), fetch=True,
    )[0]["n"]
    assert n == 1


def test_search_recalls_by_keyword():
    from hermes.db import execute
    from hermes.messages_service import search_messages

    sid = _seed_session(execute, "search1")
    _seed_msg(sid, 0, "user", "pgvector hybrid rrf ranking note", seed=10)
    _seed_msg(sid, 1, "assistant", "unrelated smalltalk here", seed=11)

    results = search_messages("pgvector", namespace="claude", limit=5, context_window=0)
    contents = [r["content_text"] for r in results]
    # Hybrid RRF: FTS drives the pgvector hit to the top; the vector path may
    # also surface a low-relevance neighbor (HNSW always returns nearest), so
    # we assert ranking, not exclusivity.
    assert any("pgvector hybrid" in c for c in contents)
    assert "pgvector hybrid" in results[0]["content_text"]
    assert results[0]["rrf_score"] >= results[-1]["rrf_score"]


def test_search_attaches_context_window_and_session():
    from hermes.db import execute
    from hermes.messages_service import search_messages

    sid = _seed_session(execute, "search2")
    _seed_msg(sid, 0, "user", "earlier turn zero", seed=20)
    _seed_msg(sid, 1, "assistant", "middle match target keyword", seed=21)
    _seed_msg(sid, 2, "user", "later turn two", seed=22)

    results = search_messages("keyword", namespace="claude", limit=5, context_window=1)
    hits = [r for r in results if "match target" in r["content_text"]]
    assert hits, "expected the keyword hit to be recalled"
    hit = hits[0]
    assert hit["seq"] == 1
    assert [p["seq"] for p in hit["prev"]] == [0]
    assert [n["seq"] for n in hit["next"]] == [2]
    assert hit["session"] is not None
    assert str(hit["session"]["id"]) == sid
