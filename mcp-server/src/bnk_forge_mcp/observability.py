"""MCP tool invocation observability helpers.

Bounded Slice 4 scope:
- structured invocation start/result logs
- catalog-enriched metadata when available
- no argument/payload logging to avoid secret leakage
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, Protocol

from .tool_catalog import ToolRiskClass, get_high_risk_tool_catalog

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


# ── Destructive-tool confirmation gate ───────────────────────────────────────
#
# risk_class used to be catalog metadata only: it was logged for telemetry but
# never checked, so the 16 `destructive` tools forwarded straight to the backend
# DELETE/POST and relied entirely on backend RBAC. Because the `mcp` service
# account is role=admin, an autonomous agent could delete real projects and
# clusters with a single tool call and no second factor (#65).
#
# The gate is applied here, at the one place every tool is registered, so it
# derives from the catalog rather than from 16 hand-edited call sites that can
# drift. Any future tool marked destructive is gated automatically.
_CONFIRM_ENV_VAR = "BNK_FORGE_MCP_REQUIRE_CONFIRMATION"

_CONFIRM_GUIDANCE = (
    "\n\nSAFETY: this tool is classified `destructive` and will not execute "
    "without an explicit `confirm=True`. Calling it without confirmation "
    "returns a refusal and changes nothing. Only set confirm=True when the "
    "destruction is intended for this specific target."
)


def _confirmation_required() -> bool:
    """Whether the destructive-tool gate is active.

    Default on. The escape hatch exists for trusted non-interactive teardown
    (CI tearing down its own fixtures); it is read per-call so it can be flipped
    without re-registering tools. Mirrors BENCHMARK_AGENT_AUTH_REQUIRED.
    """
    return os.getenv(_CONFIRM_ENV_VAR, "true").strip().lower() not in ("0", "false", "no")


def _refusal_payload(tool_name: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "error": {
                "error_class": "confirmation_required",
                "code": "CONFIRMATION_REQUIRED",
                "message": (
                    f"'{tool_name}' is a destructive operation and was called without "
                    "confirmation. Nothing was changed."
                ),
                "retryable": False,
                "next_action": (
                    "Verify this is the intended target, then re-invoke with "
                    "confirm=true. Do not retry automatically -- confirmation is "
                    "meant to represent deliberate intent, not a retry step."
                ),
            },
        },
        indent=2,
    )


def require_confirmation(
    fn: Callable[..., Awaitable[Any]], tool_name: str
) -> Callable[..., Awaitable[Any]]:
    """Wrap a destructive tool so it refuses unless confirm=True is passed.

    `confirm` is added to the wrapper's public signature rather than pulled out
    of **kwargs, so it appears in the MCP tool schema. An agent can only satisfy
    a gate it can see.
    """
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())
    confirm_param = inspect.Parameter(
        "confirm",
        inspect.Parameter.KEYWORD_ONLY,
        default=False,
        annotation=bool,
    )
    # Keyword-only must precede **kwargs if the wrapped tool declares one.
    var_kw = [p for p in params if p.kind is inspect.Parameter.VAR_KEYWORD]
    if var_kw:
        idx = params.index(var_kw[0])
        new_params = params[:idx] + [confirm_param] + params[idx:]
    else:
        new_params = params + [confirm_param]

    @wraps(fn)
    async def _gated(*args: Any, confirm: bool = False, **kwargs: Any) -> Any:
        if not confirm and _confirmation_required():
            _emit(
                {
                    "event": "tool_invocation_blocked",
                    "tool_name": tool_name,
                    "risk_class": "destructive",
                    "reason": "confirmation_required",
                }
            )
            return _refusal_payload(tool_name)
        return await fn(*args, **kwargs)

    _gated.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]
    _gated.__doc__ = (fn.__doc__ or "") + _CONFIRM_GUIDANCE
    return _gated


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

            # Enforce risk_class at runtime, not just in telemetry (#65). Gate
            # first, instrument outside, so a blocked call still emits a normal
            # start/result pair and stays visible in the audit trail.
            handler = fn
            if risk_class == ToolRiskClass.DESTRUCTIVE.value:
                handler = require_confirmation(fn, fn.__name__)

            return decorator(
                instrument_tool(
                    self._module_name,
                    handler,
                    risk_class=risk_class,
                    auth_expectation=auth_expectation,
                )
            )

        return _decorate
