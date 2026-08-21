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
    """
    key_path = os.path.join(_KEYS_DIR, filename)
    try:
        if os.path.exists(key_path):
            with open(key_path) as f:
                key = f.read().strip()
            if key:
                return key, True  # Loaded from file (still auto-generated, not user-provided)
    except (OSError, PermissionError) as e:
        logger.warning(f"Could not read {key_path}: {e}")

    # Generate new key
    key = generate_fn()

    # Try to persist it
    try:
        os.makedirs(_KEYS_DIR, exist_ok=True)
        with open(key_path, "w") as f:
            f.write(key)
        os.chmod(key_path, 0o600)  # Read/write only by owner
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

    # Seed credentials — distinct vars so admin rotation never affects MCP
    DEFAULT_ADMIN_PASSWORD: str = "changeme"
    MCP_SERVICE_USERNAME: str = "mcp"
    # #187: shared secret between the backend (which seeds the `mcp` service
    # account) and the MCP server (which authenticates with it). Defaults to
    # None -- never a shipped value like "mcp-service-changeme", which is a live
    # admin credential. It can't be auto-generated (both sides must receive the
    # same value), so it must be set explicitly; validate_production fails fast
    # in staging/prod, and when unset the backend simply doesn't seed the account
    # (MCP is unavailable until it's configured).
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

        # BE-007: Handle JWT_SECRET_KEY — persist to file so restarts reuse the same key
        if self.JWT_SECRET_KEY is None:
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
        if self.ENCRYPTION_KEY is None:
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
        if not self.MCP_SERVICE_PASSWORD or self.MCP_SERVICE_PASSWORD in ("mcp-service-changeme", "changeme"):
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
