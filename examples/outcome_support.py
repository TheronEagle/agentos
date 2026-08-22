"""Example: delegate "Resolve 47 open tickets" — full agent client loop.

Run:  python examples/outcome_support.py
Demonstrates the Service-as-Software interaction pattern:
  1. Discover capabilities (machines read schemas, not docs).
  2. Delegate an outcome by submitting a Goal.
  3. Collect the delivered Outcome (poll here; webhook in production).
"""

from __future__ import annotations

import asyncio

from agentos.interfaces.api import Platform
from agentos.models import Goal


async def main() -> None:
    platform = Platform()  # in-memory state; no services needed

    # 1. Discovery — what can this platform do for me?
    print("── capabilities ─────────────────────────────────────────")
    for cap in platform.registry.capabilities(kind="outcome_module"):
        print(f"  {cap.name}: {cap.description}")

    # 2. Delegation — hand over the outcome, not a workflow.
    goal = Goal(
        description="Resolve all unresolved tier-1 tickets from last 24h",
        requested_by="support-lead",
        success_criteria="Every fetched ticket ends resolved with a posted reply.",
    )
    execution = await platform.engine.submit(goal)
    print(f"\n── delegated ─────────────────────────────────────────────")
    print(f"  goal      = {goal.id}")
    print(f"  execution = {execution.id}  (module: {execution.module})")
    print(f"  plan      = {' → '.join(t.description for t in execution.tasks)}")

    # 3. Collection — the engine works; we simply await delivery.
    final = await platform.engine.wait_for(execution.id, timeout=30)
    assert final is not None, "execution did not finish in time"

    print("\n── outcome ───────────────────────────────────────────────")
    print(f"  status    = {final.status}")
    print(f"  summary   = {final.outcome.summary if final.outcome else 'n/a'}")
    print(f"  metrics   = {final.outcome.metrics if final.outcome else 'n/a'}")
    print(f"  validated = {final.outcome.validated if final.outcome else False}")

    print("\n── audit trail ───────────────────────────────────────────")
    for event in final.trace:
        print(f"  [{event.kind}] {event.message}")


if __name__ == "__main__":
    asyncio.run(main())
