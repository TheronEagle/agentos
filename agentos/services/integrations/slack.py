"""Slack integration.

Mock-backed by default; set AGENTOS_SLACK_BOT_TOKEN (+ optional base URL)
to post to a real workspace.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel


class SlackMessage(BaseModel):
    channel: str
    text: str
    ts: str


class SlackClient:
    """Posting notifications and outcome summaries."""

    def __init__(self, bot_token: str | None = None, base_url: str | None = None) -> None:
        self.bot_token = bot_token
        self.base_url = (base_url or "https://slack.com/api").rstrip("/")
        self._sent: list[SlackMessage] = []

    @property
    def live(self) -> bool:
        return self.bot_token is not None

    async def post_message(self, channel: str, text: str) -> SlackMessage:
        if not self.live:
            message = SlackMessage(channel=channel, text=text, ts=f"{len(self._sent) + 1}.000000")
            self._sent.append(message)
            return message
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}/chat.postMessage",
                headers={"Authorization": f"Bearer {self.bot_token}"},
                json={"channel": channel, "text": text},
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(f"Slack API error: {payload}")
            return SlackMessage(channel=channel, text=text, ts=payload["ts"])

    async def deliver_outcome(self, channel: str, outcome_summary: str, execution_id: str) -> SlackMessage:
        text = f":white_check_mark: *Outcome delivered* (`{execution_id}`)\n{outcome_summary}"
        return await self.post_message(channel, text)
