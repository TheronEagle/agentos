"""Engine tests: goal → plan → autonomous execution → validated outcome."""

from __future__ import annotations

import pytest

from agentos.core.engine import RoutingError
from agentos.models import Goal
from tests.conftest import support_goal


@pytest.mark.asyncio
async def test_submit_returns_execution_immediately(platform) -> None:
    """Agents get an ExecutionID back at once — no synchronous waiting."""
    execution = await platform.engine.submit(support_goal())
    assert execution.id.startswith("exec_")
    assert execution.goal_id.startswith("goal_")
    assert execution.module == "support"
    assert len(execution.tasks) == 2


@pytest.mark.asyncio
async def test_full_autonomous_run_delivers_validated_outcome(platform) -> None:
    goal = support_goal()
    execution = await platform.engine.submit(goal)
    final = await platform.engine.wait_for(execution.id, timeout=10)

    assert final is not None
    assert final.status == "completed"
    assert final.outcome is not None
    assert final.outcome.validated is True
    assert final.outcome.metrics["tickets_resolved"] >= 1

    stored_goal = await platform.store.get_goal(goal.id)
    assert stored_goal.status == "completed"

    # Audit trail exists and is ordered.
    kinds = [e.kind for e in final.trace]
    assert "plan_created" in kinds
    assert "outcome_delivered" in kinds


@pytest.mark.asyncio
async def test_unroutable_goal_raises_routing_error(platform) -> None:
    with pytest.raises(RoutingError):
        await platform.engine.submit(
            Goal(description="Water my office plants please")
        )


@pytest.mark.asyncio
async def test_approval_gate_required_policy_pauses_then_resumes(store) -> None:
    from agentos.interfaces.api import Platform

    gated_platform = Platform(store=store)
    gated_platform.engine.approval_policy = "required"

    goal = support_goal()
    execution = await gated_platform.engine.submit(goal)
    assert execution.status == "awaiting_approval"

    resumed = await gated_platform.engine.approve_and_resume(execution.id, "grant", "human")
    assert resumed is not None and resumed.status == "running"

    final = await gated_platform.engine.wait_for(execution.id, timeout=10)
    assert final is not None
    assert final.status == "completed"
    assert final.approvals and set(final.approvals.values()) == {"granted"}


@pytest.mark.asyncio
async def test_approval_deny_cancels_run(store) -> None:
    from agentos.interfaces.api import Platform

    gated_platform = Platform(store=store)
    gated_platform.engine.approval_policy = "required"

    execution = await gated_platform.engine.submit(support_goal())
    resumed = await gated_platform.engine.approve_and_resume(execution.id, "deny", "human")
    assert resumed is not None
    assert resumed.status == "cancelled"


@pytest.mark.asyncio
async def test_same_goal_twice_both_validate(platform) -> None:
    """Regression: second delegation of an identical goal must not inherit
    the first run's state. Caught by a from-scratch clone-and-run test."""
    for i in (1, 2):
        execution = await platform.engine.submit(support_goal())
        final = await platform.engine.wait_for(execution.id, timeout=10)
        assert final is not None and final.status == "completed", (
            f"run {i} failed: {final.error if final else 'timeout'}"
        )
        assert final.outcome is not None and final.outcome.validated is True


@pytest.mark.asyncio
async def test_delivery_hook_fires_on_completion(platform) -> None:
    seen: list[dict] = []

    def hook(execution, payload):
        seen.append(payload)

    platform.engine.on_delivery_hook(hook)
    execution = await platform.engine.submit(support_goal())
    await platform.engine.wait_for(execution.id, timeout=10)
    # Hook runs in the background task; small grace poll.
    for _ in range(50):
        if seen:
            break
        await __import__("asyncio").sleep(0.05)
    assert seen and seen[0]["event"] == "execution.finished"
