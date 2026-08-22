# Adding Tool Integrations

Integrations are typed adapters to external systems (Jira, GitHub, Slack, …).
They are deliberately thin: outcome modules own *decisions*; integrations own
*transport*.

## The Pattern

Every integration in `agentos/services/integrations/` follows the same shape:

```python
class MySystemClient:
    """Typed adapter. Mock-backed by default; live via config."""

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token = token
        self._fixture_state = ...   # deterministic mock data

    @property
    def live(self) -> bool:
        return self.base_url is not None

    async def do_thing(self, arg: str) -> ResultModel:      # pydantic in/out
        if not self.live:
            return _fixture_result(arg)                      # deterministic
        async with httpx.AsyncClient(...) as client:         # real transport
            response = await client.post(...)
            response.raise_for_status()
            return ResultModel(**response.json())
```

### Rules

1. **Pydantic models for all I/O.** Same typing discipline as the rest of the
   platform — agents consume these schemas.
2. **The mock IS the contract test.** Mock mode must exercise exactly what live
   mode does, minus HTTP. If the mock can't represent a live behaviour, the
   abstraction is wrong.
3. **Async everywhere** (`httpx.AsyncClient`). Never block the event loop.
4. **No decisions in adapters.** A `resolve_ticket()` method is fine;
   "decide whether this ticket deserves a refund" belongs in an outcome module.
5. **Config via env**, read by the module that constructs the client
   (`AGENTOS_JIRA_BASE_URL`, `AGENTOS_JIRA_TOKEN`, …). Add entries to
   `.env.example`.
6. **Timeouts explicit.** Long operations (CI runs) get long timeouts; reads
   get short ones.

## Wiring an Integration Into a Module

```python
class ShippingModule(OutcomeModule):
    def __init__(self, courier: CourierClient | None = None) -> None:
        self.courier = courier or CourierClient(
            base_url=os.getenv("AGENTOS_COURIER_BASE_URL"),
            token=os.getenv("AGENTOS_COURIER_TOKEN"),
        )
```

Dependency injection keeps modules testable: tests pass fake clients,
production gets configured ones.

## Exposing Integration Tools to Agents

If other agents should call your integration directly (not through a module),
register tools at boot:

```python
registry.register_tool(
    "courier.get_quotes",
    description="Get delivery quotes from courier partners.",
    input_schema=QuoteRequest.model_json_schema(),
    handler=courier.get_quotes,
    risk_level="low",
)
```

Tools appear in `GET /capabilities` and become executable by Workers via
`task.action = "courier.get_quotes"`.

## Current Integrations

| Client | Mock fixtures | Live config |
|---|---|---|
| `JiraClient` | 6 seeded tier-1/tier-2 tickets | `AGENTOS_JIRA_BASE_URL` + token |
| `GitHubClient` | outdated deps → green CI → PR #n | `AGENTOS_GITHUB_BASE_URL` + token |
| `SlackClient` | messages stored in memory | `AGENTOS_SLACK_BOT_TOKEN` |

PRs adding integrations should include: the adapter with mock/live modes,
fixtures that mirror real API shapes, and one outcome-module or tool usage so
the integration is reachable from goals.
