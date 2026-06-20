"""Multi-backend LLM service using OpenAI-compatible API."""
import os
import json
import logging
from typing import Any, Dict, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


def _parse_json(raw: str) -> Dict[str, Any]:
    """Parse LLM JSON output robustly: strip markdown fences; fall back to first {..} last }."""
    s = (raw or "").strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        lo, hi = s.find("{"), s.rfind("}")
        if lo != -1 and hi != -1 and hi > lo:
            return json.loads(s[lo:hi + 1])
        raise


class LLMService:
    def __init__(self, llm_config: Dict[str, Any]):
        self._configs = llm_config.get("providers", {})
        self._default = llm_config.get("default", "ollama")

        if not self._configs:
            raise ValueError("No LLM providers configured")

    def _resolve_key(self, provider_config: Dict[str, Any]) -> str:
        """Resolve API key from env var or literal value."""
        env_var = provider_config.get("api_key_env")
        if env_var:
            key = os.environ.get(env_var, "")
            if not key:
                raise ValueError(f"Environment variable {env_var} not set")
            return key
        return provider_config.get("api_key", "")

    def _get_client(self, provider_name: Optional[str] = None) -> tuple:
        """Get OpenAI client and model for a provider."""
        name = provider_name or self._default
        config = self._configs.get(name)
        if not config:
            raise ValueError(f"Provider '{name}' not configured")

        client = OpenAI(
            base_url=config["api_base"],
            api_key=self._resolve_key(config),
        )
        return client, config.get("model", ""), config.get("max_tokens", 2000)

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        provider_name: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate completion and return raw text."""
        client, model, default_max = self._get_client(provider_name)
        tokens = max_tokens if max_tokens is not None else default_max

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=tokens,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        provider_name: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate completion and parse as JSON."""
        raw = self.generate(system_prompt, user_prompt, provider_name, max_tokens=max_tokens)
        try:
            return _parse_json(raw)
        except json.JSONDecodeError as e:
            logger.error(f"LLM returned invalid JSON: {e}\nRaw: {raw[:500]}")
            raise ValueError(f"LLM returned invalid JSON: {e}") from e
