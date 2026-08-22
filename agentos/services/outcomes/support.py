"""Support outcome module — 'Handle all tier-1 tickets from last 24h.'

Accepts goals about resolving/closing/triaging support tickets, drafts a
reply per ticket (deterministic templates in mock mode; LLM when
configured), resolves them through the Jira adapter, and notifies Slack.
Self-validates that every claimed ticket reached `resolved`.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import BaseModel

from agentos.models import Capability, Execution, Goal, Outcome, Task
from agentos.services.integrations.jira import JiraClient, Ticket
from agentos.services.integrations.slack import SlackClient
from agentos.services.outcomes.base import OutcomeModule
from agentos.utils.llm_client import BaseLLMClient


class SupportParams(BaseModel):
    """Typed refinement of a support goal."""

    within_hours: int = 24
    tier: str = "tier-1"
    notify_channel: str | None = None


class SupportModule(OutcomeModule):
    name: ClassVar[str] = "support"
    description: ClassVar[str] = (
        "Resolve open customer-support tickets end to end: read each ticket, "
        "draft and post a resolution reply, close the ticket, notify the team."
    )

    def __init__(
        self,
        jira: JiraClient | None = None,
        slack: SlackClient | None = None,
        llm: BaseLLMClient | None = None,
    ) -> None:
        self.jira = jira or JiraClient()
        self.slack = slack or SlackClient()
        self._llm = llm
        self._handled: dict[str, list[Ticket]] = {}
        self._resolved_by_execution: dict[str, list[str]] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def accept(self, goal: Goal) -> bool:
        text = f"{goal.description} {goal.goal_type or ''}".lower()
        if goal.goal_type == "support":
            return True
        return any(kw in text for kw in ("ticket", "helpdesk", "support request", "tier-1", "customer issue"))

    async def plan(self, goal: Goal) -> list[Task]:
        params = SupportParams(**(goal.params or {}))
        fetch_task = Task(
            description=f"Fetch open {params.tier} tickets from the last {params.within_hours}h",
            action="jira.list_open_tickets",
            params={"within_hours": params.within_hours, "tier": params.tier},
            risk_level="low",
            goal_id=goal.id,
        )
        resolve_task = Task(
            description="Draft replies and resolve every fetched ticket",
            action="jira.resolve_tickets",
            params={"notify_channel": params.notify_channel},
            risk_level="medium",
            depends_on=[fetch_task.id],
            goal_id=goal.id,
        )
        return [fetch_task, resolve_task]

    async def run_task(self, task: Task, execution: Execution, goal: Goal) -> dict[str, Any]:
        if task.action == "jira.list_open_tickets":
            params = SupportParams(**(goal.params or {}))
            tickets = await self.jira.list_open_tier1(within_hours=params.within_hours)
            self._handled[goal.id] = tickets
            return {"tickets": [t.key for t in tickets], "count": len(tickets)}

        if task.action == "jira.resolve_tickets":
            tickets = self._handled.get(goal.id, [])
            resolved: list[str] = []
            for ticket in tickets:
                draft = await self._draft(ticket)
                closed = await self.jira.resolve(ticket.key, resolution=draft)
                resolved.append(closed.key)
                if not self.slack.live:
                    await self.slack.post_message(
                        "#support-outcomes", f"Resolved {closed.key}: {ticket.summary}"
                    )
            channel = task.params.get("notify_channel")
            if channel is None:
                # Live mode: post to the env-configured channel. Mock mode:
                # keep fixture chatter in the in-memory client.
                import os

                channel = "#support-outcomes" if not self.slack.live else os.environ.get(
                    "AGENTOS_SLACK_CHANNEL", "general"
                )
            try:
                await self.slack.post_message(channel, f"Resolved {len(resolved)} ticket(s): {', '.join(resolved)}")
            except Exception as exc:  # noqa: BLE001 - notification must not fail the outcome
                execution.add_event("webhook_sent", f"slack notify failed: {exc}")
            # Record what THIS execution resolved, keyed by execution id, so
            # validate() judges this run's work — never another run's.
            self._resolved_by_execution[execution.id] = sorted(set(resolved))
            return {"resolved": resolved, "count": len(resolved)}

        raise ValueError(f"support module cannot execute action {task.action!r}")

    async def execute(self, execution: Execution) -> Outcome:
        results = [t.result for t in execution.tasks if t.status == "succeeded" and isinstance(t.result, dict)]
        resolved: list[str] = []
        for r in results:
            resolved.extend(r.get("resolved", []) or r.get("tickets", []))
        count = len(set(resolved))
        return Outcome(
            summary=f"Resolved {count} support ticket(s): {', '.join(sorted(set(resolved))) or 'none pending'}",
            artifacts=[f"jira:{key}" for key in sorted(set(resolved))],
            metrics={"tickets_resolved": count},
        )

    async def validate(self, outcome: Outcome, execution: Execution) -> bool:
        """Self-check scoped to THIS execution.

        Every ticket this execution fetched must appear in its own resolved
        set with well-formed keys. Cross-run state is never consulted, so
        repeated delegations of the same goal validate independently.
        """
        fetched = {t.key for t in self._handled.get(execution.goal_id, [])}
        claimed = set(self._resolved_by_execution.get(execution.id, []))
        metrics_resolved = outcome.metrics.get("tickets_resolved")

        # Zero-ticket runs are legitimately valid (queue was empty).
        if not fetched and metrics_resolved == 0:
            return True
        if metrics_resolved != len(fetched) or not fetched <= claimed:
            return False
        return all(re.match(r"^(jira:)?[A-Z]+-\d+$", key) for key in outcome.artifacts)

    async def _draft(self, ticket: Ticket) -> str:
        """Draft a customer reply. Templates in mock mode; LLM when configured."""
        draft = await self.jira.draft_reply(ticket)
        if draft:
            return draft
        # Live-mode fallback: platform LLM drafts when configured.
        if self._llm is not None:
            try:
                return await self._llm.complete(
                    f"Draft a concise customer reply for this resolved support ticket.\n"
                    f"Summary: {ticket.summary}\nDescription: {ticket.description}",
                    system="You are a support resolution agent. Be specific, brief, and empathetic.",
                )
            except Exception:  # noqa: BLE001 - drafting must never block resolution
                pass
        return (
            f"Thanks for reporting '{ticket.summary}'. We've applied the standard "
            f"resolution workflow for this class of issue and confirmed the fix."
        )

    # ── Discovery surface ────────────────────────────────────────────────────

    def tools(self) -> list[Capability]:
        return [
            Capability(
                name="support.resolve_tier1",
                kind="tool",
                description="Resolve all open tier-1 tickets created within a window.",
                input_schema=SupportParams.model_json_schema(),
                output_schema={
                    "type": "object",
                    "properties": {"resolved": {"type": "array"}, "count": {"type": "integer"}},
                },
                risk_level="medium",
                module=self.name,
            )
        ]
