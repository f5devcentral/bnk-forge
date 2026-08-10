"""Component tests for blueprint Git sync service."""

from unittest.mock import patch

from models import BlueprintRelease, BlueprintSource, ModuleSource
from services.blueprint_sync_service import BlueprintSyncService


def _source(db, **overrides):
    defaults = {
        "name": "external-blueprints",
        "source_type": "git",
        "url": "https://github.com/example/blueprints.git",
        "branch": "main",
        "is_active": True,
        "sync_status": "pending",
    }
    defaults.update(overrides)
    source = BlueprintSource(**defaults)
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


def _manifest(version: str = "1.0.0") -> dict:
    return {
        "schema_version": 1,
        "blueprint": {
            "id": "ibm-roks-bnk-2-3-ehf.single-nic",
            "version": version,
            "name": "IBM ROKS BNK EHF",
            "description": "Discovered blueprint",
        },
        "compatibility": {
            "supported_platform_profiles": ["ibm_roks"],
            "required_capabilities": [],
        },
        "inputs": {"required": [], "optional": []},
        "modules": [
            {
                "id": "cluster",
                "module": "modules/cluster",
                "version": "2.3.0-ehf-2-3.2598.3-0.0.17",
                "depends_on": [],
                "inputs": {},
            }
        ],
    }


def test_sync_git_source_discovers_blueprint_release(db):
    source = _source(db)
    service = BlueprintSyncService(db)

    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=["blueprints/sample/forge-blueprint.json"]), \
            patch.object(service, "_load_manifest", return_value=_manifest()), \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    assert result["blueprints_found"] == 1
    assert result["releases_created"] == 1
    assert result["releases_existing"] == 0
    assert result["errors"] == []

    releases = db.query(BlueprintRelease).filter(BlueprintRelease.blueprint_source_id == source.id).all()
    assert len(releases) == 1
    assert releases[0].release_state == "discovered"
    assert releases[0].is_active is False
    assert releases[0].source_path == "blueprints/sample/forge-blueprint.json"


def test_sync_git_source_skips_existing_same_content(db):
    """Re-syncing byte-identical content must be a no-op (releases_existing).

    The second sync must see the RAW parsed manifest — exactly what a real
    re-sync produces — not the persisted (Pydantic-normalized) manifest.
    create_release stores content_sha256 over the normalized manifest, so
    hashing the raw manifest on the sync side reported a false conflict on
    every re-sync of unchanged content (#430)."""
    source = _source(db)
    service = BlueprintSyncService(db)

    for _ in range(2):
        with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
                patch.object(service, "_find_blueprint_manifests", return_value=["blueprints/sample/forge-blueprint.json"]), \
                patch.object(service, "_load_manifest", return_value=_manifest()), \
                patch("os.path.exists", return_value=False):
            result = service.sync_git_source(source)

    assert result["releases_created"] == 0
    assert result["releases_existing"] == 1
    assert result["releases_conflicted"] == 0
    assert result["errors"] == []
    assert db.query(BlueprintRelease).filter(BlueprintRelease.blueprint_source_id == source.id).count() == 1


def test_sync_git_source_reports_conflicting_existing_release_content(db):
    source = _source(db)
    service = BlueprintSyncService(db)

    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=["blueprints/sample/forge-blueprint.json"]), \
            patch.object(service, "_load_manifest", return_value=_manifest(version="1.0.0")), \
            patch("os.path.exists", return_value=False):
        service.sync_git_source(source)

    conflicting_manifest = _manifest(version="1.0.0")
    conflicting_manifest["blueprint"]["description"] = "Updated blueprint content"

    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=["blueprints/sample/forge-blueprint.json"]), \
            patch.object(service, "_load_manifest", return_value=conflicting_manifest), \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    assert result["releases_created"] == 0
    assert result["releases_existing"] == 0
    assert result["releases_conflicted"] == 1
    assert len(result["errors"]) == 1


def test_sync_git_source_collects_manifest_errors(db):
    source = _source(db)
    service = BlueprintSyncService(db)

    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=["blueprints/bad/forge-blueprint.json"]), \
            patch.object(service, "_load_manifest", return_value={"schema_version": 1}), \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    assert result["blueprints_found"] == 1
    assert result["releases_created"] == 0
    assert len(result["errors"]) == 1
    assert "blueprints/bad/forge-blueprint.json" in result["errors"][0]


def test_sync_git_source_enriches_manifest_from_readme(db):
    service = BlueprintSyncService(db)

    readme = """# IBM ROKS Blueprint

## Solution Description

Deploys BIG-IP Next on IBM ROKS.

## What You Get

- Single NIC topology
- Imported project flow

## Prerequisites

- IBM Cloud API key

## Modules

### Cluster

Configures the target ROKS cluster.

## Input Variables

Provide IBM Cloud and cluster details.
"""

    enriched = service._merge_readme_metadata(
        _manifest(),
        service._parse_blueprint_readme(readme),
    )

    assert enriched["blueprint"]["description"] == "Discovered blueprint"
    assert enriched["outcomes"] == ["Single NIC topology", "Imported project flow"]
    assert enriched["prerequisites"][0]["description"] == "IBM Cloud API key"
    assert enriched["input_summary"][0]["label"] == "Input guidance"


def test_transition_release_manifest_generates_discovered_blueprints(db, tmp_path):
    source = _source(db, branch="release/2.2", git_ref="release/2.2")
    service = BlueprintSyncService(db)

    repo = tmp_path / "repo"
    (repo / "catalog" / "releases").mkdir(parents=True)
    (repo / "bnk" / "flo").mkdir(parents=True)
    (repo / "k8s" / "cert-manager").mkdir(parents=True)
    (repo / "bnk" / "flo" / "README.md").write_text("## Solution Description\n\nDeploy FLO onto BNK.", encoding="utf-8")
    (repo / "k8s" / "cert-manager" / "README.md").write_text("## Solution Description\n\nInstall cert-manager.", encoding="utf-8")
    (repo / "catalog" / "releases" / "release-2.2-official.json").write_text(
        """
        {
          "schema_version": "catalog-release/v1alpha2",
          "release": {
            "channel": "release/2.2",
            "name": "BNK 2.2 Official Baseline"
          },
          "official_modules": [
            { "path": "bnk/flo", "state": "active" },
            { "path": "k8s/cert-manager", "state": "active" },
            { "path": "bnk/legacy", "state": "legacy" }
          ]
        }
        """,
        encoding="utf-8",
    )

    with patch.object(service, "_clone_repository", return_value=str(repo)), patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    assert result["blueprints_found"] == 2
    assert result["releases_created"] == 2
    releases = db.query(BlueprintRelease).filter(BlueprintRelease.blueprint_source_id == source.id).all()
    assert len(releases) == 2
    assert {release.blueprint_name for release in releases} == {"Flo", "Cert Manager"}


def test_sync_git_source_auto_creates_module_source_when_pack_manifests_present(db):
    source = _source(db, url="https://github.com/example/catalog-shared.git", branch="release/2.3", git_ref="release/2.3")
    service = BlueprintSyncService(db)

    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=[]), \
            patch("services.blueprint_sync_service.ModuleSyncService._find_pack_modules", return_value=["modules/live-observability"]), \
            patch("services.blueprint_sync_service.ModuleSyncService.sync_git_source", return_value={"modules_found": 1}), \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    module_sources = db.query(ModuleSource).filter(ModuleSource.url == source.url).all()
    assert len(module_sources) == 1
    assert result["module_auto_sync"] is not None
    assert result["module_auto_sync"]["source_id"] == module_sources[0].id
    assert result["module_auto_sync"]["created"] is True


def test_sync_git_source_does_not_create_module_source_when_no_pack_manifests(db):
    source = _source(db)
    service = BlueprintSyncService(db)

    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=[]), \
            patch("services.blueprint_sync_service.ModuleSyncService._find_pack_modules", return_value=[]), \
            patch("services.blueprint_sync_service.ModuleSyncService.sync_git_source") as mock_module_sync, \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    assert result["module_auto_sync"] is None
    assert db.query(ModuleSource).count() == 0
    mock_module_sync.assert_not_called()


def test_sync_git_source_reuses_existing_module_source_by_url_and_ref(db):
    source = _source(db, url="https://github.com/example/catalog-shared.git", branch="release/2.3", git_ref="release/2.3")
    existing_module_source = ModuleSource(
        name="shared modules",
        source_type="git",
        url="https://user:token@github.com/example/catalog-shared",
        branch="main",
        git_ref="release/2.3",
        is_active=True,
        sync_status="pending",
    )
    db.add(existing_module_source)
    db.commit()
    db.refresh(existing_module_source)

    service = BlueprintSyncService(db)
    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=[]), \
            patch("services.blueprint_sync_service.ModuleSyncService._find_pack_modules", return_value=["modules/live-observability"]), \
            patch("services.blueprint_sync_service.ModuleSyncService.sync_git_source", return_value={"modules_found": 1}), \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    assert db.query(ModuleSource).count() == 1
    assert result["module_auto_sync"] is not None
    assert result["module_auto_sync"]["source_id"] == existing_module_source.id
    assert result["module_auto_sync"]["created"] is False


def test_sync_git_source_skips_module_sync_when_linked_module_source_is_inactive(db):
    """A deliberately deactivated twin module source must not be re-synced (and
    therefore re-activated) just because the blueprint source it is linked to
    gets synced."""
    source = _source(db, url="https://github.com/example/catalog-shared.git", branch="release/2.3", git_ref="release/2.3")
    existing_module_source = ModuleSource(
        name="shared modules",
        source_type="git",
        url="https://github.com/example/catalog-shared",
        branch="release/2.3",
        git_ref="release/2.3",
        is_active=False,
        sync_status="pending",
    )
    db.add(existing_module_source)
    db.commit()
    db.refresh(existing_module_source)

    service = BlueprintSyncService(db)
    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=[]), \
            patch("services.blueprint_sync_service.ModuleSyncService._find_pack_modules", return_value=["modules/live-observability"]), \
            patch("services.blueprint_sync_service.ModuleSyncService.sync_git_source") as mock_module_sync, \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    mock_module_sync.assert_not_called()
    db.refresh(existing_module_source)
    assert existing_module_source.is_active is False
    assert result["module_auto_sync"]["sync_status"] == "skipped_inactive"
    assert result["module_auto_sync"]["source_id"] == existing_module_source.id


def test_sync_git_source_ref_mismatch_creates_distinct_module_source(db):
    source = _source(db, url="https://github.com/example/catalog-shared.git", branch="release/2.4", git_ref="release/2.4")
    existing_module_source = ModuleSource(
        name="shared modules",
        source_type="git",
        url="https://github.com/example/catalog-shared",
        branch="release/2.3",
        git_ref="release/2.3",
        is_active=True,
        sync_status="pending",
    )
    db.add(existing_module_source)
    db.commit()

    service = BlueprintSyncService(db)
    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=[]), \
            patch("services.blueprint_sync_service.ModuleSyncService._find_pack_modules", return_value=["modules/live-observability"]), \
            patch("services.blueprint_sync_service.ModuleSyncService.sync_git_source", return_value={"modules_found": 1}), \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    assert db.query(ModuleSource).count() == 2
    assert result["module_auto_sync"] is not None
    assert result["module_auto_sync"]["source_id"] != existing_module_source.id
    assert result["module_auto_sync"]["created"] is True


def test_sync_git_source_module_auto_sync_failure_does_not_fail_blueprint(db):
    """A module auto-sync failure must not roll back created blueprint releases,
    must leave the blueprint source status 'success', and must not KeyError while
    recording the failure-shape module_auto_sync result in the audit summary."""
    source = _source(db, url="https://github.com/example/catalog-shared.git", branch="release/2.3", git_ref="release/2.3")
    service = BlueprintSyncService(db)

    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=[]), \
            patch.object(
                service,
                "_auto_sync_modules_for_git_source",
                side_effect=RuntimeError("transient clone failure"),
            ), \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    assert result["module_auto_sync"] == {"sync_status": "failed", "error": "transient clone failure"}
    assert source.sync_status == "success"


def test_sync_git_source_module_auto_sync_db_error_preserves_pending_releases(db):
    """A DB-level failure during module auto-sync (e.g. two syncs racing on the
    unique module_sources.name, raising IntegrityError from db.flush() inside
    _get_or_create_linked_module_source) must not abort the surrounding
    transaction: the blueprint release flushed earlier in the same
    sync_git_source call must survive and the final commit must still succeed.
    """
    source = _source(db, name="external-blueprints", url="https://github.com/example/catalog-shared.git",
                      branch="release/2.3", git_ref="release/2.3")

    # Simulate a name collision from a concurrent sync: pre-create (and commit)
    # a module source under the exact name this sync's auto-created linked
    # module source would use, but with a different url so it is NOT reused —
    # forcing a fresh INSERT that collides on the unique name at flush().
    racing_module_source = ModuleSource(
        name="external-blueprints modules",
        source_type="git",
        url="https://github.com/example/unrelated-repo.git",
        branch="main",
        git_ref=None,
        is_active=True,
        sync_status="pending",
    )
    db.add(racing_module_source)
    db.commit()

    service = BlueprintSyncService(db)
    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=["blueprints/sample/forge-blueprint.json"]), \
            patch.object(service, "_load_manifest", return_value=_manifest()), \
            patch.object(service, "_linked_module_source_name", return_value="external-blueprints modules"), \
            patch("services.blueprint_sync_service.ModuleSyncService._find_pack_modules", return_value=["modules/live-observability"]), \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    # The module auto-sync attempt failed with a DB-level error...
    assert result["module_auto_sync"]["sync_status"] == "failed"
    assert result["module_auto_sync"]["error"]

    # ...but the blueprint release flushed before the module-auto-sync guard
    # ran must have survived the failure and been committed, and the overall
    # sync must still be reported as successful.
    releases = db.query(BlueprintRelease).filter(BlueprintRelease.blueprint_source_id == source.id).all()
    assert len(releases) == 1
    assert result["releases_created"] == 1
    assert source.sync_status == "success"

    # The rollback must have discarded only the colliding module source
    # insert attempt — the racing module source is still the only row.
    assert db.query(ModuleSource).count() == 1


def test_sync_git_source_module_auto_sync_commit_does_not_corrupt_session(db):
    """ModuleSyncService.sync_git_source owns its session and calls db.commit()
    internally (~10 call sites). The auto-sync guard must tolerate that: with
    the old SAVEPOINT guard, the callee's first commit() closed the nested
    transaction, wedging the session so the blueprint sync's final commit died
    with PendingRollbackError (#428) — while the mid-flight commit persisted
    partial release rows that later syncs flagged as conflicted."""
    source = _source(db, url="https://github.com/example/catalog-shared.git",
                     branch="release/2.3", git_ref="release/2.3")
    service = BlueprintSyncService(db)

    def committing_module_sync(module_source):
        module_source.sync_status = "success"
        db.commit()
        return {"modules_found": 1}

    with patch.object(service, "_clone_repository", return_value="/tmp/repo"), \
            patch.object(service, "_find_blueprint_manifests", return_value=["blueprints/sample/forge-blueprint.json"]), \
            patch.object(service, "_load_manifest", return_value=_manifest()), \
            patch("services.blueprint_sync_service.ModuleSyncService._find_pack_modules", return_value=["tools/roksbnkctl"]), \
            patch("services.blueprint_sync_service.ModuleSyncService.sync_git_source", side_effect=committing_module_sync), \
            patch("os.path.exists", return_value=False):
        result = service.sync_git_source(source)

    assert result["releases_created"] == 1
    assert result["module_auto_sync"]["sync_status"] == "success"
    assert source.sync_status == "success"
    releases = db.query(BlueprintRelease).filter(BlueprintRelease.blueprint_source_id == source.id).all()
    assert len(releases) == 1
