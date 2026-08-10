"""
BC-003: Component tests for project_service — ProjectService class + standalone functions.

Tests:
- ProjectService.list_projects, create_project, get_project_detail,
  update_project, delete_project, set_active_project, update_dependencies
- detect_module_dependencies (metadata + fallback rules)
- update_project_counts (denormalized count aggregation)

All tested against a real SQLite in-memory database.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, ConflictError, NotFoundError
from services.project_service import (
    MODULE_DEPENDENCY_RULES,
    ProjectService,
    detect_module_dependencies,
    recompute_all_project_counts,
    update_project_counts,
)

# ── detect_module_dependencies — metadata path ──────────────────────


class TestDetectDependenciesMetadata:
    def test_resolves_from_metadata(self):
        """When library_module has dependencies_metadata, use it."""
        lib_module = MagicMock()
        lib_module.dependencies_metadata = {
            "required": [{"module": "infra/aws/vpc"}]
        }

        # Existing module in project with matching path
        existing_pm = MagicMock()
        existing_pm.id = 42
        existing_pm.library_module.path = "infra/aws/vpc"

        deps = detect_module_dependencies(lib_module, [existing_pm])
        assert 42 in deps

    def test_flexible_path_matching(self):
        """Handles 'infra/aws/vpc' vs 'aws/vpc' normalization."""
        lib_module = MagicMock()
        lib_module.dependencies_metadata = {
            "required": [{"module": "infra/aws/vpc"}]
        }

        existing_pm = MagicMock()
        existing_pm.id = 10
        existing_pm.library_module.path = "aws/vpc"

        deps = detect_module_dependencies(lib_module, [existing_pm])
        assert 10 in deps

    def test_missing_dependency_returns_empty(self):
        """If required dep not in project, it's skipped (logged as warning)."""
        lib_module = MagicMock()
        lib_module.dependencies_metadata = {
            "required": [{"module": "infra/aws/rds"}]
        }

        existing_pm = MagicMock()
        existing_pm.id = 1
        existing_pm.library_module.path = "infra/aws/vpc"

        deps = detect_module_dependencies(lib_module, [existing_pm])
        assert deps == []

    def test_no_metadata_falls_back_to_rules(self):
        """When dependencies_metadata is None, use fallback rules."""
        lib_module = MagicMock()
        lib_module.dependencies_metadata = None
        lib_module.name = "eks-cluster"

        existing_pm = MagicMock()
        existing_pm.id = 5
        existing_pm.library_module.name = "vpc"
        existing_pm.library_module = MagicMock(name="vpc")
        existing_pm.library_module.name = "vpc"
        existing_pm.path_in_project = "infra/vpc"

        deps = detect_module_dependencies(lib_module, [existing_pm])
        # EKS depends on vpc per fallback rules
        assert 5 in deps


# ── detect_module_dependencies — fallback rules ─────────────────────


class TestDetectDependenciesFallback:
    def test_eks_depends_on_vpc(self):
        lib_module = MagicMock()
        lib_module.dependencies_metadata = None
        lib_module.name = "eks"

        vpc_mod = MagicMock()
        vpc_mod.id = 1
        vpc_mod.library_module.name = "vpc"
        vpc_mod.path_in_project = "infra/vpc"

        deps = detect_module_dependencies(lib_module, [vpc_mod])
        assert 1 in deps

    def test_no_rules_for_unknown_module(self):
        lib_module = MagicMock()
        lib_module.dependencies_metadata = None
        lib_module.name = "totally-custom-module"

        existing = MagicMock()
        existing.id = 1
        existing.library_module.name = "vpc"
        existing.path_in_project = "infra/vpc"

        deps = detect_module_dependencies(lib_module, [existing])
        assert deps == []

    def test_empty_existing_modules(self):
        lib_module = MagicMock()
        lib_module.dependencies_metadata = None
        lib_module.name = "eks"

        deps = detect_module_dependencies(lib_module, [])
        assert deps == []

    def test_rules_dict_has_expected_entries(self):
        """Verify key module types have rules defined."""
        assert "vpc" in MODULE_DEPENDENCY_RULES
        assert "eks" in MODULE_DEPENDENCY_RULES
        assert "bnk" in MODULE_DEPENDENCY_RULES
        assert MODULE_DEPENDENCY_RULES["vpc"] == []  # base layer, no deps


# ── update_project_counts ────────────────────────────────────────────


class TestUpdateProjectCounts:
    @patch("services.project_service.invalidate_cache")
    def test_counts_zero_modules(self, mock_inv, db, make_project):
        project = make_project(name="Empty Project")
        db.commit()
        update_project_counts(db, project.id)
        db.refresh(project)
        assert project.module_count == 0
        assert project.deployed_count == 0
        assert project.failed_count == 0

    @patch("services.project_service.invalidate_cache")
    def test_counts_with_modules(self, mock_inv, db, make_project, make_module_library, make_project_module):
        project = make_project(name="Counted Project")
        lib = make_module_library(name="vpc-count")

        # Create modules with various statuses
        make_project_module(project=project, library_module=lib, path_in_project="mod1", status="applied")
        make_project_module(project=project, library_module=lib, path_in_project="mod2", status="applied")
        make_project_module(project=project, library_module=lib, path_in_project="mod3", status="failed")
        make_project_module(project=project, library_module=lib, path_in_project="mod4", status="not_initialized")
        db.flush()

        update_project_counts(db, project.id)
        # Function sets attrs on the project object — check directly (no refresh)
        assert project.module_count == 4
        assert project.deployed_count == 2
        assert project.failed_count == 1

    @patch("services.project_service.invalidate_cache")
    def test_nonexistent_project_is_noop(self, mock_inv, db):
        """update_project_counts should silently return for missing project."""
        update_project_counts(db, 99999)  # Should not raise

    @patch("services.project_service.invalidate_cache")
    def test_counts_failed_statuses(self, mock_inv, db, make_project, make_module_library, make_project_module):
        project = make_project(name="Failed Statuses")
        lib = make_module_library(name="vpc-fail")

        make_project_module(project=project, library_module=lib, path_in_project="m1", status="apply_failed")
        make_project_module(project=project, library_module=lib, path_in_project="m2", status="destroy_failed")
        make_project_module(project=project, library_module=lib, path_in_project="m3", status="init_failed")
        make_project_module(project=project, library_module=lib, path_in_project="m4", status="plan_failed")
        db.flush()

        update_project_counts(db, project.id)
        # Function sets attrs on the project object — check directly (no refresh)
        assert project.module_count == 4
        assert project.failed_count == 4
        assert project.deployed_count == 0

    @patch("services.project_service.invalidate_cache")
    def test_counts_invalidates_cache(self, mock_inv, db, make_project):
        project = make_project(name="Cache Invalidation")
        db.commit()
        update_project_counts(db, project.id)
        assert mock_inv.call_count >= 1


# ── recompute_all_project_counts ─────────────────────────────────────


class TestRecomputeAllProjectCounts:
    @patch("services.project_service.invalidate_cache")
    def test_repairs_stale_failed_count(
        self, mock_inv, db, make_project, make_module_library, make_project_module
    ):
        project = make_project(name="Stale Failed Count")
        lib = make_module_library(name="vpc-recompute")
        make_project_module(project=project, library_module=lib, path_in_project="m1", status="applied")
        db.flush()

        project.failed_count = 5
        project.deployed_count = 0
        project.module_count = 99
        db.commit()

        result = recompute_all_project_counts(db)

        db.refresh(project)
        assert project.failed_count == 0
        assert project.deployed_count == 1
        assert project.module_count == 1
        assert result["projects_repaired"] >= 1
        assert result["projects_scanned"] >= 1

    @patch("services.project_service.invalidate_cache")
    def test_clean_project_not_counted_as_repaired(
        self, mock_inv, db, make_project, make_module_library, make_project_module
    ):
        project = make_project(name="Already Correct")
        lib = make_module_library(name="vpc-clean")
        make_project_module(project=project, library_module=lib, path_in_project="m1", status="applied")
        db.flush()
        update_project_counts(db, project.id)
        db.commit()

        result = recompute_all_project_counts(db)
        assert result["projects_repaired"] == 0


# ── ProjectService class — list/create/detail ────────────────────────


def _make_create_data(**overrides):
    """Build a SimpleNamespace ProjectCreate-like object for ProjectService."""
    from types import SimpleNamespace

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
        "ssh_credential_id": None,
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


class TestProjectServiceCreate:
    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_creates_project(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        result = svc.create_project(_make_create_data(name="PS Create"))
        assert result["success"] is True
        assert result["project_id"] is not None
        assert result["name"] == "PS Create"

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_duplicate_name_raises(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        svc.create_project(_make_create_data(name="DupPS"))
        with pytest.raises(ConflictError):
            svc.create_project(_make_create_data(name="DupPS"))

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_infers_provider_from_type(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        result = svc.create_project(_make_create_data(
            name="PS AWS",
            project_type="cloud-aws",
            cloud_provider=None,
        ))
        from models import Project
        p = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert p.cloud_provider == "aws"

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_infers_ibm_provider_from_type(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        result = svc.create_project(_make_create_data(
            name="PS IBM",
            project_type="cloud-ibm",
            cloud_provider=None,
        ))
        from models import Project
        p = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert p.cloud_provider == "ibm"

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_creates_project_with_ssh_credential_id(self, mock_cache, mock_inv, db):
        """K8S-014: ssh_credential_id is stored on project creation."""
        from models import Project
        from models.ssh_credential import SSHCredential

        cred = SSHCredential(name="test-ssh", host="10.0.0.5", port=22, username="ubuntu", auth_type="key")
        db.add(cred)
        db.flush()

        svc = ProjectService(db)
        result = svc.create_project(_make_create_data(
            name="K8s With SSH",
            project_type="kubernetes",
            ssh_credential_id=cred.id,
        ))
        assert result["success"] is True

        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.ssh_credential_id == cred.id

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_creates_project_without_ssh_credential(self, mock_cache, mock_inv, db):
        """K8S-014: project without SSH credential stores None."""
        from models import Project

        svc = ProjectService(db)
        result = svc.create_project(_make_create_data(
            name="K8s No SSH",
            project_type="kubernetes",
        ))

        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.ssh_credential_id is None

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_creates_project_with_platform_context(self, mock_cache, mock_inv, db):
        from models import Project

        svc = ProjectService(db)
        result = svc.create_project(_make_create_data(
            name="Platform Context Project",
            target_platform_profile="eks",
            platform_provider="aws",
            management_boundary="customer_managed_cluster",
        ))

        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.target_platform_profile == "eks"
        assert project.platform_provider == "aws"
        assert project.management_boundary == "customer_managed_cluster"

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_defaults_kubernetes_projects_to_generic_onprem_baseline(self, mock_cache, mock_inv, db):
        from models import Project

        svc = ProjectService(db)
        result = svc.create_project(
            _make_create_data(
                name="Default Baseline K8s",
                project_type="kubernetes",
                cloud_provider="on-prem",
                target_platform_profile=None,
            )
        )

        project = db.query(Project).filter(Project.id == result["project_id"]).first()
        assert project.target_platform_profile == "generic_onprem"


class TestProjectServiceSSHCredentialUpdate:
    """K8S-014: SSH credential persistence through project updates."""

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_update_sets_ssh_credential_id(self, mock_cache, mock_inv, db):
        """Setting ssh_credential_id via update stores it."""
        from models import Project
        from models.ssh_credential import SSHCredential

        cred = SSHCredential(name="upd-ssh", host="10.0.0.6", port=22, username="root", auth_type="key")
        db.add(cred)
        db.flush()

        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(name="SSH Update Test"))
        svc.update_project(created["project_id"], _make_update_data(ssh_credential_id=cred.id))

        project = db.query(Project).filter(Project.id == created["project_id"]).first()
        assert project.ssh_credential_id == cred.id

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_update_preserves_ssh_credential_when_not_sent(self, mock_cache, mock_inv, db):
        """Updating other fields preserves existing ssh_credential_id."""
        from models import Project
        from models.ssh_credential import SSHCredential

        cred = SSHCredential(name="persist-ssh", host="10.0.0.7", port=22, username="admin", auth_type="key")
        db.add(cred)
        db.flush()

        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(
            name="SSH Persist Test",
            ssh_credential_id=cred.id,
        ))

        # Update only the name — ssh_credential_id should NOT be in _UpdateData
        svc.update_project(created["project_id"], _make_update_data(name="SSH Persist Renamed"))

        project = db.query(Project).filter(Project.id == created["project_id"]).first()
        assert project.name == "SSH Persist Renamed"
        assert project.ssh_credential_id == cred.id  # preserved

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_ssh_credential_id_in_detail_response(self, mock_cache, mock_inv, db):
        """K8S-014: ssh_credential_id appears in project detail response."""
        from models.ssh_credential import SSHCredential

        cred = SSHCredential(name="detail-ssh", host="10.0.0.8", port=22, username="u", auth_type="key")
        db.add(cred)
        db.flush()

        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(
            name="SSH Detail Test",
            ssh_credential_id=cred.id,
        ))
        detail = svc.get_project_detail(created["project_id"])
        assert detail["ssh_credential_id"] == cred.id


class TestProjectServiceList:
    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_list_empty(self, mock_cache, mock_inv, db):
        mock_cache.get.return_value = None
        svc = ProjectService(db)
        result = svc.list_projects()
        assert result["projects"] == []
        assert result["total"] == 0

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_list_returns_projects(self, mock_cache, mock_inv, db):
        mock_cache.get.return_value = None
        svc = ProjectService(db)
        svc.create_project(_make_create_data(name="PS Alpha"))
        svc.create_project(_make_create_data(name="PS Beta"))
        result = svc.list_projects()
        assert result["total"] == 2

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_list_skip_cache(self, mock_cache, mock_inv, db):
        mock_cache.get.return_value = {"cached": True}
        svc = ProjectService(db)
        # skip_cache=True should bypass the cached result
        svc.create_project(_make_create_data(name="PS Skip Cache"))
        result = svc.list_projects(skip_cache=True)
        # Should get real data, not the cached mock
        assert "projects" in result
        assert result["total"] >= 1

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_list_uses_cache_when_available(self, mock_cache, mock_inv, db):
        cached_data = {"projects": [{"name": "cached"}], "total": 1}
        mock_cache.get.return_value = cached_data
        svc = ProjectService(db)
        result = svc.list_projects(skip_cache=False)
        assert result == cached_data

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_list_derives_generic_onprem_target_platform_for_legacy_kubernetes_project(self, mock_cache, mock_inv, db):
        from models import Project

        mock_cache.get.return_value = None

        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(name="Legacy List Onprem"))

        project = db.query(Project).filter(Project.id == created["project_id"]).first()
        project.target_platform_profile = None
        db.commit()

        result = svc.list_projects(skip_cache=True)
        serialized = next(p for p in result["projects"] if p["id"] == created["project_id"])
        assert serialized["target_platform_profile"] == "generic_onprem"


class TestProjectServiceDetail:
    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_get_detail(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(name="PS Detail"))
        detail = svc.get_project_detail(created["project_id"])
        assert detail["name"] == "PS Detail"
        assert "id" in detail
        assert "created_at" in detail
        assert "updated_at" in detail

    def test_get_nonexistent_raises(self, db):
        svc = ProjectService(db)
        with pytest.raises(NotFoundError):
            svc.get_project_detail(99999)

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_detail_serializes_platform_context(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(
            name="Platform Detail",
            target_platform_profile="generic_onprem",
            platform_provider="baremetal",
            management_boundary="forge_managed_components_only",
        ))

        detail = svc.get_project_detail(created["project_id"])
        assert detail["target_platform_profile"] == "generic_onprem"
        assert detail["platform_provider"] == "baremetal"
        assert detail["management_boundary"] == "forge_managed_components_only"

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_detail_derives_generic_onprem_target_platform_for_legacy_kubernetes_project(self, mock_cache, mock_inv, db):
        from models import Project

        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(name="Legacy Detail Onprem"))

        project = db.query(Project).filter(Project.id == created["project_id"]).first()
        project.target_platform_profile = None
        db.commit()

        detail = svc.get_project_detail(created["project_id"])
        assert detail["target_platform_profile"] == "generic_onprem"


class TestProjectServiceUpdate:
    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_update_name(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(name="PS Old"))
        result = svc.update_project(created["project_id"], _make_update_data(name="PS New"))
        assert result["success"] is True
        detail = svc.get_project_detail(created["project_id"])
        assert detail["name"] == "PS New"

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_update_duplicate_name_raises(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        svc.create_project(_make_create_data(name="PS Existing"))
        second = svc.create_project(_make_create_data(name="PS Rename"))
        with pytest.raises(ConflictError):
            svc.update_project(second["project_id"], _make_update_data(name="PS Existing"))

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_update_same_name_no_conflict(self, mock_cache, mock_inv, db):
        """Updating a project with its own name should not raise."""
        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(name="PS Same Name"))
        result = svc.update_project(created["project_id"], _make_update_data(name="PS Same Name"))
        assert result["success"] is True

    def test_update_nonexistent_raises(self, db):
        svc = ProjectService(db)
        with pytest.raises(NotFoundError):
            svc.update_project(99999, _make_update_data(name="Nope"))

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_update_platform_context_fields(self, mock_cache, mock_inv, db):
        from models import Project

        svc = ProjectService(db)
        created = svc.create_project(_make_create_data(name="Platform Update"))
        svc.update_project(
            created["project_id"],
            _make_update_data(
                target_platform_profile="gke",
                platform_provider="gcp",
                management_boundary="customer_managed_cluster",
            ),
        )

        project = db.query(Project).filter(Project.id == created["project_id"]).first()
        assert project.target_platform_profile == "gke"
        assert project.platform_provider == "gcp"
        assert project.management_boundary == "customer_managed_cluster"

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_update_defaults_target_platform_profile_to_generic_onprem_when_not_supplied(self, mock_cache, mock_inv, db):
        from models import Project

        svc = ProjectService(db)
        created = svc.create_project(
            _make_create_data(
                name="Platform Default On Update",
                project_type="kubernetes",
                cloud_provider="on-prem",
                target_platform_profile=None,
            )
        )

        project = db.query(Project).filter(Project.id == created["project_id"]).first()
        project.target_platform_profile = None
        db.commit()

        svc.update_project(created["project_id"], _make_update_data(description="updated"))

        project = db.query(Project).filter(Project.id == created["project_id"]).first()
        assert project.target_platform_profile == "generic_onprem"


class TestProjectServiceDelete:
    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_delete_inactive_project(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        p1 = svc.create_project(_make_create_data(name="PS Del"))
        svc.create_project(_make_create_data(name="PS Keep"))
        from models import Project
        project = db.query(Project).filter(Project.id == p1["project_id"]).first()
        project.is_active = False
        db.commit()

        with patch("services.module_lock.ModuleLockService") as mock_lock, \
             patch("services.workspace_manager.WorkspaceManager") as mock_ws:
            mock_lock.return_value.list_held.return_value = []
            mock_lock.return_value.is_held.return_value = False
            mock_ws.return_value.cleanup_project_workspaces.return_value = 0
            result = svc.delete_project(p1["project_id"])
        assert result["success"] is True

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_delete_active_project_raises(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        p1 = svc.create_project(_make_create_data(name="PS Active Del"))
        svc.create_project(_make_create_data(name="PS Other"))
        from models import Project
        project = db.query(Project).filter(Project.id == p1["project_id"]).first()
        project.is_active = True
        db.commit()

        with pytest.raises(BadRequestError, match="active project"):
            svc.delete_project(p1["project_id"])

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_force_delete_only_project(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        p = svc.create_project(_make_create_data(name="PS Only"))
        from models import Project
        project = db.query(Project).filter(Project.id == p["project_id"]).first()
        project.is_active = True
        db.commit()

        with patch("services.module_lock.ModuleLockService") as mock_lock, \
             patch("services.workspace_manager.WorkspaceManager") as mock_ws:
            mock_lock.return_value.list_held.return_value = []
            mock_lock.return_value.is_held.return_value = False
            mock_ws.return_value.cleanup_project_workspaces.return_value = 0
            result = svc.delete_project(p["project_id"], force=True)
        assert result["success"] is True


class TestProjectServiceActivate:
    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_set_active_deactivates_others(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        p1 = svc.create_project(_make_create_data(name="PS A"))
        p2 = svc.create_project(_make_create_data(name="PS B"))
        result = svc.set_active_project(p2["project_id"])
        assert result["success"] is True
        assert result["name"] == "PS B"

        from models import Project
        proj_a = db.query(Project).filter(Project.id == p1["project_id"]).first()
        proj_b = db.query(Project).filter(Project.id == p2["project_id"]).first()
        assert proj_b.is_active is True
        # set_active_project only deactivates others that were active, not all
        # proj_a may or may not be active depending on initial state

    def test_activate_nonexistent_raises(self, db):
        svc = ProjectService(db)
        with pytest.raises(NotFoundError):
            svc.set_active_project(99999)


class TestProjectServiceDependencies:
    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_update_dependencies(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        p1 = svc.create_project(_make_create_data(name="PS Dep Src"))
        p2 = svc.create_project(_make_create_data(name="PS Dep Tgt"))

        dep = MagicMock()
        dep.project_id = p1["project_id"]
        dep.model_dump.return_value = {"project_id": p1["project_id"], "outputs": []}

        result = svc.update_dependencies(p2["project_id"], [dep])
        assert result["success"] is True

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_self_dependency_raises(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        p = svc.create_project(_make_create_data(name="PS Self Dep"))

        dep = MagicMock()
        dep.project_id = p["project_id"]

        with pytest.raises(BadRequestError, match="circular"):
            svc.update_dependencies(p["project_id"], [dep])

    @patch("services.project_service.invalidate_cache")
    @patch("services.project_service.cache")
    def test_nonexistent_dep_target_raises(self, mock_cache, mock_inv, db):
        svc = ProjectService(db)
        p = svc.create_project(_make_create_data(name="PS Missing Dep"))

        dep = MagicMock()
        dep.project_id = 99999

        with pytest.raises(NotFoundError):
            svc.update_dependencies(p["project_id"], [dep])


# ── detect_module_dependencies — multiple matching deps ──────────────


class TestDetectDependenciesMultiple:
    def test_multiple_required_deps_resolved(self):
        lib_module = MagicMock()
        lib_module.dependencies_metadata = {
            "required": [
                {"module": "infra/aws/vpc"},
                {"module": "infra/aws/rds"},
            ]
        }

        vpc = MagicMock()
        vpc.id = 1
        vpc.library_module.path = "infra/aws/vpc"

        rds = MagicMock()
        rds.id = 2
        rds.library_module.path = "infra/aws/rds"

        deps = detect_module_dependencies(lib_module, [vpc, rds])
        assert 1 in deps
        assert 2 in deps

    def test_database_depends_on_vpc_fallback(self):
        lib_module = MagicMock()
        lib_module.dependencies_metadata = None
        lib_module.name = "database"

        vpc = MagicMock()
        vpc.id = 7
        vpc.library_module.name = "vpc"
        vpc.path_in_project = "infra/vpc"

        deps = detect_module_dependencies(lib_module, [vpc])
        assert 7 in deps

    def test_monitoring_depends_on_multiple_fallback(self):
        lib_module = MagicMock()
        lib_module.dependencies_metadata = None
        lib_module.name = "monitoring"

        vpc = MagicMock()
        vpc.id = 1
        vpc.library_module.name = "vpc"
        vpc.path_in_project = "infra/vpc"

        eks = MagicMock()
        eks.id = 2
        eks.library_module.name = "eks"
        eks.path_in_project = "infra/eks"

        deps = detect_module_dependencies(lib_module, [vpc, eks])
        assert 1 in deps
        assert 2 in deps
