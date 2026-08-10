"""The role in force for the current request.

An API token may grant LESS than its owner's account role: an admin can issue a
``--role operator`` (or ``viewer``) token for a CI pipeline, and that token must
be treated as an operator/viewer for the whole request — including the
admin-bypass checks that widen scope (all-projects visibility, cross-project
writes), not just the role gate on the route.

``request_role`` is stamped on the authenticated principal by the API-token path
in ``get_current_user`` (an unmapped attribute — never persisted). The
interactive JWT flow never downscopes, so it falls back to the account role.

This lives in ``core`` rather than ``routes.auth`` so that services can reach it
without importing routes (which would be a circular import).

EVERY authorization decision must read this, never ``user.role`` directly —
otherwise the downscope silently does not apply and the token grants more than
it says it does.
"""

from typing import Protocol


class HasRole(Protocol):
    """Anything with an account role — in practice models.User."""

    role: str


def effective_role(user: HasRole) -> str:
    """The role in force for this request: the token's role if downscoped, else the account's."""
    return getattr(user, "request_role", None) or user.role
