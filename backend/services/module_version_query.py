"""Shared ModuleLibrary version queries (D-033).

Small, import-cycle-free home for the version-axis queries used by module sync,
blueprint resolution, and the project-module re-pin action. Both
ImportedBlueprintService and ProjectModuleService need these and import each
other's neighbors, so the helpers live in their own leaf module.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from models import ModuleLibrary
from utils.catalog_versioning import version_sort_key


def available_module_versions(
    db: Session, path: str, module_source_id: int | None = None
) -> list[str]:
    """Active catalog versions for a module path, latest first.

    When ``module_source_id`` is given the listing is scoped to that source —
    the correct scope for re-pinning an existing project module, where jumping
    sources silently would change git_source/digests underneath the operator.
    """
    query = db.query(ModuleLibrary.version).filter(
        ModuleLibrary.path == path, ModuleLibrary.is_active
    )
    if module_source_id is not None:
        query = query.filter(ModuleLibrary.module_source_id == module_source_id)
    rows = query.order_by(ModuleLibrary.is_latest.desc(), ModuleLibrary.id.desc()).all()
    return [row[0] for row in rows if row[0]]


def recompute_is_latest(db: Session, source_id: int | None, path: str) -> bool:
    """Recompute the single is_latest row for (module_source_id, path).

    Only ACTIVE rows compete for latest — an inactive newest version must not
    hold the flag while active older versions read False (that combination
    hides the module from the default catalog view entirely). Inactive rows are
    explicitly flagged False. Flushes but does NOT commit; the caller owns the
    transaction. Returns True when any flag changed.
    """
    rows = (
        db.query(ModuleLibrary)
        .filter(ModuleLibrary.module_source_id == source_id, ModuleLibrary.path == path)
        .all()
    )
    if not rows:
        return False
    active = [r for r in rows if r.is_active]
    latest_id = (
        max(active, key=lambda r: (version_sort_key(r.version), r.id)).id if active else None
    )
    changed = False
    for row in rows:
        desired = row.id == latest_id
        if bool(row.is_latest) != desired:
            row.is_latest = desired
            changed = True
    if changed:
        db.flush()
    return changed
