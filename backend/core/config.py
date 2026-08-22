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


def _encryption_key_path() -> str:
    """The single at-rest Fernet key file (bonnyr-f5 #193 B-3).

    Resolved IDENTICALLY to ``core.encryption.ENCRYPTION_KEY_FILE`` so the value
    whose provenance this module gates on is the SAME file ``core.encryption`` (and
    ``services.backup_service``) actually loads — never a shadow. Honours an explicit
    ``ENCRYPTION_KEY_FILE`` override, else ``$KEYS_DIR/encryption.key``.
    """
    override = os.environ.get("ENCRYPTION_KEY_FILE")
    if override:
        return override
    return os.path.join(_KEYS_DIR, "encryption.key")


def _is_valid_fernet_key(value: str) -> bool:
    """True if *value* is a syntactically valid Fernet key (32 url-safe b64 bytes)."""
    try:
        from cryptography.fernet import Fernet

        Fernet(value.encode())
        return True
    except Exception:
        return False


def _write_key_file_0600(key_path: str, key_value: str) -> bool:
    """Persist *key_value* to *key_path* at mode 0600. Returns True on success.

    Creates the file 0o600 at open() time (os.open) and fchmod's it so the secret is
    never even briefly world-readable under the usual umask, and so a pre-existing
    looser file from an older release is tightened before we write (mirrors
    _persist_generated_password).
    """
    try:
        parent = os.path.dirname(key_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key_value)
        return True
    except (OSError, PermissionError) as e:
        logger.warning(f"Could not persist key to {key_path}: {e} — key will be lost on restart")
        return False


def _persist_or_load_key(key_path: str, generate_fn: Callable[[], str]) -> tuple[str, bool]:
    """
    BE-007: Load a key from persistent storage, or generate and save a new one.
    Returns (key_value, was_auto_generated). *key_path* is the FULL path to the key
    file (bonnyr-f5 #193 B-3: encryption and jwt now name distinct absolute paths so
    the encryption key can live at ``_encryption_key_path()`` — the same file
    ``core.encryption`` loads — rather than a shadow under ``_KEYS_DIR``).

    bonnyr-f5 #193 B2 (round 3): ``was_auto_generated`` gates SEC-006's fail-fast,
    so it MUST fail closed — absence of evidence of provisioning is not evidence of
    provisioning. The primary operator path is an env var (``JWT_SECRET_KEY`` /
    ``ENCRYPTION_KEY``); when set, Settings.__init__ handles it directly (and for
    ENCRYPTION_KEY writes it to ``key_path`` with a marker — see __init__). The
    SECONDARY operator path is pre-seeding the key file on the keys volume; the app
    is the ONLY other writer of that file on every shipped path, so a marker-less key
    file could equally be our own auto-gen from a prior release — exactly the
    population that upgrades into ``ENVIRONMENT=production``. The provenance signal is
    therefore an EXPLICIT operator OPT-OUT marker ``<key_path>.operator``:
      * key file present + ``.operator`` marker (a regular FILE) -> operator
        provisioned it and said so explicitly -> auto=False
      * key file present + NO ``.operator`` marker -> auto=True (fail closed)
      * key file ABSENT + ``.operator`` marker present -> provisioning ERROR
        (bonnyr-f5 #193 M-1): do NOT adopt the marker as provenance and do NOT
        persist a generated key, else boot-2 would load key+marker and classify
        auto=False — the app booting on its OWN generated key. Fail closed every
        boot instead (auto=True, unpersisted) until the operator fixes the volume.
      * key file absent, no marker -> generate + persist -> auto=True; write NO
        marker (a marker-less key already means auto=True; nothing to record, and no
        second write a partial failure could use to downgrade provenance).

    bonnyr-f5 #193 M-1: the marker must be a regular FILE — ``os.path.exists`` used
    to accept a DIRECTORY named ``<key>.operator`` as provenance. ``os.path.isfile``
    refuses that.
    """
    operator_marker_path = key_path + ".operator"
    # M-1: a regular FILE only — a directory named <key>.operator is NOT provenance.
    marker_present = os.path.isfile(operator_marker_path)
    try:
        if os.path.isfile(key_path):
            with open(key_path) as f:
                key = f.read().strip()
            if key:
                # Fail closed: operator-provided ONLY when the explicit opt-out
                # marker (a regular file) sits beside the key.
                return key, (not marker_present)
    except (OSError, PermissionError) as e:
        logger.warning(f"Could not read {key_path}: {e}")

    # Key file absent (or empty/unreadable).
    # M-1: a marker with NO key file is a provisioning ERROR, not provenance. The
    # natural key-rotation gesture (delete the key, keep the marker) would otherwise
    # heal into a fail-open across two boots: boot-1 generates+persists a key beside
    # the stale marker; boot-2 loads key+marker -> auto=False -> production BOOTS on
    # our OWN generated key. Refuse to persist so the situation can never downgrade to
    # operator-provenance; keep classifying auto-generated (fail closed) every boot.
    if marker_present:
        logger.error(
            f"Provisioning error: operator marker {operator_marker_path} is present "
            f"but the key file {key_path} is missing. Treating the key as "
            f"auto-generated (fail closed) and NOT persisting it. Restore the "
            f"operator-provisioned key file beside the marker, or remove the stale marker."
        )
        return generate_fn(), True

    # Generate new key and persist it (no marker: a marker-less key already
    # classifies auto-generated, fail closed).
    key = generate_fn()
    if _write_key_file_0600(key_path, key):
        logger.info(f"Persisted auto-generated key to {key_path}")
    return key, True  # Auto-generated


def _adopt_operator_encryption_key(key_path: str, key_value: str) -> None:
    """bonnyr-f5 #193 B-3: consume an operator-provided ``ENCRYPTION_KEY`` as the
    ONE at-rest key.

    ``ENCRYPTION_KEY`` used to gate ``validate_production`` while encrypting nothing —
    the real at-rest Fernet key was a second, independent file that
    ``core.encryption`` generated unchecked. Now, when the operator sets a (validated)
    ``ENCRYPTION_KEY``, we WRITE it to ``key_path`` and drop the ``.operator`` marker,
    so ``core.encryption`` and ``services.backup_service`` load THIS value and a later
    env-unset boot still classifies it operator-provisioned. There is exactly one key,
    one generator, one provenance signal — mirroring the JWT half, which was always
    sound.

    Never CLOBBER an operator-provisioned key: if a DIFFERENT key already occupies the
    file AND carries a ``.operator`` marker, that is a genuine "env and file disagree"
    misconfiguration -> fail closed. A DIFFERENT marker-less key is either our own
    auto-gen (which in production only ever crash-looped, so it holds no committed
    data) or dev scratch: the explicit env value is authoritative, so replace it.
    """
    marker_path = key_path + ".operator"
    if os.path.isfile(key_path):
        try:
            with open(key_path) as f:
                existing = f.read().strip()
        except (OSError, PermissionError):
            existing = ""
        if existing and existing != key_value:
            if os.path.isfile(marker_path):
                logger.error("=" * 60)
                logger.error(
                    "FATAL: ENCRYPTION_KEY differs from the operator-provisioned "
                    f"at-rest key already present at {key_path}."
                )
                logger.error(
                    "Refusing to overwrite it (that would make existing encrypted "
                    "data undecryptable). Unset ENCRYPTION_KEY to keep using the key "
                    "file, or reconcile the two, then restart."
                )
                logger.error("=" * 60)
                raise SystemExit(1)
            logger.warning(
                "Replacing a marker-less at-rest key at %s with the "
                "operator-provided ENCRYPTION_KEY.",
                key_path,
            )
    if _write_key_file_0600(key_path, key_value) and not os.path.isfile(marker_path):
        try:
            with open(marker_path, "w") as mf:
                mf.write("")
        except (OSError, PermissionError) as e:
            logger.warning(f"Could not write provenance marker {marker_path}: {e}")


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
            key, auto = _persist_or_load_key(
                os.path.join(_KEYS_DIR, "jwt_secret.key"), lambda: secrets.token_hex(32)
            )
            self.JWT_SECRET_KEY = key
            self._jwt_key_auto_generated = auto
            if self.ENVIRONMENT == "development":
                logger.info("Using auto-generated JWT_SECRET_KEY (persisted to /app/keys/)")
        else:
            self._jwt_key_auto_generated = False

        # Handle ENCRYPTION_KEY — the at-rest Fernet key that actually encrypts stored
        # secrets. bonnyr-f5 #193 B-3: gate on the value we PROTECT, not a shadow.
        # There is ONE key file (``_encryption_key_path()`` — the same file
        # ``core.encryption`` loads), ONE generator, ONE provenance signal.
        #   * ENCRYPTION_KEY set  -> validate it is a real Fernet key (fail clearly if
        #     not), write it to the key file + drop the .operator marker, and mark it
        #     operator-provided (auto=False). ``core.encryption`` then loads THIS value.
        #   * ENCRYPTION_KEY unset/empty (compose `${ENCRYPTION_KEY:-}` = "") -> load or
        #     generate the key file; provenance comes from the .operator marker.
        enc_path = _encryption_key_path()
        if not self.ENCRYPTION_KEY:
            from cryptography.fernet import Fernet
            key, auto = _persist_or_load_key(enc_path, lambda: Fernet.generate_key().decode())
            self.ENCRYPTION_KEY = key
            self._encryption_key_auto_generated = auto
            if self.ENVIRONMENT == "development":
                logger.info("Using auto-generated ENCRYPTION_KEY (persisted to /app/keys/)")
        else:
            # bonnyr-f5 #193 B-3: ENCRYPTION_KEY is now CONSUMED as the at-rest key.
            # An invalid value used to pass the production gate while encrypting
            # nothing (secrets.token_hex(16), the old printed remedy, is NOT a Fernet
            # key); fail clearly instead.
            if not _is_valid_fernet_key(self.ENCRYPTION_KEY):
                logger.error("=" * 60)
                logger.error("FATAL: ENCRYPTION_KEY is not a valid Fernet key")
                logger.error("=" * 60)
                logger.error(
                    "It must be a 32-byte url-safe base64 Fernet key. Generate one with:"
                )
                logger.error(
                    "  ENCRYPTION_KEY=$(python3 -c \"from cryptography.fernet import "
                    "Fernet; print(Fernet.generate_key().decode())\")"
                )
                logger.error("=" * 60)
                raise SystemExit(1)
            _adopt_operator_encryption_key(enc_path, self.ENCRYPTION_KEY)
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

        # Flagged outside every slice: `"*" in self.ALLOWED_ORIGINS` was a SUBSTRING
        # test on the raw CSV, so a legitimate origin that merely CONTAINS a '*'
        # (e.g. a subdomain-wildcard entry `https://*.example.com`) was wrongly
        # rejected. Mean "a wildcard origin ENTRY": test the parsed origin list for
        # an exact `*`.
        if "*" in self.cors_origins:
            issues.append(
                "ALLOWED_ORIGINS contains '*' (wildcard) — set specific origins"
            )

        if self.ENVIRONMENT == "production" and any(
            "localhost" in origin for origin in self.cors_origins
        ):
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
            # bonnyr-f5 #193 B-3: ENCRYPTION_KEY is a Fernet key (consumed as the
            # at-rest key), NOT secrets.token_hex(16) — that old recipe printed an
            # invalid key that encrypted nothing. Match .env.example:42.
            logger.error(
                "  ENCRYPTION_KEY=$(python3 -c \"from cryptography.fernet import "
                "Fernet; print(Fernet.generate_key().decode())\")"
            )
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
