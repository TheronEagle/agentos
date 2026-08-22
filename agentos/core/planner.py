"""Planner — Goal → List[Task] decomposition.

Strategy, in order:
  1. A registered outcome module claims the goal (via accept()) and plans
     with its domain logic. This is the primary path.
  2. If no module claims it and an LLM is configured, the LLM proposes a
     JSON task plan which is validated into typed Tasks.
  3. Fallback: a deterministic generic plan so the platform degrades
     gracefully instead of erroring.

The planner never executes anything — it only produces typed work units
for the engine to schedule.
"""

from __future__ import annotations

from agentos.core.registry import Registry
from agentos.models import Goal, Task
from agentos.utils.llm_client import BaseLLMClient
from agentos.utils.logging_utils import get_logger

log = get_logger(__name__)

_PLANNER_SYSTEM = (
    "You are the planning unit of an autonomous execution platform. "
    "Decompose the goal into the smallest number of concrete tasks that "
    "achieve it. Respond ONLY with a JSON array; each element: "
    '{"description": str, "action": "<integration>.<verb>", '
    '"params": object, "risk_level": "low"|"medium"|"high", '
    '"requires_approval": bool}. No prose.'
)


class PlanningError(RuntimeError):
    """No module claimed the goal and no fallback could plan it."""


class Planner:
    def __init__(self, registry: Registry, llm: BaseLLMClient | None = None) -> None:
        self.registry = registry
        self.llm = llm

    async def plan(self, goal: Goal) -> tuple[list[Task], str | None]:
        """Return (tasks, claiming_module_name).

        Raises PlanningError only when nothing can even propose a plan.
        """
        # 1. Domain module claims the goal.
        if goal.goal_type:
            module = self.registry.module(goal.goal_type)
            if module is not None and module.accept(goal):
                log.info("module claimed goal by type hint", extra={"module": module.name, "goal_id": goal.id})
                return await module.plan(goal), module.name

        for module in self.registry.find_modules_for(goal):
            log.info("module claimed goal via accept()", extra={"module": module.name, "goal_id": goal.id})
            return await module.plan(goal), module.name

        # 2. LLM-proposed plan.
        tasks = await self._llm_plan(goal)
        if tasks is not None:
            return tasks, None

        raise PlanningError(
            f"No outcome module accepts goal {goal.id!r} ({goal.description!r}) "
            "and no LLM planner is configured."
        )

    async def _llm_plan(self, goal: Goal) -> list[Task] | None:
        if self.llm is None:
            return None
        prompt = (
            f"Goal: {goal.description}\n"
            f"Params: {goal.params}\n"
            f"Constraints: {goal.constraints}\n"
            "Respond ONLY with a JSON array of tasks."
        )
        try:
            raw = await self.llm.complete_json(prompt, system=_PLANNER_SYSTEM)
        except Exception as exc:  # noqa: BLE001 - degrade, don't crash
            log.warning("LLM planning failed; falling back", extra={"goal_id": goal.id, "error": str(exc)})
            return None
        if not isinstance(raw, list):
            return None
        tasks: list[Task] = []
        for i, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            tasks.append(
                Task(
                    description=str(item.get("description", f"Step {i + 1}")),
                    action=str(item.get("action", "generic.execute")),
                    params=item.get("params") or {},
                    risk_level=item.get("risk_level", "low"),
                    requires_approval=bool(item.get("requires_approval", False)),
                    goal_id=goal.id,
                )
            )
        return tasks or None


def default_generic_plan(goal: Goal) -> list[Task]:
    """Deterministic plan used when no module/LLM is available."""
    return [
        Task(
            description=f"Gather context for: {goal.description}",
            action="generic.analyse",
            params={"goal_id": goal.id},
            goal_id=goal.id,
        ),
        Task(
            description=f"Execute outcome for: {goal.description}",
            action="generic.execute",
            params={"goal_id": goal.id},
            goal_id=goal.id,
        ),
    ]
