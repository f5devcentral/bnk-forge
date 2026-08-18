"""Canonical resolution of a ModuleLibrary row from a bare `path`.

D-033 defines module identity as ``(module_source_id, path, version)``, but
several surfaces resolve on ``path`` alone -- blueprint pins carry no source, so
they have nothing else to go on. When two sources catalog the same path (a fork
of bnkctl-index registered alongside the original, say), those surfaces have to
break the tie, and they did it *differently*:

    deploy  (stack_deployment_service)  is_latest DESC, last_synced DESC, id DESC
    policy  (project_secrets)           is_latest ASC,  id ASC  -> last wins
    stacks  (stack_service)             is_latest ASC,  id ASC  -> last wins

The map builds omit ``last_synced`` entirely, so with two sources the
secret-policy check and the deploy could pick *different* modules for the same
path -- disagreeing about which schema counts. That is the silent drift D-033
exists to kill (#90 F8).

The ordering lives here once, in both directions, so a `first()` query and a
last-wins map build cannot diverge again. `_MAP_ORDER` is the exact reverse of
`_ROW_ORDER`: iterating in that order leaves the winner last, which is the row
`resolve_module_row` would have returned.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from models import ModuleLibrary

logger = logging.getLogger(__name__)


def _row_order() -> list:
    """Winner-first ordering: use with .first()."""
    return [
        ModuleLibrary.is_latest.desc(),
        ModuleLibrary.last_synced.desc().nullslast(),
        ModuleLibrary.id.desc(),
    ]


def _map_order() -> list:
    """Winner-LAST ordering: use when building a {path: row} map by last-wins.

    Must stay the exact reverse of _row_order(); nullslast on DESC becomes
    nullsfirst on ASC.
    """
    return [
        ModuleLibrary.is_latest.asc(),
        ModuleLibrary.last_synced.asc().nullsfirst(),
        ModuleLibrary.id.asc(),
    ]


def warn_on_cross_source_ambiguity(rows: list[ModuleLibrary], path: str) -> None:
    """Log when one path is claimed by more than one module source.

    Blueprint pins carry no source, so this cannot be resolved automatically --
    but it must not stay silent either. Which source wins is decided by sync
    recency and row id, neither of which the blueprint author controls or can
    see, so an operator needs to be told the binding was a coin toss they did
    not know they were flipping.
    """
    source_ids = {r.module_source_id for r in rows if r.module_source_id is not None}
    if len(source_ids) > 1:
        logger.warning(
            "Cross-source module ambiguity for path %r: matched by %d module sources "
            "(ids %s). Blueprint pins carry no source, so the binding is decided by "
            "is_latest/last_synced/id. Deactivate the duplicate source, or make the "
            "paths distinct, to make this deterministic.",
            path,
            len(source_ids),
            sorted(source_ids),
        )


def resolve_module_row(
    db: Session, module_path: str, *, warn_ambiguous: bool = True
) -> ModuleLibrary | None:
    """Resolve one active ModuleLibrary row for a bare path, canonically."""
    query = db.query(ModuleLibrary).filter(
        ModuleLibrary.path == module_path,
        ModuleLibrary.is_active,
    )
    if warn_ambiguous:
        rows = query.order_by(*_row_order()).all()
        if not rows:
            return None
        warn_on_cross_source_ambiguity(rows, module_path)
        return rows[0]
    return query.order_by(*_row_order()).first()


def resolve_module_rows_by_path(
    db: Session, module_paths: list[str], *, warn_ambiguous: bool = True
) -> dict[str, ModuleLibrary]:
    """Batch-resolve paths to rows, agreeing with resolve_module_row row for row.

    Built last-wins over _map_order() rather than per-path queries, to keep the
    single round trip these call sites rely on to avoid N+1.
    """
    if not module_paths:
        return {}

    rows = (
        db.query(ModuleLibrary)
        .filter(
            ModuleLibrary.path.in_(module_paths),
            ModuleLibrary.is_active,
        )
        .order_by(*_map_order())
        .all()
    )

    if warn_ambiguous:
        by_path: dict[str, list[ModuleLibrary]] = {}
        for row in rows:
            if isinstance(row.path, str):
                by_path.setdefault(row.path, []).append(row)
        for path, path_rows in by_path.items():
            warn_on_cross_source_ambiguity(path_rows, path)

    return {row.path: row for row in rows if isinstance(row.path, str)}
