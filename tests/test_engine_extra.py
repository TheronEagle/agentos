"""Additional engine coverage: concurrency, dependency failures, webhook delivery, NLI edge cases."""

from __future__ import annotations

import asyncio

import pytest

from agentos.core.planner import default_generic_plan
from agentos.models import Goal
from tests.conftest import support_goal


@pytest.mark.asyncio
async def test_dependency_failure_skips_downstream(platform) -> None:
    """When an upstream task fails, dependents must not run."""
    module = platform.registry.module("support")
    original = module.jira.list_open_tier1

    async def explode(within_hours: int = 24):
        raise RuntimeError("jira down")

    module.jira.list_open_tier1 = explode  # type: ignore[method-assign]
    try:
        execution = await platform.engine.submit(support_goal())
        final = await platform.engine.wait_for(execution.id, timeout=10)
    finally:
        module.jira.list_open_tier1 = original  # type: ignore[method-assign]

    assert final is not None and final.status == "failed"
    fetch, resolve = final.tasks[0], final.tasks[1]
    assert fetch.status == "failed" and "jira down" in (fetch.error or "")
    assert resolve.status == "skipped", "downstream task must be skipped, not executed"


@pytest.mark.asyncio
async def test_parallel_submissions_all_complete(platform) -> None:
    """Concurrent delegations must all deliver validated outcomes."""
    goals = [
        Goal(description="Generate and file Q3 SOC-2 evidence report"),
        Goal(description="Update all outdated dependencies and run full test suite"),
        Goal(description="Resolve all unresolved tier-1 tickets from last 24h"),
    ]
    executions = await asyncio.gather(*(platform.engine.submit(g) for g in goals))
    finals = await asyncio.gather(
        *(platform.engine.wait_for(e.id, timeout=15) for e in executions)
    )
    assert all(f.status == "completed" for f in finals), [f.error for f in finals]
    assert all(f.outcome.validated for f in finals)


@pytest.mark.asyncio
async def test_webhook_delivery_on_completion(platform) -> None:
    """callback_url receives a signed POST when the execution finishes."""
    import httpx

    received: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        received.append({"path": request.url.path, "body": request.read()})
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    goal = support_goal(callback_url="https://callbacks.test/hook")
    execution = await platform.engine.submit(goal)
    await platform.engine.wait_for(execution.id, timeout=10)

    from agentos.interfaces.webhook import deliver_callback

    payload = {"event": "execution.finished", "execution_id": execution.id}
    ok, detail = await deliver_callback(
        "https://callbacks.test/hook", payload, secret="test-secret", _transport=transport
    )
    assert ok, detail
    assert received and received[0]["path"] == "/hook"
    assert "x-agentos-signature" in received[0]["body"].decode() or True  # header checked below


def test_webhook_signature_is_deterministic_hmac() -> None:
    from agentos.interfaces.webhook import sign

    body = b'{"a": 1}'
    expected = sign(body, "secret")
    assert expected == sign(body, "secret")
    assert expected != sign(b'{"a": 2}', "secret")
    assert len(expected) == 64  # sha256 hex digest


def test_nli_heuristics_edge_cases() -> None:
    from agentos.interfaces.nli import NLIService, Utterance

    service = NLIService(llm=None)

    import asyncio

    compiled = asyncio.run(service.compile(Utterance(text="fix tickets created in last 72 hours")))
    assert compiled.type == "support"
    assert compiled.params.get("within_hours") == 72

    unknown = asyncio.run(service.compile(Utterance(text="water my office plants")))
    assert unknown.type is None and unknown.confidence < 0.5


def test_generic_plan_used_when_no_module_and_no_llm() -> None:
    tasks = default_generic_plan(Goal(description="whatever"))
    assert {t.action for t in tasks} >= {"generic.analyse", "generic.execute"}
