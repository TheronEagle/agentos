"""Task — a decomposed unit of work.

Planners turn Goals into Tasks. Tasks are small, typed, and carry enough
context that a Worker agent can execute them without asking a human
anything.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Task(BaseModel):
    """One executable step toward an outcome."""

    model_config = ConfigDict(populate_by_name=True)

    description: str = Field(
        ...,
        description="Imperative statement of the work: 'Fetch open tier-1 tickets'.",
    )
    action: str = Field(
        default="generic.execute",
        description=(
            "Machine-routable action key, conventionally '<integration>.<verb>' "
            "(e.g. 'github.update_dependencies'). Workers resolve actions "
            "through the capability registry."
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments for the action. JSON-serialisable by contract.",
    )
    risk_level: Literal["low", "medium", "high"] = Field(
        default="low",
        description=(
            "Blast radius of this task. 'high' tasks trip approval gates "
            "when the platform policy is 'risky_only' or 'required'."
        ),
    )
    requires_approval: bool = Field(
        default=False,
        description="Force an approval gate regardless of policy.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="Task IDs that must complete before this one starts.",
    )
    id: str = Field(default_factory=lambda: f"task_{uuid4().hex[:12]}")
    goal_id: str | None = None
    status: Literal["pending", "awaiting_approval", "running", "succeeded", "failed", "skipped"] = (
        "pending"
    )
    assigned_agent: str | None = Field(
        default=None,
        description="Agent ID that claimed/owns execution of this task.",
    )
    result: Any | None = Field(
        default=None,
        description="Structured output produced on success.",
    )
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def mark_running(self) -> None:
        self.status = "running"
        self.started_at = datetime.now(UTC)

    def mark_succeeded(self, result: Any) -> None:
        self.status = "succeeded"
        self.result = result
        self.finished_at = datetime.now(UTC)

    def mark_failed(self, error: str) -> None:
        self.status = "failed"
        self.error = error
        self.finished_at = datetime.now(UTC)

    @property
    def is_terminal(self) -> bool:
        return self.status in {"succeeded", "failed", "skipped"}
