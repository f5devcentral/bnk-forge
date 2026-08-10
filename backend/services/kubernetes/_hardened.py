"""Hardened wrappers around kubernetes-client construction + calls.

Phase-1 surface: a single helper that runs an arbitrary kubernetes-client
call with a bounded timeout and translates exceptions into the categorized
errors used by the reachability subsystem (``services.reachability``).

Existing call sites continue to work unchanged — this is a scaffolding
module new call sites can adopt without rewriting the rest of the
service. Phase 2 migrates remaining services.
"""
from __future__ import annotations

import logging
from typing import Any

from kubernetes.client.exceptions import ApiException

from core.errors import AuthenticationError, NetworkUnreachableError
from services.reachability import ErrorCategory, categorize_exception

logger = logging.getLogger(__name__)

# Standard wire-bound timeouts: (connect_seconds, read_seconds).
# Used everywhere we don't have a domain-specific reason to differ.
DEFAULT_REQUEST_TIMEOUT: tuple[int, int] = (3, 8)


def call_with_timeout(fn: Any, *args: Any, timeout: tuple[int, int] | int | None = None, **kwargs: Any) -> Any:
    """Invoke a kubernetes-client method with an enforced ``_request_timeout``.

    ``_request_timeout`` is the only bound that prevents a stuck connection
    from blocking for ~75s on the OS TCP timeout. Caller can override by
    passing ``timeout=(c, r)`` or accept ``DEFAULT_REQUEST_TIMEOUT``.
    """
    if "_request_timeout" not in kwargs:
        kwargs["_request_timeout"] = timeout if timeout is not None else DEFAULT_REQUEST_TIMEOUT
    return fn(*args, **kwargs)


def translate_k8s_exception(
    exc: BaseException,
    *,
    target_id: int,
    target_name: str,
) -> Exception:
    """Map a kubernetes-client exception into a categorized AppError.

    Auth/authz errors become ``AuthenticationError`` (401, doesn't trip
    breaker); network/timeout/target errors become
    ``NetworkUnreachableError`` (503, breaker counts it).
    """
    category = categorize_exception(exc)
    if category in (ErrorCategory.AUTH, ErrorCategory.AUTHZ):
        suggested = (
            f"Authentication to cluster '{target_name}' failed. "
            f"Refresh the kubeconfig or re-authenticate the cloud credential."
        )
        if isinstance(exc, ApiException) and exc.status == 403:
            suggested = (
                f"Forge's credential for '{target_name}' lacks permission for this "
                f"operation. Grant the appropriate RBAC role to the kubeconfig user."
            )
        return AuthenticationError(
            target_type="cluster",
            target_id=target_id,
            target_name=target_name,
            suggested_action=suggested,
            details={"underlying_error": str(exc)},
        )
    if category is ErrorCategory.TIMEOUT:
        suggested = (
            f"Request to cluster '{target_name}' timed out. "
            f"Check the API server is reachable from forge."
        )
    elif category is ErrorCategory.NETWORK:
        suggested = (
            f"Cannot reach cluster '{target_name}'. "
            f"Check VPN connection or whether the API server is online."
        )
    else:
        suggested = (
            f"Cluster '{target_name}' returned an error. See logs for details."
        )
    return NetworkUnreachableError(
        target_type="cluster",
        target_id=target_id,
        target_name=target_name,
        suggested_action=suggested,
        details={"underlying_error": str(exc), "category": category.value},
    )
