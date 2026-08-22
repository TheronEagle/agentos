"""BaseAgent — the contract every AgentOS agent implements.

An agent is not a chat persona. It is an addressable worker with:
  • a discoverable identity and capabilities (via the registry),
  • an inbox for A2A messages,
  • one job: perform(goal|task) → structured result.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from agentos.core.registry import Registry
from agentos.models import Goal, Task


class AgentDescriptor(BaseModel):
    """Machine-readable identity, published to the registry at boot."""

    id: str = Field(default_factory=lambda: f"agent_{uuid.uuid4().hex[:8]}")
    kind: str = "worker"
    role: str
    capabilities: list[str] = Field(default_factory=list)


class A2AMessage(BaseModel):
    """Envelope for agent-to-agent communication."""

    message_id: str = Field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:10]}")
    sender: str
    recipient: str
    type: str  # request_task | task_result | capability_query | announce | escalate
    payload: dict[str, Any] = Field(default_factory=dict)
    in_reply_to: str | None = None


class BaseAgent(ABC):
    """Minimal lifecycle shared by all agents."""

    def __init__(self, role: str, registry: Registry) -> None:
        self.registry = registry
        self.descriptor = AgentDescriptor(role=role)
        self.inbox: list[A2AMessage] = []

    @property
    def id(self) -> str:
        return self.descriptor.id

    def capabilities(self) -> list[dict[str, Any]]:
        return [c.model_dump() for c in self.registry.capabilities()]

    def receive(self, message: A2AMessage) -> None:
        """Deliver a message to this agent's inbox (pull-based A2A)."""
        self.inbox.append(message)

    @abstractmethod
    async def perform(self, subject: Goal | Task) -> dict[str, Any]:
        """Do the work. Return a structured result; never raise for business failure."""
