"""
BNK-Forge API - Main Application Entry Point
Modular FastAPI application for OpenTofu module deployment and F5 security management
"""
import asyncio
import atexit
import logging
import os
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi import HTTPException as FastAPIHTTPException
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

# Import core modules
from core.config import settings
from core.errors import (
    AppError,
    app_error_handler,
    general_exception_handler,
    http_exception_handler,
)
from core.logging_config import configure_logging
from core.maintenance import clear_maintenance_mode
from core.maintenance_middleware import MaintenanceMiddleware

# DEVOPS-004: Configure structured logging before anything else logs
configure_logging(
    environment=settings.ENVIRONMENT,
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    service_name=settings.APP_NAME
)

# Legacy configuration removed in v2 - using environment variables directly
LOGS_DIR = os.getenv("LOGS_DIR", "/tmp/bnk-forge-logs")

# Import database
from database import SessionLocal  # noqa: F401 — used by route handlers via get_db()
from routes.alert_channels import router as alert_channels_router

# NOTE: KubernetesSync and TerragruntSync imports removed in v2.0.1
# Sync is now project-based via routes/api.py and routes/projects.py
# Import all routers
from routes.api import router as api_router
from routes.audit import router as audit_router
from routes.auth import router as auth_router
from routes.bare_metal_deployments import router as bare_metal_deployments_router
from routes.bare_metal_hosts import router as bare_metal_hosts_router
from routes.bare_metal_version_profiles import router as bare_metal_version_profiles_router
from routes.benchmarks import router as benchmarks_router
from routes.benchmarks import ws_router as benchmarks_ws_router
from routes.bf_conf_templates import router as bf_conf_templates_router
from routes.bluefield_images import router as bluefield_images_router
from routes.blueprint_catalog import router as blueprint_catalog_router
from routes.bnk_upgrade import router as bnk_upgrade_router
from routes.cloud_auth import router as cloud_auth_router
from routes.config_export import router as config_export_router
from routes.config_promotion import router as config_promotion_router
from routes.connectivity import router as connectivity_router
from routes.container_registries import router as container_registries_router
from routes.credential_templates import router as credential_templates_router
from routes.discovery import router as discovery_router
from routes.dpus import router as dpus_router
from routes.dpus_websocket import router as dpus_ws_router
from routes.drift import router as drift_router
from routes.f5_devices import credentials_router as f5_credentials_router
from routes.f5_devices import devices_router as f5_devices_router
from routes.fleet import router as fleet_router
from routes.helm import router as helm_router
from routes.k8s import (
    clusters_router,
    crds_router,
    dpf_router,
    f5bnk_router,
    llm_observability_router,
    recovery_router,
    resources_router,
    tmm_debug_router,
    topology_router,
    tunnels_router,
    waf_dashboard_router,
    waf_panels_router,
    waf_dashboard_tabs_router,
    waf_gateway_router,
    waf_logs_router,
    waf_policies_router,
)
from routes.k8s_websocket import router as k8s_websocket_router
from routes.licensing import router as licensing_router
from routes.module_library import router as module_library_router
from routes.module_sources import router as module_sources_router
from routes.notifications import router as notifications_router
from routes.notifications import ws_router as notifications_ws_router
from routes.operator_polling import router as operator_polling_router
from routes.operator_ws import router as operator_ws_router
from routes.operators import router as operators_router
from routes.project_deployments import router as project_deployments_router
from routes.project_execution import router as project_execution_router
from routes.project_modules import router as project_modules_router
from routes.project_orchestration import router as project_orchestration_router
from routes.project_secrets import router as project_secrets_router
from routes.project_variable_mappings import router as project_variable_mappings_router
from routes.project_variables import router as project_variables_router
from routes.projects import router as projects_router
from routes.qkview import router as qkview_router
from routes.registry import router as registry_router
from routes.runbooks import router as runbooks_router
from routes.snapshots import router as snapshots_router
from routes.ssh_credentials import router as ssh_credentials_router
from routes.stacks import router as stacks_router
from routes.state_viewer import router as state_viewer_router
from routes.system import public_router as system_public_router
from routes.system import router as system_router
from routes.tasks import router as tasks_router
from routes.tasks import ws_router as tasks_ws_router

# Get logger (logging already configured via configure_logging above)
logger = logging.getLogger(__name__)

# Initialize background scheduler
scheduler = BackgroundScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - handles startup and shutdown"""
    from startup_steps import (
        assert_lock_columns_step,
        assert_metadata_matches_db_step,
        cleanup_stale_state_step,
        init_database_step,
        init_ssh_tunnel_manager_step,
        populate_service_registry_step,
        seed_auth_step,
        seed_cli_bnkctl_modules_step,
        seed_defaults_step,
        seed_k8s_builtin_modules_step,
        seed_python_modules_step,
        seed_stack_templates_step,
        start_scheduler_step,
        sync_module_catalog_step,
    )

    # Startup
    logger.info("=" * 60)
    logger.info("BNK-Forge Startup")
    logger.info("=" * 60)

    # Database init and lock-column assertion are fatal — everything else is best-effort.
    # assert_lock_columns_step calls sys.exit(1) if migration hasn't been run.
    FATAL_STEPS = [
        ("Database", init_database_step),
        # Repo-wide ORM-vs-DB drift gate (#161): fails loud if the DB is missing a
        # table/column the code expects. assert_lock_columns_step stays as a
        # targeted, fast check for the four lock columns — the general gate is the
        # net for everything else.
        ("Schema drift", assert_metadata_matches_db_step),
        ("Lock columns", assert_lock_columns_step),
    ]
    # Clear any stale maintenance mode flag from previous crash/restart
    try:
        clear_maintenance_mode()
        logger.info("Cleared any stale maintenance mode flag")
    except Exception as e:
        logger.warning(f"Could not clear maintenance mode (Redis may be unavailable): {e}")

    BEST_EFFORT_STEPS = [
        ("System defaults", seed_defaults_step),
        ("Python module catalog", seed_python_modules_step),
        ("k8s builtin modules", seed_k8s_builtin_modules_step),
        ("Module catalog sync", sync_module_catalog_step),
        ("cli-bnkctl modules", seed_cli_bnkctl_modules_step),
        ("Stack templates", seed_stack_templates_step),
        ("Auth", seed_auth_step),
        ("Stale state cleanup", cleanup_stale_state_step),
        ("Scheduler", lambda: start_scheduler_step(scheduler)),
        ("SSH tunnel manager", init_ssh_tunnel_manager_step),
        ("Service registry", populate_service_registry_step),
    ]

    for name, step in FATAL_STEPS:
        try:
            step()
            logger.info(f"✓ {name}")
        except Exception as e:
            logger.critical(f"✗ {name}: {e}")
            raise SystemExit(f"Cannot start without {name}: {e}")

    for name, step in BEST_EFFORT_STEPS:
        try:
            # Run each step in a thread pool thread so the event loop stays
            # responsive during git I/O (clone/fetch) and DB migrations.
            # Each step owns its own DB session via get_db_context(), so
            # thread execution is safe.  Steps are awaited sequentially to
            # preserve ordering guarantees (e.g. seed_defaults before catalog sync).
            await asyncio.to_thread(step)
            logger.info(f"✓ {name}")
        except Exception as e:
            logger.error(f"✗ {name}: {e}")

    # Start WebSocket service (async — can't be a sync step)
    subscriber_task = None
    keepalive_task = None
    try:
        from services.websocket_service import keepalive_task as keepalive_fn
        from services.websocket_service import start_redis_subscriber
        subscriber_task = asyncio.create_task(start_redis_subscriber())
        keepalive_task = asyncio.create_task(keepalive_fn())
        logger.info("✓ WebSocket service (Redis subscriber + keepalive)")
    except Exception as e:
        logger.error(f"✗ WebSocket service: {e}")

    # Start reachability subsystem (async scheduler, one task per probe class).
    # Failure here must NOT block startup — connectivity is observability, not
    # the request path.
    try:
        from database import SessionLocal as _SessionLocal
        from services.reachability import registry as _reach_registry
        from services.reachability.probes.cluster import ClusterProbe
        from services.reachability.probes.ssh import SSHProbe
        _reach_registry.configure(db_session_factory=_SessionLocal)
        _reach_registry.register(ClusterProbe(db_session_factory=_SessionLocal))
        _reach_registry.register(SSHProbe(db_session_factory=_SessionLocal))
        await _reach_registry.start()
        logger.info("✓ Reachability scheduler")
    except Exception as e:
        logger.error(f"✗ Reachability scheduler: {e}")

    logger.info("=" * 60)
    logger.info("BNK-Forge API Ready")
    logger.info("=" * 60)

    yield

    # Shutdown
    logger.info("Shutting down BNK-Forge...")

    # Close all SSH tunnels
    try:
        from services.ssh_tunnel_manager import get_tunnel_manager
        tunnel_manager = get_tunnel_manager()
        tunnel_manager.close_all()
    except Exception as e:
        logger.warning(f"SSH tunnel cleanup error: {e}")

    # Stop reachability scheduler
    try:
        from services.reachability import registry as _reach_registry
        await _reach_registry.stop()
    except Exception as e:
        logger.warning(f"Reachability scheduler shutdown error: {e}")

    # Stop WebSocket tasks
    if subscriber_task:
        subscriber_task.cancel()
        try:
            await subscriber_task
        except asyncio.CancelledError:
            pass
    if keepalive_task:
        keepalive_task.cancel()
        try:
            await keepalive_task
        except asyncio.CancelledError:
            pass

    if scheduler.running:
        scheduler.shutdown()

# Create FastAPI app with lifespan
app = FastAPI(title="BNK-Forge API", version=settings.VERSION, lifespan=lifespan)

# Initialize rate limiter
# Limits: 100 requests per minute per IP for general endpoints
# Health checks and static content are not rate limited
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Security Headers Middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"  # Allow iframes from same origin
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        # Horizon 3 prep: API version headers for debugging and future API versioning
        response.headers["X-BNK-Forge-Version"] = settings.VERSION
        response.headers["X-BNK-Forge-API"] = "v1"
        return response

# OBS-001: Correlation ID Middleware — must be outermost (added first, runs last)
# Generates X-Request-ID for every request and propagates via contextvars
from core.correlation import CorrelationMiddleware

app.add_middleware(CorrelationMiddleware)

app.add_middleware(SecurityHeadersMiddleware)

# Enable CORS - Configurable origins for development and production
# Configured via ALLOWED_ORIGINS env var (parsed by Settings in core/config.py)
# Default: * (convenient local/container dev)
# In production, set ALLOWED_ORIGINS in .env to your domain(s)
# Example: ALLOWED_ORIGINS=https://bnk-forge.yourdomain.com,https://app.yourdomain.com
# Wildcard is intentionally allowed for development, but should be locked down for production.
allowed_origins = settings.cors_origins

# Log configured origins (helps with debugging deployment issues)
logger.info(f"CORS configured with allowed origins: {allowed_origins}")

# Note: When allow_origins=["*"], allow_credentials must be False per CORS spec
# For wildcard origins, credentials are handled via proxy headers instead
allow_creds = allowed_origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_creds,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
    max_age=600,
)

# Audit Middleware — logs all mutating API calls (POST/PUT/DELETE/PATCH)
# Must be added BEFORE auth middleware so it wraps the full request lifecycle
from core.audit_middleware import AuditMiddleware

app.add_middleware(AuditMiddleware)

# Maintenance Middleware — blocks non-system requests during restore operations
# Added after AuditMiddleware so maintenance blocks reach the audit log too
app.add_middleware(MaintenanceMiddleware)

# Authentication Middleware (must be added BEFORE registering routes)
# When REQUIRE_AUTH=true, all /api/ routes require a valid JWT Bearer token
# When REQUIRE_AUTH=false, all requests pass through (legacy/dev mode)
from core.auth_middleware import AuthMiddleware

app.add_middleware(AuthMiddleware)

# Register shutdown with atexit as backup
atexit.register(lambda: scheduler.shutdown() if scheduler.running else None)

# Register global error handlers
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(FastAPIHTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# Include all routers
app.include_router(api_router)
app.include_router(projects_router)
app.include_router(project_variables_router)  # Project variables & variable defaults
app.include_router(clusters_router)     # K8s cluster CRUD, scan, adaptive modules
app.include_router(crds_router)         # CRD discovery — D-018 P1
app.include_router(topology_router)    # Namespace topology graph — D-018 P4
app.include_router(resources_router)    # K8s resource CRUD, pod/node ops, metrics
app.include_router(f5bnk_router)        # F5 BNK gateways, topology, health
app.include_router(llm_observability_router)  # AI-gateway observability — Loki request analytics
app.include_router(dpf_router)          # NVIDIA DPF — DPU devices, clusters, services, health
app.include_router(waf_policies_router)   # WAF Policy Manager — appprotect.f5.com CRDs via PLM
app.include_router(waf_logs_router)       # WAF Security Logs — syslog endpoint resolution + log fetch
app.include_router(waf_dashboard_router)  # WAF Dashboard — ClickHouse analytics
app.include_router(waf_panels_router)     # WAF Panel Builder — CRUD + data queries
app.include_router(waf_dashboard_tabs_router)  # WAF Dashboard — custom tab CRUD
app.include_router(waf_gateway_router)    # WAF Gateway API — Gateway/HTTPRoute/WSP CRUD
app.include_router(bare_metal_hosts_router)               # Bare-metal DPU hosts — CRUD + discovery
app.include_router(f5_devices_router)                     # F5 BIG-IP devices — CRUD + read-only probe (D-023 P1)
app.include_router(f5_credentials_router)                 # F5 BIG-IP credentials — CRUD + test (D-023 P1)
app.include_router(bare_metal_deployments_router)          # Bare-metal deployments — lifecycle
app.include_router(bare_metal_version_profiles_router)     # BNK version profiles — version matrix
app.include_router(bluefield_images_router)                # DPU Provisioning — BFB image catalog
app.include_router(bf_conf_templates_router)               # DPU Provisioning — bf.conf template catalog
app.include_router(dpus_router)                            # DPU Provisioning — per-project DPUs + settings
app.include_router(dpus_ws_router)                         # DPU Provisioning — BMC serial console (WS)
app.include_router(tmm_debug_router)    # TMM debug sidecar — tmctl, configview, bdt_cli diagnostics
app.include_router(recovery_router)     # Post-reboot recovery — CWC cert re-sync, platform restart
app.include_router(tunnels_router)      # SSH tunnel management
app.include_router(module_library_router)
app.include_router(project_modules_router)
app.include_router(project_execution_router)     # OpenTofu init/plan/apply/destroy/deploy/retry/cancel
app.include_router(project_deployments_router)    # Deployment logs, history, state management
app.include_router(project_variable_mappings_router)  # Variable mappings & templates
app.include_router(cloud_auth_router)
app.include_router(state_viewer_router)  # State file viewer - fixed for v2
app.include_router(notifications_router)
app.include_router(notifications_ws_router)  # WebSocket — no JWT auth (WS has no Request headers)
app.include_router(tasks_ws_router)  # WebSocket — registered first (no JWT auth, WS has no Request headers)
app.include_router(tasks_router)
app.include_router(drift_router)  # Drift detection v2 - uses OpenTofuRuntime
app.include_router(system_public_router)  # Health endpoint — no auth (Docker healthcheck)
app.include_router(system_router)
app.include_router(credential_templates_router)
app.include_router(ssh_credentials_router)  # SSH credentials — first-class on-prem/bastion access
app.include_router(container_registries_router)  # Container registries — first-class OCI registry access
app.include_router(helm_router)
app.include_router(module_sources_router)
app.include_router(blueprint_catalog_router)
app.include_router(registry_router)
app.include_router(stacks_router)
app.include_router(k8s_websocket_router)  # WebSocket routes for K8s exec and log streaming
app.include_router(project_secrets_router)  # Project-level secrets management
app.include_router(discovery_router)  # Project-scoped SSH discovery trigger/read
app.include_router(auth_router)  # Authentication — login, users, JWT
app.include_router(audit_router)  # Audit trail — paginated log viewer
app.include_router(operators_router)  # Operator management — tokens, listing, commands
app.include_router(operator_ws_router)  # Operator WebSocket — agent connection endpoint
app.include_router(operator_polling_router)  # Operator polling — HTTP-based command dispatch
app.include_router(alert_channels_router)  # Alert channels — webhook/Slack/Teams notifications
app.include_router(config_export_router)  # Config export/import/diff — cluster config snapshots
app.include_router(bnk_upgrade_router)  # BNK upgrade workflow — version upgrades with health gates
app.include_router(runbooks_router)  # Runbook automation — diagnostic sequences for common BNK issues
app.include_router(licensing_router)  # BNK licensing — CWC license status, activation, telemetry via operator
app.include_router(qkview_router)  # QKView — CWC diagnostic tarball collection for F5 BNK
app.include_router(snapshots_router)  # Config snapshots — point-in-time backup and one-click rollback
app.include_router(config_promotion_router)  # Cross-cluster config promotion — export/diff/apply between clusters
app.include_router(project_orchestration_router)  # Parallel execution — deploy-all/destroy-all, execution plans
app.include_router(benchmarks_ws_router)  # Benchmark WebSocket — real-time progress (no JWT auth, WS has no headers)
app.include_router(benchmarks_router)  # AI Performance Benchmarks — Phase 2 dashboard
app.include_router(connectivity_router)  # Reachability state + SSE stream + force-probe
app.include_router(fleet_router)  # Fleet members — D-022 host-inclusive fleet inventory

# Health check for quick verification
@app.get("/ping")
def ping():
    """Simple ping endpoint for health checks"""
    return {"status": "ok", "message": "BNK-Forge API is running"}
