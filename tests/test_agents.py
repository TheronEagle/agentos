"""A2A + worker/orchestrator tests: agents coordinating without humans."""

from __future__ import annotations

import pytest

from agentos.agents.a2a import A2ABus
from agentos.agents.worker import Worker
from agentos.core.registry import Registry
from agentos.models import Goal, Task


@pytest.mark.asyncio
async def test_worker_executes_registered_handler() -> None:
    registry = Registry()

    async def add(a: int, b: int) -> int:
        return a + b

    registry.register_tool("math.add", "Add numbers", {"type": "object"}, add)
    worker = Worker(role="calculator", registry=registry)
    task = Task(description="add two numbers", action="math.add", params={"a": 2, "b": 3})

    result = await worker.perform(task)
    assert result["status"] == "succeeded"
    assert result["result"] == 5
    assert task.status == "succeeded"


@pytest.mark.asyncio
async def test_worker_structured_failure_on_unknown_action() -> None:
    worker = Worker(role="lonely", registry=Registry())
    task = Task(description="do magic", action="magic.teleport")
    result = await worker.perform(task)
    assert result["status"] == "failed"
    assert "no handler registered" in result["error"]
    assert task.status == "failed"


@pytest.mark.asyncio
async def test_capability_query_over_bus() -> None:
    registry = Registry()
    registry.register_tool("x.y", "demo tool", {"type": "object"}, lambda: None)
    requester = Worker(role="requester", registry=registry)
    responder = Worker(role="responder", registry=registry)

    bus = A2ABus()
    bus.register(requester)
    bus.register(responder)

    response = await bus.request(requester.id, responder.id, "what can you do?")
    assert response is not None
    assert any(c["name"] == "x.y" for c in response["capabilities"])
    # Inbox drained after query.
    assert requester.inbox == [] or all(m.type != "capability_query" for m in requester.inbox)


@pytest.mark.asyncio
async def test_broadcast_reaches_all_but_sender() -> None:
    bus = A2ABus()
    workers = [Worker(role=f"w{i}", registry=Registry()) for i in range(3)]
    for w in workers:
        bus.register(w)

    count = await bus.broadcast(workers[0].id, "announce", {"hello": True})
    assert count == 2
    assert workers[0].inbox == []
    assert all(len(w.inbox) == 1 for w in workers[1:])


@pytest.mark.asyncio
async def test_orchestrator_fans_out_sub_goals(platform) -> None:
    goal = Goal(
        description="Run the weekly ops sweep",
        params={
            "sub_goals": [
                "> resolve all unresolved tier-1 tickets from last 24h",
                "> generate and file Q3 SOC-2 evidence report",
            ]
        },
    )
    execution = await platform.orchestrator.run(goal)

    assert execution.module == "orchestrator"
    assert execution.outcome is not None
    metrics = execution.outcome.metrics
    assert metrics["sub_goals_total"] == 2
    assert metrics["sub_goals_completed"] == 2
    assert all(execution_id.startswith("exec_") for execution_id in metrics["sub_execution_ids"])

    # Child executions actually completed too.
    for child_id in metrics["sub_execution_ids"]:
        child = await platform.store.get_execution(child_id)
        assert child is not None and child.status == "completed"
