"""
Configuration for the BNK-Forge MCP server.

Reads from environment variables with sensible defaults for Docker Compose deployment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MCPConfig:
    """MCP server configuration — all sourced from environment variables."""

    # BNK-Forge API connection
    api_base_url: str = field(default_factory=lambda: os.getenv("BNK_FORGE_API_URL", "http://bnk-forge-backend:8000"))
    api_token: str = field(default_factory=lambda: os.getenv("BNK_FORGE_TOKEN", ""))
    api_username: str = field(default_factory=lambda: os.getenv("BNK_FORGE_USERNAME", "mcp"))
    # No hardcoded default — password must be supplied via BNK_FORGE_PASSWORD env var.
    # auth-probe healthcheck treats empty password as "skip auth check" (no credentials).
    api_password: str = field(default_factory=lambda: os.getenv("BNK_FORGE_PASSWORD", ""))
    api_timeout: int = field(default_factory=lambda: int(os.getenv("BNK_FORGE_API_TIMEOUT", "30")))
    verify_ssl: bool = field(default_factory=lambda: os.getenv("BNK_FORGE_VERIFY_SSL", "false").lower() == "true")

    # MCP server settings
    host: str = field(default_factory=lambda: os.getenv("MCP_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("MCP_PORT", "8081")))
    log_level: str = field(default_factory=lambda: os.getenv("MCP_LOG_LEVEL", "INFO"))

    @property
    def has_token(self) -> bool:
        return bool(self.api_token)

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_username and self.api_password)


def load_config() -> MCPConfig:
    """Load configuration from environment."""
    return MCPConfig()
