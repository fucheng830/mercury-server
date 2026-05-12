"""Recap configuration loader."""
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


_config: Optional[Dict[str, Any]] = None


def get_config() -> Dict[str, Any]:
    global _config
    if _config is None:
        _config = _load_config()
    return _config


def reload_config() -> Dict[str, Any]:
    global _config
    _config = _load_config()
    return _config


def _load_config() -> Dict[str, Any]:
    """Load config from project dir, fall back to ~/.hermes/config.yaml."""
    project_config = Path(__file__).parent / "config.yaml"
    hermes_config = Path(os.path.expanduser("~/.hermes/config.yaml"))

    source = project_config if project_config.exists() else hermes_config
    if not source.exists():
        return _default_config()

    with open(source, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return {**_default_config(), **data}


def _default_config() -> Dict[str, Any]:
    return {
        "recap": {
            "schedule": {"enabled": False, "cron": "0 23 * * *", "timezone": "Asia/Shanghai"},
            "llm": {"default": "ollama", "providers": {}},
            "memory": {"auto_write": False, "max_entries_per_day": 3, "importance_filter": "high"},
            "storage": {"recap_dir": "~/.hermes/recaps"},
        },
        "hermes": {
            "home": "~/.hermes",
            "mcp_src": "",
            "db": {
                "host": "192.168.0.17",
                "port": 5432,
                "database": "hermes_memory",
                "user": "hermes",
                "password": "",
                "pool_size": 5,
            },
            "embedding": {
                "api_base": "http://192.168.0.13:11434",
                "model": "bge-m3",
                "dimensions": 1024,
            },
            "iteration": {
                "daily_cron": "59 23 * * *",
                "weekly_cron": "0 1 * * 0",
                "monthly_cron": "0 2 1 * *",
            },
        },
    }


def get_llm_config() -> Dict[str, Any]:
    return get_config()["recap"]["llm"]


def get_memory_config() -> Dict[str, Any]:
    return get_config()["recap"]["memory"]


def get_storage_config() -> Dict[str, Any]:
    return get_config()["recap"]["storage"]


def get_hermes_config() -> Dict[str, Any]:
    return get_config()["hermes"]


def get_db_config() -> Dict[str, Any]:
    return get_config().get("hermes", {}).get("db", {})


def get_embedding_config() -> Dict[str, Any]:
    return get_config().get("hermes", {}).get("embedding", {})


def get_iteration_config() -> Dict[str, Any]:
    return get_config().get("hermes", {}).get("iteration", {})


def get_ingest_config() -> Dict[str, Any]:
    return get_config().get("hermes", {}).get("ingest", {})
