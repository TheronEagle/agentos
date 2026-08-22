"""OutcomeModule — the first-class citizen of Service-as-Software.

An outcome module is a self-contained, autonomous unit that owns an
outcome end to end: it decides whether it can handle a goal (accept),
decomposes it into tasks (plan), executes them without human babysitting
(execute), and self-checks the result before delivery (validate).

This is the contract that replaces "features behind a GUI". If you can
implement these four methods, you have a product.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel

from agentos.models import Capability, Execution, Goal, Outcome, Task


class OutcomeModule(ABC):
    """Base class for outcome modules.

    Subclasses set `name` / `description` and implement the four-method
    lifecycle. Everything else — discovery via /capabilities, routing,
    audit trails — comes for free from the engine and registry.
    """

    name: ClassVar[str]
    description: ClassVar[str]

    @abstractmethod
    def accept(self, goal: Goal) -> bool:
        """Can this module handle this goal? Pure predicate; no side effects."""

    @abstractmethod
    async def plan(self, goal: Goal) -> list[Task]:
        """Decompose the goal into executable tasks."""

    @abstractmethod
    async def execute(self, execution: Execution) -> Outcome:
        """Run all tasks autonomously. Return the delivered Outcome."""

    @abstractmethod
    async def validate(self, outcome: Outcome, execution: Execution) -> bool:
        """Self-check the outcome before delivery. False = engine fails the run."""

    def tools(self) -> list[Capability]:
        """Capabilities this module publishes for discovery (GET /capabilities)."""
        return []

    # ── Schema helpers used by the registry ──────────────────────────────────

    def goal_schema(self) -> dict[str, Any]:
        """JSON Schema of the goals this module accepts (best-effort, for discovery)."""
        params = getattr(self, "params_model", None)
        if isinstance(params, type) and issubclass(params, BaseModel):
            schema = params.model_json_schema()
        else:
            schema = {"type": "object", "properties": {}}
        return {
            "type": "object",
            "required": ["description"],
            "properties": {
                "description": {"type": "string", "minLength": 3},
                "type": {"const": self.name},
                "params": schema,
            },
        }

    def outcome_schema(self) -> dict[str, Any]:
        """JSON Schema of the delivered Outcome."""
        return Outcome.model_json_schema()
