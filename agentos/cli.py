"""CLI entrypoints.

`agentos-server` boots the API. Kept deliberately tiny — the API is the
product; the CLI just starts it.
"""

from __future__ import annotations

import uvicorn

from agentos.utils.config import get_settings


def serve() -> None:
    """Run the AgentOS API server."""
    settings = get_settings()
    uvicorn.run(
        "agentos.interfaces.api:get_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )
