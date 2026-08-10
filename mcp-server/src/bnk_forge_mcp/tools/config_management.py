"""
Configuration export/import and cross-cluster promotion tools.

Maps to: routes/config_export.py, routes/config_promotion.py
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from ..client import BNKForgeClient


def register(mcp: FastMCP, client: BNKForgeClient) -> None:
    """Register config management tools with the MCP server."""

    def _augment_platform_guidance(result: dict) -> dict:
        """Add MCP-side safety guidance from shared backend platform context."""
        if not isinstance(result, dict):
            return result

        platform_context = result.get("platform_context")
        if not isinstance(platform_context, dict):
            return result

        caveats = platform_context.get("comparison_caveats")
        if isinstance(caveats, list) and caveats:
            guidance = list(result.get("mcp_guidance", [])) if isinstance(result.get("mcp_guidance"), list) else []
            guidance.append(caveats[0])
            result["mcp_guidance"] = guidance

        if platform_context.get("mixed_platform_profiles") is True:
            safety = list(result.get("safety_notes", [])) if isinstance(result.get("safety_notes"), list) else []
            safety.append(
                "Mixed platform profiles detected; validate platform-specific constraints and prerequisites before import/promotion actions."
            )
            result["safety_notes"] = safety

        return result

    @mcp.tool()
    async def config_export(cluster_id: int, format: str = "yaml") -> str:
        """Export the BNK configuration from a cluster.

        Exports all Gateway API resources, policies, and related config
        as a portable YAML or JSON bundle.

        Args:
            cluster_id: The cluster ID
            format: Export format — "yaml" or "json" (default: "yaml")
        """
        if format == "json":
            result = await client.get(f"/api/clusters/{cluster_id}/bnk/export/json")
        else:
            result = await client.get(f"/api/clusters/{cluster_id}/bnk/export")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def config_diff(cluster_a_id: int, cluster_b_id: int) -> str:
        """Compare BNK configuration between two clusters.

        Shows what's different between the two cluster configs,
        useful for validating promotions or detecting drift.

        Args:
            cluster_a_id: First cluster ID
            cluster_b_id: Second cluster ID
        """
        result = await client.post(
            "/api/clusters/bnk/diff",
            json={"cluster_a_id": cluster_a_id, "cluster_b_id": cluster_b_id},
        )
        result = _augment_platform_guidance(result)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def config_promote(
        source_cluster_id: int,
        target_cluster_id: int,
        dry_run: bool = True,
    ) -> str:
        """Promote configuration from one cluster to another.

        Copies BNK configuration from the source cluster to the target.
        Use dry_run=True first to preview changes before applying.

        Args:
            source_cluster_id: Source cluster ID
            target_cluster_id: Target cluster ID
            dry_run: If True, only preview changes without applying (default: True)
        """
        result = await client.post(
            "/api/config/promote",
            json={
                "source_cluster_id": source_cluster_id,
                "target_cluster_id": target_cluster_id,
                "dry_run": dry_run,
            },
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def config_import(cluster_id: int, config_data: str) -> str:
        """Import configuration into a cluster.

        Applies a previously exported configuration bundle to a cluster.

        Args:
            cluster_id: The target cluster ID
            config_data: The configuration data (JSON string)
        """
        try:
            parsed_config = json.loads(config_data)
        except json.JSONDecodeError:
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "error_class": "validation_error",
                        "detail": "config_data must be valid JSON matching backend ConfigImportRequest.config",
                    },
                },
                indent=2,
            )

        if not isinstance(parsed_config, dict):
            return json.dumps(
                {
                    "ok": False,
                    "error": {
                        "error_class": "validation_error",
                        "detail": "config_data JSON must decode to an object",
                    },
                },
                indent=2,
            )

        result = await client.post(
            f"/api/clusters/{cluster_id}/bnk/import",
            json={"config": parsed_config},
        )
        return json.dumps(result, indent=2)
