"""Worker — executes individual tasks.

Workers claim tasks from a plan and execute them by resolving the task's
`action` key against the capability registry. If the action maps to a
registered handler, that handler runs; otherwise the worker reports a
structured failure. Workers never guess and never ask humans questions.
"""

from __future__ import annotations

from typing import Any

from agentos.agents.base import BaseAgent
from agentos.core.registry import Registry
from agentos.models import Goal, Task
from agentos.utils.logging_utils import get_logger

log = get_logger(__name__)


class Worker(BaseAgent):
    """A task-execution agent bound to one capability domain."""

    def __init__(self, role: str, registry: Registry, domain: str | None = None) -> None:
        super().__init__(role=role, registry=registry)
        self.descriptor.kind = "worker"
        self.domain = domain
        self._results: dict[str, dict[str, Any]] = {}

    async def perform(self, subject: Goal | Task) -> dict[str, Any]:
        if isinstance(subject, Task):
            return await self.execute_task(subject)
        # Given a bare goal, a worker executes its generic steps via the registry.
        return {"status": "rejected", "reason": "workers execute Tasks; submit Goals to the engine"}

    async def execute_task(self, task: Task) -> dict[str, Any]:
        task.assigned_agent = self.id
        task.mark_running()
        handler = self.registry.handler(task.action)
        if handler is None:
            error = f"no handler registered for action {task.action!r}"
            task.mark_failed(error)
            return {"status": "failed", "task_id": task.id, "error": error}
        try:
            result = handler(**task.params)
            if hasattr(result, "__await__"):
                result = await result
            task.mark_succeeded(result)
            self._results[task.id] = {"status": "succeeded", "result": result}
            return {"status": "succeeded", "task_id": task.id, "result": result}
        except Exception as exc:  # noqa: BLE001 - structured failure, never a crash
            error = f"{type(exc).__name__}: {exc}"
            task.mark_failed(error)
            self._results[task.id] = {"status": "failed", "error": error}
            return {"status": "failed", "task_id": task.id, "error": error}

    async def drain_inbox(self) -> list[dict[str, Any]]:
        """Process A2A messages: capability queries answered from the registry."""
        responses: list[dict[str, Any]] = []
        while self.inbox:
            message = self.inbox.pop(0)
            if message.type == "capability_query":
                responses.append(
                    {
                        "to": message.sender,
                        "in_reply_to": message.message_id,
                        "capabilities": self.capabilities(),
                    }
                )
            else:
                responses.append({"to": message.sender, "in_reply_to": message.message_id, "ack": True})
        return responses


def make_worker(role: str, registry: Registry, domain: str | None = None) -> Worker:
    """Convenience factory."""
    return Worker(role=role, registry=registry, domain=domain)
