"""
Tests for MCP server creation and tool registration.

Verifies all tool modules are registered correctly with the expected tool names.
"""

from __future__ import annotations

import pytest

from bnk_forge_mcp.config import MCPConfig
from bnk_forge_mcp.observability import ObservabilityMCPProxy

pytest.importorskip("mcp", reason="mcp package not installed in this test environment")

from bnk_forge_mcp.server import create_server


@pytest.fixture
def config() -> MCPConfig:
    return MCPConfig(
        api_base_url="http://test:8000",
        api_token="test-token",
        api_timeout=5,
        verify_ssl=False,
    )


def test_server_creates_successfully(config: MCPConfig) -> None:
    """Server creates without error."""
    mcp = create_server(config)
    assert mcp is not None
    assert mcp.name == "BNK-Forge"


def test_server_has_instructions(config: MCPConfig) -> None:
    """Server has helpful instructions for AI assistants."""
    mcp = create_server(config)
    assert mcp.instructions is not None
    assert "BNK-Forge" in mcp.instructions
    assert "system_health" in mcp.instructions


def test_all_system_tools_registered(config: MCPConfig) -> None:
    """System tools are registered."""
    mcp = create_server(config)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}

    expected = {"system_health", "system_version", "system_settings", "list_users", "system_queue_metrics", "audit_log"}
    assert expected.issubset(tool_names), f"Missing system tools: {expected - tool_names}"


def test_all_cluster_tools_registered(config: MCPConfig) -> None:
    """Cluster management tools are registered."""
    mcp = create_server(config)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}

    expected = {
        "list_clusters", "get_cluster", "test_cluster_connectivity", "scan_cluster",
        "list_namespaces", "list_resources", "get_resource", "get_pod_logs",
        "describe_resource", "get_cluster_events", "get_node_metrics", "get_pod_metrics",
        "restart_pod", "scale_deployment",
    }
    assert expected.issubset(tool_names), f"Missing cluster tools: {expected - tool_names}"


def test_all_bnk_tools_registered(config: MCPConfig) -> None:
    """BNK operations tools are registered."""
    mcp = create_server(config)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}

    expected = {
        "bnk_data", "bnk_gateway_topology", "bnk_health", "bnk_policy_associations",
        "a2a_discover_agents", "tmm_list_pods", "tmm_exec_command", "tmm_configview",
        "bnk_current_version", "bnk_available_versions", "bnk_upgrade_plan",
        "bnk_upgrade_execute", "bnk_upgrade_history", "bnk_license_status",
        "bnk_telemetry_report", "bnk_recovery_cert_sync", "bnk_platform_restart",
        "bnk_recovery_status",
    }
    assert expected.issubset(tool_names), f"Missing BNK tools: {expected - tool_names}"


def test_all_diagnostics_fleet_tools_registered(config: MCPConfig) -> None:
    """Diagnostics and fleet tools are registered."""
    mcp = create_server(config)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}

    expected = {
        "qkview_create", "qkview_list", "qkview_status", "qkview_setup_certs",
        "fleet_health", "list_operators",
        "drift_check", "drift_history",
        "list_snapshots", "create_snapshot", "compare_snapshots",
        "list_runbooks", "execute_runbook",
        "dpf_detect", "dpf_data", "dpf_health",
        "list_alert_channels",
    }
    assert expected.issubset(tool_names), f"Missing diagnostics tools: {expected - tool_names}"


def test_all_helm_tools_registered(config: MCPConfig) -> None:
    """Helm tools are registered."""
    mcp = create_server(config)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}

    expected = {
        "helm_list_repos", "helm_add_repo", "helm_search_charts",
        "helm_list_releases", "helm_get_release", "helm_get_values",
        "helm_release_history", "helm_install", "helm_upgrade",
        "helm_rollback", "helm_uninstall",
    }
    assert expected.issubset(tool_names), f"Missing Helm tools: {expected - tool_names}"


def test_all_config_tools_registered(config: MCPConfig) -> None:
    """Config management tools are registered."""
    mcp = create_server(config)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}

    expected = {"config_export", "config_diff", "config_promote", "config_import"}
    assert expected.issubset(tool_names), f"Missing config tools: {expected - tool_names}"


def test_all_iac_tools_registered(config: MCPConfig) -> None:
    """IaC operations tools are registered."""
    mcp = create_server(config)
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}

    expected = {
        "list_projects", "get_project", "project_dependency_graph",
        "list_project_modules", "list_module_catalog",
        "project_plan", "project_apply", "project_destroy",
        "project_deploy_all", "project_destroy_all",
        "deployment_history",
        "list_stacks", "get_stack", "deploy_stack",
    }
    assert expected.issubset(tool_names), f"Missing IaC tools: {expected - tool_names}"


def test_total_tool_count(config: MCPConfig) -> None:
    """Verify we have the expected number of tools registered."""
    mcp = create_server(config)
    tools = mcp._tool_manager.list_tools()
    tool_count = len(tools)

    # We should have ~70-175 tools across all modules (Tier A-E expansion in 3.1)
    assert tool_count >= 65, f"Expected at least 65 tools, got {tool_count}"
    assert tool_count <= 200, f"Expected at most 200 tools, got {tool_count}"

    # Print for visibility
    print(f"\nTotal MCP tools registered: {tool_count}")
    for t in sorted(tools, key=lambda t: t.name):
        print(f"  • {t.name}")


def test_observability_proxy_preserves_tool_names() -> None:
    class _FakeMCP:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}

        def tool(self, *args, **kwargs):
            def decorator(fn):
                self.tools[fn.__name__] = fn
                return fn

            return decorator

    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, "system")

    @proxy.tool()
    async def sample_tool() -> str:
        return "ok"

    assert "sample_tool" in mcp.tools
