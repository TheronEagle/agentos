"""Outbound webhook delivery.

When a goal carries callback_url, AgentOS POSTs the finished Outcome with
an HMAC signature header so the receiving agent can verify provenance.
Delivery is fire-and-confirm: failures are logged into the trace, never
raised into the engine.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from agentos.core.engine import Engine
from agentos.utils.logging_utils import get_logger

log = get_logger(__name__)


def sign(payload: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


async def deliver_callback(
    callback_url: str,
    payload: dict[str, Any],
    secret: str | None = None,
    timeout: float = 15.0,
) -> tuple[bool, str]:
    body = httpx.Request("POST", callback_url, json=payload).read()
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-AgentOS-Signature"] = sign(body, secret)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(callback_url, content=body, headers=headers)
            return response.status_code < 400, f"status={response.status_code}"
    except Exception as exc:  # noqa: BLE001 - delivery is best-effort, traced always
        return False, f"{type(exc).__name__}: {exc}"


def install_delivery_hooks(engine: Engine, settings: Any) -> None:
    """Wire callback_url delivery onto terminal executions."""

    async def deliver(execution: Any, event: dict[str, Any]) -> None:
        goal = await engine.store.get_goal(execution.goal_id)
        url = goal.callback_url if goal else None
        if not url:
            return
        ok, detail = await deliver_callback(url, event, settings.webhook_secret)
        execution.add_event("webhook_sent", f"callback {url}: {'ok' if ok else detail}")
        await engine.store.save_execution(execution)
        log.info("webhook delivered", extra={"execution_id": execution.id})

    engine.on_delivery_hook(deliver)
