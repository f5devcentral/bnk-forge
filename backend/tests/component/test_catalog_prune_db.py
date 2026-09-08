"""Catalog pruning against a real database.

The unit tests drive a MagicMock session, which covers the grouping and refusal
logic well but cannot see what the database actually does — ``db.delete(row)``
on a mock is a recorded call, so the ORM cascade behind it is invisible. That
matters here more than usual:

``ModuleLibrary.project_modules`` is declared ``cascade="all, delete-orphan"``,
so deleting a referenced ModuleLibrary takes the project's ProjectModule row
with it, silently — the NOT NULL on ``module_library_id`` never gets a chance to
fire. And ``stack_instances.blueprint_release_id`` is ``ON DELETE SET NULL``, so
deleting a deployed release quietly strips the stack of its provenance instead
of failing.

Neither failure raises. Both destroy data. So the guards need testing where the
cascade can actually happen.
"""

from __future__ import annotations

import pytest

from core.errors import InternalError
from models import BlueprintRelease, ModuleLibrary, ModuleSource, ProjectModule
from models.blueprint_catalog import BlueprintSource
from models.stack import StackInstance
from services.catalog_prune_service import prune_blueprint_source, prune_module_source
from tests.factories import (
    ModuleLibraryFactory,
    ProjectModuleFactory,
    StackInstanceFactory,
)

_seq = iter(range(1, 10_000))


def _module_source(db) -> ModuleSource:
    """No factory exists for these yet; build the minimum the columns require."""
    src = ModuleSource(
        name=f"mod-source-{next(_seq)}", source_type="git", url="https://example.invalid/m.git"
    )
    db.add(src)
    db.flush()
    return src


def _blueprint_source(db) -> BlueprintSource:
    """No factory exists for these yet; build the minimum the columns require."""
    src = BlueprintSource(
        name=f"bp-source-{next(_seq)}", source_type="git", url="https://example.invalid/bp.git"
    )
    db.add(src)
    db.flush()
    return src


def _release(db, source, blueprint_id, version) -> BlueprintRelease:
    rel = BlueprintRelease(
        blueprint_source_id=source.id,
        blueprint_id=blueprint_id,
        blueprint_version=version,
        blueprint_name=f"{blueprint_id} {version}",
        schema_version=1,
        manifest={},
        content_sha256=f"{next(_seq):064d}",
        is_active=True,
    )
    db.add(rel)
    db.flush()
    return rel


def _versions(db, source, path, versions):
    return [
        ModuleLibraryFactory(db, path=path, version=v, module_source_id=source.id, is_active=True)
        for v in versions
    ]


@pytest.mark.component
class TestModulePruneAgainstTheDatabase:
    def test_delete_never_takes_a_projects_module_with_it(self, db):
        """The cascade this guard exists for, exercised where it can fire."""
        source = _module_source(db)
        old, new = _versions(db, source, "modules/app", ["1.0.0", "2.0.0"])
        pinned = ProjectModuleFactory(db, library_module=old)
        db.flush()

        result = prune_module_source(db, source.id, keep=1, delete=True)
        db.flush()

        assert db.query(ProjectModule).filter(ProjectModule.id == pinned.id).count() == 1
        assert db.query(ModuleLibrary).filter(ModuleLibrary.id == old.id).count() == 1
        assert db.query(ModuleLibrary).filter(ModuleLibrary.id == new.id).count() == 1
        assert [i.action for i in result.items if i.version == "1.0.0"] == ["in_use"]

    def test_delete_removes_a_version_nothing_points_at(self, db):
        source = _module_source(db)
        old, new = _versions(db, source, "modules/app", ["1.0.0", "2.0.0"])
        db.flush()

        prune_module_source(db, source.id, keep=1, delete=True)
        db.flush()

        assert db.query(ModuleLibrary).filter(ModuleLibrary.id == old.id).count() == 0
        assert db.query(ModuleLibrary).filter(ModuleLibrary.id == new.id).count() == 1

    def test_is_latest_lands_on_the_newest_survivor(self, db):
        """Deactivating a superseded version must leave exactly one active latest.

        Note this drives the ordinary case — the oldest is retired. The case
        where the NEWEST row is the inactive one is covered separately below;
        this test alone does not reach that branch.
        """
        source = _module_source(db)
        v1, v2, v3 = _versions(db, source, "modules/app", ["1.0.0", "2.0.0", "3.0.0"])
        v3.is_latest = True
        db.flush()

        prune_module_source(db, source.id, keep=2)
        db.flush()
        for row in (v1, v2, v3):
            db.refresh(row)

        assert v1.is_active is False, "the superseded version should be hidden"
        survivors = [r for r in (v1, v2, v3) if r.is_active]
        latest = [r for r in survivors if r.is_latest]
        assert len(latest) == 1, f"exactly one active latest, got {len(latest)}"
        assert latest[0].id == v3.id
        assert v1.is_latest is False

    def test_prune_never_leaves_a_path_with_no_active_version(self, db):
        """The newest row being inactive must not cost the path its last active one.

        Reachable without an operator touching anything: module_sync_service
        clears is_active on a manifest-backed row whose pack path stops
        appearing upstream — a rename is enough. That row still sorts newest, so
        it takes the kept slot, and a keep=1 prune would deactivate the last
        ACTIVE version underneath it. The module then has no active version and
        no is_latest, vanishes from the default catalog view, and there is no
        un-prune to walk it back.
        """
        source = _module_source(db)
        v1, v2 = _versions(db, source, "modules/app", ["1.0.0", "2.0.0"])
        v2.is_latest = True
        # is_latest defaults to True on the column, so the flag has to be forced
        # OFF here — otherwise the assertions below pass on the default and would
        # hold even with recompute_is_latest deleted from the service.
        v1.is_latest = False
        # Exactly what sync does on an upstream rename.
        v2.is_active = False
        db.flush()

        result = prune_module_source(db, source.id, keep=1)
        db.flush()
        db.refresh(v1)
        db.refresh(v2)

        active = [r for r in (v1, v2) if r.is_active]
        assert active, "the path must keep at least one active version"
        assert active[0].id == v1.id, "the newest ACTIVE version is the one spared"
        assert v1.is_latest is True, "the spared row must be handed the flag"
        assert v2.is_latest is False, "the inactive row must not keep it"
        assert sum(1 for r in (v1, v2) if r.is_latest) == 1
        spared = [i for i in result.items if i.version == "1.0.0"]
        assert spared and spared[0].action == "kept", spared
        assert "last active version" in spared[0].reason

    def test_a_group_that_still_has_an_active_version_is_pruned_normally(self, db):
        """The spare must not fire when something else already survives."""
        source = _module_source(db)
        v1, v2 = _versions(db, source, "modules/app", ["1.0.0", "2.0.0"])
        db.flush()

        prune_module_source(db, source.id, keep=1)
        db.flush()
        db.refresh(v1)
        db.refresh(v2)

        assert v2.is_active is True
        assert v1.is_active is False, "the superseded version is still retired"

    def test_pre_delete_assertion_refuses_a_referenced_row(self, db):
        """Belt and braces: the pre-delete assertion, not the loop's check.

        Nothing in normal operation reaches this, which is the point — if the
        loop's count is ever wrong the database will not save us, so the service
        raises instead of letting the cascade run.
        """
        from services import catalog_prune_service as svc

        source = _module_source(db)
        row = ModuleLibraryFactory(db, path="modules/app", version="1.0.0",
                                   module_source_id=source.id)
        ProjectModuleFactory(db, library_module=row)
        db.flush()

        with pytest.raises(InternalError, match="Refusing to delete module version"):
            svc._assert_no_project_modules(db, row)


@pytest.mark.component
class TestBlueprintPruneAgainstTheDatabase:
    def _releases(self, db, source, blueprint_id, versions):
        return [_release(db, source, blueprint_id, v) for v in versions]

    def test_delete_never_strips_a_stacks_provenance(self, db):
        """That FK is ON DELETE SET NULL, so the delete would quietly succeed."""
        source = _blueprint_source(db)
        old, new = self._releases(db, source, "bp/app", ["1.0.0", "2.0.0"])
        stack = StackInstanceFactory(db, blueprint_release_id=old.id)
        db.flush()

        result = prune_blueprint_source(db, source.id, keep=1, delete=True)
        db.flush()
        db.refresh(stack)

        assert stack.blueprint_release_id == old.id, "provenance must survive"
        assert db.query(BlueprintRelease).filter(BlueprintRelease.id == old.id).count() == 1
        assert [i.action for i in result.items if i.version == "1.0.0"] == ["in_use"]
        assert db.query(StackInstance).filter(StackInstance.id == stack.id).count() == 1

    def test_delete_removes_a_release_nothing_was_deployed_from(self, db):
        source = _blueprint_source(db)
        old, new = self._releases(db, source, "bp/app", ["1.0.0", "2.0.0"])
        db.flush()

        prune_blueprint_source(db, source.id, keep=1, delete=True)
        db.flush()

        assert db.query(BlueprintRelease).filter(BlueprintRelease.id == old.id).count() == 0
        assert db.query(BlueprintRelease).filter(BlueprintRelease.id == new.id).count() == 1

    def test_pre_delete_assertion_refuses_a_deployed_release(self, db):
        from services import catalog_prune_service as svc

        source = _blueprint_source(db)
        (rel,) = self._releases(db, source, "bp/app", ["1.0.0"])
        StackInstanceFactory(db, blueprint_release_id=rel.id)
        db.flush()

        with pytest.raises(InternalError, match="Refusing to delete blueprint release"):
            svc._assert_no_stack_instances(db, rel)
