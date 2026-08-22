"""Planner + registry tests: routing, decomposition, capability discovery."""

from __future__ import annotations

import pytest

from agentos.core.planner import default_generic_plan
from agentos.models import Capability, Goal
from tests.conftest import support_goal


def test_support_module_accepts_ticket_goals(platform) -> None:
    matches = platform.registry.find_modules_for(support_goal())
    assert [m.name for m in matches] == ["support"]


def test_compliance_module_accepts_soc2_goal(platform) -> None:
    goal = Goal(description="Generate and file Q3 SOC-2 evidence report")
    matches = platform.registry.find_modules_for(goal)
    assert matches[0].name == "compliance"


def test_codebase_module_accepts_dependency_goal(platform) -> None:
    goal = Goal(description="Update all outdated dependencies and run full test suite")
    matches = platform.registry.find_modules_for(goal)
    assert matches[0].name == "codebase"


def test_goal_type_hint_routes_directly(platform) -> None:
    goal = Goal(description="Do the thing", type="support")
    module = platform.registry.module("support")
    assert module is not None and module.accept(goal)


@pytest.mark.asyncio
async def test_planner_produces_typed_tasks_with_dependencies(platform) -> None:
    tasks, owner = await platform.planner.plan(support_goal())
    assert owner == "support"
    assert all(isinstance(t.id, str) for t in tasks)
    assert tasks[1].depends_on == [tasks[0].id]


@pytest.mark.asyncio
async def test_planner_llm_path_parses_json(platform) -> None:
    from agentos.utils.llm_client import NoneClient

    platform.planner.llm = NoneClient()
    goal = Goal(description="Something no module claims: water the plants")  # unroutable by modules
    tasks = await platform.planner._llm_plan(goal)
    assert tasks is not None and len(tasks) == 2
    assert all(t.goal_id == goal.id for t in tasks)


def test_registry_capability_discovery(platform) -> None:
    caps = platform.registry.capabilities()
    names = [c.name for c in caps]
    assert "support" in names and "compliance" in names and "codebase" in names
    assert "support.resolve_tier1" in names

    tools = platform.registry.capabilities(kind="outcome_module")
    assert all(c.kind == "outcome_module" for c in tools)

    # Every capability carries a JSON Schema contract.
    for cap in caps:
        assert isinstance(cap.input_schema, dict) and cap.input_schema.get("type") == "object"


def test_registry_rejects_duplicate_module(platform) -> None:
    from agentos.services.outcomes.support import SupportModule

    with pytest.raises(ValueError):
        platform.registry.register_module(SupportModule())


def test_generic_fallback_plan_shape() -> None:
    goal = Goal(description="Whatever")
    tasks = default_generic_plan(goal)
    assert len(tasks) == 2 and all(t.goal_id == goal.id for t in tasks)


def test_capability_model_schema_contract() -> None:
    cap = Capability(
        name="x.y",
        kind="tool",
        description="d",
        input_schema={"type": "object", "properties": {}},
    )
    assert cap.risk_level == "low"
