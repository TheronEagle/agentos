"""Capability — the machine-readable contract agents discover at runtime.

Agents never read documentation to learn what they can do. They query the
registry and receive typed schemas (JSON Schema, via Pydantic).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Capability(BaseModel):
    """A discoverable unit of functionality exposed to agents."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Unique key, e.g. 'github.update_dependencies'.")
    kind: Literal["tool", "outcome_module", "integration", "a2a"] = Field(
        ...,
        description=(
            "tool = single action; outcome_module = full goal→outcome pipeline; "
            "integration = external system adapter; a2a = agent-to-agent channel."
        ),
    )
    description: str
    input_schema: dict[str, Any] = Field(
        ...,
        description="JSON Schema for valid inputs. Generated from typed models.",
    )
    output_schema: dict[str, Any] | None = Field(
        default=None,
        description="JSON Schema for outputs, when statically known.",
    )
    risk_level: Literal["low", "medium", "high"] = "low"
    module: str | None = Field(
        default=None,
        description="Owning outcome module, if any.",
    )
    tags: list[str] = Field(default_factory=list)
