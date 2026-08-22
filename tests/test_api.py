"""API tests: an agent client discovering, delegating, polling — no humans."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(platform) -> TestClient:
    from agentos.interfaces.api import create_app

    app = create_app(platform=platform)
    with TestClient(app) as http:
        yield http


def test_openapi_schema_carries_agent_instructions(client: TestClient) -> None:
    """The schema IS the docs. Agents read x-agent-instructions from it."""
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "AgentOS"
    instructions = {
        path: next(
            (op.get("x-agent-instructions") for op in methods.values() if op.get("x-agent-instructions")),
            None,
        )
        for path, methods in schema["paths"].items()
    }
    assert instructions["/goals"] and "202" in instructions["/goals"]
    assert instructions["/capabilities"]
    assert instructions["/executions/{execution_id}"]


def test_health_lists_registered_modules(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert {"support", "compliance", "codebase"} <= set(body["modules"])


def test_agent_discovers_capabilities_then_delegates(client: TestClient) -> None:
    """Full agent loop: discover → submit → poll → collect outcome."""
    caps = client.get("/capabilities").json()
    assert any(c["kind"] == "outcome_module" and c["name"] == "support" for c in caps)

    response = client.post(
        "/goals",
        json={"description": "Resolve all unresolved tier-1 tickets from last 24h"},
    )
    assert response.status_code == 202
    body = response.json()
    execution_id = body["execution_id"]
    assert response.headers["location"] == f"/executions/{execution_id}"

    # Poll like a machine.
    final = client.get(f"/executions/{execution_id}").json()
    deadline = 100
    while final["status"] not in {"completed", "failed", "cancelled"} and deadline:
        import time

        time.sleep(0.05)
        final = client.get(f"/executions/{execution_id}").json()
        deadline -= 1
    assert final["status"] == "completed"
    assert final["outcome"]["validated"] is True
    assert final["outcome"]["metrics"]["tickets_resolved"] >= 1
    assert any(e["kind"] == "outcome_delivered" for e in final["trace"])


def test_nli_compiles_utterance_into_goal_shape(client: TestClient) -> None:
    compiled = client.post(
        "/nli/compile",
        json={"text": "Please handle my Q3 SOC-2 compliance report"},
    ).json()
    assert compiled["goal"]["type"] == "compliance"
    assert compiled["goal"]["params"]["period"].startswith("Q3")
    assert compiled["next_step"].startswith("POST")


def test_compliance_outcome_via_api(client: TestClient) -> None:
    response = client.post("/goals", json={"description": "Generate and file Q3 SOC-2 evidence report"})
    execution_id = response.json()["execution_id"]
    final = client.get(f"/executions/{execution_id}").json()
    deadline = 100
    while final["status"] not in {"completed", "failed", "cancelled"} and deadline:
        import time

        time.sleep(0.05)
        final = client.get(f"/executions/{execution_id}").json()
        deadline -= 1
    assert final["status"] == "completed"
    assert final["outcome"]["metrics"]["filed"] is True
    assert len(final["outcome"]["artifacts"]) == 1


def test_unroutable_goal_returns_422_with_reason(client: TestClient) -> None:
    response = client.post("/goals", json={"description": "Water my office plants"})
    assert response.status_code == 422
    assert "No outcome module accepts" in response.json()["detail"]


def test_unknown_execution_is_404(client: TestClient) -> None:
    assert client.get("/executions/exec_does_not_exist").status_code == 404


def test_a2a_register_and_message(client: TestClient) -> None:
    created = client.post("/agents", json={"role": "external-fixer"}).json()
    agent_id = created["agent_id"]

    sent = client.post(
        "/a2a/messages",
        json={"sender": "orchestrator-1", "recipient": agent_id, "type": "capability_query", "payload": {}},
    )
    assert sent.status_code == 202

    drained = client.get(f"/agents/{agent_id}/messages").json()
    assert drained and "capabilities" in drained[0]


def test_approval_endpoint_flow(store) -> None:
    """Opt-in gate over HTTP: pause → grant → complete."""
    from fastapi.testclient import TestClient

    from agentos.interfaces.api import Platform, create_app

    gated = Platform(store=store)
    gated.engine.approval_policy = "required"
    with TestClient(create_app(platform=gated)) as http:
        submission = http.post(
            "/goals", json={"description": "Resolve all unresolved tier-1 tickets from last 24h"}
        ).json()
        exec_id = submission["execution_id"]

        paused = http.get(f"/executions/{exec_id}").json()
        assert paused["status"] == "awaiting_approval"

        decided = http.post(f"/executions/{exec_id}/approvals", json={"decision": "grant", "approver": "corey"})
        assert decided.status_code == 200

        final = http.get(f"/executions/{exec_id}").json()
        deadline = 100
        while final["status"] not in {"completed", "failed", "cancelled"} and deadline:
            import time

            time.sleep(0.05)
            final = http.get(f"/executions/{exec_id}").json()
            deadline -= 1
        assert final["status"] == "completed"


def test_api_key_gate_enforced_when_configured(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from agentos.interfaces.api import Platform, create_app

    monkeypatch.setenv("AGENTOS_API_KEY", "sekrit")
    from agentos.utils.config import get_settings

    get_settings.cache_clear()
    try:
        platform = Platform()
        with TestClient(create_app(platform=platform)) as http:
            assert http.get("/capabilities").status_code == 401
            assert (
                http.get("/capabilities", headers={"Authorization": "Bearer sekrit"}).status_code
                == 200
            )
    finally:
        get_settings.cache_clear()


def test_goal_roundtrip_listing(client: TestClient) -> None:
    post = client.post("/goals", json={"description": "Update all outdated dependencies and run full test suite"})
    goal_id = post.json()["goal_id"]
    fetched = client.get(f"/goals/{goal_id}")
    assert fetched.status_code == 200
    listed = client.get("/goals").json()
    assert any(g["id"] == goal_id for g in listed)
