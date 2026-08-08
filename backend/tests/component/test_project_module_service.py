"""
BC-004: Component tests for ProjectModuleService — module CRUD and deps.

Tests list, add, update, remove modules, variable validation,
dependency management, and status queries against real DB.
Mocks only external services (Celery, workspace locks, cache).
"""

from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, ConflictError, NotFoundError
from routes.module_library import get_module_variables
from services.project_module_service import ProjectModuleService

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def svc(db):
    """Create a ProjectModuleService backed by the test DB."""
    return ProjectModuleService(db)


@pytest.fixture()
def project_and_lib(db, make_project, make_module_library):
    """Create a project and a module library entry."""
    project = make_project(name="Module Test Project")
    lib = make_module_library(
        name="vpc",
        description="VPC module",
        category="networking",
    )
    db.commit()
    return project, lib


@pytest.fixture()
def project_with_lib_modules(db, make_project, make_module_library):
    """Create a project with 3 library modules available."""
    project = make_project(name="Multi Module Project")
    libs = []
    for name, cat in [("vpc", "networking"), ("security-group", "security"), ("eks-cluster", "kubernetes")]:
        lib = make_module_library(name=name, category=cat)
        libs.append(lib)
    db.commit()
    return project, libs


# ── List modules ─────────────────────────────────────────────────────


class TestListModules:
    def test_empty_project(self, svc, project_and_lib):
        project, _ = project_and_lib
        result = svc.list_modules(project.id)
        assert result["modules"] == []
        assert result["total"] == 0

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_lists_added_modules(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        svc.add_module(project.id, lib.id, "infra/vpc")
        result = svc.list_modules(project.id)
        assert result["total"] == 1
        assert result["modules"][0]["module_name"] == "vpc"

    def test_nonexistent_project_raises(self, svc):
        with pytest.raises(NotFoundError):
            svc.list_modules(99999)

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_list_modules_exposes_explicit_engine_and_lifecycle_capabilities(
        self,
        _mock_deps,
        _mock_counts,
        svc,
        db,
        make_project,
        make_module_library,
    ):
        project = make_project(name="Ansible Capability Project")
        lifecycle = {
            "supports_init": True,
            "supports_plan": False,
            "supports_apply": True,
            "supports_destroy": False,
        }
        lib = make_module_library(
            name="ansible-module",
            path="packs/ansible/module",
            module_source_kind="git_catalog",
            execution_engine="ansible",
            deploy_model="ansible_playbook",
            engine_type="ansible",
            pack_manifest={"deployment_pack": {"lifecycle": lifecycle}},
        )
        db.commit()

        svc.add_module(project.id, lib.id, "packs/ansible/module")
        result = svc.list_modules(project.id)

        serialized = result["modules"][0]
        assert serialized["module_source_kind"] == "git_catalog"
        assert serialized["execution_engine"] == "ansible"
        assert serialized["deploy_model"] == "ansible_playbook"
        assert serialized["engine_type"] == "ansible"
        assert serialized["lifecycle_capabilities"] == lifecycle
        assert serialized["library_module"]["engine_type"] == "ansible"
        assert serialized["library_module"]["execution_engine"] == "ansible"
        assert serialized["library_module"]["deploy_model"] == "ansible_playbook"
        assert serialized["library_module"]["lifecycle_capabilities"] == lifecycle

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_list_modules_falls_back_to_opentofu_when_no_explicit_engine(
        self,
        _mock_deps,
        _mock_counts,
        svc,
        db,
        make_project,
        make_module_library,
    ):
        project = make_project(name="Legacy OpenTofu Project")
        lib = make_module_library(
            name="legacy-module",
            path="infra/aws/legacy",
            engine_type=None,
            pack_manifest=None,
        )
        db.commit()

        svc.add_module(project.id, lib.id, "infra/aws/legacy")
        result = svc.list_modules(project.id)

        serialized = result["modules"][0]
        assert serialized["execution_engine"] == "opentofu"
        assert serialized["engine_type"] == "opentofu"
        assert "lifecycle_capabilities" not in serialized


# ── Add module ───────────────────────────────────────────────────────


class TestAddModule:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_add_module_success(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        result = svc.add_module(project.id, lib.id, "infra/vpc")
        assert result["success"] is True
        assert result["module_id"] is not None
        assert "vpc" in result["message"]

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_duplicate_path_raises(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        svc.add_module(project.id, lib.id, "infra/vpc")
        with pytest.raises(BadRequestError, match="already exists"):
            svc.add_module(project.id, lib.id, "infra/vpc")

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_nonexistent_library_module_raises(self, mock_deps, mock_counts, svc, project_and_lib):
        project, _ = project_and_lib
        with pytest.raises(NotFoundError):
            svc.add_module(project.id, 99999, "infra/fake")

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_default_deployment_order(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        result = svc.add_module(project.id, lib.id, "infra/vpc")
        assert result["deployment_order"] == 0

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_custom_deployment_order(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        result = svc.add_module(project.id, lib.id, "infra/vpc", deployment_order=5)
        assert result["deployment_order"] == 5

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_variable_overrides_stored(self, mock_deps, mock_counts, svc, db, make_project, make_module_library):
        project = make_project(name="Vars Override Project")
        lib = make_module_library(
            name="vpc-with-schema",
            category="networking",
            variables_schema=[{"name": "vpc_cidr", "type": "string", "required": False}],
        )
        db.commit()
        result = svc.add_module(
            project.id, lib.id, "infra/vpc",
            variable_overrides={"vpc_cidr": "10.0.0.0/16"},
        )
        # Verify via list
        modules = svc.list_modules(project.id)
        assert modules["modules"][0]["variable_overrides"]["vpc_cidr"] == "10.0.0.0/16"

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_add_module_with_manifest_inputs_and_no_variables_schema(
        self,
        mock_deps,
        mock_counts,
        svc,
        db,
        make_project,
        make_module_library,
    ):
        project = make_project(name="Manifest Inputs Project")
        lib = make_module_library(
            name="ibm_roks_single_nic",
            category="infra",
            variables_schema=None,
            inputs_metadata={
                "required": [
                    {"name": "ibmcloud_api_key", "type": "string", "required": True, "sensitive": True},
                    {"name": "ibmcloud_cluster_region", "type": "string", "required": True},
                ],
                "optional": [
                    {"name": "ibmcloud_resource_group", "type": "string", "default": "default"},
                ],
            },
        )
        db.commit()

        result = svc.add_module(
            project.id,
            lib.id,
            "infra/ibm_roks_single_nic",
            variable_overrides={
                "ibmcloud_api_key": "secret",
                "ibmcloud_cluster_region": "us-south",
                "ibmcloud_resource_group": "default",
            },
        )

        assert result["success"] is True
        modules = svc.list_modules(project.id)
        assert modules["modules"][0]["variable_overrides"]["ibmcloud_api_key"] == "secret"

    def test_manifest_inputs_are_returned_from_module_variables_endpoint(
        self,
        db,
        make_module_library,
    ):
        lib = make_module_library(
            name="ibm_roks_single_nic",
            category="infra",
            variables_schema=None,
            inputs_metadata={
                "required": [
                    {"name": "ibmcloud_api_key", "type": "string", "sensitive": True},
                    {"name": "ibmcloud_cluster_region", "type": "string"},
                ],
                "optional": [
                    {"name": "ibmcloud_resource_group", "type": "string", "default": "default"},
                ],
            },
        )
        db.commit()

        result = get_module_variables(lib.id, db)

        assert result["success"] is True
        variable_names = [item["name"] for item in result["variables"]]
        assert "ibmcloud_api_key" in variable_names
        assert "ibmcloud_cluster_region" in variable_names
        assert "ibmcloud_resource_group" in variable_names


    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_add_module_disabled_skips_validation(self, mock_deps, mock_counts, svc, db, make_project, make_module_library):
        """add_module with enabled=False must NOT validate variables and must create module enabled=False."""
        project = make_project(name="Disabled Module Validation Project")
        lib = make_module_library(
            name="requires-registry",
            category="infra",
            variables_schema=None,
            inputs_metadata={
                "required": [
                    {"name": "registry", "type": "string", "source": "user"},
                ],
                "optional": [],
            },
        )
        db.commit()

        # enabled=False: no registry provided — must NOT raise BadRequestError
        result = svc.add_module(
            project.id,
            lib.id,
            "infra/test/extra",
            variable_overrides={},
            enabled=False,
        )
        assert result["success"] is True

        from models import ProjectModule
        module = db.query(ProjectModule).filter(ProjectModule.id == result["module_id"]).first()
        assert module is not None
        assert module.enabled is False

        # enabled=True: variable_overrides provided but missing required input MUST raise BadRequestError
        with pytest.raises(BadRequestError):
            svc.add_module(
                project.id,
                lib.id,
                "infra/test/extra-enabled",
                variable_overrides={"unrelated_key": "value"},  # truthy but missing 'registry'
                enabled=True,
            )


# ── Update module ────────────────────────────────────────────────────


class TestUpdateModule:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_update_deployment_order(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        result = svc.update_module(added["module_id"], deployment_order=10)
        assert result["success"] is True

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_update_enabled(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        svc.update_module(added["module_id"], enabled=False)
        modules = svc.list_modules(project.id)
        assert modules["modules"][0]["enabled"] is False

    def test_update_nonexistent_raises(self, svc):
        with pytest.raises(NotFoundError):
            svc.update_module(99999, deployment_order=1)


# ── Remove module ────────────────────────────────────────────────────


class TestRemoveModule:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_remove_module(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")

        with patch("services.module_lock.ModuleLockService") as mock_lock:
            mock_lock.return_value.is_held.return_value = False
            mock_lock.return_value.get_holder.return_value = None
            with patch("services.workspace_manager.WorkspaceManager") as mock_ws:
                mock_ws.return_value.cleanup_module_workspace.return_value = False
                result = svc.remove_module(added["module_id"])

        assert result["success"] is True
        assert svc.list_modules(project.id)["total"] == 0

    def test_remove_nonexistent_raises(self, svc):
        with pytest.raises(NotFoundError):
            svc.remove_module(99999)


# ── Module status and variables ──────────────────────────────────────


class TestModuleStatus:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_get_status(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        status = svc.get_module_status(added["module_id"])
        assert status["status"] == "not_initialized"
        assert status["id"] == added["module_id"]

    def test_status_nonexistent_raises(self, svc):
        with pytest.raises(NotFoundError):
            svc.get_module_status(99999)


class TestModuleVariables:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_get_variables(self, mock_deps, mock_counts, svc, db, make_project, make_module_library):
        project = make_project(name="Vars Get Project")
        lib = make_module_library(
            name="vpc-vars",
            category="networking",
            variables_schema=[{"name": "region", "type": "string", "required": False}],
        )
        db.commit()
        added = svc.add_module(
            project.id, lib.id, "infra/vpc",
            variable_overrides={"region": "us-west-2"},
        )
        result = svc.get_module_variables(added["module_id"])
        assert result["exists"] is True
        assert result["variables"]["region"] == "us-west-2"
        assert result["module_name"] == "vpc-vars"


# ── Dependency management ────────────────────────────────────────────


class TestDependencies:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_get_dependencies_empty(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        result = svc.get_dependencies(added["module_id"])
        assert result["dependencies"] == []
        assert result["all_met"] is True

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_get_dependents_empty(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        result = svc.get_dependents(added["module_id"])
        assert result["dependents"] == []
        assert result["count"] == 0

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_set_and_get_dependencies(self, mock_deps, mock_counts, svc, project_with_lib_modules):
        project, libs = project_with_lib_modules
        vpc = svc.add_module(project.id, libs[0].id, "infra/vpc")
        eks = svc.add_module(project.id, libs[2].id, "infra/eks")

        # Set eks depends on vpc
        result = svc.set_dependencies(eks["module_id"], [vpc["module_id"]])
        assert result["success"] is True
        assert vpc["module_id"] in result["dependencies"]

        # Verify
        deps = svc.get_dependencies(eks["module_id"])
        assert len(deps["dependencies"]) == 1
        assert deps["dependencies"][0]["id"] == vpc["module_id"]

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_check_dependencies_all_met(self, mock_deps, mock_counts, svc, db, project_with_lib_modules):
        project, libs = project_with_lib_modules
        vpc_res = svc.add_module(project.id, libs[0].id, "infra/vpc")
        eks_res = svc.add_module(project.id, libs[2].id, "infra/eks")

        # Set dependency and mark vpc as applied
        svc.set_dependencies(eks_res["module_id"], [vpc_res["module_id"]])
        from models import ProjectModule
        vpc_mod = db.query(ProjectModule).filter(ProjectModule.id == vpc_res["module_id"]).first()
        vpc_mod.status = "applied"
        db.flush()

        eks_mod = db.query(ProjectModule).filter(ProjectModule.id == eks_res["module_id"]).first()
        is_met, unmet = svc.check_module_dependencies(eks_mod)
        assert is_met is True
        assert unmet == []

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_check_dependencies_unmet(self, mock_deps, mock_counts, svc, db, project_with_lib_modules):
        project, libs = project_with_lib_modules
        vpc_res = svc.add_module(project.id, libs[0].id, "infra/vpc")
        eks_res = svc.add_module(project.id, libs[2].id, "infra/eks")

        svc.set_dependencies(eks_res["module_id"], [vpc_res["module_id"]])
        # vpc stays as "not_initialized" — dependency not met

        from models import ProjectModule
        eks_mod = db.query(ProjectModule).filter(ProjectModule.id == eks_res["module_id"]).first()
        is_met, unmet = svc.check_module_dependencies(eks_mod)
        assert is_met is False
        assert len(unmet) == 1


# ── Validate variables ───────────────────────────────────────────────


class TestValidateVariables:
    def test_no_schema_returns_warning(self, svc, db, make_module_library):
        lib = make_module_library(name="no-schema", variables_schema=None)
        db.commit()
        result = svc.validate_variables(lib.id, {"any": "value"})
        assert result["success"] is True
        assert result["validated"] is False
        assert len(result["warnings"]) > 0

    def test_valid_variables(self, svc, db, make_module_library):
        lib = make_module_library(
            name="with-schema",
            variables_schema=[
                {"name": "region", "type": "string", "required": True},
                {"name": "count", "type": "number", "required": False},
            ],
        )
        db.commit()
        result = svc.validate_variables(lib.id, {"region": "us-west-2"})
        assert result["success"] is True
        assert result["validated"] is True

    def test_missing_required_fails(self, svc, db, make_module_library):
        lib = make_module_library(
            name="required-schema",
            variables_schema=[
                {"name": "region", "type": "string", "required": True},
            ],
        )
        db.commit()
        result = svc.validate_variables(lib.id, {})
        assert result["success"] is False
        assert len(result["errors"]) > 0

    def test_unknown_variables_warned(self, svc, db, make_module_library):
        lib = make_module_library(
            name="warn-schema",
            variables_schema=[
                {"name": "region", "type": "string", "required": True},
            ],
        )
        db.commit()
        result = svc.validate_variables(lib.id, {"region": "us-west-2", "extra": "value"})
        assert result["success"] is True
        assert any("extra" in w for w in result["warnings"])

    def test_nonexistent_module_raises(self, svc):
        with pytest.raises(NotFoundError):
            svc.validate_variables(99999, {"any": "value"})


# ── Calculate deployment order ───────────────────────────────────────


class TestCalculateDeploymentOrder:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_empty_project(self, mock_deps, mock_counts, svc, project_and_lib):
        project, _ = project_and_lib
        result = svc.calculate_deployment_order(project.id)
        assert result["success"] is True
        assert result["updated"] == 0

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_single_module_order(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        svc.add_module(project.id, lib.id, "infra/vpc")
        result = svc.calculate_deployment_order(project.id)
        assert result["success"] is True
        assert result["updated"] == 1

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_multiple_modules_ordered(self, mock_deps, mock_counts, svc, project_with_lib_modules):
        project, libs = project_with_lib_modules
        vpc = svc.add_module(project.id, libs[0].id, "infra/vpc")
        sg = svc.add_module(project.id, libs[1].id, "infra/sg")
        eks = svc.add_module(project.id, libs[2].id, "infra/eks")

        # Set deps: eks depends on vpc
        svc.set_dependencies(eks["module_id"], [vpc["module_id"]])

        result = svc.calculate_deployment_order(project.id)
        assert result["success"] is True
        assert result["updated"] == 3
        assert "changes" in result

    def test_nonexistent_project_raises(self, svc):
        with pytest.raises(NotFoundError):
            svc.calculate_deployment_order(99999)

    def test_calculate_deployment_order_uses_metadata_not_corrupt_namelist(
        self,
        svc,
        db,
        make_project,
        make_module_library,
    ):
        """Regression: corrupt flat dependencies name-list must NOT be used for ordering.

        dependencies_metadata is the acyclic source of truth.  The flat
        `dependencies` name-list may contain cycles (e.g. aws infra modules);
        the function must resolve via metadata and produce a valid acyclic order.
        """
        project = make_project(name="Metadata Ordering Project")

        # CORRECT acyclic metadata chain: core → (none), mid → core, leaf → mid
        lib_core = make_module_library(
            name="core",
            path="infra/test/core",
            dependencies=[],  # corrupt: empty — no issue
            dependencies_metadata={"required": [], "optional": []},
        )
        lib_mid = make_module_library(
            name="mid",
            path="infra/test/mid",
            # CORRUPT name-list would form a core↔mid cycle if used
            dependencies=["core"],
            dependencies_metadata={"required": [{"module": "infra/test/core"}], "optional": []},
        )
        lib_leaf = make_module_library(
            name="leaf",
            path="infra/test/leaf",
            # CORRUPT name-list: mid↔leaf cycle
            dependencies=["mid"],
            dependencies_metadata={"required": [{"module": "infra/test/mid"}], "optional": []},
        )

        # Make the name-list truly cyclic so the test catches a regression immediately
        lib_core.dependencies = ["mid"]   # core ↔ mid cycle
        lib_mid.dependencies = ["core", "leaf"]  # mid ↔ leaf cycle
        db.flush()

        # Add all three as ProjectModules
        from models import ProjectModule

        pm_core = ProjectModule(
            project_id=project.id, module_library_id=lib_core.id,
            path_in_project="infra/test/core", status="not_initialized",
            variables={}, deployment_order=0, enabled=True,
        )
        pm_mid = ProjectModule(
            project_id=project.id, module_library_id=lib_mid.id,
            path_in_project="infra/test/mid", status="not_initialized",
            variables={}, deployment_order=0, enabled=True,
        )
        pm_leaf = ProjectModule(
            project_id=project.id, module_library_id=lib_leaf.id,
            path_in_project="infra/test/leaf", status="not_initialized",
            variables={}, deployment_order=0, enabled=True,
        )
        db.add_all([pm_core, pm_mid, pm_leaf])
        db.flush()

        # Before fix, the corrupt name-list would raise CircularDependencyError
        result = svc.calculate_deployment_order(project.id)

        assert result["success"] is True
        assert result["updated"] == 3

        # Verify topological order from the returned changes dict
        changes_by_name = {c["name"]: c["new_order"] for c in result["changes"]}
        assert "core" in changes_by_name
        assert "mid" in changes_by_name
        assert "leaf" in changes_by_name
        assert changes_by_name["core"] < changes_by_name["mid"]
        assert changes_by_name["mid"] < changes_by_name["leaf"]

    def test_calculate_deployment_order_falls_back_to_flat_namelist_without_metadata(
        self,
        svc,
        db,
        make_project,
        make_module_library,
    ):
        """Regression: modules without dependencies_metadata (e.g. bare-metal/SSH BNK
        chain modules) must still resolve deploy order from the flat `dependencies`
        name-list, not collapse into a single parallel layer.
        """
        project = make_project(name="Flat Namelist Ordering Project")

        lib_core = make_module_library(
            name="bnk-flo",
            path="bare_metal/bnk-flo",
            dependencies=[],
            dependencies_metadata=None,
        )
        lib_leaf = make_module_library(
            name="bnk-cneinstance",
            path="bare_metal/bnk-cneinstance",
            dependencies=["bnk-flo"],
            dependencies_metadata=None,
        )
        db.flush()

        from models import ProjectModule

        pm_core = ProjectModule(
            project_id=project.id, module_library_id=lib_core.id,
            path_in_project="bare_metal/bnk-flo", status="not_initialized",
            variables={}, deployment_order=0, enabled=True,
        )
        pm_leaf = ProjectModule(
            project_id=project.id, module_library_id=lib_leaf.id,
            path_in_project="bare_metal/bnk-cneinstance", status="not_initialized",
            variables={}, deployment_order=0, enabled=True,
        )
        db.add_all([pm_core, pm_leaf])
        db.flush()

        result = svc.calculate_deployment_order(project.id)

        assert result["success"] is True
        assert result["updated"] == 2

        changes_by_name = {c["name"]: c["new_order"] for c in result["changes"]}
        assert changes_by_name["bnk-flo"] < changes_by_name["bnk-cneinstance"]

        # The edge must survive on the module itself too, not just the returned order.
        assert pm_core.id in (pm_leaf.dependencies or [])


# ── Dependency graph ─────────────────────────────────────────────────


class TestDependencyGraph:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_empty_graph(self, mock_deps, mock_counts, svc, project_and_lib):
        project, _ = project_and_lib
        result = svc.get_dependency_graph(project.id)
        assert result["project_id"] == project.id
        assert result["module_count"] == 0
        assert result["graph"]["nodes"] == []
        assert result["graph"]["edges"] == []

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_graph_with_dependencies(self, mock_deps, mock_counts, svc, project_with_lib_modules):
        project, libs = project_with_lib_modules
        vpc = svc.add_module(project.id, libs[0].id, "infra/vpc")
        eks = svc.add_module(project.id, libs[2].id, "infra/eks")

        svc.set_dependencies(eks["module_id"], [vpc["module_id"]])

        result = svc.get_dependency_graph(project.id)
        assert result["module_count"] == 2
        assert len(result["graph"]["nodes"]) == 2
        assert len(result["graph"]["edges"]) == 1
        edge = result["graph"]["edges"][0]
        assert edge["from"] == vpc["module_id"]
        assert edge["to"] == eks["module_id"]

    def test_graph_nonexistent_project_raises(self, svc):
        with pytest.raises(NotFoundError):
            svc.get_dependency_graph(99999)


# ── Create task ──────────────────────────────────────────────────────


class TestCreateTask:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_create_task_returns_task_object(self, mock_deps, mock_counts, svc, db, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        from models import ProjectModule
        module = db.query(ProjectModule).filter(ProjectModule.id == added["module_id"]).first()

        task = svc.create_task("plan", module)
        assert task.id is not None
        assert task.task_type == "plan"
        assert task.status == "queued"
        assert task.project_id == project.id
        assert task.module_id == module.id
        assert task.triggered_by == "user"

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_create_task_custom_trigger(self, mock_deps, mock_counts, svc, db, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        from models import ProjectModule
        module = db.query(ProjectModule).filter(ProjectModule.id == added["module_id"]).first()

        task = svc.create_task("apply", module, triggered_by="scheduler")
        assert task.triggered_by == "scheduler"
        assert task.task_type == "apply"


class TestSubmitPlanDispatch:
    @patch("services.execution.task_dispatch.dispatch_plan")
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_submit_plan_uses_dispatch_helper_for_engine_aware_routing(
        self,
        _mock_deps,
        _mock_counts,
        mock_dispatch_plan,
        svc,
        db,
        project_and_lib,
    ):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        # Phase 2 state machine requires module to be initialized before
        # submit_plan transitions to "planning" — production submit_plan
        # validates this via _validate_for_operation but the test bypasses
        # that path. Set up a legal precondition.
        from sqlalchemy import text
        db.execute(text("UPDATE project_modules SET status = 'initialized' WHERE id = :id"),
                   {"id": added["module_id"]})
        db.commit()

        mock_dispatch_plan.return_value = MagicMock(id="celery-plan-1")

        result = svc.submit_plan(added["module_id"])

        assert result["success"] is True
        assert result["celery_task_id"] == "celery-plan-1"

        from models import ProjectModule

        module = db.query(ProjectModule).filter(ProjectModule.id == added["module_id"]).first()
        assert module.status == "planning"
        mock_dispatch_plan.assert_called_once()

    @patch("services.execution.task_dispatch.dispatch_plan")
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_submit_plan_dispatches_ansible_module_with_explicit_engine_metadata(
        self,
        _mock_deps,
        _mock_counts,
        mock_dispatch_plan,
        svc,
        db,
        make_project,
        make_module_library,
    ):
        project = make_project(name="Ansible Plan Project")
        ansible_lib = make_module_library(
            name="ansible-pack",
            category="infrastructure",
            git_source="https://github.com/test/ansible-pack.git",
            engine_type="ansible",
        )
        db.commit()

        added = svc.add_module(project.id, ansible_lib.id, "packs/ansible-pack")
        from sqlalchemy import text
        db.execute(text("UPDATE project_modules SET status = 'initialized' WHERE id = :id"),
                   {"id": added["module_id"]})
        db.commit()
        mock_dispatch_plan.return_value = MagicMock(id="celery-plan-ans")

        result = svc.submit_plan(added["module_id"])

        assert result["success"] is True
        assert result["celery_task_id"] == "celery-plan-ans"
        dispatched_module = mock_dispatch_plan.call_args.args[1]
        assert dispatched_module.library_module.engine_type == "ansible"

    @patch("services.execution.task_dispatch.dispatch_init")
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_submit_init_commits_task_before_worker_reads_it(
        self,
        _mock_deps,
        _mock_counts,
        mock_dispatch_init,
        svc,
        db,
        project_and_lib,
    ):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")

        task_seen_by_dispatch = {}

        def _fake_dispatch(task_id, module):
            from models import Task as TaskModel

            task_row = db.query(TaskModel).filter(TaskModel.id == task_id).first()
            task_seen_by_dispatch["exists"] = task_row is not None
            task_seen_by_dispatch["status"] = task_row.status if task_row else None
            task_seen_by_dispatch["module_status"] = module.status
            return MagicMock(id="celery-init-1")

        mock_dispatch_init.side_effect = _fake_dispatch

        result = svc.submit_init(added["module_id"])

        assert result["success"] is True
        # By the time the worker dispatches, the task row must be visible AND
        # module.status must already reflect the queued operation. Both belong
        # in the commit that precedes the .delay() call, so a worker reading
        # via a separate connection sees a consistent snapshot.
        assert task_seen_by_dispatch == {
            "exists": True,
            "status": "queued",
            "module_status": "initializing",
        }


# ── Module removal with locks ────────────────────────────────────────


class TestRemoveModuleLocked:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_locked_module_raises_conflict(self, mock_deps, mock_counts, svc, project_and_lib):
        from core.errors import ConflictError

        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")

        with patch("services.module_lock.ModuleLockService") as mock_lock:
            mock_lock.return_value.is_held.return_value = True
            mock_lock.return_value.get_holder.return_value = {
                "module_id": added["module_id"],
                "holding_task_id": 123,
            }
            with pytest.raises(ConflictError, match="operation in progress"):
                svc.remove_module(added["module_id"])


# ── Reset module status (private helper via cancel) ──────────────────


class TestResetModuleStatus:
    def test_reset_from_initializing(self, svc):
        """_reset_module_status maps initializing → not_initialized."""
        from models import ProjectModule
        module = MagicMock(spec=ProjectModule)
        module.status = "initializing"
        svc._reset_module_status(module, "Cancelled")
        assert module.status == "not_initialized"
        assert module.deployment_error == "Cancelled"

    def test_reset_from_planning(self, svc):
        from models import ProjectModule
        module = MagicMock(spec=ProjectModule)
        module.status = "planning"
        svc._reset_module_status(module, "Cancelled")
        assert module.status == "initialized"

    def test_reset_from_applying(self, svc):
        from models import ProjectModule
        module = MagicMock(spec=ProjectModule)
        module.status = "applying"
        svc._reset_module_status(module, "Cancelled")
        assert module.status == "planned"

    def test_reset_from_destroying(self, svc):
        from models import ProjectModule
        module = MagicMock(spec=ProjectModule)
        module.status = "destroying"
        svc._reset_module_status(module, "Cancelled")
        assert module.status == "applied"

    def test_reset_from_unknown_status_is_noop(self, svc):
        from models import ProjectModule
        module = MagicMock(spec=ProjectModule)
        module.status = "applied"
        svc._reset_module_status(module, "Cancelled")
        # Should remain unchanged — no mapping for "applied"
        assert module.status == "applied"


# ── Module status filtering ──────────────────────────────────────────


class TestModuleStatusFiltering:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_multiple_modules_ordered_by_deployment_order(self, mock_deps, mock_counts, svc, project_with_lib_modules):
        project, libs = project_with_lib_modules
        svc.add_module(project.id, libs[0].id, "infra/vpc", deployment_order=2)
        svc.add_module(project.id, libs[1].id, "infra/sg", deployment_order=1)
        svc.add_module(project.id, libs[2].id, "infra/eks", deployment_order=3)

        result = svc.list_modules(project.id)
        orders = [m["deployment_order"] for m in result["modules"]]
        assert orders == sorted(orders)


# ── Validate for operation (private helper) ──────────────────────────


class TestValidateForOperation:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_validate_module_success(self, mock_deps, mock_counts, svc, db, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        result = svc.validate_module(added["module_id"], operation="plan")
        assert result["valid"] is True
        assert result["errors"] == []

    def test_validate_nonexistent_raises(self, svc):
        with pytest.raises(NotFoundError):
            svc.validate_module(99999)


# ── Plan status ──────────────────────────────────────────────────────


class TestPlanStatus:
    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_get_plan_status_no_plan(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")

        with patch("services.workspace_manager.WorkspaceManager") as mock_ws:
            ws_instance = mock_ws.return_value
            ws_instance.is_initialized.return_value = False
            ws_instance.has_saved_plan.return_value = False
            result = svc.get_plan_status(added["module_id"])

        assert result["module_id"] == added["module_id"]
        assert result["has_plan"] is False
        assert result["workspace_initialized"] is False

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_get_plan_status_with_plan(self, mock_deps, mock_counts, svc, project_and_lib):
        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")

        with patch("services.workspace_manager.WorkspaceManager") as mock_ws:
            ws_instance = mock_ws.return_value
            ws_instance.is_initialized.return_value = True
            ws_instance.has_saved_plan.return_value = True
            ws_instance.plan_is_valid.return_value = (True, "")
            result = svc.get_plan_status(added["module_id"])

        assert result["has_plan"] is True
        assert result["plan_valid"] is True
        assert result["workspace_initialized"] is True


# ── Regression: calculate_deployment_order replaces deps (not unions) ─────────


class TestCalculateDeploymentOrderReplacement:
    """Regression: calculate_deployment_order must REPLACE module.dependencies
    with freshly detected IDs, not union with old ones.

    If a module is removed and deployment order is recalculated, the stale ID
    from the removed module must NOT persist — it caused deploy-all to stall
    waiting for a module that no longer exists.
    """

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_calculate_deployment_order_removes_stale_dependency_ids(
        self, mock_deps, mock_counts, svc, db, make_project, make_module_library,
    ):
        """After recalculation and flush, a stale dep ID not in detected set is removed.

        calculate_deployment_order mutates ORM objects without committing; the caller
        must flush/commit.  We flush after the call then check the in-session ORM state.
        """
        from models import ProjectModule

        project = make_project(name="StaleDepProject")
        lib_a = make_module_library(name="mod-a", category="infra")
        lib_b = make_module_library(name="mod-b", category="infra")
        db.commit()

        # Add module A (no deps detected)
        added_a = svc.add_module(project.id, lib_a.id, "infra/mod-a")
        module_a_id = added_a["module_id"]

        # Add module B and manually inject a stale dep ID (simulating a removed module)
        added_b = svc.add_module(project.id, lib_b.id, "infra/mod-b")
        module_b_id = added_b["module_id"]

        mod_b = db.query(ProjectModule).filter(ProjectModule.id == module_b_id).first()
        stale_id = 99999  # a module that doesn't exist
        mod_b.dependencies = [module_a_id, stale_id]
        db.commit()

        # Recalculate — detect_module_dependencies returns [] (no deps detected)
        svc.calculate_deployment_order(project.id)
        # Flush so the session has the updated state written to the in-session identity map
        db.flush()
        # Expire cache and re-read from DB to verify persistence
        db.expire_all()
        mod_b_after = db.query(ProjectModule).filter(ProjectModule.id == module_b_id).first()

        # After replacement semantics + flush, the stale ID must be gone
        assert stale_id not in (mod_b_after.dependencies or []), (
            f"Stale dep ID {stale_id} persisted after recalculation (union bug). "
            f"Got: {mod_b_after.dependencies}"
        )

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_calculate_deployment_order_detected_deps_are_written(
        self, mock_deps, mock_counts, svc, db, make_project, make_module_library,
    ):
        """Detected dependency IDs appear in module.dependencies after recalculation."""
        from models import ProjectModule

        project = make_project(name="DetectedDepProject")
        lib_a = make_module_library(name="vpc", category="networking")
        lib_b = make_module_library(name="eks", category="kubernetes")
        db.commit()

        added_a = svc.add_module(project.id, lib_a.id, "infra/vpc")
        module_a_id = added_a["module_id"]
        added_b = svc.add_module(project.id, lib_b.id, "infra/eks")
        module_b_id = added_b["module_id"]

        # Override detect to report mod_a as dep of mod_b
        def _fake_detect(lib_mod, other_modules):
            if lib_mod.name == "eks":
                return [module_a_id]
            return []

        with patch("services.project_module_service.detect_module_dependencies", side_effect=_fake_detect):
            svc.calculate_deployment_order(project.id)

        mod_b = db.query(ProjectModule).filter(ProjectModule.id == module_b_id).first()
        assert module_a_id in (mod_b.dependencies or [])


# ── Regression: add_module validates variable_overrides for disabled modules ──


class TestAddModuleValidatesDisabledModuleVariables:
    """Regression: add_module must validate variable_overrides even when enabled=False.

    Invalid overrides must not be silently persisted until apply time.
    """

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_add_disabled_module_with_invalid_overrides_raises(
        self, mock_deps, mock_counts, svc, db, make_project, make_module_library,
    ):
        """A missing required variable on a disabled module must raise BadRequestError.

        The original code skipped validation for disabled modules (enabled=False).
        After the fix, validation always runs when variable_overrides are provided.
        """
        project = make_project(name="DisabledValidateProject")
        lib = make_module_library(
            name="validated-mod",
            category="infra",
            variables_schema=[
                {"name": "cidr", "type": "string", "required": True},
                {"name": "optional_field", "type": "string", "required": False},
            ],
        )
        db.commit()

        # cidr is required but absent from overrides — must be rejected even though enabled=False
        with pytest.raises(BadRequestError):
            svc.add_module(
                project.id, lib.id, "infra/mod",
                variable_overrides={"optional_field": "hello"},  # cidr missing → validation error
                enabled=False,
            )

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_add_disabled_module_with_valid_overrides_succeeds(
        self, mock_deps, mock_counts, svc, db, make_project, make_module_library,
    ):
        """Valid overrides on a disabled module are accepted."""
        project = make_project(name="DisabledValidProject")
        lib = make_module_library(
            name="valid-mod",
            category="infra",
            variables_schema=[
                {"name": "size", "type": "string", "required": False},
            ],
        )
        db.commit()

        result = svc.add_module(
            project.id, lib.id, "infra/mod",
            variable_overrides={"size": "large"},
            enabled=False,
        )
        assert result["success"] is True


# ── Regression: update_module enabling recomputes deps ────────────────────────


class TestUpdateModuleEnablingRecomputesDeps:
    """Regression: update_module(enabled=True) on a previously-disabled module must
    trigger calculate_deployment_order so the sequencer has fresh dependencies.
    """

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_enabling_module_triggers_deployment_order_recompute(
        self, mock_deps, mock_counts, svc, db, make_project, make_module_library,
    ):
        """Enabling a disabled module calls calculate_deployment_order for the project."""
        project = make_project(name="EnableRecomputeProject")
        lib = make_module_library(name="opt-mod", category="infra")
        db.commit()

        added = svc.add_module(project.id, lib.id, "infra/opt", enabled=False)
        module_id = added["module_id"]

        # Patch calculate_deployment_order to track whether it was called
        with patch.object(svc, "calculate_deployment_order", wraps=svc.calculate_deployment_order) as mock_calc:
            svc.update_module(module_id, enabled=True)

        mock_calc.assert_called_once_with(project.id)

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_updating_already_enabled_module_does_not_recompute(
        self, mock_deps, mock_counts, svc, db, make_project, make_module_library,
    ):
        """Updating an already-enabled module (no enabled flag change) skips recompute."""
        project = make_project(name="AlreadyEnabledProject")
        lib = make_module_library(name="opt-mod2", category="infra")
        db.commit()

        added = svc.add_module(project.id, lib.id, "infra/opt2", enabled=True)
        module_id = added["module_id"]

        with patch.object(svc, "calculate_deployment_order") as mock_calc:
            svc.update_module(module_id, deployment_order=5)  # no enabled= kwarg

        mock_calc.assert_not_called()


# ── Module actions (D-034) ───────────────────────────────────────────


ACTION_ARTIFACT_MANIFEST = {
    "schema_version": 1,
    "name": "ocibnkctl-runner",
    "version": "1.0.0",
    "kind": "container_image",
    "container_image": {
        "registry_host": "ghcr.io",
        "repository": "jgruberf5/ocibnkctl-runner",
        "digest": "sha256:" + "a" * 64,
    },
    "steps": {"apply": [{"name": "up", "args": ["ocibnkctl", "e2e"]}]},
    "actions": {
        "run-scenario": {
            "title": "Run a functional scenario",
            "description": "Runs one scenario by name",
            "rating": "amber",
            "steps": [{"name": "run", "args": ["ocibnkctl", "scenario", "run", "{{inputs.scenario}}"]}],
            "inputs": [{"name": "scenario", "type": "string", "source": "user"}],
        },
    },
}


ENUM_ACTION_MANIFEST = {
    **ACTION_ARTIFACT_MANIFEST,
    "actions": {
        "run-scenario": {
            "title": "Run a functional scenario",
            "rating": "amber",
            "steps": [{"name": "run", "args": ["ocibnkctl", "scenario", "run", "{{inputs.scenario}}"]}],
            "inputs": [
                {"name": "scenario", "type": "string", "choices": ["tcpl4lb", "udp"]},
                {"name": "region", "type": "string", "default": "us-east"},
            ],
        },
    },
}


@pytest.fixture()
def container_module_with_actions(db, make_project, make_module_library, make_project_module):
    """An applied container module whose manifest declares an action."""
    project = make_project(name="Actions Project")
    lib = make_module_library(
        name="ocibnkctl-runner",
        category="bnk",
        execution_engine="container",
        pack_manifest=ACTION_ARTIFACT_MANIFEST,
    )
    module = make_project_module(project=project, library_module=lib, status="applied")
    db.commit()
    return module


@pytest.fixture()
def container_module_with_enum_action(db, make_project, make_module_library, make_project_module):
    """An applied container module whose action declares an enum input + a default."""
    project = make_project(name="Enum Actions Project")
    lib = make_module_library(
        name="ocibnkctl-runner-enum",
        category="bnk",
        execution_engine="container",
        pack_manifest=ENUM_ACTION_MANIFEST,
    )
    module = make_project_module(project=project, library_module=lib, status="applied")
    db.commit()
    return module


class TestListModuleActions:
    def test_list_actions_returns_declared_actions(self, svc, container_module_with_actions):
        result = svc.list_module_actions(container_module_with_actions.id)
        assert result["total"] == 1
        action = result["actions"][0]
        assert action["name"] == "run-scenario"
        assert action["title"] == "Run a functional scenario"
        assert action["rating"] == "amber"
        assert action["inputs"][0]["name"] == "scenario"

    def test_list_actions_empty_for_module_without_manifest(self, svc, db, project_and_lib):
        project, lib = project_and_lib
        with patch("services.project_module_service.update_project_counts"), \
             patch("services.project_module_service.detect_module_dependencies", return_value=[]):
            added = svc.add_module(project.id, lib.id, "infra/vpc")
        result = svc.list_module_actions(added["module_id"])
        assert result == {"module_id": added["module_id"], "actions": [], "total": 0}


class TestSubmitAction:
    @patch("services.execution.task_dispatch.dispatch_container_action")
    def test_submit_action_happy_path_creates_task_and_dispatches(
        self, mock_dispatch, svc, db, container_module_with_actions
    ):
        module = container_module_with_actions
        mock_dispatch.return_value = MagicMock(id="celery-action-1")

        result = svc.submit_action(module.id, "run-scenario", inputs={"scenario": "tcpl4lb"})

        assert result["success"] is True
        assert result["action"] == "run-scenario"
        assert result["celery_task_id"] == "celery-action-1"
        assert result["status"] == "queued"

        from models import Task
        task = db.query(Task).filter(Task.id == result["task_id"]).first()
        assert task.task_type == "action"
        assert task.meta_data == {"action": "run-scenario"}

        # An action run must never change the module's status.
        db.refresh(module)
        assert module.status == "applied"

        mock_dispatch.assert_called_once_with(
            result["task_id"], module, "run-scenario", {"scenario": "tcpl4lb"}
        )

    def test_submit_action_rejects_module_not_applied(self, svc, db, container_module_with_actions):
        module = container_module_with_actions
        from sqlalchemy import text
        db.execute(text("UPDATE project_modules SET status = 'initialized' WHERE id = :id"), {"id": module.id})
        db.commit()

        with pytest.raises(BadRequestError, match="initialized"):
            svc.submit_action(module.id, "run-scenario")

    def test_submit_action_rejects_unknown_action(self, svc, container_module_with_actions):
        with pytest.raises(BadRequestError, match="no action 'does-not-exist'"):
            svc.submit_action(container_module_with_actions.id, "does-not-exist")

    def test_submit_action_rejects_non_container_module(self, svc, db, project_and_lib):
        project, lib = project_and_lib
        with patch("services.project_module_service.update_project_counts"), \
             patch("services.project_module_service.detect_module_dependencies", return_value=[]):
            added = svc.add_module(project.id, lib.id, "infra/vpc")
        with pytest.raises(BadRequestError, match="container artifact"):
            svc.submit_action(added["module_id"], "run-scenario")

    def test_submit_action_rejects_locked_module(self, svc, container_module_with_actions):
        with patch("services.module_lock.ModuleLockService.is_held", return_value=True), \
             patch("services.module_lock.ModuleLockService.get_holder", return_value={"task_id": 99}):
            with pytest.raises(ConflictError):
                svc.submit_action(container_module_with_actions.id, "run-scenario")

    # ── Adversarial input validation (D-034 F1/F2) ──────────────────────

    @patch("services.execution.task_dispatch.dispatch_container_action")
    def test_submit_action_rejects_leading_dash_free_string(
        self, mock_dispatch, svc, container_module_with_actions
    ):
        with pytest.raises(BadRequestError, match="cannot start with"):
            svc.submit_action(
                container_module_with_actions.id,
                "run-scenario",
                inputs={"scenario": "--kubeconfig=/attacker/path"},
            )
        mock_dispatch.assert_not_called()

    @patch("services.execution.task_dispatch.dispatch_container_action")
    def test_submit_action_rejects_undeclared_input_key(
        self, mock_dispatch, svc, container_module_with_actions
    ):
        with pytest.raises(BadRequestError, match="Undeclared action input 'rogue'"):
            svc.submit_action(
                container_module_with_actions.id,
                "run-scenario",
                inputs={"scenario": "tcpl4lb", "rogue": "attacker-value"},
            )
        mock_dispatch.assert_not_called()

    @patch("services.execution.task_dispatch.dispatch_container_action")
    def test_submit_action_rejects_enum_value_not_in_choices(
        self, mock_dispatch, svc, container_module_with_enum_action
    ):
        with pytest.raises(BadRequestError, match="Invalid action input"):
            svc.submit_action(
                container_module_with_enum_action.id,
                "run-scenario",
                inputs={"scenario": "not-in-choices"},
            )
        mock_dispatch.assert_not_called()

    @patch("services.execution.task_dispatch.dispatch_container_action")
    def test_submit_action_accepts_enum_choice_and_applies_default(
        self, mock_dispatch, svc, container_module_with_enum_action
    ):
        module = container_module_with_enum_action
        mock_dispatch.return_value = MagicMock(id="celery-action-enum")

        result = svc.submit_action(module.id, "run-scenario", inputs={"scenario": "tcpl4lb"})

        assert result["success"] is True
        # Effective inputs passed to dispatch include the applied default for
        # the omitted 'region' input.
        mock_dispatch.assert_called_once_with(
            result["task_id"], module, "run-scenario", {"scenario": "tcpl4lb", "region": "us-east"}
        )


# ── Disabled modules are not runnable (issue #527) ───────────────────


class TestDisabledModuleIsNotRunnable:
    """`enabled: false` must actually hold a module back.

    Issue #527: a blueprint's second module was explicitly disabled, yet the
    dependency chain dispatched it the moment the first module completed — so
    there was no way to build a ROKS cluster without BNK landing on it. Blueprint
    manifests depend on this too: `optional: true` creates a module DISABLED, and
    that guarantee is only as strong as this check.
    """

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_validate_rejects_disabled_module(self, _deps, _counts, svc, db, project_and_lib):
        """The shared plan/apply gate refuses a disabled module."""
        from models import ProjectModule

        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        module = db.query(ProjectModule).filter(ProjectModule.id == added["module_id"]).first()
        module.enabled = False
        db.flush()

        result = svc.validate_module(module.id, operation="apply")

        assert result["valid"] is False, (
            "a disabled module validated as ready to apply — nothing stops it being "
            "deployed (issue #527)"
        )
        assert any("disabled" in e.lower() for e in result["errors"]), result["errors"]

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_validate_allows_enabled_module(self, _deps, _counts, svc, db, project_and_lib):
        """Contrast: the same module passes once enabled.

        Without this, the assertion above would also hold if validation were
        broken for every module.
        """
        from models import ProjectModule

        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        module = db.query(ProjectModule).filter(ProjectModule.id == added["module_id"]).first()
        module.enabled = True
        db.flush()

        result = svc.validate_module(module.id, operation="apply")

        assert not any("disabled" in e.lower() for e in result["errors"]), result["errors"]

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_submit_apply_refuses_disabled_module(self, _deps, _counts, svc, db, project_and_lib):
        """A disabled module cannot be deployed by calling apply directly either."""
        from models import ProjectModule

        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        module = db.query(ProjectModule).filter(ProjectModule.id == added["module_id"]).first()
        module.enabled = False
        module.status = "initialized"
        db.flush()

        with patch("services.execution.task_dispatch.dispatch_apply") as mock_dispatch:
            with pytest.raises(BadRequestError, match="(?i)disabled"):
                svc.submit_apply(module.id)
            mock_dispatch.assert_not_called()


# ── Cancel actually stops the work (issues #462, #527 part 2) ────────


class TestCancelStopsRealWork:
    """Cancel must revoke queued tasks and kill the daemon-side container.

    Issue #462: `revoke(terminate=True)` SIGKILLs the worker-side `docker run`
    client; the container keeps running against live infrastructure while Forge
    reports "cancelled" and drops the module lock. And matching only
    `status == "in_progress"` left a task queued behind it un-revoked, so the
    worker ran the full apply after the user was told it stopped.
    """

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_cancel_revokes_and_marks_a_queued_task(
        self, _deps, _counts, svc, db, project_and_lib, make_task
    ):
        """F4: a queued task is revoked and marked cancelled, not left live."""
        from models import ProjectModule
        from models import Task as TaskModel

        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        module = db.query(ProjectModule).filter(ProjectModule.id == added["module_id"]).first()
        module.status = "applying"
        db.flush()

        queued = make_task(
            project=project, module=module, task_type="apply",
            status="queued", celery_task_id="celery-queued-1",
        )

        revoked = []
        with patch("celery_app.celery_app") as mock_celery, \
             patch.object(svc, "_kill_containers_for_tasks", return_value=[]):
            mock_celery.control.revoke.side_effect = lambda tid, **kw: revoked.append(tid)
            result = svc.cancel_operation(module.id)

        assert result["success"] is True
        assert "celery-queued-1" in revoked, (
            "queued task was not revoked — the worker would still run the full apply "
            "after the user was told it stopped (issue #462 F4)"
        )
        # The service marks; the route commits (project_execution.cancel_deployment),
        # so assert on the session object rather than re-reading the DB.
        assert queued.status == "cancelled", (
            "a revoked task left in 'queued' still looks live to the rest of the system"
        )
        assert result["tasks_cancelled"] == 1
        assert db.query(TaskModel).filter(
            TaskModel.module_id == module.id,
            TaskModel.status.in_(["queued", "pending", "in_progress"]),
        ).count() == 0

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_cancel_kills_the_daemon_side_container(
        self, _deps, _counts, svc, db, project_and_lib, make_task
    ):
        """F3: the container is killed, and killed BEFORE the lock is released."""
        from models import ProjectModule

        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        module = db.query(ProjectModule).filter(ProjectModule.id == added["module_id"]).first()
        module.status = "applying"
        db.flush()

        make_task(
            project=project, module=module, task_type="apply",
            status="in_progress", celery_task_id="celery-running-1",
        )

        order = []
        with patch("celery_app.celery_app"), \
             patch("services.execution.task_dispatch.get_engine_type", return_value="container"), \
             patch("services.execution.container_runner.DockerRunner") as mock_runner, \
             patch("services.module_lock.ModuleLockService") as mock_lock:
            mock_runner.return_value.kill_task_containers.side_effect = (
                lambda tid: order.append("kill") or ["abc123def456"]
            )
            mock_lock.return_value.force_release.side_effect = lambda mid: order.append("unlock")

            result = svc.cancel_operation(module.id)

        assert result["containers_killed"] == 1, (
            "the daemon-side container was not killed — it keeps mutating the "
            "workspace while Forge reports the operation cancelled (issue #462 F3)"
        )
        assert order == ["kill", "unlock"], (
            f"lock must not be released while a container is still live, got {order}"
        )

    @patch("services.project_module_service.update_project_counts")
    @patch("services.project_module_service.detect_module_dependencies", return_value=[])
    def test_cancel_skips_container_kill_for_non_container_engines(
        self, _deps, _counts, svc, db, project_and_lib, make_task
    ):
        """An OpenTofu module has no step container — don't go looking for one."""
        from models import ProjectModule

        project, lib = project_and_lib
        added = svc.add_module(project.id, lib.id, "infra/vpc")
        module = db.query(ProjectModule).filter(ProjectModule.id == added["module_id"]).first()
        module.status = "applying"
        db.flush()

        make_task(
            project=project, module=module, task_type="apply",
            status="in_progress", celery_task_id="celery-running-2",
        )

        with patch("celery_app.celery_app"), \
             patch("services.execution.task_dispatch.get_engine_type", return_value="opentofu"), \
             patch("services.execution.container_runner.DockerRunner") as mock_runner:
            result = svc.cancel_operation(module.id)

        mock_runner.assert_not_called()
        assert result["containers_killed"] == 0
