"""In-process circuit breaker + categorization helpers.

A small, dependency-free breaker keyed by (target_type, target_id). Lives in
process and starts CLOSED on every backend boot — fail-open on restart so a
phantom OPEN never persists past a fix (resilience4j default + Envoy
guidance). Cross-process visibility comes from the registry's Redis state
cache, not from the breaker itself.

Design notes
------------
- Only network/timeout/slow/target failures count toward opening. Auth and
  authz failures do NOT — otherwise a wrong token leaves the breaker stuck
  open forever (botocore + kubectl precedent).
- Half-open allows exactly one trial call. If it succeeds, breaker closes
  and the consecutive-failure counter resets. If it fails, breaker reopens
  for another sleep window.
- Thread/asyncio safe via a per-key asyncio.Lock; sync callers use it via
  the wrapper's own loop. Failures from passive observation flow through
  the registry's record_real_call() so the breaker never lies about state.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar, cast

from services.reachability.probe import ErrorCategory

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _Breaker:
    """State for one (target_type, target_id) breaker."""

    failure_threshold: int = 5
    sleep_window_seconds: int = 30
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    half_open_inflight: bool = False
    last_success_at: float | None = None
    target_name: str | None = None  # populated by registry for friendlier errors
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def can_attempt(self) -> bool:
        """Return True if a call should be allowed through right now.

        Caller is responsible for invoking ``record_success`` / ``record_failure``
        after the attempt completes (or for setting ``half_open_inflight``).
        """
        if self.state is BreakerState.CLOSED:
            return True
        if self.state is BreakerState.OPEN:
            if time.monotonic() - self.opened_at >= self.sleep_window_seconds:
                # Transition to half-open; let exactly one probe through.
                self.state = BreakerState.HALF_OPEN
                self.half_open_inflight = False
            else:
                return False
        if self.state is BreakerState.HALF_OPEN:
            if self.half_open_inflight:
                return False
            self.half_open_inflight = True
            return True
        return False

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_success_at = time.monotonic()
        self.half_open_inflight = False
        if self.state is not BreakerState.CLOSED:
            logger.info(
                "Breaker closed (target_name=%s) after success",
                self.target_name,
            )
        self.state = BreakerState.CLOSED

    def record_failure(self, category: ErrorCategory | None) -> None:
        # Auth failures must NOT count — otherwise breaker stays open on bad token.
        if category in (ErrorCategory.AUTH, ErrorCategory.AUTHZ):
            return
        self.consecutive_failures += 1
        self.half_open_inflight = False
        if (
            self.state is BreakerState.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        ):
            if self.state is not BreakerState.OPEN:
                logger.warning(
                    "Breaker opened (target_name=%s, consecutive_failures=%d, last_category=%s)",
                    self.target_name,
                    self.consecutive_failures,
                    category.value if category else "unknown",
                )
            self.state = BreakerState.OPEN
            self.opened_at = time.monotonic()


def categorize_exception(exc: BaseException) -> ErrorCategory:
    """Best-effort classification of an arbitrary exception.

    Centralized so call-site decorators and probe implementations agree on
    what counts as a network failure vs. an auth failure. Lazy imports keep
    this importable without optional deps installed (kubernetes, requests).
    """
    # Built-in timeouts first (works regardless of optional deps).
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return ErrorCategory.TIMEOUT

    # urllib3 / requests connection errors.
    try:
        import urllib3.exceptions as _u3
        if isinstance(exc, _u3.MaxRetryError | _u3.NewConnectionError | _u3.ConnectTimeoutError):
            return ErrorCategory.NETWORK
        if isinstance(exc, _u3.ReadTimeoutError):
            return ErrorCategory.TIMEOUT
    except ImportError:
        pass

    try:
        from requests import exceptions as _rq
        if isinstance(exc, _rq.ConnectTimeout | _rq.ReadTimeout | _rq.Timeout):
            return ErrorCategory.TIMEOUT
        if isinstance(exc, _rq.ConnectionError):
            return ErrorCategory.NETWORK
    except ImportError:
        pass

    # Kubernetes ApiException carries an HTTP status — split auth vs target.
    try:
        from kubernetes.client.exceptions import ApiException as _K8sApi
        if isinstance(exc, _K8sApi):
            status = getattr(exc, "status", None)
            if status == 401:
                return ErrorCategory.AUTH
            if status == 403:
                return ErrorCategory.AUTHZ
            if status and status >= 500:
                return ErrorCategory.TARGET
            return ErrorCategory.TARGET
    except ImportError:
        pass

    # Plain socket / OS-level errors.
    if isinstance(exc, ConnectionError | OSError):
        return ErrorCategory.NETWORK

    return ErrorCategory.TARGET


def with_breaker(target_type: str, *, target_id_arg: str = "cluster_id") -> Callable[[F], F]:
    """Gate a function on the breaker for ``(target_type, target_id)``.

    - If breaker is OPEN, raise ``BreakerOpenError`` immediately (no remote call).
    - If the call succeeds, record success (closes breaker if it was half-open).
    - If the call raises, classify the exception and feed it to the breaker.
      Auth failures do not count.

    The decorator dispatches sync/async wrappers based on the wrapped callable.

    The ``target_id`` is read from the named argument ``target_id_arg``; supports
    both keyword and positional invocation.
    """
    # Local import to avoid circular: registry imports breaker too.
    from core.errors import BreakerOpenError
    from services.reachability.registry import registry as _registry

    def decorator(fn: F) -> F:
        sig = inspect.signature(fn)
        param_names = list(sig.parameters.keys())

        def _resolve_target_id(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int | None:
            if target_id_arg in kwargs:
                v = kwargs[target_id_arg]
            else:
                try:
                    idx = param_names.index(target_id_arg)
                except ValueError:
                    return None
                v = args[idx] if idx < len(args) else None
            try:
                return int(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        def _build_open_error(target_id: int) -> BreakerOpenError:
            reg = _registry
            target_name = reg.get_target_name(target_type, target_id) or f"{target_type}:{target_id}"
            last = reg.get_last_success_iso(target_type, target_id)
            return BreakerOpenError(
                target_type=target_type,
                target_id=target_id,
                target_name=target_name,
                suggested_action=(
                    f"{target_type.title()} '{target_name}' was unreachable on recent "
                    f"attempts; not retrying yet. Use the retry button to force a probe."
                ),
                last_success_at=last,
            )

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                target_id = _resolve_target_id(args, kwargs)
                reg = _registry
                if target_id is None:
                    return await fn(*args, **kwargs)
                if not reg.allow_call(target_type, target_id):
                    raise _build_open_error(target_id)
                start = time.monotonic()
                try:
                    result = await fn(*args, **kwargs)
                except BaseException as exc:
                    latency_ms = (time.monotonic() - start) * 1000
                    category = categorize_exception(exc)
                    reg.record_real_call(
                        target_type, target_id,
                        success=False,
                        error_category=category,
                        latency_ms=latency_ms,
                    )
                    raise
                latency_ms = (time.monotonic() - start) * 1000
                reg.record_real_call(
                    target_type, target_id,
                    success=True,
                    latency_ms=latency_ms,
                )
                return result
            return cast(F, async_wrapper)
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                target_id = _resolve_target_id(args, kwargs)
                reg = _registry
                if target_id is None:
                    return fn(*args, **kwargs)
                if not reg.allow_call(target_type, target_id):
                    raise _build_open_error(target_id)
                start = time.monotonic()
                try:
                    result = fn(*args, **kwargs)
                except BaseException as exc:
                    latency_ms = (time.monotonic() - start) * 1000
                    category = categorize_exception(exc)
                    reg.record_real_call(
                        target_type, target_id,
                        success=False,
                        error_category=category,
                        latency_ms=latency_ms,
                    )
                    raise
                latency_ms = (time.monotonic() - start) * 1000
                reg.record_real_call(
                    target_type, target_id,
                    success=True,
                    latency_ms=latency_ms,
                )
                return result
            return cast(F, sync_wrapper)

    return decorator
