"""Jira integration.

Mock-backed by default (in-memory ticket store seeded with realistic
tier-1 tickets); configure base_url + token for a real instance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import BaseModel


class Ticket(BaseModel):
    key: str
    summary: str
    priority: str  # tier-1 … tier-4 (or P0…P3)
    status: str  # open | resolved | closed
    created_at: datetime
    description: str = ""
    resolution: str | None = None
    resolved_by: str | None = None


class JiraClient:
    """Ticket triage surface used by the support outcome module."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        seed_tickets: list[Ticket] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        now = datetime.now(UTC)
        self._tickets: dict[str, Ticket] = {}
        if seed_tickets:
            for t in seed_tickets:
                self._tickets[t.key] = t
        else:
            fixtures = [
                ("SUP-101", "Password reset loop on mobile", "tier-1"),
                ("SUP-102", "Cannot export invoice PDF", "tier-1"),
                ("SUP-103", "Dashboard chart shows yesterday's data", "tier-1"),
                ("SUP-104", "Email digest sent twice", "tier-1"),
                ("SUP-105", "SSO redirect fails behind corporate proxy", "tier-2"),
                ("SUP-106", "API rate limit errors during bulk upload", "tier-2"),
            ]
            for i, (key, summary, tier) in enumerate(fixtures):
                self._tickets[key] = Ticket(
                    key=key,
                    summary=summary,
                    priority=tier,
                    status="open",
                    created_at=now - timedelta(hours=i + 2),
                    description=f"Customer reports: {summary.lower()}.",
                )

        # Demo realism: the fixture queue replenishes. A real helpdesk keeps
        # receiving tickets, so a resolved mock ticket re-opens after a short
        # window instead of leaving the queue permanently empty.
        self._reopen_after_seconds = 30
        self._resolved_at: dict[str, datetime] = {}

    def reset(self) -> None:
        """Restore the original fixture queue (demo/testing helper)."""
        self._tickets.clear()
        self._resolved_at.clear()
        fresh = JiraClient(base_url=self.base_url, token=self.token)
        self._tickets.update(fresh._tickets)

    def _replenish(self) -> None:
        """Re-open resolved fixture tickets older than the reopen window."""
        if self.live:
            return
        now = datetime.now(UTC)
        for key, ticket in list(self._tickets.items()):
            if ticket.status == "resolved" and key in self._resolved_at:
                age = (now - self._resolved_at[key]).total_seconds()
                if age > self._reopen_after_seconds:
                    ticket.status = "open"
                    ticket.resolution = None
                    ticket.resolved_by = None
                    del self._resolved_at[key]

    @property
    def live(self) -> bool:
        return self.base_url is not None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def list_open_tier1(self, within_hours: int = 24) -> list[Ticket]:
        self._replenish()
        cutoff = datetime.now(UTC) - timedelta(hours=within_hours)
        if not self.live:
            return [
                t.model_copy(deep=True)
                for t in self._tickets.values()
                if t.priority == "tier-1" and t.status == "open" and t.created_at >= cutoff
            ]
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=30) as client:
            response = await client.get(
                "/rest/api/3/search",
                params={
                    "jql": 'project = SUP AND priority = "tier-1" AND status = "open" AND created >= -24h',
                    "maxResults": 100,
                },
            )
            response.raise_for_status()
            return [Ticket(**_jira_to_ticket(item)) for item in response.json().get("issues", [])]

    async def draft_reply(self, ticket: Ticket) -> str:
        """Draft a customer reply. Uses deterministic templates in mock mode."""
        if not self.live:
            templates = {
                "password reset": "We've cleared the stuck reset state for your account — please try signing in again.",
                "export": (
                    "The PDF export service was restarted and your document is "
                    "available under Reports → Exports."
                ),
                "chart": "Your dashboard cache has been refreshed; current data will display within 5 minutes.",
                "digest": "A duplicate-subscription flag caused double digests; it has been removed.",
            }
            for needle, template in templates.items():
                if needle in ticket.summary.lower():
                    return template
            return (
                f"Thanks for reporting '{ticket.summary}'. We've applied the standard "
                f"resolution workflow for this class of issue and confirmed the fix."
            )
        # Live mode delegates drafting to the platform LLM at the module layer;
        # the integration only transports.
        return ""

    async def resolve(self, ticket_key: str, resolution: str, resolved_by: str = "agentos/support-agent") -> Ticket:
        if not self.live:
            ticket = self._tickets[ticket_key]
            ticket.status = "resolved"
            ticket.resolution = resolution
            ticket.resolved_by = resolved_by
            self._resolved_at[ticket_key] = datetime.now(UTC)
            return ticket.model_copy(deep=True)
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=30) as client:
            transitions = await client.get(f"/rest/api/3/issue/{ticket_key}/transitions")
            transitions.raise_for_status()
            resolve_id = next(
                t["id"] for t in transitions.json()["transitions"] if "resolve" in t["name"].lower()
            )
            payload: dict[str, Any] = {"transition": {"id": resolve_id}}
            if resolution:
                payload["fields"] = {"resolution": {"name": "Done"}}
            response = await client.post(f"/rest/api/3/issue/{ticket_key}/transitions", json=payload)
            response.raise_for_status()
            issue = (await client.get(f"/rest/api/3/issue/{ticket_key}")).json()
            return Ticket(**_jira_to_ticket(issue))


def _jira_to_ticket(issue: dict[str, Any]) -> dict[str, Any]:
    fields = issue.get("fields", {})
    return {
        "key": issue.get("key", ""),
        "summary": fields.get("summary", ""),
        "priority": str(fields.get("priority", {}).get("name", "tier-3")).lower(),
        "status": str(fields.get("status", {}).get("name", "open")).lower(),
        "created_at": fields.get("created") or datetime.now(UTC).isoformat(),
        "description": fields.get("description") or "",
    }
