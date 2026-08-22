"""Codebase outcome module — 'Update all outdated dependencies and run tests.'

Reads the repo's dependency manifest, bumps outdated pins, runs the test
suite, and opens a PR when green (never merges without explicit
instruction — the PR is the review surface for humans who want one).
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from agentos.models import Capability, Execution, Goal, Outcome, Task
from agentos.services.integrations.github import GitHubClient, PullRequest
from agentos.services.outcomes.base import OutcomeModule


class CodebaseParams(BaseModel):
    repo: str = "acme/agentos-demo"
    branch: str = "agent/dependency-bump"
    merge_when_green: bool = Field(
        default=False,
        description="Only if the delegating party explicitly asked for auto-merge.",
    )


class DependencyUpdateResult(BaseModel):
    updated: list[str] = Field(default_factory=list)
    tests_passed: bool = False
    pr: PullRequest | None = None


class CodebaseModule(OutcomeModule):
    name: ClassVar[str] = "codebase"
    description: ClassVar[str] = (
        "Update outdated dependencies in a repository, run the full test "
        "suite, and open a pull request with the result."
    )

    def __init__(self, github: GitHubClient | None = None) -> None:
        self.github = github or GitHubClient()
        self._results: dict[str, DependencyUpdateResult] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def accept(self, goal: Goal) -> bool:
        if goal.goal_type == "codebase":
            return True
        text = goal.description.lower()
        return any(
            kw in text
            for kw in ("dependenc", "requirements.txt", "pyproject", "upgrade package", "bump ")
        )

    async def plan(self, goal: Goal) -> list[Task]:
        scan = Task(
            description="Scan dependency manifest for outdated packages",
            action="github.list_outdated",
            params={"repo": CodebaseParams(**(goal.params or {})).repo},
            risk_level="low",
            goal_id=goal.id,
        )
        bump = Task(
            description="Update outdated dependencies on a work branch",
            action="github.update_dependencies",
            risk_level="medium",
            depends_on=[scan.id],
            goal_id=goal.id,
        )
        test = Task(
            description="Run the full test suite against the updated tree",
            action="github.run_tests",
            risk_level="low",
            depends_on=[bump.id],
            goal_id=goal.id,
        )
        pr = Task(
            description="Open a pull request with the bump for review",
            action="github.open_pr",
            risk_level="low",
            depends_on=[test.id],
            goal_id=goal.id,
        )
        return [scan, bump, test, pr]

    async def run_task(self, task: Task, execution: Execution, goal: Goal) -> dict[str, Any]:
        params = CodebaseParams(**(goal.params or {}))
        result = self._results.setdefault(goal.id, DependencyUpdateResult())

        if task.action == "github.list_outdated":
            outdated = await self.github.list_outdated_dependencies(params.repo)
            return {"outdated": outdated}

        if task.action == "github.update_dependencies":
            updated = await self.github.update_dependencies(params.repo)
            result.updated = updated
            return {"updated": updated}

        if task.action == "github.run_tests":
            passed = await self.github.run_tests(params.repo, ref=params.branch)
            result.tests_passed = passed
            return {"tests_passed": passed}

        if task.action == "github.open_pr":
            if not result.tests_passed:
                raise RuntimeError("refusing to open PR: test suite did not pass")
            pr = await self.github.open_pull_request(
                params.repo,
                title=f"chore: update {len(result.updated)} outdated dependencies",
                body=(
                    "Automated dependency bump by AgentOS.\n\n"
                    "Updated:\n" + "\n".join(f"- {line}" for line in result.updated) +
                    f"\n\nFull test suite: {'PASSING' if result.tests_passed else 'FAILING'}"
                ),
                head=params.branch,
            )
            result.pr = pr
            return {"pr": pr.model_dump()}

        raise ValueError(f"codebase module cannot execute action {task.action!r}")

    async def execute(self, execution: Execution) -> Outcome:
        result = self._results.get(execution.goal_id) or DependencyUpdateResult()
        pr_part = (
            f"; PR #{result.pr.number} opened ({result.pr.url})"
            if result.pr
            else "; no PR opened"
        )
        return Outcome(
            summary=(
                f"Updated {len(result.updated)} dependencies, tests "
                f"{'passed' if result.tests_passed else 'failed'}{pr_part}."
            ),
            artifacts=[result.pr.url] if result.pr else [],
            metrics={
                "dependencies_updated": len(result.updated),
                "tests_passed": result.tests_passed,
                "pr_number": result.pr.number if result.pr else None,
            },
        )

    async def validate(self, outcome: Outcome, execution: Execution) -> bool:
        # Self-check: we never deliver a dependency outcome with failing tests.
        return bool(outcome.metrics.get("tests_passed")) is True

    # ── Discovery surface ────────────────────────────────────────────────────

    def tools(self) -> list[Capability]:
        return [
            Capability(
                name="codebase.update_dependencies_and_test",
                kind="tool",
                description="Bump outdated deps, run tests, open PR if green.",
                input_schema=CodebaseParams.model_json_schema(),
                output_schema=DependencyUpdateResult.model_json_schema(),
                risk_level="medium",
                module=self.name,
            )
        ]
