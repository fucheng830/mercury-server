"""End-to-end smoke check for POST /api/sessions/search (against hermes_test).

Seeds one session + 3 messages, calls the real FastAPI route via TestClient,
asserts the response shape, and cleans up (CASCADE via client delete).
Self-contained — sets DB env vars itself.

    python scripts/verify_message_search.py
"""
import os
for k, v in {
    "MERCURY_DB_NAME": "hermes_test",
    "MERCURY_DB_HOST": "192.168.0.17",
    "MERCURY_DB_USER": "hermes",
    "MERCURY_DB_PASSWORD": "hermes",
    "MERCURY_EMBEDDING_URL": "http://192.168.0.13:11434",
}.items():
    os.environ.setdefault(k, v)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from hermes.db import execute, get_conn, put_conn

# Clean slate (CASCADE: client → sessions → session_messages)
execute("DELETE FROM clients WHERE name = 'verify-client'")
execute("INSERT INTO clients (name) VALUES ('verify-client')")
cid = execute("SELECT id FROM clients WHERE name = 'verify-client'", fetch=True)[0]["id"]
sid = execute(
    "INSERT INTO sessions (client_id, project_id, session_id, project_path, "
    "preview, message_count, namespace) "
    "VALUES (%s, 'verify-proj', 'verify-sess', '/verify', 'preview', 3, 'claude') RETURNING id",
    (str(cid),), fetch=True,
)[0]["id"]
sid = str(sid)


def _msg(seq, role, text, seed):
    v = np.zeros(1024, dtype="float32")
    v[seed] = 1.0
    conn = get_conn()
    try:
        from pgvector.psycopg2 import register_vector
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO session_messages (session_id, namespace, seq, role, "
                "content_text, embedding) VALUES (%s, 'claude', %s, %s, %s, %s::vector)",
                (sid, seq, role, text, v),
            )
        conn.commit()
    finally:
        put_conn(conn)


_msg(0, "user", "how does the RRF hybrid search work", 10)
_msg(1, "assistant", "it combines vector and FTS via reciprocal rank fusion", 11)
_msg(2, "user", "unrelated smalltalk here", 12)
print(f"seeded session {sid}, 3 messages")

from fastapi.testclient import TestClient
import server  # noqa: E402

with TestClient(server.app) as client:
    r = client.post("/api/sessions/search",
                    json={"query": "RRF hybrid", "namespace": "claude",
                          "limit": 5, "context_window": 1})
    print("status:", r.status_code)
    data = r.json()
    results = data.get("results", [])
    print("results:", len(results))
    for hit in results[:3]:
        print(f"  seq={hit['seq']} role={hit['role']} rrf={hit['rrf_score']:.4f} "
              f"prev={len(hit.get('prev', []))} next={len(hit.get('next', []))} "
              f"session={'y' if hit.get('session') else 'n'} "
              f"text={hit['content_text'][:55]!r}")

    assert r.status_code == 200, f"expected 200, got {r.status_code}: {data}"
    assert results, "expected at least one hit"
    top = results[0]
    assert top.get("session"), "session metadata missing on top hit"
    assert isinstance(top.get("prev", []), list) and isinstance(top.get("next", []), list)
    print("\nOK: /api/sessions/search returns ranked hits with session + context window")

# Cleanup
execute("DELETE FROM clients WHERE name = 'verify-client'")
print("cleaned up")
