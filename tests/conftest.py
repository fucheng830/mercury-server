"""Pytest config: point mercury at a throwaway test DB before any hermes import.

Set MERCURY_TEST_DB=<dbname> to enable DB integration tests (skipped otherwise).
The env is set at conftest import time so the mercury connection pool (created lazily
on first get_conn) targets the test database, never production.
"""
import os

TEST_DB = os.environ.get("MERCURY_TEST_DB", "")
if TEST_DB:
    os.environ.setdefault("MERCURY_DB_NAME", TEST_DB)
    os.environ.setdefault("MERCURY_DB_HOST", os.environ.get("MERCURY_DB_HOST", "192.168.0.17"))
    os.environ.setdefault("MERCURY_DB_USER", "hermes")
    os.environ.setdefault("MERCURY_DB_PASSWORD", "hermes")
    os.environ.setdefault("MERCURY_EMBEDDING_URL", os.environ.get("MERCURY_EMBEDDING_URL", "http://192.168.0.13:11434"))

import pytest


@pytest.fixture(scope="session", autouse=True)
def _ensure_schema():
    if TEST_DB:
        from hermes.db import init_db
        init_db()


_TRUNCATE = ("memory_entities", "relations", "entities", "memories", "projects")


@pytest.fixture
def db():
    """Yield the execute() helper against the test DB, with clean tables before/after."""
    if not TEST_DB:
        pytest.skip("set MERCURY_TEST_DB to run DB integration tests")
    from hermes.db import execute
    for t in _TRUNCATE:
        execute(f"DELETE FROM {t}")
    yield execute
    for t in _TRUNCATE:
        execute(f"DELETE FROM {t}")


@pytest.fixture
def embedding_ok():
    """Skip a test if the embedding service is unreachable."""
    from hermes.embedding import generate_embedding
    try:
        generate_embedding("ping")
    except Exception:
        pytest.skip("embedding service unreachable")
