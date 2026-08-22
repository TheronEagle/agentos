# Writing Agent-Native Services

A guide for contributors building on AgentOS — where the operator is an
autonomous agent and the product is a delivered outcome.

## The Mindset Shift

| You're probably used to… | Agent-native instead… |
|---|---|
| Designing screens & user flows | Designing **Goals** & success criteria |
| Input forms with validation | Typed `params` schemas published via `/capabilities` |
| Error messages for humans | Structured failures in the execution trace |
| "The user will click through this" | "An agent must complete this unattended at 3am" |
| Docs explaining how your API works | Schemas that *are* the docs |

## Rules for Agent-Native Endpoints

1. **Accept Goals, not commands.** If your endpoint takes 12 optional fields,
   you've built a form. Take an outcome description plus typed params.
2. **Return IDs immediately.** Long work → `202` + ExecutionID + poll/webhook.
   Synchronous responses are for reads only.
3. **Publish machine guidance.** Every endpoint gets an
   `openapi_extra={"x-agent-instructions": "…"}` telling an agent when/how to
   call it. Agents read the schema; write those strings for them.
4. **Failures are data.** Raise `HTTPException` with a *reason*; inside modules,
   mark tasks failed with structured errors. An agent should be able to decide
   what to do next from the response alone.
5. **Never require a human decision mid-flow.** If oversight is genuinely
   needed, use the approval-gate policy (`risk_level="high"` or
   `requires_approval=True`) — don't invent bespoke blocking.

## Consuming the Platform as an Agent

```python
import httpx, time

base = "http://localhost:8080"

# 1. Discover capabilities (schemas, not docs).
caps = httpx.get(f"{base}/capabilities").json()
support = next(c for c in caps if c["name"] == "support")

# 2. Compile natural language (optional) or construct the Goal directly.
goal = {
    "description": "Resolve all unresolved tier-1 tickets from last 24h",
    "requested_by": "my-agent-id",
    # "callback_url": "https://your-agent.example/outcomes",  # push instead of poll
}

# 3. Delegate.
resp = httpx.post(f"{base}/goals", json=goal)
assert resp.status_code == 202
exec_id = resp.json()["execution_id"]

# 4. Collect the outcome.
while True:
    ex = httpx.get(f"{base}/executions/{exec_id}").json()
    if ex["status"] in {"completed", "failed", "cancelled"}:
        break
    time.sleep(0.5)

print(ex["outcome"]["summary"], ex["outcome"]["metrics"])
```

Verify webhook payloads with `X-AgentOS-Signature` (hex HMAC-SHA256 of the raw
body using `AGENTOS_WEBHOOK_SECRET`) — see `agentos/interfaces/webhook.py::sign`.

## Debugging an Autonomous Run

Every execution carries its full trace:

```bash
curl -s localhost:8080/executions/$EXEC_ID | jq '.trace[] | {kind, message, task_id}'
```

Trace kinds: `plan_created`, `task_started/succeeded/failed`,
`approval_requested/granted/denied`, `validation`, `outcome_delivered`,
`webhook_sent`, `error`. Logs carry the same IDs (`execution_id`, `task_id`)
as JSON keys — correlate freely between trace, logs, and OTel spans.
