"""GitHub integration.

Mock-backed by default (deterministic fixtures); set AGENTOS_GITHUB_BASE_URL
+ AGENTOS_GITHUB_TOKEN to go live. Same typed surface either way — the mock
documents exactly what live mode does.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field


class PullRequest(BaseModel):
    number: int
    title: str
    branch: str
    url: str
    checks_passed: bool


class DependencyReport(BaseModel):
    outdated: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    tests_passed: bool = False
    pr: PullRequest | None = None


class GitHubClient:
    """Repo operations used by the codebase outcome module."""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        # Deterministic fixture state for mock mode.
        self._prs: dict[int, dict[str, Any]] = {}
        self._next_pr = 1

    @property
    def live(self) -> bool:
        return self.base_url is not None

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def list_outdated_dependencies(self, repo: str) -> list[str]:
        if not self.live:
            return ["requests 2.31.0 → 2.32.3", "pydantic 2.7.1 → 2.8.2", "httpx 0.27.0 → 0.27.2"]
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=30) as client:
            response = await client.get(f"/repos/{repo}/dependency-graph/compare")
            response.raise_for_status()
            return [f"{item['name']} {item['current_version']} → {item['latest_version']}" for item in response.json()]

    async def update_dependencies(self, repo: str, pins: list[str] | None = None) -> list[str]:
        """Bump dependencies (mock: returns the new pin set)."""
        if not self.live:
            return pins or [line.split(" ")[0] + "@latest" for line in await self.list_outdated_dependencies(repo)]
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=30) as client:
            response = await client.put(
                f"/repos/{repo}/dependency-graph/merge-updates",
                json={"updates": pins or []},
            )
            response.raise_for_status()
            return pins or []

    async def run_tests(self, repo: str, ref: str = "agent/dependency-bump") -> bool:
        if not self.live:
            return True  # fixture: CI green
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=120) as client:
            response = await client.post(f"/repos/{repo}/actions/runs", json={"ref": ref})
            response.raise_for_status()
            run_id = response.json().get("id")
            status_response = await client.get(f"/repos/{repo}/actions/runs/{run_id}")
            status_response.raise_for_status()
            conclusion = status_response.json().get("conclusion")
            return conclusion == "success"

    async def open_pull_request(self, repo: str, title: str, body: str, head: str, base: str = "main") -> PullRequest:
        if not self.live:
            number = self._next_pr
            self._next_pr += 1
            pr = {
                "number": number,
                "title": title,
                "branch": head,
                "url": f"https://github.com/mock/{repo}/pull/{number}",
                "checks_passed": True,
                "body": body,
                "base": base,
            }
            self._prs[number] = pr
            return PullRequest(**pr)
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=30) as client:
            response = await client.post(
                f"/repos/{repo}/pulls",
                json={"title": title, "body": body, "head": head, "base": base},
            )
            response.raise_for_status()
            data = response.json()
            return PullRequest(
                number=data["number"],
                title=data["title"],
                branch=head,
                url=data["html_url"],
                checks_passed=False,  # unknown until checks run
            )

    async def merge_pull_request(self, repo: str, number: int) -> bool:
        if not self.live:
            pr = self._prs.pop(number, None)
            return pr is not None
        async with httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=30) as client:
            response = await client.put(f"/repos/{repo}/pulls/{number}/merge")
            return response.status_code in (200, 405)  # 405 = already merged
