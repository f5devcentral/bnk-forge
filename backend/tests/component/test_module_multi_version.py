"""D-033 PR-1 component tests: multi-version module catalog.

Covers the version-keyed upsert in ModuleSyncService._import_pack_module
(create / grandfather-update / unchanged / conflict), is_latest recomputation,
stale inactivation across versions, and the structural-immutability guard on
hashed ModuleLibrary rows.
"""

import pytest
from sqlalchemy.orm.attributes import flag_modified

from models import ModuleLibrary, ModuleSource
from services.module_sync_service import ModuleSyncService, _version_sort_key
from services.module_version_query import recompute_is_latest


def _source(db, **overrides):
    defaults = {
        "name": "bnkctl-index",
        "source_type": "git",
        "url": "https://github.com/example/bnkctl-index.git",
        "branch": "main",
        "is_active": True,
        "sync_status": "pending",
    }
    defaults.update(overrides)
    source = ModuleSource(**defaults)
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _info(version: str, path: str = "tools/roksbnkctl", description: str = "ctl tool") -> dict:
    return {
        "name": "roksbnkctl",
        "path": path,
        "version": version,
        "category": "bnk",
        "description": description,
        "engine_type": "container",
        "execution_engine": "container",
        "module_source_kind": "git_catalog",
        "pack_manifest": {"schema_version": 1, "module": {"name": "roksbnkctl", "version": version}},
        "inputs_metadata": {"required": [], "optional": []},
        "variables_schema": [],
        "outputs_metadata": [],
        "dependencies_metadata": {"required": [], "optional": []},
        "dependencies": [],
    }


def _rows(db, source_id, path="tools/roksbnkctl"):
    return (
        db.query(ModuleLibrary)
        .filter(ModuleLibrary.module_source_id == source_id, ModuleLibrary.path == path)
        .order_by(ModuleLibrary.id)
        .all()
    )


# ── Version-keyed upsert outcomes ─────────────────────────────────────────


def test_import_new_version_creates_row_and_preserves_old(db):
    source = _source(db)
    svc = ModuleSyncService(db)

    assert svc._import_pack_module(source, _info("1.11.4"), "tools/roksbnkctl") == "created"
    assert svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl") == "created"

    rows = _rows(db, source.id)
    assert [r.version for r in rows] == ["1.11.4", "1.20.0"]
    old, new = rows
    assert old.pack_manifest["module"]["version"] == "1.11.4"  # old content intact
    assert old.is_latest is False
    assert new.is_latest is True
    assert old.is_active and new.is_active


def test_import_same_version_same_content_is_unchanged(db):
    source = _source(db)
    svc = ModuleSyncService(db)

    assert svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl") == "created"
    assert svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl") == "unchanged"
    assert len(_rows(db, source.id)) == 1


def test_import_same_version_different_content_conflicts(db):
    source = _source(db)
    svc = ModuleSyncService(db)

    assert svc._import_pack_module(source, _info("1.20.0", description="original"), "tools/roksbnkctl") == "created"
    outcome = svc._import_pack_module(source, _info("1.20.0", description="tampered"), "tools/roksbnkctl")
    assert outcome == "conflict"

    (row,) = _rows(db, source.id)
    assert row.description == "original"  # stored row untouched


def test_unchanged_resync_reactivates_inactive_version(db):
    source = _source(db)
    svc = ModuleSyncService(db)
    svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl")
    (row,) = _rows(db, source.id)
    row.is_active = False
    db.commit()

    assert svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl") == "unchanged"
    db.refresh(row)
    assert row.is_active is True


# ── Legacy (pre-D-033) row transition ─────────────────────────────────────


def _legacy_row(db, source, version, path="tools/roksbnkctl"):
    row = ModuleLibrary(
        name="roksbnkctl",
        category="bnk",
        path=path,
        git_source=f"{source.url}//{path}",
        version=version,
        module_source_id=source.id,
        source_path=path,
        pack_manifest={"schema_version": 1, "legacy": True},
        engine_type="container",
        is_active=True,
        content_sha256=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_legacy_row_same_version_grandfathered_once_then_frozen(db):
    source = _source(db)
    svc = ModuleSyncService(db)
    legacy = _legacy_row(db, source, "1.11.4")

    assert svc._import_pack_module(source, _info("1.11.4", description="refreshed"), "tools/roksbnkctl") == "updated"
    db.refresh(legacy)
    assert legacy.description == "refreshed"
    assert legacy.content_sha256 is not None

    # Second drift of the same version now conflicts — the row froze.
    outcome = svc._import_pack_module(source, _info("1.11.4", description="drifted again"), "tools/roksbnkctl")
    assert outcome == "conflict"
    db.refresh(legacy)
    assert legacy.description == "refreshed"


def test_legacy_row_version_bump_creates_new_row_and_keeps_legacy(db):
    source = _source(db)
    svc = ModuleSyncService(db)
    legacy = _legacy_row(db, source, "1.11.4")

    assert svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl") == "created"

    rows = _rows(db, source.id)
    assert len(rows) == 2
    db.refresh(legacy)
    assert legacy.version == "1.11.4"
    assert legacy.pack_manifest == {"schema_version": 1, "legacy": True}  # untouched
    assert legacy.is_latest is False
    assert rows[-1].version == "1.20.0"
    assert rows[-1].is_latest is True


# ── Stale inactivation across versions ────────────────────────────────────


def test_stale_inactivation_keeps_all_versions_of_present_path(db):
    source = _source(db)
    svc = ModuleSyncService(db)
    svc._import_pack_module(source, _info("1.11.4"), "tools/roksbnkctl")
    svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl")

    inactivated = svc._inactivate_stale_manifest_modules(
        source_id=source.id, discovered_pack_paths={"tools/roksbnkctl"}
    )
    assert inactivated == 0
    assert all(r.is_active for r in _rows(db, source.id))

    inactivated = svc._inactivate_stale_manifest_modules(source_id=source.id, discovered_pack_paths=set())
    db.commit()
    assert inactivated == 2
    assert not any(r.is_active for r in _rows(db, source.id))


# ── Structural immutability guard ─────────────────────────────────────────


def test_hashed_row_rejects_structural_mutation(db):
    source = _source(db)
    svc = ModuleSyncService(db)
    svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl")
    (row,) = _rows(db, source.id)

    row.pack_manifest = {"tampered": True}
    with pytest.raises(ValueError, match="structurally immutable"):
        db.commit()
    db.rollback()


def test_hashed_row_allows_lifecycle_mutation(db):
    source = _source(db)
    svc = ModuleSyncService(db)
    svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl")
    (row,) = _rows(db, source.id)

    row.is_active = False
    row.is_latest = False
    row.test_status = "passed"
    db.commit()
    db.refresh(row)
    assert row.is_active is False


def test_null_hash_row_stays_fully_mutable(db):
    source = _source(db)
    legacy = _legacy_row(db, source, "0.9.0")
    legacy.description = "edited freely"
    legacy.pack_manifest = {"schema_version": 1, "edited": True}
    db.commit()
    db.refresh(legacy)
    assert legacy.description == "edited freely"


# ── Review-hardening regressions (#433 self-review) ───────────────────────


def test_conflict_resync_reactivates_inactive_version(db):
    """A stale-inactivated version whose path reappears with drifted content is
    reactivated (its immutable content IS that version) while the conflict is
    still reported — previously it stayed inactive forever."""
    source = _source(db)
    svc = ModuleSyncService(db)
    svc._import_pack_module(source, _info("1.20.0", description="original"), "tools/roksbnkctl")
    (row,) = _rows(db, source.id)
    row.is_active = False
    db.commit()

    outcome = svc._import_pack_module(source, _info("1.20.0", description="drifted"), "tools/roksbnkctl")
    assert outcome == "conflict"
    db.refresh(row)
    assert row.is_active is True
    assert row.description == "original"  # content still not overwritten


def test_recompute_is_latest_ignores_inactive_rows(db):
    """An inactive newest version must not hold is_latest while active older
    versions read False — that combination hides the module from every
    latest-filtered view."""
    source = _source(db)
    svc = ModuleSyncService(db)
    svc._import_pack_module(source, _info("1.11.4"), "tools/roksbnkctl")
    svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl")
    old, new = _rows(db, source.id)

    new.is_active = False
    recompute_is_latest(db, source.id, "tools/roksbnkctl")
    db.commit()

    db.refresh(old), db.refresh(new)
    assert old.is_latest is True  # newest ACTIVE version wins
    assert new.is_latest is False


def test_import_error_rolls_back_and_sync_still_succeeds(db):
    """A per-module import failure (e.g. IntegrityError from a concurrent sync)
    must not poison the session — remaining modules and the final status commit
    proceed."""
    from unittest.mock import patch

    source = _source(db)
    svc = ModuleSyncService(db)

    def _boom_then_ok(src_arg, module_info, module_path):
        if module_path == "tools/bad":
            raise RuntimeError("simulated commit-time failure")
        return ModuleSyncService._import_pack_module(svc, src_arg, module_info, module_path)

    with patch.object(svc, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(svc, "_find_pack_modules", return_value=["tools/bad", "tools/roksbnkctl"]), \
            patch.object(svc, "_parse_pack_module",
                         side_effect=lambda p, _d: _info("1.0.0", path=p)), \
            patch.object(svc, "_import_pack_module", side_effect=_boom_then_ok), \
            patch("os.path.exists", return_value=False):
        results = svc.sync_git_source(source)

    assert results["modules_created"] == 1
    assert len(results["pack_errors"]) == 1
    db.refresh(source)
    assert source.sync_status == "success"


def test_hashed_row_allows_no_op_structural_write(db):
    """Idempotent writers (official-catalog upsert with flag_modified, seeders
    re-asserting equal values) must pass the immutability guard when the value
    is unchanged."""
    source = _source(db)
    svc = ModuleSyncService(db)
    svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl")
    (row,) = _rows(db, source.id)

    row.pack_manifest = dict(row.pack_manifest)  # equal value, new identity
    flag_modified(row, "pack_manifest")
    row.description = row.description  # no-op setattr
    db.commit()  # must not raise
    db.refresh(row)
    assert row.version == "1.20.0"


def test_create_skips_path_owned_by_ssh_builtin(db):
    """A catalog pack at an SSH builtin's path must not create a parallel row —
    it would demote the builtin's is_latest and steal path-based resolution."""
    source = _source(db)
    builtin = ModuleLibrary(
        name="ssh-owned", category="bnk", path="bnk/ssh-owned",
        git_source="builtin://python-registry", execution_engine="ssh",
        module_source_kind="builtin", is_official=True, is_active=True,
    )
    db.add(builtin)
    db.commit()

    svc = ModuleSyncService(db)
    outcome = svc._import_pack_module(source, _info("2.0.0", path="bnk/ssh-owned"), "bnk/ssh-owned")
    assert outcome == "skipped"
    rows = db.query(ModuleLibrary).filter(ModuleLibrary.path == "bnk/ssh-owned").all()
    assert len(rows) == 1  # only the builtin


def test_module_count_counts_distinct_paths_not_version_rows(db):
    source = _source(db)
    svc = ModuleSyncService(db)
    svc._import_pack_module(source, _info("1.11.4"), "tools/roksbnkctl")
    svc._import_pack_module(source, _info("1.20.0"), "tools/roksbnkctl")
    assert svc._count_active_modules_for_source(source.id) == 1


# ── Version ordering ──────────────────────────────────────────────────────


def test_version_sort_key_orders_numerically_and_handles_noise():
    ordered = sorted(
        ["1.9.0", "1.20.0", "v2.3.1", "2.3.0-ehf-2-3.2598.3-0.0.17", "2.3.0", None, "garbage"],
        key=_version_sort_key,
    )
    assert ordered.index("1.9.0") < ordered.index("1.20.0")  # numeric, not lexicographic
    assert ordered.index("2.3.0-ehf-2-3.2598.3-0.0.17") < ordered.index("2.3.0")  # pre-release below release
    assert ordered.index("2.3.0") < ordered.index("v2.3.1")
    assert ordered[0] in (None, "garbage")  # unparseable sorts lowest


def test_version_sort_key_ignores_build_metadata():
    """+build metadata has no precedence (semver §10); it must not demote a patch release."""
    ordered = sorted(
        ["1.20.0", "1.20.1+hotfix.1", "v2.0.1+build.7", "2.0.0", "2.0.1-rc.1+build.9"],
        key=_version_sort_key,
    )
    assert ordered.index("1.20.0") < ordered.index("1.20.1+hotfix.1")  # hotfix outranks base
    assert ordered.index("2.0.0") < ordered.index("v2.0.1+build.7")
    # pre-release with build metadata still ranks below the release of the same core
    assert ordered.index("2.0.1-rc.1+build.9") < ordered.index("v2.0.1+build.7")
