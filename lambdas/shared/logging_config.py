"""Structured logging with structlog. All log entries are JSON so they
land cleanly in CloudWatch Logs Insights + stream to QuickSight.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


def configure_logging() -> structlog.stdlib.BoundLogger:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level, logging.INFO),
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger()


def bind_job_context(logger: Any, job_id: str, question_id: str | None = None) -> Any:
    """Bind Step Functions execution context to every log line in the handler."""
    ctx = {"job_id": job_id}
    if question_id:
        ctx["question_id"] = question_id
    return logger.bind(**ctx)
