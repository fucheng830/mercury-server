"""DB integration tests for counterexample gate (refutation links + threshold demotion).

Run with MERCURY_TEST_DB=hermes_test; skipped otherwise (db fixture gates it).
"""
import json

import pytest

pytestmark = pytest.mark.usefixtures("db")


def _vec(seed: int):
    import numpy as np
    v = np.zeros(1024, dtype="float32")
    v[seed % 1024] = 1.0
    return v.tolist()


def _write(content, project_id, **kw):
    from hermes.memory_service import write_memory
    return write_memory(
        content=content, stage="memory", type=kw.get("type", "ARCH"),
        importance=kw.get("importance", 3), status="active",
        project_id=project_id, embedding=_vec(kw.get("seed", 1)), auto_embed=False,
    )


def _proj(name):
    from hermes.memory_service import get_or_create_project
    return get_or_create_project(name, f"/{name}", "claude")["id"]


def test_v3_tables_exist():
    from hermes.db import execute_one
    for tbl in ("memory_refutations", "memory_events"):
        row = execute_one(
            "SELECT to_regclass(%s) AS c", (tbl,),
        )
        assert row and row["c"] == tbl, f"table {tbl} missing after init_db"
