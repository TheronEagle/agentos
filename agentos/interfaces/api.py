"""FastAPI interface — the machine-first product surface.

Design rules:
  • Every endpoint consumes/produces typed JSON; OpenAPI is generated with
    `x-agent-instructions` so agents can self-serve without docs.
  • Every mutating endpoint accepts Goals/IDs — never forms, never sessions.
  • Results are async: submit returns an ExecutionID immediately.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agentos.agents.a2a import A2ABus
from agentos.agents.orchestrator import Orchestrator
from agentos.core.engine import Engine, RoutingError
from agentos.core.planner import Planner, PlanningError
from agentos.core.registry import Registry
from agentos.core.state import BaseStore, make_store
from agentos.models import Goal
from agentos.utils.config import get_settings
from agentos.utils.llm_client import create_llm_client
from agentos.utils.logging_utils import get_logger, setup_logging

log = get_logger(__name__)

API_VERSION = "0.1.0"


def x_agent_instructions(instructions: str) -> dict[str, str]:
    """OpenAPI extension marking endpoints with machine-usable guidance."""
    return {"x-agent-instructions": instructions}


class Platform:
    """Composition root: wires registry, engine, agents, and stores."""

    def __init__(self, store: BaseStore | None = None) -> None:
        settings = get_settings()
        self.settings = settings
        self.store = store or make_store(settings.database_url)
        self.registry = Registry()
        self.llm = create_llm_client(
            provider=settings.llm_provider,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        self.planner = Planner(self.registry, llm=self.llm)
        self.engine = Engine(
            registry=self.registry,
            planner=self.planner,
            store=self.store,
            approval_policy=settings.approval_policy,
        )
        self.bus = A2ABus()
        self.orchestrator = Orchestrator(engine=self.engine, registry=self.registry, bus=self.bus)

        # Built-in outcome modules register themselves here. Integrations
        # go live automatically when their env credentials are present —
        # no code changes needed to move from fixtures to real systems.
        import os

        from agentos.services.integrations.github import GitHubClient
        from agentos.services.integrations.jira import JiraClient
        from agentos.services.integrations.slack import SlackClient
        from agentos.services.outcomes.codebase import CodebaseModule
        from agentos.services.outcomes.compliance import ComplianceModule
        from agentos.services.outcomes.support import SupportModule

        self.support = SupportModule(
            jira=JiraClient(
                base_url=os.environ.get("AGENTOS_JIRA_BASE_URL"),
                token=os.environ.get("AGENTOS_JIRA_TOKEN"),
            ),
            slack=SlackClient(
                bot_token=os.environ.get("AGENTOS_SLACK_BOT_TOKEN") or None,
            ),
            llm=self.llm,
        )
        self.compliance = ComplianceModule(
            slack=SlackClient(bot_token=os.environ.get("AGENTOS_SLACK_BOT_TOKEN") or None),
        )
        self.codebase = CodebaseModule(
            github=GitHubClient(
                base_url=os.environ.get("AGENTOS_GITHUB_BASE_URL"),
                token=os.environ.get("AGENTOS_GITHUB_TOKEN"),
            ),
        )
        for module in (self.support, self.compliance, self.codebase):
            self.registry.register_module(module)

    async def startup(self) -> None:
        sql_store = getattr(self.store, "create_tables", None)
        if sql_store is not None:
            await sql_store()

    async def shutdown(self) -> None:
        close = getattr(self.store, "close", None)
        if close is not None:
            await close()


class AgentRegistration(BaseModel):
    """Body for registering an external agent."""

    role: str = "external-worker"
    capabilities: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalDecision(BaseModel):
    """Body for the opt-in approval gate endpoint."""

    decision: str = Field(pattern="^(grant|deny)$")
    approver: str = "human"


def require_api_key(
    authorization: str | None = Header(default=None),
) -> None:
    """Bearer-token gate, active only when AGENTOS_API_KEY is configured."""
    settings = get_settings()
    if not settings.api_key:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, settings.api_key):
        raise HTTPException(status_code=401, detail="invalid bearer token")


def create_app(platform: Platform | None = None) -> FastAPI:
    """Build the FastAPI application. `platform` injectable for tests."""
    platform = platform or Platform()
    settings = platform.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        setup_logging(settings.log_level)
        await platform.startup()
        log.info("agentos up", extra={"module": "api"})
        yield
        await platform.shutdown()

    app = FastAPI(
        title="AgentOS",
        version=API_VERSION,
        description=(
            "Service-as-Software execution platform. Autonomous agents are the "
            "operators; you delegate outcomes. Start at `POST /goals`, poll "
            "`GET /executions/{execution_id}`, or subscribe via `callback_url`."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )

    # CORS: the project's own static site (and any agent page) may call the API
    # from a browser. Wide-open by default — gate real callers with AGENTOS_API_KEY.
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("AGENTOS_CORS_ORIGINS", "*").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Discovery ────────────────────────────────────────────────────────────

    @app.get(
        "/health",
        tags=["system"],
        openapi_extra=x_agent_instructions("Liveness probe. 200 means submit-able."),
    )
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": API_VERSION,
            "modules": [m.name for m in platform.registry.modules()],
            "llm_provider": settings.llm_provider,
        }

    @app.get(
        "/capabilities",
        tags=["discovery"],
        openapi_extra=x_agent_instructions(
            "Call this FIRST. It returns every capability as a typed JSON-Schema "
            "contract. Compose requests from these schemas; never guess fields."
        ),
    )
    async def capabilities(
        kind: str | None = Query(default=None, description="tool | outcome_module | integration | a2a"),
        tag: str | None = Query(default=None),
        _: None = Depends(require_api_key),
    ) -> list[dict[str, Any]]:
        return [c.model_dump() for c in platform.registry.capabilities(kind=kind, tag=tag)]

    # ── Goals ────────────────────────────────────────────────────────────────

    @app.post(
        "/goals",
        status_code=202,
        tags=["outcomes"],
        openapi_extra=x_agent_instructions(
            "Delegate an outcome. Body is a Goal: {\"description\": \"<what done "
            "looks like>\"}. Returns 202 with goal_id + execution_id immediately. "
            "Poll GET /executions/{execution_id} or set callback_url for push."
        ),
    )
    async def submit_goal(
        goal: Goal,
        _: None = Depends(require_api_key),
    ) -> JSONResponse:
        try:
            if goal.params.get("sub_goals"):
                execution = await platform.orchestrator.run(goal)
            else:
                execution = await platform.engine.submit(goal)
        except RoutingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except PlanningError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(
            status_code=202,
            content={
                "goal_id": goal.id,
                "execution_id": execution.id,
                "status": execution.status,
                "module": execution.module,
                "links": {
                    "self": f"/executions/{execution.id}",
                    "goal": f"/goals/{goal.id}",
                },
            },
            headers={"Location": f"/executions/{execution.id}"},
        )

    @app.get("/goals/{goal_id}", tags=["outcomes"])
    async def get_goal(goal_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
        goal = await platform.store.get_goal(goal_id)
        if goal is None:
            raise HTTPException(status_code=404, detail=f"goal {goal_id!r} not found")
        return goal.model_dump()

    @app.get("/goals", tags=["outcomes"])
    async def list_goals(
        limit: int = Query(default=50, le=200),
        offset: int = Query(default=0, ge=0),
        _: None = Depends(require_api_key),
    ) -> list[dict[str, Any]]:
        goals = await platform.store.list_goals(limit=limit, offset=offset)
        return [g.model_dump() for g in goals]

    # ── Executions ───────────────────────────────────────────────────────────

    @app.get(
        "/executions/{execution_id}",
        tags=["outcomes"],
        openapi_extra=x_agent_instructions(
            "Poll until status is completed|failed|cancelled. `outcome` carries "
            "the delivered result; `trace` is the full audit log."
        ),
    )
    async def get_execution(execution_id: str, _: None = Depends(require_api_key)) -> dict[str, Any]:
        execution = await platform.store.get_execution(execution_id)
        if execution is None:
            raise HTTPException(status_code=404, detail=f"execution {execution_id!r} not found")
        return execution.model_dump()

    @app.get("/executions", tags=["outcomes"])
    async def list_executions(
        goal_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        limit: int = Query(default=50, le=200),
        offset: int = Query(default=0, ge=0),
        _: None = Depends(require_api_key),
    ) -> list[dict[str, Any]]:
        executions = await platform.store.list_executions(goal_id=goal_id, status=status, limit=limit, offset=offset)
        return [e.model_dump() for e in executions]

    # ── Human-in-the-loop (opt-in gates) ─────────────────────────────────────

    @app.post(
        "/executions/{execution_id}/approvals",
        tags=["governance"],
        openapi_extra=x_agent_instructions(
            "Only relevant when an approval gate paused the execution "
            "(status=awaiting_approval). decision: grant|deny."
        ),
    )
    async def decide_approval(
        execution_id: str,
        decision: ApprovalDecision,
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        execution = await platform.engine.approve_and_resume(execution_id, decision.decision, decision.approver)
        if execution is None:
            raise HTTPException(status_code=409, detail=f"execution {execution_id!r} is not awaiting approval")
        return execution.model_dump()

    # ── Agents & A2A ─────────────────────────────────────────────────────────

    @app.post(
        "/agents",
        status_code=201,
        tags=["agents"],
        openapi_extra=x_agent_instructions(
            "Register an external agent. It immediately appears in /capabilities "
            "and becomes routable for goals it declares."
        ),
    )
    async def register_agent(
        registration: AgentRegistration,
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        from agentos.agents.worker import Worker

        worker = Worker(role=registration.role, registry=platform.registry)
        platform.bus.register(worker)
        return {"agent_id": worker.id, "role": worker.descriptor.role, "inbox": f"/agents/{worker.id}/messages"}

    @app.get("/agents", tags=["agents"])
    async def list_agents(_: None = Depends(require_api_key)) -> list[dict[str, Any]]:
        return [a.descriptor.model_dump() for a in platform.bus.agents()]

    @app.get("/agents/{agent_id}/messages", tags=["agents"])
    async def drain_agent_inbox(agent_id: str, _: None = Depends(require_api_key)) -> list[dict[str, Any]]:
        agent = platform.bus.agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail=f"agent {agent_id!r} not found")
        responses = await agent.drain_inbox()
        return responses

    @app.post("/a2a/messages", status_code=202, tags=["agents"])
    async def send_a2a_message(
        message: dict[str, Any],
        _: None = Depends(require_api_key),
    ) -> dict[str, Any]:
        from agentos.agents.base import A2AMessage

        envelope = A2AMessage(**message)
        if envelope.recipient == "*":
            count = await platform.bus.broadcast(envelope.sender, envelope.type, envelope.payload)
            return {"delivered": count, "mode": "broadcast"}
        await platform.bus.send(envelope)
        return {"delivered": 1, "mode": "direct"}

    # ── Webhooks (outbound delivery) ─────────────────────────────────────────

    @app.get("/webhooks/signature-help", tags=["webhooks"], include_in_schema=True)
    async def webhook_help() -> dict[str, str]:
        return {
            "verification": "X-AgentOS-Signature: hex hmac_sha256(body, AGENTOS_WEBHOOK_SECRET)",
            "note": "Set AGENTOS_WEBHOOK_SECRET to enable signing on callback_url deliveries.",
        }

    # Attach the NLI router (compile utterances → Goals).
    from agentos.interfaces.nli import build_nli_router

    app.include_router(build_nli_router(platform.llm))

    # Attach webhook delivery to terminal executions.
    from agentos.interfaces.webhook import install_delivery_hooks

    install_delivery_hooks(platform.engine, settings)

    return app


def get_app() -> FastAPI:
    """ASGI entrypoint: `uvicorn agentos.interfaces.api:get_app`."""
    return create_app()
