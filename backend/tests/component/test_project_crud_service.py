"""
BC-002: Component tests for ProjectCRUDService — DB-backed project CRUD.

Tests create, list, detail, update, delete, and activate operations
against a real SQLite in-memory database.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, ConflictError, NotFoundError
from services.project_crud_service import ProjectCRUDService

# ── Helpers ──────────────────────────────────────────────────────────


def _make_create_data(**overrides):
    """Build a SimpleNamespace ProjectCreate-like object."""
    defaults = {
        "name": "Test Project",
        "description": "A test project",
        "project_type": "kubernetes",
        "cloud_provider": "on-prem",
        "environment": "dev",
        "backend_type": "local",
        "region": None,
        "color": "#a8337a",
        "icon": "cloud",
        "credential_template_id": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _UpdateData:
    """Bare update object — hasattr only returns True for attrs explicitly set."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_update_data(**overrides):
    """Build an update-like object.

    credential_template_id is excluded by default so hasattr(...) returns False
    unless explicitly provided.
    """
    defaults = {
        "name": None,
        "description": None,
        "color": None,
        "icon": None,
        "project_type": None,
        "cloud_provider": None,
        "backend_type": None,
        "region": None,
        "state_config": None,
        "enabled": None,
    }
    defaults.update(overrides)
    return _UpdateData(**defaults)


# ── Create ───────────────────────────────────────────────────────────


class TestProjectCreate:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_creates_project(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(name="My Project"))
        assert result["success"] is True
        assert result["project_id"] is not None
        assert result["name"] == "My Project"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_duplicate_name_raises_conflict(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        svc.create_project(_make_create_data(name="Unique Name"))
        with pytest.raises(ConflictError):
            svc.create_project(_make_create_data(name="Unique Name"))

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_type_inferred_from_provider_aws(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="AWS Project",
            project_type="cloud-aws",
            cloud_provider=None,
        ))
        # Verify the project was created and provider was inferred
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.cloud_provider == "aws"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_defaults_applied(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="Defaults Project",
            environment=None,
            backend_type=None,
            color=None,
            icon=None,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.environment == "dev"
        assert project.backend_type == "local"
        assert project.color == "#a8337a"


# ── List ─────────────────────────────────────────────────────────────


class TestProjectList:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_list_empty(self, mock_cache, mock_inv, db):
        mock_cache.get.return_value = None
        svc = ProjectCRUDService(db)
        result = svc.list_projects()
        assert result["projects"] == []
        assert result["total"] == 0

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_list_returns_all(self, mock_cache, mock_inv, db):
        mock_cache.get.return_value = None
        svc = ProjectCRUDService(db)
        svc.create_project(_make_create_data(name="Alpha"))
        svc.create_project(_make_create_data(name="Beta"))
        result = svc.list_projects()
        assert result["total"] == 2
        names = [p["name"] for p in result["projects"]]
        assert "Alpha" in names
        assert "Beta" in names

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_list_ordered_by_name(self, mock_cache, mock_inv, db):
        mock_cache.get.return_value = None
        svc = ProjectCRUDService(db)
        svc.create_project(_make_create_data(name="Zebra"))
        svc.create_project(_make_create_data(name="Apple"))
        result = svc.list_projects()
        names = [p["name"] for p in result["projects"]]
        assert names == sorted(names)


# ── Detail ───────────────────────────────────────────────────────────


class TestProjectDetail:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_get_detail(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        created = svc.create_project(_make_create_data(name="Detail Project"))
        detail = svc.get_project_detail(created["project_id"])
        assert detail["name"] == "Detail Project"
        assert "id" in detail
        assert "created_at" in detail

    def test_get_nonexistent_raises(self, db):
        svc = ProjectCRUDService(db)
        with pytest.raises(NotFoundError):
            svc.get_project_detail(99999)


# ── Update ───────────────────────────────────────────────────────────


class TestProjectUpdate:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_update_name(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        created = svc.create_project(_make_create_data(name="Old Name"))
        result = svc.update_project(
            created["project_id"],
            _make_update_data(name="New Name"),
        )
        assert result["success"] is True
        detail = svc.get_project_detail(created["project_id"])
        assert detail["name"] == "New Name"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_update_duplicate_name_raises(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        svc.create_project(_make_create_data(name="Existing"))
        second = svc.create_project(_make_create_data(name="ToRename"))
        with pytest.raises(ConflictError):
            svc.update_project(
                second["project_id"],
                _make_update_data(name="Existing"),
            )

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_update_description(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        created = svc.create_project(_make_create_data(name="Desc Test"))
        svc.update_project(
            created["project_id"],
            _make_update_data(description="Updated description"),
        )
        detail = svc.get_project_detail(created["project_id"])
        assert detail["description"] == "Updated description"

    def test_update_nonexistent_raises(self, db):
        svc = ProjectCRUDService(db)
        with pytest.raises(NotFoundError):
            svc.update_project(99999, _make_update_data(name="Nope"))


# ── Delete ───────────────────────────────────────────────────────────


class TestProjectDelete:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_delete_inactive_project(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        # Create 2 projects, make first inactive
        p1 = svc.create_project(_make_create_data(name="ToDelete"))
        svc.create_project(_make_create_data(name="KeepThis"))
        from models import Project
        project = db.query(Project).filter(Project.id == p1["project_id"]).first()
        project.is_active = False
        db.commit()

        with patch("services.project_crud_service.ProjectCRUDService._check_workspace_locks"), \
             patch("services.project_crud_service.ProjectCRUDService._cleanup_workspaces"):
            result = svc.delete_project(p1["project_id"])
        assert result["success"] is True

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_delete_active_project_with_others_raises(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        p1 = svc.create_project(_make_create_data(name="Active"))
        svc.create_project(_make_create_data(name="Other"))
        from models import Project
        project = db.query(Project).filter(Project.id == p1["project_id"]).first()
        project.is_active = True
        db.commit()

        with pytest.raises(BadRequestError, match="active project"):
            svc.delete_project(p1["project_id"])

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_force_delete_only_project(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        p1 = svc.create_project(_make_create_data(name="OnlyProject"))
        from models import Project
        project = db.query(Project).filter(Project.id == p1["project_id"]).first()
        project.is_active = True
        db.commit()

        with patch("services.project_crud_service.ProjectCRUDService._check_workspace_locks"), \
             patch("services.project_crud_service.ProjectCRUDService._cleanup_workspaces"):
            result = svc.delete_project(p1["project_id"], force=True)
        assert result["success"] is True

    def test_delete_nonexistent_raises(self, db):
        svc = ProjectCRUDService(db)
        with pytest.raises(NotFoundError):
            svc.delete_project(99999)


# ── Activate ─────────────────────────────────────────────────────────


class TestProjectActivate:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_activate_deactivates_others(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        p1 = svc.create_project(_make_create_data(name="Project A"))
        p2 = svc.create_project(_make_create_data(name="Project B"))

        result = svc.activate_project(p2["project_id"])
        assert result["success"] is True
        assert result["name"] == "Project B"

        from models import Project
        proj_a = db.query(Project).filter(Project.id == p1["project_id"]).first()
        proj_b = db.query(Project).filter(Project.id == p2["project_id"]).first()
        assert proj_b.is_active is True
        assert proj_a.is_active is False

    def test_activate_nonexistent_raises(self, db):
        svc = ProjectCRUDService(db)
        with pytest.raises(NotFoundError):
            svc.activate_project(99999)


# ── Get Active ───────────────────────────────────────────────────────


class TestGetActiveProject:
    def test_no_active_project(self, db):
        svc = ProjectCRUDService(db)
        result = svc.get_active_project()
        assert result["active"] is False
        assert result["project"] is None

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_with_active_project(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        p = svc.create_project(_make_create_data(name="ActiveOne"))
        svc.activate_project(p["project_id"])
        result = svc.get_active_project()
        assert result["active"] is True
        assert result["project"]["name"] == "ActiveOne"


# ── Update Dependencies ──────────────────────────────────────────────


class TestUpdateDependencies:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_set_dependencies(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        p1 = svc.create_project(_make_create_data(name="Dep Source"))
        p2 = svc.create_project(_make_create_data(name="Dep Target"))

        dep = MagicMock()
        dep.project_id = p1["project_id"]
        dep.model_dump.return_value = {"project_id": p1["project_id"], "outputs": []}

        result = svc.update_dependencies(p2["project_id"], [dep])
        assert result["success"] is True

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_self_dependency_raises(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        p = svc.create_project(_make_create_data(name="Self Dep"))

        dep = MagicMock()
        dep.project_id = p["project_id"]

        with pytest.raises(BadRequestError, match="circular"):
            svc.update_dependencies(p["project_id"], [dep])

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_nonexistent_dependency_raises(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        p = svc.create_project(_make_create_data(name="Has Dep"))

        dep = MagicMock()
        dep.project_id = 99999

        with pytest.raises(NotFoundError):
            svc.update_dependencies(p["project_id"], [dep])

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_empty_dependencies_list(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        p = svc.create_project(_make_create_data(name="Empty Deps"))
        result = svc.update_dependencies(p["project_id"], [])
        assert result["success"] is True


# ── Type and Provider Inference ──────────────────────────────────────


class TestTypeProviderInference:
    """Tests for _resolve_type_and_provider with credential templates in DB."""

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_infer_type_from_credential_template_aws(self, mock_cache, mock_inv, db):
        """When no project_type given, infer from credential template provider."""
        from models import CloudCredentialTemplate

        template = CloudCredentialTemplate(
            name="AWS Prod Creds",
            provider="aws",
            region="us-east-1",
        )
        db.add(template)
        db.flush()

        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="InferType AWS",
            project_type=None,
            cloud_provider=None,
            credential_template_id=template.id,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.project_type == "cloud-aws"
        assert project.cloud_provider == "aws"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_infer_type_from_credential_template_ssh(self, mock_cache, mock_inv, db):
        """SSH template → kubernetes type, on-prem provider."""
        from models import CloudCredentialTemplate

        template = CloudCredentialTemplate(
            name="SSH On-Prem",
            provider="ssh",
        )
        db.add(template)
        db.flush()

        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="InferType SSH",
            project_type=None,
            cloud_provider=None,
            credential_template_id=template.id,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.project_type == "kubernetes"
        assert project.cloud_provider == "on-prem"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_type_azure_infers_provider(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="Azure Infer",
            project_type="cloud-azure",
            cloud_provider=None,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.cloud_provider == "azure"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_type_gcp_infers_provider(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="GCP Infer",
            project_type="cloud-gcp",
            cloud_provider=None,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.cloud_provider == "gcp"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_type_ibm_infers_provider(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="IBM Infer",
            project_type="cloud-ibm",
            cloud_provider=None,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.cloud_provider == "ibm"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_infer_type_from_credential_template_ibm(self, mock_cache, mock_inv, db):
        from models import CloudCredentialTemplate

        template = CloudCredentialTemplate(
            name="IBM Prod Creds",
            provider="ibm",
            region="us-south",
        )
        db.add(template)
        db.flush()

        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="InferType IBM",
            project_type=None,
            cloud_provider=None,
            credential_template_id=template.id,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.project_type == "cloud-ibm"
        assert project.cloud_provider == "ibm"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_type_kubernetes_infers_on_prem(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="K8s Infer",
            project_type="kubernetes",
            cloud_provider=None,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.cloud_provider == "on-prem"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_explicit_provider_overrides_inference(self, mock_cache, mock_inv, db):
        """If both type and provider given, provider is kept as-is."""
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="Explicit Provider",
            project_type="cloud-aws",
            cloud_provider="custom",
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.cloud_provider == "custom"


# ── Region Resolution ────────────────────────────────────────────────


class TestRegionResolution:
    """Tests for _resolve_region with credential template region inheritance."""

    @patch("services.project_crud_service.get_default", return_value="us-east-1")
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_inherits_region_from_template(self, mock_cache, mock_inv, mock_default, db):
        from models import CloudCredentialTemplate

        template = CloudCredentialTemplate(
            name="EU Template",
            provider="aws",
            region="eu-west-1",
        )
        db.add(template)
        db.flush()

        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="Region Inherit",
            project_type="cloud-aws",
            cloud_provider="aws",
            region=None,
            credential_template_id=template.id,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.region == "eu-west-1"

    @patch("services.project_crud_service.get_default", return_value="us-east-1")
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_explicit_region_overrides_template(self, mock_cache, mock_inv, mock_default, db):
        from models import CloudCredentialTemplate

        template = CloudCredentialTemplate(
            name="EU Template 2",
            provider="aws",
            region="eu-west-1",
        )
        db.add(template)
        db.flush()

        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="Region Override",
            project_type="cloud-aws",
            cloud_provider="aws",
            region="ap-southeast-1",
            credential_template_id=template.id,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.region == "ap-southeast-1"

    @patch("services.project_crud_service.get_default", return_value="us-east-1")
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_no_region_no_template_uses_default(self, mock_cache, mock_inv, mock_default, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="Default Region",
            project_type="cloud-aws",
            cloud_provider="aws",
            region=None,
            credential_template_id=None,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.region == "us-east-1"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_non_cloud_project_keeps_region_none(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="Non Cloud Region",
            project_type="kubernetes",
            cloud_provider="on-prem",
            region=None,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.region is None

    @patch("services.project_crud_service.get_default", return_value="us-south")
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_ibm_project_uses_ibm_default_region(self, mock_cache, mock_inv, mock_default, db):
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="IBM Default Region",
            project_type="cloud-ibm",
            cloud_provider="ibm",
            region=None,
        ))
        from models import Project
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.region == "us-south"


# ── Update Advanced ──────────────────────────────────────────────────


class TestProjectUpdateAdvanced:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_update_color_and_icon(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        created = svc.create_project(_make_create_data(name="Style Update"))
        svc.update_project(
            created["project_id"],
            _make_update_data(color="#00ff00", icon="rocket"),
        )
        detail = svc.get_project_detail(created["project_id"])
        assert detail["color"] == "#00ff00"
        assert detail["icon"] == "rocket"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_update_backend_type(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        created = svc.create_project(_make_create_data(name="Backend Update"))
        svc.update_project(
            created["project_id"],
            _make_update_data(backend_type="s3"),
        )
        detail = svc.get_project_detail(created["project_id"])
        assert detail["backend_type"] == "s3"

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_update_enabled_sets_is_active(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        created = svc.create_project(_make_create_data(name="Enable Toggle"))
        svc.update_project(
            created["project_id"],
            _make_update_data(enabled=False),
        )
        detail = svc.get_project_detail(created["project_id"])
        assert detail["is_active"] is False

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_update_with_credential_template_id(self, mock_cache, mock_inv, db):
        from models import CloudCredentialTemplate

        template = CloudCredentialTemplate(
            name="Update Cred Template",
            provider="aws",
            region="us-west-2",
        )
        db.add(template)
        db.flush()

        svc = ProjectCRUDService(db)
        created = svc.create_project(_make_create_data(name="Cred Update"))
        update_data = _make_update_data()
        update_data.credential_template_id = template.id
        svc.update_project(created["project_id"], update_data)
        detail = svc.get_project_detail(created["project_id"])
        assert detail["credential_template_id"] == template.id


# ── Delete Advanced ──────────────────────────────────────────────────


class TestProjectDeleteAdvanced:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_only_project_no_force_raises(self, mock_cache, mock_inv, db):
        """Single active project without force=True should raise."""
        svc = ProjectCRUDService(db)
        p = svc.create_project(_make_create_data(name="OnlyOne"))
        from models import Project
        project = db.query(Project).filter(Project.id == p["project_id"]).first()
        project.is_active = True
        db.commit()

        with pytest.raises(BadRequestError, match="only project"):
            svc.delete_project(p["project_id"])

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_workspace_lock_blocks_delete(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        p = svc.create_project(_make_create_data(name="Locked Project"))
        from models import Project
        project = db.query(Project).filter(Project.id == p["project_id"]).first()
        project.is_active = False
        db.commit()

        with patch("services.project_crud_service.ProjectCRUDService._check_workspace_locks") as mock_lock:
            mock_lock.side_effect = ConflictError("project", "Cannot delete: operations in progress")
            with pytest.raises(ConflictError, match="operations in progress"):
                svc.delete_project(p["project_id"])


# ── Serialization ────────────────────────────────────────────────────


class TestSerialization:
    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_list_item_has_expected_keys(self, mock_cache, mock_inv, db):
        mock_cache.get.return_value = None
        svc = ProjectCRUDService(db)
        svc.create_project(_make_create_data(name="Full Keys"))
        result = svc.list_projects()
        item = result["projects"][0]
        expected_keys = {
            "id", "name", "description", "color", "icon", "is_active",
            "has_deployments", "module_count", "deployed_count", "failed_count",
            "project_type", "cloud_provider", "backend_type", "region",
            "created_at",
        }
        assert expected_keys.issubset(set(item.keys()))

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_detail_has_updated_at(self, mock_cache, mock_inv, db):
        svc = ProjectCRUDService(db)
        created = svc.create_project(_make_create_data(name="Detail Keys"))
        detail = svc.get_project_detail(created["project_id"])
        assert "updated_at" in detail

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_list_item_has_deployments_false_by_default(self, mock_cache, mock_inv, db):
        mock_cache.get.return_value = None
        svc = ProjectCRUDService(db)
        svc.create_project(_make_create_data(name="No Deployments"))
        result = svc.list_projects()
        assert result["projects"][0]["has_deployments"] is False


# ── Default credential template binding on create ─────────────────────


class TestDefaultCredentialTemplateBinding:
    """
    create_project must bind the is_default credential template when
    no explicit credential_template_id is supplied.
    """

    def _make_default_template(self, db):
        from models import CloudCredentialTemplate
        template = CloudCredentialTemplate(
            name="AWS Default",
            provider="aws",
            is_default=True,
            aws_auth_method="access_keys",
            aws_access_key_id="AKIA_DEFAULT",
        )
        db.add(template)
        db.flush()
        return template

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_create_project_binds_default_template_when_none_supplied(self, mock_cache, mock_inv, db):
        """No credential_template_id in create data → project.credential_template_id
        must equal the is_default template's id.
        """
        from models import Project
        template = self._make_default_template(db)
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="Auto Bound",
            cloud_provider="aws",
            credential_template_id=None,
        ))
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.credential_template_id == template.id

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_create_project_explicit_template_not_overridden(self, mock_cache, mock_inv, db):
        """Explicit credential_template_id must not be replaced by the default."""
        from models import CloudCredentialTemplate, Project
        default_tpl = self._make_default_template(db)
        explicit_tpl = CloudCredentialTemplate(
            name="Explicit", provider="aws", is_default=False,
            aws_auth_method="access_keys", aws_access_key_id="AKIA_EXPLICIT",
        )
        db.add(explicit_tpl)
        db.flush()

        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="Explicitly Bound",
            cloud_provider="aws",
            credential_template_id=explicit_tpl.id,
        ))
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.credential_template_id == explicit_tpl.id
        assert project.credential_template_id != default_tpl.id

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_create_project_no_default_template_does_not_fail(self, mock_cache, mock_inv, db):
        """If no is_default template exists, create_project must not raise."""
        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="No Default Template",
            cloud_provider="aws",
            credential_template_id=None,
        ))
        assert result["success"] is True

    @patch("services.project_crud_service.invalidate_cache")
    @patch("services.project_crud_service.cache")
    def test_create_project_wrong_provider_default_not_bound(self, mock_cache, mock_inv, db):
        """An AWS project with only a GCP is_default template must NOT bind that template.

        Cross-provider default binding would inject GCP credentials into an AWS
        project, causing a 401 with a misleading error.
        """
        from models import CloudCredentialTemplate, Project
        gcp_default = CloudCredentialTemplate(
            name="GCP Default",
            provider="gcp",
            is_default=True,
        )
        db.add(gcp_default)
        db.flush()

        svc = ProjectCRUDService(db)
        result = svc.create_project(_make_create_data(
            name="AWS No Bind",
            project_type="cloud-aws",
            cloud_provider="aws",
            credential_template_id=None,
        ))
        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        # The GCP default template must NOT be bound to this AWS project
        assert project.credential_template_id is None
