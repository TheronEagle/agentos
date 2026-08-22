"""Example: delegate "Generate and file Q3 SOC-2 evidence report".

Shows a higher-risk outcome (external filing) flowing through the same
three-step pattern — and what the audit trail looks like for a
multi-step autonomous run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agentos.interfaces.api import Platform
from agentos.models import Goal


async def main() -> None:
    platform = Platform()

    goal = Goal(
        description="Generate and file Q3 SOC-2 evidence report",
        requested_by="head-of-platform",
        params={"period": "Q3-2026"},
        constraints=["no customer data leaves the tenant"],
        success_criteria="All five required controls evidenced and filed.",
    )
    execution = await platform.engine.submit(goal)
    print(f"delegated → execution {execution.id} (module: {execution.module})")

    final = await platform.engine.wait_for(execution.id, timeout=30)
    assert final is not None
    print(f"status: {final.status}")
    print(f"outcome: {final.outcome.summary if final.outcome else final.error}")
    print(f"artifacts: {final.outcome.artifacts if final.outcome else []}")

    # The filed report is a real artifact on disk.
    for artifact in final.outcome.artifacts if final.outcome else []:
        path = Path(artifact)
        if path.exists():
            report = json.loads(path.read_text())
            print(f"\nfiled report → {path}")
            print(f"  framework: {report['framework']}")
            print(f"  controls : {', '.join(c['control'] for c in report['controls'])}")

    print("\nplan execution trace:")
    for event in final.trace:
        task_tag = f" [{event.task_id}]" if event.task_id else ""
        print(f"  [{event.kind}]{task_tag} {event.message}")


if __name__ == "__main__":
    asyncio.run(main())
