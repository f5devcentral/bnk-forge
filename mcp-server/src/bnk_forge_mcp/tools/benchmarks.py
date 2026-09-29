"""
Benchmark target tools for MCP.

Tools for discovering, listing, and registering LLM benchmark targets
across Kubernetes clusters.
Maps to: routes/benchmarks.py (/api/benchmarks/targets)
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import BNKForgeClient


def register(mcp: FastMCP, client: BNKForgeClient) -> None:
    """Register benchmark target tools with the MCP server."""

    @mcp.tool()
    async def list_benchmark_targets(
        cluster_id: int | None = None,
        name: str | None = None,
        status: str | None = None,
    ) -> str:
        """List benchmark targets registered in BNK-Forge.

        Returns target details, associated cluster ID, LLM endpoint info,
        and proxy deployments. Target names are unique per cluster: the same
        name may appear under different clusters.

        Args:
            cluster_id: Filter targets belonging to a specific cluster ID.
            name: Exact-match filter by target name.
            status: Filter by target status (e.g. 'active', 'ready', 'unhealthy').
        """
        params: dict[str, Any] = {}
        if cluster_id is not None:
            params["cluster_id"] = cluster_id
        if name is not None:
            params["name"] = name
        if status is not None:
            params["status"] = status

        result = await client.get("/api/benchmarks/targets", params=params or None)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def create_benchmark_target(
        cluster_id: int,
        name: str,
        llm_base_url: str,
        llm_model: str,
        llm_namespace: str = "default",
        llm_endpoint: str = "/v1/chat/completions",
        proxy_namespace: str = "perf-proxies",
        description: str = "",
    ) -> str:
        """Register a benchmark target for a Kubernetes cluster.

        Registers an LLM inference endpoint (e.g. vLLM or Ollama service)
        running on a cluster as a benchmark target.

        Target names are unique per cluster: (cluster_id, name) is the
        uniqueness key. Multiple clusters may register targets with identical
        names (for example, standard demo routes like 'mcp-default-mcp-financial-route').
        Registering a duplicate target name within the same cluster returns a 409 conflict.

        Args:
            cluster_id: ID of the Kubernetes cluster hosting the target.
            name: Target display name (unique per cluster).
            llm_base_url: Base URL of the LLM endpoint (e.g. 'http://vllm.default:8000').
            llm_model: Name of the model (e.g. 'meta-llama/Llama-3-8B-Instruct').
            llm_namespace: Kubernetes namespace of the LLM service (default: 'default').
            llm_endpoint: API endpoint path (default: '/v1/chat/completions').
            proxy_namespace: Namespace for benchmark proxy deployments (default: 'perf-proxies').
            description: Optional human-readable description for the target.
        """
        payload: dict[str, Any] = {
            "cluster_id": cluster_id,
            "name": name,
            "llm_base_url": llm_base_url,
            "llm_model": llm_model,
            "llm_namespace": llm_namespace,
            "llm_endpoint": llm_endpoint,
            "proxy_namespace": proxy_namespace,
        }
        if description:
            payload["description"] = description

        result = await client.post("/api/benchmarks/targets", json=payload)
        return json.dumps(result, indent=2)
