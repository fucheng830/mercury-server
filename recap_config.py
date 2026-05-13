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

    config = {**_default_config(), **data}
    return _apply_env_overrides(config)


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """Override config values from environment variables (Docker-friendly)."""
    env_map = {
        "MERCURY_DB_HOST": ("hermes", "db", "host"),
        "MERCURY_DB_PORT": ("hermes", "db", "port", lambda v: int(v)),
        "MERCURY_DB_NAME": ("hermes", "db", "database"),
        "MERCURY_DB_USER": ("hermes", "db", "user"),
        "MERCURY_DB_PASSWORD": ("hermes", "db", "password"),
        "MERCURY_EMBEDDING_URL": ("hermes", "embedding", "api_base"),
    }

    for env_var, path in env_map.items():
        val = os.environ.get(env_var)
        if val is None:
            continue
        target = config
        for key in path[:-1]:
            target = target.setdefault(key, {})
        last = path[-1]
        if callable(last):
            key, convert = path[-2], last
            target = config
            for k in path[:-2]:
                target = target.setdefault(k, {})
            target[key] = convert(val)
        else:
            target[last] = val

    # Handle inline LLM API key (goes into recap.llm.providers.nvidia.api_key)
    if os.environ.get("MERCURY_LLM_API_KEY"):
        providers = config.setdefault("recap", {}).setdefault("llm", {}).setdefault("providers", {})
        providers.setdefault("nvidia", {})["api_key"] = os.environ["MERCURY_LLM_API_KEY"]

    return config


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
