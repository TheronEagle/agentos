"""Pluggable LLM client.

One interface, any backend: OpenAI, Anthropic, or anything speaking the
OpenAI /chat/completions schema (LiteLLM proxy, Ollama's OpenAI mode,
LM Studio, vLLM…). AgentOS never hardcodes a vendor.

The default provider is "none": deterministic stubs so the platform is
fully testable and demo-able with zero API keys. Real providers slot in
via environment configuration.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from agentos.utils.logging_utils import get_logger

log = get_logger(__name__)


class LLMError(RuntimeError):
    """Raised when the configured backend fails or returns unusable output."""


class BaseLLMClient(ABC):
    """Contract every backend implements. Agents depend on this, not vendors."""

    @abstractmethod
    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        """Return the model's text response for a prompt."""

    async def complete_json(self, prompt: str, *, system: str | None = None) -> Any:
        """Return parsed JSON from the model's response.

        Tolerates fenced code blocks; raises LLMError if no JSON object/array
        can be extracted.
        """
        text = await self.complete(prompt, system=system)
        return self._extract_json(text)

    @staticmethod
    def _extract_json(text: str) -> Any:
        # Strip markdown fences if present.
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
        candidate = fence.group(1).strip() if fence else text.strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
        # Last resort: first {...} or [...] span.
        for opener, closer in (("{", "}"), ("[", "]")):
            start = candidate.find(opener)
            end = candidate.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(candidate[start : end + 1])
                except json.JSONDecodeError:
                    continue
        raise LLMError(f"No parseable JSON in LLM response: {text[:200]!r}")


class OpenAICompatClient(BaseLLMClient):
    """Any endpoint implementing POST {base_url}/chat/completions."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=timeout)

    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = await self._client.post("/chat/completions", json={"model": self.model, "messages": messages})
        response.raise_for_status()
        payload = response.json()
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Malformed completion payload: {payload}") from exc


class AnthropicClient(BaseLLMClient):
    """Anthropic Messages API."""

    def __init__(self, model: str, api_key: str, timeout: float = 60.0) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com/v1",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        response = await self._client.post("/messages", json=body)
        response.raise_for_status()
        payload = response.json()
        try:
            return payload["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Malformed completion payload: {payload}") from exc


class NoneClient(BaseLLMClient):
    """Zero-dependency deterministic backend.

    Returns structured JSON derived from the prompt itself. This keeps every
    code path — planning, drafting, validation — exercisable in tests, CI,
    and air-gapped demos without a single API key.
    """

    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        lowered = prompt.lower()
        if "json array" in lowered or '"tasks"' in lowered:
            return json.dumps(
                [
                    {
                        "description": "Analyse request and gather required context",
                        "action": "generic.analyse",
                        "risk_level": "low",
                    },
                    {
                        "description": "Execute the requested outcome autonomously",
                        "action": "generic.execute",
                        "risk_level": "low",
                    },
                ]
            )
        if "json object" in lowered:
            return json.dumps(
                {"summary": "Completed deterministically without an LLM.", "metrics": {"mode": "deterministic"}}
            )
        return (
            "Deterministic draft (no LLM configured): AgentOS processed the request "
            "using its built-in heuristics. Configure AGENTOS_LLM_PROVIDER to enable "
            "generative behaviour."
        )


def create_llm_client(
    provider: str,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
) -> BaseLLMClient:
    """Factory: build the configured backend. Unknown providers raise loudly."""
    provider = (provider or "none").lower().strip()
    if provider == "none":
        return NoneClient()
    if provider == "openai":
        return OpenAICompatClient(model or "gpt-4o", api_key=api_key)
    if provider == "anthropic":
        if not api_key:
            raise LLMError("provider 'anthropic' requires AGENTOS_LLM_API_KEY")
        return AnthropicClient(model or "claude-sonnet-4", api_key=api_key)
    if provider == "openai-compatible":
        return OpenAICompatClient(model or "default", api_key=api_key, base_url=base_url or "http://localhost:4000/v1")
    raise LLMError(
        f"Unknown LLM provider {provider!r}. Supported: none, openai, anthropic, openai-compatible."
    )
