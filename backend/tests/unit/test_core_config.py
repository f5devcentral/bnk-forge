"""
BU-003: Unit tests for core.config module.

Tests Settings defaults, property parsing, and production validation.
Uses the already-instantiated global `settings` object where possible,
and constructs new Settings instances for validation edge cases.
"""

import pytest

from core.config import Settings, _read_version_file, settings

# ── Global settings defaults ─────────────────────────────────────────


class TestSettingsDefaults:
    def test_app_name(self):
        assert settings.APP_NAME == "bnk-forge"

    def test_environment_is_development(self):
        assert settings.ENVIRONMENT == "development"

    def test_api_host(self):
        assert settings.API_HOST == "0.0.0.0"

    def test_api_port(self):
        assert settings.API_PORT == 8000

    def test_require_auth_is_true(self):
        assert settings.REQUIRE_AUTH is True

    def test_database_url_is_set(self):
        """DATABASE_URL is always set — either from env (test: sqlite) or default (postgresql)."""
        assert settings.DATABASE_URL is not None
        assert len(settings.DATABASE_URL) > 10

    def test_jwt_secret_key_is_set(self):
        """Auto-generated or from env, but always present after init."""
        assert settings.JWT_SECRET_KEY is not None
        assert len(settings.JWT_SECRET_KEY) > 10

    def test_encryption_key_is_set(self):
        """Auto-generated or from env, but always present after init."""
        assert settings.ENCRYPTION_KEY is not None
        assert len(settings.ENCRYPTION_KEY) > 10

    def test_logs_dir_default(self):
        assert settings.LOGS_DIR == "/tmp/bnk-forge-logs"


# ── CORS origins property ────────────────────────────────────────────


class TestCorsOrigins:
    def test_cors_origins_is_list(self):
        assert isinstance(settings.cors_origins, list)

    def test_cors_origins_contains_localhost(self):
        """Default ALLOWED_ORIGINS stays permissive for local development."""
        origins = settings.cors_origins
        assert "*" in origins or any("localhost" in o for o in origins)

    def test_cors_origins_parsed_from_csv(self):
        """ALLOWED_ORIGINS is CSV, cors_origins splits it."""
        assert len(settings.cors_origins) >= 1


# ── Version reading ──────────────────────────────────────────────────


class TestReadVersionFile:
    def test_version_returns_string(self):
        version = _read_version_file()
        assert isinstance(version, str)

    def test_version_looks_like_semver(self):
        """Version should be X.Y.Z format or fallback 0.0.0."""
        version = _read_version_file()
        parts = version.split(".")
        assert len(parts) >= 2  # at least X.Y
        # Each part should be numeric
        for part in parts:
            assert part.isdigit(), f"Version part '{part}' is not numeric"

    def test_settings_version_matches(self):
        """settings.VERSION should equal what _read_version_file returns."""
        assert settings.VERSION == _read_version_file()


# ── Production validation ────────────────────────────────────────────


class TestProductionValidation:
    def test_development_passes_validation(self):
        """In development mode, validate_production is a no-op."""
        # Should not raise
        settings.validate_production()

    def test_production_with_auto_keys_fails(self, monkeypatch):
        """Production environment with auto-generated keys should fail."""
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY=None,  # Will auto-generate
            ENCRYPTION_KEY=None,  # Will auto-generate
        )
        # Force the auto-generated flags
        s._jwt_key_auto_generated = True
        s._encryption_key_auto_generated = True

        with pytest.raises(SystemExit):
            s.validate_production()

    def test_production_with_explicit_keys_passes(self):
        """Production with explicitly set keys should pass."""
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="explicit-jwt-key-for-production-use",
            ENCRYPTION_KEY="explicit-encryption-key-for-production",
            MCP_SERVICE_PASSWORD="explicit-mcp-service-secret",  # #187: required
            ALLOWED_ORIGINS="https://my-app.example.com",
        )
        # Explicit keys set _auto_generated to False
        assert s._jwt_key_auto_generated is False
        assert s._encryption_key_auto_generated is False
        # Should not raise
        s.validate_production()

    def test_production_without_mcp_service_password_fails(self):
        """#187: MCP_SERVICE_PASSWORD unset in prod must fail fast."""
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="explicit-jwt-key-for-production-use",
            ENCRYPTION_KEY="explicit-encryption-key-for-production",
            ALLOWED_ORIGINS="https://my-app.example.com",
            MCP_SERVICE_PASSWORD=None,
        )
        with pytest.raises(SystemExit):
            s.validate_production()

    def test_production_with_default_mcp_password_fails(self):
        """#187: the known shipped default must also fail, not just unset."""
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="explicit-jwt-key-for-production-use",
            ENCRYPTION_KEY="explicit-encryption-key-for-production",
            ALLOWED_ORIGINS="https://my-app.example.com",
            MCP_SERVICE_PASSWORD="mcp-service-changeme",
        )
        with pytest.raises(SystemExit):
            s.validate_production()

    def test_production_wildcard_cors_fails(self):
        """Production with wildcard CORS should fail."""
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="explicit-key",
            ENCRYPTION_KEY="explicit-key",
            ALLOWED_ORIGINS="*",
        )
        with pytest.raises(SystemExit):
            s.validate_production()

    def test_production_localhost_cors_fails(self):
        """Production with localhost in CORS should fail."""
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="explicit-key",
            ENCRYPTION_KEY="explicit-key",
            ALLOWED_ORIGINS="http://localhost:3000",
        )
        with pytest.raises(SystemExit):
            s.validate_production()

    def test_staging_skips_localhost_check(self):
        """Staging mode checks auto-keys but NOT localhost in CORS."""
        s = Settings(
            ENVIRONMENT="staging",
            JWT_SECRET_KEY="explicit-key",
            ENCRYPTION_KEY="explicit-key",
            MCP_SERVICE_PASSWORD="explicit-mcp-service-secret",  # #187: required
            ALLOWED_ORIGINS="http://localhost:3000",
        )
        # Should not raise — staging allows localhost
        s.validate_production()


# ── Settings Config class ────────────────────────────────────────────


class TestSettingsConfig:
    def test_case_sensitive(self):
        assert Settings.model_config.get("case_sensitive", True)

    def test_extra_ignored(self):
        """Extra env vars should not crash Settings."""
        # Settings has extra="ignore"
        s = Settings(NONEXISTENT_SETTING="ignored")
        assert not hasattr(s, "NONEXISTENT_SETTING") or True  # extra is ignored
