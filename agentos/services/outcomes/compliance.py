"""Compliance outcome module — 'Generate and file my Q3 SOC-2 evidence report.'

Gathers control evidence from connected systems (mock fixtures by
default), assembles a structured SOC-2-style evidence report, validates
completeness against a control checklist, and delivers it to the
compliance officer (artifact + optional Slack notification).
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from agentos.models import Capability, Execution, Goal, Outcome, Task
from agentos.services.integrations.slack import SlackClient
from agentos.services.outcomes.base import OutcomeModule


class EvidenceItem(BaseModel):
    control: str
    description: str
    evidence: str
    source: str
    status: str = "pass"


class ComplianceReport(BaseModel):
    framework: str = "SOC-2 Type II"
    period: str
    controls: list[EvidenceItem] = Field(default_factory=list)
    owner: str = "compliance-officer"
    filed: bool = False
    artifact: str | None = None


REQUIRED_CONTROLS = ["access-review", "change-management", "incident-response", "encryption-at-rest", "vendor-risk"]


class ComplianceModule(OutcomeModule):
    name: ClassVar[str] = "compliance"
    description: ClassVar[str] = (
        "Gather control evidence, generate a SOC-2 evidence report for a "
        "reporting period, self-check completeness, and file it with the "
        "compliance officer."
    )

    def __init__(self, slack: SlackClient | None = None) -> None:
        self.slack = slack or SlackClient()
        self._reports: dict[str, ComplianceReport] = {}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def accept(self, goal: Goal) -> bool:
        if goal.goal_type == "compliance":
            return True
        text = goal.description.lower()
        return any(kw in text for kw in ("soc-2", "soc2", "compliance report", "evidence", "audit"))

    async def plan(self, goal: Goal) -> list[Task]:
        gather = Task(
            description="Collect control evidence from all connected systems",
            action="compliance.gather_evidence",
            params=goal.params or {"period": _infer_period(goal)},
            risk_level="low",
            goal_id=goal.id,
        )
        assemble = Task(
            description="Assemble the evidence report and check completeness",
            action="compliance.assemble_report",
            risk_level="low",
            depends_on=[gather.id],
            goal_id=goal.id,
        )
        file_task = Task(
            description="File the report with the compliance officer and notify",
            action="compliance.file_report",
            risk_level="high",  # external side effect → trips risky_only gates
            requires_approval=False,
            depends_on=[assemble.id],
            goal_id=goal.id,
        )
        return [gather, assemble, file_task]

    async def run_task(self, task: Task, execution: Execution, goal: Goal) -> dict[str, Any]:
        if task.action == "compliance.gather_evidence":
            period = task.params.get("period") or _infer_period(goal)
            report = ComplianceReport(period=period, controls=_fixture_evidence())
            self._reports[goal.id] = report
            return {"controls_collected": len(report.controls), "period": period}

        if task.action == "compliance.assemble_report":
            report = self._reports[goal.id]
            missing = [c for c in REQUIRED_CONTROLS if c not in {i.control for i in report.controls}]
            return {"missing_controls": missing, "complete": not missing}

        if task.action == "compliance.file_report":
            report = self._reports[goal.id]
            artifact = f"artifacts/{goal.slug}-{report.period.lower()}-soc2-evidence.json"
            from pathlib import Path

            path = Path(artifact)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report.model_dump_json(indent=2))
            report.artifact = artifact
            report.filed = True
            channel = "#compliance"
            await self.slack.post_message(
                channel,
                f"SOC-2 evidence report for {report.period} filed by agent (`{execution.id}`).",
            )
            return {"filed": True, "artifact": artifact, "delivered_to": report.owner}

        raise ValueError(f"compliance module cannot execute action {task.action!r}")

    async def execute(self, execution: Execution) -> Outcome:
        results = [t.result for t in execution.tasks if t.status == "succeeded" and isinstance(t.result, dict)]
        final = next((r for r in reversed(results) if r.get("filed")), {})
        report = self._reports.get(execution.goal_id)
        return Outcome(
            summary=(
                f"Generated and filed SOC-2 evidence report for {report.period if report else 'period'}; "
                f"{len(report.controls) if report else 0} controls evidenced."
            ),
            artifacts=[final["artifact"]] if final.get("artifact") else [],
            metrics={
                "controls": len(report.controls) if report else 0,
                "filed": bool(final.get("filed")),
                "framework": "SOC-2 Type II",
            },
        )

    async def validate(self, outcome: Outcome, execution: Execution) -> bool:
        report = self._reports.get(execution.goal_id)
        if report is None or not outcome.metrics.get("filed"):
            return False
        covered = {item.control for item in report.controls}
        return all(control in covered for control in REQUIRED_CONTROLS)

    # ── Discovery surface ────────────────────────────────────────────────────

    def tools(self) -> list[Capability]:
        return [
            Capability(
                name="compliance.file_soc2_evidence",
                kind="tool",
                description="Generate and file a SOC-2 evidence report for a reporting period.",
                input_schema={"type": "object", "properties": {"period": {"type": "string", "examples": ["Q3-2026"]}}},
                risk_level="high",
                module=self.name,
            )
        ]


def _infer_period(goal: Goal) -> str:
    text = goal.description.upper()
    for quarter in ("Q1", "Q2", "Q3", "Q4"):
        if quarter in text:
            year_match = [token for token in text.replace(",", " ").split() if token.isdigit() and len(token) == 4]
            return f"{quarter}-{year_match[0]}" if year_match else quarter
    return "CURRENT"


def _fixture_evidence() -> list[EvidenceItem]:
    return [
        EvidenceItem(
            control="access-review",
            description="Quarterly access review completed",
            evidence="access-review-q3.xlsx (142 accounts reviewed)",
            source="HRIS",
        ),
        EvidenceItem(
            control="change-management",
            description="All production changes carried approved PRs",
            evidence="github: 87/87 merged PRs with approvals",
            source="GitHub",
        ),
        EvidenceItem(
            control="incident-response",
            description="Incident runbook exercised in drill",
            evidence="drill-2026-07.pdf, MTTD 4m / MTTR 31m",
            source="PagerDuty",
        ),
        EvidenceItem(
            control="encryption-at-rest",
            description="All data stores enforce AES-256",
            evidence="cloud-config-scan.json: 0 violations",
            source="Cloud Scanner",
        ),
        EvidenceItem(
            control="vendor-risk",
            description="Critical vendors re-assessed",
            evidence="vendor-register.csv: 12/12 current",
            source="Procurement",
        ),
    ]
