"""
CT-038: Negative schema tests for Module, Secret, Snapshot, Promotion schemas.

Tests that AddModuleRequest, UpdateModuleRequest, VariableMappingRequest,
ValueSecretCreate, CreateSnapshotRequest, DiffSnapshotsRequest, and
PromoteRequest reject wrong payload shapes with ValidationError.
"""

import pytest
from pydantic import ValidationError

from routes.config_promotion import PromoteRequest
from routes.project_modules import AddModuleRequest, UpdateModuleRequest
from routes.project_secrets import ValueSecretCreate
from routes.project_variable_mappings import VariableMappingRequest
from routes.snapshots import CreateSnapshotRequest, DiffSnapshotsRequest

# ── AddModuleRequest ─────────────────────────────────────────────────


class TestAddModuleRequestNegative:
    def test_valid_add(self):
        req = AddModuleRequest(module_library_id=1, path_in_project="infra/vpc")
        assert req.module_library_id == 1
        assert req.deployment_order == 0
        assert req.variable_overrides == {}

    def test_missing_module_library_id_rejected(self):
        with pytest.raises(ValidationError):
            AddModuleRequest(path_in_project="infra/vpc")  # type: ignore[call-arg]

    def test_missing_path_rejected(self):
        with pytest.raises(ValidationError):
            AddModuleRequest(module_library_id=1)  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            AddModuleRequest()  # type: ignore[call-arg]

    def test_module_library_id_zero_rejected(self):
        """module_library_id has gt=0."""
        with pytest.raises(ValidationError):
            AddModuleRequest(module_library_id=0, path_in_project="infra/vpc")

    def test_module_library_id_negative_rejected(self):
        with pytest.raises(ValidationError):
            AddModuleRequest(module_library_id=-1, path_in_project="infra/vpc")

    def test_module_library_id_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            AddModuleRequest(module_library_id="abc", path_in_project="infra/vpc")  # type: ignore[arg-type]

    def test_path_empty_rejected(self):
        """path_in_project has min_length=1."""
        with pytest.raises(ValidationError):
            AddModuleRequest(module_library_id=1, path_in_project="")

    def test_path_too_long_rejected(self):
        """path_in_project has max_length=1000."""
        with pytest.raises(ValidationError):
            AddModuleRequest(module_library_id=1, path_in_project="x" * 1001)

    def test_path_traversal_rejected(self):
        """Validator rejects .. in path."""
        with pytest.raises(ValidationError):
            AddModuleRequest(module_library_id=1, path_in_project="../escape")

    def test_absolute_path_rejected(self):
        """Validator rejects paths starting with /."""
        with pytest.raises(ValidationError):
            AddModuleRequest(module_library_id=1, path_in_project="/absolute/path")

    def test_special_chars_in_path_rejected(self):
        """Validator rejects paths not matching ^[a-zA-Z0-9/_-]+$."""
        with pytest.raises(ValidationError):
            AddModuleRequest(module_library_id=1, path_in_project="path with spaces")

    def test_deployment_order_negative_rejected(self):
        """deployment_order has ge=0."""
        with pytest.raises(ValidationError):
            AddModuleRequest(
                module_library_id=1, path_in_project="infra/vpc",
                deployment_order=-1,
            )

    def test_deployment_order_too_large_rejected(self):
        """deployment_order has le=9999."""
        with pytest.raises(ValidationError):
            AddModuleRequest(
                module_library_id=1, path_in_project="infra/vpc",
                deployment_order=10000,
            )

    def test_variable_overrides_dangerous_value_rejected(self):
        """Validator rejects values containing dangerous chars like ;, |, &, `, $, (, )."""
        with pytest.raises(ValidationError):
            AddModuleRequest(
                module_library_id=1, path_in_project="infra/vpc",
                variable_overrides={"key": "value; rm -rf /"},
            )

    def test_variable_overrides_shell_injection_rejected(self):
        with pytest.raises(ValidationError):
            AddModuleRequest(
                module_library_id=1, path_in_project="infra/vpc",
                variable_overrides={"key": "$(evil)"},
            )


# ── UpdateModuleRequest ──────────────────────────────────────────────


class TestUpdateModuleRequestNegative:
    def test_valid_all_optional(self):
        req = UpdateModuleRequest()
        assert req.variable_overrides is None
        assert req.deployment_order is None
        assert req.enabled is None

    def test_partial_update_valid(self):
        req = UpdateModuleRequest(enabled=False, deployment_order=5)
        assert req.enabled is False

    def test_variable_overrides_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            UpdateModuleRequest(variable_overrides="not-a-dict")  # type: ignore[arg-type]

    def test_deployment_order_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            UpdateModuleRequest(deployment_order="five")  # type: ignore[arg-type]

    def test_enabled_wrong_type_rejected(self):
        """Pydantic v2 lax mode coerces truthy strings to bool, but dicts should fail."""
        with pytest.raises(ValidationError):
            UpdateModuleRequest(enabled={"value": True})  # type: ignore[arg-type]


# ── VariableMappingRequest ───────────────────────────────────────────


class TestVariableMappingRequestNegative:
    def test_valid_mapping(self):
        req = VariableMappingRequest(
            source_variable_name="vpc_id",
            target_variable_name="network_id",
        )
        assert req.transform_type == "none"
        assert req.is_active is True

    def test_missing_source_rejected(self):
        with pytest.raises(ValidationError):
            VariableMappingRequest(target_variable_name="x")  # type: ignore[call-arg]

    def test_missing_target_rejected(self):
        with pytest.raises(ValidationError):
            VariableMappingRequest(source_variable_name="x")  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            VariableMappingRequest()  # type: ignore[call-arg]

    def test_empty_source_rejected(self):
        """source_variable_name has min_length=1."""
        with pytest.raises(ValidationError):
            VariableMappingRequest(source_variable_name="", target_variable_name="x")

    def test_empty_target_rejected(self):
        """target_variable_name has min_length=1."""
        with pytest.raises(ValidationError):
            VariableMappingRequest(source_variable_name="x", target_variable_name="")

    def test_source_too_long_rejected(self):
        """source_variable_name has max_length=255."""
        with pytest.raises(ValidationError):
            VariableMappingRequest(
                source_variable_name="x" * 256,
                target_variable_name="y",
            )

    def test_source_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            VariableMappingRequest(
                source_variable_name=123, target_variable_name="y"
            )  # type: ignore[arg-type]


# ── ValueSecretCreate ────────────────────────────────────────────────


class TestValueSecretCreateNegative:
    def test_valid_secret(self):
        req = ValueSecretCreate(name="db_password", value="s3cret!")
        assert req.name == "db_password"
        assert req.description is None

    def test_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            ValueSecretCreate(value="s3cret!")  # type: ignore[call-arg]

    def test_missing_value_rejected(self):
        with pytest.raises(ValidationError):
            ValueSecretCreate(name="key")  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            ValueSecretCreate()  # type: ignore[call-arg]

    def test_name_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ValueSecretCreate(name=123, value="secret")  # type: ignore[arg-type]

    def test_value_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ValueSecretCreate(name="key", value=123)  # type: ignore[arg-type]

    def test_with_optional_fields_valid(self):
        req = ValueSecretCreate(
            name="api_key", value="abc123",
            description="API key for external service",
            target_module_path="infra/api",
            target_variable_name="api_key",
        )
        assert req.target_module_path == "infra/api"


# ── CreateSnapshotRequest ────────────────────────────────────────────


class TestCreateSnapshotRequestNegative:
    def test_valid_defaults(self):
        req = CreateSnapshotRequest()
        assert req.label is None
        assert req.cluster_id is None

    def test_with_optional_fields_valid(self):
        req = CreateSnapshotRequest(label="pre-deploy", cluster_id=5)
        assert req.label == "pre-deploy"
        assert req.cluster_id == 5

    def test_cluster_id_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            CreateSnapshotRequest(cluster_id="five")  # type: ignore[arg-type]

    def test_label_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            CreateSnapshotRequest(label=123)  # type: ignore[arg-type]


# ── DiffSnapshotsRequest ────────────────────────────────────────────


class TestDiffSnapshotsRequestNegative:
    def test_valid_diff(self):
        req = DiffSnapshotsRequest(snapshot_a_id=1, snapshot_b_id=2)
        assert req.snapshot_a_id == 1

    def test_missing_snapshot_a_rejected(self):
        with pytest.raises(ValidationError):
            DiffSnapshotsRequest(snapshot_b_id=2)  # type: ignore[call-arg]

    def test_missing_snapshot_b_rejected(self):
        with pytest.raises(ValidationError):
            DiffSnapshotsRequest(snapshot_a_id=1)  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            DiffSnapshotsRequest()  # type: ignore[call-arg]

    def test_snapshot_id_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            DiffSnapshotsRequest(snapshot_a_id="one", snapshot_b_id=2)  # type: ignore[arg-type]


# ── PromoteRequest ───────────────────────────────────────────────────


class TestPromoteRequestNegative:
    def test_valid_promote(self):
        req = PromoteRequest(source_cluster_id=1, target_cluster_id=2)
        assert req.dry_run is True  # default

    def test_missing_source_rejected(self):
        with pytest.raises(ValidationError):
            PromoteRequest(target_cluster_id=2)  # type: ignore[call-arg]

    def test_missing_target_rejected(self):
        with pytest.raises(ValidationError):
            PromoteRequest(source_cluster_id=1)  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            PromoteRequest()  # type: ignore[call-arg]

    def test_source_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            PromoteRequest(source_cluster_id="one", target_cluster_id=2)  # type: ignore[arg-type]

    def test_target_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            PromoteRequest(source_cluster_id=1, target_cluster_id="two")  # type: ignore[arg-type]

    def test_dry_run_wrong_type_rejected(self):
        """Pydantic v2 lax mode coerces truthy strings to bool, but dicts should fail."""
        with pytest.raises(ValidationError):
            PromoteRequest(source_cluster_id=1, target_cluster_id=2, dry_run={"value": True})  # type: ignore[arg-type]

    def test_non_dry_run_valid(self):
        req = PromoteRequest(source_cluster_id=1, target_cluster_id=2, dry_run=False)
        assert req.dry_run is False
