"""
BNK-Forge MCP Server — main entry point.

Exposes BNK-Forge's API as MCP tools accessible by any MCP-compatible
AI assistant (Claude Desktop, VS Code Copilot, OpenCode, etc.).

Architecture:
    AI Assistant ↔ MCP Protocol (Streamable HTTP) ↔ This Server ↔ HTTP ↔ FastAPI Backend

Transport: Streamable HTTP (stateless) for Docker container deployment.
Auth: Reuses BNK-Forge JWT tokens — auto-login with env credentials.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from .client import BNKForgeClient
from .config import MCPConfig, load_config
from .observability import ObservabilityMCPProxy
from .tools import (
    register_bnk_operations,
    register_cloud_auth,
    register_cluster_management,
    register_config_management,
    register_diagnostics_fleet,
    register_helm,
    register_iac_operations,
    register_system,
)

logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    """Application context — holds the HTTP client for the BNK-Forge API."""

    client: BNKForgeClient
    config: MCPConfig


def create_server(config: MCPConfig | None = None) -> FastMCP:
    """
    Create and configure the MCP server with all tools registered.

    This is the main factory function. It:
    1. Creates the FastMCP server with Streamable HTTP transport settings
    2. Sets up a lifespan manager for the HTTP client
    3. Registers all tool modules (system, clusters, BNK, diagnostics, helm, config, IaC)
    """
    if config is None:
        config = load_config()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Create the HTTP client (shared across all tools)
    api_client = BNKForgeClient(config)

    @asynccontextmanager
    async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
        """Manage the API client lifecycle.

        NOTE: With stateless_http=True, the lifespan is invoked per-request.
        The HTTP client is created once at module level and shared — we must NOT
        close it here or subsequent requests will fail.
        """
        yield AppContext(client=api_client, config=config)

    logger.info("BNK-Forge MCP server configured")
    logger.info(f"  API URL: {config.api_base_url}")
    logger.info(f"  Auth: {'token' if config.has_token else 'credentials' if config.has_credentials else 'NONE'}")
    logger.info(f"  Port: {config.port}")

    # Create FastMCP server — stateless HTTP for container deployment
    mcp = FastMCP(
        "BNK-Forge",
        instructions=(
            "BNK-Forge MCP server — AI-powered operations for F5 BIG-IP Next for Kubernetes. "
            "Use these tools to manage Kubernetes clusters, monitor F5 BNK health, "
            "debug TMM traffic management, run diagnostics, manage Helm releases, "
            "and deploy infrastructure. Start with 'system_health' to check connectivity, "
            "then 'list_clusters' to see available clusters."
        ),
        stateless_http=True,
        json_response=True,
        host=config.host,
        port=config.port,
        lifespan=lifespan,
    )

    # Register all tool modules
    register_system(ObservabilityMCPProxy(mcp, "system"), api_client)
    register_cluster_management(ObservabilityMCPProxy(mcp, "cluster_management"), api_client)
    register_bnk_operations(ObservabilityMCPProxy(mcp, "bnk_operations"), api_client)
    register_diagnostics_fleet(ObservabilityMCPProxy(mcp, "diagnostics_fleet"), api_client)
    register_helm(ObservabilityMCPProxy(mcp, "helm"), api_client)
    register_config_management(ObservabilityMCPProxy(mcp, "config_management"), api_client)
    register_iac_operations(ObservabilityMCPProxy(mcp, "iac_operations"), api_client)
    register_cloud_auth(ObservabilityMCPProxy(mcp, "cloud_auth"), api_client)

    logger.info("All tool modules registered.")

    return mcp


def main() -> None:
    """Entry point for the MCP server."""
    config = load_config()
    mcp = create_server(config)
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
