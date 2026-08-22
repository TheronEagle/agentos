"""Structured logging.

Agents must be debuggable. Every log line is JSON with stable keys so log
aggregators — or other agents — can filter by goal_id / execution_id /
task_id without regex archaeology.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Emit machine-parseable single-line JSON logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Structured context attached via `log.info("msg", extra={"goal_id": ...})`
        for key in ("goal_id", "execution_id", "task_id", "agent_id", "module"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "info") -> None:
    """Configure root logging once per process."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers[:] = [handler]


def get_logger(name: str) -> logging.LoggerAdapter[logging.Logger]:
    """Logger that accepts keyword context: log.info("x", extra={"goal_id": ...})."""
    return logging.LoggerAdapter(logging.getLogger(name), {})
