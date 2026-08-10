"""
OpenTofu Engine — wraps OpenTofuRuntime as a DeploymentEngine.

This is a thin adapter. The actual logic stays in opentofu_runtime.py (~990 lines
of battle-tested code). This wrapper translates between the new engine interface
and the existing implementation.

Phase 1a of execution engine unification (P0):
  - Each method creates a DB session, loads the module, delegates to OpenTofuRuntime
  - Returns OperationResult/PlanResult so the engine interface contract is fulfilled
  - The Celery tasks call through this adapter for high-level operations

OpenTofuRuntime is tightly coupled to the DB (takes Session, loads
ProjectModule, etc.). This adapter bridges that coupling — it owns the DB session
lifecycle and translates ModuleContext ↔ ProjectModule.
"""

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from core.encryption import decrypt_value
from services.execution.engine_interface import (
    DeploymentEngine,
    ModuleContext,
    OperationResult,
    PlanResult,
)

logger = logging.getLogger(__name__)


class OpenTofuEngine(DeploymentEngine):
    """
    Wraps OpenTofuRuntime for the DeploymentEngine interface.

    Usage:
        from database import SessionLocal
        engine = OpenTofuEngine(SessionLocal)
        result = engine.apply(ctx, on_output=some_callback)

    The adapter:
      - Creates a fresh DB session per operation (short-lived, closed in finally)
      - Loads ProjectModule from ctx.module_id
      - Delegates to OpenTofuRuntime methods
      - Translates (exit_code, logs) tuples → OperationResult/PlanResult
    """

    def __init__(self, db_session_factory):
        """
        Args:
            db_session_factory: Callable that returns a new DB session.
                               Typically `database.SessionLocal`.
        """
        self._db_factory = db_session_factory

    def health_check(self) -> bool:
        """Check that the `tofu` binary is available and responds."""
        import subprocess
        try:
            result = subprocess.run(
                ["tofu", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _load_module(self, db, module_id: int):
        """Load ProjectModule with relationships needed by OpenTofuRuntime."""
        from sqlalchemy.orm import joinedload

        from models import ProjectModule

        module = (
            db.query(ProjectModule)
            .options(
                joinedload(ProjectModule.library_module),
                joinedload(ProjectModule.project),
            )
            .filter(ProjectModule.id == module_id)
            .first()
        )
        if not module:
            raise ValueError(f"ProjectModule {module_id} not found")
        return module

    def _create_engine(self, db):
        """Create an OpenTofuRuntime instance with the given session."""
        from services.execution.opentofu_runtime import OpenTofuRuntime
        return OpenTofuRuntime(db)

    def _get_credentials_env(self, project, db) -> dict[str, str]:
        """Get cloud credentials environment variables for subprocess calls."""
        from services.credentials_service import get_cloud_credentials_env
        return get_cloud_credentials_env(project, db)

    def _get_global_tfvar_env(self, project, db) -> dict[str, str]:
        """Return TF_VAR_jwt_token env entry if the project has an active jwt_token secret.

        jwt_token (BNK LICENSE) is injected as an env var so that modules which
        don't declare ``variable "jwt_token" {}`` receive the value without
        triggering the "Value for undeclared variable" warning from tofu.

        Returns {} if no active jwt_token secret exists or its decrypted value is
        empty — tofu treats an empty TF_VAR_* differently from "not set", so we
        only inject when there is a real value.

        # Future: if a second globally-broadcast secret appears, consider a
        # registry — see followup_jwt_token_undeclared_variable_warning.md
        """
        from models import ProjectSecret

        try:
            secret = (
                db.query(ProjectSecret)
                .filter(
                    ProjectSecret.project_id == project.id,
                    ProjectSecret.name == "jwt_token",
                    ProjectSecret.is_active.is_(True),
                )
                .first()
            )
            if secret is None or secret.secret_type != "value" or not secret.value_encrypted:
                return {}
            decrypted = decrypt_value(secret.value_encrypted)
            if not decrypted:
                return {}
            value = decrypted.strip()
            if not value:
                return {}
            return {"TF_VAR_jwt_token": value}
        except Exception:
            logger.exception("Failed to retrieve jwt_token secret for project %s", project.id)
            return {}

    def init(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """
        Initialize the module workspace.

        Delegates to:
          OpenTofuRuntime.prepare_persistent_workspace(module)
          OpenTofuRuntime.run_init(work_dir, env)
        """
        db = self._db_factory()
        start = time.time()
        try:
            module = self._load_module(db, ctx.module_id)
            engine = self._create_engine(db)
            env = ctx.credentials_env or self._get_credentials_env(module.project, db)
            env = {**env, **self._get_global_tfvar_env(module.project, db)}

            # Prepare workspace (clones source, writes configs)
            work_dir = engine.prepare_persistent_workspace(module)

            if on_output:
                on_output(f"Workspace prepared: {work_dir}")

            # Run init
            exit_code, logs = engine.run_init(work_dir, env)

            if on_output:
                for line in logs.splitlines():
                    on_output(line)

            duration = time.time() - start
            if exit_code == 0:
                return OperationResult(
                    success=True,
                    stdout=logs,
                    duration_seconds=duration,
                )
            else:
                return OperationResult(
                    success=False,
                    stdout=logs,
                    duration_seconds=duration,
                    error_message="OpenTofu init failed",
                )
        except Exception as e:
            duration = time.time() - start
            logger.exception(f"OpenTofuEngine.init failed for module {ctx.module_id}: {e}")
            return OperationResult(
                success=False,
                error_message=str(e),
                duration_seconds=duration,
            )
        finally:
            db.close()

    def plan(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> PlanResult:
        """
        Preview what would change.

        Delegates to:
          OpenTofuRuntime.prepare_persistent_workspace(module)
          OpenTofuRuntime.run_plan_detailed(work_dir, env)

        Uses -detailed-exitcode so we can populate has_changes accurately:
          exit 0 = no changes, exit 2 = changes detected, exit 1 = error.
        """
        db = self._db_factory()
        try:
            module = self._load_module(db, ctx.module_id)
            engine = self._create_engine(db)
            env = ctx.credentials_env or self._get_credentials_env(module.project, db)
            env = {**env, **self._get_global_tfvar_env(module.project, db)}

            work_dir = engine.prepare_persistent_workspace(module)

            if on_output:
                on_output(f"Workspace prepared: {work_dir}")

            exit_code, logs = engine.run_plan_detailed(work_dir, env)

            if on_output:
                for line in logs.splitlines():
                    on_output(line)

            # Parse resource counts from plan output
            adds, changes, destroys = _parse_plan_summary(logs)

            if exit_code == 0:
                # No changes
                return PlanResult(
                    has_changes=False,
                    adds=0,
                    changes=0,
                    destroys=0,
                    details=logs,
                )
            elif exit_code == 2:
                # Changes detected
                return PlanResult(
                    has_changes=True,
                    adds=adds,
                    changes=changes,
                    destroys=destroys,
                    details=logs,
                    plan_id=f"{work_dir}/plan.out",
                )
            else:
                # Error
                return PlanResult(
                    has_changes=False,
                    details=f"Plan failed (exit code {exit_code}):\n{logs}",
                )
        except Exception as e:
            logger.exception(f"OpenTofuEngine.plan failed for module {ctx.module_id}: {e}")
            return PlanResult(
                has_changes=False,
                details=f"Plan error: {e}",
            )
        finally:
            db.close()

    def apply(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """
        Deploy the module.

        Delegates to:
          OpenTofuRuntime.prepare_persistent_workspace(module)
          OpenTofuRuntime.run_init(work_dir, env)  — if workspace not initialized
          OpenTofuRuntime.run_plan(work_dir, env)   — creates plan.out
          OpenTofuRuntime.run_apply(work_dir, env)  — applies plan.out, captures outputs
        """
        db = self._db_factory()
        start = time.time()
        try:
            module = self._load_module(db, ctx.module_id)
            engine = self._create_engine(db)
            env = ctx.credentials_env or self._get_credentials_env(module.project, db)
            env = {**env, **self._get_global_tfvar_env(module.project, db)}

            from services.workspace_manager import WorkspaceManager
            workspace = WorkspaceManager(db)

            # Prepare workspace
            work_dir = engine.prepare_persistent_workspace(module)

            if on_output:
                on_output(f"Workspace prepared: {work_dir}")

            all_logs = ""

            # Init if needed
            needs_reinit, reinit_reason = workspace.needs_reinit(module)
            if not workspace.is_initialized(module) or needs_reinit:
                if on_output:
                    on_output(f"Running init{f': {reinit_reason}' if reinit_reason else ''}...")
                init_code, init_logs = engine.run_init(work_dir, env)
                all_logs += init_logs + "\n"

                if on_output:
                    for line in init_logs.splitlines():
                        on_output(line)

                if init_code != 0:
                    duration = time.time() - start
                    return OperationResult(
                        success=False,
                        stdout=all_logs,
                        duration_seconds=duration,
                        error_message="OpenTofu init failed",
                    )
                workspace.mark_initialized(module)
                workspace.save_init_version(module)
            else:
                all_logs += "=== INIT SKIPPED (using cached providers) ===\n"
                if on_output:
                    on_output("Init skipped (using cached providers)")

            # Plan
            if on_output:
                on_output("Running plan...")
            plan_code, plan_logs = engine.run_plan(work_dir, env)
            all_logs += "--- PLAN ---\n" + plan_logs + "\n"

            if on_output:
                for line in plan_logs.splitlines():
                    on_output(line)

            if plan_code != 0:
                duration = time.time() - start
                return OperationResult(
                    success=False,
                    stdout=all_logs,
                    duration_seconds=duration,
                    error_message="OpenTofu plan failed",
                )

            # Apply
            if on_output:
                on_output("Running apply...")
            apply_code, apply_logs, outputs = engine.run_apply(work_dir, env, module=module)
            all_logs += "--- APPLY ---\n" + apply_logs

            if on_output:
                for line in apply_logs.splitlines():
                    on_output(line)

            duration = time.time() - start

            # Parse resource counts from apply output
            created, modified, destroyed = _parse_apply_summary(apply_logs)

            if apply_code == 0:
                return OperationResult(
                    success=True,
                    outputs=outputs,
                    stdout=all_logs,
                    resources_created=created,
                    resources_modified=modified,
                    resources_destroyed=destroyed,
                    duration_seconds=duration,
                )
            else:
                return OperationResult(
                    success=False,
                    stdout=all_logs,
                    duration_seconds=duration,
                    error_message="OpenTofu apply failed",
                    error_suggestion=engine.parse_destroy_error(apply_logs, module) if module else None,
                )
        except Exception as e:
            duration = time.time() - start
            logger.exception(f"OpenTofuEngine.apply failed for module {ctx.module_id}: {e}")
            return OperationResult(
                success=False,
                error_message=str(e),
                duration_seconds=duration,
            )
        finally:
            db.close()

    def destroy(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """
        Remove all resources created by this module.

        Delegates to:
          OpenTofuRuntime.prepare_persistent_workspace(module)
          OpenTofuRuntime.run_init(work_dir, env)          — if needed
          OpenTofuRuntime.run_destroy_with_retry(work_dir, env, module)
        """
        db = self._db_factory()
        start = time.time()
        try:
            module = self._load_module(db, ctx.module_id)
            engine = self._create_engine(db)
            env = ctx.credentials_env or self._get_credentials_env(module.project, db)
            env = {**env, **self._get_global_tfvar_env(module.project, db)}

            from services.workspace_manager import WorkspaceManager
            workspace = WorkspaceManager(db)

            work_dir = engine.prepare_persistent_workspace(module)

            if on_output:
                on_output(f"Workspace prepared: {work_dir}")

            all_logs = ""

            # Init if needed
            needs_reinit, reinit_reason = workspace.needs_reinit(module)
            if not workspace.is_initialized(module) or needs_reinit:
                if on_output:
                    on_output(f"Running init{f': {reinit_reason}' if reinit_reason else ''}...")
                init_code, init_logs = engine.run_init(work_dir, env)
                all_logs += init_logs + "\n"

                if on_output:
                    for line in init_logs.splitlines():
                        on_output(line)

                if init_code != 0:
                    duration = time.time() - start
                    return OperationResult(
                        success=False,
                        stdout=all_logs,
                        duration_seconds=duration,
                        error_message="OpenTofu init failed (needed before destroy)",
                    )
                workspace.mark_initialized(module)
                workspace.save_init_version(module)
            else:
                all_logs += "=== INIT SKIPPED (using cached providers) ===\n"
                if on_output:
                    on_output("Init skipped (using cached providers)")

            # Destroy with retry (handles DependencyViolation errors)
            if on_output:
                on_output("Running destroy...")
            timeout = engine.get_destroy_timeout(module)
            destroy_code, destroy_logs = engine.run_destroy_with_retry(
                work_dir, env, module=module, timeout=timeout
            )
            all_logs += "--- DESTROY ---\n" + destroy_logs

            if on_output:
                for line in destroy_logs.splitlines():
                    on_output(line)

            duration = time.time() - start

            # Parse resource counts from destroy output
            _, _, destroyed = _parse_apply_summary(destroy_logs)

            if destroy_code == 0:
                return OperationResult(
                    success=True,
                    stdout=all_logs,
                    resources_destroyed=destroyed,
                    duration_seconds=duration,
                )
            else:
                error_analysis = engine.parse_destroy_error(all_logs, module)
                return OperationResult(
                    success=False,
                    stdout=all_logs,
                    duration_seconds=duration,
                    error_message="OpenTofu destroy failed",
                    error_suggestion=error_analysis if error_analysis else None,
                )
        except Exception as e:
            duration = time.time() - start
            logger.exception(f"OpenTofuEngine.destroy failed for module {ctx.module_id}: {e}")
            return OperationResult(
                success=False,
                error_message=str(e),
                duration_seconds=duration,
            )
        finally:
            db.close()

    def get_outputs(self, ctx: ModuleContext) -> dict[str, Any]:
        """
        Read current outputs from OpenTofu state.

        Delegates to OpenTofuRuntime.capture_outputs(work_dir, env).
        """
        db = self._db_factory()
        try:
            module = self._load_module(db, ctx.module_id)
            engine = self._create_engine(db)
            env = ctx.credentials_env or self._get_credentials_env(module.project, db)

            from services.workspace_manager import WorkspaceManager
            workspace = WorkspaceManager(db)
            work_dir = workspace.get_workspace_path(module)

            if not work_dir:
                logger.warning(f"No workspace found for module {ctx.module_id}")
                return {}

            outputs = engine.capture_outputs(work_dir, env)
            return engine.normalize_outputs(outputs, module)
        except Exception as e:
            logger.exception(f"OpenTofuEngine.get_outputs failed for module {ctx.module_id}: {e}")
            return {}
        finally:
            db.close()

    def check_drift(self, ctx: ModuleContext) -> PlanResult | None:
        """
        Check if actual state differs from desired using -detailed-exitcode.

        Delegates to plan() which already uses run_plan_detailed.
        """
        return self.plan(ctx)


# ---------------------------------------------------------------------------
# Helpers — parse OpenTofu output for resource counts
# ---------------------------------------------------------------------------

def _parse_plan_summary(output: str) -> tuple:
    """
    Parse 'Plan: X to add, Y to change, Z to destroy.' from plan output.
    Returns (adds, changes, destroys).
    """
    match = re.search(
        r"Plan:\s+(\d+)\s+to add,\s+(\d+)\s+to change,\s+(\d+)\s+to destroy",
        output,
    )
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    return 0, 0, 0


def _parse_apply_summary(output: str) -> tuple:
    """
    Parse 'Apply complete! Resources: X added, Y changed, Z destroyed.' from apply/destroy output.
    Returns (added, changed, destroyed).
    """
    match = re.search(
        r"(\d+)\s+added,\s+(\d+)\s+changed,\s+(\d+)\s+destroyed",
        output,
    )
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    return 0, 0, 0
