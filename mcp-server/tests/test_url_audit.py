"""
URL audit tests for MCP tool endpoints.

Ensures every MCP tool URL maps to a real backend route.
When adding new MCP tools, add the expected METHOD + URL here.
When adding new backend routes, consider adding an MCP tool.

Run: pytest tests/test_url_audit.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ground truth: every MCP tool → (METHOD, URL_PATTERN)
# Update this whenever you add/modify MCP tools.
# ---------------------------------------------------------------------------

EXPECTED_TOOLS: dict[str, tuple[str, str]] = {
    # system.py
    "system_health": ("GET", "/api/system/health"),
    "system_version": ("GET", "/api/version"),
    "system_settings": ("GET", "/api/settings"),
    "list_users": ("GET", "/api/auth/users"),
    "system_queue_metrics": ("GET", "/api/system/queue-metrics"),
    "audit_log": ("GET", "/api/audit"),
    # cluster_management.py
    "list_clusters": ("GET", "/api/k8s/clusters"),
    "create_cluster": ("POST", "/api/projects/{project_id}/k8s/clusters"),
    "detect_eks_clusters": ("POST", "/api/projects/{project_id}/k8s/clusters/detect-eks"),
    "get_cluster": ("GET", "/api/k8s/clusters/{cluster_id}"),
    "update_cluster": ("PUT", "/api/k8s/clusters/{cluster_id}"),
    "delete_cluster": ("DELETE", "/api/k8s/clusters/{cluster_id}"),
    "refresh_kubeconfig": ("POST", "/api/k8s/clusters/{cluster_id}/refresh-kubeconfig"),
    "test_cluster_connectivity": ("POST", "/api/k8s/clusters/{cluster_id}/test"),
    "scan_cluster": ("POST", "/api/k8s/clusters/{cluster_id}/scan"),
    "list_namespaces": ("GET", "/api/k8s/clusters/{cluster_id}/namespaces"),
    "apply_resource": ("POST", "/api/k8s/clusters/{cluster_id}/resources/{resource_type}"),
    "update_resource": ("PUT", "/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}"),
    "patch_resource": ("PATCH", "/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}"),
    "delete_resource": ("DELETE", "/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}"),
    "label_resource": ("POST", "/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/label"),
    "annotate_resource": ("POST", "/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/annotate"),
    "rollout_history": ("GET", "/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/history"),
    "rollout_status": ("GET", "/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/status"),
    "rollout_undo": ("POST", "/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/undo"),
    "rollout_restart": ("POST", "/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/restart"),
    "rollout_pause": ("POST", "/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/pause"),
    "rollout_resume": ("POST", "/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/resume"),
    "cordon_node": ("POST", "/api/k8s/clusters/{cluster_id}/nodes/{node_name}/cordon"),
    "uncordon_node": ("POST", "/api/k8s/clusters/{cluster_id}/nodes/{node_name}/uncordon"),
    "drain_node": ("POST", "/api/k8s/clusters/{cluster_id}/nodes/{node_name}/drain"),
    "open_tunnel": ("POST", "/api/k8s/clusters/{cluster_id}/tunnel/open"),
    "close_tunnel": ("POST", "/api/k8s/clusters/{cluster_id}/tunnel/close"),
    "list_resources": ("GET", "/api/k8s/clusters/{cluster_id}/resources/{resource_type}"),
    "get_resource": ("GET", "/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{name}/describe"),
    "get_pod_logs": ("GET", "/api/k8s/clusters/{cluster_id}/pods/{pod_name}/logs"),
    "describe_resource": ("GET", "/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{name}/describe"),
    "get_cluster_events": ("GET", "/api/k8s/clusters/{cluster_id}/events"),
    "get_node_metrics": ("GET", "/api/k8s/clusters/{cluster_id}/top/nodes"),
    "get_pod_metrics": ("GET", "/api/k8s/clusters/{cluster_id}/top/pods"),
    "restart_pod": ("POST", "/api/k8s/clusters/{cluster_id}/pods/{pod_name}/restart"),
    "scale_deployment": ("POST", "/api/k8s/clusters/{cluster_id}/deployments/{name}/scale"),
    # bnk_operations.py
    "bnk_data": ("GET", "/api/k8s/clusters/{cluster_id}/f5bnk/data"),
    "bnk_gateway_topology": ("GET", "/api/k8s/clusters/{cluster_id}/f5bnk/gateway-topology"),
    "bnk_health": ("GET", "/api/k8s/clusters/{cluster_id}/f5bnk/health"),
    "bnk_policy_associations": ("GET", "/api/k8s/clusters/{cluster_id}/f5bnk/policy-gateway-associations"),
    "a2a_discover_agents": ("GET", "/api/k8s/clusters/{cluster_id}/f5bnk/a2a/agents"),
    "tmm_list_pods": ("GET", "/api/k8s/clusters/{cluster_id}/tmm-debug/pods"),
    "tmm_exec_command": ("POST", "/api/k8s/clusters/{cluster_id}/tmm-debug/exec"),
    "tmm_configview": ("POST", "/api/k8s/clusters/{cluster_id}/tmm-debug/configview"),
    "bnk_current_version": ("GET", "/api/k8s/clusters/{cluster_id}/bnk/upgrade/current"),
    "bnk_available_versions": ("GET", "/api/k8s/clusters/{cluster_id}/bnk/upgrade/versions"),
    "bnk_upgrade_plan": ("POST", "/api/k8s/clusters/{cluster_id}/bnk/upgrade/plan"),
    "bnk_upgrade_execute": ("POST", "/api/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}/execute"),
    "bnk_upgrade_history": ("GET", "/api/k8s/clusters/{cluster_id}/bnk/upgrade/history"),
    "bnk_license_status": ("GET", "/api/licensing/{cluster_id}/status"),
    "bnk_telemetry_report": ("GET", "/api/licensing/{cluster_id}/report"),
    "bnk_license_renew": ("POST", "/api/licensing/{cluster_id}/renew"),
    "bnk_recovery_cert_sync": ("POST", "/api/k8s/clusters/{cluster_id}/recovery/cwc-certs"),
    "bnk_platform_restart": ("POST", "/api/k8s/clusters/{cluster_id}/recovery/platform-restart"),
    "bnk_recovery_status": ("GET", "/api/k8s/clusters/{cluster_id}/recovery/status"),
    # diagnostics_fleet.py
    "qkview_create": ("POST", "/api/qkview/create"),
    "qkview_list": ("GET", "/api/qkview/list"),
    "qkview_status": ("GET", "/api/qkview/{qkview_id}/status"),
    "qkview_setup_certs": ("POST", "/api/licensing/{cluster_id}/cwc-setup"),
    "fleet_health": ("GET", "/api/operators/fleet-health"),
    "list_operators": ("GET", "/api/operators"),
    "drift_check": ("POST", "/api/projects/{project_id}/drift/check-now"),
    "drift_history": ("GET", "/api/projects/{project_id}/drift/checks"),
    "list_snapshots": ("GET", "/api/projects/{project_id}/snapshots"),
    "create_snapshot": ("POST", "/api/projects/{project_id}/snapshots"),
    "compare_snapshots": ("POST", "/api/snapshots/diff"),
    "list_runbooks": ("GET", "/api/runbooks"),
    "execute_runbook": ("POST", "/api/runbooks/{runbook_id}/run"),
    "cluster_connectivity": ("GET", "/api/k8s/clusters/{cluster_id}/connectivity"),
    "cluster_connectivity_batch": ("GET", "/api/k8s/clusters/connectivity"),
    "dpf_detect": ("GET", "/api/k8s/clusters/{cluster_id}/dpf/detect"),
    "dpf_data": ("GET", "/api/k8s/clusters/{cluster_id}/dpf/data"),
    "dpf_health": ("GET", "/api/k8s/clusters/{cluster_id}/dpf/health"),
    "list_alert_channels": ("GET", "/api/alert-channels"),
    # helm.py
    "helm_list_repos": ("GET", "/api/helm/repositories"),
    "helm_add_repo": ("POST", "/api/helm/repositories"),
    "helm_search_charts": ("GET", "/api/helm/charts/search"),
    "helm_list_releases": ("GET", "/api/k8s/{cluster_id}/helm/releases"),
    "helm_get_release": ("GET", "/api/k8s/{cluster_id}/helm/releases/{release_name}"),
    "helm_get_values": ("GET", "/api/k8s/{cluster_id}/helm/releases/{release_name}/values"),
    "helm_release_history": ("GET", "/api/k8s/{cluster_id}/helm/releases/{release_name}/history"),
    "helm_install": ("POST", "/api/k8s/{cluster_id}/helm/install"),
    "helm_upgrade": ("PUT", "/api/k8s/{cluster_id}/helm/releases/{release_name}/upgrade"),
    "helm_rollback": ("POST", "/api/k8s/{cluster_id}/helm/releases/{release_name}/rollback"),
    "helm_uninstall": ("DELETE", "/api/k8s/{cluster_id}/helm/releases/{release_name}"),
    # config_management.py
    "config_export": ("GET", "/api/clusters/{cluster_id}/bnk/export"),
    "config_diff": ("POST", "/api/clusters/bnk/diff"),
    "config_promote": ("POST", "/api/config/promote"),
    "config_import": ("POST", "/api/clusters/{cluster_id}/bnk/import"),
    # iac_operations.py
    "list_projects": ("GET", "/api/projects"),
    "create_project": ("POST", "/api/projects"),
    "get_project": ("GET", "/api/projects/{project_id}"),
    "update_project": ("PUT", "/api/projects/{project_id}"),
    "delete_project": ("DELETE", "/api/projects/{project_id}"),
    "activate_project": ("POST", "/api/projects/{project_id}/activate"),
    "project_dependency_graph": ("GET", "/api/project-modules/project/{project_id}/dependency-graph"),
    "list_project_modules": ("GET", "/api/project-modules/project/{project_id}"),
    "create_project_module": ("POST", "/api/project-modules/project/{project_id}/add"),
    "update_project_module": ("PUT", "/api/project-modules/{module_id}"),
    "change_project_module_version": ("POST", "/api/project-modules/{module_id}/change-version"),
    "delete_project_module": ("DELETE", "/api/project-modules/{module_id}"),
    "project_module_init": ("POST", "/api/project-modules/{module_id}/init"),
    "project_module_deploy": ("POST", "/api/project-modules/{module_id}/deploy"),
    "project_module_cancel": ("POST", "/api/project-modules/{module_id}/cancel"),
    "project_module_retry": ("POST", "/api/project-modules/{module_id}/retry"),
    "get_module_logs": ("GET", "/api/project-modules/{module_id}/logs"),
    "get_module_state_info": ("GET", "/api/project-modules/{module_id}/state-info"),
    "recover_module_state": ("POST", "/api/project-modules/{module_id}/recover-state"),
    "list_module_catalog": ("GET", "/api/module-library"),
    "get_module_state_history": ("GET", "/api/project-modules/{module_id}/state-history"),
    "project_plan": ("POST", "/api/project-modules/{module_id}/plan"),
    "project_apply": ("POST", "/api/project-modules/{module_id}/apply"),
    "project_destroy": ("POST", "/api/project-modules/{module_id}/destroy"),
    "project_deploy_all": ("POST", "/api/projects/{project_id}/deploy-all"),
    "project_destroy_all": ("POST", "/api/projects/{project_id}/destroy-all"),
    "deployment_history": ("GET", "/api/project-modules/project/{project_id}/deployments"),
    "list_stacks": ("GET", "/api/stacks/templates"),
    "get_stack": ("GET", "/api/stacks/templates/{slug}"),
    "deploy_stack": ("POST", "/api/stacks/projects/{project_id}/stacks/{stack_id}/deploy"),
    "stack_template_preview": ("GET", "/api/stacks/templates/{slug}/preview"),
    "stack_template_required_inputs": ("GET", "/api/stacks/templates/{slug}/required-inputs"),
    "stack_template_check_prerequisites": ("GET", "/api/stacks/templates/{slug}/check-prerequisites"),
    "create_stack_instance": ("POST", "/api/stacks/projects/{project_id}/stacks"),
    "get_stack_instance": ("GET", "/api/stacks/projects/{project_id}/stacks/{stack_id}"),
    "stack_instance_status": ("GET", "/api/stacks/projects/{project_id}/stacks/{stack_id}/status"),
    "delete_stack_instance": ("DELETE", "/api/stacks/projects/{project_id}/stacks/{stack_id}"),
    "get_task": ("GET", "/api/tasks/{task_id}"),
    "cancel_task": ("POST", "/api/tasks/{task_id}/cancel"),
    "list_project_secrets": ("GET", "/api/projects/{project_id}/secrets"),
    "create_project_secret_value": ("POST", "/api/projects/{project_id}/secrets/value"),
    "delete_project_secret": ("DELETE", "/api/projects/{project_id}/secrets/{secret_id}"),
    "list_required_project_secrets": ("GET", "/api/projects/{project_id}/secrets/required"),
    # cloud_auth.py
    "list_aws_regions": ("GET", "/api/cloud-auth/aws/regions"),
    "aws_sso_initiate": ("POST", "/api/cloud-auth/aws/sso/initiate"),
    "aws_sso_poll": ("POST", "/api/cloud-auth/aws/sso/poll"),
    "aws_set_project_credentials": ("POST", "/api/cloud-auth/aws/credentials"),
    "aws_assume_role": ("POST", "/api/cloud-auth/aws/assume-role"),
    "get_project_aws_credentials": ("GET", "/api/cloud-auth/aws/credentials/{project_id}"),
    "delete_project_aws_credentials": ("DELETE", "/api/cloud-auth/aws/credentials/{project_id}"),
    "list_credential_templates": ("GET", "/api/credential-templates"),
    "create_credential_template": ("POST", "/api/credential-templates"),
    "test_credential_template": ("POST", "/api/credential-templates/{template_id}/test"),
    "list_ssh_credentials": ("GET", "/api/ssh-credentials"),
    "create_ssh_credential": ("POST", "/api/ssh-credentials"),
    "test_ssh_credential": ("POST", "/api/ssh-credentials/{credential_id}/test"),
}


def _normalize_path(path: str) -> str:
    """Replace {param_name} with {X} for comparison."""
    return re.sub(r"\{[^}]+\}", "{X}", path)


def _extract_backend_routes() -> set[tuple[str, str]]:
    """
    Parse all backend/routes/**/*.py files and return (METHOD, normalized_path) tuples.

    Handles:
    - Routes with prefix in APIRouter(prefix="...")
    - Routes with empty string "" or "/" meaning "the prefix itself"
    - Multiple routers in same file (router, public_router, ws_router, etc.)
    - Nested packages where sub-router files have no prefix but
      the parent __init__.py assembles them under a shared prefix
      (e.g. operators/__init__.py → prefix="/api/operators", sub-files have no prefix)
    """
    # Match any `foo = APIRouter(...)` declaration
    prefix_pat = re.compile(r"(\w+)\s*=\s*APIRouter\s*\(([^)]*)\)")
    prefix_val_pat = re.compile(r"prefix\s*=\s*[\"']([^\"']*)[\"']")
    # Match @foo.get("/path"), @foo.post("/path"), etc.
    route_pat = re.compile(
        r"@(\w+)\.(get|post|put|delete|patch)\s*\(\s*[\"']([^\"']*)[\"']",
        re.IGNORECASE,
    )

    backend_root = Path(__file__).resolve().parents[2] / "backend" / "routes"
    routes: set[tuple[str, str]] = set()

    # ---------------------------------------------------------------
    # Phase 1: Discover package-level prefixes from __init__.py files
    # e.g. operators/__init__.py defines router = APIRouter(prefix="/api/operators")
    # and include_router(crud_router), so crud.py routes inherit that prefix.
    # ---------------------------------------------------------------
    package_prefixes: dict[str, str] = {}  # dir path str → prefix
    for init_file in backend_root.rglob("__init__.py"):
        content = init_file.read_text()
        for m in prefix_pat.finditer(content):
            args = m.group(2)
            pv = prefix_val_pat.search(args)
            if pv and pv.group(1):
                # This package assembles sub-routers under this prefix
                package_prefixes[str(init_file.parent)] = pv.group(1)

    # ---------------------------------------------------------------
    # Phase 2: Extract routes from every .py file (skip __init__.py, __pycache__)
    # ---------------------------------------------------------------
    for py_file in backend_root.rglob("*.py"):
        if py_file.name.startswith("__"):
            continue
        content = py_file.read_text()

        # Build map of router_name -> prefix for this file
        router_prefixes: dict[str, str] = {}
        for m in prefix_pat.finditer(content):
            router_name = m.group(1).lower()
            args = m.group(2)
            pv = prefix_val_pat.search(args)
            router_prefixes[router_name] = pv.group(1) if pv else ""

        # Determine package-level prefix for sub-router files
        # (applies when the file's own router has no prefix)
        pkg_prefix = package_prefixes.get(str(py_file.parent), "")

        for m in route_pat.finditer(content):
            router_name = m.group(1).lower()
            method = m.group(2).upper()
            path = m.group(3)

            # Get prefix for this specific router variable
            file_prefix = router_prefixes.get(router_name, "")

            # If the file's router has no prefix, inherit package prefix
            prefix = file_prefix if file_prefix else pkg_prefix

            # Resolve full path
            if path in ("", "/"):
                full = prefix if prefix else "/"
            elif path.startswith("/api"):
                # Absolute path already includes /api — use as-is
                full = path
            else:
                full = prefix + path

            # Clean up
            full = full.replace("//", "/")
            if full and not full.startswith("/"):
                full = "/" + full

            if full and full != "/":  # Skip root-only routes
                routes.add((method, _normalize_path(full)))

    return routes


# Cache backend routes for the whole test session
_BACKEND_ROUTES: set[tuple[str, str]] | None = None


def get_backend_routes() -> set[tuple[str, str]]:
    global _BACKEND_ROUTES
    if _BACKEND_ROUTES is None:
        _BACKEND_ROUTES = _extract_backend_routes()
    return _BACKEND_ROUTES


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMCPToolURLs:
    """Verify every MCP tool URL matches a real backend route."""

    @pytest.fixture(autouse=True)
    def _load_routes(self) -> None:
        self.backend = get_backend_routes()

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOLS.keys()))
    def test_tool_url_exists_in_backend(self, tool_name: str) -> None:
        """Each MCP tool must map to an actual backend route."""
        method, url = EXPECTED_TOOLS[tool_name]
        norm = _normalize_path(url)
        assert (method, norm) in self.backend, (
            f"MCP tool '{tool_name}' points to {method} {url} "
            f"which does NOT exist in backend routes"
        )

    def test_no_duplicate_tool_urls(self) -> None:
        """No two MCP tools should call the exact same endpoint (except describe/get_resource)."""
        seen: dict[tuple[str, str], str] = {}
        duplicates: list[str] = []
        # Known intentional duplicates
        known_dupes = {("GET", "/api/k8s/clusters/{X}/resources/{X}/{X}/describe")}

        for tool_name, (method, url) in EXPECTED_TOOLS.items():
            key = (method, _normalize_path(url))
            if key in seen and key not in known_dupes:
                duplicates.append(f"  {tool_name} and {seen[key]} both use {method} {url}")
            seen[key] = tool_name

        assert not duplicates, f"Duplicate tool URLs found:\n" + "\n".join(duplicates)

    def test_tool_count_matches_registered(self) -> None:
        """The audit list should match the number of tools actually registered."""
        pytest.importorskip("bnk_forge_mcp", reason="bnk_forge_mcp not installed (requires Python >=3.11)")
        pytest.importorskip("mcp", reason="mcp package not installed in this test environment")
        from bnk_forge_mcp.config import MCPConfig
        from bnk_forge_mcp.server import create_server

        config = MCPConfig(
            api_base_url="http://test:8000",
            api_token="test-token",
            api_timeout=5,
            verify_ssl=False,
        )
        mcp = create_server(config)
        registered = {t.name for t in mcp._tool_manager.list_tools()}
        audit = set(EXPECTED_TOOLS.keys())

        missing_from_audit = registered - audit
        extra_in_audit = audit - registered

        assert not missing_from_audit, f"Tools registered but NOT in audit: {missing_from_audit}"
        assert not extra_in_audit, f"Tools in audit but NOT registered: {extra_in_audit}"

    def test_backend_route_extraction_works(self) -> None:
        """Sanity check: we can extract backend routes."""
        assert len(self.backend) > 100, f"Expected >100 backend routes, got {len(self.backend)}"
