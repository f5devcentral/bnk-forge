"""
BU-014: Unit tests for schemas.stacks module.

Tests stack template, instance, preview, and custom operation schemas
with real Pydantic validation — no mocks.
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from schemas.stacks import (
    DuplicateTemplateRequest,
    ImportTemplateRequest,
    PublishTemplateRequest,
    SaveAsTemplateRequest,
    StackInstanceCreate,
    StackInstanceModuleResponse,
    StackInstanceResponse,
    StackPreviewResponse,
    StackStatusResponse,
    StackTemplateListResponse,
    StackTemplateModuleResponse,
    StackTemplateResponse,
)

# ── Stack Template Response ──────────────────────────────────────────


class TestStackTemplateResponse:
    def test_full_template(self):
        now = datetime.now()
        t = StackTemplateResponse(
            id=1,
            name="VPC + EKS",
            slug="vpc-eks",
            description="Full AWS stack",
            category="aws",
            cloud_provider="aws",
            icon="cloud",
            color="#FF6600",
            estimated_time="30 min",
            estimated_cost="$50/mo",
            difficulty="intermediate",
            modules=[{"id": 1, "name": "vpc"}],
            variable_templates={"vpc_cidr": "10.0.0.0/16"},
            prerequisites=["AWS credentials"],
            tags=["aws", "networking"],
            is_active=True,
            is_featured=True,
            version="2.0.0",
            created_by="admin",
            is_public=True,
            forked_from=None,
            created_at=now,
            updated_at=now,
        )
        assert t.name == "VPC + EKS"
        assert t.is_featured is True
        assert t.version == "2.0.0"

    def test_optional_fields_default_to_none(self):
        now = datetime.now()
        t = StackTemplateResponse(
            id=1,
            name="Minimal",
            slug="minimal",
            description=None,
            category=None,
            cloud_provider=None,
            icon=None,
            color=None,
            estimated_time=None,
            estimated_cost=None,
            difficulty=None,
            modules=None,
            variable_templates=None,
            prerequisites=None,
            tags=None,
            is_active=True,
            is_featured=False,
            created_at=now,
            updated_at=now,
        )
        assert t.description is None
        assert t.version == "1.0.0"  # default
        assert t.created_by == "system"  # default
        assert t.is_public is False  # default
        assert t.forked_from is None  # default


class TestStackTemplateListResponse:
    def test_list_item(self):
        t = StackTemplateListResponse(
            id=5,
            name="Simple S3",
            slug="simple-s3",
            description="Just an S3 bucket",
            category="storage",
            cloud_provider="aws",
            icon=None,
            color=None,
            estimated_time="5 min",
            estimated_cost="$1/mo",
            difficulty="easy",
            tags=["storage"],
            is_active=True,
            is_featured=False,
        )
        assert t.id == 5
        assert t.tags == ["storage"]


# ── Stack Preview ────────────────────────────────────────────────────


class TestStackPreviewResponse:
    def test_preview(self):
        p = StackPreviewResponse(
            modules=[{"id": 1, "name": "vpc"}, {"id": 2, "name": "eks"}],
            prerequisites=["AWS account"],
            estimated_cost="$100/mo",
            estimated_time="45 min",
            total_modules=2,
        )
        assert p.total_modules == 2
        assert len(p.modules) == 2

    def test_preview_minimal(self):
        p = StackPreviewResponse(
            modules=[],
            prerequisites=None,
            estimated_cost=None,
            estimated_time=None,
            total_modules=0,
        )
        assert p.total_modules == 0


class TestStackTemplateModuleResponse:
    def test_module_with_capability_metadata(self):
        module = StackTemplateModuleResponse(
            path="packs/ansible/app",
            name="app",
            required=True,
            engine_type="ansible",
            lifecycle_capabilities={
                "supports_init": True,
                "supports_plan": False,
                "supports_apply": True,
                "supports_destroy": False,
                "supports_get_outputs": True,
            },
            order=1,
        )

        assert module.engine_type == "ansible"
        assert module.lifecycle_capabilities is not None
        assert module.lifecycle_capabilities["supports_destroy"] is False
        assert module.model_dump().get("order") == 1


class TestStackInstanceModuleResponse:
    def test_instance_module_with_capability_metadata(self):
        module = StackInstanceModuleResponse(
            id=1,
            name="ansible-pack",
            path="packs/ansible/instance",
            status="applied",
            deployment_order=1,
            engine_type="ansible",
            lifecycle_capabilities={
                "supports_init": True,
                "supports_plan": False,
                "supports_apply": True,
                "supports_destroy": False,
            },
            extra_debug="ok",
        )

        assert module.engine_type == "ansible"
        assert module.lifecycle_capabilities is not None
        assert module.lifecycle_capabilities["supports_destroy"] is False
        assert module.model_dump().get("extra_debug") == "ok"


# ── Stack Instance Create ────────────────────────────────────────────


class TestStackInstanceCreate:
    def test_valid_create(self):
        s = StackInstanceCreate(
            template_id=1,
            name="My VPC Stack",
            variables={"vpc_cidr": "10.0.0.0/16"},
        )
        assert s.template_id == 1
        assert s.name == "My VPC Stack"

    def test_default_variables(self):
        s = StackInstanceCreate(template_id=1, name="Test")
        assert s.variables is None

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            StackInstanceCreate(template_id=1, name="")

    def test_long_name_rejected(self):
        with pytest.raises(ValidationError):
            StackInstanceCreate(template_id=1, name="x" * 256)

    def test_cidr_validation_runs(self):
        """The @model_validator calls validate_cidr_fields on variables."""
        with pytest.raises(ValidationError):
            StackInstanceCreate(
                template_id=1,
                name="Bad CIDR",
                variables={"vpc_cidr": "not-a-cidr"},
            )

    def test_valid_cidr_in_variables(self):
        s = StackInstanceCreate(
            template_id=1,
            name="Good CIDR",
            variables={"vpc_cidr": "10.0.0.0/16", "other_key": "ignored"},
        )
        assert s.variables["vpc_cidr"] == "10.0.0.0/16"


# ── Stack Instance Response ──────────────────────────────────────────


class TestStackInstanceResponse:
    def test_full_response(self):
        now = datetime.now()
        r = StackInstanceResponse(
            id=1,
            project_id=10,
            template_id=3,
            name="Production VPC",
            status="deploying",
            variables={"region": "us-west-2"},
            deployed_modules=[1, 2, 3],
            current_step=2,
            total_steps=5,
            started_at=now,
            completed_at=None,
            error_message=None,
            created_at=now,
            updated_at=now,
        )
        assert r.status == "deploying"
        assert r.current_step == 2
        assert len(r.deployed_modules) == 3


class TestStackStatusResponse:
    def test_status(self):
        s = StackStatusResponse(
            current_step=3,
            total_steps=5,
            status="in_progress",
            modules=[{"id": 1, "status": "applied"}, {"id": 2, "status": "pending"}],
        )
        assert s.current_step == 3
        assert len(s.modules) == 2


# ── Custom Stack Operations ──────────────────────────────────────────


class TestSaveAsTemplateRequest:
    def test_valid(self):
        r = SaveAsTemplateRequest(name="My Custom Stack")
        assert r.name == "My Custom Stack"
        assert r.category == "custom"  # default
        assert r.version == "1.0.0"  # default
        assert r.is_public is False  # default
        assert r.tags is None  # default=None (Optional field)

    def test_full_fields(self):
        r = SaveAsTemplateRequest(
            name="Enterprise Stack",
            description="Full enterprise setup",
            category="enterprise",
            version="2.0.0",
            is_public=True,
            tags=["enterprise", "production"],
            icon="building",
            color="#0066FF",
        )
        assert r.is_public is True
        assert len(r.tags) == 2

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            SaveAsTemplateRequest(name="")

    def test_long_name_rejected(self):
        with pytest.raises(ValidationError):
            SaveAsTemplateRequest(name="x" * 256)


class TestDuplicateTemplateRequest:
    def test_valid(self):
        r = DuplicateTemplateRequest(name="Copy of VPC Stack")
        assert r.description is None
        assert r.version == "1.0.0"

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            DuplicateTemplateRequest(name="")


class TestImportTemplateRequest:
    def test_valid(self):
        r = ImportTemplateRequest(template_json={"name": "imported", "modules": []})
        assert r.template_json["name"] == "imported"


class TestPublishTemplateRequest:
    def test_publish(self):
        r = PublishTemplateRequest(is_public=True)
        assert r.is_public is True

    def test_unpublish(self):
        r = PublishTemplateRequest(is_public=False)
        assert r.is_public is False


# =====================================================================
# CT-036: Negative schema tests — wrong payload shapes → ValidationError
# =====================================================================


class TestStackInstanceCreateNegative:
    def test_missing_template_id_rejected(self):
        with pytest.raises(ValidationError):
            StackInstanceCreate(name="Test")  # type: ignore[call-arg]

    def test_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            StackInstanceCreate(template_id=1)  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            StackInstanceCreate()  # type: ignore[call-arg]

    def test_template_id_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            StackInstanceCreate(template_id="abc", name="Test")  # type: ignore[arg-type]

    def test_name_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            StackInstanceCreate(template_id=1, name=123)  # type: ignore[arg-type]

    def test_variables_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            StackInstanceCreate(template_id=1, name="Test", variables="not-a-dict")  # type: ignore[arg-type]

    def test_invalid_cidr_in_variables_rejected(self):
        """Model validator calls validate_cidr_fields on variables."""
        with pytest.raises(ValidationError):
            StackInstanceCreate(
                template_id=1, name="Bad", variables={"vpc_cidr": "not-a-cidr"}
            )


class TestSaveAsTemplateRequestNegative:
    def test_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            SaveAsTemplateRequest()  # type: ignore[call-arg]

    def test_name_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            SaveAsTemplateRequest(name=123)  # type: ignore[arg-type]

    def test_tags_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            SaveAsTemplateRequest(name="Test", tags="not-a-list")  # type: ignore[arg-type]

    def test_is_public_wrong_type_rejected(self):
        """Pydantic v2 lax mode coerces truthy strings to bool, but dicts should fail."""
        with pytest.raises(ValidationError):
            SaveAsTemplateRequest(name="Test", is_public={"value": True})  # type: ignore[arg-type]


class TestDuplicateTemplateRequestNegative:
    def test_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            DuplicateTemplateRequest()  # type: ignore[call-arg]

    def test_name_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            DuplicateTemplateRequest(name=123)  # type: ignore[arg-type]

    def test_long_name_rejected(self):
        with pytest.raises(ValidationError):
            DuplicateTemplateRequest(name="x" * 256)


class TestImportTemplateRequestNegative:
    def test_missing_template_json_rejected(self):
        with pytest.raises(ValidationError):
            ImportTemplateRequest()  # type: ignore[call-arg]

    def test_template_json_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ImportTemplateRequest(template_json="not-a-dict")  # type: ignore[arg-type]

    def test_template_json_as_list_rejected(self):
        with pytest.raises(ValidationError):
            ImportTemplateRequest(template_json=[1, 2, 3])  # type: ignore[arg-type]


class TestPublishTemplateRequestNegative:
    def test_missing_is_public_rejected(self):
        with pytest.raises(ValidationError):
            PublishTemplateRequest()  # type: ignore[call-arg]

    def test_is_public_wrong_type_rejected(self):
        """Pydantic v2 lax mode coerces truthy strings to bool, but dicts should fail."""
        with pytest.raises(ValidationError):
            PublishTemplateRequest(is_public={"value": True})  # type: ignore[arg-type]
