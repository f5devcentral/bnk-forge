"""
Diagnostics and fleet management tools.

QKView diagnostics, fleet health, drift detection, snapshots, and runbooks.
Maps to: routes/qkview.py, routes/operators/fleet.py, routes/drift.py,
         routes/snapshots.py, routes/runbooks.py, routes/k8s/dpf.py
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import BNKForgeClient


def register(mcp: FastMCP, client: BNKForgeClient) -> None:
    """Register diagnostics and fleet tools with the MCP server."""

    def _augment_fleet_platform_guidance(result: dict) -> dict:
        """Add MCP-side guidance from backend fleet platform context."""
        if not isinstance(result, dict):
            return result

        platform_context = result.get("platform_context")
        if not isinstance(platform_context, dict):
            return result

        if platform_context.get("mixed_platform_profiles") is True:
            guidance = list(result.get("mcp_guidance", [])) if isinstance(result.get("mcp_guidance"), list) else []
            caveats = platform_context.get("comparison_caveats")
            if isinstance(caveats, list) and caveats:
                guidance.append(caveats[0])
            guidance.append(
                "Fleet summaries span mixed platform profiles; treat status comparisons as platform-context-aware, not universally equivalent."
            )
            result["mcp_guidance"] = guidance

        return result

    # ------------------------------------------------------------------
    # QKView Diagnostics
    # ------------------------------------------------------------------

    @mcp.tool()
    async def qkview_create(cluster_id: int) -> str:
        """Create a QKView diagnostic tarball from the CWC.

        QKView captures detailed diagnostic information about the BNK installation.
        This is an async operation — use qkview_status to track progress.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.post("/api/qkview/create", json={"cluster_id": cluster_id})
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def qkview_list(cluster_id: int) -> str:
        """List available QKView diagnostic files for a cluster.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get("/api/qkview/list", params={"cluster_id": cluster_id})
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def qkview_status(cluster_id: int, qkview_id: int) -> str:
        """Check the status of a specific QKView operation.

        Shows if the QKView is being created, completed, or failed.

        Args:
            cluster_id: The cluster ID
            qkview_id: The QKView ID (from qkview_create)
        """
        result = await client.get(f"/api/qkview/{qkview_id}/status", params={"cluster_id": cluster_id})
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def qkview_setup_certs(cluster_id: int) -> str:
        """Setup shared CWC API mTLS certificates.

        Creates cert-manager Certificate resources for CWC API access.
        Required before QKView and licensing operations will work.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.post(f"/api/licensing/{cluster_id}/cwc-setup")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Fleet Health
    # ------------------------------------------------------------------

    @mcp.tool()
    async def fleet_health() -> str:
        """Get aggregate fleet health across all registered operators.

        Returns operator status, cluster health summaries, and connectivity status
        for the entire fleet.
        """
        result = await client.get("/api/operators/fleet-health")
        result = _augment_fleet_platform_guidance(result)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def list_operators() -> str:
        """List all registered operators and their connection status.

        Shows each operator's name, cluster, version, last heartbeat, and mode.
        """
        result = await client.get("/api/operators")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Cluster Connectivity Probes
    # ------------------------------------------------------------------

    @mcp.tool()
    async def cluster_connectivity(cluster_id: int) -> str:
        """Probe network connectivity to a Kubernetes cluster.

        Tests ICMP (ping), TCP port reachability, and K8s API accessibility.
        Returns a diagnostic status with actionable suggestions.

        Status values: healthy, reachable, port_blocked, unreachable, unknown.

        Args:
            cluster_id: The cluster ID to probe
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/connectivity")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def cluster_connectivity_batch() -> str:
        """Probe connectivity to all registered clusters in parallel.

        Returns per-cluster connectivity status plus a summary.
        Useful for quickly identifying unreachable or firewall-blocked clusters.
        """
        result = await client.get("/api/k8s/clusters/connectivity")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Drift Detection
    # ------------------------------------------------------------------

    @mcp.tool()
    async def drift_check(project_id: int) -> str:
        """Trigger a drift check for a project.

        Compares the desired state (stored in BNK-Forge) against the actual
        state on the cluster and reports any differences.

        Args:
            project_id: The project ID
        """
        result = await client.post(f"/api/projects/{project_id}/drift/check-now")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def drift_history(project_id: int, limit: int = 20) -> str:
        """Get drift detection history for a project.

        Args:
            project_id: The project ID
            limit: Maximum number of results (default 20)
        """
        result = await client.get(f"/api/projects/{project_id}/drift/checks", params={"limit": limit})
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Config Snapshots
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_snapshots(project_id: int) -> str:
        """List configuration snapshots for a project.

        Snapshots capture the complete project state for comparison and rollback.

        Args:
            project_id: The project ID
        """
        result = await client.get(f"/api/projects/{project_id}/snapshots")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def create_snapshot(project_id: int, label: str = "", cluster_id: int | None = None) -> str:
        """Create a configuration snapshot of a project's current state.

        Args:
            project_id: The project ID
            label: Optional label for the snapshot
            cluster_id: Optional cluster ID to scope the snapshot
        """
        body: dict[str, Any] = {}
        if label:
            body["label"] = label
        if cluster_id is not None:
            body["cluster_id"] = cluster_id
        result = await client.post(f"/api/projects/{project_id}/snapshots", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def compare_snapshots(snapshot_a_id: int, snapshot_b_id: int) -> str:
        """Compare two configuration snapshots and show differences.

        Args:
            snapshot_a_id: First snapshot ID
            snapshot_b_id: Second snapshot ID
        """
        result = await client.post(
            "/api/snapshots/diff",
            json={"snapshot_a_id": snapshot_a_id, "snapshot_b_id": snapshot_b_id},
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Runbooks
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_runbooks() -> str:
        """List available diagnostic runbooks.

        Runbooks are automated diagnostic procedures for common BNK issues.
        """
        result = await client.get("/api/runbooks")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def execute_runbook(cluster_id: int, runbook_id: str) -> str:
        """Execute a diagnostic runbook against a cluster.

        Runs the automated diagnostic steps and returns findings.

        Args:
            cluster_id: The cluster ID
            runbook_id: The runbook identifier
        """
        result = await client.post(
            f"/api/runbooks/{runbook_id}/run",
            json={"cluster_id": cluster_id},
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # DPF (DPU Framework)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def dpf_detect(cluster_id: int) -> str:
        """Detect NVIDIA DPF (DPU Framework) CRDs on a cluster.

        Checks for DPF installation and available DPU resources.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/dpf/detect")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def dpf_data(cluster_id: int) -> str:
        """Get DPF data including DPU devices, clusters, and services.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/dpf/data")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def dpf_health(cluster_id: int) -> str:
        """Get DPF health status for a cluster.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/dpf/health")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Alert Channels
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_alert_channels() -> str:
        """List configured alert channels (Slack, email, webhook)."""
        result = await client.get("/api/alert-channels")
        return json.dumps(result, indent=2)
