"""MCP tool invocation observability helpers.

Bounded Slice 4 scope:
- structured invocation start/result logs
- catalog-enriched metadata when available
- no argument/payload logging to avoid secret leakage
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Protocol

from .tool_catalog import get_high_risk_tool_catalog

logger = logging.getLogger(__name__)


class _MCPToolRegistrar(Protocol):
    def tool(self, *args: Any, **kwargs: Any): ...


_CATALOG_BY_TOOL: dict[str, dict[str, Any]] = {
    str(entry["tool_name"]): entry for entry in get_high_risk_tool_catalog()
}

# Fail-safe fallback for tools with no catalog seed (e.g. genuinely-new future tools).
# Conservative by design — never "uncataloged".
_FALLBACK_RISK_CLASS = "mutating"
_FALLBACK_AUTH_EXPECTATION = "authenticated"


def _emit(event: dict[str, Any]) -> None:
    """Emit one grep-friendly JSON log line for MCP observability."""
    logger.info("mcp_tool_event %s", json.dumps(event, sort_keys=True, separators=(",", ":")))


def _extract_error_class(result: Any) -> str | None:
    """Try to classify failure from MCP tool return payload.

    Tools return JSON strings; when they carry MCP error envelopes we can read
    `error.error_class` without logging payload content.
    """
    if isinstance(result, dict):
        payload = result
    elif isinstance(result, str):
        try:
            payload = json.loads(result)
        except Exception:
            return None
    else:
        return None

    if not isinstance(payload, dict):
        return None
    if payload.get("ok") is not False:
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    error_class = error.get("error_class")
    return str(error_class) if error_class else None


def instrument_tool(
    module_name: str,
    fn: Callable[..., Awaitable[Any]],
    risk_class: str | None = None,
    auth_expectation: str | None = None,
) -> Callable[..., Awaitable[Any]]:
    """Wrap one MCP tool with structured invocation logs.

    ``risk_class`` and ``auth_expectation`` should be passed from the proxy's
    stamped _meta (resolved at registration time) so that observability derives
    from the single _meta source rather than re-reading _CATALOG_BY_TOOL.
    When called directly (e.g. from tests without the proxy) they fall back to
    the catalog seed or the conservative fail-safe.
    """
    tool_name = fn.__name__

    # Fall back to catalog seed when called without pre-resolved values.
    if risk_class is None or auth_expectation is None:
        catalog_entry = _CATALOG_BY_TOOL.get(tool_name)
        if risk_class is None:
            risk_class = catalog_entry.get("risk_class") if catalog_entry else _FALLBACK_RISK_CLASS
        if auth_expectation is None:
            auth_expectation = catalog_entry.get("auth_expectation") if catalog_entry else _FALLBACK_AUTH_EXPECTATION

    # Capture backend method/path for start-event from catalog seed (informational only).
    catalog_entry = _CATALOG_BY_TOOL.get(tool_name)
    backend_method = catalog_entry.get("http_method") if catalog_entry else None
    backend_path = catalog_entry.get("backend_path_template") if catalog_entry else None

    @wraps(fn)
    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        invocation_id = uuid.uuid4().hex[:12]
        start = time.perf_counter()

        _emit(
            {
                "event": "tool_invocation_start",
                "invocation_id": invocation_id,
                "tool_name": tool_name,
                "module": module_name,
                "risk_class": risk_class,
                "auth_expectation": auth_expectation,
                "backend_method": backend_method,
                "backend_path": backend_path,
            }
        )

        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            _emit(
                {
                    "event": "tool_invocation_result",
                    "invocation_id": invocation_id,
                    "tool_name": tool_name,
                    "module": module_name,
                    "success": False,
                    "duration_ms": duration_ms,
                    "error_class": "handler_exception",
                    "error_type": type(exc).__name__,
                }
            )
            raise

        duration_ms = int((time.perf_counter() - start) * 1000)
        error_class = _extract_error_class(result)
        _emit(
            {
                "event": "tool_invocation_result",
                "invocation_id": invocation_id,
                "tool_name": tool_name,
                "module": module_name,
                "success": error_class is None,
                "duration_ms": duration_ms,
                "error_class": error_class,
            }
        )
        return result

    return _wrapped


class ObservabilityMCPProxy:
    """Thin FastMCP proxy that auto-instruments tool decorators per module."""

    def __init__(self, mcp: _MCPToolRegistrar, module_name: str) -> None:
        self._mcp = mcp
        self._module_name = module_name

    def tool(self, *args: Any, **kwargs: Any):
        meta = dict(kwargs.get("meta") or {})
        meta.setdefault("module", self._module_name)

        # Stamp risk_class and auth_expectation from catalog seed at registration
        # time so every tool carries governance metadata in _meta (single source).
        # setdefault means an explicit meta= passed by a caller wins over the seed.
        # For tools with no catalog entry the conservative fail-safe is applied —
        # never "uncataloged".
        def _decorate(fn: Callable[..., Awaitable[Any]]):
            entry = _CATALOG_BY_TOOL.get(fn.__name__)
            risk_class: str = meta.get(
                "risk_class",
                entry["risk_class"] if entry else _FALLBACK_RISK_CLASS,
            )
            auth_expectation: str = meta.get(
                "auth_expectation",
                entry["auth_expectation"] if entry else _FALLBACK_AUTH_EXPECTATION,
            )
            meta.setdefault("risk_class", risk_class)
            meta.setdefault("auth_expectation", auth_expectation)

            # Re-bind meta in kwargs so the inner mcp.tool() call carries all keys.
            kwargs["meta"] = meta
            decorator = self._mcp.tool(*args, **kwargs)
            return decorator(
                instrument_tool(self._module_name, fn, risk_class=risk_class, auth_expectation=auth_expectation)
            )

        return _decorate
