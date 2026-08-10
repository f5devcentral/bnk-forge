"""Tests for machine-readable MCP governance tool catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bnk_forge_mcp.tool_catalog import (
    ALLOWED_AUTH_EXPECTATIONS,
    ALLOWED_TOOL_STABILITY,
    GOVERNED_MODULES,
    ToolRiskClass,
    get_high_risk_tool_catalog,
)


class _FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _NoopClient:
    async def get(self, path: str, params=None):
        return {"path": path, "params": params}

    async def post(self, path: str, json=None, params=None):
        return {"path": path, "json": json, "params": params}

    async def put(self, path: str, json=None, params=None):
        return {"path": path, "json": json, "params": params}

    async def delete(self, path: str, params=None):
        return {"path": path, "params": params}


def _collect_registered_tools_by_module() -> dict[str, set[str]]:
    from bnk_forge_mcp.tools import (
        register_bnk_operations,
        register_cloud_auth,
        register_cluster_management,
        register_config_management,
        register_diagnostics_fleet,
        register_helm,
        register_iac_operations,
        register_system,
    )

    module_registrars = {
        "system": register_system,
        "cluster_management": register_cluster_management,
        "bnk_operations": register_bnk_operations,
        "diagnostics_fleet": register_diagnostics_fleet,
        "helm": register_helm,
        "config_management": register_config_management,
        "iac_operations": register_iac_operations,
        "cloud_auth": register_cloud_auth,
    }

    mcp = _FakeMCP()
    client = _NoopClient()
    by_module: dict[str, set[str]] = {}

    for module_name, registrar in module_registrars.items():
        before = set(mcp.tools.keys())
        registrar(mcp, client)
        after = set(mcp.tools.keys())
        by_module[module_name] = after - before

    return by_module


def _load_json_catalog() -> list[dict[str, object]]:
    catalog_path = Path(__file__).resolve().parents[1] / "tools" / "mcp_tool_catalog.json"
    return json.loads(catalog_path.read_text())


def test_catalog_json_matches_python_catalog() -> None:
    assert _load_json_catalog() == get_high_risk_tool_catalog()


def test_catalog_uses_allowed_risk_classes() -> None:
    allowed = {risk.value for risk in ToolRiskClass}
    catalog = _load_json_catalog()

    for entry in catalog:
        assert entry["risk_class"] in allowed


def test_catalog_uses_allowed_auth_expectations() -> None:
    allowed = set(ALLOWED_AUTH_EXPECTATIONS)
    catalog = _load_json_catalog()

    for entry in catalog:
        assert entry["auth_expectation"] in allowed


def test_catalog_uses_allowed_stability_values() -> None:
    allowed = set(ALLOWED_TOOL_STABILITY)
    catalog = _load_json_catalog()

    for entry in catalog:
        assert entry["stability"] in allowed


def test_catalog_entries_include_lifecycle_metadata() -> None:
    catalog = _load_json_catalog()

    for entry in catalog:
        assert str(entry.get("since_version", "")).strip(), (
            f"Catalog entry missing since_version: {entry['tool_name']}"
        )
        assert isinstance(entry.get("deprecated"), bool), (
            f"Catalog entry deprecated flag must be bool: {entry['tool_name']}"
        )
        replacement_tool = entry.get("replacement_tool")
        assert replacement_tool is None or isinstance(replacement_tool, str), (
            f"Catalog entry replacement_tool must be string|null: {entry['tool_name']}"
        )


def test_deprecated_entries_require_replacement_or_justification() -> None:
    catalog = _load_json_catalog()

    for entry in catalog:
        if entry.get("deprecated") is not True:
            continue
        replacement_tool = str(entry.get("replacement_tool") or "").strip()
        notes = str(entry.get("notes") or "").strip().lower()
        has_justification = "deprecated" in notes or "sunset" in notes
        assert replacement_tool or has_justification, (
            "Deprecated catalog entry must define replacement_tool or include "
            f"deprecation justification in notes: {entry['tool_name']}"
        )


def test_replacement_tool_semantics_are_coherent() -> None:
    """Replacement metadata must be actionable and internally consistent."""
    catalog = _load_json_catalog()
    tool_names = {str(entry["tool_name"]) for entry in catalog}

    for entry in catalog:
        tool_name = str(entry["tool_name"])
        deprecated = bool(entry.get("deprecated"))
        replacement_tool = str(entry.get("replacement_tool") or "").strip()

        if replacement_tool:
            assert replacement_tool in tool_names, (
                f"replacement_tool must reference a real catalog tool: {tool_name} -> {replacement_tool}"
            )
            assert replacement_tool != tool_name, (
                f"replacement_tool cannot point to itself: {tool_name}"
            )

        if not deprecated:
            assert not replacement_tool, (
                "Non-deprecated catalog entries must not define replacement_tool: "
                f"{tool_name}"
            )


def test_deprecated_entries_include_compatibility_guidance() -> None:
    """Deprecated entries should communicate compatibility/sunset expectations in notes."""
    catalog = _load_json_catalog()

    for entry in catalog:
        if entry.get("deprecated") is not True:
            continue

        notes = str(entry.get("notes") or "").strip().lower()
        replacement_tool = str(entry.get("replacement_tool") or "").strip().lower()

        assert "deprecated" in notes, (
            "Deprecated catalog notes must explicitly say deprecated: "
            f"{entry['tool_name']}"
        )

        has_window_language = any(token in notes for token in ("compatibility", "sunset", "removal"))
        has_replacement_mention = replacement_tool and replacement_tool in notes

        assert has_window_language or has_replacement_mention, (
            "Deprecated catalog notes must include compatibility/sunset guidance "
            "or explicitly mention replacement tool: "
            f"{entry['tool_name']}"
        )


def test_catalog_tools_are_registered_in_mcp_modules() -> None:
    pytest.importorskip("mcp", reason="mcp package not installed in this test environment")
    registered_by_module = _collect_registered_tools_by_module()
    registered = set().union(*registered_by_module.values())
    catalog_tools = {entry["tool_name"] for entry in _load_json_catalog()}
    missing = catalog_tools - registered
    assert not missing, f"Catalog includes unknown/unregistered tools: {missing}"


def test_governed_modules_have_full_catalog_coverage() -> None:
    """Governed modules must be fully represented in the catalog."""
    pytest.importorskip("mcp", reason="mcp package not installed in this test environment")

    registered_by_module = _collect_registered_tools_by_module()
    catalog = _load_json_catalog()

    for module_name in GOVERNED_MODULES:
        assert module_name in registered_by_module, f"Unknown governed module: {module_name}"

        catalog_tools_for_module = {
            str(entry["tool_name"]) for entry in catalog if str(entry["module"]) == module_name
        }
        registered_tools_for_module = registered_by_module[module_name]

        missing_from_catalog = registered_tools_for_module - catalog_tools_for_module
        assert not missing_from_catalog, (
            f"Governed module '{module_name}' has tools missing from catalog: "
            f"{sorted(missing_from_catalog)}"
        )


def test_governed_modules_have_non_empty_auth_expectations() -> None:
    catalog = _load_json_catalog()

    for entry in catalog:
        if str(entry["module"]) not in GOVERNED_MODULES:
            continue
        assert str(entry["auth_expectation"]).strip(), (
            "Governed-module entry has empty auth_expectation: "
            f"{entry['tool_name']} ({entry['module']})"
        )


def test_governed_auth_expectations_align_with_known_backend_dependencies() -> None:
    """Bounded auth trust check for governed modules via route decorators/signatures."""
    base = Path(__file__).resolve().parents[2]
    source_by_file = {
        "api.py": (base / "backend" / "routes" / "api.py").read_text(),
        "system.py": (base / "backend" / "routes" / "system.py").read_text(),
        "k8s/clusters.py": (base / "backend" / "routes" / "k8s" / "clusters.py").read_text(),
        "k8s/resources.py": (base / "backend" / "routes" / "k8s" / "resources.py").read_text(),
        "helm.py": (base / "backend" / "routes" / "helm.py").read_text(),
        "config_export.py": (base / "backend" / "routes" / "config_export.py").read_text(),
        "config_promotion.py": (base / "backend" / "routes" / "config_promotion.py").read_text(),
        "projects.py": (base / "backend" / "routes" / "projects.py").read_text(),
        "project_modules.py": (base / "backend" / "routes" / "project_modules.py").read_text(),
        "module_library.py": (base / "backend" / "routes" / "module_library.py").read_text(),
        "project_execution.py": (base / "backend" / "routes" / "project_execution.py").read_text(),
        "project_orchestration.py": (base / "backend" / "routes" / "project_orchestration.py").read_text(),
        "project_deployments.py": (base / "backend" / "routes" / "project_deployments.py").read_text(),
        "stacks.py": (base / "backend" / "routes" / "stacks.py").read_text(),
        "k8s/f5bnk.py": (base / "backend" / "routes" / "k8s" / "f5bnk.py").read_text(),
        "k8s/tmm_debug.py": (base / "backend" / "routes" / "k8s" / "tmm_debug.py").read_text(),
        "bnk_upgrade.py": (base / "backend" / "routes" / "bnk_upgrade.py").read_text(),
        "licensing.py": (base / "backend" / "routes" / "licensing.py").read_text(),
        "k8s/recovery.py": (base / "backend" / "routes" / "k8s" / "recovery.py").read_text(),
        "audit.py": (base / "backend" / "routes" / "audit.py").read_text(),
        "k8s/tunnels.py": (base / "backend" / "routes" / "k8s" / "tunnels.py").read_text(),
        "tasks.py": (base / "backend" / "routes" / "tasks.py").read_text(),
        "project_secrets.py": (base / "backend" / "routes" / "project_secrets.py").read_text(),
        "cloud_auth.py": (base / "backend" / "routes" / "cloud_auth.py").read_text(),
        "credential_templates.py": (base / "backend" / "routes" / "credential_templates.py").read_text(),
        "ssh_credentials.py": (base / "backend" / "routes" / "ssh_credentials.py").read_text(),
        # diagnostics_fleet backend route files
        "qkview.py": (base / "backend" / "routes" / "qkview.py").read_text(),
        "operators/fleet.py": (base / "backend" / "routes" / "operators" / "fleet.py").read_text(),
        "operators/crud.py": (base / "backend" / "routes" / "operators" / "crud.py").read_text(),
        "drift.py": (base / "backend" / "routes" / "drift.py").read_text(),
        "snapshots.py": (base / "backend" / "routes" / "snapshots.py").read_text(),
        "runbooks.py": (base / "backend" / "routes" / "runbooks.py").read_text(),
        "k8s/dpf.py": (base / "backend" / "routes" / "k8s" / "dpf.py").read_text(),
        "alert_channels.py": (base / "backend" / "routes" / "alert_channels.py").read_text(),
    }

    # Bounded subset: explicit tools whose backend auth pattern is easy to verify statically.
    expected = {
        "system_health": ("system.py", "require_admin"),
        "system_version": ("api.py", "require_viewer"),
        "system_settings": ("api.py", "require_admin"),
        "list_users": ("auth.py", "require_admin"),
        "system_queue_metrics": ("system.py", "require_admin"),
        "audit_log": ("audit.py", "get_current_user"),
        "list_clusters": ("k8s/clusters.py", "require_viewer"),
        "create_cluster": ("k8s/clusters.py", "require_project_owner"),
        "detect_eks_clusters": ("k8s/clusters.py", "require_project_owner"),
        "get_cluster": ("k8s/clusters.py", "require_viewer"),
        "update_cluster": ("k8s/clusters.py", "require_cluster_owner"),
        "delete_cluster": ("k8s/clusters.py", "require_cluster_owner"),
        "refresh_kubeconfig": ("k8s/clusters.py", "require_cluster_owner"),
        "test_cluster_connectivity": ("k8s/clusters.py", "require_cluster_owner"),
        "scan_cluster": ("k8s/clusters.py", "require_cluster_owner"),
        "list_namespaces": ("k8s/clusters.py", "require_viewer"),
        "apply_resource": ("k8s/resources.py", "require_cluster_owner"),
        "update_resource": ("k8s/resources.py", "require_cluster_owner"),
        "patch_resource": ("k8s/resources.py", "require_cluster_owner"),
        "delete_resource": ("k8s/resources.py", "require_cluster_owner"),
        "label_resource": ("k8s/resources.py", "require_cluster_owner"),
        "annotate_resource": ("k8s/resources.py", "require_cluster_owner"),
        "rollout_history": ("k8s/resources.py", "require_viewer"),
        "rollout_status": ("k8s/resources.py", "require_viewer"),
        "rollout_undo": ("k8s/resources.py", "require_cluster_owner"),
        "rollout_restart": ("k8s/resources.py", "require_cluster_owner"),
        "rollout_pause": ("k8s/resources.py", "require_cluster_owner"),
        "rollout_resume": ("k8s/resources.py", "require_cluster_owner"),
        "cordon_node": ("k8s/resources.py", "require_cluster_owner"),
        "uncordon_node": ("k8s/resources.py", "require_cluster_owner"),
        "drain_node": ("k8s/resources.py", "require_cluster_owner"),
        "open_tunnel": ("k8s/tunnels.py", "require_cluster_owner"),
        "close_tunnel": ("k8s/tunnels.py", "require_cluster_owner"),
        "list_resources": ("k8s/resources.py", "require_viewer"),
        "get_resource": ("k8s/resources.py", "require_viewer"),
        "get_pod_logs": ("k8s/resources.py", "require_viewer"),
        "describe_resource": ("k8s/resources.py", "require_viewer"),
        "get_cluster_events": ("k8s/resources.py", "require_viewer"),
        "get_node_metrics": ("k8s/resources.py", "require_viewer"),
        "get_pod_metrics": ("k8s/resources.py", "require_viewer"),
        "restart_pod": ("k8s/resources.py", "require_cluster_owner"),
        "scale_deployment": ("k8s/resources.py", "require_cluster_owner"),
        "helm_list_repos": ("helm.py", "require_viewer"),
        "helm_add_repo": ("helm.py", "require_operator"),
        "helm_search_charts": ("helm.py", "require_viewer"),
        "helm_list_releases": ("helm.py", "require_viewer"),
        "helm_get_release": ("helm.py", "require_viewer"),
        "helm_get_values": ("helm.py", "require_viewer"),
        "helm_release_history": ("helm.py", "require_viewer"),
        "helm_install": ("helm.py", "require_cluster_owner"),
        "helm_upgrade": ("helm.py", "require_cluster_owner"),
        "helm_rollback": ("helm.py", "require_cluster_owner"),
        "helm_uninstall": ("helm.py", "require_cluster_owner"),
        "config_export": ("config_export.py", "require_viewer"),
        "config_diff": ("config_export.py", "require_operator"),
        "config_import": ("config_export.py", "require_cluster_owner"),
        "config_promote": ("config_promotion.py", "require_operator"),
        "list_projects": ("projects.py", "require_viewer"),
        "create_project": ("projects.py", "require_operator"),
        "get_project": ("projects.py", "require_viewer"),
        "update_project": ("projects.py", "require_project_owner"),
        "delete_project": ("projects.py", "require_project_owner"),
        "activate_project": ("projects.py", "require_project_owner"),
        "project_dependency_graph": ("project_modules.py", "require_viewer"),
        "list_project_modules": ("project_modules.py", "require_viewer"),
        "create_project_module": ("project_modules.py", "require_project_owner"),
        "update_project_module": ("project_modules.py", "require_module_owner"),
        "change_project_module_version": ("project_modules.py", "require_module_owner"),
        "delete_project_module": ("project_modules.py", "require_module_owner"),
        "project_module_init": ("project_execution.py", "require_module_owner"),
        "project_module_deploy": ("project_execution.py", "require_module_owner"),
        "project_module_cancel": ("project_execution.py", "require_module_owner"),
        "project_module_retry": ("project_execution.py", "require_module_owner"),
        "get_module_logs": ("project_deployments.py", "require_viewer"),
        "get_module_state_info": ("project_deployments.py", "require_viewer"),
        "recover_module_state": ("project_deployments.py", "require_module_owner"),
        "list_module_catalog": ("module_library.py", "require_viewer"),
        "get_module_state_history": ("project_modules.py", "require_viewer"),
        "project_plan": ("project_execution.py", "require_module_owner"),
        "project_apply": ("project_execution.py", "require_module_owner"),
        "project_destroy": ("project_execution.py", "require_module_owner"),
        "project_deploy_all": ("project_orchestration.py", "require_project_owner"),
        "project_destroy_all": ("project_orchestration.py", "require_project_owner"),
        "deployment_history": ("project_deployments.py", "require_viewer"),
        "list_stacks": ("stacks.py", "require_viewer"),
        "get_stack": ("stacks.py", "require_viewer"),
        "deploy_stack": ("stacks.py", "require_project_owner"),
        "stack_template_preview": ("stacks.py", "require_viewer"),
        "stack_template_required_inputs": ("stacks.py", "require_viewer"),
        "stack_template_check_prerequisites": ("stacks.py", "require_viewer"),
        "create_stack_instance": ("stacks.py", "require_project_owner"),
        "get_stack_instance": ("stacks.py", "require_viewer"),
        "stack_instance_status": ("stacks.py", "require_viewer"),
        "delete_stack_instance": ("stacks.py", "require_project_owner"),
        "get_task": ("tasks.py", "require_viewer"),
        "cancel_task": ("tasks.py", "require_viewer"),
        "list_project_secrets": ("project_secrets.py", "require_viewer"),
        "create_project_secret_value": ("project_secrets.py", "require_project_owner"),
        "delete_project_secret": ("project_secrets.py", "require_project_owner"),
        "list_required_project_secrets": ("project_secrets.py", "require_viewer"),
        # diagnostics_fleet
        "qkview_create": ("qkview.py", "require_operator"),
        "qkview_list": ("qkview.py", "require_viewer"),
        "qkview_status": ("qkview.py", "require_viewer"),
        "qkview_setup_certs": ("licensing.py", "require_operator"),
        "fleet_health": ("operators/fleet.py", "require_viewer"),
        "list_operators": ("operators/crud.py", "require_viewer"),
        "drift_check": ("drift.py", "require_project_owner"),
        "drift_history": ("drift.py", "require_viewer"),
        "list_snapshots": ("snapshots.py", "require_viewer"),
        "create_snapshot": ("snapshots.py", "require_project_owner"),
        "compare_snapshots": ("snapshots.py", "require_viewer"),
        "list_runbooks": ("runbooks.py", "require_viewer"),
        "execute_runbook": ("runbooks.py", "require_viewer"),
        "cluster_connectivity": ("k8s/clusters.py", "require_viewer"),
        "cluster_connectivity_batch": ("k8s/clusters.py", "require_viewer"),
        "dpf_detect": ("k8s/dpf.py", "require_viewer"),
        "dpf_data": ("k8s/dpf.py", "require_viewer"),
        "dpf_health": ("k8s/dpf.py", "require_viewer"),
        "list_alert_channels": ("alert_channels.py", "require_viewer"),
        # cloud_auth
        "list_aws_regions": ("cloud_auth.py", "require_operator"),
        "aws_sso_initiate": ("cloud_auth.py", "require_operator"),
        "aws_sso_poll": ("cloud_auth.py", "require_operator"),
        "aws_set_project_credentials": ("cloud_auth.py", "require_operator"),
        "aws_assume_role": ("cloud_auth.py", "require_operator"),
        "get_project_aws_credentials": ("cloud_auth.py", "require_operator"),
        "delete_project_aws_credentials": ("cloud_auth.py", "require_operator"),
        "list_credential_templates": ("credential_templates.py", "require_viewer"),
        "create_credential_template": ("credential_templates.py", "require_operator"),
        "test_credential_template": ("credential_templates.py", "require_operator"),
        "list_ssh_credentials": ("ssh_credentials.py", "require_viewer"),
        "create_ssh_credential": ("ssh_credentials.py", "require_operator"),
        "test_ssh_credential": ("ssh_credentials.py", "require_operator"),
        "bnk_data": ("k8s/f5bnk.py", "require_viewer"),
        "bnk_gateway_topology": ("k8s/f5bnk.py", "require_viewer"),
        "bnk_health": ("k8s/f5bnk.py", "require_viewer"),
        "bnk_policy_associations": ("k8s/f5bnk.py", "require_viewer"),
        "a2a_discover_agents": ("k8s/f5bnk.py", "require_viewer"),
        "tmm_list_pods": ("k8s/tmm_debug.py", "require_viewer"),
        "tmm_exec_command": ("k8s/tmm_debug.py", "require_viewer"),
        "tmm_configview": ("k8s/tmm_debug.py", "require_viewer"),
        "bnk_current_version": ("bnk_upgrade.py", "require_viewer"),
        "bnk_available_versions": ("bnk_upgrade.py", "require_viewer"),
        "bnk_upgrade_plan": ("bnk_upgrade.py", "require_cluster_owner"),
        "bnk_upgrade_execute": ("bnk_upgrade.py", "require_cluster_owner"),
        "bnk_upgrade_history": ("bnk_upgrade.py", "require_viewer"),
        "bnk_license_status": ("licensing.py", "require_viewer"),
        "bnk_telemetry_report": ("licensing.py", "require_viewer"),
        "bnk_license_renew": ("licensing.py", "require_operator"),
        "bnk_recovery_cert_sync": ("k8s/recovery.py", "require_operator"),
        "bnk_platform_restart": ("k8s/recovery.py", "require_operator"),
        "bnk_recovery_status": ("k8s/recovery.py", "require_operator"),
    }
    dep_to_expectation = {
        "require_viewer": "viewer",
        "require_operator": "operator",
        "require_admin": "admin",
        "require_cluster_owner": "cluster_owner",
        "require_module_owner": "module_owner",
        "require_project_owner": "project_owner",
        "get_current_user": "authenticated",
    }

    auth_source = (base / "backend" / "routes" / "auth.py").read_text()
    source_by_file["auth.py"] = auth_source

    catalog = {str(entry["tool_name"]): entry for entry in _load_json_catalog()}
    governed_tools = {
        str(entry["tool_name"])
        for entry in _load_json_catalog()
        if str(entry["module"]) in GOVERNED_MODULES
    }

    assert governed_tools.issubset(set(expected.keys())), (
        "Auth dependency verification map missing governed tools: "
        f"{sorted(governed_tools - set(expected.keys()))}"
    )

    for tool_name in sorted(governed_tools):
        file_name, dependency_marker = expected[tool_name]
        source = source_by_file[file_name]
        assert dependency_marker in source, (
            f"Expected dependency marker '{dependency_marker}' missing from backend/{file_name} "
            f"for tool {tool_name}"
        )
        assert catalog[tool_name]["auth_expectation"] == dep_to_expectation[dependency_marker]


def test_catalog_subset_aligns_with_url_audit_ground_truth() -> None:
    pytest.importorskip(
        "tests.test_url_audit",
        reason="url audit module unavailable in this test environment",
    )
    from tests.test_url_audit import EXPECTED_TOOLS

    for entry in _load_json_catalog():
        tool_name = str(entry["tool_name"])
        method = str(entry["http_method"])
        path = str(entry["backend_path_template"])

        assert tool_name in EXPECTED_TOOLS, f"Catalog tool missing from URL audit list: {tool_name}"
        expected_method, expected_path = EXPECTED_TOOLS[tool_name]
        assert method == expected_method
        assert path == expected_path
