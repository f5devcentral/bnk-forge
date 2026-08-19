"""Prune superseded catalog versions (D-033).

D-033 gives the catalog one immutable row per ``(source, path, version)``: an
edit never overwrites, it adds. That is the right call — a project pinned to a
version must keep resolving to the bytes it was deployed from — but nothing ever
removes the rows it leaves behind. A source under active development accumulates
every version it has ever had, and the only way back is to delete the source and
re-register it, which throws away its configuration and every release with it.

So there are two operations here, and the distinction matters:

  deactivate  clears ``is_active``. The version stops appearing in the catalog
              and stops competing for ``is_latest``, but the row survives and a
              project pinned to it still resolves. Reversible, and safe on any
              version. This is the default.

  delete      removes the row. Only ever applied to versions nothing references,
              because a project module's ``module_library_id`` is NOT NULL — the
              delete would either fail on the constraint or, worse, be made to
              succeed by cascading and take the project's module with it.

Neither ever touches the newest ``keep`` versions of a path, so the thing an
operator would deploy next is never the thing that disappears.

And neither touches a version something is deployed from. The reference check
runs on every candidate, not only when deleting: a version a project module
pins, or a release a stack was built from, is reported ``in_use`` and left
exactly as it is. Hiding the version a running deployment is on would make the
catalog lie about what is deployed, and it is not what "retire the old versions"
was ever meant to mean. ``include_in_use`` opts into deactivating them anyway;
nothing opts into deleting them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from core.errors import InternalError, NotFoundError
from models import BlueprintRelease, ModuleLibrary, ModuleSource, ProjectModule
from models.blueprint_catalog import BlueprintSource
from models.stack import StackInstance
from services.module_version_query import recompute_is_latest
from utils.catalog_versioning import version_sort_key

logger = logging.getLogger(__name__)


@dataclass
class PruneItem:
    identity: str          # module path, or blueprint id
    version: str
    action: str            # kept | deactivated | deleted | in_use
    reason: str = ""


@dataclass
class PruneResult:
    source_id: int
    dry_run: bool
    keep: int
    items: list[PruneItem] = field(default_factory=list)

    def as_dict(self) -> dict:
        counts: dict[str, int] = {}
        for i in self.items:
            counts[i.action] = counts.get(i.action, 0) + 1
        return {
            "source_id": self.source_id,
            "dry_run": self.dry_run,
            "keep": self.keep,
            "counts": counts,
            "items": [
                {"identity": i.identity, "version": i.version, "action": i.action, "reason": i.reason}
                for i in self.items
            ],
        }


def _ordered(rows: list, version_of) -> list:
    """Newest first. Ties break on row id so an unparseable version is stable.

    Note what that means for a source not versioned by semver: ``version_sort_key``
    returns an empty key for anything it cannot parse, so every row ties and the
    order falls entirely to ``r.id`` — "keep the newest" quietly becomes "keep the
    most recently synced". Consistent with ``recompute_is_latest``, so not a
    divergence, but worth knowing on an operation that deletes.
    """
    return sorted(rows, key=lambda r: (version_sort_key(version_of(r)), r.id), reverse=True)


def _spare_last_active(kept: list, candidates: list, refs_by_id: dict[int, int],
                       include_in_use: bool):
    """The newest active row to spare when pruning would otherwise leave none.

    A prune must never leave a group with zero active versions. The module
    disappears from the default catalog view, ``is_latest`` lands on nothing,
    and there is no un-prune endpoint to walk it back.

    This is reachable without anyone deactivating anything by hand.
    ``module_sync_service`` clears ``is_active`` on any manifest-backed row
    whose pack path stops appearing upstream — a rename is enough. That row
    still sorts newest, so it occupies the kept slot while the last ACTIVE
    version is deactivated underneath it. Sync already holds this invariant on
    its own side, handing ``is_latest`` to "the newest remaining ACTIVE
    version so the path doesn't vanish"; prune has to hold it too.

    Returns the row to spare, or ``None`` when something already survives.
    A kept row survives if it is active; a candidate survives if it is active
    and left untouched for being in use.
    """
    survives = [r for r in kept if r.is_active]
    survives += [
        r for r in candidates
        if r.is_active and refs_by_id.get(r.id) and not include_in_use
    ]
    if survives:
        return None
    return next((r for r in candidates if r.is_active), None)


_SPARED_REASON = (
    "last active version for this group; pruning it would remove the entry from "
    "the catalog entirely"
)


def _assert_no_project_modules(db: Session, row: ModuleLibrary) -> None:
    """Refuse to delete a version any project module still points at.

    The candidate loop already checks this, so reaching here with references
    means the loop was wrong. That is worth a second query because the database
    will NOT catch the mistake: ``ModuleLibrary.project_modules`` is declared
    ``cascade="all, delete-orphan"``, so SQLAlchemy deletes the project's
    modules first and the NOT NULL on ``module_library_id`` never gets a chance
    to fire. Verified against the real ORM — deleting a referenced
    ModuleLibrary takes the ProjectModule row with it, silently. So the guard in
    the loop is not backed by a constraint; it is the only thing standing
    between ``delete=True`` and destroying a project's modules, and a wrong
    answer costs data rather than raising.
    """
    refs = db.query(ProjectModule).filter(ProjectModule.module_library_id == row.id).count()
    if refs:
        raise InternalError(
            f"Refusing to delete module version {row.path}@{row.version}: "
            f"{refs} project module(s) reference it. Deleting would cascade and "
            f"remove them."
        )


def _assert_no_stack_instances(db: Session, row: BlueprintRelease) -> None:
    """Refuse to delete a release any stack instance was built from.

    Same reasoning, opposite database behaviour and the same outcome: that FK is
    ``ON DELETE SET NULL``, so the delete succeeds quietly and strips the stack
    of the record of what it was deployed from. Nothing raises either way, so
    the check has to.
    """
    refs = db.query(StackInstance).filter(StackInstance.blueprint_release_id == row.id).count()
    if refs:
        raise InternalError(
            f"Refusing to delete blueprint release {row.blueprint_id}@{row.blueprint_version}: "
            f"{refs} stack instance(s) were deployed from it. Deleting would null "
            f"their provenance."
        )


def prune_module_source(
    db: Session,
    source_id: int,
    *,
    keep: int = 1,
    delete: bool = False,
    dry_run: bool = False,
    include_in_use: bool = False,
) -> PruneResult:
    """Retire superseded module versions for one source.

    Grouped by ``path``: each module keeps its newest ``keep`` versions and every
    older one is deactivated, or deleted when ``delete`` is set and no project
    module points at it. ``is_latest`` is recomputed per path afterwards —
    without that a path whose newest row was just deactivated would have no
    latest at all, and the module would vanish from the default catalog view
    rather than fall back to the newest surviving version.
    """
    if not db.query(ModuleSource).filter(ModuleSource.id == source_id).first():
        # Otherwise a typo'd id reports a successful no-op prune, which reads as
        # "there was nothing to retire" rather than "that source does not exist".
        raise NotFoundError("module_source", source_id)

    result = PruneResult(source_id=source_id, dry_run=dry_run, keep=keep)

    rows = db.query(ModuleLibrary).filter(ModuleLibrary.module_source_id == source_id).all()
    by_path: dict[str, list[ModuleLibrary]] = {}
    for r in rows:
        by_path.setdefault(r.path or "", []).append(r)

    touched_paths: set[str] = set()
    for path, versions in by_path.items():
        ordered = _ordered(versions, lambda r: r.version)
        kept, candidates = ordered[:keep], ordered[keep:]
        # Checked for every candidate, whatever the mode. A version in use is
        # never deleted and, by default, not even hidden. Counted once up front
        # because the spare-the-last-active decision needs the same answer.
        refs_by_id = {
            r.id: db.query(ProjectModule)
            .filter(ProjectModule.module_library_id == r.id)
            .count()
            for r in candidates
        }
        spared = _spare_last_active(kept, candidates, refs_by_id, include_in_use)

        for row in kept:
            result.items.append(PruneItem(path, row.version or "", "kept"))
        for row in candidates:
            refs = refs_by_id[row.id]
            if spared is not None and row.id == spared.id:
                result.items.append(
                    PruneItem(path, row.version or "", "kept", _SPARED_REASON)
                )
                # Touched even though nothing was deactivated. Reaching here means
                # the newest row is inactive while this one is active, so the flag
                # is on the wrong row — the exact combination recompute_is_latest
                # exists to resolve ("an inactive newest version must not hold the
                # flag while active older versions read False"). Declining to
                # deactivate anything and leaving the flags correct are different
                # claims; without this only the first one would be true.
                touched_paths.add(path)
                continue
            if refs and not include_in_use:
                result.items.append(
                    PruneItem(path, row.version or "", "in_use",
                              f"{refs} project module(s) pinned to it; left untouched")
                )
                continue
            if refs:
                # include_in_use: hide it, never remove it. module_library_id is
                # NOT NULL, so deleting would either fail on the constraint or
                # take the project's module with it.
                result.items.append(
                    PruneItem(path, row.version or "", "deactivated",
                              f"{refs} project module(s) pinned to it; hidden, not deleted")
                )
                if not dry_run and row.is_active:
                    row.is_active = False
                    touched_paths.add(path)
                continue
            if delete:
                result.items.append(PruneItem(path, row.version or "", "deleted"))
                if not dry_run:
                    _assert_no_project_modules(db, row)
                    db.delete(row)
                    touched_paths.add(path)
            else:
                if row.is_active:
                    result.items.append(PruneItem(path, row.version or "", "deactivated"))
                    if not dry_run:
                        row.is_active = False
                        touched_paths.add(path)
                else:
                    result.items.append(
                        PruneItem(path, row.version or "", "kept", "already inactive")
                    )

    if not dry_run:
        db.flush()
        for path in touched_paths:
            recompute_is_latest(db, source_id, path)
        db.flush()
    return result


def prune_blueprint_source(
    db: Session,
    source_id: int,
    *,
    keep: int = 1,
    delete: bool = False,
    dry_run: bool = False,
    include_in_use: bool = False,
) -> PruneResult:
    """Retire superseded blueprint releases for one source.

    Grouped by ``blueprint_id``. A release a StackInstance points at is never
    deleted: that FK is ``ON DELETE SET NULL``, so the delete would quietly
    succeed and strip the stack of the record of what it was deployed from —
    which is exactly the provenance the immutable-release rule exists to keep.
    """
    if not db.query(BlueprintSource).filter(BlueprintSource.id == source_id).first():
        raise NotFoundError("blueprint_source", source_id)

    result = PruneResult(source_id=source_id, dry_run=dry_run, keep=keep)

    rows = (
        db.query(BlueprintRelease)
        .filter(BlueprintRelease.blueprint_source_id == source_id)
        .all()
    )
    by_id: dict[str, list[BlueprintRelease]] = {}
    for r in rows:
        by_id.setdefault(r.blueprint_id or "", []).append(r)

    for bp_id, releases in by_id.items():
        ordered = _ordered(releases, lambda r: r.blueprint_version)
        kept, candidates = ordered[:keep], ordered[keep:]
        refs_by_id = {
            r.id: db.query(StackInstance)
            .filter(StackInstance.blueprint_release_id == r.id)
            .count()
            for r in candidates
        }
        # Same invariant as the module path: a blueprint whose newest release is
        # already inactive must not lose its last active one and disappear from
        # the version picker.
        spared = _spare_last_active(kept, candidates, refs_by_id, include_in_use)

        for row in kept:
            result.items.append(PruneItem(bp_id, row.blueprint_version or "", "kept"))
        for row in candidates:
            refs = refs_by_id[row.id]
            if spared is not None and row.id == spared.id:
                result.items.append(
                    PruneItem(bp_id, row.blueprint_version or "", "kept", _SPARED_REASON)
                )
                continue
            if refs and not include_in_use:
                result.items.append(
                    PruneItem(bp_id, row.blueprint_version or "", "in_use",
                              f"{refs} stack instance(s) deployed from it; left untouched")
                )
                continue
            if refs:
                result.items.append(
                    PruneItem(bp_id, row.blueprint_version or "", "deactivated",
                              f"{refs} stack instance(s) deployed from it; hidden, not deleted")
                )
                if not dry_run and row.is_active:
                    row.is_active = False
                continue
            if delete:
                result.items.append(PruneItem(bp_id, row.blueprint_version or "", "deleted"))
                if not dry_run:
                    _assert_no_stack_instances(db, row)
                    db.delete(row)
            else:
                if row.is_active:
                    result.items.append(PruneItem(bp_id, row.blueprint_version or "", "deactivated"))
                    if not dry_run:
                        row.is_active = False
                else:
                    result.items.append(
                        PruneItem(bp_id, row.blueprint_version or "", "kept", "already inactive")
                    )

    if not dry_run:
        db.flush()
    # No is_latest recompute here, unlike the module path: BlueprintRelease has
    # no is_latest column — only is_active — so there is nothing to recompute.
    # The asymmetry is deliberate, not an omission.
    return result
