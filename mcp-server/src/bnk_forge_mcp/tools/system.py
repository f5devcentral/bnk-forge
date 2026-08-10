"""
System & authentication tools.

Tools for checking system health, version info, and managing the connection.
Maps to: routes/system.py, routes/api.py, routes/auth.py
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from ..client import BNKForgeClient


def register(mcp: FastMCP, client: BNKForgeClient) -> None:
    """Register system tools with the MCP server."""

    @mcp.tool()
    async def system_health() -> str:
        """Get BNK-Forge system health status.

        Returns the health of all services (database, Redis, Celery workers),
        resource counts, and system version information.
        """
        result = await client.get("/api/system/health")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def system_version() -> str:
        """Get BNK-Forge version and API information."""
        result = await client.get("/api/version")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def system_settings() -> str:
        """Get current BNK-Forge system settings.

        Returns all configurable settings including defaults, feature flags,
        and environment configuration.
        """
        result = await client.get("/api/settings")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def list_users() -> str:
        """List all BNK-Forge users.

        Returns user accounts with roles (admin, operator, viewer).
        Requires admin privileges.
        """
        result = await client.get("/api/auth/users")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def system_queue_metrics() -> str:
        """Get Celery task queue metrics.

        Shows active, reserved, and scheduled tasks across all workers.
        Useful for diagnosing task queue backlogs or worker issues.
        """
        result = await client.get("/api/system/queue-metrics")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def audit_log(limit: int = 50) -> str:
        """Get the audit trail of recent actions.

        Shows who did what, when. Useful for security reviews and debugging.

        Args:
            limit: Maximum number of audit entries to return (default 50)
        """
        page_size = max(1, min(limit, 200))
        result = await client.get("/api/audit", params={"page": 1, "page_size": page_size})
        return json.dumps(result, indent=2)
