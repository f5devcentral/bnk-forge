"""
F5 BNK (BIG-IP Next for Kubernetes) operations tools.

The core domain — BNK health, gateway topology, TMM debug, upgrades, licensing.
Maps to: routes/k8s/f5bnk.py, routes/k8s/tmm_debug.py, routes/bnk_upgrade.py,
         routes/licensing.py, routes/k8s/recovery.py
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import BNKForgeClient


def register(mcp: FastMCP, client: BNKForgeClient) -> None:
    """Register F5 BNK operations tools with the MCP server."""

    # ------------------------------------------------------------------
    # BNK Data & Topology
    # ------------------------------------------------------------------

    @mcp.tool()
    async def bnk_data(cluster_id: int, namespace: str = "") -> str:
        """Get comprehensive F5 BNK data for a cluster.

        Fetches all 16 CRD types (Gateways, HTTPRoutes, GatewayClasses, Policies, etc.)
        in a single call and returns health analysis, topology graph, backend analysis,
        and policy associations.

        This is the primary tool for understanding the BNK state on a cluster.

        Args:
            cluster_id: The cluster ID
            namespace: Filter by namespace (empty for all BNK namespaces)
        """
        params: dict[str, Any] = {}
        if namespace:
            params["namespace"] = namespace
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/f5bnk/data", params=params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_gateway_topology(cluster_id: int) -> str:
        """Get the gateway topology graph for a BNK cluster.

        Returns a structured graph of GatewayClass → Gateway → HTTPRoute → Backend
        relationships, including listener configurations and route rules.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/f5bnk/gateway-topology")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_health(cluster_id: int) -> str:
        """Get F5 BNK health analysis for a cluster.

        Returns health scores, status of key components (controller, TMM, FLO),
        and any issues or warnings detected.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/f5bnk/health")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_policy_associations(cluster_id: int) -> str:
        """Get policy-to-gateway associations for a BNK cluster.

        Shows which WAF, rate-limit, access-control, and other policies
        are attached to which Gateways and HTTPRoutes.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/f5bnk/policy-gateway-associations")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # A2A Protocol Discovery
    # ------------------------------------------------------------------

    @mcp.tool()
    async def a2a_discover_agents(cluster_id: int, probe: bool = False) -> str:
        """Discover A2A (Agent-to-Agent) protocol agents behind BNK HTTPRoutes.

        Scans HTTPRoutes for backend services and optionally probes each
        for the standard /.well-known/agent-card.json endpoint.

        Args:
            cluster_id: The cluster ID
            probe: If True, actively probe each service for agent cards (slower but more accurate)
        """
        params: dict[str, Any] = {}
        if probe:
            params["probe"] = "true"
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/f5bnk/a2a/agents", params=params)
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # TMM Debug
    # ------------------------------------------------------------------

    @mcp.tool()
    async def tmm_list_pods(cluster_id: int) -> str:
        """List all TMM (Traffic Management Microkernel) pods on a BNK cluster.

        Returns pod names, namespaces, status, and available debug commands.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/tmm-debug/pods")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def tmm_exec_command(
        cluster_id: int,
        pod_name: str,
        command: str,
        namespace: str = "",
    ) -> str:
        """Execute a debug command in a TMM pod's debug sidecar.

        Supported commands include tmctl, configview, bdt_cli, and raw shell commands.
        Executes in the debug container, not the main TMM container.

        Args:
            cluster_id: The cluster ID
            pod_name: TMM pod name
            command: Command to execute (e.g. "tmctl -a virtual_server_stat", "configview list")
            namespace: Pod namespace (auto-detected if empty)
        """
        body: dict[str, Any] = {"pod_name": pod_name, "command": command}
        if namespace:
            body["namespace"] = namespace
        result = await client.post(f"/api/k8s/clusters/{cluster_id}/tmm-debug/exec", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def tmm_configview(
        cluster_id: int,
        pod_name: str,
        namespace: str = "",
    ) -> str:
        """Get the TMM configuration view (configview list) from a TMM pod.

        Shows all configured virtual servers, pools, and their UUIDs.

        Args:
            cluster_id: The cluster ID
            pod_name: TMM pod name
            namespace: Pod namespace (auto-detected if empty)
        """
        body: dict[str, Any] = {"pod_name": pod_name}
        if namespace:
            body["namespace"] = namespace
        result = await client.post(f"/api/k8s/clusters/{cluster_id}/tmm-debug/configview", json=body)
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # BNK Upgrades
    # ------------------------------------------------------------------

    @mcp.tool()
    async def bnk_current_version(cluster_id: int) -> str:
        """Get the currently installed BNK version on a cluster.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/bnk/upgrade/current")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_available_versions(cluster_id: int) -> str:
        """List available BNK upgrade versions for a cluster.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/bnk/upgrade/versions")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_upgrade_plan(cluster_id: int, target_version: str) -> str:
        """Create an upgrade plan for a BNK cluster.

        Analyzes the current state and produces a step-by-step plan with
        pre-checks, upgrade steps, and rollback instructions.

        Args:
            cluster_id: The cluster ID
            target_version: The target BNK version to upgrade to
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/bnk/upgrade/plan",
            json={"target_version": target_version},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_upgrade_execute(cluster_id: int, upgrade_id: int) -> str:
        """Execute a BNK upgrade on a cluster.

        This is an async operation — use system_health or task status to monitor progress.
        Always create and review an upgrade plan first (bnk_upgrade_plan returns the upgrade_id).

        Args:
            cluster_id: The cluster ID
            upgrade_id: The upgrade plan ID (from bnk_upgrade_plan)
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}/execute",
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_upgrade_history(cluster_id: int) -> str:
        """Get upgrade history for a BNK cluster.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/bnk/upgrade/history")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Licensing
    # ------------------------------------------------------------------

    @mcp.tool()
    async def bnk_license_status(cluster_id: int) -> str:
        """Get BNK license status for a cluster.

        Shows license type, expiration, features, and CWC connectivity status.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/licensing/{cluster_id}/status")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_telemetry_report(cluster_id: int) -> str:
        """Get the BNK telemetry report for licensing compliance.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/licensing/{cluster_id}/report")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_license_renew(cluster_id: int, jwt: str) -> str:
        """Renew an expired or expiring BNK license.

        Full renewal cycle: deletes CPCL state secrets, restarts CWC pod,
        then activates the new license with the provided JWT.

        Use this for license renewals (eval or paid). For first-time
        activation use the Activate License function in the UI.

        WARNING: This restarts the CWC pod. Licensing/telemetry will be
        briefly unavailable (~2 minutes). Traffic processing is unaffected.

        Args:
            cluster_id: The cluster ID
            jwt: Raw JWT string from MyF5 for the new/renewed license
        """
        result = await client.post(
            f"/api/licensing/{cluster_id}/renew",
            json={"jwt": jwt},
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    @mcp.tool()
    async def bnk_recovery_cert_sync(cluster_id: int) -> str:
        """Resync CWC certificates after a cluster reboot.

        Recreates cert-manager certificates for CWC communication.
        Required when CWC TLS certs have expired or been lost.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.post(f"/api/k8s/clusters/{cluster_id}/recovery/cwc-certs")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_platform_restart(cluster_id: int) -> str:
        """Restart BNK platform components (controller, FLO, TMM).

        Use as a recovery measure when BNK components are in a bad state.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/recovery/platform-restart",
            json={
                "restart_controller": True,
                "restart_flo": False,
                "restart_tmm": False,
            },
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def bnk_recovery_status(cluster_id: int) -> str:
        """Check the recovery status of a BNK cluster.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/recovery/status")
        return json.dumps(result, indent=2)
