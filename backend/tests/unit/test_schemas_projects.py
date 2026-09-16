"""
BU-013 + CT-031: Unit tests for schemas.projects module.

Tests Pydantic schema validation for project domain:
ProjectCreate, ProjectUpdate, VariableDefaultsUpdate, ProjectDependencyItem,
TransferOwnershipRequest, ProjectVariablesUpdate.
Includes negative validation tests (CT-031).
"""

import pytest
from pydantic import ValidationError

from schemas.projects import (
    DeployAllRequest,
    DestroyAllRequest,
    ProjectCreate,
    ProjectDependencyItem,
    ProjectListItem,
    ProjectListResponse,
    ProjectMutationResponse,
    ProjectUpdate,
    ProjectVariablesUpdate,
    SuccessResponse,
    TransferOwnershipRequest,
    VariableDefaultsUpdate,
)


class TestProjectCreate:
    def test_minimal_create(self):
        req = ProjectCreate(name="My Project")
        assert req.name == "My Project"
        assert req.environment == "dev"  # default
        assert req.backend_type == "local"  # default
        assert req.color == "#a8337a"  # default

    def test_full_create(self):
        req = ProjectCreate(
            name="Prod-AWS",
            description="Production deployment",
            project_type="cloud-aws",
            cloud_provider="aws",
            environment="prod",
            region="us-east-1",
            backend_type="s3",
            color="#ff0000",
            icon="cloud",
        )
        assert req.region == "us-east-1"
        assert req.cloud_provider == "aws"

    def test_full_create_ibm(self):
        # IBM Cloud resource names must be lowercase alphanumeric + hyphens,
        # per is_ibm_safe_name. Uppercase project names are rejected.
        req = ProjectCreate(
            name="prod-ibm",
            description="IBM deployment",
            project_type="cloud-ibm",
            cloud_provider="ibm",
            environment="prod",
            region="us-south",
        )
        assert req.region == "us-south"
        assert req.cloud_provider == "ibm"

    def test_ibm_uppercase_name_rejected(self):
        """IBM Cloud resource names must be lowercase; uppercase is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ProjectCreate(
                name="Prod-IBM",
                project_type="cloud-ibm",
                cloud_provider="ibm",
                region="us-south",
            )
        # Error should suggest a slugified form.
        assert "prod-ibm" in str(exc_info.value)

    def test_invalid_ibm_region_type_rejected(self):
        """Region must be a string."""
        with pytest.raises(ValidationError):
            ProjectCreate(
                name="prod-ibm",
                project_type="cloud-ibm",
                cloud_provider="ibm",
                region=123,  # type: ignore[arg-type]
            )

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            ProjectCreate(name="")

    def test_long_name_rejected(self):
        with pytest.raises(ValidationError):
            ProjectCreate(name="x" * 201)

    def test_valid_aws_region_accepted(self):
        req = ProjectCreate(name="Test", region="eu-west-1")
        assert req.region == "eu-west-1"

    def test_invalid_aws_region_type_rejected(self):
        """Region must be a string."""
        with pytest.raises(ValidationError):
            ProjectCreate(name="Test", cloud_provider="aws", region=123)  # type: ignore[arg-type]

    def test_aws_shaped_region_accepted(self):
        """Any AWS-shaped region is accepted (new/private regions)."""
        req = ProjectCreate(name="Test", cloud_provider="aws", region="xx-fake-1")
        assert req.region == "xx-fake-1"

    def test_freeform_region_accepted(self):
        """Non-AWS-pattern regions like 'on-prem' should pass validation."""
        req = ProjectCreate(name="Test", region="on-prem")
        assert req.region == "on-prem"

    def test_none_region_accepted(self):
        req = ProjectCreate(name="Test", region=None)
        assert req.region is None


class TestProjectUpdate:
    def test_all_fields_optional(self):
        """Update schema accepts empty (all None)."""
        req = ProjectUpdate()
        assert req.name is None
        assert req.environment is None

    def test_partial_update(self):
        req = ProjectUpdate(name="Updated", environment="staging")
        assert req.name == "Updated"
        assert req.environment == "staging"

    def test_invalid_region_type_rejected(self):
        with pytest.raises(ValidationError):
            ProjectUpdate(cloud_provider="aws", region=123)  # type: ignore[arg-type]

    def test_aws_shaped_region_accepted(self):
        req = ProjectUpdate(cloud_provider="aws", region="xx-fake-1")
        assert req.region == "xx-fake-1"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            ProjectUpdate(name="")


class TestVariableDefaultsUpdate:
    def test_valid_defaults(self):
        req = VariableDefaultsUpdate(defaults={"key": "value"})
        assert req.defaults["key"] == "value"

    def test_cidr_validation_runs(self):
        """CIDR-keyed variables should be validated."""
        with pytest.raises(ValidationError):
            VariableDefaultsUpdate(defaults={"vpc_cidr": "invalid"})

    def test_valid_cidr_accepted(self):
        req = VariableDefaultsUpdate(defaults={"vpc_cidr": "10.0.0.0/16"})
        assert req.defaults["vpc_cidr"] == "10.0.0.0/16"


class TestProjectDependencyItem:
    def test_valid_dependency(self):
        dep = ProjectDependencyItem(project_id=5, outputs=["vpc_id", "subnet_ids"])
        assert dep.project_id == 5
        assert len(dep.outputs) == 2

    def test_zero_project_id_rejected(self):
        with pytest.raises(ValidationError):
            ProjectDependencyItem(project_id=0)

    def test_negative_project_id_rejected(self):
        with pytest.raises(ValidationError):
            ProjectDependencyItem(project_id=-1)

    def test_empty_outputs_default(self):
        dep = ProjectDependencyItem(project_id=1)
        assert dep.outputs == []


class TestResponseSchemas:
    def test_success_response(self):
        resp = SuccessResponse(message="done")
        assert resp.success is True

    def test_project_mutation_response(self):
        resp = ProjectMutationResponse(project_id=1, name="Test", message="created")
        assert resp.project_id == 1

    def test_deploy_all_defaults_parallel(self):
        req = DeployAllRequest()
        assert req.parallel is False

    def test_destroy_all_defaults_parallel(self):
        req = DestroyAllRequest()
        assert req.parallel is False

    def test_destroy_all_defaults_force_destroy_false(self):
        """force_destroy defaults to False (#329)."""
        req = DestroyAllRequest()
        assert req.force_destroy is False

    def test_destroy_all_force_destroy_true(self):
        """force_destroy=True is accepted."""
        req = DestroyAllRequest(force_destroy=True)
        assert req.force_destroy is True

    def test_destroy_all_force_destroy_with_other_fields(self):
        """force_destroy can be combined with other fields."""
        req = DestroyAllRequest(parallel=False, force_destroy=True)
        assert req.force_destroy is True
        assert req.parallel is False

    def test_project_list_response(self):
        resp = ProjectListResponse(projects=[], total=0)
        assert resp.total == 0


# =====================================================================
# CT-031: Negative schema tests — wrong payload shapes → ValidationError
# =====================================================================


class TestProjectCreateNegative:
    def test_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            ProjectCreate()  # type: ignore[call-arg]

    def test_name_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ProjectCreate(name=123)  # type: ignore[arg-type]

    def test_name_as_list_rejected(self):
        with pytest.raises(ValidationError):
            ProjectCreate(name=["a", "b"])  # type: ignore[arg-type]

    def test_state_config_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ProjectCreate(name="Test", state_config="not a dict")  # type: ignore[arg-type]

    def test_credential_template_id_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ProjectCreate(name="Test", credential_template_id="abc")  # type: ignore[arg-type]


class TestProjectUpdateNegative:
    def test_name_as_list_rejected(self):
        with pytest.raises(ValidationError):
            ProjectUpdate(name=["a"])  # type: ignore[arg-type]

    def test_enabled_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ProjectUpdate(enabled="not-a-bool")  # type: ignore[arg-type]

    def test_state_config_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ProjectUpdate(state_config="not a dict")  # type: ignore[arg-type]


class TestTransferOwnershipRequestNegative:
    def test_valid_transfer(self):
        req = TransferOwnershipRequest(new_owner_id=5)
        assert req.new_owner_id == 5

    def test_missing_new_owner_id_rejected(self):
        with pytest.raises(ValidationError):
            TransferOwnershipRequest()  # type: ignore[call-arg]

    def test_new_owner_id_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            TransferOwnershipRequest(new_owner_id="not-an-int")  # type: ignore[arg-type]


class TestProjectVariablesUpdateNegative:
    def test_valid_update(self):
        req = ProjectVariablesUpdate(variables={"key": "value"})
        assert req.variables["key"] == "value"

    def test_missing_variables_rejected(self):
        with pytest.raises(ValidationError):
            ProjectVariablesUpdate()  # type: ignore[call-arg]

    def test_variables_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ProjectVariablesUpdate(variables="not a dict")  # type: ignore[arg-type]

    def test_variables_as_list_rejected(self):
        with pytest.raises(ValidationError):
            ProjectVariablesUpdate(variables=[1, 2, 3])  # type: ignore[arg-type]


class TestVariableDefaultsUpdateNegative:
    def test_missing_defaults_rejected(self):
        with pytest.raises(ValidationError):
            VariableDefaultsUpdate()  # type: ignore[call-arg]

    def test_defaults_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            VariableDefaultsUpdate(defaults="not a dict")  # type: ignore[arg-type]
