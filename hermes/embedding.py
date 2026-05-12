"""Embedding generation via bge-m3 on Ollama."""
import logging
from typing import List

import requests

logger = logging.getLogger(__name__)


def _get_config():
    from recap_config import get_embedding_config
    return get_embedding_config()


def generate_embedding(text: str) -> List[float]:
    """Generate embedding for a single text string. Returns 1024-dim vector."""
    cfg = _get_config()
    api_base = cfg.get("api_base", "http://192.168.0.13:11434")
    model = cfg.get("model", "bge-m3")

    resp = requests.post(
        f"{api_base}/api/embed",
        json={"model": model, "input": text},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["embeddings"][0]


def generate_embeddings_batch(texts: List[str], batch_size: int = 10) -> List[List[float]]:
    """Generate embeddings for multiple texts. Batches to avoid overload."""
    cfg = _get_config()
    api_base = cfg.get("api_base", "http://192.168.0.13:11434")
    model = cfg.get("model", "bge-m3")

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = requests.post(
            f"{api_base}/api/embed",
            json={"model": model, "input": batch},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        all_embeddings.extend(data["embeddings"])

    return all_embeddings
