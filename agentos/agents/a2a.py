"""A2A bus — lightweight agent-to-agent communication.

Pull-based, in-process message bus. Agents publish results, request
tasks, and query each other's capabilities without knowing each other's
addresses — only IDs. The same envelope shape works over Redis pub/sub or
HTTP when agents are remote; the semantics do not change.
"""

from __future__ import annotations

import asyncio
from typing import Any

from agentos.agents.base import A2AMessage, BaseAgent
from agentos.utils.logging_utils import get_logger

log = get_logger(__name__)


class A2ABus:
    """Topic-less, recipient-addressed bus with wildcard broadcast."""

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._history: list[A2AMessage] = []
        self._lock = asyncio.Lock()

    def register(self, agent: BaseAgent) -> None:
        self._agents[agent.id] = agent

    def agent(self, agent_id: str) -> BaseAgent | None:
        return self._agents.get(agent_id)

    def agents(self) -> list[BaseAgent]:
        return list(self._agents.values())

    async def send(self, message: A2AMessage) -> None:
        async with self._lock:
            self._history.append(message)
        recipient = self._agents.get(message.recipient)
        if recipient is None:
            log.warning("a2a drop: unknown recipient", extra={"agent_id": message.recipient})
            return
        recipient.receive(message)

    async def broadcast(self, sender: str, message_type: str, payload: dict[str, Any]) -> int:
        """Send to every registered agent except the sender. Returns fan-out count."""
        count = 0
        for agent_id in self._agents:
            if agent_id == sender:
                continue
            await self.send(
                A2AMessage(sender=sender, recipient=agent_id, type=message_type, payload=payload)
            )
            count += 1
        return count

    async def request(self, sender: str, recipient_id: str, capability_query: str) -> dict[str, Any] | None:
        """Synchronous-style capability query: ask an agent what it can do."""
        recipient = self._agents.get(recipient_id)
        if recipient is None:
            return None
        await self.send(
            A2AMessage(
                sender=sender,
                recipient=recipient_id,
                type="capability_query",
                payload={"query": capability_query},
            )
        )
        responses = await recipient.drain_inbox()
        return responses[0] if responses else None

    def history(self, limit: int = 100) -> list[A2AMessage]:
        return self._history[-limit:]
