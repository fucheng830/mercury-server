"""One-time migration: MEMORY.md entries + historical recaps → PostgreSQL."""
import json
import logging
import os
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


def migrate_md_entries() -> Dict[str, int]:
    """Migrate MEMORY.md and USER.md entries to core layer."""
    from recap_config import get_hermes_config
    from hermes.memory_service import write_memory

    home = Path(os.path.expanduser(get_hermes_config().get("home", "~/.hermes")))
    memories_dir = home / "memories"
    migrated = 0

    for target in ("memory", "user"):
        md_file = memories_dir / f"{target}.md"
        if not md_file.exists():
            logger.info(f"No {md_file} to migrate")
            continue

        content = md_file.read_text(encoding="utf-8")
        entries = [e.strip() for e in content.split("§") if e.strip()]

        for entry in entries:
            if entry.startswith("#") or len(entry) < 10:
                continue
            tags = ["migrated"]
            if target == "user":
                tags.append("user-profile")
            write_memory(
                content=entry,
                layer="core",
                source="migration",
                importance=4,
                tags=tags,
                auto_embed=True,
            )
            migrated += 1

    logger.info(f"Migrated {migrated} MD entries to core layer")
    return {"md_entries": migrated}


def migrate_historical_recaps() -> Dict[str, int]:
    """Migrate all existing recap JSON files to episodic layer."""
    from recap_config import get_storage_config
    from hermes.iteration import run_daily_ingestion

    recap_dir = Path(os.path.expanduser(get_storage_config().get("recap_dir", "~/.hermes/recaps")))
    if not recap_dir.exists():
        return {"recaps": 0}

    total_ingested = 0
    recap_files = sorted(recap_dir.glob("*.json"))

    for rf in recap_files:
        date = rf.stem
        try:
            result = run_daily_ingestion(date)
            total_ingested += result.get("ingested", 0)
            logger.info(f"Migrated recap {date}: {result.get('ingested', 0)} entries")
        except Exception as e:
            logger.error(f"Failed to migrate recap {date}: {e}")

    logger.info(f"Migrated {total_ingested} entries from {len(recap_files)} recaps")
    return {"recaps": len(recap_files), "total_entries": total_ingested}


def run_full_migration() -> Dict[str, int]:
    """Run all migrations."""
    from hermes.db import init_db
    init_db()

    md_result = migrate_md_entries()
    recap_result = migrate_historical_recaps()

    return {**md_result, **recap_result}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = run_full_migration()
    print(f"Migration complete: {result}")
