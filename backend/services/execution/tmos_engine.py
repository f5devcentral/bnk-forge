"""
TMOS Execution Engine — writes AS3 config to a classic BIG-IP via iControl REST.

Implements the DeploymentEngine ABC for modules with execution_engine="tmos".

Design:
  - No DB access in the hot path (all credentials resolved in Celery task via ModuleContext).
  - apply() is SYNCHRONOUS but contains an internal blocking poll loop — consistent with
    KubernetesEngine using asyncio.run() internally.  The ABC is sync-by-design.
  - The AS3 45s auto-swap trap: a POST may return a final 200 OR a 202+taskRef.
    apply() handles BOTH without assuming synchronous return.
  - destroy() is tenant-scoped ONLY — refuses empty ctx.as3_tenant (fail-closed).
  - D-017 honest failure: a failed declaration/task → OperationResult(success=False, ...).
  - Declaration bodies are NEVER logged (TLS keys).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from services.execution.engine_interface import (
    DeploymentEngine,
    ModuleContext,
    OperationResult,
    PlanResult,
)
from services.f5.icontrol_client import (
    _AS3_DRY_RUN_MIN,
    IControlWriteClient,
    _parse_as3_version,
    _parse_dry_run_delta,
    _task_terminal_result,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TmosEngine(DeploymentEngine):
    """
    Deployment engine that writes AS3 declarations to a classic F5 BIG-IP.

    Receives all connection details via ModuleContext TMOS fields:
        ctx.tmos_host, ctx.tmos_port, ctx.tmos_credential,
        ctx.tmos_verify_https, ctx.as3_tenant, ctx.as3_declaration

    The task layer (tmos_tasks.py) resolves the device + credential from DB
    and populates these fields before calling the engine.  The engine itself
    has zero DB knowledge.
    """

    def __init__(self, db_session_factory=None) -> None:
        """
        Args:
            db_session_factory: Optional callable returning a DB session.
                Only needed for get_outputs() which reads ProjectModule.outputs.
                The engine hot path (init/plan/apply/destroy) never touches DB.
        """
        self._db_factory = db_session_factory

    # ------------------------------------------------------------------
    # Client factory
    # ------------------------------------------------------------------

    def _client(self, ctx: ModuleContext) -> IControlWriteClient:
        """Build a write-capable iControl client from ModuleContext TMOS fields."""
        host = ctx.tmos_host
        port = ctx.tmos_port or 443
        credential = ctx.tmos_credential
        verify = ctx.tmos_verify_https if ctx.tmos_verify_https is not None else True

        if not host or not credential:
            raise ValueError(
                f"ModuleContext is missing tmos_host or tmos_credential for module {ctx.module_id}"
            )

        return IControlWriteClient(host, port, credential, verify_https_cert=verify)

    # ------------------------------------------------------------------
    # init — authenticate + AS3 version probe
    # ------------------------------------------------------------------

    def init(self, ctx: ModuleContext, on_output: Callable[[str], None] | None = None) -> OperationResult:
        """Authenticate to BIG-IP and verify AS3 ≥ 3.30.

        Version gate:  plan() uses controls.dryRun which requires AS3 ≥ 3.30.
        If the device has an older AS3, plan() will fail; we surface that here
        rather than allowing a silent failure later.
        """
        started = time.monotonic()
        try:
            client = self._client(ctx)

            if on_output:
                on_output(f"[tmos] Connecting to BIG-IP {ctx.tmos_host}:{ctx.tmos_port or 443}")

            token, expires_at = client.authenticate()
            if on_output:
                on_output(f"[tmos] Authenticated; token valid until {expires_at.isoformat()}")

            as3_ver_str = client.get_as3_version()
            as3_ver = _parse_as3_version(as3_ver_str)

            if on_output:
                on_output(f"[tmos] AS3 version: {as3_ver_str}")

            if as3_ver < _AS3_DRY_RUN_MIN:
                min_str = ".".join(str(v) for v in _AS3_DRY_RUN_MIN)
                msg = (
                    f"AS3 version {as3_ver_str} is below minimum {min_str} required for "
                    "controls.dryRun (plan). Upgrade AS3 before running plan operations."
                )
                logger.warning("TmosEngine.init: %s on %s", msg, ctx.tmos_host)
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=msg,
                    error_suggestion=f"Upgrade F5 AS3 to ≥ {min_str} on device {ctx.tmos_host}",
                )

            if on_output:
                on_output(f"[tmos] AS3 {as3_ver_str} meets minimum requirement. Init successful.")

            return OperationResult(
                success=True,
                stdout=f"BIG-IP {ctx.tmos_host} AS3 {as3_ver_str} ready",
                duration_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            logger.exception("TmosEngine.init failed for module %s", ctx.module_id)
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"TMOS init failed: {exc}",
            )

    # ------------------------------------------------------------------
    # plan — AS3 dry-run
    # ------------------------------------------------------------------

    def plan(self, ctx: ModuleContext, on_output: Callable[[str], None] | None = None) -> PlanResult:
        """POST declaration with controls.dryRun=true → return delta as PlanResult.

        Requires AS3 ≥ 3.30.  Call init() first to verify the version gate.
        """
        try:
            tenant = (ctx.as3_tenant or "").strip()
            declaration = ctx.as3_declaration

            if not tenant:
                return PlanResult(
                    has_changes=False,
                    details="Plan refused: as3_tenant is empty in ModuleContext",
                )

            if not declaration:
                return PlanResult(
                    has_changes=True,
                    details="No declaration in ModuleContext — will execute",
                )

            client = self._client(ctx)

            # Version gate: controls.dryRun is silently ignored on AS3 < 3.30,
            # meaning plan() would perform a REAL apply on old versions.
            # Cache the version string to avoid a second HTTP auth call in the error path.
            ver_str = client.get_as3_version()
            as3_ver = _parse_as3_version(ver_str)
            if as3_ver < _AS3_DRY_RUN_MIN:
                min_str = ".".join(str(v) for v in _AS3_DRY_RUN_MIN)
                return PlanResult(
                    has_changes=False,
                    details=(
                        f"Plan refused: AS3 {ver_str} < {min_str} — "
                        "controls.dryRun requires >=3.30 (would apply for real on this version)"
                    ),
                )

            if on_output:
                on_output(f"[tmos] Dry-run plan for tenant '{tenant}' on {ctx.tmos_host}")

            # post_declaration injects controls.dryRun=true
            resp = client.post_declaration(tenant, declaration, dry_run=True)

            # Dry-run may respond synchronously (200) or async (202).
            # If async, poll to the terminal result.
            if _has_task_ref(resp):
                task_id = resp.get("id") or resp.get("selfLink", "").rsplit("/", 1)[-1]
                if on_output:
                    on_output(f"[tmos] Dry-run returned task {task_id}; polling...")
                resp = client.poll_task(task_id, on_output=on_output)

            adds, changes, destroys, details = _parse_dry_run_delta(resp)
            has_changes = bool(adds or changes or destroys)

            if on_output:
                on_output(
                    f"[tmos] Dry-run complete: +{adds} ~{changes} -{destroys}"
                )

            return PlanResult(
                has_changes=has_changes,
                adds=adds,
                changes=changes,
                destroys=destroys,
                details=details,
                plan_id=None,  # AS3 has no saved plan concept
            )
        except Exception as exc:
            logger.exception("TmosEngine.plan failed for module %s", ctx.module_id)
            return PlanResult(
                has_changes=True,
                details=f"Plan failed ({exc}) — treating as has_changes",
            )

    # ------------------------------------------------------------------
    # apply — POST + async task-poll state machine
    # ------------------------------------------------------------------

    def apply(self, ctx: ModuleContext, on_output: Callable[[str], None] | None = None) -> OperationResult:
        """POST AS3 declaration and poll to terminal result.

        The 45s auto-swap trap:  AS3 may return a final 200 (sync) OR a 202
        with a task reference (async).  We handle BOTH paths.  Token is
        refreshed on every poll iteration (applies exceed the 20-min TTL).

        D-017: a non-2xx / failed task → OperationResult(success=False, ...).
        """
        started = time.monotonic()
        try:
            tenant = (ctx.as3_tenant or "").strip()
            declaration = ctx.as3_declaration

            if not tenant:
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message="apply refused: as3_tenant is empty in ModuleContext",
                    error_suggestion="Set as3_tenant in the module context before applying",
                )

            if not declaration:
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message="apply refused: no as3_declaration in ModuleContext",
                )

            if on_output:
                on_output(f"[tmos] Applying AS3 declaration for tenant '{tenant}' on {ctx.tmos_host}")

            client = self._client(ctx)
            resp = client.post_declaration(tenant, declaration, dry_run=False)

            # ------------------------------------------------------------------
            # Determine if the response is a final answer or an async task ref.
            # ------------------------------------------------------------------
            if _has_task_ref(resp):
                task_id = resp.get("id") or resp.get("selfLink", "").rsplit("/", 1)[-1]
                if on_output:
                    on_output(f"[tmos] AS3 returned task {task_id}; polling to completion...")
                resp = client.poll_task(task_id, on_output=on_output)

            # ------------------------------------------------------------------
            # Interpret the terminal response (D-017 honest failure).
            # ------------------------------------------------------------------
            success, message = _task_terminal_result(resp)

            duration = time.monotonic() - started

            if not success:
                logger.error(
                    "TmosEngine.apply: declaration failed for tenant '%s' on %s: %s",
                    tenant,
                    ctx.tmos_host,
                    message,
                )
                return OperationResult(
                    success=False,
                    duration_seconds=duration,
                    error_message=f"AS3 declaration failed: {message}",
                )

            if on_output:
                on_output(f"[tmos] Apply complete in {duration:.1f}s: {message}")

            return OperationResult(
                success=True,
                stdout=f"AS3 tenant '{tenant}' applied: {message}",
                duration_seconds=duration,
            )
        except Exception as exc:
            logger.exception("TmosEngine.apply failed for module %s", ctx.module_id)
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"TMOS apply failed: {exc}",
            )

    # ------------------------------------------------------------------
    # destroy — tenant-scoped DELETE
    # ------------------------------------------------------------------

    def destroy(self, ctx: ModuleContext, on_output: Callable[[str], None] | None = None) -> OperationResult:
        """DELETE the AS3 tenant scoped to ctx.as3_tenant.

        FAIL CLOSED: if as3_tenant is empty/None, refuses with success=False
        and NEVER calls bare /declare.  The client adds a second layer of
        refusal via IControlWriteClient.delete_tenant().
        """
        started = time.monotonic()
        try:
            tenant = (ctx.as3_tenant or "").strip()

            # Engine-layer tenant guard (client adds another layer)
            if not tenant:
                msg = "destroy refused: as3_tenant is empty — would delete ALL tenants"
                logger.error("TmosEngine.destroy: %s on %s (module %s)", msg, ctx.tmos_host, ctx.module_id)
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=msg,
                    error_suggestion="Set as3_tenant in the module context to scope this destroy operation",
                )

            if on_output:
                on_output(f"[tmos] Deleting AS3 tenant '{tenant}' on {ctx.tmos_host}")

            client = self._client(ctx)
            # delete_tenant performs its own safety checks (*, /, empty)
            resp = client.delete_tenant(tenant)

            # Async response handling
            if _has_task_ref(resp):
                task_id = resp.get("id") or resp.get("selfLink", "").rsplit("/", 1)[-1]
                if on_output:
                    on_output(f"[tmos] Delete returned task {task_id}; polling...")
                resp = client.poll_task(task_id, on_output=on_output)

            success, message = _task_terminal_result(resp)
            duration = time.monotonic() - started

            if not success:
                logger.error(
                    "TmosEngine.destroy: tenant '%s' deletion failed on %s: %s",
                    tenant,
                    ctx.tmos_host,
                    message,
                )
                return OperationResult(
                    success=False,
                    duration_seconds=duration,
                    error_message=f"AS3 tenant delete failed: {message}",
                )

            if on_output:
                on_output(f"[tmos] Tenant '{tenant}' deleted in {duration:.1f}s")

            return OperationResult(
                success=True,
                stdout=f"AS3 tenant '{tenant}' deleted",
                duration_seconds=duration,
            )
        except Exception as exc:
            logger.exception("TmosEngine.destroy failed for module %s", ctx.module_id)
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=f"TMOS destroy failed: {exc}",
            )

    # ------------------------------------------------------------------
    # get_outputs — read from ProjectModule.outputs via injected db_factory
    # ------------------------------------------------------------------

    def get_outputs(self, ctx: ModuleContext) -> dict[str, Any]:
        """Read outputs from last apply (stored in ProjectModule.outputs).

        SSHEngine precedent — engine has no DB in the hot path; DB access
        is gated on the injected db_session_factory.
        """
        if self._db_factory:
            db = self._db_factory()
            try:
                from models import ProjectModule

                module = db.query(ProjectModule).filter(
                    ProjectModule.id == ctx.module_id,
                ).first()
                return module.outputs or {} if module else {}
            finally:
                db.close()
        return {}

    # ------------------------------------------------------------------
    # health_check
    # ------------------------------------------------------------------

    def health_check(self) -> bool:
        """TMOS engine is always available (iControl is a library, not a service)."""
        return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _has_task_ref(resp: dict) -> bool:
    """Return True when the AS3 response is a 202-style async task reference.

    AS3 returns either:
      - A final declaration result (has ``results`` list, no ``id`` UUID)
      - A task reference (has ``id`` UUID + ``selfLink`` pointing to /task/...)
    """
    self_link = resp.get("selfLink") or ""
    task_id = resp.get("id") or ""
    # Task references have a UUID-shaped id and a selfLink containing /task/
    return bool(task_id) and ("/task/" in self_link or _looks_like_uuid(str(task_id)))


def _looks_like_uuid(value: str) -> bool:
    """Heuristic: a UUID has 5 hyphen-separated groups."""
    parts = value.split("-")
    return len(parts) == 5 and all(p.isalnum() for p in parts)
