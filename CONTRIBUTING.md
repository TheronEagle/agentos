# Contributing to AgentOS

AgentOS is a manifesto with code: software where autonomous agents are the
operators and humans delegate outcomes. Contributions must strengthen that
thesis, not soften it.

## What We Merge

1. **Outcome modules** (`agentos/services/outcomes/`) — new autonomous product
   surfaces. This is the primary contribution. See [docs/OUTCOMES.md](docs/OUTCOMES.md).
2. **Integrations** (`agentos/services/integrations/`) — typed, mock-backed
   adapters that let modules reach external systems. See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).
3. **Engine/planner improvements** — better routing, scheduling, retries,
   validation hooks. Must preserve the contracts and stay dependency-light.
4. **Agent-behaviour tests** — anything that simulates what agents *do*
   (discover → delegate → poll → approve) rather than what humans click.

## What We Don't Merge

- React/Vue/Svelte frontends, admin dashboards, login pages. The OpenAPI
  schema is the interface; generate any GUI from it.
- Session-based workflows, cookie flows, click-through wizards.
- Hardcoded vendor calls outside the pluggable LLM client.
- Features requiring a human decision mid-flow (use approval-gate policy).

## Development Setup

```bash
git clone https://github.com/TheronEagle/agentos && cd agentos
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest                # 32 tests, <1s, zero services needed
ruff check agentos    # lint
```

No API keys, no Docker, no Postgres required for development — the platform
degrades deterministically (`memory://` store, `none` LLM provider).

## Conventional Commits

```
feat(outcomes): add incident-response outcome module
fix(engine): resume gated executions after denial correctly
docs(architecture): expand A2A topology section
test(planner): cover LLM fallback plan validation
refactor(registry): split tool/module registration
chore(deps): bump fastapi to 0.115
```

Scope names: `engine`, `planner`, `registry`, `state`, `api`, `nli`,
`webhook`, `queue`, `agents`, `outcomes`, `integrations`, `models`, `utils`,
`docs`, `ci`.

## Pull Request Checklist

- [ ] Type hints on every public function; `mypy agentos` has no new errors
- [ ] `pytest` green — including tests you added covering the new behaviour
- [ ] New endpoints carry `x-agent-instructions` OpenAPI extensions
- [ ] New capabilities publish JSON Schema via the registry
- [ ] Outcome modules implement all four lifecycle methods + strict `validate()`
- [ ] Integrations ship deterministic mock mode; live mode behind env vars
- [ ] Docs updated (README map, docs/, .env.example) when behaviour changes
- [ ] No secrets in code, tests, or fixtures

## Design Review Questions

Before submitting, answer honestly:

1. Can an agent use this feature end-to-end without ever opening `/docs`?
2. Does it work with `AGENTOS_LLM_PROVIDER=none` (no keys)?
3. Would this still make sense if the operator were never a human?
4. Is every side effect visible in the execution trace?

If any answer is "no", iterate before opening the PR.
