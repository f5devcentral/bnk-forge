"""Component tests for the builtin blueprint source service."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from models import BlueprintRelease, BlueprintSource
from schemas.blueprints import BlueprintManifest
from services.blueprint_builtin_source_service import (
    ensure_default_builtin_blueprint_source,
    sync_builtin_source,
)
from services.blueprint_catalog_service import BlueprintCatalogService

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _minimal_manifest(blueprint_id: str = "aws-eks-cluster-demo", version: str = "1.0.0") -> dict:
    return {
        "schema_version": 1,
        "blueprint": {
            "id": blueprint_id,
            "version": version,
            "name": "AWS EKS Cluster - Demo",
            "description": "Test builtin blueprint",
        },
        "inputs": {"required": [], "optional": []},
        "modules": [
            {
                "id": "infra-aws-vpc",
                "module": "infra/aws/vpc",
                "version": "1.0.0",
                "depends_on": [],
                "inputs": {},
            }
        ],
    }


# ---------------------------------------------------------------------------
# ensure_default_builtin_blueprint_source
# ---------------------------------------------------------------------------

def test_ensure_default_builtin_blueprint_source_creates_source(db):
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()

    assert source.id is not None
    assert source.source_type == "builtin"
    assert source.name == "builtin-blueprints"
    assert source.is_active is True


def test_ensure_default_builtin_blueprint_source_idempotent(db):
    source1 = ensure_default_builtin_blueprint_source(db)
    db.commit()
    source2 = ensure_default_builtin_blueprint_source(db)
    db.commit()

    assert source1.id == source2.id
    assert db.query(BlueprintSource).filter(BlueprintSource.source_type == "builtin").count() == 1


# ---------------------------------------------------------------------------
# sync_builtin_source — loads bundled aws-eks-cluster-demo manifest
# ---------------------------------------------------------------------------

def test_sync_builtin_source_loads_aws_eks_cluster_demo(db):
    """sync_builtin_source picks up the bundled aws-eks-cluster-demo manifest."""
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()

    result = sync_builtin_source(db, source)
    db.commit()

    assert result["blueprints_found"] >= 1
    assert result["releases_created"] >= 1
    assert result["errors"] == []

    release = (
        db.query(BlueprintRelease)
        .filter(BlueprintRelease.blueprint_source_id == source.id)
        .filter(BlueprintRelease.blueprint_id == "aws-eks-cluster-demo")
        .first()
    )
    assert release is not None
    assert release.blueprint_version == "1.1.0"
    assert release.release_state == "imported"
    assert release.validation_state == "valid"
    assert release.is_active is True


# ---------------------------------------------------------------------------
# sync_builtin_source — idempotency
# ---------------------------------------------------------------------------

def test_sync_builtin_source_idempotent(db):
    """Running sync twice does not duplicate the release."""
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()

    sync_builtin_source(db, source)
    db.commit()

    first_count = db.query(BlueprintRelease).filter(BlueprintRelease.blueprint_source_id == source.id).count()

    result2 = sync_builtin_source(db, source)
    db.commit()

    second_count = db.query(BlueprintRelease).filter(BlueprintRelease.blueprint_source_id == source.id).count()

    assert second_count == first_count
    assert result2["releases_existing"] >= 1
    assert result2["releases_created"] == 0


# ---------------------------------------------------------------------------
# sync_builtin_source — mocked single manifest
# ---------------------------------------------------------------------------

def test_sync_builtin_source_creates_imported_valid_active_release(db):
    """Mocked manifest is stored with IMPORTED/valid/active states."""
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()

    manifest = _minimal_manifest()
    mock_manifests = [("data/blueprints/aws-eks-cluster-demo/forge-blueprint.json", manifest)]

    with patch("services.blueprint_builtin_source_service._discover_builtin_manifests", return_value=mock_manifests):
        result = sync_builtin_source(db, source)
    db.commit()

    assert result["blueprints_found"] == 1
    assert result["releases_created"] == 1
    assert result["releases_existing"] == 0
    assert result["errors"] == []

    release = db.query(BlueprintRelease).filter(BlueprintRelease.blueprint_source_id == source.id).one()
    assert release.blueprint_id == "aws-eks-cluster-demo"
    assert release.blueprint_version == "1.0.0"
    assert release.release_state == "imported"
    assert release.validation_state == "valid"
    assert release.is_active is True


def test_sync_builtin_source_skips_existing_same_content(db):
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()

    manifest = _minimal_manifest()
    mock_manifests = [("data/blueprints/aws-eks-cluster-demo/forge-blueprint.json", manifest)]

    with patch("services.blueprint_builtin_source_service._discover_builtin_manifests", return_value=mock_manifests):
        sync_builtin_source(db, source)
        db.commit()

    with patch("services.blueprint_builtin_source_service._discover_builtin_manifests", return_value=mock_manifests):
        result2 = sync_builtin_source(db, source)
    db.commit()

    assert result2["releases_created"] == 0
    assert result2["releases_existing"] == 1
    assert db.query(BlueprintRelease).filter(BlueprintRelease.blueprint_source_id == source.id).count() == 1


# ---------------------------------------------------------------------------
# BlueprintManifest schema — optional field
# ---------------------------------------------------------------------------

def test_blueprint_manifest_accepts_module_with_optional_true():
    data = {
        "schema_version": 1,
        "blueprint": {"id": "test", "version": "1.0.0", "name": "Test"},
        "inputs": {"required": [], "optional": []},
        "modules": [
            {
                "id": "some-module",
                "module": "infra/aws/vpc",
                "version": "1.0.0",
                "optional": True,
                "depends_on": [],
                "inputs": {},
            }
        ],
    }
    manifest = BlueprintManifest.model_validate(data)
    assert manifest.modules[0].optional is True


def test_blueprint_manifest_optional_defaults_to_false():
    data = {
        "schema_version": 1,
        "blueprint": {"id": "test", "version": "1.0.0", "name": "Test"},
        "inputs": {"required": [], "optional": []},
        "modules": [
            {
                "id": "some-module",
                "module": "infra/aws/vpc",
                "version": "1.0.0",
                "depends_on": [],
                "inputs": {},
            }
        ],
    }
    manifest = BlueprintManifest.model_validate(data)
    assert manifest.modules[0].optional is False


# ---------------------------------------------------------------------------
# sync_builtin_source — all 12 bundled manifests load
# ---------------------------------------------------------------------------

def test_sync_builtin_source_loads_all_13_manifests(db):
    """sync_builtin_source finds and creates releases for all 13 bundled blueprints."""
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()

    result = sync_builtin_source(db, source)
    db.commit()

    assert result["blueprints_found"] == 13, (
        f"Expected 13 bundled blueprints, found {result['blueprints_found']}. "
        f"Errors: {result['errors']}"
    )
    assert result["releases_invalid"] == 0, f"Validation failures: {result['errors']}"
    assert result["errors"] == []

    total_releases = (
        db.query(BlueprintRelease)
        .filter(BlueprintRelease.blueprint_source_id == source.id)
        .count()
    )
    assert total_releases == 13


# ---------------------------------------------------------------------------
# set_release_visibility — via BlueprintCatalogService
# ---------------------------------------------------------------------------

def _make_imported_release(db, source_id: int, blueprint_id: str = "aws-eks-cluster-demo") -> BlueprintRelease:
    svc = BlueprintCatalogService(db)
    svc.create_release(
        SimpleNamespace(
            blueprint_source_id=source_id,
            manifest=_minimal_manifest(blueprint_id),
            source_path=f"data/blueprints/{blueprint_id}/forge-blueprint.json",
            source_ref=None,
            release_state="imported",
            state_reason="test",
            is_active=True,
        )
    )
    db.flush()
    return (
        db.query(BlueprintRelease)
        .filter(BlueprintRelease.blueprint_id == blueprint_id)
        .one()
    )


def test_set_release_visibility_off_hides_from_catalog(db):
    """Toggling visibility OFF sets is_active=False without changing release_state."""
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()
    release = _make_imported_release(db, source.id)
    db.commit()

    assert release.is_active is True
    assert release.release_state == "imported"

    svc = BlueprintCatalogService(db)
    result = svc.set_release_visibility(release.id, False)
    db.commit()

    assert result["is_active"] is False
    assert result["release_state"] == "imported"


def test_set_release_visibility_on_restores_visibility(db):
    """Toggling visibility back ON sets is_active=True."""
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()
    release = _make_imported_release(db, source.id, blueprint_id="aws-k8s-foundation")
    db.commit()

    svc = BlueprintCatalogService(db)
    svc.set_release_visibility(release.id, False)
    db.commit()

    result = svc.set_release_visibility(release.id, True)
    db.commit()

    assert result["is_active"] is True
    assert result["release_state"] == "imported"


def test_set_release_visibility_discovered_raises(db):
    """set_release_visibility blocks discovered releases."""
    from core.errors import BadRequestError

    source = ensure_default_builtin_blueprint_source(db)
    db.commit()

    # Create a discovered release (not imported)
    svc = BlueprintCatalogService(db)
    svc.create_release(
        SimpleNamespace(
            blueprint_source_id=source.id,
            manifest=_minimal_manifest("azure-aks-foundation", "2.0.0"),
            source_path="data/blueprints/azure-aks-foundation/forge-blueprint.json",
            source_ref=None,
            release_state="discovered",
            state_reason="test",
            is_active=False,
        )
    )
    db.flush()
    discovered = (
        db.query(BlueprintRelease)
        .filter(BlueprintRelease.blueprint_id == "azure-aks-foundation")
        .one()
    )
    db.commit()

    with pytest.raises(BadRequestError, match="import it first"):
        svc.set_release_visibility(discovered.id, True)


def test_is_featured_serialized_from_manifest(db):
    """_serialize_release reads is_featured from stored manifest."""
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()

    featured_manifest = {
        **_minimal_manifest("aws-k8s-foundation"),
        "is_featured": True,
    }
    svc = BlueprintCatalogService(db)
    svc.create_release(
        SimpleNamespace(
            blueprint_source_id=source.id,
            manifest=featured_manifest,
            source_path="data/blueprints/aws-k8s-foundation/forge-blueprint.json",
            source_ref=None,
            release_state="imported",
            state_reason="test",
            is_active=True,
        )
    )
    db.flush()
    release = (
        db.query(BlueprintRelease)
        .filter(BlueprintRelease.blueprint_id == "aws-k8s-foundation")
        .one()
    )
    db.commit()

    result = svc.get_release(release.id)
    assert result["is_featured"] is True


# ---------------------------------------------------------------------------
# _serialize_release — source_type exposed from related source
# ---------------------------------------------------------------------------

def test_serialize_release_includes_source_type(db):
    """_serialize_release includes source_type from the related BlueprintSource."""
    source = ensure_default_builtin_blueprint_source(db)
    db.commit()
    release = _make_imported_release(db, source.id, blueprint_id="aws-eks-cluster-demo")
    db.commit()

    svc = BlueprintCatalogService(db)
    result = svc.get_release(release.id)

    assert "source_type" in result
    assert result["source_type"] == "builtin"
