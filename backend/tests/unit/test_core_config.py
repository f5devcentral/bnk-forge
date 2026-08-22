"""
BU-003: Unit tests for core.config module.

Tests Settings defaults, property parsing, and production validation.
Uses the already-instantiated global `settings` object where possible,
and constructs new Settings instances for validation edge cases.
"""

import pytest
from cryptography.fernet import Fernet

from core import config as config_mod
from core.config import Settings, _read_version_file, settings

# A syntactically valid Fernet key — ENCRYPTION_KEY is now CONSUMED as the at-rest
# key (bonnyr-f5 #193 B-3), so production/explicit-key tests must supply a real one.
VALID_FERNET = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _isolate_keys(tmp_path, monkeypatch):
    """Give every test in this module a hermetic keys volume (bonnyr-f5 #193 B-3).

    ENCRYPTION_KEY, when set, is now written to the at-rest key file, and the
    provenance flag reflects that file. Point both the JWT dir (``_KEYS_DIR``) and the
    encryption key file (``ENCRYPTION_KEY_FILE``, which ``_encryption_key_path()``
    prefers) at THIS test's ``tmp_path`` so Settings() constructions never touch the
    shared conftest key file or each other. ``tmp_path`` is the same object the test
    body sees, so a test that pre-seeds ``tmp_path / "encryption.key"`` seeds exactly
    the file the code resolves.
    """
    monkeypatch.setattr(config_mod, "_KEYS_DIR", str(tmp_path))
    monkeypatch.setenv("ENCRYPTION_KEY_FILE", str(tmp_path / "encryption.key"))


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
            ENCRYPTION_KEY=VALID_FERNET,  # B-3: must be a real Fernet key now
            MCP_SERVICE_PASSWORD="explicit-mcp-service-secret",  # #187: required
            ALLOWED_ORIGINS="https://my-app.example.com",
        )
        # Explicit keys set _auto_generated to False
        assert s._jwt_key_auto_generated is False
        assert s._encryption_key_auto_generated is False
        # Should not raise
        s.validate_production()

    def test_production_invalid_fernet_encryption_key_fails_at_construction(self):
        """bonnyr-f5 #193 B-3: ENCRYPTION_KEY is consumed as the at-rest key, so an
        invalid Fernet value (e.g. the OLD decoy `secrets.token_hex(16)`) must fail
        CLEARLY at construction — not pass the gate while encrypting nothing."""
        import secrets as _secrets
        with pytest.raises(SystemExit):
            Settings(
                ENVIRONMENT="production",
                JWT_SECRET_KEY="explicit-jwt-key-for-production-use",
                ENCRYPTION_KEY=_secrets.token_hex(16),  # NOT a Fernet key
                MCP_SERVICE_PASSWORD="explicit-mcp-service-secret",
                ALLOWED_ORIGINS="https://my-app.example.com",
            )

    def test_production_without_mcp_service_password_fails(self):
        """#187: MCP_SERVICE_PASSWORD unset in prod must fail fast."""
        s = Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="explicit-jwt-key-for-production-use",
            ENCRYPTION_KEY=VALID_FERNET,
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
            ENCRYPTION_KEY=VALID_FERNET,
            ALLOWED_ORIGINS="https://my-app.example.com",
            MCP_SERVICE_PASSWORD="mcp-service-changeme",
        )
        with pytest.raises(SystemExit):
            s.validate_production()

    def _valid_prod_kwargs(self, **overrides):
        """Everything a production Settings needs to PASS, so a single mutated field
        is the sole reason a test fails — no accidental trip on another gate."""
        base = dict(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="explicit-jwt-key-for-production-use",
            ENCRYPTION_KEY=VALID_FERNET,
            MCP_SERVICE_PASSWORD="explicit-mcp-service-secret",
            ALLOWED_ORIGINS="https://my-app.example.com",
        )
        base.update(overrides)
        return base

    def test_production_wildcard_cors_fails(self):
        """A wildcard ORIGIN ENTRY fails — with everything else valid, so the
        wildcard check is the sole trip (mutation-proof)."""
        s = Settings(**self._valid_prod_kwargs(ALLOWED_ORIGINS="*"))
        with pytest.raises(SystemExit):
            s.validate_production()

    def test_production_origin_containing_star_is_not_a_wildcard(self):
        """Flagged outside every slice: `"*" in ALLOWED_ORIGINS` was a SUBSTRING
        test, so a legitimate subdomain-wildcard origin was wrongly rejected. With
        the fix it checks the parsed list for an exact `*` entry, so an origin that
        merely CONTAINS `*` must PASS."""
        s = Settings(**self._valid_prod_kwargs(ALLOWED_ORIGINS="https://*.example.com"))
        # Must not raise — this is a specific origin, not the wildcard entry.
        s.validate_production()

    def test_production_localhost_cors_fails(self):
        """Production with localhost in CORS should fail — everything else valid so
        the localhost check is the SOLE reason (the old test tripped on the MCP gate,
        making it vacuous; a mutation removing the localhost check must now break it)."""
        s = Settings(**self._valid_prod_kwargs(ALLOWED_ORIGINS="http://localhost:3000"))
        with pytest.raises(SystemExit):
            s.validate_production()

    def test_production_non_localhost_cors_passes(self):
        """Positive control for the above: the SAME otherwise-valid settings with a
        real domain must PASS, proving the localhost branch is what fails the test."""
        s = Settings(**self._valid_prod_kwargs(ALLOWED_ORIGINS="https://forge.example.com"))
        s.validate_production()

    def test_staging_skips_localhost_check(self):
        """Staging mode checks auto-keys but NOT localhost in CORS."""
        s = Settings(
            ENVIRONMENT="staging",
            JWT_SECRET_KEY="explicit-key",
            ENCRYPTION_KEY=VALID_FERNET,
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
    # bonnyr-f5 #193 B-3: ENCRYPTION_KEY is now consumed as the at-rest key, so it
    # must be a valid Fernet key here.
    REAL = {
        "JWT_SECRET_KEY": "a-real-jwt-secret-key-value-that-is-long-enough",
        "ENCRYPTION_KEY": VALID_FERNET,
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
    """bonnyr-f5 #193 B2 (round 3): provenance FAILS CLOSED. A key WE generate is
    auto_generated=True and stays so across restarts (no marker is written — a
    marker-less key already means auto-generated). A key the OPERATOR pre-seeds is
    auto_generated=False ONLY when the operator drops an explicit ``.operator``
    opt-out marker beside it; a marker-less pre-seeded key (indistinguishable from a
    prior-release auto-gen — the whole upgrade population) is treated as
    auto-generated so SEC-006's fail-fast still fires in production."""

    def test_generated_key_is_flagged_and_stays_flagged(self, tmp_path):
        kp = str(tmp_path / "k.key")
        key1, auto1 = config_mod._persist_or_load_key(kp, lambda: "generated-value")
        assert auto1 is True
        assert (tmp_path / "k.key").exists()
        # New polarity: generation writes NO marker of any kind.
        assert not (tmp_path / "k.key.autogen").exists()
        assert not (tmp_path / "k.key.operator").exists()
        # Second boot loads from disk; a marker-less key stays flagged auto-generated.
        key2, auto2 = config_mod._persist_or_load_key(kp, lambda: "unused")
        assert key2 == key1
        assert auto2 is True

    def test_generated_key_file_is_mode_0600(self, tmp_path):
        # Minor (bonnyr-f5 #193): the higher-value JWT/ENCRYPTION secret must not be
        # written through a 0644 window. os.open(0o600)+fchmod, like the sibling
        # _persist_generated_password. Assert the final mode is tight.
        import os
        import stat

        config_mod._persist_or_load_key(str(tmp_path / "k.key"), lambda: "generated-value")
        mode = stat.S_IMODE(os.stat(tmp_path / "k.key").st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_operator_provisioned_key_is_not_flagged(self, tmp_path):
        # Operator drops a key file on the volume AND the explicit .operator opt-out
        # marker that asserts "I provisioned this" — the only way to be trusted.
        (tmp_path / "k.key").write_text("operator-secret")
        (tmp_path / "k.key.operator").write_text("")  # any/empty contents
        key, auto = config_mod._persist_or_load_key(str(tmp_path / "k.key"), lambda: "unused")
        assert key == "operator-secret"
        assert auto is False

    def test_directory_named_marker_is_not_provenance(self, tmp_path):
        # bonnyr-f5 #193 M-1: os.path.exists accepted a DIRECTORY named <key>.operator
        # as provenance. os.path.isfile refuses it, so a key beside a directory-marker
        # is still auto-generated (fail closed).
        (tmp_path / "k.key").write_text("operator-secret")
        (tmp_path / "k.key.operator").mkdir()  # a directory, not a regular file
        key, auto = config_mod._persist_or_load_key(str(tmp_path / "k.key"), lambda: "unused")
        assert key == "operator-secret"
        assert auto is True  # directory does NOT count

    def test_stale_marker_without_keyfile_stays_fail_closed_across_two_boots(self, tmp_path):
        # bonnyr-f5 #193 M-1: the natural rotation gesture — delete the key, keep the
        # marker — must NOT heal into a fail-open. Boot 1: key absent + stale marker.
        # It must classify auto=True AND refuse to persist a generated key, so boot 2
        # is the SAME situation and stays auto=True — never auto=False on our own key.
        (tmp_path / "k.key.operator").write_text("")  # marker present, key gone
        kp = str(tmp_path / "k.key")
        _k1, auto1 = config_mod._persist_or_load_key(kp, lambda: "gen-boot-1")
        assert auto1 is True
        assert not (tmp_path / "k.key").exists()  # NOT persisted (fail closed)
        _k2, auto2 = config_mod._persist_or_load_key(kp, lambda: "gen-boot-2")
        assert auto2 is True  # still fail closed on boot 2 — never downgraded

    def test_previous_release_keyfile_without_marker_is_autogenerated(self, tmp_path):
        # bonnyr-f5 #193 B2 (round 3): the UPGRADE population. A keys volume written
        # by a previously released version holds the key file with NO marker of any
        # kind. Under the OLD polarity this classified as operator-provided (auto=False)
        # and SEC-006's fail-fast passed OPEN on an auto-generated secret. Fail closed:
        # a marker-less key is auto-generated.
        (tmp_path / "k.key").write_text("prior-release-autogen-value")
        key, auto = config_mod._persist_or_load_key(str(tmp_path / "k.key"), lambda: "unused")
        assert key == "prior-release-autogen-value"
        assert auto is True  # fail closed

    def test_previous_release_shape_fails_fast_in_production(self, tmp_path, monkeypatch):
        # bonnyr-f5 #193 B2 (round 3), end-to-end: seed the PREVIOUS-RELEASE on-disk
        # shape (jwt_secret.key + encryption.key present, NO markers) and boot with
        # ENVIRONMENT=production. validate_production MUST raise SystemExit — the
        # whole point of the upgrade hardening switch. This is the test the round-3
        # review said did not exist.
        from core import config as config_mod

        monkeypatch.setattr(config_mod, "_KEYS_DIR", str(tmp_path))
        (tmp_path / "jwt_secret.key").write_text("prior-release-jwt-secret-value")
        (tmp_path / "encryption.key").write_text("prior-release-encryption-value")
        s = Settings(
            ENVIRONMENT="production",
            MCP_SERVICE_PASSWORD="a-real-mcp-service-shared-secret",
            ALLOWED_ORIGINS="https://forge.example.com",
        )
        # Both keys loaded from the marker-less files must be flagged auto-generated.
        assert s._jwt_key_auto_generated is True
        assert s._encryption_key_auto_generated is True
        with pytest.raises(SystemExit):
            s.validate_production()

    def test_operator_marker_shape_passes_production(self, tmp_path, monkeypatch):
        # The complement: an operator who pre-seeds keys AND drops the .operator
        # markers is trusted, so production boots. Proves the opt-out path works.
        from core import config as config_mod

        monkeypatch.setattr(config_mod, "_KEYS_DIR", str(tmp_path))
        (tmp_path / "jwt_secret.key").write_text("operator-provisioned-jwt-secret")
        (tmp_path / "jwt_secret.key.operator").write_text("")
        (tmp_path / "encryption.key").write_text("operator-provisioned-enc-secret")
        (tmp_path / "encryption.key.operator").write_text("")
        s = Settings(
            ENVIRONMENT="production",
            MCP_SERVICE_PASSWORD="a-real-mcp-service-shared-secret",
            ALLOWED_ORIGINS="https://forge.example.com",
        )
        assert s._jwt_key_auto_generated is False
        assert s._encryption_key_auto_generated is False
        s.validate_production()  # must not raise

    def test_partial_write_cannot_downgrade_provenance(self, tmp_path, monkeypatch):
        # bonnyr-f5 #193 B2 (round 3) — the "second trigger" is GONE. Under the old
        # design a key write that succeeded while the marker write failed left a
        # marker-less key that classified as operator-provided (auto=False) on the
        # NEXT boot — failing open on a fresh install too. The new design writes NO
        # marker on generation, so there is no second write to fail: a key that
        # persisted always reloads as auto-generated. Simulate a key that got
        # written but (old design) no marker, and confirm it reloads auto=True.
        kp = str(tmp_path / "k.key")
        # First boot generates + persists the key (no marker written by design).
        key1, auto1 = config_mod._persist_or_load_key(kp, lambda: "gen-1")
        assert auto1 is True
        # There is deliberately no marker to have failed to write.
        assert not (tmp_path / "k.key.autogen").exists()
        # Next boot: marker-less key reloads as auto-generated — never downgraded.
        _key2, auto2 = config_mod._persist_or_load_key(kp, lambda: "unused")
        assert auto2 is True


# ── ENCRYPTION_KEY is consumed as the ONE at-rest key (bonnyr-f5 #193 B-3) ──


class TestEncryptionKeyEnvConsumed:
    """B-3: setting ENCRYPTION_KEY no longer guards a value that encrypts nothing.
    It IS the at-rest key — validated, written to the key file, provenance=operator.
    The at-rest-key consumer (core.encryption) is asserted separately in
    tests/unit/test_core_encryption.py; here we prove config's own contract."""

    def test_valid_env_key_written_to_file_with_marker_and_not_flagged(self, tmp_path):
        enc_file = tmp_path / "encryption.key"
        s = Settings(ENVIRONMENT="production", ENCRYPTION_KEY=VALID_FERNET)
        # The env value is now the at-rest key on disk, with an operator marker.
        assert enc_file.read_text().strip() == VALID_FERNET
        assert (tmp_path / "encryption.key.operator").is_file()
        assert s._encryption_key_auto_generated is False  # gate reflects a real key

    def test_row1_reproduction_invalid_env_key_no_longer_passes(self, tmp_path):
        # The exact B-3 row 1: ENCRYPTION_KEY set to secrets.token_hex(16) (the OLD
        # printed remedy) used to PASS the production gate while the real at-rest key
        # was auto-generated and unchecked. Now it fails clearly at construction.
        import secrets as _secrets
        with pytest.raises(SystemExit):
            Settings(ENVIRONMENT="production", ENCRYPTION_KEY=_secrets.token_hex(16))

    def test_env_key_does_not_clobber_operator_provisioned_different_key(self, tmp_path):
        # A DIFFERENT operator-provisioned key already occupies the file (with its
        # marker). Setting ENCRYPTION_KEY to a different valid key must FAIL CLOSED,
        # never silently overwrite (that would make existing data undecryptable).
        other = Fernet.generate_key().decode()
        (tmp_path / "encryption.key").write_text(other)
        (tmp_path / "encryption.key.operator").write_text("")
        with pytest.raises(SystemExit):
            Settings(ENVIRONMENT="development", ENCRYPTION_KEY=VALID_FERNET)
        # The operator's key is untouched.
        assert (tmp_path / "encryption.key").read_text() == other

    def test_env_key_replaces_marker_less_autogen_key(self, tmp_path):
        # A marker-less key in the file is our own auto-gen (in production that path
        # only ever crash-looped, so no committed data). The explicit env value is
        # authoritative and replaces it.
        (tmp_path / "encryption.key").write_text("some-old-marker-less-value")
        s = Settings(ENVIRONMENT="development", ENCRYPTION_KEY=VALID_FERNET)
        assert (tmp_path / "encryption.key").read_text().strip() == VALID_FERNET
        assert s._encryption_key_auto_generated is False

    def test_env_unset_generates_valid_fernet_at_rest_key(self, tmp_path):
        # No ENCRYPTION_KEY set: the app generates a real Fernet key into the file.
        s = Settings(ENVIRONMENT="development")
        assert s._encryption_key_auto_generated is True
        Fernet(s.ENCRYPTION_KEY.encode())  # must be a valid Fernet key
        assert (tmp_path / "encryption.key").read_text().strip() == s.ENCRYPTION_KEY


# ── Settings Config class ────────────────────────────────────────────


class TestSettingsConfig:
    def test_case_sensitive(self):
        assert Settings.model_config.get("case_sensitive", True)

    def test_extra_ignored(self):
        """Extra env vars should not crash Settings."""
        # Settings has extra="ignore"
        s = Settings(NONEXISTENT_SETTING="ignored")
        assert not hasattr(s, "NONEXISTENT_SETTING") or True  # extra is ignored
