"""
Tests for MCP server configuration.
"""

from __future__ import annotations

from bnk_forge_mcp.config import MCPConfig, load_config


def test_default_config() -> None:
    """Default config uses sensible defaults."""
    config = MCPConfig()
    assert "8000" in config.api_base_url
    assert config.api_username == "mcp"
    # No hardcoded password default — password must be supplied via env var
    assert config.api_password == ""
    assert config.has_credentials is False
    assert config.port == 8081
    assert config.host == "0.0.0.0"
    assert config.log_level == "INFO"


def test_config_from_env(monkeypatch) -> None:
    """Config reads from environment variables."""
    monkeypatch.setenv("BNK_FORGE_API_URL", "http://custom:9000")
    monkeypatch.setenv("BNK_FORGE_TOKEN", "my-token")
    monkeypatch.setenv("MCP_PORT", "9999")
    monkeypatch.setenv("MCP_LOG_LEVEL", "DEBUG")

    config = MCPConfig()

    assert config.api_base_url == "http://custom:9000"
    assert config.api_token == "my-token"
    assert config.port == 9999
    assert config.log_level == "DEBUG"


def test_has_token() -> None:
    """has_token property works correctly."""
    assert MCPConfig(api_token="tok").has_token is True
    assert MCPConfig(api_token="").has_token is False


def test_has_credentials() -> None:
    """has_credentials property works correctly."""
    assert MCPConfig(api_username="admin", api_password="pass").has_credentials is True
    assert MCPConfig(api_username="", api_password="").has_credentials is False


def test_load_config() -> None:
    """load_config returns a valid MCPConfig."""
    config = load_config()
    assert isinstance(config, MCPConfig)
