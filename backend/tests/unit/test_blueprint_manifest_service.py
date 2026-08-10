"""Unit tests for canonical blueprint manifest validation."""

import pytest

from services.blueprint_manifest_service import (
    BlueprintManifestValidationError,
    validate_blueprint_manifest,
)


def _valid_manifest() -> dict:
    return {
        "schema_version": 1,
        "blueprint": {
            "id": "bnk.existing-cluster",
            "version": "2.2.0",
            "name": "BNK Existing Cluster",
            "description": "Validated imported blueprint",
        },
        "compatibility": {
            "supported_platform_profiles": ["generic_onprem", "eks"],
            "required_capabilities": ["gateway_api"],
        },
        "outcomes": ["Gateway deployed", "Inputs validated"],
        "prerequisites": [
            {"type": "project_secret", "name": "license_jwt", "description": "F5 license JWT"}
        ],
        "inputs": {
            "required": [
                {
                    "name": "cluster_name",
                    "type": "string",
                    "description": "Target cluster name",
                    "label": "Cluster Name",
                    "example": "my-cluster",
                }
            ],
            "optional": [
                {
                    "name": "region",
                    "type": "string",
                    "description": "Cloud region",
                    "default": "us-west-2",
                }
            ],
        },
        "modules": [
            {
                "id": "network",
                "module": "infra/aws/network",
                "version": "1.3.0",
                "name": "AWS Network",
                "description": "Creates VPC networking for the solution.",
                "depends_on": [],
                "inputs": {},
            },
            {
                "id": "gateway",
                "module": "bnk/gateway/core",
                "version": "2.2.0",
                "depends_on": ["network"],
                "inputs": {"cluster_name": "${inputs.cluster_name}"},
            },
        ],
    }


def test_validate_blueprint_manifest_accepts_valid_manifest() -> None:
    manifest = validate_blueprint_manifest(_valid_manifest())

    assert manifest.blueprint.id == "bnk.existing-cluster"
    assert manifest.modules[1].depends_on == ["network"]


def test_validate_blueprint_manifest_rejects_missing_required_field_with_structured_issue() -> None:
    bad_manifest = _valid_manifest()
    del bad_manifest["blueprint"]["id"]

    with pytest.raises(BlueprintManifestValidationError) as exc_info:
        validate_blueprint_manifest(bad_manifest)

    issues = exc_info.value.issues
    assert any(issue.code == "BLUEPRINT_MANIFEST_FIELD_REQUIRED" for issue in issues)
    assert any(issue.path == "blueprint.id" for issue in issues)


def test_validate_blueprint_manifest_rejects_implicit_latest_module_version() -> None:
    bad_manifest = _valid_manifest()
    bad_manifest["modules"][0]["version"] = "latest"

    with pytest.raises(BlueprintManifestValidationError) as exc_info:
        validate_blueprint_manifest(bad_manifest)

    issues = exc_info.value.issues
    assert any(issue.code == "BLUEPRINT_MODULE_VERSION_INVALID" for issue in issues)
    assert any(issue.path == "modules.0.version" for issue in issues)


@pytest.mark.parametrize(
    "version_ref",
    [
        "1.2",
        "1.2.x",
        "*",
        "~1.2.3",
        "^1.2.3",
        ">=1.2.3",
        "main",
    ],
)
def test_validate_blueprint_manifest_rejects_floating_or_range_module_versions(version_ref: str) -> None:
    bad_manifest = _valid_manifest()
    bad_manifest["modules"][0]["version"] = version_ref

    with pytest.raises(BlueprintManifestValidationError) as exc_info:
        validate_blueprint_manifest(bad_manifest)

    issues = exc_info.value.issues
    assert any(issue.code == "BLUEPRINT_MODULE_VERSION_INVALID" for issue in issues)
    assert any(issue.path == "modules.0.version" for issue in issues)


@pytest.mark.parametrize("version_ref", ["1.2.3", "v1.2.3", "2.0.1-rc.1", "v2.0.1+build.7", "1.20.0.1"])
def test_validate_blueprint_manifest_accepts_pinned_module_versions(version_ref: str) -> None:
    manifest_data = _valid_manifest()
    manifest_data["modules"][0]["version"] = version_ref

    manifest = validate_blueprint_manifest(manifest_data)

    assert manifest.modules[0].version == version_ref


def test_validate_blueprint_manifest_rejects_missing_dependency_reference() -> None:
    bad_manifest = _valid_manifest()
    bad_manifest["modules"][1]["depends_on"] = ["nonexistent-module"]

    with pytest.raises(BlueprintManifestValidationError) as exc_info:
        validate_blueprint_manifest(bad_manifest)

    issues = exc_info.value.issues
    assert any(issue.code == "BLUEPRINT_MODULE_REFERENCE_NOT_FOUND" for issue in issues)
    assert any(issue.path == "modules.1.depends_on.0" for issue in issues)


def test_validate_blueprint_manifest_rejects_duplicate_dependency_refs_within_module() -> None:
    bad_manifest = _valid_manifest()
    bad_manifest["modules"][1]["depends_on"] = ["network", "network"]

    with pytest.raises(BlueprintManifestValidationError) as exc_info:
        validate_blueprint_manifest(bad_manifest)

    issues = exc_info.value.issues
    assert any(issue.code == "BLUEPRINT_MODULE_DEPENDENCY_DUPLICATE" for issue in issues)
    assert any(issue.path == "modules.1.depends_on.1" for issue in issues)


def test_validate_blueprint_manifest_rejects_self_dependency_with_structured_issue() -> None:
    bad_manifest = _valid_manifest()
    bad_manifest["modules"][1]["depends_on"] = ["gateway"]

    with pytest.raises(BlueprintManifestValidationError) as exc_info:
        validate_blueprint_manifest(bad_manifest)

    issues = exc_info.value.issues
    assert any(issue.code == "BLUEPRINT_MODULE_SELF_DEPENDENCY" for issue in issues)
    assert any(issue.path == "modules.1.depends_on.0" for issue in issues)


def test_validate_blueprint_manifest_rejects_cyclic_dependencies() -> None:
    bad_manifest = _valid_manifest()
    bad_manifest["modules"][0]["depends_on"] = ["gateway"]

    with pytest.raises(BlueprintManifestValidationError) as exc_info:
        validate_blueprint_manifest(bad_manifest)

    issues = exc_info.value.issues
    cycle_issues = [issue for issue in issues if issue.code == "BLUEPRINT_DEPENDENCY_CYCLE"]
    assert len(cycle_issues) == 2


def test_validate_blueprint_manifest_rejects_duplicate_module_ids() -> None:
    bad_manifest = _valid_manifest()
    bad_manifest["modules"][1]["id"] = "network"

    with pytest.raises(BlueprintManifestValidationError) as exc_info:
        validate_blueprint_manifest(bad_manifest)

    issues = exc_info.value.issues
    duplicate_issues = [issue for issue in issues if issue.code == "BLUEPRINT_MODULE_ID_DUPLICATE"]
    assert len(duplicate_issues) == 2
