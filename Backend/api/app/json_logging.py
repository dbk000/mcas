"""
JSON Structured Logging
=======================

Configures stdlib logging to emit one JSON object per line to stdout.
Context vars (run_id, worker_id, symbol, correlation_id) are automatically
injected into every log record by the formatter.

Usage:
    from json_logging import setup_logging, ctx_correlation_id
    setup_logging()

    # Context propagates to all downstream loggers automatically
    token = ctx_correlation_id.set("req-abc-123")
    logger.info("Processing request")  # includes correlation_id in JSON
    ctx_correlation_id.reset(token)
"""

import os
import sys
import logging
from datetime import datetime, timezone
from contextvars import ContextVar

from pythonjsonlogger import jsonlogger

# ---------------------------------------------------------------------------
# Context vars — set per-request/per-run, read by formatter at emit time
# ---------------------------------------------------------------------------
ctx_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")
ctx_run_id: ContextVar[str] = ContextVar("run_id", default="")
ctx_worker_id: ContextVar[str] = ContextVar("worker_id", default="")
ctx_symbol: ContextVar[str] = ContextVar("symbol", default="")

# ---------------------------------------------------------------------------
# Service identity (set via env)
# ---------------------------------------------------------------------------
SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")
SERVICE_VERSION = os.getenv("ENGINE_VERSION", "1.0.0")


class MCASJsonFormatter(jsonlogger.JsonFormatter):
    """
    JSON formatter that auto-injects service identity and context vars.

    Every log line becomes:
    {
      "timestamp": "...", "level": "INFO", "logger": "api.access",
      "message": "...", "service": "api", "version": "1.0.0",
      "correlation_id": "...", ...
    }
    """

    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)

        log_record["timestamp"] = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        log_record["level"] = record.levelname
        log_record["logger"] = record.name

        log_record["service"] = SERVICE_NAME
        log_record["version"] = SERVICE_VERSION

        # Inject active context vars (only if set / non-empty)
        for name, var in (
            ("correlation_id", ctx_correlation_id),
            ("run_id", ctx_run_id),
            ("worker_id", ctx_worker_id),
            ("symbol", ctx_symbol),
        ):
            val = var.get("")
            if val:
                log_record[name] = val

        # Structured exception info
        if record.exc_info and not log_record.get("exception"):
            log_record["exception"] = self.formatException(record.exc_info)


def setup_logging(level: str = None):
    """
    Call once at process startup before any other logging.
    Replaces the root logger's handlers with a single JSON stdout handler.
    """
    level = level or os.getenv("LOG_LEVEL", "INFO")

    handler = logging.StreamHandler(sys.stdout)
    formatter = MCASJsonFormatter()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper — returns a stdlib logger."""
    return logging.getLogger(name)
