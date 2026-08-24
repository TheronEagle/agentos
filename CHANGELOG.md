# Changelog

All notable changes to AgentOS. Format follows [Keep a Changelog](https://keepachangelog.com/);
versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-08-24

First public release. The thesis, working: **agents are the operators; you delegate outcomes.**

### Added

- **Execution engine** — `submit(Goal) → Execution` returns immediately (202-style).
  Full lifecycle: route → plan → opt-in approval gate → dependency-ordered task
  execution → module self-validation → HMAC-signed webhook delivery.
- **Capability registry** — agents discover abilities via `GET /capabilities`;
  every capability publishes typed JSON-Schema input/output contracts.
- **Planner** — three-tier strategy: outcome-module `plan()`, LLM JSON fallback,
  deterministic generic plan. Never fails to produce typed tasks.
- **Outcome modules** (first-class citizens):
  - `support` — fetch tier-1 tickets → draft replies → resolve → Slack notify
  - `compliance` — gather evidence → assemble SOC-2 report → file real artifact
  - `codebase` — bump deps → run tests → open PR only when green
- **Integrations** — Jira / GitHub / Slack clients, deterministic fixtures by
  default, live mode via env credentials (`AGENTOS_SLACK_BOT_TOKEN`, …).
  Mocks mutate realistically and replenish for repeat demos.
- **Agents & A2A** — Workers resolve `task.action` keys against the registry;
  Orchestrator fans composite goals out in parallel; pull-based A2A bus with
  broadcast + capability queries.
- **Interfaces** — FastAPI surface with `x-agent-instructions` OpenAPI extensions,
  compile-only NLI (`POST /nli/compile`), HMAC webhook delivery, optional
  Celery queue (`agentos[queue]`), CORS for browser/agent pages.
- **Human-in-the-loop is opt-in** — `AGENTOS_APPROVAL_POLICY=never|risky_only|required`,
  decided via one endpoint; grants/denials recorded in the immutable trace.
- **State** — `memory://` default; SQLite (`agentos[database]`) and Postgres
  (`agentos[postgres]`) behind one interface.
- **Observability** — structured JSON logs keyed by goal/execution/task/agent;
  OpenTelemetry hooks (`agentos[otel]`); append-only execution traces.
- **Zero-key demo mode** — deterministic LLM stubs; full test suite runs offline.

### Fixed

- Support-module validation now scoped per-execution — repeated delegations of
  the same goal validate independently (caught by a from-scratch clone-and-run test).
- Tasks downstream of a failed dependency are `skipped`, not `failed`.
- Approval gates pause synchronously at submission — no race with the run loop.

### Site

- Single-page manifesto at https://theroneagle.github.io/agentos with a live
  demo panel delegating real goals to a running instance; `?api=<url>` targets
  any AgentOS deployment.

[0.1.0]: https://github.com/TheronEagle/agentos/releases/tag/v0.1.0
