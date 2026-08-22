"""Shared fixtures: a fully-wired platform with built-in modules."""

from __future__ import annotations

import pytest

from agentos.core.state import InMemoryStore
from agentos.interfaces.api import Platform
from agentos.models import Goal


@pytest.fixture()
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture()
def platform(store: InMemoryStore) -> Platform:
    return Platform(store=store)


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def support_goal(**overrides) -> Goal:
    payload = {
        "description": "Resolve all unresolved tier-1 tickets from last 24h",
        "requested_by": "test-agent",
    }
    payload.update(overrides)
    return Goal(**payload)
