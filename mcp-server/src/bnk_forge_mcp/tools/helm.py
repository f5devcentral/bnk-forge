"""
Helm management tools.

Full Helm lifecycle — repositories, charts, releases.
Maps to: routes/helm.py
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import BNKForgeClient


def register(mcp: FastMCP, client: BNKForgeClient) -> None:
    """Register Helm tools with the MCP server."""

    # ------------------------------------------------------------------
    # Helm Repos
    # ------------------------------------------------------------------

    @mcp.tool()
    async def helm_list_repos() -> str:
        """List configured Helm repositories (global, not per-cluster)."""
        result = await client.get("/api/helm/repositories")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def helm_add_repo(name: str, url: str) -> str:
        """Add a Helm repository (global, not per-cluster).

        Args:
            name: Repository name (e.g. "bitnami")
            url: Repository URL (e.g. "https://charts.bitnami.com/bitnami")
        """
        result = await client.post(
            "/api/helm/repositories",
            json={"name": name, "url": url},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def helm_search_charts(keyword: str = "") -> str:
        """Search for Helm charts across all configured repositories (global).

        Args:
            keyword: Search keyword (empty for all charts)
        """
        params: dict[str, Any] = {}
        if keyword:
            params["keyword"] = keyword
        result = await client.get("/api/helm/charts/search", params=params)
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Helm Releases
    # ------------------------------------------------------------------

    @mcp.tool()
    async def helm_list_releases(cluster_id: int, namespace: str = "") -> str:
        """List Helm releases installed on a cluster.

        Args:
            cluster_id: The cluster ID
            namespace: Filter by namespace (empty for all)
        """
        params: dict[str, Any] = {}
        if namespace:
            params["namespace"] = namespace
        result = await client.get(f"/api/k8s/{cluster_id}/helm/releases", params=params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def helm_get_release(cluster_id: int, release_name: str, namespace: str = "default") -> str:
        """Get detailed information about a Helm release.

        Args:
            cluster_id: The cluster ID
            release_name: Name of the Helm release
            namespace: Release namespace (default: "default")
        """
        result = await client.get(
            f"/api/k8s/{cluster_id}/helm/releases/{release_name}",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def helm_get_values(cluster_id: int, release_name: str, namespace: str = "default") -> str:
        """Get the current values of a Helm release.

        Args:
            cluster_id: The cluster ID
            release_name: Name of the Helm release
            namespace: Release namespace (default: "default")
        """
        result = await client.get(
            f"/api/k8s/{cluster_id}/helm/releases/{release_name}/values",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def helm_release_history(cluster_id: int, release_name: str, namespace: str = "default") -> str:
        """Get the revision history of a Helm release.

        Args:
            cluster_id: The cluster ID
            release_name: Name of the Helm release
            namespace: Release namespace (default: "default")
        """
        result = await client.get(
            f"/api/k8s/{cluster_id}/helm/releases/{release_name}/history",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def helm_install(
        cluster_id: int,
        release_name: str,
        chart: str,
        namespace: str = "default",
        values: dict[str, Any] | None = None,
        version: str = "",
    ) -> str:
        """Install a Helm chart as a new release.

        Args:
            cluster_id: The cluster ID
            release_name: Name for the new release
            chart: Chart reference (e.g. "bitnami/nginx" or "repo/chart")
            namespace: Target namespace (default: "default")
            values: Custom values to override chart defaults
            version: Chart version (empty for latest)
        """
        body: dict[str, Any] = {
            "release_name": release_name,
            "chart": chart,
            "namespace": namespace,
        }
        if values:
            body["values"] = values
        if version:
            body["version"] = version
        result = await client.post(f"/api/k8s/{cluster_id}/helm/install", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def helm_upgrade(
        cluster_id: int,
        release_name: str,
        chart: str,
        namespace: str = "default",
        values: dict[str, Any] | None = None,
        version: str = "",
    ) -> str:
        """Upgrade an existing Helm release.

        Args:
            cluster_id: The cluster ID
            release_name: Name of the release to upgrade
            chart: Chart reference
            namespace: Release namespace (default: "default")
            values: New values (merged with existing)
            version: Target chart version (empty for latest)
        """
        body: dict[str, Any] = {
            "chart": chart,
        }
        if values:
            body["values"] = values
        if version:
            body["version"] = version
        result = await client.put(
            f"/api/k8s/{cluster_id}/helm/releases/{release_name}/upgrade",
            json=body,
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def helm_rollback(
        cluster_id: int,
        release_name: str,
        revision: int,
        namespace: str = "default",
    ) -> str:
        """Rollback a Helm release to a previous revision.

        Args:
            cluster_id: The cluster ID
            release_name: Name of the release
            revision: Revision number to rollback to
            namespace: Release namespace (default: "default")
        """
        result = await client.post(
            f"/api/k8s/{cluster_id}/helm/releases/{release_name}/rollback",
            json={"revision": revision},
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def helm_uninstall(cluster_id: int, release_name: str, namespace: str = "default") -> str:
        """Uninstall a Helm release from a cluster.

        Args:
            cluster_id: The cluster ID
            release_name: Name of the release to uninstall
            namespace: Release namespace (default: "default")
        """
        result = await client.delete(
            f"/api/k8s/{cluster_id}/helm/releases/{release_name}",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)
