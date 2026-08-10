"""Component tests for blueprint catalog persistence service."""

from types import SimpleNamespace

import pytest

from core.errors import BadRequestError, ConflictError, NotFoundError
from models import BlueprintRelease, BlueprintSource
from services.blueprint_catalog_service import BlueprintCatalogService
from services.defaults_service import seed_defaults, set_default


def _source_data(**overrides):
    defaults = {
        "name": "external-blueprints",
        "source_type": "git",
        "url": "https://github.com/example/blueprints.git",
        "branch": "main",
        "git_ref": None,
        "is_active": True,
        "description": "Cataloged blueprints",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _manifest(version: str = "1.0.0") -> dict:
    return {
        "schema_version": 1,
        "blueprint": {
            "id": "bnk.existing-cluster",
            "version": version,
            "name": "BNK Existing Cluster",
            "description": "Validated imported blueprint",
        },
        "category": "infrastructure",
        "cloud_provider": "ibm",
        "tags": ["ibm", "roks", "infrastructure"],
        "compatibility": {
            "supported_platform_profiles": ["generic_onprem"],
            "required_capabilities": ["kubernetes_api"],
        },
        "inputs": {
            "required": [
                {
                    "name": "project_name",
                    "type": "string",
                    "description": "Project display name",
                }
            ],
            "optional": [],
        },
        "modules": [
            {
                "id": "foundation",
                "module": "bnk.foundation.cluster",
                "version": "1.2.3",
                "depends_on": [],
                "inputs": {
                    "namespace": "f5-bnk",
                },
            },
            {
                "id": "networking",
                "module": "bnk.network.multus",
                "version": "v2.0.1",
                "depends_on": ["foundation"],
                "inputs": {},
            },
        ],
    }


def _release_data(source_id: int, **overrides):
    defaults = {
        "blueprint_source_id": source_id,
        "manifest": _manifest(),
        "source_path": "blueprints/existing-cluster/forge-blueprint.json",
        "source_ref": "refs/tags/v1.0.0",
        "release_state": "imported",
        "state_reason": "initial import",
        "is_active": True,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_create_source_persists_and_lists(db):
    service = BlueprintCatalogService(db)

    created = service.create_source(_source_data())
    listed = service.list_sources()

    assert created["name"] == "external-blueprints"
    assert created["sync_status"] == "pending"
    assert created["release_count"] == 0
    assert len(listed) == 1


def test_create_source_rejects_duplicate_name(db):
    service = BlueprintCatalogService(db)
    service.create_source(_source_data())

    with pytest.raises(ConflictError):
        service.create_source(_source_data())


def test_create_release_persists_manifest_identity_validation_and_state(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())

    created_release = service.create_release(_release_data(source["id"]))
    persisted_source = service.get_source(source["id"])

    assert created_release["blueprint_source_id"] == source["id"]
    assert created_release["blueprint_id"] == "bnk.existing-cluster"
    assert created_release["blueprint_version"] == "1.0.0"
    assert created_release["validation_state"] == "valid"
    assert created_release["validation_errors"] is None
    assert created_release["release_state"] == "imported"
    assert created_release["category"] == "infrastructure"
    assert created_release["cloud_provider"] == "ibm"
    assert created_release["tags"] == ["ibm", "roks", "infrastructure"]
    assert created_release["imported_at"] is not None
    assert created_release["mutable_lifecycle_fields"] == ["imported_at", "is_active", "release_state", "state_reason"]
    assert len(created_release["content_sha256"]) == 64
    assert persisted_source["release_count"] == 1


def test_create_release_blocks_approved_when_manifest_invalid(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())
    invalid_manifest = _manifest()
    invalid_manifest["modules"][0]["version"] = "latest"

    with pytest.raises(BadRequestError, match="validation"):
        service.create_release(
            _release_data(
                source["id"],
                manifest=invalid_manifest,
                release_state="approved",
            )
        )


def test_update_release_state_allows_promotion_for_valid_release(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())
    created = service.create_release(_release_data(source["id"]))

    updated = service.update_release_state(
        created["id"],
        SimpleNamespace(release_state="approved", state_reason="review passed", is_active=True),
    )

    assert updated["release_state"] == "approved"
    assert updated["state_reason"] == "review passed"


def test_create_discovered_release_requires_inactive_and_no_import_timestamp(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())

    created = service.create_release(
        _release_data(
            source["id"],
            release_state="discovered",
            is_active=False,
            state_reason="awaiting import",
        )
    )

    assert created["release_state"] == "discovered"
    assert created["is_active"] is False
    assert created["imported_at"] is None


def test_create_discovered_release_rejects_active_true(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())

    with pytest.raises(BadRequestError, match="must be inactive"):
        service.create_release(
            _release_data(
                source["id"],
                release_state="discovered",
                is_active=True,
            )
        )


def test_imported_release_structural_immutability_blocks_manifest_mutation(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())
    created = service.create_release(_release_data(source["id"]))

    release_row = db.query(BlueprintRelease).filter(BlueprintRelease.id == created["id"]).first()
    assert release_row is not None
    release_row.blueprint_name = "Mutated Name"

    with pytest.raises(ValueError, match="structurally immutable"):
        db.flush()


def test_get_unknown_release_raises_not_found(db):
    service = BlueprintCatalogService(db)
    with pytest.raises(NotFoundError):
        service.get_release(99999)


def test_delete_source_blocks_configured_default_blueprint_source(db):
    service = BlueprintCatalogService(db)
    seed_defaults(db)
    set_default(db, "blueprint_library.git_url", "https://github.com/example/blueprints.git")
    set_default(db, "blueprint_library.git_ref", "main")
    db.commit()

    created = service.create_source(
        SimpleNamespace(
            name="official-blueprints-blueprints",
            source_type="git",
            url="https://github.com/example/blueprints.git",
            branch="main",
            git_ref="main",
            is_active=True,
            description="default",
        )
    )

    with pytest.raises(BadRequestError) as exc:
        service.delete_source(created["id"])

    assert exc.value.code == "DEFAULT_BLUEPRINT_SOURCE_IMMUTABLE"


def test_list_releases_filters_by_state_and_source(db):
    service = BlueprintCatalogService(db)
    source_a = service.create_source(_source_data(name="a", url="https://git.example.com/a.git"))
    source_b = service.create_source(_source_data(name="b", url="https://git.example.com/b.git"))
    service.create_release(_release_data(source_a["id"], release_state="imported"))
    service.create_release(
        _release_data(
            source_b["id"],
            manifest=_manifest("1.0.1"),
            release_state="discovered",
            is_active=False,
        )
    )

    imported_source_a = service.list_releases(source_id=source_a["id"], release_state="imported")
    discovered = service.list_releases(release_state="discovered")

    assert len(imported_source_a) == 1
    assert imported_source_a[0]["blueprint_source_id"] == source_a["id"]
    assert len(discovered) == 1
    assert discovered[0]["release_state"] == "discovered"


def test_list_sources_rejects_unsupported_source_type_filter(db):
    service = BlueprintCatalogService(db)
    with pytest.raises(BadRequestError, match="source_type"):
        service.list_sources(source_type="registry")


def test_update_release_state_blocks_invalid_promotion(db):
    source = BlueprintSource(
        name="manual-source",
        source_type="git",
        url="https://git.example.com/manual.git",
        sync_status="pending",
        is_active=True,
    )
    db.add(source)
    db.flush()

    invalid_release = BlueprintRelease(
        blueprint_source_id=source.id,
        blueprint_id="bad.bp",
        blueprint_version="1.0.0",
        blueprint_name="Bad BP",
        schema_version=1,
        source_path="blueprints/bad/forge-blueprint.json",
        source_ref="refs/heads/main",
        content_sha256="0" * 64,
        manifest={"schema_version": 1, "blueprint": {"id": "bad.bp", "version": "1.0.0", "name": "Bad BP"}, "modules": []},
        validation_state="invalid",
        validation_errors=[{"code": "X"}],
        release_state="discovered",
        is_active=True,
    )
    db.add(invalid_release)
    db.flush()

    service = BlueprintCatalogService(db)
    with pytest.raises(BadRequestError, match="promoted"):
        service.update_release_state(
            invalid_release.id,
            SimpleNamespace(release_state="approved", state_reason="bad", is_active=True),
        )


def test_update_release_state_promotes_discovered_to_imported_and_sets_imported_at(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())
    created = service.create_release(
        _release_data(
            source["id"],
            release_state="discovered",
            is_active=False,
        )
    )

    updated = service.update_release_state(
        created["id"],
        SimpleNamespace(release_state="imported", state_reason="imported", is_active=True),
    )

    assert updated["release_state"] == "imported"
    assert updated["is_active"] is True
    assert updated["imported_at"] is not None


def test_update_release_state_imported_release_can_be_activated_from_discovered(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())
    created = service.create_release(
        _release_data(
            source["id"],
            release_state="discovered",
            is_active=False,
        )
    )

    updated = service.update_release_state(
        created["id"],
        SimpleNamespace(release_state="imported", state_reason="manual import", is_active=True),
    )

    assert updated["release_state"] == "imported"
    assert updated["is_active"] is True


def test_delete_release_removes_release_and_updates_source_count(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())
    discovered = service.create_release(
        _release_data(
            source["id"],
            manifest=_manifest("1.0.1"),
            release_state="discovered",
            is_active=False,
        )
    )
    imported = service.create_release(_release_data(source["id"]))

    result = service.delete_release(discovered["id"])

    assert result["success"] is True
    assert "deleted" in result["message"]
    with pytest.raises(NotFoundError):
        service.get_release(discovered["id"])
    persisted_source = service.get_source(source["id"])
    assert persisted_source["release_count"] == 1
    assert service.get_release(imported["id"])["id"] == imported["id"]


def test_update_release_state_blocks_reverting_to_discovered(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())
    created = service.create_release(_release_data(source["id"]))

    with pytest.raises(BadRequestError, match="cannot transition"):
        service.update_release_state(
            created["id"],
            SimpleNamespace(release_state="discovered", state_reason="revert", is_active=False),
        )


def test_lifecycle_update_allows_only_mutable_fields(db):
    service = BlueprintCatalogService(db)
    source = service.create_source(_source_data())
    created = service.create_release(_release_data(source["id"]))
    release_row = db.query(BlueprintRelease).filter(BlueprintRelease.id == created["id"]).first()
    assert release_row is not None

    release_row.state_reason = "updated reason"
    release_row.is_active = False
    db.flush()

    persisted = service.get_release(created["id"])
    assert persisted["state_reason"] == "updated reason"
    assert persisted["is_active"] is False
