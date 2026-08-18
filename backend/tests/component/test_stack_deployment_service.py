"""
Tests for services.stack_deployment_service — stack deployment orchestration.

BC-007: StackDeploymentService — real DB, mock only Celery task dispatch.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.encryption import encrypt_value
from models import ProjectModule, ProjectSecret
from models.enums import ModuleStatus, StackInstanceStatus
from models.system import ApplicationSetting
from services.stack_deployment_service import StackDeploymentService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_secret(db, project_id, name, *, is_active=True, value: str | None = None):
    """Insert a ProjectSecret directly into the test DB."""
    secret = ProjectSecret(
        project_id=project_id,
        name=name,
        secret_type="value",
        value_encrypted=encrypt_value(value) if value is not None else None,
        is_active=is_active,
    )
    db.add(secret)
    db.flush()
    return secret


# ---------------------------------------------------------------------------
# get_required_secret_prerequisites
# ---------------------------------------------------------------------------

class TestGetRequiredSecretPrerequisites:
    """Extract project_secret prerequisites from a template."""

    def test_returns_matching_prerequisites(self, db, make_stack_template):
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "aws_access_key", "description": "AWS key"},
            {"type": "project_secret", "name": "db_password", "description": "DB pass"},
        ])
        svc = StackDeploymentService(db)
        result = svc.get_required_secret_prerequisites(t)
        assert len(result) == 2
        assert result[0]["name"] == "aws_access_key"
        assert result[1]["name"] == "db_password"

    def test_filters_non_secret_prerequisites(self, db, make_stack_template):
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "api_key"},
            {"type": "k8s_cluster", "name": "cluster"},
            {"type": "dns_zone", "name": "zone"},
        ])
        svc = StackDeploymentService(db)
        result = svc.get_required_secret_prerequisites(t)
        assert len(result) == 1
        assert result[0]["name"] == "api_key"

    def test_empty_prerequisites(self, db, make_stack_template):
        t = make_stack_template(prerequisites=[])
        svc = StackDeploymentService(db)
        result = svc.get_required_secret_prerequisites(t)
        assert result == []

    def test_none_prerequisites(self, db, make_stack_template):
        t = make_stack_template(prerequisites=None)
        svc = StackDeploymentService(db)
        result = svc.get_required_secret_prerequisites(t)
        assert result == []

    def test_skips_entry_without_name(self, db, make_stack_template):
        t = make_stack_template(prerequisites=[
            {"type": "project_secret"},
            {"type": "project_secret", "name": "valid_secret"},
        ])
        svc = StackDeploymentService(db)
        result = svc.get_required_secret_prerequisites(t)
        assert len(result) == 1
        assert result[0]["name"] == "valid_secret"


# ---------------------------------------------------------------------------
# validate_required_secrets
# ---------------------------------------------------------------------------

class TestValidateRequiredSecrets:
    """Pre-flight secrets check against the project."""

    def test_all_secrets_present(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "aws_key", "description": "AWS key"},
        ])
        _add_secret(db, p.id, "aws_key")
        svc = StackDeploymentService(db)
        ok, missing = svc.validate_required_secrets(p.id, t)
        assert ok is True
        assert missing == []

    def test_missing_secrets_detected(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "aws_key", "description": "AWS key"},
            {"type": "project_secret", "name": "db_pass", "description": "DB password"},
        ])
        _add_secret(db, p.id, "aws_key")
        svc = StackDeploymentService(db)
        ok, missing = svc.validate_required_secrets(p.id, t)
        assert ok is False
        assert len(missing) == 1
        assert missing[0]["name"] == "db_pass"
        assert missing[0]["description"] == "DB password"

    def test_inactive_secrets_not_counted(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "api_token"},
        ])
        _add_secret(db, p.id, "api_token", is_active=False)
        svc = StackDeploymentService(db)
        ok, missing = svc.validate_required_secrets(p.id, t)
        assert ok is False
        assert len(missing) == 1

    def test_no_required_secrets_passes(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[])
        svc = StackDeploymentService(db)
        ok, missing = svc.validate_required_secrets(p.id, t)
        assert ok is True
        assert missing == []

    def test_missing_description_defaults_empty(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "no_desc"},
        ])
        svc = StackDeploymentService(db)
        ok, missing = svc.validate_required_secrets(p.id, t)
        assert ok is False
        assert missing[0]["description"] == ""


# ---------------------------------------------------------------------------
# check_prerequisites
# ---------------------------------------------------------------------------

class TestCheckPrerequisites:
    """Full prerequisites check returning structured result."""

    def test_all_satisfied(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "key_a", "description": "Key A"},
        ])
        _add_secret(db, p.id, "key_a")
        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)
        assert result["all_satisfied"] is True
        assert result["missing_secrets"] == []
        assert len(result["required_secrets"]) == 1
        assert result["required_secrets"][0]["exists"] is True

    def test_partially_satisfied(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "present_key"},
            {"type": "project_secret", "name": "missing_key", "description": "Needed key"},
        ])
        _add_secret(db, p.id, "present_key")
        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)
        assert result["all_satisfied"] is False
        assert len(result["missing_secrets"]) == 1
        assert result["missing_secrets"][0]["name"] == "missing_key"
        assert len(result["required_secrets"]) == 2

    def test_no_prerequisites_returns_satisfied(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[])
        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)
        assert result["all_satisfied"] is True
        assert result["missing_secrets"] == []
        assert result["required_secrets"] == []

    def test_only_non_secret_prerequisites_returns_satisfied(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "k8s_cluster", "name": "main-cluster"},
        ])
        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)
        assert result["all_satisfied"] is True

    def test_check_prerequisites_exposes_project_only_secret_policy(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "cne_pull_secret"},
        ])
        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)
        assert result["secret_source_policy"] == {
            "project_secret_precedence": True,
            "global_cne_pull_secret_default_supported": True,
        }

    def test_check_prerequisites_missing_cne_pull_secret_includes_guidance(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "cne_pull_secret", "description": "FAR pull secret"},
        ])
        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)
        assert result["all_satisfied"] is False
        assert len(result["missing_secrets"]) == 1
        missing = result["missing_secrets"][0]
        assert missing["name"] == "cne_pull_secret"
        assert "global fallback" in missing["guidance"]

    def test_check_prerequisites_uses_system_default_for_cne_pull_secret(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "cne_pull_secret", "description": "FAR pull secret"},
        ])
        db.add(ApplicationSetting(
            key="bnk.far_pull_secret_default",
            value=encrypt_value("eyJhdXRocyI6eyJyZXBvLmY1LmNvbSI6eyJhdXRoIjoiZEdWemREbz0ifX19") or "",
            value_type="string",
            description="FAR fallback",
            category="bnk",
            is_encrypted=True,
        ))
        db.flush()

        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)
        assert result["all_satisfied"] is True
        assert result["required_secrets"][0]["source"] == "system_default"
        assert result["required_secrets"][0]["valid"] is True

    def test_check_prerequisites_reports_invalid_cne_pull_secret_content(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "cne_pull_secret", "description": "FAR pull secret"},
        ])
        _add_secret(db, p.id, "cne_pull_secret", value="not-base64")
        db.flush()

        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)
        assert result["all_satisfied"] is False
        assert len(result["missing_secrets"]) == 1
        entry = result["missing_secrets"][0]
        assert entry["name"] == "cne_pull_secret"
        assert entry["exists"] is True
        assert entry["valid"] is False
        assert "base64" in (entry.get("validation_error") or "")

    def test_check_prerequisites_reports_invalid_jwt_token(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "jwt_token", "description": "JWT"},
        ])
        _add_secret(db, p.id, "jwt_token", value="invalid-jwt")
        db.flush()

        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)
        assert result["all_satisfied"] is False
        assert len(result["missing_secrets"]) == 1
        entry = result["missing_secrets"][0]
        assert entry["name"] == "jwt_token"
        assert entry["valid"] is False
        assert "three dot-separated" in (entry.get("validation_error") or "")

    def test_check_prerequisites_existing_cluster_fail_when_no_registered_cluster(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(slug="bnk-on-k8s", prerequisites=[])
        svc = StackDeploymentService(db)

        result = svc.check_prerequisites(p.id, t)

        assert result["all_satisfied"] is False
        assert result["selected_cluster"] is None
        assert result["preflight_summary"]["blocking_failures"] == 1
        checks = {c["id"]: c for c in result["preflight_checks"]}
        assert checks["cluster_selected"]["status"] == "fail"
        assert checks["cluster_selected"]["blocking"] is True

    @patch("services.stack_deployment_service.client.VersionApi")
    @patch("services.stack_deployment_service.client.CoreV1Api")
    def test_check_prerequisites_existing_cluster_runtime_reachability_passes(
        self,
        mock_core_v1,
        mock_version_api,
        db,
        make_project,
        make_stack_template,
        make_k8s_cluster,
    ):
        p = make_project()
        make_k8s_cluster(project=p)
        t = make_stack_template(slug="bnk-on-k8s", prerequisites=[])

        mock_k8s_client = MagicMock()
        core_api_instance = mock_core_v1.return_value
        core_api_instance.list_namespace.return_value = MagicMock()

        version_info = MagicMock()
        version_info.major = "1"
        version_info.minor = "29"
        mock_version_api.return_value.get_code.return_value = version_info

        svc = StackDeploymentService(db)
        svc.k8s_service.load_kubeconfig = MagicMock(return_value=mock_k8s_client)

        result = svc.check_prerequisites(p.id, t)

        assert result["all_satisfied"] is True
        assert result["selected_cluster"]["id"] is not None
        checks = {c["id"]: c for c in result["preflight_checks"]}
        assert checks["cluster_selected"]["status"] == "pass"
        assert checks["cluster_runtime_reachability"]["status"] == "pass"
        assert result["preflight_summary"]["blocking_failures"] == 0

    @patch("services.stack_deployment_service.client.VersionApi")
    @patch("services.stack_deployment_service.client.CoreV1Api")
    def test_check_prerequisites_existing_cluster_multicluster_ambiguity_is_blocking_failure(
        self,
        mock_core_v1,
        mock_version_api,
        db,
        make_project,
        make_stack_template,
        make_k8s_cluster,
    ):
        p = make_project()
        make_k8s_cluster(project=p, name="cluster-a")
        make_k8s_cluster(project=p, name="cluster-b")
        t = make_stack_template(slug="bnk-on-k8s", prerequisites=[])

        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)

        assert result["all_satisfied"] is False
        assert result["selected_cluster"] is None
        checks = {c["id"]: c for c in result["preflight_checks"]}
        assert checks["cluster_selection_ambiguity"]["status"] == "fail"
        assert checks["cluster_selection_ambiguity"]["blocking"] is True
        assert checks["cluster_runtime_reachability"]["status"] == "warn"
        assert result["preflight_summary"]["blocking_failures"] == 1

        mock_core_v1.assert_not_called()
        mock_version_api.assert_not_called()

    def test_check_prerequisites_existing_cluster_reachability_failure_is_blocking(
        self,
        db,
        make_project,
        make_stack_template,
        make_k8s_cluster,
    ):
        p = make_project()
        make_k8s_cluster(project=p)
        t = make_stack_template(slug="bnk-on-k8s", prerequisites=[])

        svc = StackDeploymentService(db)
        svc.k8s_service.load_kubeconfig = MagicMock(side_effect=RuntimeError("connection refused"))

        result = svc.check_prerequisites(p.id, t)

        assert result["all_satisfied"] is False
        checks = {c["id"]: c for c in result["preflight_checks"]}
        assert checks["cluster_runtime_reachability"]["status"] == "fail"

    def test_check_prerequisites_existing_cluster_loopback_api_server_is_blocking_with_guidance(
        self,
        db,
        make_project,
        make_stack_template,
        make_k8s_cluster,
    ):
        p = make_project()
        cluster = make_k8s_cluster(project=p, api_server="https://127.0.0.1:6443", ssh_tunnel_enabled=False)
        t = make_stack_template(slug="bnk-on-k8s", prerequisites=[])

        svc = StackDeploymentService(db)

        result = svc.check_prerequisites(p.id, t)

        assert result["all_satisfied"] is False
        assert result["selected_cluster"]["id"] == cluster.id
        checks = {c["id"]: c for c in result["preflight_checks"]}
        reachability = checks["cluster_runtime_reachability"]
        assert reachability["status"] == "fail"
        assert reachability["blocking"] is True
        assert "host-loopback endpoint" in reachability["message"]
        assert "not localhost/127.0.0.1" in reachability["guidance"]
        assert result["preflight_summary"]["blocking_failures"] == 1

    @patch("services.stack_deployment_service.client.VersionApi")
    @patch("services.stack_deployment_service.client.CoreV1Api")
    def test_check_prerequisites_existing_cluster_loopback_allowed_when_ssh_tunnel_enabled(
        self,
        mock_core_v1,
        mock_version_api,
        db,
        make_project,
        make_stack_template,
        make_k8s_cluster,
    ):
        p = make_project()
        make_k8s_cluster(project=p, api_server="https://localhost:6443", ssh_tunnel_enabled=True)
        t = make_stack_template(slug="bnk-on-k8s", prerequisites=[])

        mock_k8s_client = MagicMock()
        core_api_instance = mock_core_v1.return_value
        core_api_instance.list_namespace.return_value = MagicMock()

        version_info = MagicMock()
        version_info.major = "1"
        version_info.minor = "30"
        mock_version_api.return_value.get_code.return_value = version_info

        svc = StackDeploymentService(db)
        svc.k8s_service.load_kubeconfig = MagicMock(return_value=mock_k8s_client)

        result = svc.check_prerequisites(p.id, t)

        assert result["all_satisfied"] is True
        checks = {c["id"]: c for c in result["preflight_checks"]}
        assert checks["cluster_runtime_reachability"]["status"] == "pass"

    def test_check_prerequisites_existing_cluster_alias_slug_applies_runtime_reachability_guard(
        self,
        db,
        make_project,
        make_stack_template,
        make_k8s_cluster,
    ):
        p = make_project()
        make_k8s_cluster(project=p, api_server="https://localhost:6443", ssh_tunnel_enabled=False)
        t = make_stack_template(slug="f5-bnk-2.2", prerequisites=[])

        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t)

        assert result["all_satisfied"] is False
        checks = {c["id"]: c for c in result["preflight_checks"]}
        assert checks["cluster_selected"]["status"] == "pass"
        assert checks["cluster_runtime_reachability"]["status"] == "fail"
        assert checks["cluster_runtime_reachability"]["blocking"] is True


# ---------------------------------------------------------------------------
# SEC-001: _extract_variable_names
# ---------------------------------------------------------------------------

class TestExtractVariableNames:
    """Unit tests for _extract_variable_names helper."""

    def test_flat_variables(self, db):
        svc = StackDeploymentService(db)
        result = svc._extract_variable_names({"jwt_token": "abc", "empty": ""})
        assert result == {"jwt_token"}

    def test_nested_module_variables(self, db):
        svc = StackDeploymentService(db)
        result = svc._extract_variable_names({
            "bnk/flo": {"jwt_token": "abc", "empty": ""},
            "k8s/bnk-prerequisites": {"cne_pull_secret": "xyz"},
        })
        assert result == {"jwt_token", "cne_pull_secret"}

    def test_mixed_flat_and_nested(self, db):
        svc = StackDeploymentService(db)
        result = svc._extract_variable_names({
            "flat_key": "value",
            "module/path": {"nested_key": "value2"},
        })
        assert result == {"flat_key", "nested_key"}

    def test_none_returns_empty(self, db):
        svc = StackDeploymentService(db)
        assert svc._extract_variable_names(None) == set()

    def test_empty_dict_returns_empty(self, db):
        svc = StackDeploymentService(db)
        assert svc._extract_variable_names({}) == set()


# ---------------------------------------------------------------------------
# SEC-001: check_prerequisites with stack_variables
# ---------------------------------------------------------------------------

class TestCheckPrerequisitesWithStackVariables:
    """Check prerequisites considering stack instance variables (SEC-001)."""

    def test_stack_variable_satisfies_missing_secret(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "jwt_token", "description": "JWT"},
        ])
        svc = StackDeploymentService(db)

        # Without stack_variables — missing
        result = svc.check_prerequisites(p.id, t)
        assert result["all_satisfied"] is False
        assert len(result["missing_secrets"]) == 1

        # With stack_variables — satisfied
        result = svc.check_prerequisites(p.id, t, stack_variables={
            "bnk/flo": {"jwt_token": "header.payload.signature"},
        })
        assert result["all_satisfied"] is True
        assert result["missing_secrets"] == []
        assert result["required_secrets"][0]["exists"] is True
        assert result["required_secrets"][0]["source"] == "stack_variable"

    def test_flat_stack_variable_satisfies_secret(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "cne_pull_secret"},
        ])
        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t, stack_variables={
            "cne_pull_secret": "eyJhdXRocyI6eyJyZXBvLmY1LmNvbSI6eyJhdXRoIjoiZEdWemREbz0ifX19",
        })
        assert result["all_satisfied"] is True
        assert result["required_secrets"][0]["source"] == "stack_variable"

    def test_project_secret_takes_precedence_over_stack_variable(self, db, make_project, make_stack_template):
        """When a project secret exists, source should be 'project' not 'stack_variable'."""
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "jwt_token"},
        ])
        _add_secret(db, p.id, "jwt_token", value="header.payload.signature")
        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t, stack_variables={
            "bnk/flo": {"jwt_token": "from-wizard"},
        })
        assert result["all_satisfied"] is True
        # Project secret found first, so source is "project"
        assert result["required_secrets"][0]["source"] == "project"

    def test_empty_stack_variable_does_not_satisfy(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "jwt_token"},
        ])
        svc = StackDeploymentService(db)
        result = svc.check_prerequisites(p.id, t, stack_variables={
            "bnk/flo": {"jwt_token": ""},
        })
        assert result["all_satisfied"] is False
        assert len(result["missing_secrets"]) == 1

    def test_multiple_secrets_partial_satisfaction(self, db, make_project, make_stack_template):
        p = make_project()
        t = make_stack_template(prerequisites=[
            {"type": "project_secret", "name": "jwt_token"},
            {"type": "project_secret", "name": "cne_pull_secret"},
        ])
        svc = StackDeploymentService(db)
        # Only jwt_token in stack vars, cne_pull_secret missing
        result = svc.check_prerequisites(p.id, t, stack_variables={
            "bnk/flo": {"jwt_token": "header.payload.signature"},
        })
        assert result["all_satisfied"] is False
        assert len(result["missing_secrets"]) == 1
        assert result["missing_secrets"][0]["name"] == "cne_pull_secret"
        # jwt_token should be satisfied
        jwt_entry = [s for s in result["required_secrets"] if s["name"] == "jwt_token"][0]
        assert jwt_entry["exists"] is True


# ---------------------------------------------------------------------------
# create_project_modules_from_template
# ---------------------------------------------------------------------------

class TestCreateProjectModulesFromTemplate:
    """Create ProjectModules from stack template definitions."""

    def test_creates_modules_from_template(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        lib_vpc = make_module_library(name="vpc", path="bnk/vpc")
        lib_eks = make_module_library(name="eks", path="bnk/eks")
        t = make_stack_template(modules=[
            {"path": "bnk/vpc", "name": "vpc", "variables": {"cidr": "10.0.0.0/16"}},
            {"path": "bnk/eks", "name": "eks", "variables": {"version": "1.28"}},
        ])
        si = make_stack_instance(project=p, template=t)
        svc = StackDeploymentService(db)
        success, modules, error = svc.create_project_modules_from_template(si)
        assert success is True
        assert error == ""
        assert len(modules) == 2
        assert modules[0].path_in_project == f"stack-{si.id}/bnk/vpc"
        assert modules[1].path_in_project == f"stack-{si.id}/bnk/eks"
        assert modules[0].variable_overrides == {"cidr": "10.0.0.0/16"}
        assert modules[0].status == ModuleStatus.NOT_INITIALIZED

    def test_sets_deployment_order(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        make_module_library(name="a", path="mod/a")
        make_module_library(name="b", path="mod/b")
        make_module_library(name="c", path="mod/c")
        t = make_stack_template(modules=[
            {"path": "mod/a", "name": "a"},
            {"path": "mod/b", "name": "b"},
            {"path": "mod/c", "name": "c"},
        ])
        si = make_stack_instance(project=p, template=t)
        svc = StackDeploymentService(db)
        success, modules, _ = svc.create_project_modules_from_template(si)
        assert success is True
        assert [m.deployment_order for m in modules] == [0, 1, 2]

    def test_merges_instance_variables(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template(modules=[
            {"path": "bnk/vpc", "name": "vpc", "variables": {"cidr": "10.0.0.0/16", "region": "us-east-1"}},
        ])
        si = make_stack_instance(
            project=p, template=t,
            variables={"bnk/vpc": {"cidr": "172.16.0.0/16"}, "env": "prod"},
        )
        svc = StackDeploymentService(db)
        success, modules, _ = svc.create_project_modules_from_template(si)
        assert success is True
        # Module-specific override takes precedence over template default
        assert modules[0].variable_overrides["cidr"] == "172.16.0.0/16"
        # Template default preserved for non-overridden vars
        assert modules[0].variable_overrides["region"] == "us-east-1"
        # Stack-wide flat variable applied
        assert modules[0].variable_overrides["env"] == "prod"

    def test_module_scoped_variables_are_mirrored_to_project_defaults(
        self,
        db,
        make_project,
        make_stack_template,
        make_stack_instance,
        make_module_library,
    ):
        p = make_project()
        make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template(modules=[
            {"path": "bnk/vpc", "name": "vpc", "variables": {}},
        ])
        si = make_stack_instance(
            project=p,
            template=t,
            variables={
                "bnk/vpc": {"user_ip": "5.6.7.8/32", "ecr_registry": "example.ecr.aws/abc"},
            },
        )

        svc = StackDeploymentService(db)
        success, modules, _ = svc.create_project_modules_from_template(si)

        assert success is True
        assert modules[0].variable_overrides["user_ip"] == "5.6.7.8/32"
        assert modules[0].variable_overrides["ecr_registry"] == "example.ecr.aws/abc"
        db.refresh(p)
        defaults = (p.project_variables or {}).get("variable_defaults", {})
        assert defaults["user_ip"] == "5.6.7.8/32"
        assert defaults["ecr_registry"] == "example.ecr.aws/abc"

    def test_aws_vpc_subnets_are_derived_from_vpc_cidr_when_template_defaults_drift(
        self,
        db,
        make_project,
        make_stack_template,
        make_stack_instance,
        make_module_library,
    ):
        p = make_project(cloud_provider="aws", region="ap-southeast-2")
        make_module_library(name="vpc", path="infra/aws/vpc")
        t = make_stack_template(modules=[
            {
                "path": "infra/aws/vpc",
                "name": "vpc",
                "variables": {
                    "vpc_cidr": "10.0.0.0/16",
                    "public_subnet_cidr": "10.0.1.0/24",
                    "private_external_subnet_a_cidr": "10.0.10.0/24",
                    "private_external_subnet_b_cidr": "10.0.11.0/24",
                    "private_internal_subnet_a_cidr": "10.0.20.0/24",
                    "private_internal_subnet_b_cidr": "10.0.21.0/24",
                },
            },
        ])
        si = make_stack_instance(
            project=p,
            template=t,
            variables={
                "vpc_cidr": "10.120.0.0/16",
            },
        )

        svc = StackDeploymentService(db)
        success, modules, error = svc.create_project_modules_from_template(si)

        assert success is True
        assert error == ""
        vars_out = modules[0].variable_overrides
        assert vars_out["vpc_cidr"] == "10.120.0.0/16"
        assert vars_out["public_subnet_cidr"] == "10.120.1.0/24"
        assert vars_out["private_external_subnet_a_cidr"] == "10.120.10.0/24"
        assert vars_out["private_external_subnet_b_cidr"] == "10.120.11.0/24"
        assert vars_out["private_internal_subnet_a_cidr"] == "10.120.20.0/24"
        assert vars_out["private_internal_subnet_b_cidr"] == "10.120.21.0/24"

    def test_aws_vpc_explicit_invalid_subnet_outside_vpc_fails_truthfully(
        self,
        db,
        make_project,
        make_stack_template,
        make_stack_instance,
        make_module_library,
    ):
        p = make_project(cloud_provider="aws", region="ap-southeast-2")
        make_module_library(name="vpc", path="infra/aws/vpc")
        t = make_stack_template(modules=[
            {
                "path": "infra/aws/vpc",
                "name": "vpc",
                "variables": {
                    "vpc_cidr": "10.0.0.0/16",
                    "public_subnet_cidr": "10.0.1.0/24",
                },
            },
        ])
        si = make_stack_instance(
            project=p,
            template=t,
            variables={
                "vpc_cidr": "10.120.0.0/16",
                "public_subnet_cidr": "10.0.1.0/24",
            },
        )

        svc = StackDeploymentService(db)
        success, modules, error = svc.create_project_modules_from_template(si)

        assert success is False
        assert modules == []
        assert "public_subnet_cidr" in error
        assert "not within vpc_cidr" in error

    def test_required_module_not_in_library_fails(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        p = make_project()
        t = make_stack_template(modules=[
            {"path": "nonexistent/module", "name": "ghost", "required": True},
        ])
        si = make_stack_instance(project=p, template=t)
        svc = StackDeploymentService(db)
        success, modules, error = svc.create_project_modules_from_template(si)
        assert success is False
        assert modules == []
        # D-029 P0: fail-closed aggregate error enumerates the missing path.
        assert "nonexistent/module" in error
        assert "missing from catalog" in error

    def test_all_missing_required_modules_aggregated_fail_closed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """D-029 P0: deploy fails closed listing EVERY missing required module
        (not just the first), before any ProjectModule is created."""
        p = make_project()
        # Only one of the four bnk-on-k8s modules is synced into the library.
        make_module_library(name="bnk-prerequisites", path="k8s/bnk-prerequisites")
        t = make_stack_template(modules=[
            {"path": "k8s/bnk-prerequisites", "name": "bnk-prerequisites", "required": True},
            {"path": "bnk/flo", "name": "flo", "required": True},
            {"path": "bnk/cneinstance", "name": "cneinstance", "required": True},
            {"path": "bnk/bnk-vlans", "name": "bnk-vlans", "required": True},
        ])
        si = make_stack_instance(project=p, template=t)
        svc = StackDeploymentService(db)

        success, modules, error = svc.create_project_modules_from_template(si)

        # Fail-closed: blocked before any apply.
        assert success is False
        assert modules == []
        # Every missing required path is enumerated (not just the first miss).
        assert "bnk/flo" in error
        assert "bnk/cneinstance" in error
        assert "bnk/bnk-vlans" in error
        # The one present module is NOT reported as missing.
        assert "k8s/bnk-prerequisites" not in error
        # No partial stack was persisted.
        assert (
            db.query(ProjectModule)
            .filter(ProjectModule.stack_instance_id == si.id)
            .count()
            == 0
        )

    def test_verify_template_modules_present_aggregates_and_surfaces_optional(
        self, db, make_stack_template, make_module_library
    ):
        """The preflight aggregates all missing required modules and surfaces
        (does not silently drop) absent optional modules."""
        make_module_library(name="present", path="bnk/present")
        t = make_stack_template(modules=[
            {"path": "bnk/present", "name": "present", "required": True},
            {"path": "bnk/missing-req-a", "name": "missing-req-a", "required": True},
            {"path": "bnk/missing-req-b", "name": "missing-req-b", "required": True},
            {"path": "bnk/missing-opt", "name": "missing-opt", "required": False},
        ])
        svc = StackDeploymentService(db)

        ok, missing_required, skipped_optional = svc.verify_template_modules_present(t)

        assert ok is False
        missing_paths = {m["path"] for m in missing_required}
        assert missing_paths == {"bnk/missing-req-a", "bnk/missing-req-b"}
        skipped_paths = {m["path"] for m in skipped_optional}
        assert skipped_paths == {"bnk/missing-opt"}

    def test_verify_template_modules_present_all_present(
        self, db, make_stack_template, make_module_library
    ):
        make_module_library(name="a", path="bnk/a")
        make_module_library(name="b", path="bnk/b")
        t = make_stack_template(modules=[
            {"path": "bnk/a", "name": "a", "required": True},
            {"path": "bnk/b", "name": "b", "required": True},
        ])
        svc = StackDeploymentService(db)

        ok, missing_required, skipped_optional = svc.verify_template_modules_present(t)

        assert ok is True
        assert missing_required == []
        assert skipped_optional == []

    def test_optional_module_not_in_library_skipped(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template(modules=[
            {"path": "bnk/vpc", "name": "vpc"},
            {"path": "bnk/optional", "name": "optional", "required": False},
        ])
        si = make_stack_instance(project=p, template=t)
        svc = StackDeploymentService(db)
        success, modules, _ = svc.create_project_modules_from_template(si)
        assert success is True
        assert len(modules) == 1

    def test_empty_template_modules_fails(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        p = make_project()
        t = make_stack_template(modules=[])
        si = make_stack_instance(project=p, template=t)
        svc = StackDeploymentService(db)
        success, modules, error = svc.create_project_modules_from_template(si)
        assert success is False
        assert "no modules defined" in error

    def test_updates_stack_instance_metadata(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        make_module_library(name="vpc", path="bnk/vpc")
        make_module_library(name="eks", path="bnk/eks")
        t = make_stack_template(modules=[
            {"path": "bnk/vpc", "name": "vpc"},
            {"path": "bnk/eks", "name": "eks"},
        ])
        si = make_stack_instance(project=p, template=t)
        svc = StackDeploymentService(db)
        success, modules, _ = svc.create_project_modules_from_template(si)
        assert success is True
        db.refresh(si)
        assert si.total_steps == 2
        assert si.current_step == 0
        assert len(si.deployed_modules) == 2

    def test_idempotent_on_retry(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """S14-006: Reuses existing ProjectModules from a previous failed attempt."""
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template(modules=[
            {"path": "bnk/vpc", "name": "vpc"},
        ])
        si = make_stack_instance(project=p, template=t)
        # Pre-create a module as if from a previous attempt
        existing = ProjectModule(
            project_id=p.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.NOT_INITIALIZED,
            deployment_order=0,
            stack_instance_id=si.id,
        )
        db.add(existing)
        db.flush()
        svc = StackDeploymentService(db)
        success, modules, _ = svc.create_project_modules_from_template(si)
        assert success is True
        assert len(modules) == 1
        assert modules[0].id == existing.id

    def test_deploy_stack_backfills_missing_required_template_module_into_existing_stack(
        self,
        db,
        make_project,
        make_stack_template,
        make_stack_instance,
        make_module_library,
    ):
        project = make_project()
        cert_manager = make_module_library(
            name="cert-manager",
            path="k8s/cert-manager",
            dependencies_metadata={"required": []},
        )
        issuer = make_module_library(
            name="bnk-cert-issuer",
            path="k8s/bnk-cert-issuer",
            dependencies_metadata={"required": [{"module": "k8s/cert-manager"}]},
        )
        template = make_stack_template(modules=[
            {"path": "k8s/cert-manager", "name": "cert-manager"},
            {"path": "k8s/bnk-cert-issuer", "name": "bnk-cert-issuer", "variables": {"namespace": "cert-manager"}},
        ])
        stack = make_stack_instance(project=project, template=template, status=StackInstanceStatus.FAILED)

        existing = ProjectModule(
            project_id=project.id,
            module_library_id=cert_manager.id,
            path_in_project=f"stack-{stack.id}/k8s/cert-manager",
            status=ModuleStatus.APPLIED,
            deployment_order=0,
            stack_instance_id=stack.id,
            variable_overrides={},
            dependencies=[],
        )
        db.add(existing)
        db.commit()

        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(stack)

        assert success is True
        assert "2 modules" in msg
        db.refresh(stack)
        modules = db.query(ProjectModule).filter(ProjectModule.stack_instance_id == stack.id).order_by(ProjectModule.deployment_order).all()
        assert [module.path_in_project for module in modules] == [
            f"stack-{stack.id}/k8s/cert-manager",
            f"stack-{stack.id}/k8s/bnk-cert-issuer",
        ]
        assert modules[0].id == existing.id
        assert modules[1].module_library_id == issuer.id
        assert modules[1].status == ModuleStatus.NOT_INITIALIZED
        assert modules[1].variable_overrides == {"namespace": "cert-manager"}
        assert modules[1].dependencies == [existing.id]
        assert stack.total_steps == 2
        assert sum(1 for module in modules if module.status == ModuleStatus.APPLIED) == 1

    def test_deploy_stack_reconciles_existing_module_order_and_dependencies_without_creating_new_modules(
        self,
        db,
        make_project,
        make_stack_template,
        make_stack_instance,
        make_module_library,
    ):
        project = make_project()
        prereqs = make_module_library(
            name="bnk-prerequisites",
            path="k8s/bnk-prerequisites",
            dependencies_metadata={"required": []},
        )
        cert_manager = make_module_library(
            name="cert-manager",
            path="k8s/cert-manager",
            dependencies_metadata={"required": [{"module": "k8s/bnk-prerequisites"}]},
        )
        template = make_stack_template(modules=[
            {"path": "k8s/bnk-prerequisites", "name": "prereqs"},
            {"path": "k8s/cert-manager", "name": "cert-manager"},
        ])
        stack = make_stack_instance(project=project, template=template, status=StackInstanceStatus.FAILED)

        existing_cert = ProjectModule(
            project_id=project.id,
            module_library_id=cert_manager.id,
            path_in_project=f"stack-{stack.id}/k8s/cert-manager",
            status=ModuleStatus.NOT_INITIALIZED,
            deployment_order=0,
            stack_instance_id=stack.id,
            variable_overrides={},
            dependencies=[],
        )
        existing_prereqs = ProjectModule(
            project_id=project.id,
            module_library_id=prereqs.id,
            path_in_project=f"stack-{stack.id}/k8s/bnk-prerequisites",
            status=ModuleStatus.APPLIED,
            deployment_order=1,
            stack_instance_id=stack.id,
            variable_overrides={},
            dependencies=[999999],
        )
        db.add_all([existing_cert, existing_prereqs])
        db.commit()

        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(stack)

        assert success is True
        assert "2 modules" in msg
        db.refresh(stack)
        modules = db.query(ProjectModule).filter(ProjectModule.stack_instance_id == stack.id).order_by(ProjectModule.deployment_order, ProjectModule.id).all()
        assert len(modules) == 2
        assert [module.id for module in modules] == [existing_prereqs.id, existing_cert.id]
        assert existing_prereqs.dependencies == []
        assert existing_cert.dependencies == [existing_prereqs.id]
        assert stack.total_steps == 2
        assert sum(1 for module in modules if module.status == ModuleStatus.APPLIED) == 1
        assert set(stack.deployed_modules) == {existing_prereqs.id, existing_cert.id}


# ---------------------------------------------------------------------------
# deploy_stack
# ---------------------------------------------------------------------------

class TestDeployStack:
    """Deploy flow with status transitions."""

    def test_deploy_pending_stack_succeeds(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template(modules=[
            {"path": "bnk/vpc", "name": "vpc"},
        ])
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.PENDING)
        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(si)
        assert success is True
        assert "1 modules" in msg
        db.refresh(si)
        assert si.status == StackInstanceStatus.PENDING
        assert si.started_at is not None

    def test_deploy_already_deploying_rejected(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        p = make_project()
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYING)
        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(si)
        assert success is False
        assert "already deploying" in msg

    def test_deploy_destroying_rejected(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        p = make_project()
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DESTROYING)
        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(si)
        assert success is False
        assert "being destroyed" in msg

    def test_deploy_deployed_allows_redeploy(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """S18-001: Allow redeployment of already-deployed stacks."""
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template(modules=[{"path": "bnk/vpc", "name": "vpc"}])
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        # Attach a module so the count is nonzero
        pm = ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id,
        )
        db.add(pm)
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(si)
        assert success is True
        assert "redeployment" in msg

    def test_deploy_fails_with_missing_secrets(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        """S19-001: Deployment blocked when required secrets are missing."""
        p = make_project()
        t = make_stack_template(
            modules=[{"path": "bnk/vpc", "name": "vpc"}],
            prerequisites=[{"type": "project_secret", "name": "aws_key"}],
        )
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.PENDING)
        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(si)
        assert success is False
        assert "Missing required project secrets" in msg
        assert "aws_key" in msg

    def test_deploy_fails_with_cne_pull_secret_specific_guidance(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        p = make_project()
        t = make_stack_template(
            modules=[{"path": "bnk/vpc", "name": "vpc"}],
            prerequisites=[{"type": "project_secret", "name": "cne_pull_secret"}],
        )
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.PENDING)
        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(si)
        assert success is False
        assert "cne_pull_secret" in msg
        assert "global fallback" in msg

    def test_deploy_fails_when_no_modules_created(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        p = make_project()
        t = make_stack_template(modules=[
            {"path": "missing/mod", "name": "ghost", "required": True},
        ])
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.PENDING)
        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(si)
        assert success is False
        db.refresh(si)
        assert si.status == StackInstanceStatus.FAILED
        assert si.completed_at is not None

    def test_deploy_sets_status_failed_on_empty_template(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        p = make_project()
        t = make_stack_template(modules=[])
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.PENDING)
        svc = StackDeploymentService(db)
        success, msg = svc.deploy_stack(si)
        assert success is False
        db.refresh(si)
        assert si.status == StackInstanceStatus.FAILED


# ---------------------------------------------------------------------------
# update_stack_progress
# ---------------------------------------------------------------------------

class TestUpdateStackProgress:
    """Progress tracking based on module statuses."""

    def test_no_modules_no_op(self, db, make_project, make_stack_template, make_stack_instance):
        p = make_project()
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYING)
        svc = StackDeploymentService(db)
        svc.update_stack_progress(si)
        db.refresh(si)
        # No modules attached — should be no-op
        assert si.status == StackInstanceStatus.DEPLOYING

    def test_all_applied_marks_deployed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYING)
        pm = ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id,
        )
        db.add(pm)
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        svc.update_stack_progress(si)
        db.refresh(si)
        assert si.status == StackInstanceStatus.DEPLOYED
        assert si.completed_at is not None
        assert si.current_step == 1

    def test_failed_module_marks_stack_failed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYING)
        pm = ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLY_FAILED, stack_instance_id=si.id,
            deployment_error="apply error",
        )
        db.add(pm)
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        svc.update_stack_progress(si)
        db.refresh(si)
        assert si.status == StackInstanceStatus.FAILED
        assert "apply error" in si.error_message

    def test_partial_progress_stays_deploying(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        from models import Task as TaskModel
        from models.enums import TaskStatus

        p = make_project()
        lib_a = make_module_library(name="a", path="mod/a")
        lib_b = make_module_library(name="b", path="mod/b")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYING)
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib_a.id,
            path_in_project=f"stack-{si.id}/mod/a",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id,
        ))
        pm_b = ProjectModule(
            project_id=p.id, module_library_id=lib_b.id,
            path_in_project=f"stack-{si.id}/mod/b",
            status=ModuleStatus.APPLYING, stack_instance_id=si.id,
        )
        db.add(pm_b)
        db.flush()
        # Create an active Task so BUG-009 stale recovery does NOT reset this
        # genuinely in-progress module from APPLYING → not_initialized.
        task = TaskModel(
            project_id=p.id,
            module_id=pm_b.id,
            task_type="apply",
            status=TaskStatus.IN_PROGRESS,
            celery_task_id="fake-celery-id",
        )
        db.add(task)
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        svc.update_stack_progress(si)
        db.refresh(si)
        assert si.status == StackInstanceStatus.DEPLOYING
        assert si.current_step == 1

    def test_destroying_all_destroyed_marks_destroyed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DESTROYING)
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.DESTROYED, stack_instance_id=si.id,
        ))
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        svc.update_stack_progress(si)
        db.refresh(si)
        assert si.status == StackInstanceStatus.DESTROYED
        assert si.completed_at is not None

    def test_destroying_with_failure_marks_failed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DESTROYING)
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.DESTROY_FAILED, stack_instance_id=si.id,
            deployment_error="destroy failed: timeout",
        ))
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        svc.update_stack_progress(si)
        db.refresh(si)
        assert si.status == StackInstanceStatus.FAILED
        assert "destroy failed" in si.error_message

    def test_destroying_no_modules_left_marks_destroyed(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        """When all modules are deleted during destroy, stack becomes destroyed."""
        p = make_project()
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DESTROYING)
        svc = StackDeploymentService(db)
        svc.update_stack_progress(si)
        db.refresh(si)
        assert si.status == StackInstanceStatus.DESTROYED
        assert si.completed_at is not None


# ---------------------------------------------------------------------------
# get_deployment_status
# ---------------------------------------------------------------------------

class TestGetDeploymentStatus:
    """Status dict for API responses."""

    def test_no_modules_returns_empty(self, db, make_project, make_stack_template, make_stack_instance):
        p = make_project()
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.PENDING)
        svc = StackDeploymentService(db)
        result = svc.get_deployment_status(si)
        assert result["status"] == StackInstanceStatus.PENDING
        assert result["current_step"] == 0
        assert result["total_steps"] == 0
        assert result["modules"] == []
        assert result["progress_percentage"] == 0

    def test_returns_module_details(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYING)
        si.total_steps = 2
        si.current_step = 1
        pm = ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id,
            deployment_order=0,
        )
        db.add(pm)
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        result = svc.get_deployment_status(si)
        assert result["status"] == StackInstanceStatus.DEPLOYING
        assert result["total_steps"] == 2
        assert result["current_step"] == 1
        assert len(result["modules"]) == 1
        assert result["modules"][0]["name"] == "vpc"
        assert result["modules"][0]["status"] == ModuleStatus.APPLIED
        assert result["progress_percentage"] == 100  # 1 applied / 1 total

    def test_progress_percentage_calculation(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        lib_a = make_module_library(name="a", path="mod/a")
        lib_b = make_module_library(name="b", path="mod/b")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYING)
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib_a.id,
            path_in_project=f"stack-{si.id}/mod/a",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id, deployment_order=0,
        ))
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib_b.id,
            path_in_project=f"stack-{si.id}/mod/b",
            status=ModuleStatus.APPLYING, stack_instance_id=si.id, deployment_order=1,
        ))
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        result = svc.get_deployment_status(si)
        assert result["progress_percentage"] == 50  # 1 applied / 2 total

    def test_includes_timestamps_and_error(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        from datetime import UTC, datetime
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        now = datetime.now(UTC)
        si = make_stack_instance(
            project=p, template=t, status=StackInstanceStatus.FAILED,
        )
        si.started_at = now
        si.completed_at = now
        si.error_message = "something broke"
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLY_FAILED, stack_instance_id=si.id,
            deployment_order=0,
        ))
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        result = svc.get_deployment_status(si)
        assert result["started_at"] is not None
        assert result["completed_at"] is not None
        assert result["error_message"] == "something broke"


# ---------------------------------------------------------------------------
# destroy_stack
# ---------------------------------------------------------------------------

class TestDestroyStack:
    """Destroy flow — mocks Celery task dispatch."""

    def test_destroy_with_applied_modules(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """D-001 Phase 3 (event-chain): destroy initiates first wave dispatch."""
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id,
        ))
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)

        with patch.object(svc, '_dispatch_first_destroy_wave_stack', return_value="mock-task-id") as mock_wave:
            success, msg = svc.destroy_stack(si)

        assert success is True
        assert "1 modules" in msg
        mock_wave.assert_called_once()
        db.refresh(si)
        assert si.status == StackInstanceStatus.DESTROYING

    def test_destroy_no_modules_deletes_instance(
        self, db, make_project, make_stack_template, make_stack_instance
    ):
        p = make_project()
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        si_id = si.id
        svc = StackDeploymentService(db)
        success, msg = svc.destroy_stack(si)
        assert success is True
        assert "no modules" in msg.lower()
        from models import StackInstance
        assert db.query(StackInstance).filter_by(id=si_id).first() is None

    def test_destroy_already_destroying_rejected(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DESTROYING)
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id,
        ))
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        success, msg = svc.destroy_stack(si)
        assert success is False
        assert "already being destroyed" in msg

    def test_destroy_skips_not_initialized_modules(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Modules without infrastructure are deleted directly, not destroyed."""
        p = make_project()
        lib_a = make_module_library(name="vpc", path="bnk/vpc")
        lib_b = make_module_library(name="rds", path="bnk/rds")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        # Module with infra — needs destroy
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib_a.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id,
        ))
        # Module without infra — delete directly
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib_b.id,
            path_in_project=f"stack-{si.id}/bnk/rds",
            status=ModuleStatus.NOT_INITIALIZED, stack_instance_id=si.id,
        ))
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)

        with patch.object(svc, '_dispatch_first_destroy_wave_stack', return_value="mock-task-id") as mock_wave:
            success, msg = svc.destroy_stack(si)

        assert success is True
        assert "1 modules" in msg  # Only 1 module needs actual destroy
        mock_wave.assert_called_once()

    def test_destroy_blocks_when_module_in_progress(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """BUG-005: Cannot destroy stack when a module has a genuinely active operation.

        BUG-008 stale recovery auto-resets modules stuck in transitional status
        with no active Task. To simulate a genuinely in-progress module, we
        create a Task record in IN_PROGRESS status so the stale recovery skips it.
        """
        from models import Task as TaskModel
        from models.enums import TaskStatus

        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        pm = ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLYING, stack_instance_id=si.id,
        )
        db.add(pm)
        db.flush()
        # Create an active task so BUG-008 stale recovery does NOT reset this module
        task = TaskModel(
            project_id=p.id,
            module_id=pm.id,
            task_type="apply",
            status=TaskStatus.IN_PROGRESS,
            celery_task_id="fake-celery-id",
        )
        db.add(task)
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)
        success, msg = svc.destroy_stack(si)
        assert success is False
        assert "operation in progress" in msg

    def test_destroy_does_not_orphan_an_interrupted_apply(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """#30: a module whose apply worker died must be DESTROYED, not deleted.

        `tofu apply` creates IAM roles, VPCs and the like well before it
        finishes, so a module that reached `applying` may own cloud resources
        even with no terminal task on record. BUG-008 recovery used to reset it
        to not_initialized, which routed it into the delete-directly branch --
        the row vanished with no destroy attempt, the resources outlived
        Forge's knowledge of them, and the next deploy hit EntityAlreadyExists.
        """
        p = make_project()
        lib = make_module_library(name="iam", path="bnk/iam")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        pm = ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/iam",
            status=ModuleStatus.APPLYING, stack_instance_id=si.id,
        )
        db.add(pm)
        db.flush()
        # No Task row at all: the worker died before recording anything.
        db.refresh(si)
        svc = StackDeploymentService(db)

        with patch.object(svc, '_dispatch_first_destroy_wave_stack', return_value="mock-task-id") as mock_wave:
            success, msg = svc.destroy_stack(si)

        assert success is True
        db.refresh(pm)
        # Recovered to a destroy-ELIGIBLE state, not a delete-eligible one.
        assert pm.status == ModuleStatus.APPLY_FAILED
        assert "may exist" in (pm.deployment_error or "").lower() or "destroy will be attempted" in (pm.deployment_error or "")
        # And it was queued for a real destroy rather than deleted.
        mock_wave.assert_called_once()
        assert "1 modules" in msg
        assert db.query(ProjectModule).filter_by(id=pm.id).count() == 1

    def test_destroy_does_not_orphan_an_interrupted_destroy(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Symmetric: a module interrupted mid-destroy still owns whatever was not torn down."""
        p = make_project()
        lib = make_module_library(name="iam", path="bnk/iam")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        pm = ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/iam",
            status=ModuleStatus.DESTROYING, stack_instance_id=si.id,
        )
        db.add(pm)
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)

        with patch.object(svc, '_dispatch_first_destroy_wave_stack', return_value="mock-task-id") as mock_wave:
            success, _ = svc.destroy_stack(si)

        assert success is True
        db.refresh(pm)
        assert pm.status == ModuleStatus.DESTROY_FAILED
        mock_wave.assert_called_once()

    def test_interrupted_init_or_plan_is_still_safe_to_delete(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Nothing is applied during init/plan, so nothing can be orphaned. Keep the fast path."""
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        pm = ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.PLANNING, stack_instance_id=si.id,
        )
        db.add(pm)
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)

        with patch.object(svc, '_dispatch_first_destroy_wave_stack') as mock_wave:
            success, msg = svc.destroy_stack(si)

        assert success is True
        mock_wave.assert_not_called()
        assert db.query(ProjectModule).filter_by(id=pm.id).count() == 0

    def test_destroy_handles_dispatch_failure(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """When first-wave dispatch fails, stack is marked failed."""
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id,
        ))
        db.flush()
        db.refresh(si)
        svc = StackDeploymentService(db)

        with patch.object(svc, '_dispatch_first_destroy_wave_stack', side_effect=Exception("Celery broker down")):
            success, msg = svc.destroy_stack(si)

        assert success is False
        assert "Failed to start" in msg
        db.refresh(si)
        assert si.status == StackInstanceStatus.FAILED

    def test_destroy_deletes_already_destroyed_modules(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Already-destroyed modules are removed directly, not sent through destroy task."""
        p = make_project()
        lib_a = make_module_library(name="vpc", path="bnk/vpc")
        lib_b = make_module_library(name="rds", path="bnk/rds")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib_a.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.APPLIED, stack_instance_id=si.id,
        ))
        destroyed_pm = ProjectModule(
            project_id=p.id, module_library_id=lib_b.id,
            path_in_project=f"stack-{si.id}/bnk/rds",
            status=ModuleStatus.DESTROYED, stack_instance_id=si.id,
        )
        db.add(destroyed_pm)
        db.flush()
        destroyed_id = destroyed_pm.id
        db.refresh(si)
        svc = StackDeploymentService(db)

        with patch.object(svc, '_dispatch_first_destroy_wave_stack', return_value="mock-task-id"):
            success, msg = svc.destroy_stack(si)

        assert success is True
        # The destroyed module record should have been deleted
        assert db.query(ProjectModule).filter_by(id=destroyed_id).first() is None
        # Only 1 module needs actual destruction
        db.refresh(si)
        assert si.total_steps == 1

    def test_destroy_all_modules_no_infra_marks_destroyed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """When no modules need actual destruction, stack is immediately marked destroyed."""
        p = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)
        db.add(ProjectModule(
            project_id=p.id, module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status=ModuleStatus.NOT_INITIALIZED, stack_instance_id=si.id,
        ))
        db.flush()
        db.refresh(si)
        si_id = si.id
        svc = StackDeploymentService(db)
        success, msg = svc.destroy_stack(si)
        assert success is True
        assert "deleted" in msg.lower()
        from models import StackInstance
        # Stack instance is deleted after destroy
        assert db.query(StackInstance).filter_by(id=si_id).first() is None

    def test_destroy_no_modules_cleans_blueprint_workspace(
        self,
        db,
        make_project,
        make_stack_template,
        make_stack_instance,
    ):
        """Destroy path deletes stack-scoped blueprint workspace when stack is deleted directly."""
        p = make_project()
        t = make_stack_template()
        si = make_stack_instance(project=p, template=t, status=StackInstanceStatus.DEPLOYED)

        svc = StackDeploymentService(db)
        with patch("services.stack_deployment_service.WorkspaceManager") as mock_wm_cls:
            mock_wm = MagicMock()
            mock_wm_cls.return_value = mock_wm

            success, msg = svc.destroy_stack(si)

        assert success is True
        assert "no modules" in msg.lower()
        mock_wm.cleanup_blueprint_workspace.assert_called_once_with(p.id, si.id)


# ---------------------------------------------------------------------------
# BM-019: _pre_mark_applied_modules
# ---------------------------------------------------------------------------

class TestPreMarkAppliedModules:
    """BM-019: Skip already-deployed infrastructure modules in bare-metal stacks."""

    def test_pre_marks_matching_applied_modules(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """When project has applied infra modules, new stack inherits their APPLIED status."""
        p = make_project()
        lib_probe = make_module_library(name="probe-dpu", path="bare-metal/probe-dpu")
        lib_flash = make_module_library(name="flash-dpu", path="bare-metal/flash-dpu")
        lib_bnk = make_module_library(name="install-bnk", path="bare-metal/install-bnk")

        # First stack (DPU Infrastructure) — probe and flash already APPLIED.
        infra_template = make_stack_template(category="bare-metal", modules=[
            {"path": "bare-metal/probe-dpu", "name": "probe-dpu"},
            {"path": "bare-metal/flash-dpu", "name": "flash-dpu"},
        ])
        infra_si = make_stack_instance(project=p, template=infra_template)
        applied_probe = ProjectModule(
            project_id=p.id,
            module_library_id=lib_probe.id,
            path_in_project=f"stack-{infra_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.APPLIED,
            outputs={"dpu_ip": "10.0.0.1"},
            stack_instance_id=infra_si.id,
            deployment_order=0,
        )
        applied_flash = ProjectModule(
            project_id=p.id,
            module_library_id=lib_flash.id,
            path_in_project=f"stack-{infra_si.id}/bare-metal/flash-dpu",
            status=ModuleStatus.APPLIED,
            outputs={"bfb_version": "3.9.0"},
            stack_instance_id=infra_si.id,
            deployment_order=1,
        )
        db.add(applied_probe)
        db.add(applied_flash)
        db.flush()

        # Second stack (BNK Full PoC) — overlapping modules + new module.
        bnk_template = make_stack_template(category="bare-metal", modules=[
            {"path": "bare-metal/probe-dpu", "name": "probe-dpu"},
            {"path": "bare-metal/flash-dpu", "name": "flash-dpu"},
            {"path": "bare-metal/install-bnk", "name": "install-bnk"},
        ])
        bnk_si = make_stack_instance(project=p, template=bnk_template)

        # Create fresh NOT_INITIALIZED modules for the new stack.
        new_probe = ProjectModule(
            project_id=p.id,
            module_library_id=lib_probe.id,
            path_in_project=f"stack-{bnk_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.NOT_INITIALIZED,
            stack_instance_id=bnk_si.id,
            deployment_order=0,
        )
        new_flash = ProjectModule(
            project_id=p.id,
            module_library_id=lib_flash.id,
            path_in_project=f"stack-{bnk_si.id}/bare-metal/flash-dpu",
            status=ModuleStatus.NOT_INITIALIZED,
            stack_instance_id=bnk_si.id,
            deployment_order=1,
        )
        new_bnk = ProjectModule(
            project_id=p.id,
            module_library_id=lib_bnk.id,
            path_in_project=f"stack-{bnk_si.id}/bare-metal/install-bnk",
            status=ModuleStatus.NOT_INITIALIZED,
            stack_instance_id=bnk_si.id,
            deployment_order=2,
        )
        db.add(new_probe)
        db.add(new_flash)
        db.add(new_bnk)
        db.flush()

        module_map = {
            "bare-metal/probe-dpu": new_probe,
            "bare-metal/flash-dpu": new_flash,
            "bare-metal/install-bnk": new_bnk,
        }
        created_modules = [new_probe, new_flash, new_bnk]

        svc = StackDeploymentService(db)
        count = svc._pre_mark_applied_modules(p, bnk_si, created_modules, module_map)

        assert count == 2
        assert new_probe.status == ModuleStatus.APPLIED
        assert new_flash.status == ModuleStatus.APPLIED
        # Non-overlapping module stays NOT_INITIALIZED.
        assert new_bnk.status == ModuleStatus.NOT_INITIALIZED

    def test_no_cross_project_contamination(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Applied modules from a different project must NOT be inherited."""
        project_a = make_project()
        project_b = make_project()
        lib = make_module_library(name="probe-dpu", path="bare-metal/probe-dpu")

        # Stack in project A with an APPLIED module.
        infra_si_a = make_stack_instance(project=project_a)
        db.add(ProjectModule(
            project_id=project_a.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{infra_si_a.id}/bare-metal/probe-dpu",
            status=ModuleStatus.APPLIED,
            stack_instance_id=infra_si_a.id,
            deployment_order=0,
        ))
        db.flush()

        # New stack in project B — should NOT inherit project A's module.
        bnk_si_b = make_stack_instance(project=project_b)
        new_probe = ProjectModule(
            project_id=project_b.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{bnk_si_b.id}/bare-metal/probe-dpu",
            status=ModuleStatus.NOT_INITIALIZED,
            stack_instance_id=bnk_si_b.id,
            deployment_order=0,
        )
        db.add(new_probe)
        db.flush()

        module_map = {"bare-metal/probe-dpu": new_probe}
        svc = StackDeploymentService(db)
        count = svc._pre_mark_applied_modules(project_b, bnk_si_b, [new_probe], module_map)

        assert count == 0
        assert new_probe.status == ModuleStatus.NOT_INITIALIZED

    def test_copies_outputs_from_source(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Pre-marked modules inherit outputs from the source module for downstream wiring."""
        from datetime import UTC, datetime

        p = make_project()
        lib = make_module_library(name="probe-dpu", path="bare-metal/probe-dpu")

        source_ts = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)

        infra_si = make_stack_instance(project=p)
        source_module = ProjectModule(
            project_id=p.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{infra_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.APPLIED,
            outputs={"dpu_ip": "192.168.1.10", "bmc_ip": "192.168.1.11"},
            last_deployed_at=source_ts,
            stack_instance_id=infra_si.id,
            deployment_order=0,
        )
        db.add(source_module)
        db.flush()

        bnk_si = make_stack_instance(project=p)
        new_module = ProjectModule(
            project_id=p.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{bnk_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.NOT_INITIALIZED,
            stack_instance_id=bnk_si.id,
            deployment_order=0,
        )
        db.add(new_module)
        db.flush()

        module_map = {"bare-metal/probe-dpu": new_module}
        svc = StackDeploymentService(db)
        count = svc._pre_mark_applied_modules(p, bnk_si, [new_module], module_map)

        assert count == 1
        assert new_module.status == ModuleStatus.APPLIED
        assert new_module.outputs == {"dpu_ip": "192.168.1.10", "bmc_ip": "192.168.1.11"}
        assert new_module.last_deployed_at == source_ts

    def test_skips_already_initialized_modules(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Modules that already have a non-NOT_INITIALIZED state are not overwritten (retry safety)."""
        p = make_project()
        lib = make_module_library(name="probe-dpu", path="bare-metal/probe-dpu")

        infra_si = make_stack_instance(project=p)
        db.add(ProjectModule(
            project_id=p.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{infra_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.APPLIED,
            stack_instance_id=infra_si.id,
            deployment_order=0,
        ))
        db.flush()

        bnk_si = make_stack_instance(project=p)
        # Module already in APPLY_FAILED state from a previous attempt.
        retry_module = ProjectModule(
            project_id=p.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{bnk_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.APPLY_FAILED,
            stack_instance_id=bnk_si.id,
            deployment_order=0,
        )
        db.add(retry_module)
        db.flush()

        module_map = {"bare-metal/probe-dpu": retry_module}
        svc = StackDeploymentService(db)
        count = svc._pre_mark_applied_modules(p, bnk_si, [retry_module], module_map)

        assert count == 0
        # Status must remain unchanged — not overwritten.
        assert retry_module.status == ModuleStatus.APPLY_FAILED

    def test_returns_zero_when_no_previous_applied_modules(
        self, db, make_project, make_stack_instance, make_module_library
    ):
        """When there are no previously-applied modules, returns 0 and touches nothing."""
        p = make_project()
        lib = make_module_library(name="probe-dpu", path="bare-metal/probe-dpu")
        bnk_si = make_stack_instance(project=p)
        new_module = ProjectModule(
            project_id=p.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{bnk_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.NOT_INITIALIZED,
            stack_instance_id=bnk_si.id,
            deployment_order=0,
        )
        db.add(new_module)
        db.flush()

        module_map = {"bare-metal/probe-dpu": new_module}
        svc = StackDeploymentService(db)
        count = svc._pre_mark_applied_modules(p, bnk_si, [new_module], module_map)

        assert count == 0
        assert new_module.status == ModuleStatus.NOT_INITIALIZED

    def test_does_not_use_own_stack_as_source(
        self, db, make_project, make_stack_instance, make_module_library
    ):
        """Modules from the current stack are excluded via stack_instance_id != filter.

        Even if the current stack already has an APPLIED module for a given path,
        it must not be used as a source for another module in the same stack creation
        call (guard against self-referential pre-marking).
        """
        p = make_project()
        lib_probe = make_module_library(name="probe-dpu", path="bare-metal/probe-dpu")
        lib_flash = make_module_library(name="flash-dpu", path="bare-metal/flash-dpu")

        # The new (current) stack.
        current_si = make_stack_instance(project=p)

        # Simulate a module in the CURRENT stack that is already APPLIED (e.g., re-run scenario).
        applied_in_current = ProjectModule(
            project_id=p.id,
            module_library_id=lib_probe.id,
            path_in_project=f"stack-{current_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.APPLIED,
            stack_instance_id=current_si.id,
            deployment_order=0,
        )
        # Another module in the CURRENT stack that is NOT_INITIALIZED.
        pending_in_current = ProjectModule(
            project_id=p.id,
            module_library_id=lib_flash.id,
            path_in_project=f"stack-{current_si.id}/bare-metal/flash-dpu",
            status=ModuleStatus.NOT_INITIALIZED,
            stack_instance_id=current_si.id,
            deployment_order=1,
        )
        db.add(applied_in_current)
        db.add(pending_in_current)
        db.flush()

        # module_map only includes the pending module (not applied_in_current).
        # The key "bare-metal/probe-dpu" would match applied_in_current, but
        # the DB filter excludes same-stack rows — so previous_applied is empty.
        module_map = {"bare-metal/flash-dpu": pending_in_current}
        svc = StackDeploymentService(db)
        count = svc._pre_mark_applied_modules(
            p, current_si, [applied_in_current, pending_in_current], module_map
        )

        # No cross-stack sources available — pending stays NOT_INITIALIZED.
        assert count == 0
        assert pending_in_current.status == ModuleStatus.NOT_INITIALIZED

    def test_skips_source_with_none_outputs(
        self, db, make_project, make_stack_instance, make_module_library
    ):
        """Source modules with outputs=None are not pre-marked (guard against None output wiring)."""
        p = make_project()
        lib = make_module_library(name="probe-dpu", path="bare-metal/probe-dpu")

        # Previous stack — module APPLIED but outputs were never persisted (None).
        infra_si = make_stack_instance(project=p)
        db.add(ProjectModule(
            project_id=p.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{infra_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.APPLIED,
            outputs=None,  # Outputs missing — guard must skip this source.
            stack_instance_id=infra_si.id,
            deployment_order=0,
        ))
        db.flush()

        # New stack with a matching NOT_INITIALIZED module.
        bnk_si = make_stack_instance(project=p)
        new_module = ProjectModule(
            project_id=p.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{bnk_si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.NOT_INITIALIZED,
            stack_instance_id=bnk_si.id,
            deployment_order=0,
        )
        db.add(new_module)
        db.flush()

        module_map = {"bare-metal/probe-dpu": new_module}
        svc = StackDeploymentService(db)
        count = svc._pre_mark_applied_modules(p, bnk_si, [new_module], module_map)

        # Source has None outputs — must NOT be pre-marked.
        assert count == 0
        assert new_module.status == ModuleStatus.NOT_INITIALIZED


# ---------------------------------------------------------------------------
# BM-020: _enforce_sequential_chain
# ---------------------------------------------------------------------------

class TestEnforceSequentialChain:
    """BM-020: bare-metal modules must execute strictly one-at-a-time."""

    def _make_modules(self, db, p, lib_entries, si):
        """Helper: create ProjectModules with ascending deployment_order."""
        modules = []
        module_map = {}
        for idx, (path, lib) in enumerate(lib_entries):
            pm = ProjectModule(
                project_id=p.id,
                module_library_id=lib.id,
                path_in_project=f"stack-{si.id}/{path}",
                status=ModuleStatus.NOT_INITIALIZED,
                deployment_order=idx,
                stack_instance_id=si.id,
            )
            db.add(pm)
            modules.append(pm)
            module_map[path] = pm
        db.flush()
        return modules, module_map

    def test_sequential_chain_five_modules(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Each module depends on exactly the previous one; first has no deps."""
        p = make_project()
        paths = [
            "bare-metal/probe-dpu",
            "bare-metal/flash-dpu",
            "k8s/bnk-prerequisites",
            "k8s/network-setup",
            "k8s/cert-manager",
        ]
        libs = [make_module_library(name=path.split("/")[-1], path=path) for path in paths]
        t = make_stack_template(category="bare-metal")
        si = make_stack_instance(project=p, template=t)

        modules, module_map = self._make_modules(db, p, list(zip(paths, libs)), si)

        svc = StackDeploymentService(db)
        svc._enforce_sequential_chain(modules, module_map)

        # First module has no dependencies
        assert modules[0].dependencies == []

        # Each subsequent module depends on exactly the previous one
        for i in range(1, len(modules)):
            assert modules[i].dependencies == [modules[i - 1].id], (
                f"Module {i} ({paths[i]}) should depend on module {i-1} ({paths[i-1]}), "
                f"got {modules[i].dependencies}"
            )

    def test_sequential_chain_single_module(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Single-module stack: module has no dependencies."""
        p = make_project()
        lib = make_module_library(name="probe-dpu", path="bare-metal/probe-dpu")
        t = make_stack_template(category="bare-metal")
        si = make_stack_instance(project=p, template=t)

        pm = ProjectModule(
            project_id=p.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bare-metal/probe-dpu",
            status=ModuleStatus.NOT_INITIALIZED,
            deployment_order=0,
            stack_instance_id=si.id,
        )
        db.add(pm)
        db.flush()

        svc = StackDeploymentService(db)
        svc._enforce_sequential_chain([pm], {"bare-metal/probe-dpu": pm})

        assert pm.dependencies == []

    def test_sequential_chain_empty_list(self, db):
        """Empty module list: no-op, no errors."""
        svc = StackDeploymentService(db)
        # Should not raise
        svc._enforce_sequential_chain([], {})

    def test_sequential_chain_overrides_catalog_dag_deps(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """DAG-style deps from catalog metadata are overwritten by the sequential chain.

        Simulates bnk/flo having catalog deps on [k8s/bnk-prerequisites, k8s/bnk-cert-issuer]
        — after enforcement it should only depend on the immediately preceding module.
        """
        p = make_project()
        lib_prereqs = make_module_library(name="bnk-prerequisites", path="k8s/bnk-prerequisites")
        lib_net = make_module_library(name="network-setup", path="k8s/network-setup")
        lib_flo = make_module_library(name="flo", path="bnk/flo")
        t = make_stack_template(category="bare-metal")
        si = make_stack_instance(project=p, template=t)

        pm_prereqs = ProjectModule(
            project_id=p.id, module_library_id=lib_prereqs.id,
            path_in_project=f"stack-{si.id}/k8s/bnk-prerequisites",
            status=ModuleStatus.NOT_INITIALIZED, deployment_order=0,
            stack_instance_id=si.id,
        )
        pm_net = ProjectModule(
            project_id=p.id, module_library_id=lib_net.id,
            path_in_project=f"stack-{si.id}/k8s/network-setup",
            status=ModuleStatus.NOT_INITIALIZED, deployment_order=1,
            stack_instance_id=si.id,
        )
        pm_flo = ProjectModule(
            project_id=p.id, module_library_id=lib_flo.id,
            path_in_project=f"stack-{si.id}/bnk/flo",
            status=ModuleStatus.NOT_INITIALIZED, deployment_order=2,
            stack_instance_id=si.id,
        )
        db.add_all([pm_prereqs, pm_net, pm_flo])
        db.flush()

        # Simulate catalog DAG: flo depends on [prereqs, net] (wrong — must be overridden)
        pm_flo.dependencies = [pm_prereqs.id, pm_net.id]

        svc = StackDeploymentService(db)
        svc._enforce_sequential_chain(
            [pm_prereqs, pm_net, pm_flo],
            {"k8s/bnk-prerequisites": pm_prereqs, "k8s/network-setup": pm_net, "bnk/flo": pm_flo},
        )

        # After enforcement: flo depends ONLY on network-setup (previous in template order)
        assert pm_flo.dependencies == [pm_net.id]
        assert pm_net.dependencies == [pm_prereqs.id]
        assert pm_prereqs.dependencies == []

    def test_bare_metal_template_enforces_sequential_chain_via_create_modules(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Integration: bare-metal category triggers sequential chain enforcement end-to-end."""
        p = make_project()
        # Use empty dependencies_metadata to simulate catalog modules with no declared deps
        lib_a = make_module_library(
            name="mod-a", path="bare-metal/mod-a",
            dependencies_metadata={"required": [], "optional": []},
        )
        lib_b = make_module_library(
            name="mod-b", path="bare-metal/mod-b",
            dependencies_metadata={"required": [], "optional": []},
        )
        lib_c = make_module_library(
            name="mod-c", path="bnk/mod-c",
            # Simulate a catalog DAG dep that skips mod-b entirely
            dependencies_metadata={"required": [{"module": "bare-metal/mod-a"}], "optional": []},
        )
        t = make_stack_template(
            category="bare-metal",
            modules=[
                {"path": "bare-metal/mod-a", "name": "mod-a"},
                {"path": "bare-metal/mod-b", "name": "mod-b"},
                {"path": "bnk/mod-c", "name": "mod-c"},
            ],
        )
        si = make_stack_instance(project=p, template=t)

        svc = StackDeploymentService(db)
        success, modules, error = svc.create_project_modules_from_template(si)

        assert success is True
        assert error == ""
        assert len(modules) == 3

        # Sort by deployment_order to get canonical order
        ordered = sorted(modules, key=lambda m: m.deployment_order or 0)

        # First module: no dependencies
        assert ordered[0].dependencies == []

        # Second module: depends only on first
        assert ordered[1].dependencies == [ordered[0].id]

        # Third module: depends only on second (NOT on mod-a as catalog declared)
        assert ordered[2].dependencies == [ordered[1].id]
