"""Ansible DeploymentEngine adapter for manifest-driven deployment packs."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

from services.execution.ansible_runner import (
    AnsibleManifestContractError,
    AnsibleRunner,
)
from services.execution.engine_interface import (
    DeploymentEngine,
    ModuleContext,
    OperationResult,
    PlanResult,
)

logger = logging.getLogger(__name__)


class AnsibleEngine(DeploymentEngine):
    """Governed ansible engine using manifest-declared playbook entrypoints only."""

    def __init__(self, db_session_factory, runner: AnsibleRunner | None = None):
        self._db_factory = db_session_factory
        self._runner = runner or AnsibleRunner()

    def health_check(self) -> bool:
        return self._runner.health_check()

    def init(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        db = self._db_factory()
        started = time.monotonic()
        try:
            module = self._load_module(db, ctx.module_id)
            pack_cfg, work_dir, apply_playbook = self._prepare_execution_paths(module, operation="init")
            if on_output:
                on_output("[ansible] validating manifest and playbook syntax")
            result = self._runner.run_syntax_check(
                work_dir=work_dir,
                playbook_path=apply_playbook,
                variables=ctx.variables,
            )
            if on_output:
                self._emit_output_lines(on_output, result.stdout, result.stderr)

            if result.returncode == 0:
                return OperationResult(
                    success=True,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    duration_seconds=time.monotonic() - started,
                )

            return OperationResult(
                success=False,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=time.monotonic() - started,
                error_message="Ansible syntax validation failed",
            )
        except Exception as exc:
            logger.exception("AnsibleEngine.init failed for module %s", ctx.module_id)
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=str(exc),
            )
        finally:
            db.close()

    def plan(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> PlanResult:
        db = self._db_factory()
        try:
            module = self._load_module(db, ctx.module_id)
            _pack_cfg, work_dir, apply_playbook = self._prepare_execution_paths(module, operation="plan")
            if on_output:
                on_output("[ansible] running check-mode preview")

            result = self._runner.run_check_mode(
                work_dir=work_dir,
                playbook_path=apply_playbook,
                variables=ctx.variables,
            )
            if on_output:
                self._emit_output_lines(on_output, result.stdout, result.stderr)

            if result.returncode == 0:
                return PlanResult(
                    has_changes=True,
                    details=(
                        "Ansible check-mode completed. This preview is best-effort and "
                        "does not provide OpenTofu-style exact change counts."
                    ),
                )

            return PlanResult(
                has_changes=False,
                details=f"Ansible check-mode failed.\n{result.stderr or result.stdout}",
            )
        except Exception as exc:
            logger.exception("AnsibleEngine.plan failed for module %s", ctx.module_id)
            return PlanResult(has_changes=False, details=f"Plan error: {exc}")
        finally:
            db.close()

    def apply(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        db = self._db_factory()
        started = time.monotonic()
        try:
            module = self._load_module(db, ctx.module_id)
            pack_cfg, work_dir, apply_playbook = self._prepare_execution_paths(module, operation="apply")
            if on_output:
                on_output("[ansible] running apply playbook")

            run_result = self._runner.run_apply(
                work_dir=work_dir,
                playbook_path=apply_playbook,
                variables=ctx.variables,
            )
            if on_output:
                self._emit_output_lines(on_output, run_result.stdout, run_result.stderr)

            if run_result.returncode != 0:
                return OperationResult(
                    success=False,
                    stdout=run_result.stdout,
                    stderr=run_result.stderr,
                    duration_seconds=time.monotonic() - started,
                    error_message="Ansible apply failed",
                )

            outputs_required = self._outputs_artifact_required(module)
            outputs: dict[str, Any] = {}
            if pack_cfg.outputs_file:
                outputs = self._runner.read_outputs_artifact(
                    work_dir=work_dir,
                    outputs_file=pack_cfg.outputs_file,
                    required=outputs_required,
                )
            elif outputs_required:
                return OperationResult(
                    success=False,
                    stdout=run_result.stdout,
                    stderr=run_result.stderr,
                    duration_seconds=time.monotonic() - started,
                    error_message="Manifest requires outputs artifact but deployment_pack.entrypoints.outputs_file is missing",
                )

            return OperationResult(
                success=True,
                outputs=outputs,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                duration_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            logger.exception("AnsibleEngine.apply failed for module %s", ctx.module_id)
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=str(exc),
            )
        finally:
            db.close()

    def destroy(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        db = self._db_factory()
        started = time.monotonic()
        try:
            module = self._load_module(db, ctx.module_id)
            pack_cfg, work_dir, _apply_playbook = self._prepare_execution_paths(module, operation="destroy")

            if not pack_cfg.supports_destroy:
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message="Destroy is not supported for this ansible pack (lifecycle.supports_destroy=false)",
                )

            if not pack_cfg.destroy_playbook:
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message="Destroy is declared but deployment_pack.entrypoints.destroy_playbook is missing",
                )

            destroy_playbook = self._runner.resolve_workspace_subpath(work_dir, pack_cfg.destroy_playbook)
            if not os.path.exists(destroy_playbook):
                return OperationResult(
                    success=False,
                    duration_seconds=time.monotonic() - started,
                    error_message=f"Destroy playbook not found: {pack_cfg.destroy_playbook}",
                )

            if on_output:
                on_output("[ansible] running destroy playbook")

            run_result = self._runner.run_apply(
                work_dir=work_dir,
                playbook_path=destroy_playbook,
                variables=ctx.variables,
            )
            if on_output:
                self._emit_output_lines(on_output, run_result.stdout, run_result.stderr)

            if run_result.returncode != 0:
                return OperationResult(
                    success=False,
                    stdout=run_result.stdout,
                    stderr=run_result.stderr,
                    duration_seconds=time.monotonic() - started,
                    error_message="Ansible destroy failed",
                )

            return OperationResult(
                success=True,
                stdout=run_result.stdout,
                stderr=run_result.stderr,
                duration_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            logger.exception("AnsibleEngine.destroy failed for module %s", ctx.module_id)
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - started,
                error_message=str(exc),
            )
        finally:
            db.close()

    def get_outputs(self, ctx: ModuleContext) -> dict[str, Any]:
        db = self._db_factory()
        outputs_required = False
        try:
            module = self._load_module(db, ctx.module_id)
            pack_cfg, work_dir, _apply_playbook = self._prepare_execution_paths(module, operation="get_outputs")
            outputs_required = self._outputs_artifact_required(module)

            if not pack_cfg.outputs_file:
                if outputs_required:
                    raise ValueError(
                        "Manifest defines outputs.key_outputs but deployment_pack.entrypoints.outputs_file is missing"
                    )
                return {}

            return self._runner.read_outputs_artifact(
                work_dir=work_dir,
                outputs_file=pack_cfg.outputs_file,
                required=outputs_required,
            )
        except Exception:
            if outputs_required:
                raise
            logger.exception("AnsibleEngine.get_outputs failed for module %s", ctx.module_id)
            return {}
        finally:
            db.close()

    def _load_module(self, db, module_id: int):
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

    def _prepare_execution_paths(self, module, *, operation: str) -> tuple[Any, str, str]:
        pack_manifest = self._require_pack_manifest(module)
        pack_cfg = self._runner.parse_pack_config(pack_manifest)
        work_dir = self._prepare_workspace(module, operation=operation)

        exec_dir = self._runner.resolve_workspace_subpath(work_dir, pack_cfg.working_directory)
        if not os.path.isdir(exec_dir):
            raise AnsibleManifestContractError(
                f"deployment_pack.working_directory does not exist in workspace: {pack_cfg.working_directory}"
            )

        apply_playbook = self._runner.resolve_workspace_subpath(exec_dir, pack_cfg.playbook)
        if not os.path.exists(apply_playbook):
            raise FileNotFoundError(f"Apply playbook not found: {pack_cfg.playbook}")

        return pack_cfg, exec_dir, apply_playbook

    def _prepare_workspace(self, module, *, operation: str) -> str:
        from services.execution.opentofu_runtime import OpenTofuRuntime

        runtime = OpenTofuRuntime(self._db_factory())
        try:
            return runtime.prepare_persistent_workspace(module, operation=operation)
        finally:
            runtime.db.close()

    def _require_pack_manifest(self, module) -> dict[str, Any]:
        lib_module = getattr(module, "library_module", None)
        if lib_module is None:
            raise ValueError(f"ProjectModule {module.id} has no library module")

        pack_manifest = getattr(lib_module, "pack_manifest", None)
        if not isinstance(pack_manifest, dict):
            raise ValueError("Ansible engine requires module library pack_manifest metadata")

        return pack_manifest

    def _outputs_artifact_required(self, module) -> bool:
        pack_manifest = self._require_pack_manifest(module)
        outputs = pack_manifest.get("outputs")
        if not isinstance(outputs, dict):
            return False
        key_outputs = outputs.get("key_outputs")
        return isinstance(key_outputs, list) and len(key_outputs) > 0

    def _emit_output_lines(self, on_output: Callable[[str], None], stdout: str, stderr: str) -> None:
        for line in (stdout or "").splitlines():
            on_output(line)
        for line in (stderr or "").splitlines():
            on_output(f"[stderr] {line}")
