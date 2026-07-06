"""Backfill missing embeddings in session_messages (vectors only, no re-parse).

For rows where embedding IS NULL, batch-embed content_text and UPDATE in place.
Idempotent: re-running only touches rows still missing a vector. Use this when
the embedding service was unreachable during the original JSONL backfill
(e.g. config.yaml had a placeholder api_base and no MERCURY_EMBEDDING_URL env).

Usage:
    MERCURY_EMBEDDING_URL=http://192.168.0.13:11434 \\
    python scripts/backfill_embeddings.py [--batch 20] [--limit 0]
"""
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("embed-backfill")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--batch", type=int, default=20, help="rows per embedding request")
    ap.add_argument("--limit", type=int, default=0, help="max rows to process (0=all)")
    args = ap.parse_args()

    import numpy as np
    from hermes.db import execute, get_conn, put_conn

    pending = execute(
        "SELECT count(*) AS n FROM session_messages WHERE embedding IS NULL",
        fetch=True,
    )[0]["n"]
    if not pending:
        log.info("no rows missing embedding; nothing to do")
        return
    log.info("rows missing embedding: %d", pending)

    from hermes.embedding import generate_embeddings_batch

    processed = 0
    updated = 0
    while True:
        rows = execute(
            "SELECT id, content_text FROM session_messages WHERE embedding IS NULL "
            "ORDER BY id LIMIT %s",
            (args.batch,), fetch=True,
        ) or []
        if not rows:
            break

        ids = [r["id"] for r in rows]
        texts = [r["content_text"] for r in rows]
        # Try batch first; on failure (e.g. one over-long text → 400), fall
        # back to per-row so a single huge message doesn't block the rest.
        try:
            vectors = generate_embeddings_batch(texts, batch_size=len(texts))
        except Exception as e:
            log.warning("batch failed (%s); per-row fallback", e)
            vectors = []
            for txt in texts:
                try:
                    vectors.append(generate_embeddings_batch([txt], batch_size=1)[0])
                except Exception as e2:
                    log.warning("row failed, skipping: %s", e2)
                    vectors.append(None)

        conn = get_conn()
        try:
            from pgvector.psycopg2 import register_vector
            register_vector(conn)
            with conn.cursor() as cur:
                for rid, vec in zip(ids, vectors):
                    if vec is None:
                        continue
                    cur.execute(
                        "UPDATE session_messages SET embedding = %s::vector WHERE id = %s",
                        (np.array(vec, dtype=np.float32), rid),
                    )
            conn.commit()
            updated += sum(1 for v in vectors if v is not None)
        except Exception:
            conn.rollback()
            raise
        finally:
            put_conn(conn)

        processed += len(rows)
        if processed % 200 == 0 or processed >= pending:
            log.info("progress %d/%d (updated=%d)", processed, pending, updated)
        if args.limit and processed >= args.limit:
            break

    log.info("done: processed=%d updated=%d", processed, updated)


if __name__ == "__main__":
    main()
