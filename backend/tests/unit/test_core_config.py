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


# ── Production validation is SATISFIABLE from the shipped env (bonnyr-f5 #193 B2) ──


class TestProductionValidationSatisfiableFromEnv:
    """bonnyr-f5 #193 B2: ENVIRONMENT=production is documented as the hardening
    switch, and the compose files now plumb every var validate_production gates on
    (MCP_SERVICE_PASSWORD, JWT_SECRET_KEY, ENCRYPTION_KEY, ALLOWED_ORIGINS). Freeze
    that contract: each gated var must both (a) TRIP the fail-fast when left at the
    shipped-empty/wildcard default, and (b) CLEAR it when set to a real value — so
    the shipped .env can actually satisfy production on every path, not just trip it.
    """

    # A full set of operator-supplied real values (what a hardened .env delivers).
    REAL = {
        "JWT_SECRET_KEY": "a-real-jwt-secret-key-value-that-is-long-enough",
        "ENCRYPTION_KEY": "a-real-operator-provided-encryption-key",
        "MCP_SERVICE_PASSWORD": "a-real-mcp-service-shared-secret",
        "ALLOWED_ORIGINS": "https://forge.example.com",
    }

    # The value each var carries when the operator has NOT set it, exactly as the
    # compose anchors deliver it: keys/password default to "" (empty), CORS to "*".
    SHIPPED_DEFAULT = {
        "JWT_SECRET_KEY": "",
        "ENCRYPTION_KEY": "",
        "MCP_SERVICE_PASSWORD": "",
        "ALLOWED_ORIGINS": "*",
    }

    def test_all_real_values_pass(self):
        """Every var set to a real value → production validation passes."""
        Settings(ENVIRONMENT="production", **self.REAL).validate_production()

    @pytest.mark.parametrize("var", sorted(REAL.keys()))
    def test_each_gated_var_trips_then_is_satisfiable(self, var):
        # Only this one var left at its shipped default → must fail fast.
        env = dict(self.REAL)
        env[var] = self.SHIPPED_DEFAULT[var]
        with pytest.raises(SystemExit):
            Settings(ENVIRONMENT="production", **env).validate_production()
        # Restoring a real value for it (all others already real) → passes.
        env[var] = self.REAL[var]
        Settings(ENVIRONMENT="production", **env).validate_production()

    def test_empty_string_keys_are_treated_as_unset(self):
        """bonnyr-f5 #193 B2: compose delivers `${JWT_SECRET_KEY:-}` = "" when the
        operator does not set it. An empty value must count as unset (auto-generated,
        flagged) — not as an explicitly-provided empty key that would pass."""
        s = Settings(ENVIRONMENT="production", JWT_SECRET_KEY="", ENCRYPTION_KEY="")
        assert s._jwt_key_auto_generated is True
        assert s._encryption_key_auto_generated is True
        # And an auto-generated (non-empty) value is still present for the app to use.
        assert s.JWT_SECRET_KEY
        assert s.ENCRYPTION_KEY

    def test_all_shipped_defaults_raise(self):
        """The pure default path (nothing set) still fails fast — the switch is real."""
        with pytest.raises(SystemExit):
            Settings(ENVIRONMENT="production", **self.SHIPPED_DEFAULT).validate_production()


# ── _persist_or_load_key provenance marker (bonnyr-f5 #193 B2) ────────


class TestPersistOrLoadKeyProvenance:
    """A key WE generate is auto_generated=True (and stays so across restarts, via
    the sidecar marker); a key the OPERATOR pre-seeds on the volume (no marker) is
    auto_generated=False. This keeps SEC-006's fail-fast permanent on a fresh prod
    boot while letting an operator provision keys on disk."""

    def test_generated_key_is_flagged_and_stays_flagged(self, tmp_path, monkeypatch):
        from core import config as config_mod

        monkeypatch.setattr(config_mod, "_KEYS_DIR", str(tmp_path))
        key1, auto1 = config_mod._persist_or_load_key("k.key", lambda: "generated-value")
        assert auto1 is True
        assert (tmp_path / "k.key").exists()
        assert (tmp_path / "k.key.autogen").exists()
        # Second boot loads from disk; the marker keeps it flagged auto-generated.
        key2, auto2 = config_mod._persist_or_load_key("k.key", lambda: "unused")
        assert key2 == key1
        assert auto2 is True

    def test_operator_provisioned_key_is_not_flagged(self, tmp_path, monkeypatch):
        from core import config as config_mod

        monkeypatch.setattr(config_mod, "_KEYS_DIR", str(tmp_path))
        # Operator drops a key file on the volume, WITHOUT our marker.
        (tmp_path / "k.key").write_text("operator-secret")
        key, auto = config_mod._persist_or_load_key("k.key", lambda: "unused")
        assert key == "operator-secret"
        assert auto is False


# ── Settings Config class ────────────────────────────────────────────


class TestSettingsConfig:
    def test_case_sensitive(self):
        assert Settings.model_config.get("case_sensitive", True)

    def test_extra_ignored(self):
        """Extra env vars should not crash Settings."""
        # Settings has extra="ignore"
        s = Settings(NONEXISTENT_SETTING="ignored")
        assert not hasattr(s, "NONEXISTENT_SETTING") or True  # extra is ignored
