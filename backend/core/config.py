"""
Configuration management with Pydantic validation
Ensures all required environment variables are set and validated at startup

NOTE: Configurable settings (like cloud regions) should NOT have defaults here.
They should come from the database via services.defaults_service.get_default().
This file is for infrastructure/bootstrap settings only.
"""
import logging
import os
import secrets
from collections.abc import Callable
from typing import Any, Literal

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

# Passwords ever shipped as the MCP service default. Treated as "not set" on
# both the fail-fast (validate_production) and the boot-rotation path (bonnyr-f5 #188).
MCP_KNOWN_DEFAULT_PASSWORDS = ("mcp-service-changeme", "changeme")

# BE-007: Directory for persisting auto-generated keys across restarts
_KEYS_DIR = os.environ.get("KEYS_DIR", "/app/keys")


def _read_version_file() -> str:
    """Read version from VERSION file (source of truth)."""
    version_paths = [
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "VERSION"),  # /app/VERSION
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VERSION"),  # repo root
    ]
    for path in version_paths:
        if os.path.exists(path):
            with open(path) as f:
                return f.read().strip()
    return "0.0.0"  # fallback if VERSION file not found


def _persist_or_load_key(filename: str, generate_fn: Callable[[], str]) -> tuple[str, bool]:
    """
    BE-007: Load a key from persistent storage, or generate and save a new one.
    Returns (key_value, was_auto_generated).

    bonnyr-f5 #193 B2 (round 3): ``was_auto_generated`` gates SEC-006's fail-fast,
    so it MUST fail closed — absence of evidence of provisioning is not evidence of
    provisioning. The primary operator path is an env var (``JWT_SECRET_KEY`` /
    ``ENCRYPTION_KEY``), which never reaches this function (Settings.__init__ only
    calls it when the field is None). The SECONDARY operator path is pre-seeding the
    key file on the keys volume; the app is the ONLY other writer of that file on
    every shipped path, so a marker-less key file could equally be our own auto-gen
    from a prior release — which is exactly the population that upgrades into
    ``ENVIRONMENT=production``. The provenance signal is therefore an EXPLICIT
    operator OPT-OUT marker ``<filename>.operator`` (fail closed on its absence):
      * key file present + ``<filename>.operator`` marker present -> operator
        provisioned it and said so explicitly -> auto=False
      * key file present + NO ``.operator`` marker -> auto=True (fail closed: our
        own prior-release auto-gen, or unknown provenance — refuse in production)
      * key file absent -> generate now -> auto=True; we write NO marker (a
        marker-less key already means auto=True, so there is nothing to record).

    Round-3 note: the OLD polarity used a ``<filename>.autogen`` marker written on
    generation and classified on its ABSENCE. Every keys volume from a previously
    released version holds the key with no marker, so it classified as
    operator-provided and ``validate_production`` passed on an auto-generated secret
    across the entire upgrade population (SEC-006 failed OPEN). It also had a
    "second trigger": the marker write shared a ``try`` with the key write, so a
    partial write (key persisted, marker not) downgraded provenance on a fresh
    install too. Inverting the sentinel to an opt-out marker fixes both — there is
    no marker write on generation, so a partial write can no longer downgrade
    provenance, and a marker-less key fails closed.

    An operator pre-seeding a key must therefore ALSO drop a ``<filename>.operator``
    file beside it (any/empty contents) to assert provenance; otherwise the app
    treats the pre-seeded key as auto-generated and refuses to boot in production.
    """
    key_path = os.path.join(_KEYS_DIR, filename)
    operator_marker_path = key_path + ".operator"
    try:
        if os.path.exists(key_path):
            with open(key_path) as f:
                key = f.read().strip()
            if key:
                # Fail closed: operator-provided ONLY when the explicit opt-out
                # marker sits beside the key. A marker-less key is auto-generated
                # (our own prior-release gen, or unknown provenance).
                auto = not os.path.exists(operator_marker_path)
                return key, auto
    except (OSError, PermissionError) as e:
        logger.warning(f"Could not read {key_path}: {e}")

    # Generate new key
    key = generate_fn()

    # Try to persist it. We write NO marker: a marker-less key already classifies
    # as auto-generated (fail closed), so there is nothing to record and no second
    # write that a partial failure could use to downgrade provenance. Create the
    # file 0o600 at open() time (os.open) and fchmod it so the secret is never even
    # briefly world-readable under the usual umask (mirrors _persist_generated_password).
    try:
        os.makedirs(_KEYS_DIR, exist_ok=True)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        # The mode arg only applies on CREATE; force 0o600 so a pre-existing looser
        # file (e.g. 0o644 from an older release) is tightened before we write.
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        logger.info(f"Persisted auto-generated key to {key_path}")
    except (OSError, PermissionError) as e:
        logger.warning(f"Could not persist key to {key_path}: {e} — key will be lost on restart")

    return key, True  # Auto-generated


class Settings(BaseSettings):
    """Application settings with validation"""

    # Application
    APP_NAME: str = "bnk-forge"
    VERSION: str = _read_version_file()
    ENVIRONMENT: str = "development"  # development, staging, production

    # Security - CORS
    # Default is wildcard for easier local/container dev.
    # In production, set ALLOWED_ORIGINS explicitly to trusted domains.
    ALLOWED_ORIGINS: str = "*"

    @property
    def cors_origins(self) -> list[str]:
        """Parse ALLOWED_ORIGINS into list"""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",")]

    # API Configuration
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000

    # Database
    DATABASE_URL: str = "postgresql://bnkforge:bnkforge_dev_password@postgres:5432/bnkforge"

    # Paths
    LOGS_DIR: str = "/tmp/bnk-forge-logs"

    # Authentication
    REQUIRE_AUTH: bool = True  # JWT auth enforced on all API routes
    JWT_SECRET_KEY: str | None = None
    ENCRYPTION_KEY: str | None = None

    # DEFAULT_ADMIN_PASSWORD defaults to None, never a hardcoded value: a
    # shipped default like "changeme" is a live, publicly-known admin credential
    # on every fresh deployment (#184). When unset, seed_admin_user generates a
    # random one and logs it once (the account is must_change_password anyway).
    DEFAULT_ADMIN_PASSWORD: str | None = None
    # Test/ephemeral environments (e2e) seed a KNOWN admin and skip the
    # must-change gate so the suite can reach protected routes. Defaults True;
    # never set false on a real deployment.
    DEFAULT_ADMIN_MUST_CHANGE: bool = True
    MCP_SERVICE_USERNAME: str = "mcp"
    # #187/#188: shared secret between the backend (which seeds the `mcp` service
    # account) and the MCP server (which authenticates with it). The seeded 'mcp'
    # account is role=admin and exempt from the #184 must-change gate, so this
    # value must NEVER carry a published default like "mcp-service-changeme" — that
    # would be a live, publicly-known admin credential.
    #
    # Defaults to None. It CANNOT be auto-generated: both sides must receive the
    # SAME value, so it must be set explicitly. validate_production fails fast
    # (SystemExit) under ENVIRONMENT=staging|production when it is unset or a known
    # default.
    #
    # Merged behaviour (#186 + #188 — #188's "unset -> disable" was chosen over
    # #186's "unset -> generate"): when this is UNSET (or a known published
    # default), startup_steps.seed_auth_step does NOT call ensure_service_user
    # (its _mcp_pw_usable gate is false); it calls disable_stale_service_user
    # instead, so the 'mcp' account is left DISABLED/unavailable until an operator
    # configures a real password (which re-seeds and re-activates the row). No
    # random secret is generated and nothing is surfaced.
    #
    # When it IS set to a usable value: the BACKEND receives MCP_SERVICE_PASSWORD
    # on every deploy mode (the backend-env anchors in every compose file, the ibm
    # installer, and the Helm shared-env in _helpers.tpl sourced from the release
    # Secret's mcp-password key), and ensure_service_user reconciles the 'mcp'
    # account's stored hash to it — the same per-install secret the mcp client
    # uses — so the env var can be rotated without auth drift. Any row still
    # holding a shipped published default is refused as a seed value and rotated
    # out on upgrade. The reserved-name guard in ensure_service_user and #188's
    # Helm mcp-secret work share this credential surface.
    MCP_SERVICE_PASSWORD: str | None = None

    # Benchmark agent auth flag.
    # When False (default): register/ingest/WS are open (preserves the documented curl flow).
    # When True: register + ingest require a valid bearer token; WS validates ?token= and
    # checks the agent_id claim matches the path. The built-in forge-agent always sends a
    # token so flipping this flag on is a no-op for it.
    # Secure by default (#148). The agent-facing endpoints -- POST
    # /api/benchmarks/results, /results/aiperf and /agents -- mutate
    # control-plane state; with this off they accept unauthenticated writes.
    # The built-in forge-agent gets a bootstrap token minted at startup
    # (mint_builtin_agent_token_step) so a default deployment keeps working.
    # Set to false explicitly to restore the open curl flow on a trusted network.
    BENCHMARK_AGENT_AUTH_REQUIRED: bool = True

    # External URL that remote benchmark agents use to reach Forge.
    # Must be set before SSH-provisioning a managed agent host.
    # Example: https://forge.example.com  (no trailing slash)
    # The provisioner writes this as FORGE_URL in the agent's EnvironmentFile.
    FORGE_EXTERNAL_URL: str = ""

    # Redis
    REDIS_URL: str | None = None

    # Celery
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None

    # LLM gateway observability — in-cluster Loki that carries the
    # per-request llm-gateway log stream. Queries are proxied through the
    # cluster's K8s API-server service-proxy, so Loki need not be exposed.
    LOKI_NAMESPACE: str = "llm-egress"
    LOKI_SERVICE: str = "loki"
    LOKI_PORT: int = 3100
    LOKI_SCHEME: Literal["http", "https"] = "http"

    # Discovery — two tiers because each probe that goes via jumphost
    # multiplies the session load on that single jumphost. Direct probes
    # (no jumphost) parallelise much higher.
    DISCOVERY_MAX_PARALLEL_DIRECT: int = 100
    DISCOVERY_MAX_PARALLEL_VIA_JUMPHOST: int = 10
    # Back-compat / kill-switch. When set, overrides both of the above.
    DISCOVERY_MAX_PARALLEL: int | None = None

    # SEC-006/BE-007: Track whether keys were explicitly provided vs auto-generated
    _jwt_key_auto_generated: bool = False
    _encryption_key_auto_generated: bool = False

    class Config:
        case_sensitive = True
        extra = "ignore"  # Allow extra env vars (e.g. INFRACOST_API_KEY, HOST_REPO_PATH) without crashing

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        # BE-007: Handle JWT_SECRET_KEY — persist to file so restarts reuse the same key.
        # bonnyr-f5 #193 B2: treat an EMPTY value as unset. The compose anchors plumb
        # `${JWT_SECRET_KEY:-}`, so an operator who does not set it delivers "" (not an
        # absent var) to the container. `is None` would then accept "" as an
        # explicitly-provided key (auto_generated=False), so validate_production would
        # pass on an empty secret. `not` routes "" through generation instead.
        if not self.JWT_SECRET_KEY:
            key, auto = _persist_or_load_key("jwt_secret.key", lambda: secrets.token_hex(32))
            self.JWT_SECRET_KEY = key
            self._jwt_key_auto_generated = auto
            if self.ENVIRONMENT == "development":
                logger.info("Using auto-generated JWT_SECRET_KEY (persisted to /app/keys/)")
        else:
            self._jwt_key_auto_generated = False

        # BE-007: Handle ENCRYPTION_KEY — persist to file so encrypted data survives restarts
        # NOTE: ENCRYPTION_KEY must be a valid Fernet key (44-byte base64-encoded)
        # Use Fernet.generate_key() to create valid keys
        # bonnyr-f5 #193 B2: empty (compose `${ENCRYPTION_KEY:-}`) counts as unset — see JWT above.
        if not self.ENCRYPTION_KEY:
            from cryptography.fernet import Fernet
            key, auto = _persist_or_load_key("encryption.key", lambda: Fernet.generate_key().decode())
            self.ENCRYPTION_KEY = key
            self._encryption_key_auto_generated = auto
            if self.ENVIRONMENT == "development":
                logger.info("Using auto-generated ENCRYPTION_KEY (persisted to /app/keys/)")
        else:
            self._encryption_key_auto_generated = False

    def validate_production(self) -> None:
        """
        SEC-006: Validate production-specific requirements.
        In production/staging, FAIL FAST if critical security settings are missing.
        """
        if self.ENVIRONMENT not in ("staging", "production"):
            return

        issues = []

        # SEC-006: Auto-generated keys are not acceptable in production
        if self._jwt_key_auto_generated:
            issues.append(
                "JWT_SECRET_KEY was not explicitly set — set it as an environment variable"
            )

        if self._encryption_key_auto_generated:
            issues.append(
                "ENCRYPTION_KEY was not explicitly set — set it as an environment variable"
            )

        # #187: the MCP service password is a shared secret and cannot be
        # auto-generated -- it must be set explicitly and identically on the
        # backend and the MCP server. Refuse an unset or known-default value.
        # bonnyr-f5: the actually-shipped default across dist/helm/scripts was
        # "changeme", not just "mcp-service-changeme" — reject both.
        if not self.MCP_SERVICE_PASSWORD or self.MCP_SERVICE_PASSWORD in MCP_KNOWN_DEFAULT_PASSWORDS:
            issues.append(
                "MCP_SERVICE_PASSWORD was not set to a real value — set it (the same "
                "value the MCP server gets as BNK_FORGE_PASSWORD) as an environment variable"
            )

        if "*" in self.ALLOWED_ORIGINS:
            issues.append(
                "ALLOWED_ORIGINS contains '*' (wildcard) — set specific origins"
            )

        if "localhost" in self.ALLOWED_ORIGINS and self.ENVIRONMENT == "production":
            issues.append(
                "ALLOWED_ORIGINS contains 'localhost' — use your actual domain/IP"
            )

        if issues:
            logger.error("=" * 60)
            logger.error("FATAL: Production configuration errors detected")
            logger.error("=" * 60)
            for issue in issues:
                logger.error(f"  ✗ {issue}")
            logger.error("")
            logger.error("To fix: set these as environment variables in docker-compose.yml.")
            logger.error("  JWT_SECRET_KEY=$(python3 -c \"import secrets; print(secrets.token_hex(32))\")")
            logger.error("  ENCRYPTION_KEY=$(python3 -c \"import secrets; print(secrets.token_hex(16))\")")
            logger.error("  MCP_SERVICE_PASSWORD=<strong shared secret; the SAME value the MCP server gets as BNK_FORGE_PASSWORD>")
            logger.error("See: docs/DEPLOYMENT.md")
            logger.error("=" * 60)
            raise SystemExit(1)

    def validate_all(self) -> None:
        """Run all validations"""
        logger.info(f"Configuration: env={self.ENVIRONMENT}, db={self.DATABASE_URL}, cors={self.cors_origins}")

        # SEC-006: Fail fast in production if security settings are missing
        self.validate_production()


# Create global settings instance
settings = Settings()

# Run validation on import
settings.validate_all()
