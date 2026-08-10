"""Fleet RBAC helpers — D-022 Phase 4.

Provides require_fleet_mutation() which gates bulk-op / policy-enforce /
label-assign mutations by composing the EXISTING per-project _check_ownership
over ALL distinct project_ids in the caller's blast radius.

Design (from DECOMPOSITION.md §3):
- No new role enum — composes the existing admin/operator/viewer model.
- Admin users bypass all checks (delegated to _check_ownership).
- None project_id → skip (legacy/global membership, matches _check_ownership
  behaviour for null-owner projects).
- Cross-project blast radius: iterate ALL distinct member project_ids (a
  Decision's resolved members can span projects; decision.project_id is
  denormalized/may be null).
"""

import logging
from collections.abc import Iterable

from sqlalchemy.orm import Session

from models import User
from models.project import Project

logger = logging.getLogger(__name__)


def require_fleet_mutation(
    project_ids: Iterable[int | None],
    user: User,
    db: Session,
) -> None:
    """Raise ForbiddenError if the user may not mutate ANY of the given projects.

    Loops over the distinct non-None project_ids and calls _check_ownership for
    each one. The first project that rejects the caller raises ForbiddenError —
    fail-closed, no partial side effects.

    None project_ids are skipped (legacy/global members have no owner scope).
    An empty iterable (or all-None) is allowed (no restriction).
    """
    from routes.auth import _check_ownership

    seen: set[int] = set()
    for pid in project_ids:
        if pid is None:
            continue
        if pid in seen:
            continue
        seen.add(pid)

        project = db.get(Project, pid)
        if project is None:
            # Project row gone (orphan membership) — skip, same as null-owner.
            logger.debug(
                "require_fleet_mutation: project_id=%s not found, skipping ownership check",
                pid,
            )
            continue

        # Delegates to existing logic: admin bypass + owner-or-403.
        _check_ownership(user, project)


def resolve_rollout_defaults(
    db: Session,
    project_ids: Iterable[int | None],
) -> tuple[int, str]:
    """Return (concurrency, gate_mode) for a bulk-op spanning the given projects.

    Resolution order:
    1. If all non-None projects agree on a value → use it.
    2. If multiple non-None projects have CONFLICTING values → fall back to
       system literal (concurrency=5, gate_mode=GATE_MODE_MANUAL).
    3. NULL project_id or project not found → treat as "no preference".

    This keeps the fallback deterministic and avoids silently favouring one
    project over another in cross-project decisions.
    """
    from models.fleet_targeting import GATE_MODE_MANUAL

    _SYSTEM_CONCURRENCY = 5
    _SYSTEM_GATE_MODE = GATE_MODE_MANUAL

    concurrencies: set[int] = set()
    gate_modes: set[str] = set()

    seen: set[int] = set()
    for pid in project_ids:
        if pid is None:
            continue
        if pid in seen:
            continue
        seen.add(pid)

        project = db.get(Project, pid)
        if project is None:
            continue

        if project.fleet_default_concurrency is not None:
            concurrencies.add(project.fleet_default_concurrency)
        if project.fleet_default_gate_mode is not None:
            gate_modes.add(project.fleet_default_gate_mode)

    # Conflicting values → fall back to system literal (deterministic).
    concurrency = next(iter(concurrencies)) if len(concurrencies) == 1 else _SYSTEM_CONCURRENCY
    gate_mode = next(iter(gate_modes)) if len(gate_modes) == 1 else _SYSTEM_GATE_MODE

    return concurrency, gate_mode
