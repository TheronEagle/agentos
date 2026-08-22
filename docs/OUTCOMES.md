# Defining Outcome-Based Modules

Outcome modules are the first-class citizens of Service-as-Software. One module
= one outcome domain = one autonomous product.

## The Contract

```python
from typing import ClassVar
from agentos.models import Capability, Execution, Goal, Outcome, Task
from agentos.services.outcomes.base import OutcomeModule


class MyModule(OutcomeModule):
    name: ClassVar[str] = "shipping"           # unique routing key
    description: ClassVar[str] = (
        "Book the cheapest courier that delivers before the deadline "
        "and email the customer a tracking link."
    )

    def accept(self, goal: Goal) -> bool:
        """Pure predicate: can I handle this goal? No side effects."""
        return "courier" in goal.description.lower() or goal.goal_type == "shipping"

    async def plan(self, goal: Goal) -> list[Task]:
        quotes = Task(description="Get quotes from couriers", action="shipping.get_quotes", goal_id=goal.id)
        book = Task(description="Book cheapest qualifying courier", action="shipping.book",
                    depends_on=[quotes.id], risk_level="medium", goal_id=goal.id)
        notify = Task(description="Email tracking link to customer", action="shipping.notify",
                      depends_on=[book.id], risk_level="low", goal_id=goal.id)
        return [quotes, book, notify]

    async def run_task(self, task: Task, execution: Execution, goal: Goal) -> dict:
        """Engine calls this per task. Return structured results."""
        if task.action == "shipping.get_quotes":
            ...
        elif task.action == "shipping.book":
            ...
        raise ValueError(f"cannot execute {task.action!r}")

    async def execute(self, execution: Execution) -> Outcome:
        """Fold task results into one delivered Outcome."""
        return Outcome(summary="Booked courier X, ETA Thursday", metrics={"cost_usd": 14.2})

    async def validate(self, outcome: Outcome, execution: Execution) -> bool:
        """Self-check BEFORE delivery. False fails the whole run."""
        return outcome.metrics.get("tracking_link") is not None
```

Register it:

```python
platform.registry.register_module(MyModule())
```

That single call gives you:
- routing (engine asks `accept()` on every submission),
- decomposition (`plan()`),
- execution with dependency ordering and concurrency caps,
- self-validation gating delivery,
- discovery (`GET /capabilities` publishes your module + tools),
- full audit trails.

## Method-by-Method Guidance

### `accept(goal) -> bool`
Keep it cheap, pure, and honest. Match on keywords, the optional
`goal.type` hint, or structured `goal.params`. A module should refuse goals
it can't fully deliver rather than half-handle them — the engine routes to
the first accepting module, so precision beats recall.

### `plan(goal) -> list[Task]`
- Small, imperative task descriptions ("Fetch X", not "Handle the X situation").
- Use `depends_on` for ordering; independent tasks run concurrently.
- Set `risk_level="high"` on externally-visible side effects (filing,
  spending money, posting publicly). This is what `risky_only` gates trip on.
- Optionally use the platform LLM (`BaseLLMClient`) for dynamic plans;
  fall back to deterministic plans when it's unavailable.

### `execute(execution) -> Outcome`
Read `execution.tasks[i].result` (whatever `run_task` returned), fold into a
single `Outcome`: human-readable `summary`, `artifacts` (paths/URLs/IDs),
quantified `metrics`. The engine marks `validated=True` only after your
`validate()` passes.

### `validate(outcome, execution) -> bool`
Be strict. Check completeness (every fetched item resolved?), sanity
(metrics present? artifact paths well-formed?). A false here fails the run —
that's the point. Never rubber-stamp.

### Optional: `tools() -> list[Capability]`
Publish individual tools beyond the module-level capability so other agents
can compose them. Give each tool a JSON-Schema `input_schema`.

## Testing Your Module

Follow the existing suite style — tests simulate agents, not humans:

```python
async def test_my_module_delivers(platform):
    execution = await platform.engine.submit(Goal(description="book a courier for tomorrow"))
    final = await platform.engine.wait_for(execution.id, timeout=10)
    assert final.status == "completed"
    assert final.outcome.validated is True
```

Also cover: unroutable inputs (module doesn't `accept`), validation failure,
and each `run_task` branch. See `tests/test_engine.py` for patterns.

## Checklist Before Opening a PR

- [ ] `accept()` is pure and precise
- [ ] Tasks have explicit dependencies; risky ones flagged `high`
- [ ] `run_task` returns JSON-serialisable dicts
- [ ] `validate()` would actually catch your module's real failure modes
- [ ] Module + tools appear in `GET /capabilities` with real schemas
- [ ] Tests simulate delegation end-to-end (submit → poll → outcome)
- [ ] Mock-backed integration adapters; live mode behind env config
