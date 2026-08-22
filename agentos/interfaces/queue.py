"""Celery queue — external worker dispatch (optional deployment mode).

By default the API process executes goals in-process with asyncio. When
AGENTOS_CELERY_BROKER_URL is set, `run_worker` starts a Celery app and
goals submitted over HTTP are handed to distributed workers instead —
same engine, same contracts, horizontal scale.

Run:  celery -A agentos.interfaces.queue:run_worker worker --loglevel=info
"""

from __future__ import annotations

import asyncio
from typing import Any

try:
    from celery import Celery

    _HAS_CELERY = True
except ImportError:  # pragma: no cover - only without the [queue] extra
    _HAS_CELERY = False

from agentos.utils.config import get_settings
from agentos.utils.logging_utils import get_logger

log = get_logger(__name__)


def _build_celery() -> Any:
    settings = get_settings()
    if not settings.celery_broker_url:
        raise RuntimeError(
            "AGENTOS_CELERY_BROKER_URL is not set; the built-in asyncio executor "
            "is active. Configure a broker to run Celery workers."
        )
    if not _HAS_CELERY:
        raise RuntimeError("pip install agentos[queue] to use Celery workers")

    celery = Celery("agentos", broker=settings.celery_broker_url, include=["agentos.interfaces.queue"])
    celery.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])

    @celery.task(name="agentos.execute_goal")
    def execute_goal(goal_payload: dict[str, Any]) -> dict[str, Any]:
        """Execute one goal inside a worker process."""
        from agentos.interfaces.api import Platform
        from agentos.models import Goal

        async def _run() -> dict[str, Any]:
            platform = Platform()
            await platform.startup()
            try:
                execution = await platform.engine.submit(Goal(**goal_payload))
                final = await platform.engine.wait_for(execution.id, timeout=600)
                return {
                    "execution_id": execution.id,
                    "status": final.status if final else "timeout",
                    "outcome": final.outcome.model_dump() if final and final.outcome else None,
                }
            finally:
                await platform.shutdown()

        return asyncio.run(_run())

    return celery


run_worker = None  # populated lazily so importing this module never requires Celery

if _HAS_CELERY:
    try:
        run_worker = _build_celery()
    except Exception as exc:  # noqa: BLE001 - misconfig should not break imports
        log.warning("celery app not built: %s", exc)


def enqueue_goal(goal_payload: dict[str, Any]) -> str | None:
    """Hand a Goal payload to the queue. Returns task id, or None if queue disabled."""
    if run_worker is None:
        return None
    result = run_worker.send_task("agentos.execute_goal", args=[goal_payload])
    return result.id
