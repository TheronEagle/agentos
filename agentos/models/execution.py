"""Execution — the audit trail of an autonomous run.

Every goal execution produces an immutable, inspectable trace: the plan,
each task attempt, every LLM call, every decision. Agents must be
debuggable; "the model did something" is not an explanation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from agentos.models.task import Task


class TraceEvent(BaseModel):
    """A single append-only entry in an execution trace."""

    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: Literal[
        "goal_accepted",
        "plan_created",
        "approval_requested",
        "approval_granted",
        "approval_denied",
        "task_started",
        "task_succeeded",
        "task_failed",
        "task_skipped",
        "llm_call",
        "a2a_message",
        "validation",
        "outcome_delivered",
        "webhook_sent",
        "error",
        "cancelled",
    ]
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    task_id: str | None = None
    agent_id: str | None = None


class Outcome(BaseModel):
    """The delivered result of a goal. What the delegating party actually wants."""

    summary: str = Field(..., description="Human/agent-readable statement of what was achieved.")
    artifacts: list[str] = Field(
        default_factory=list,
        description="Paths, URLs, or IDs of deliverables produced during execution.",
    )
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Quantified outcome data: tickets_resolved=47, tests_passed=true…",
    )
    validated: bool = Field(
        default=False,
        description="Whether the producing module's self-check passed before delivery.",
    )


class Execution(BaseModel):
    """Full lifecycle record of one goal → plan → tasks → outcome run."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(default_factory=lambda: f"exec_{uuid4().hex[:12]}")
    goal_id: str
    module: str | None = Field(
        default=None,
        description="Outcome module that claimed the goal.",
    )
    plan_summary: str | None = None
    status: Literal[
        "planning", "awaiting_approval", "running", "completed", "failed", "cancelled"
    ] = "planning"
    tasks: list[Task] = Field(default_factory=list)
    outcome: Outcome | None = None
    error: str | None = None
    trace: list[TraceEvent] = Field(
        default_factory=list,
        description="Append-only event log. Never rewritten, only extended.",
    )
    approvals: dict[str, Literal["granted", "denied"]] = Field(
        default_factory=dict,
        description="task_id → decision, for executions that hit approval gates.",
    )
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def add_event(
        self,
        kind: str,  # narrowed by TraceEvent validation below
        message: str,
        *,
        task_id: str | None = None,
        agent_id: str | None = None,
        **data: Any,
    ) -> TraceEvent:
        event = TraceEvent(
            kind=kind,  # type: ignore[arg-type]
            message=message,
            data=data,
            task_id=task_id,
            agent_id=agent_id,
        )
        self.trace.append(event)
        return event

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()
