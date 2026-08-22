"""Orchestrator — multi-agent coordination for complex outcomes.

Where the engine runs ONE module's plan, the orchestrator composes
several specialised agents: it decomposes a goal into sub-goals, routes
each to an agent (or another outcome module), fans work out over the A2A
bus, and folds partial results into one delivered Outcome.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentos.agents.a2a import A2ABus
from agentos.agents.worker import Worker
from agentos.core.engine import Engine
from agentos.core.registry import Registry
from agentos.models import Execution, Goal, Outcome
from agentos.utils.logging_utils import get_logger

log = get_logger(__name__)


class Orchestrator:
    """Coordinates multiple agents/modules on composite goals.

    Sub-goal convention: prefix each line of `goal.params["sub_goals"]`
    with ">", e.g.::

        Goal(description="Ship the release",
             params={"sub_goals": ["> resolve open tickets", "> bump deps"]})
    """

    def __init__(self, engine: Engine, registry: Registry, bus: A2ABus | None = None) -> None:
        self.engine = engine
        self.registry = registry
        self.bus = bus or A2ABus()

    def spawn_worker(self, role: str, domain: str | None = None) -> Worker:
        worker = Worker(role=role, registry=self.registry, domain=domain)
        self.bus.register(worker)
        return worker

    async def run(self, goal: Goal, max_parallel: int = 3) -> Execution:
        """Execute a composite goal by delegating sub-goals to modules in parallel."""
        sub_goal_texts: list[str] = list(goal.params.get("sub_goals") or [])
        if not sub_goal_texts:
            # Nothing to fan out — delegate straight through the engine.
            return await self.engine.submit(goal)

        from agentos.models import Execution as _Execution

        execution = _Execution(goal_id=goal.id, module="orchestrator", status="running")
        execution.add_event("a2a_message", f"orchestrating {len(sub_goal_texts)} sub-goal(s)")

        sem = asyncio.Semaphore(max_parallel)

        async def delegate(text: str) -> dict[str, Any]:
            stripped = text[1:].strip() if text.strip().startswith(">") else text.strip()
            sub = Goal(
                description=stripped,
                requested_by=f"orchestrator:{goal.id}",
                callback_url=goal.callback_url,
            )
            matches = self.registry.find_modules_for(sub)
            if not matches:
                return {"sub_goal": stripped, "status": "unroutable"}
            module_name = matches[0].name
            # Announce delegation on the bus so worker agents observe the plan.
            await self.bus.broadcast(
                sender="orchestrator", message_type="announce", payload={"delegated_to": module_name, "goal": stripped}
            )
            async with sem:
                try:
                    sub_execution = await self.engine.submit(sub)
                    final = await self.engine.wait_for(sub_execution.id, timeout=120)
                    return {
                        "sub_goal": stripped,
                        "module": module_name,
                        "execution_id": sub_execution.id,
                        "status": final.status if final else "unknown",
                        "outcome": final.outcome.model_dump() if final and final.outcome else None,
                    }
                except Exception as exc:  # noqa: BLE001
                    return {"sub_goal": stripped, "module": module_name, "status": "failed", "error": str(exc)}

        results = await asyncio.gather(*(delegate(text) for text in sub_goal_texts))

        succeeded = [r for r in results if r.get("status") == "completed"]
        failed = [r for r in results if r.get("status") != "completed"]
        all_succeeded = len(failed) == 0 and len(succeeded) > 0

        execution.tasks = []  # orchestration has no own tasks; children have executions
        execution.outcome = Outcome(
            summary=(
                f"Composite outcome: {len(succeeded)}/{len(results)} sub-goals completed. "
                + "; ".join(r["sub_goal"] for r in results)
            ),
            metrics={
                "sub_goals_total": len(results),
                "sub_goals_completed": len(succeeded),
                "sub_execution_ids": [r.get("execution_id") for r in results if r.get("execution_id")],
            },
            validated=all_succeeded,
        )
        execution.status = "completed" if all_succeeded else ("failed" if not succeeded else "completed")
        if not all_succeeded:
            execution.error = f"{len(failed)} sub-goal(s) did not complete"
        execution.add_event("outcome_delivered", execution.outcome.summary)
        await self.engine.store.save_execution(execution)
        await self.engine._emit(execution)
        return execution
