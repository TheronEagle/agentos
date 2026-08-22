"""Goal — an outcome request.

A Goal is not a form submission and not a chat message. It is a
machine-readable statement of *what done looks like*, plus optional
constraints. Humans (or other agents) delegate; the engine owns the how.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:64] or "goal"


class Goal(BaseModel):
    """An outcome someone wants delivered. Never 'how' — always 'what'."""

    model_config = ConfigDict(populate_by_name=True)

    description: str = Field(
        ...,
        min_length=3,
        max_length=10_000,
        description=(
            "The outcome being delegated, stated as a result. "
            "'Resolve all unresolved tier-1 tickets from last 24h', "
            "not 'log into the helpdesk and click around'."
        ),
    )
    goal_type: str | None = Field(
        default=None,
        alias="type",
        description=(
            "Optional coarse category hint used for routing "
            "(e.g. 'support', 'compliance', 'codebase'). If omitted, the "
            "engine asks every registered module via accept()."
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured parameters that refine the outcome.",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description=(
            "Hard boundaries the execution must respect, e.g. "
            "'no external emails', 'budget < $50'."
        ),
    )
    success_criteria: str | None = Field(
        default=None,
        description="Optional explicit definition of 'done' the validator can check.",
    )
    callback_url: str | None = Field(
        default=None,
        description=(
            "Where the finished Outcome should be POSTed when ready. "
            "Agents poll or listen; nobody waits synchronously."
        ),
    )
    requested_by: str = Field(
        default="anonymous",
        description="Identity of the delegating party (human team, agent ID…).",
    )
    approval_policy_override: Literal["never", "risky_only", "required"] | None = Field(
        default=None,
        description="Per-goal override of the platform autonomy policy.",
    )
    id: str = Field(default_factory=lambda: f"goal_{uuid4().hex[:12]}")
    status: Literal[
        "queued", "planning", "awaiting_approval", "executing", "completed", "failed", "cancelled"
    ] = "queued"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("description")
    @classmethod
    def _description_not_whitespace(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("description must contain non-whitespace characters")
        return v.strip()

    @property
    def slug(self) -> str:
        return _slugify(self.description)

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)
