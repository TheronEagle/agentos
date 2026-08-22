<div align="center">

# AgentOS

**Rebuilding Software for AI Agents — From SaaS to Service-as-Software**

*You don't use AgentOS. You delegate outcomes to it.*

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![API-first](https://img.shields.io/badge/interface-OpenAPI%20first-6B46C1.svg)](#the-api-is-the-product)

</div>

---

## The Thesis

For twenty years, software has been sold as **seats + features**: a human buys a
license, learns a GUI, and clicks through workflows one screen at a time.
SaaS made distribution cheaper. It did not change the deal — *you still do the work.*

**Service-as-Software inverts the unit of sale.** You don't buy access to
features; you buy *outcomes*. "Resolve my tier-1 tickets." "File my Q3 compliance
report." "Update my dependencies and open a PR." Autonomous agents are the
operators. Humans (and other agents) state what done looks like; the platform
plans, executes, self-checks, and delivers.

AgentOS is that platform, as an open-source reference implementation:

| SaaS world | Service-as-Software world |
|---|---|
| GUI first, API as an afterthought | **API & schema first** — GUIs are generated from the OpenAPI spec, if at all |
| Seat-based pricing | **Outcome-based pricing** — you pay per delivered result |
| Human-operated, click by click | **Agent-operated**, end to end |
| Sessions, forms, dashboards | **Goals, executions, audit trails** |
| Help docs and onboarding webinars | **Capability registry** — agents discover abilities via `GET /capabilities` |
| Chatbot bolted onto a dashboard | **Agent-native from day one** — no retrofitting |

## Quickstart

```bash
git clone https://github.com/TheronEagle/agentos && cd agentos
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'

# Zero-config demo mode: deterministic LLM stubs, in-memory state.
uvicorn agentos.interfaces.api:get_app --factory --port 8080
```

Now delegate your first outcome — no login, no UI, just the contract:

```bash
# What can this platform do? (machines read this, not humans)
curl -s localhost:8080/capabilities | jq '.[].name'

# Delegate an outcome. Get an ExecutionID back immediately.
curl -s -X POST localhost:8080/goals \
  -H 'Content-Type: application/json' \
  -d '{"description": "Resolve all unresolved tier-1 tickets from last 24h"}'
# → {"goal_id": "...", "execution_id": "exec_...", "status": "planning", ...}

# Poll for the delivered outcome.
curl -s localhost:8080/executions/exec_... | jq '.outcome'
```

Or spin up the full stack (Postgres + Redis + Celery workers):

```bash
cp .env.example .env   # optional overrides
docker compose up --build
```

### Three outcomes, zero clicks

```python
from agentos.models import Goal
from agentos.interfaces.api import Platform

platform = Platform()

# Support: read tickets → draft replies → resolve → notify Slack
await platform.engine.submit(Goal(description="Resolve all unresolved tier-1 tickets from last 24h"))

# Compliance: gather evidence → assemble report → file with compliance officer
await platform.engine.submit(Goal(description="Generate and file Q3 SOC-2 evidence report"))

# Codebase: bump deps → run tests → open PR if green
await platform.engine.submit(Goal(description="Update all outdated dependencies and run full test suite"))
```

Runnable versions live in [`examples/`](examples/) — each prints the delegation,
the plan, the outcome, and the full audit trail.

## Why Not Just Use SaaS?

Because SaaS rents you *tools*; AgentOS delivers *results*.

1. **The work is the product, not the workflow.** SaaS gives you screens and asks
   you to operate them. AgentOS accepts `{"description": "resolve my tickets"}`
   and returns `{"tickets_resolved": 47}`. The difference is not cosmetic — it is
   the difference between buying a tractor and hiring a farm.

2. **Agents are the operators.** Your ops agent reads `/capabilities`, composes a
   Goal from JSON Schemas, submits it, and receives a webhook when the outcome is
   delivered. No browser. No OAuth dance across twelve tabs. No human context-switching.

3. **Outcomes are auditable by construction.** Every execution carries an
   append-only trace: plan, task attempts, LLM calls, approvals, deliveries.
   When something goes sideways you replay the trace — there is no
   "recreate the bug by clicking in the same order."

4. **Autonomy is the default; gates are opt-in.** Legacy systems assume humans
   must approve everything, so automation dies in ticket queues. AgentOS ships
   fully autonomous (`AGENTOS_APPROVAL_POLICY=never`); if you want oversight,
   set `risky_only` or `required` and approve via one endpoint call.

5. **Composability beats suites.** One outcome module can orchestrate others over
   A2A. Your "weekly ops sweep" is two lines of sub-goals, not a Zapier Rube
   Goldberg machine that breaks when someone renames a column.

## Architecture in One Screen

```
                       ┌────────────────────────────────────┐
   Goals (JSON) ──────▶│            FastAPI surface         │
   NL utterances ─────▶│  /goals · /capabilities · /nli     │
   Webhooks ◀──────────│  /executions · /agents · /a2a      │
                       └──────────────┬─────────────────────┘
                                      ▼
                       ┌────────────────────────────────────┐
                       │              Engine                │
                       │  route → plan → execute → validate │
                       │        → deliver → audit           │
                       └───┬───────────────┬────────────────┘
                           ▼               ▼
                 ┌──────────────┐  ┌───────────────────────┐
                 │   Planner    │  │  Capability Registry  │
                 │ module | LLM │  │ typed JSON-Schema     │
                 └──────────────┘  └───────────────────────┘
                           ▼               ▼
     ┌──────────────────────────┐   ┌─────────────────────────┐
     │   Outcome modules        │   │  Agents (A2A bus)       │
     │ support · compliance ·   │   │  Workers · Orchestrator │
     │ codebase                 │   │                         │
     └──────────┬───────────────┘   └─────────────────────────┘
                ▼
     ┌──────────────────────────────────────────────┐
     │ Integrations: Jira · GitHub · Slack (mock→live) │
     └──────────────────────────────────────────────┘
                ▼
     State: memory:// · SQLite · PostgreSQL | Queue: asyncio · Celery+Redis
```

Deep dive in [ARCHITECTURE.md](ARCHITECTURE.md).

## The API Is the Product

No React app. No admin dashboard. No login pages. The FastAPI-generated
OpenAPI schema is the interface, annotated with `x-agent-instructions`
extensions so agents can self-serve:

```bash
curl -s localhost:8080/openapi.json | jq '.paths["/goals"].post["x-agent-instructions"]'
# → "Delegate an outcome. Body is a Goal... Returns 202 immediately..."
```

Swagger UI at `/docs` exists only because it is free — generated from the same
schema machines consume. If you want a custom GUI later, generate it from the
schema like everything else does.

## Design Principles

1. **Natural language is a client protocol, not the product.** `POST /nli/compile`
   translates one utterance into one typed Goal. No chat sessions, no memory of
   "the conversation" — compilation and delegation stay separable.
2. **Everything async.** Submit returns `202` + ExecutionID. Poll, or provide
   `callback_url` and get HMAC-signed webhook delivery.
3. **Discovery over documentation.** Agents learn the system from
   `/capabilities`, never from READMEs.
4. **Typed everywhere.** Pydantic models all the way down; every capability
   publishes input/output JSON Schema.
5. **Graceful degradation.** No LLM key? Deterministic stubs. No Postgres?
   In-memory store. No Redis? In-process queue. The demo works offline; scale-ups
   are configuration changes, not rewrites.
6. **Human-in-the-loop is governance, not architecture.** Approval gates exist as
   explicit policy switches — never as assumptions baked into every flow.

## Repository Map

```
agentos/
├── core/          engine · planner · registry · state stores
├── interfaces/    api.py (FastAPI) · nli.py · webhook.py · queue.py (Celery)
├── agents/        base · worker · orchestrator · a2a bus
├── services/
│   ├── outcomes/      support · compliance · codebase   ← first-class citizens
│   └── integrations/  jira · github · slack             ← mock-backed, live-ready
├── models/        goal · task · execution · capability
└── utils/         config · structured logging · pluggable LLM client
tests/             agent-behaviour tests (submit/poll/approve/A2A)
examples/          runnable outcome demos + custom-agent guide
docs/              AGENTS.md · OUTCOMES.md · INTEGRATIONS.md · ARCHITECTURE.md
```

## Configuration

Everything is env-driven (`AGENTOS_*`, see [.env.example](.env.example)):
LLM backend (`openai` / `anthropic` / `openai-compatible` / `none`),
state (`memory://` / SQLite / Postgres), queue (asyncio or Celery+Redis),
autonomy policy, webhook signing secret, optional bearer auth.

## Contributing

Outcome modules are the contribution surface. Implement four methods —
`accept`, `plan`, `execute`, `validate` — register, and you have shipped a
product, not a PR against someone else's UX. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [docs/OUTCOMES.md](docs/OUTCOMES.md).

## License

MIT — see [LICENSE](LICENSE).
