"""Centralised settings.

Everything is environment-driven with an AGENTOS_ prefix so the same image
runs in dev, CI, and production without code changes. There is deliberately
no config file and no admin UI: agents set env vars, like adults.

If `pydantic-settings` happens to be installed we use it; otherwise an
equivalent minimal reader keeps AgentOS importable with zero dependencies.
Both paths expose identical fields and defaults.
"""

from __future__ import annotations

import os
from functools import lru_cache

_FIELDS: dict[str, tuple[object, type]] = {
    # name: (default, coercion)
    "database_url": ("memory://", str),
    "redis_url": (None, str),
    "celery_broker_url": (None, str),
    "approval_policy": ("never", str),  # never | risky_only | required
    "webhook_secret": (None, str),
    "api_key": (None, str),
    "llm_provider": ("none", str),  # openai | anthropic | openai-compatible | none
    "llm_model": (None, str),
    "llm_api_key": (None, str),
    "llm_base_url": (None, str),
    "host": ("0.0.0.0", str),
    "port": (8080, int),
    "log_level": ("info", str),
    "public_base_url": ("http://localhost:8080", str),
    "otel_endpoint": (None, str),
}

try:  # Optional nicety, not a requirement.
    from pydantic_settings import BaseSettings

    _HAS_PYDANTIC_SETTINGS = True
except ImportError:  # pragma: no cover - exercised only without pydantic-settings
    _HAS_PYDANTIC_SETTINGS = False


if _HAS_PYDANTIC_SETTINGS:

    class Settings(BaseSettings):  # type: ignore[no-redef]
        """Runtime configuration. All fields are overridable via AGENTOS_* env vars."""

        model_config = {"env_prefix": "AGENTOS_", "env_file": ".env", "extra": "ignore"}

        database_url: str = "memory://"
        redis_url: str | None = None
        celery_broker_url: str | None = None
        approval_policy: str = "never"
        webhook_secret: str | None = None
        api_key: str | None = None
        llm_provider: str = "none"
        llm_model: str | None = None
        llm_api_key: str | None = None
        llm_base_url: str | None = None
        host: str = "0.0.0.0"
        port: int = 8080
        log_level: str = "info"
        public_base_url: str = "http://localhost:8080"
        otel_endpoint: str | None = None

else:

    class Settings:  # type: ignore[no-redef]
        """Dependency-free mirror of the pydantic-settings version."""

        def __getattr__(self, name: str):
            if name in _FIELDS:
                default, coerce = _FIELDS[name]
                raw = os.environ.get(f"AGENTOS_{name.upper()}")
                if raw is None or raw == "":
                    return default
                if coerce is int:
                    try:
                        return int(raw)
                    except ValueError:
                        return default
                return raw
            raise AttributeError(f"Settings has no field {name!r}")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
