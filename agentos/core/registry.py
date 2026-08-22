"""Registry — the capability discovery layer.

Agents discover what they can do by querying the registry (exposed at
GET /capabilities), never by reading documentation. Every tool, outcome
module, and integration registers a typed, JSON-Schema'd Capability here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agentos.models import Capability
from agentos.utils.logging_utils import get_logger

if TYPE_CHECKING:
    from agentos.services.outcomes.base import OutcomeModule

log = get_logger(__name__)


class Registry:
    """In-process capability registry. One per AgentOS instance."""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}
        self._tool_handlers: dict[str, Callable[..., Any]] = {}
        self._outcome_modules: dict[str, OutcomeModule] = {}

    # ── Outcome modules ──────────────────────────────────────────────────────

    def register_module(self, module: OutcomeModule) -> None:
        """Register an outcome module plus a Capability for it and each tool it exposes."""
        if module.name in self._outcome_modules:
            raise ValueError(f"Outcome module {module.name!r} already registered")
        self._outcome_modules[module.name] = module

        self._capabilities[module.name] = Capability(
            name=module.name,
            kind="outcome_module",
            description=module.description,
            input_schema=module.goal_schema(),
            output_schema=module.outcome_schema(),
            risk_level="medium",
            tags=["outcome"],
        )
        for tool in module.tools():
            self.register_capability(tool)
        log.info("registered outcome module", extra={"module": module.name})

    def module(self, name: str) -> OutcomeModule | None:
        return self._outcome_modules.get(name)

    def modules(self) -> list[OutcomeModule]:
        return list(self._outcome_modules.values())

    def find_modules_for(self, goal: Any) -> list[OutcomeModule]:
        """All modules whose accept() returns True, in registration order."""
        return [m for m in self._outcome_modules.values() if m.accept(goal)]

    # ── Tools / capabilities ─────────────────────────────────────────────────

    def register_capability(self, capability: Capability, handler: Callable[..., Any] | None = None) -> None:
        if capability.name in self._capabilities:
            raise ValueError(f"Capability {capability.name!r} already registered")
        self._capabilities[capability.name] = capability
        if handler is not None:
            self._tool_handlers[capability.name] = handler

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[..., Any],
        *,
        kind: str = "tool",
        risk_level: str = "low",
        module: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.register_capability(
            Capability(
                name=name,
                kind=kind,  # type: ignore[arg-type]
                description=description,
                input_schema=input_schema,
                risk_level=risk_level,  # type: ignore[arg-type]
                module=module,
                tags=tags or [],
            ),
            handler=handler,
        )

    def handler(self, name: str) -> Callable[..., Any] | None:
        return self._tool_handlers.get(name)

    def capability(self, name: str) -> Capability | None:
        return self._capabilities.get(name)

    def capabilities(self, kind: str | None = None, tag: str | None = None) -> list[Capability]:
        caps = list(self._capabilities.values())
        if kind is not None:
            caps = [c for c in caps if c.kind == kind]
        if tag is not None:
            caps = [c for c in caps if tag in c.tags]
        return caps
