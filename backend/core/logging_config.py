"""
DEVOPS-004: Structured JSON logging configuration

Provides JSON-formatted logs for production environments to enable:
- Easy parsing by log aggregation tools (ELK, Datadog, CloudWatch)
- Structured searching and filtering
- Consistent log format across all services

In development, uses human-readable format for easier debugging.
"""

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """
    Custom JSON formatter for structured logging.

    Output format:
    {
        "timestamp": "2026-02-06T12:34:56.789Z",
        "level": "INFO",
        "logger": "uvicorn.access",
        "message": "Request completed",
        "service": "bnk-forge",
        "extra": { ... }
    }
    """

    def __init__(self, service_name: str = "bnk-forge"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": self.service_name,
        }

        # OBS-001: Include correlation ID if present
        request_id = getattr(record, "request_id", None)
        if request_id and request_id != "-":
            log_data["request_id"] = request_id

        # Add source location for errors
        if record.levelno >= logging.WARNING:
            log_data["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add any extra fields passed to the logger
        # e.g., logger.info("message", extra={"request_id": "abc123"})
        standard_attrs = {
            'name', 'msg', 'args', 'created', 'filename', 'funcName',
            'levelname', 'levelno', 'lineno', 'module', 'msecs',
            'pathname', 'process', 'processName', 'relativeCreated',
            'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
            'taskName', 'message'
        }
        extra = {k: v for k, v in record.__dict__.items() if k not in standard_attrs}
        if extra:
            log_data["extra"] = extra

        return json.dumps(log_data, default=str)


class HumanReadableFormatter(logging.Formatter):
    """
    Human-readable formatter for development.

    Output format:
    2026-02-06 12:34:56 | INFO     | uvicorn.access | Request completed
    """

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )


def configure_logging(
    environment: str = "development",
    log_level: str = "INFO",
    service_name: str = "bnk-forge"
) -> None:
    """
    Configure application-wide logging.

    Args:
        environment: "development", "staging", or "production"
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        service_name: Service name for JSON logs

    In production/staging: Uses JSON format
    In development: Uses human-readable format
    """
    # Determine format based on environment
    use_json = environment in ("production", "staging")

    # Allow override via environment variable
    if os.getenv("LOG_FORMAT", "").lower() == "json":
        use_json = True
    elif os.getenv("LOG_FORMAT", "").lower() == "text":
        use_json = False

    # Create formatter
    formatter: logging.Formatter
    if use_json:
        formatter = JSONFormatter(service_name=service_name)
    else:
        formatter = HumanReadableFormatter()

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add stream handler (stdout)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    # OBS-001: Add correlation ID filter so request_id appears in all logs
    from core.correlation import CorrelationLogFilter
    root_logger.addFilter(CorrelationLogFilter())

    # Reduce noise from verbose libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)

    # Log startup info
    logger = logging.getLogger(__name__)
    logger.info(
        f"Logging configured: format={'JSON' if use_json else 'text'}, "
        f"level={log_level}, environment={environment}"
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the given name.

    Usage:
        from core.logging_config import get_logger
        logger = get_logger(__name__)
        logger.info("Something happened", extra={"user_id": 123})
    """
    return logging.getLogger(name)
