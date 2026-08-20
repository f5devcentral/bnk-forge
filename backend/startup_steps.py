"""
Startup initialization steps for BNK-Forge.

Each function is a self-contained startup step that can succeed or fail
independently. They are called in order by the lifespan manager in main.py.
"""

import logging

from core.config import settings

logger = logging.getLogger(__name__)


def init_database_step():
    """Verify database connectivity. Fatal if this fails."""
    from database import init_database
    init_database()


def seed_defaults_step():
    """Seed system defaults into the database."""
    from database import get_db_context
    from services.blueprint_builtin_source_service import ensure_default_builtin_blueprint_source, sync_builtin_source
    from services.blueprint_default_source_service import ensure_default_blueprint_source
    from services.blueprint_sync_service import BlueprintSyncService
    from services.defaults_service import seed_defaults
    with get_db_context() as db:
        seeded_count = seed_defaults(db)
        source = ensure_default_blueprint_source(db)
        if source is not None and source.is_active:
            try:
                BlueprintSyncService(db).sync_git_source(source)
            except Exception as sync_err:
                logger.warning(f"  Default blueprint source sync failed: {sync_err}")
        try:
            builtin_source = ensure_default_builtin_blueprint_source(db)
            sync_builtin_source(db, builtin_source)
        except Exception as builtin_err:
            logger.warning(f"  Builtin blueprint source sync failed: {builtin_err}")
        db.commit()
    if seeded_count > 0:
        logger.info(f"  Seeded {seeded_count} system defaults")
    else:
        logger.info("  System defaults already configured")


def seed_stack_templates_step():
    """Seed stack templates."""
    from database import get_db_context
    from services.stack_template_seed import seed_stack_templates
    with get_db_context() as db:
        created, updated = seed_stack_templates(db)
    if created > 0 or updated > 0:
        logger.info(f"  Stack templates: {created} created, {updated} updated")
    else:
        logger.info("  Stack templates up to date")


def seed_python_modules_step():
    """Mirror the Python module registry into ModuleLibrary."""
    from database import get_db_context
    from services.python_module_seeder import seed_python_modules
    with get_db_context() as db:
        created, updated = seed_python_modules(db)
    if created or updated:
        logger.info(f"  Python modules: {created} created, {updated} updated")
    else:
        logger.info("  Python modules up to date")


def seed_k8s_builtin_modules_step():
    """Upsert built-in k8s module entries into ModuleLibrary.

    k8s modules (bnk-prerequisites, network-setup, cert-manager, bnk-cert-issuer)
    are executed by the kubernetes engine, not the Python SSH registry, so they
    are not covered by seed_python_modules_step.  This step ensures all four have
    a ModuleLibrary row so deploy gate checks pass.
    """
    from database import get_db_context
    from services.k8s_builtin_module_seeder import seed_k8s_builtin_modules
    with get_db_context() as db:
        created, updated = seed_k8s_builtin_modules(db)
    if created or updated:
        logger.info(f"  k8s builtin modules: {created} created, {updated} updated")
    else:
        logger.info("  k8s builtin modules up to date")


def sync_module_catalog_step():
    """Sync git module catalog on boot (best-effort; non-fatal).

    Skips cleanly if:
    - No default module_library.git_url is configured.
    - A ModuleSource row exists for the configured URL but is_active=False (off-switch).
    - The TTL has not expired (force=False → up to 300 s between syncs).
    - Another instance already holds the sync advisory lock (multi-replica boot).

    On clone/pull/network failure, logs a warning and returns — a fresh or
    offline box must still boot; the manual "Sync all" button remains as fallback.

    A Postgres transaction-scoped advisory lock prevents duplicate syncs when
    multiple replicas boot concurrently; the lock auto-releases at transaction end
    (commit, rollback, or connection close) — no explicit unlock needed.
    SQLite (tests) skips the lock.
    """
    import sqlalchemy as sa

    from database import get_db_context
    from models import ApplicationSetting, ModuleSource
    from services.git_auth_service import GitAuthService
    from services.module_catalog_service import sync_module_catalog

    # pg_try_advisory_xact_lock key — "BNKMOD" in ASCII packed into 48 bits
    _SYNC_LOCK_KEY = 0x424E4B4D4F44

    with get_db_context() as db:
        # ── Secondary guard: skip if git_url is genuinely empty/missing ──────
        git_url_setting = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.git_url"
        ).first()
        if not git_url_setting or not git_url_setting.value:
            logger.info("  Module catalog sync skipped — no catalog git_url configured")
            return

        # ── FIX 1: off-switch — respect ModuleSource.is_active ────────────────
        # Normalize the URL the same way _ensure_official_module_source does
        # so the lookup matches the row that would be used during sync.
        clean_url = GitAuthService.strip_url_credentials(git_url_setting.value)
        existing_source = db.query(ModuleSource).filter(
            ModuleSource.source_type == "git",
            ModuleSource.url == clean_url,
        ).first()
        if existing_source is not None and not existing_source.is_active:
            logger.info(
                "  Module catalog sync skipped — official module source '%s' is deactivated",
                existing_source.name,
            )
            return

        # ── FIX 6a: Postgres advisory lock — skip if another replica is syncing
        dialect = db.bind.dialect.name if db.bind else "postgresql"
        lock_acquired = True
        if dialect == "postgresql":
            lock_acquired = db.execute(
                sa.text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": _SYNC_LOCK_KEY},
            ).scalar()
            if not lock_acquired:
                logger.info(
                    "  Module catalog sync skipped — another instance holds the sync lock"
                )
                return

        try:
            stats = sync_module_catalog(db, force=False)
            created = stats.get("created", 0)
            updated = stats.get("updated", 0)
            errors = stats.get("errors", [])
            ref = stats.get("synced_version") or "unknown"

            # ── FIX 3: only commit + invalidate cache on a clean sync ─────────
            if errors:
                db.rollback()
                logger.warning(
                    "  Module catalog sync completed with errors — rolled back:"
                    " created=%d, updated=%d, errors=%s",
                    created, updated, errors,
                )
            elif stats.get("skipped_recent"):
                logger.info("  Module catalog sync skipped (TTL not expired)")
            else:
                db.commit()
                from core.cache import invalidate_cache
                invalidate_cache("module_library:*")
                logger.info(
                    "  Module catalog sync: created=%d, updated=%d, ref=%s",
                    created, updated, ref,
                )
        except Exception as sync_err:
            logger.warning("  Module catalog sync failed (non-fatal): %s", sync_err)


def seed_cli_bnkctl_modules_step():
    """Upsert built-in cli-bnkctl module entries into ModuleLibrary.

    CLI-bnkctl modules (awsbnkctl bnk-demo, etc.) are executed by the CliEngine
    (local subprocess), not the Python SSH registry, so they are not covered by
    seed_python_modules_step. This step ensures they have a ModuleLibrary row
    so deploy gate checks pass.
    """
    from database import get_db_context
    from services.cli_bnkctl_module_seeder import seed_cli_bnkctl_modules
    with get_db_context() as db:
        created, updated = seed_cli_bnkctl_modules(db)
    if created or updated:
        logger.info(f"  cli-bnkctl modules: {created} created, {updated} updated")
    else:
        logger.info("  cli-bnkctl modules up to date")


def seed_deployable_releases_step():
    """Seed BNK deployable releases into the catalog if not already present."""
    from database import get_db_context
    from services.bare_metal.version_profiles import BnkDeployableReleaseService
    with get_db_context() as db:
        seeded_count = BnkDeployableReleaseService(db).seed_profiles()
        db.commit()
    if seeded_count > 0:
        logger.info(f"  Seeded {seeded_count} BNK deployable release(s)")
    else:
        logger.info("  BNK deployable releases already configured")


def seed_auth_step():
    """Seed default admin user if no users exist; always reconcile MCP service account."""
    from database import get_db_context
    from services.auth_service import ensure_service_user, seed_admin_user
    with get_db_context() as db:
        admin = seed_admin_user(db)
    if admin:
        logger.info("  Created default admin user — change password on first login")
        logger.info("  See docs/INSTALLATION.md for first-login instructions")
    else:
        logger.info("  Users already exist")

    # Ensure the MCP service account exists and its password hash matches the
    # current MCP_SERVICE_PASSWORD — prevents auth drift when the env var is
    # rotated. #187: only when a password is actually configured; never seed the
    # account with a shipped default. Unset -> MCP is simply unavailable until an
    # operator sets MCP_SERVICE_PASSWORD (and gives the MCP server the same value).
    if settings.MCP_SERVICE_PASSWORD:
        with get_db_context() as db:
            ensure_service_user(
                db,
                username=settings.MCP_SERVICE_USERNAME,
                password=settings.MCP_SERVICE_PASSWORD,
            )
    else:
        logger.warning(
            "  MCP_SERVICE_PASSWORD is not set — MCP service account not seeded; "
            "the MCP server will be unable to authenticate until you set it"
        )

    if settings.REQUIRE_AUTH:
        logger.info("  Authentication ENABLED (REQUIRE_AUTH=true)")
    else:
        logger.warning("  Authentication DISABLED (REQUIRE_AUTH=false)")


def mint_builtin_agent_token_step():
    """Write a bootstrap token for the built-in forge-agent (#148).

    Agent-facing endpoints require an agent-class bearer token, so the built-in
    agent -- which ships in docker-compose.yml with no operator-provisioned
    token -- needs one it can find. It registers BEFORE it has an agent_id, so
    an agent_id-bound token from _mint_agent_token cannot exist yet; this
    token deliberately carries role=agent and NO agent_id. That lets it
    register and connect the WS as a claimless agent, and nothing more.

    Written to its OWN VOLUME (AGENT_TOKEN_DIR, default /app/agent-token) --
    not into the keys volume beside jwt_secret.key. That is what lets
    docker-compose.yml hand the agent container this one file and nothing
    else, and it works on a cold first boot: a volume `subpath` mount fails
    container creation if the path does not exist yet, and on first boot the
    backend has not written anything when the agent container is created.
    A dedicated named volume is created empty by Docker and needs no
    ordering. The file is stable across restarts: reissued only when
    missing, no longer valid for the current JWT_SECRET_KEY, or close to
    expiry -- so a running agent keeps working across backend restarts.

    Only meaningful when BENCHMARK_AGENT_AUTH_REQUIRED is on; when it is off,
    the endpoints are open and the file is harmless.
    """
    import os
    from datetime import UTC, datetime, timedelta

    from core.errors import UnauthorizedError
    from services.auth_service import create_access_token, decode_token

    token_dir = os.environ.get("AGENT_TOKEN_DIR", "/app/agent-token")
    path = os.path.join(token_dir, "builtin_agent.token")
    lifetime = timedelta(days=365)
    # Reissue while there is still comfortably more life left than the gap
    # between backend restarts. A token that merely "decodes today" is not
    # good enough: if it expires under a running agent, every heartbeat starts
    # 4401ing and nothing reissues until the NEXT restart -- a silent lockout,
    # which is the one thing a bootstrap credential must never do.
    renew_before = timedelta(days=30)

    existing = None
    try:
        with open(path) as f:
            existing = f.read().strip() or None
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("  Could not read %s: %s", path, exc)

    if existing:
        try:
            payload = decode_token(existing)
            exp = payload.get("exp")
            remaining = (
                datetime.fromtimestamp(int(exp), tz=UTC) - datetime.now(UTC)
                if exp is not None else timedelta(0)
            )
            if remaining > renew_before:
                logger.info("  Built-in agent bootstrap token present and valid")
                return
            logger.info(
                "  Built-in agent bootstrap token expires in %s — reissuing early", remaining
            )
        except UnauthorizedError:
            logger.info("  Built-in agent bootstrap token stale (secret rotated?) — reissuing")

    token = create_access_token(
        {"sub": "forge-builtin-agent", "role": "agent"},
        expires_delta=lifetime,
    )
    try:
        os.makedirs(token_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write(token)
        # 0644, not 0600: the agent container runs as uid 1001 (Dockerfile.agent)
        # and the backend as another uid, and the file crosses between them via
        # the compose mount. World-read is the mechanism, not an accident -- the
        # token is deliberately narrow (role=agent, no agent_id) so this exposure
        # buys register + claimless WS and nothing else. Never widen its claims.
        # chmod AFTER write, not via an opener: an opener's mode applies only on
        # create, so a rewrite of an existing 0600 file would keep it 0600 and
        # the agent could not read the reissued token.
        os.chmod(path, 0o644)
        logger.info("  Wrote built-in agent bootstrap token to %s", path)
    except OSError as exc:
        logger.warning(
            "  Could not write built-in agent bootstrap token (%s); the built-in "
            "agent will fail to register while BENCHMARK_AGENT_AUTH_REQUIRED is on",
            exc,
        )


def assert_lock_columns_step():
    """Assert that all whitelisted entity tables have the four lock columns.

    Runs once on app boot. Exits non-zero (via SystemExit) if any of the
    four required columns is missing on any allowed table. This catches schema
    drift even when the full app doesn't boot (e.g. CI migration smoke tests).

    Uses information_schema.columns on Postgres, pragma_table_info on SQLite.
    """
    import sys

    from database import get_db_context
    from services.entity_lock import _ALLOWED_LOCK_TABLES

    _REQUIRED_LOCK_COLUMNS = frozenset(
        {"lock_fence_token", "holding_task_id", "heartbeat_at", "lock_acquired_at"}
    )

    with get_db_context() as db:
        import sqlalchemy as sa

        dialect = db.bind.dialect.name if db.bind else "postgresql"

        for table in sorted(_ALLOWED_LOCK_TABLES):
            if dialect == "sqlite":
                rows = db.execute(
                    sa.text(f"PRAGMA table_info({table})")  # noqa: S608
                ).fetchall()
                present = {r[1] for r in rows}  # column name is index 1
            else:
                rows = db.execute(
                    sa.text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :t"
                    ),
                    {"t": table},
                ).fetchall()
                present = {r[0] for r in rows}

            missing = _REQUIRED_LOCK_COLUMNS - present
            if missing:
                logger.critical(
                    "Lock column assertion FAILED: table=%s missing=%s — "
                    "run alembic upgrade head and restart",
                    table,
                    sorted(missing),
                )
                sys.exit(1)

    logger.info("  Lock column assertion passed for %d tables", len(_ALLOWED_LOCK_TABLES))


def _detect_missing_schema(connection):
    """Compare ORM metadata against the live DB; return drift the DB is MISSING.

    Reuses Alembic's autogenerate machinery (``compare_metadata`` with a
    ``MigrationContext`` bound to the live connection) instead of hand-rolling
    column introspection. The diff is interpreted from the DB's point of view:

      * ``add_table`` / ``add_column``  → the ORM declares it but the DB lacks it.
        This is the runtime-crash case (#161): code SELECTs a column the DB does
        not have. We FLAG these.
      * ``remove_table`` / ``remove_column`` → the DB has something the ORM no
        longer declares (legacy/dropped tables, association tables created out of
        band). This is NOT a runtime crash for the running code, so we IGNORE it.
      * ``modify_type`` / ``modify_nullable`` / ``modify_default`` / etc. → benign
        type/nullable/server_default noise (heavily skewed by SQLite-vs-Postgres
        dialect differences). We IGNORE these — they are not "the DB is missing
        something the ORM needs".

    Scope: only tables/columns declared in ORM metadata (``Base.metadata.tables``)
    are considered.  Non-public schemas (pg_catalog, information_schema, out-of-band
    extension tables) are excluded via the ``include_object`` filter so Postgres
    extension schemas cannot produce false-positive startup failures.

    Returns a list of human-readable strings describing only the FLAGGED
    (missing-from-DB) drift. Empty list == no actionable drift.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    import models  # noqa: F401 — import side-effect: registers every table on Base.metadata
    from database import Base

    orm_table_names: set[str] = set(Base.metadata.tables.keys())

    def _include_object(obj, name, type_, reflected, compare_to):  # type: ignore[return]
        """Restrict comparison to ORM-declared tables only.

        ``reflected=True`` means the object came from the live DB (not ORM);
        ``reflected=False`` means it came from ORM metadata.  We include an object
        when the table name is present in our ORM registry — this excludes non-public
        system schema tables (pg_catalog, information_schema) and any out-of-band
        extension tables that Alembic might pick up from the DB, preventing false
        positive startup failures on installations with extra Postgres schemas.
        """
        if type_ == "table":
            return name in orm_table_names
        elif type_ == "column":
            # obj is a SQLAlchemy Column; obj.table is the parent Table object.
            # Use getattr with a fallback to avoid bool() on a SA clause element.
            parent_table = getattr(obj, "table", None)
            table_name = parent_table.name if parent_table is not None else name
            return table_name in orm_table_names
        return True  # pass through other object types unchanged

    mc = MigrationContext.configure(
        connection,
        opts={"include_object": _include_object, "include_schemas": False},
    )
    diffs = compare_metadata(mc, Base.metadata)

    missing: list[str] = []
    for diff in diffs:
        # compare_metadata yields either a tuple, or a list of column-level
        # tuples grouped per table. Normalise to a flat list of tuples.
        entries = diff if isinstance(diff, list) else [diff]
        for entry in entries:
            op = entry[0]
            if op == "add_table":
                # entry = ("add_table", Table)
                missing.append(f"table '{entry[1].name}' (ORM declares it; DB is missing it)")
            elif op == "add_column":
                # entry = ("add_column", schema, table_name, Column)
                missing.append(
                    f"column '{entry[2]}.{entry[3].name}' (ORM declares it; DB is missing it)"
                )
            # remove_table / remove_column / modify_* are intentionally ignored —
            # see docstring for the rationale (extra DB objects + dialect noise).

    return missing


def assert_metadata_matches_db_step():
    """ORM-vs-DB drift gate — fail loud when the DB is MISSING what the code expects.

    Generalises ``assert_lock_columns_step`` (which only checked 4 lock columns on
    a whitelist) into a repo-wide schema-drift gate. Compares the full SQLAlchemy
    ORM metadata (``Base.metadata``) against the live DB schema using Alembic's
    ``compare_metadata`` and exits non-zero (``sys.exit(1)``, same fail-loud
    pattern as ``assert_lock_columns_step``) if the ORM declares any table or
    column the DB does not have.

    This catches the #161 failure mode: a migration was never applied (or the DB
    was stamped-to-head without create_all running the table bodies), so the code
    SELECTs a column that does not exist and crashes at request time. We turn that
    into a clear, actionable boot-time failure instead.

    Only MISSING-from-DB drift is fatal. Extra DB objects and type/nullable/
    server_default differences are tolerated (see ``_detect_missing_schema``).
    """
    import sys

    from database import get_db_context

    with get_db_context() as db:
        connection = db.connection()
        missing = _detect_missing_schema(connection)

    if missing:
        logger.critical(
            "Schema drift assertion FAILED — the database is MISSING %d object(s) the "
            "ORM expects:\n  - %s\n"
            "The code will crash at runtime SELECTing these. Run `alembic upgrade head` "
            "and restart. (If this is a fresh DB, ensure init_db.py / migrations ran.)",
            len(missing),
            "\n  - ".join(missing),
        )
        sys.exit(1)

    logger.info("  Schema drift assertion passed — ORM metadata matches the live DB")


def cleanup_stale_state_step():
    """Clean up stuck tasks and stale workspace locks from previous run."""
    from database import get_db_context
    from services.execution_janitor import reset_stale_executions

    with get_db_context() as db:
        result = reset_stale_executions(db)
    if result["tasks_reset"] or result["stale_destroy_stacks_reset"]:
        logger.info(
            "  Reset %d stuck task(s), %d stuck-destroying stack(s)",
            result["tasks_reset"],
            result["stale_destroy_stacks_reset"],
        )

    # Module locks self-heal via the heartbeat-reclaim mechanism: any orphaned
    # lock from a previous worker generation will be reclaimed by the next
    # acquire after RECLAIM_AFTER_SECONDS. No startup sweep needed — and a
    # blanket force_release would clobber locks held by celery workers running
    # on a different lifecycle.


def start_scheduler_step(scheduler):
    """Start APScheduler with credential refresh + project stats recompute + stale-execution janitor jobs."""
    from apscheduler.triggers.interval import IntervalTrigger

    from services.credential_refresh_service import CredentialRefreshService
    refresh_service = CredentialRefreshService()

    scheduler.add_job(
        func=refresh_service.check_and_refresh_all,
        trigger=IntervalTrigger(minutes=5),
        id='credential_refresh',
        name='Automatic credential refresh',
        replace_existing=True
    )

    scheduler.add_job(
        func=_recompute_project_stats_job,
        trigger=IntervalTrigger(minutes=5),
        id='project_stats_recompute',
        name='Periodic project stats recompute',
        replace_existing=True,
    )

    scheduler.add_job(
        func=_stale_execution_janitor_job,
        trigger=IntervalTrigger(minutes=2),
        id='stale_execution_janitor',
        name='Reset orphaned parallel executions and tasks',
        replace_existing=True,
    )

    scheduler.add_job(
        func=_stale_entity_lock_sweep_job,
        trigger=IntervalTrigger(minutes=2),
        id='stale_entity_lock_sweep',
        name='Reclaim stale heartbeat locks across all entity tables',
        replace_existing=True,
    )

    scheduler.start()
    logger.info("  Credential auto-refresh every 5m")
    logger.info("  Project stats recompute every 5m")
    logger.info("  Stale-execution janitor every 2m")
    logger.info("  Stale entity lock sweep every 2m")


def _recompute_project_stats_job():
    """APScheduler entry point — owns its DB session lifecycle."""
    from database import get_db_context
    from services.project_service import recompute_all_project_counts

    try:
        with get_db_context() as db:
            result = recompute_all_project_counts(db)
        if result.get("projects_repaired"):
            logger.info(
                "Project stats recompute repaired %d/%d projects",
                result["projects_repaired"],
                result["projects_scanned"],
            )
    except Exception as e:
        logger.warning(f"Project stats recompute job failed: {e}")


def _stale_execution_janitor_job():
    """APScheduler entry point — owns its DB session lifecycle."""
    from database import get_db_context
    from services.execution_janitor import reset_stale_executions

    try:
        with get_db_context() as db:
            reset_stale_executions(db)
    except Exception as e:
        logger.warning(f"Stale-execution janitor job failed: {e}")


def _stale_entity_lock_sweep_job():
    """APScheduler entry point — sweeps stale entity locks across all tables."""
    from database import get_db_context
    from services.entity_lock import sweep_stale_entity_locks

    try:
        with get_db_context() as db:
            sweep_stale_entity_locks(db)
    except Exception as e:
        logger.warning(f"Stale entity lock sweep job failed: {e}")


def init_ssh_tunnel_manager_step():
    """Initialize SSH tunnel manager (singleton, lazy)."""
    from services.ssh_tunnel_manager import get_tunnel_manager
    get_tunnel_manager()
    logger.info("  Tunnels opened on demand")


def populate_service_registry_step():
    """Populate the service registry with operator connections."""
    from services.operator_registry import operator_connections as op_conns
    from services.registry import ServiceRegistry

    svc_registry = ServiceRegistry.get()
    svc_registry.operator_connections = op_conns
    logger.info("  operator_connections=ready")
