"""Execution engine — turns Goals into delivered Outcomes.

Lifecycle per goal:

    submit → route (registry accept) → plan (planner) → [approval gate?]
           → execute tasks in dependency order → validate outcome
           → deliver (callback webhook) → terminal state.

The engine returns an ExecutionID immediately and owns everything after
that. Nobody — human or agent — watches a progress bar.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC
from typing import Any

from agentos.core.planner import Planner
from agentos.core.registry import Registry
from agentos.core.state import BaseStore
from agentos.models import Execution, Goal, Outcome, Task
from agentos.utils.logging_utils import get_logger

log = get_logger(__name__)


class RoutingError(RuntimeError):
    """No registered outcome module accepts the goal."""


class Engine:
    """Owns the full lifecycle: goal in, outcome out, audit trail throughout."""

    def __init__(
        self,
        registry: Registry,
        planner: Planner,
        store: BaseStore,
        llm: Any | None = None,
        approval_policy: str = "never",
        max_concurrency: int = 4,
    ) -> None:
        self.registry = registry
        self.planner = planner
        self.store = store
        self.llm = llm
        self.approval_policy = approval_policy
        self._max_concurrency = max_concurrency
        self._executions_in_flight: dict[str, asyncio.Task[None]] = {}
        # Hooks the interfaces layer can attach (webhooks, A2A announcements…).
        self.on_event: list[Callable[[Execution, dict[str, Any]], Any]] = []

    # ── Submission ───────────────────────────────────────────────────────────

    async def route_goal(self, goal: Goal) -> str:
        """Validate routability and persist the goal. Returns the execution id
        only after planning; use `submit` for the full async handoff."""
        if not self.registry.find_modules_for(goal):
            raise RoutingError(
                f"No outcome module accepts goal: {goal.description!r}"
            )
        await self.store.save_goal(goal)
        return goal.id

    async def submit(self, goal: Goal) -> Execution:
        """Accept a Goal, plan it, kick off autonomous execution.

        Returns an Execution carrying an ExecutionID. Execution proceeds in
        a background task; poll GET /executions/{id} or supply callback_url.
        """
        claimed: tuple[list[Task], str | None] | None = None
        module_name: str | None = None

        if goal.goal_type:
            module = self.registry.module(goal.goal_type)
            if module is not None and module.accept(goal):
                claimed = (await module.plan(goal), module.name)

        if claimed is None:
            matches = self.registry.find_modules_for(goal)
            if not matches:
                # Persist for auditability even though we reject it.
                await self.store.save_goal(goal)
                goal.status = "failed"
                await self.store.save_goal(goal)
                raise RoutingError(f"No outcome module accepts goal: {goal.description!r}")
            module_name = matches[0].name
            claimed = (await matches[0].plan(goal), module_name)

        tasks, owner = claimed
        execution = Execution(
            goal_id=goal.id,
            module=owner,
            status="planning",
            tasks=tasks,
            plan_summary=" → ".join(t.description for t in tasks[:5]) + (" …" if len(tasks) > 5 else ""),
        )
        execution.add_event("plan_created", f"Planned {len(tasks)} task(s)", tasks=[t.description for t in tasks])

        # Opt-in approval gate: pause synchronously so the caller observes
        # status=awaiting_approval immediately (no race with the run loop).
        gate_task_id = self._approval_gate_needed(execution)
        if gate_task_id is not None:
            gated = next(t for t in execution.tasks if t.id == gate_task_id)
            gated.status = "awaiting_approval"
            execution.status = "awaiting_approval"
            execution.add_event("approval_requested", "Awaiting human approval (opt-in gate)", task_id=gate_task_id)

        goal.status = "awaiting_approval" if execution.status == "awaiting_approval" else "planning"
        goal.touch()
        await self.store.save_goal(goal)
        await self.store.save_execution(execution)

        if execution.status == "awaiting_approval":
            return execution  # resumes via approve_and_resume()

        bg = asyncio.create_task(self._run(execution.id))
        self._executions_in_flight[execution.id] = bg
        bg.add_done_callback(lambda _t: self._executions_in_flight.pop(execution.id, None))
        return execution

    # ── Execution ────────────────────────────────────────────────────────────

    async def _run(self, execution_id: str) -> None:
        execution = await self.store.get_execution(execution_id)
        if execution is None:  # pragma: no cover - defensive
            log.error("execution vanished before run", extra={"execution_id": execution_id})
            return
        goal = await self.store.get_goal(execution.goal_id)
        if goal is None:  # pragma: no cover - defensive
            execution.status = "failed"
            execution.error = "goal record missing"
            await self.store.save_execution(execution)
            return

        try:
            await self._execute_all(execution, goal)
        except Exception as exc:  # noqa: BLE001 - engine owns failure semantics
            log.exception("execution failed", extra={"execution_id": execution.id})
            execution.status = "failed"
            execution.error = str(exc)
            execution.add_event("error", str(exc))
            goal.status = "failed"
            goal.touch()
            await self.store.save_goal(goal)
        finally:
            execution.finished_at = execution.finished_at or _now()
            await self.store.save_execution(execution)
            await self._emit(execution)

    def _approval_gate_needed(self, execution: Execution) -> str | None:
        """First task id requiring a gate under the active policy, else None."""
        policy = self.approval_policy
        if policy == "required":
            pending = [t for t in execution.tasks if t.status == "pending"]
            return pending[0].id if pending else None
        if policy == "risky_only":
            risky = [
                t
                for t in execution.tasks
                if t.status == "pending" and (t.risk_level == "high" or t.requires_approval)
            ]
            return risky[0].id if risky else None
        return None

    async def approve_and_resume(self, execution_id: str, decision: str, approver: str) -> Execution | None:
        """Apply an approval decision and resume execution if granted."""
        execution = await self.store.get_execution(execution_id)
        if execution is None or execution.status != "awaiting_approval":
            return None
        gated = next((t for t in execution.tasks if t.status == "awaiting_approval"), None)
        task_id = gated.id if gated else ""
        execution.approvals[task_id] = "granted" if decision == "grant" else "denied"
        execution.add_event(
            "approval_granted" if decision == "grant" else "approval_denied",
            f"{decision} by {approver}",
            task_id=task_id,
        )
        if decision != "grant" or gated is None:
            execution.status = "cancelled"
            execution.finished_at = _now()
            await self.store.save_execution(execution)
            await self._emit(execution)
            return execution

        gated.status = "pending"  # cleared to run
        execution.status = "running"
        await self.store.save_execution(execution)
        bg = asyncio.create_task(self._resume(execution.id))
        self._executions_in_flight[execution.id] = bg
        bg.add_done_callback(lambda _t: self._executions_in_flight.pop(execution.id, None))
        return execution

    async def _resume(self, execution_id: str) -> None:
        execution = await self.store.get_execution(execution_id)
        if execution is None:  # pragma: no cover
            return
        goal = await self.store.get_goal(execution.goal_id)
        if goal is None:  # pragma: no cover
            return
        try:
            await self._execute_all(execution, goal)
        except Exception as exc:  # noqa: BLE001
            execution.status = "failed"
            execution.error = str(exc)
            execution.add_event("error", str(exc))
        finally:
            execution.finished_at = execution.finished_at or _now()
            await self.store.save_execution(execution)
            await self._emit(execution)

    async def _execute_all(self, execution: Execution, goal: Goal) -> None:
        execution.status = "running"
        goal.status = "executing"
        goal.touch()
        await self.store.save_goal(goal)
        execution.add_event("task_started", "Execution started")

        module = self.registry.module(execution.module) if execution.module else None

        ready = [t for t in execution.tasks if t.status == "pending"]
        sem = asyncio.Semaphore(self._max_concurrency)

        async def run_one(task: Task) -> None:
            deps_ok = all(
                dep_status(execution, d) == "succeeded" for d in task.depends_on
            )
            if not deps_ok:
                # Never ran — mark skipped, not failed. The execution still
                # fails overall, but the trace distinguishes "did work and
                # broke" from "was never eligible to run".
                task.status = "skipped"
                task.error = "upstream dependency did not succeed"
                task.finished_at = _now()
                execution.add_event("task_skipped", task.description, task_id=task.id)
                return
            async with sem:
                task.mark_running()
                execution.add_event("task_started", task.description, task_id=task.id)
                try:
                    if module is not None and hasattr(module, "run_task") and callable(module.run_task):
                        result = await module.run_task(task, execution, goal)  # type: ignore[attr-defined]
                    else:
                        result = {"ok": True, "task": task.description}
                    task.mark_succeeded(result)
                    execution.add_event("task_succeeded", task.description, task_id=task.id)
                except Exception as exc:  # noqa: BLE001
                    task.mark_failed(str(exc))
                    execution.add_event("task_failed", f"{task.description}: {exc}", task_id=task.id)

        await asyncio.gather(*(run_one(t) for t in ready))

        failed = [t for t in execution.tasks if t.status == "failed"]
        if failed:
            execution.status = "failed"
            execution.error = f"{len(failed)} task(s) failed: {failed[0].error!r}"
            goal.status = "failed"
            goal.touch()
            await self.store.save_goal(goal)
            return

        # ── Outcome assembly + validation ────────────────────────────────
        if module is not None:
            outcome = await module.execute(execution)
            valid = await module.validate(outcome, execution)
            execution.add_event("validation", f"module validated={valid}")
            if not valid:
                execution.status = "failed"
                execution.error = "outcome failed module self-validation"
                goal.status = "failed"
                goal.touch()
                await self.store.save_goal(goal)
                return
        else:
            outcome = Outcome(
                summary=f"Completed {len(execution.tasks)} task(s) for: {goal.description}",
                metrics={"tasks": len(execution.tasks)},
                validated=True,
            )

        outcome.validated = True
        execution.outcome = outcome
        execution.status = "completed"
        goal.status = "completed"
        goal.touch()
        execution.add_event("outcome_delivered", outcome.summary)
        await self.store.save_goal(goal)

    # ── Delivery ─────────────────────────────────────────────────────────────

    async def wait_for(self, execution_id: str, timeout: float = 60.0, poll_interval: float = 0.05) -> Execution | None:
        """Block until an execution reaches a terminal state (or timeout).

        In-process convenience for orchestrators/tests; remote callers poll
        GET /executions/{id} or use webhooks instead.
        """
        import time

        deadline = time.monotonic() + timeout
        while True:
            execution = await self.store.get_execution(execution_id)
            if execution is not None and execution.status in {"completed", "failed", "cancelled"}:
                return execution
            if time.monotonic() > deadline:
                return None
            await asyncio.sleep(poll_interval)

    def on_delivery_hook(self, hook: Callable[[Execution, dict[str, Any]], Any]) -> None:
        """Register a callback invoked with every terminal execution."""
        self.on_event.append(hook)

    async def _emit(self, execution: Execution) -> None:
        payload = {
            "event": "execution.finished",
            "execution_id": execution.id,
            "goal_id": execution.goal_id,
            "status": execution.status,
            "outcome": execution.outcome.model_dump() if execution.outcome else None,
        }
        for hook in list(self.on_event):
            try:
                result = hook(execution, payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 - delivery hooks must never break runs
                log.exception("delivery hook raised", extra={"execution_id": execution.id})


def _now():
    from datetime import datetime

    return datetime.now(UTC)


def dep_status(execution: Execution, task_id: str) -> str:
    for t in execution.tasks:
        if t.id == task_id:
            return t.status
    return "missing"
