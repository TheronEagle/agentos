# AgentOS Architecture

> **The one-line version:** SaaS sells access to software and humans operate it.
> AgentOS accepts outcomes and autonomous agents deliver them. Every architectural
> decision below follows from that inversion.

---

## 1. From GUI-First to API-First (Actually Schema-First)

Traditional products build screens first and expose APIs as a legal obligation.
AgentOS has no product surface except the OpenAPI schema:

```
┌─────────────┐   generates    ┌──────────────────┐   consumed by
│ Typed models │ ────────────▶ │  OpenAPI schema   │ ◀───── agents (machines)
│ (Pydantic)   │               │  + x-agent-instr. │ ◀───── Swagger UI (free)
└─────────────┘                └──────────────────┘ ◀───── generated GUIs (optional)
```

- **Typed models are the single source of truth.** `Goal`, `Task`, `Execution`,
  `Capability` are Pydantic models; JSON Schemas for every capability are derived,
  never hand-written.
- **`x-agent-instructions` extensions** annotate endpoints with machine-usable
  guidance ("call this FIRST", "returns 202 immediately"). An agent reading the
  schema needs no documentation.
- **No sessions, no forms.** Every mutating endpoint consumes typed JSON and
  returns an ID. The only "state" is durable execution state — not browser state.

## 2. From Seat-Based to Outcome-Based

| | SaaS | AgentOS |
|---|---|---|
| Unit of sale | seat/month | outcome/delivered-result |
| What the customer buys | access to features | **completed work** |
| Success metric | DAU, session length | goals completed, outcomes validated |
| Pricing pressure | maximize engagement | minimize time-to-outcome |

An **Outcome Module** is the productization of this idea. Each module owns one
outcome domain end-to-end via four methods:

```python
class OutcomeModule(ABC):
    def accept(self, goal: Goal) -> bool: ...          # Can I handle this?
    async def plan(self, goal: Goal) -> list[Task]: ... # Decompose into work
    async def execute(self, execution) -> Outcome: ...  # Run autonomously
    async def validate(self, outcome, execution) -> bool: ...  # Self-check before delivery
```

The engine routes by asking every registered module `accept(goal)` — or honors an
explicit `type` hint on the goal. Modules register their capabilities into the
registry at boot; discovery is automatic.

## 3. From Human-Operated to Agent-Operated

### 3.1 The Execution Lifecycle

```
 submit(Goal)
     │
     ▼
 route ──✗──▶ RoutingError(422)          no module accepts
     │
     ▼
 plan ───▶ [Task, Task, …]               module.plan() or LLM fallback
     │
     ▼
 gate? ──▶ awaiting_approval             ONLY if policy ≠ never (opt-in!)
     │                                      resumes via POST /executions/{id}/approvals
     ▼
 execute tasks                           dependency-ordered, concurrency-capped,
     │                                   structured per-task results/failures
     ▼
 validate(outcome)                       module self-checks its own delivery
     │
     ├─ fail → status=failed + trace
     ▼
 deliver                                 webhook to callback_url (HMAC-signed)
     │
     ▼
 terminal state + append-only audit trail
```

`submit()` returns an `ExecutionID` immediately (HTTP 202). Nobody waits
synchronously; callers poll `GET /executions/{id}` or receive a signed callback.

### 3.2 Agents Are Operators, Not Features

- **Workers** claim tasks and resolve `task.action` keys against the capability
  registry. Unknown action → structured failure, never a guess.
- **The Orchestrator** composes modules: a goal carrying `params.sub_goals`
  fans out to child engines in parallel over the A2A bus and folds partial
  results into one composite Outcome.
- **A2A bus**: address-based (`agent_id`), pull-based inboxes, broadcast +
  capability queries. The envelope (`A2AMessage`) is transport-agnostic — the
  same shape works in-process, over Redis pub/sub, or HTTP between deployments.

### 3.3 Capability Discovery Over Documentation

Agents never read READMEs to learn what they can do:

```bash
curl localhost:8080/capabilities | jq '.[] | {name, kind, input_schema}'
```

Every capability carries JSON Schema input/output contracts and a risk level.
This is how the platform stays composable: new modules integrate without any
consumer-side changes, because consumers read schemas, not docs.

## 4. Autonomy Is Default; Governance Is Configuration

Human-in-the-loop is **explicit policy**, never architectural assumption:

```bash
AGENTOS_APPROVAL_POLICY=never      # default: full autonomy
AGENTOS_APPROVAL_POLICY=risky_only # pause before risk_level="high" tasks
AGENTOS_APPROVAL_POLICY=required   # pause before every task
```

Gates pause the execution *synchronously* at submission (status
`awaiting_approval`, no race conditions) and resume through one endpoint:
`POST /executions/{id}/approvals {"decision": "grant"|"deny"}`. Denials cancel;
grants re-enter the run loop. Every decision lands in the immutable trace.

## 5. State & Scaling Topology

```
 dev/laptop        memory:// + asyncio executor        zero dependencies
 single node       sqlite+aiosqlite://                 pip install agentos[database]
 production        postgresql+asyncpg + Celery/Redis   docker compose up
                   (API replicas + worker fleet)
```

- **Durable state** (goals, executions, traces) → SQL document rows behind
  `BaseStore`. Swapping backends is a URL change.
- **Ephemeral context** (locks, scratchpads) → Redis when configured; the A2A
  bus and stores degrade to in-process structures otherwise.
- **Execution** → in-process asyncio by default; Celery workers when
  `AGENTOS_CELERY_BROKER_URL` is set. Identical engine semantics either way.

## 6. Debuggability Is a Design Constraint

Autonomous systems you cannot debug are systems you cannot ship:

- **Append-only traces** on every execution: `plan_created`, `task_started`,
  `llm_call`, `validation`, `webhook_sent`… Never rewritten, only extended.
- **Structured JSON logs** with stable keys (`goal_id`, `execution_id`,
  `task_id`, `agent_id`) so aggregators filter without regex.
- **OpenTelemetry hooks** (`pip install agentos[otel]`) export spans to any
  OTLP endpoint.
- **Deterministic degradation**: with `AGENTOS_LLM_PROVIDER=none`, the entire
  platform runs on stubs — reproducible CI, offline demos, testable failure paths.

## 7. Deliberate Non-Decisions

- **No React frontend.** Swagger UI ships free; anything richer gets generated
  from the same schema. A hand-built admin dashboard would re-introduce the
  human-operated assumptions this architecture exists to delete.
- **No login pages.** Machine callers authenticate with bearer tokens
  (`AGENTOS_API_KEY`) or nothing (local/dev). Humans approving gates identify
  via a string field — identity belongs to your IdP, not this platform.
- **No vendor lock-in.** LLM access goes through one interface
  (`BaseLLMClient`) with OpenAI / Anthropic / any OpenAI-schema endpoint behind it.
