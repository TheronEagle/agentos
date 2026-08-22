"""Natural Language Interface — compile plain English into a Goal.

NLI is NOT a chatbot. There is no conversation loop. It is a compiler:
one utterance in, one typed Goal out (or an error). The Goal then flows
through the same engine as any API submission — because under Service-as-
Software, natural language is just another client protocol.

With an LLM configured it parses free text into structured Goals.
Without one, it applies deterministic keyword heuristics so the endpoint
works offline and in tests.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agentos.utils.llm_client import BaseLLMClient

router = APIRouter(prefix="/nli", tags=["nli"])


class Utterance(BaseModel):
    text: str = Field(..., min_length=3, description="Raw natural-language outcome request.")
    requested_by: str = "anonymous"


class CompiledGoal(BaseModel):
    description: str
    type: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(..., ge=0.0, le=1.0)
    compiled_by: str  # "llm" | "heuristics"


_SYSTEM = (
    "You compile an outcome request into ONE JSON object (no prose): "
    '{"description": "<imperative outcome>", "type": "support"|"compliance"|'
    '"codebase"|null, "params": {}}. Map the request onto the closest of the '
    "three types; null if none fit."
)

_KEYWORDS: list[tuple[str, str]] = [
    ("soc-2", "compliance"),
    ("soc2", "compliance"),
    ("compliance report", "compliance"),
    ("evidence report", "compliance"),
    ("audit", "compliance"),
    ("ticket", "support"),
    ("helpdesk", "support"),
    ("tier-1", "support"),
    ("customer issue", "support"),
    ("dependency", "codebase"),
    ("requirements.txt", "codebase"),
    ("pyproject", "codebase"),
    ("bump", "codebase"),
]

_PERIOD = re.compile(r"\b(Q[1-4])\b")
_YEAR = re.compile(r"\b(20\d{2})\b")
_HOURS = re.compile(r"\blast\s+(\d+)\s*hours?\b")
_TIER = re.compile(r"\btier-(\d)\b")


def _heuristic_compile(text: str) -> dict[str, Any]:
    lowered = text.lower()
    goal_type: str | None = None
    for needle, kind in _KEYWORDS:
        if needle in lowered:
            goal_type = kind
            break

    params: dict[str, Any] = {}
    hours = _HOURS.search(lowered)
    if hours:
        params["within_hours"] = int(hours.group(1))
    tier = _TIER.search(lowered)
    if tier and goal_type == "support":
        params["tier"] = f"tier-{tier.group(1)}"

    period_match = _PERIOD.search(text.upper())
    year_match = _YEAR.search(text)
    if period_match and goal_type == "compliance":
        params["period"] = (
            f"{period_match.group(1)}-{year_match.group(1)}" if year_match else period_match.group(1)
        )

    return {"type": goal_type, "params": params}


class NLIService:
    """Compile-only NLI. No sessions, no memory, no chat."""

    def __init__(self, llm: BaseLLMClient | None) -> None:
        self.llm = llm

    async def compile(self, utterance: Utterance) -> CompiledGoal:
        if self.llm is not None:
            try:
                raw = await self.llm.complete_json(utterance.text, system=_SYSTEM)
                if isinstance(raw, dict) and raw.get("description"):
                    return CompiledGoal(
                        description=str(raw["description"]),
                        type=raw.get("type"),
                        params=raw.get("params") or {},
                        confidence=float(raw.get("confidence", 0.9)),
                        compiled_by="llm",
                    )
            except Exception:  # noqa: BLE001 - fall through to heuristics
                pass
        parsed = _heuristic_compile(utterance.text)
        return CompiledGoal(
            description=utterance.text.strip(),
            type=parsed["type"],
            params=parsed["params"],
            confidence=0.6 if parsed["type"] else 0.2,
            compiled_by="heuristics",
        )


def build_nli_router(llm: BaseLLMClient | None) -> APIRouter:
    service = NLIService(llm)

    @router.post(
        "/compile",
        openapi_extra={
            "x-agent-instructions": (
                "Translate one utterance into a Goal, then POST it to /goals. "
                "Two-step by design: compilation and delegation stay separable."
            )
        },
    )
    async def compile_utterance(payload: Utterance) -> dict[str, Any]:
        compiled = await service.compile(payload)
        return {
            "goal": {
                "description": compiled.description,
                "type": compiled.type,
                "params": compiled.params,
                "requested_by": payload.requested_by,
            },
            "confidence": compiled.confidence,
            "compiled_by": compiled.compiled_by,
            "next_step": "POST this goal object to /goals to delegate the outcome.",
        }

    return router


from agentos.core.registry import Registry  # noqa: E402  (used by type checkers only)


def nli_router(registry: Registry, llm: BaseLLMClient | None) -> APIRouter:  # pragma: no cover
    """Alias kept for discoverability."""
    return build_nli_router(llm)
