"""D-033 PR-2 component tests: exact pinned-version resolution + version re-pin.

Covers ImportedBlueprintService pinned-version resolution (no fallback),
version-aware missing-module reporting (BLUEPRINT_MODULE_VERSION_MISSING vs
BLUEPRINT_MODULES_MISSING), and ProjectModuleService.change_module_version.
"""

import pytest

from core.errors import BadRequestError
from models import BlueprintSource
from services.imported_blueprint_service import ImportedBlueprintService
from services.project_module_service import ProjectModuleService
from tests.factories import ModuleLibraryFactory


def _two_versions(db, path="tools/roksbnkctl"):
    old = ModuleLibraryFactory(
        db, path=path, name="roksbnkctl", version="1.11.4",
        content_sha256="a" * 64, is_latest=False, is_official=False,
    )
    new = ModuleLibraryFactory(
        db, path=path, name="roksbnkctl", version="1.20.0",
        content_sha256="b" * 64, is_latest=True, is_official=False,
    )
    db.commit()
    return old, new


def _release(db, *, module_version: str, path="tools/roksbnkctl", state="imported"):
    source = BlueprintSource(
        name="idx", source_type="git", url="https://github.com/example/idx.git",
        branch="main", is_active=True, sync_status="success",
    )
    db.add(source)
    db.flush()
    from models import BlueprintRelease

    release = BlueprintRelease(
        blueprint_source_id=source.id,
        blueprint_id="demo",
        blueprint_version="1.0.0",
        blueprint_name="Demo",
        schema_version=1,
        content_sha256="c" * 64,
        manifest={
            "schema_version": 1,
            "blueprint": {"id": "demo", "version": "1.0.0", "name": "Demo"},
            "modules": [
                {"id": "m1", "module": path, "version": module_version, "depends_on": [], "inputs": {}}
            ],
            "inputs": {"required": [], "optional": []},
        },
        validation_state="valid",
        release_state=state,
        is_active=True,
    )
    db.add(release)
    db.commit()
    db.refresh(release)
    return release


# ── Exact pinned-version resolution ───────────────────────────────────────


def test_pinned_resolution_picks_exact_version_not_latest(db):
    old, new = _two_versions(db)
    svc = ImportedBlueprintService(db)

    resolved = svc._resolve_library_module("tools/roksbnkctl", "1.11.4")
    assert resolved is not None and resolved.id == old.id  # not the is_latest row

    resolved_latest = svc._resolve_library_module("tools/roksbnkctl")
    assert resolved_latest is not None and resolved_latest.id == new.id


def test_pinned_resolution_never_falls_back_to_other_hashed_version(db):
    _two_versions(db)
    svc = ImportedBlueprintService(db)
    assert svc._resolve_library_module("tools/roksbnkctl", "9.9.9") is None


def test_pinned_miss_with_hashed_rows_never_substitutes_legacy_row(db):
    """#433 self-review: once ANY hashed version exists for a path, a missed pin
    must be a hard miss — a leftover legacy row (whatever its version string)
    must not be silently substituted."""
    _two_versions(db)  # hashed 1.11.4 + 1.20.0
    legacy = ModuleLibraryFactory(
        db, path="tools/roksbnkctl", name="roksbnkctl", version="0.9.0",
        content_sha256=None, is_latest=False, is_official=False,
    )
    db.commit()
    svc = ImportedBlueprintService(db)

    assert svc._resolve_library_module("tools/roksbnkctl", "9.9.9") is None
    # And the legacy row is still resolvable when pinned exactly? No — legacy
    # rows have untrusted version strings; only hashed pins match exactly.
    resolved = svc._resolve_library_module("tools/roksbnkctl", "0.9.0")
    assert resolved is not None and resolved.id == legacy.id  # exact string match still wins


def test_change_module_version_never_jumps_sources(db, make_project_module):
    """#433 self-review: a re-pin is scoped to the current row's source — a same
    (path, version) row from ANOTHER source must not be silently adopted."""
    from models import ModuleSource

    src_a = ModuleSource(name="src-a", source_type="git", url="https://a.example/repo.git",
                         branch="main", is_active=True, sync_status="success")
    src_b = ModuleSource(name="src-b", source_type="git", url="https://b.example/repo.git",
                         branch="main", is_active=True, sync_status="success")
    db.add_all([src_a, src_b])
    db.flush()
    mine = ModuleLibraryFactory(
        db, path="tools/x", name="x", version="1.0.0", content_sha256="a" * 64,
        module_source_id=src_a.id, is_official=False,
    )
    ModuleLibraryFactory(
        db, path="tools/x", name="x", version="2.0.0", content_sha256="b" * 64,
        module_source_id=src_b.id, is_official=False,
    )
    pm = make_project_module(library_module=mine)
    db.commit()

    with pytest.raises(BadRequestError) as exc_info:
        ProjectModuleService(db).change_module_version(pm.id, "2.0.0")
    assert exc_info.value.code == "MODULE_VERSION_NOT_FOUND"
    assert exc_info.value.details["available_versions"] == ["1.0.0"]  # source-scoped listing


def test_pinned_resolution_falls_back_to_legacy_null_hash_row(db):
    """Transitional: a pre-D-033 row (NULL content hash) resolves regardless of
    the pin — its version string predates versioned identity. Strictness kicks
    in once the row is re-synced and hashed."""
    legacy = ModuleLibraryFactory(
        db, path="tools/legacyctl", name="legacyctl", version=None,
        content_sha256=None, is_official=False,
    )
    db.commit()
    svc = ImportedBlueprintService(db)
    resolved = svc._resolve_library_module("tools/legacyctl", "1.0.0")
    assert resolved is not None and resolved.id == legacy.id


def test_missing_modules_distinguishes_version_from_path(db):
    _two_versions(db)
    svc = ImportedBlueprintService(db)

    version_miss = svc._missing_modules(
        [{"id": "m1", "module": "tools/roksbnkctl", "version": "9.9.9"}]
    )
    assert len(version_miss) == 1
    assert version_miss[0]["reason"] == "version_missing"
    assert version_miss[0]["pinned_version"] == "9.9.9"
    assert set(version_miss[0]["available_versions"]) == {"1.11.4", "1.20.0"}
    assert "Available versions" in version_miss[0]["message"]

    path_miss = svc._missing_modules(
        [{"id": "m2", "module": "tools/nonexistent", "version": "1.0.0"}]
    )
    assert path_miss[0]["reason"] == "path_missing"
    assert path_miss[0]["available_versions"] == []


def test_validate_release_raises_version_missing_code(db):
    _two_versions(db)
    release = _release(db, module_version="9.9.9")
    svc = ImportedBlueprintService(db)

    with pytest.raises(BadRequestError) as exc_info:
        svc._validate_release(release.id)
    assert exc_info.value.code == "BLUEPRINT_MODULE_VERSION_MISSING"
    details = exc_info.value.details["missing_modules"][0]
    assert details["reason"] == "version_missing"


def test_validate_release_raises_modules_missing_code_for_absent_path(db):
    release = _release(db, module_version="1.0.0", path="tools/nonexistent")
    svc = ImportedBlueprintService(db)

    with pytest.raises(BadRequestError) as exc_info:
        svc._validate_release(release.id)
    assert exc_info.value.code == "BLUEPRINT_MODULES_MISSING"


def test_validate_release_passes_when_pin_resolves(db):
    _two_versions(db)
    release = _release(db, module_version="1.11.4")
    svc = ImportedBlueprintService(db)
    assert svc._validate_release(release.id).id == release.id


def test_preview_reports_available_versions_on_version_miss(db):
    _two_versions(db)
    svc = ImportedBlueprintService(db)
    preview = svc._serialize_modules_for_preview(
        [{"id": "m1", "module": "tools/roksbnkctl", "version": "9.9.9", "inputs": {}}]
    )
    assert preview[0]["module_catalog_status"] == "missing"
    assert "Available versions" in preview[0]["module_catalog_message"]


# ── Project-module version re-pin ─────────────────────────────────────────


def test_change_module_version_swaps_pin(db, make_project_module):
    old, new = _two_versions(db)
    pm = make_project_module(library_module=old)
    db.commit()

    result = ProjectModuleService(db).change_module_version(pm.id, "1.20.0")
    db.commit()

    assert result["success"] is True
    assert result["previous_version"] == "1.11.4"
    assert result["version"] == "1.20.0"
    db.refresh(pm)
    assert pm.module_library_id == new.id


def test_change_module_version_same_version_is_noop(db, make_project_module):
    old, _ = _two_versions(db)
    pm = make_project_module(library_module=old)
    db.commit()

    result = ProjectModuleService(db).change_module_version(pm.id, "1.11.4")
    assert result["success"] is True
    db.refresh(pm)
    assert pm.module_library_id == old.id


def test_change_module_version_unknown_version_lists_available(db, make_project_module):
    old, _ = _two_versions(db)
    pm = make_project_module(library_module=old)
    db.commit()

    with pytest.raises(BadRequestError) as exc_info:
        ProjectModuleService(db).change_module_version(pm.id, "9.9.9")
    assert exc_info.value.code == "MODULE_VERSION_NOT_FOUND"
    assert set(exc_info.value.details["available_versions"]) == {"1.11.4", "1.20.0"}
