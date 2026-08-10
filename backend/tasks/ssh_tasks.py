"""SSH Engine Celery tasks for bare-metal deployment steps.

Follows the same pattern as ansible_tasks.py / kubernetes_tasks.py:
  - Load ProjectModule + BareMetalHost from DB
  - Build ModuleContext with SSH credentials (never serialized/logged)
  - Call SSHEngine methods (init/apply/destroy)
  - Update task/module status in DB
  - Publish completion events for WebSocket streaming

Extended time limits: connectivity-risk operations (BFB flash, NIC mode
change) can take 10-15 minutes. Celery soft_time_limit=1800 / time_limit=3600.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from celery_app import celery_app
from database import SessionLocal, get_db_context
from models import ProjectModule
from models import Task as TaskModel
from models.bare_metal import BareMetalHost
from services.execution.engine_interface import ModuleContext
from services.execution.ssh_engine import SSHEngine
from services.execution.variable_assembler import build_variables_for_ssh
from services.execution.variable_assembler import can_execute as check_dependencies
from services.module_lock import (
    ModuleLockError,
    ModuleLockLostError,
    module_lock,
    set_locked_module_fields,
)
from services.project_service import update_project_counts
from services.ssh.paramiko_utils import decrypt_ssh_credential
from tasks._task_lookup import fetch_task_or_raise
from tasks._tofu_helpers import (
    CallbackTask,
    _mark_task_failed,
    _notify_task_started,
    _publish_task_completion,
    _trigger_next_destroy_module,
    _update_stack_status_if_needed,
    create_deployment_record,
)

logger = logging.getLogger(__name__)


# ── Context builder ──────────────────────────────────────────────────

def _resolve_jumphost_chain(db, chain_spec: list[dict]) -> list[dict]:
    """Resolve a jumphost chain spec into connection-ready dicts.

    The BareMetalHost.jumphost_chain stores SSHCredential IDs:
        [{"ssh_credential_id": 1}, {"ssh_credential_id": 2}]

    We decrypt each credential into host/username/password/key dicts.
    """
    from models.ssh_credential import SSHCredential

    resolved: list[dict] = []
    for hop in chain_spec:
        cred_id = hop.get("ssh_credential_id")
        if not cred_id:
            continue
        cred = db.query(SSHCredential).filter(SSHCredential.id == cred_id).first()
        if not cred:
            logger.warning("Jumphost credential %s not found — skipping hop", cred_id)
            continue
        decrypted = decrypt_ssh_credential(cred)
        resolved.append({
            "host": decrypted["host"],
            "port": decrypted["port"],
            "username": decrypted["username"],
            "password": decrypted.get("password"),
            "private_key_content": decrypted.get("private_key_content"),
            "passphrase": decrypted.get("key_passphrase"),
        })
    return resolved


def _build_ssh_context(db, module: ProjectModule, operation: str = "apply") -> ModuleContext:
    """Build ModuleContext with SSH fields from BareMetalHost.

    Resolves host credentials, DPU credentials, and jumphost chain
    into the ModuleContext. Credentials live only in memory for the
    duration of task execution — never serialized to logs, DB, or UI.

    ``operation`` selects the variable-assembly mode. Destroy must pass
    ``"destroy"`` so the assembler's destroy-lenient branches apply — otherwise
    a required dependency output that was already torn down raises before the
    engine runs, blocking teardown and orphaning the release.
    """
    # Find the bare-metal host via variable overrides
    host_id = (module.variable_overrides or {}).get("bare_metal_host_id")
    if not host_id:
        # Fall back to variables
        host_id = (module.variables or {}).get("bare_metal_host_id")
    if not host_id:
        raise ValueError(
            f"Module {module.id} ({module.library_module.path if module.library_module else '?'}) "
            "has no bare_metal_host_id in variables"
        )

    host = db.query(BareMetalHost).filter(BareMetalHost.id == int(host_id)).first()
    if not host:
        raise ValueError(f"BareMetalHost {host_id} not found")

    # Decrypt host credential
    if not host.ssh_credential:
        raise ValueError(f"BareMetalHost {host.id} ({host.name}) has no SSH credential")
    host_cred = decrypt_ssh_credential(host.ssh_credential)

    # Decrypt DPU credential if present
    dpu_cred: dict[str, Any] | None = None
    if host.dpu_credential:
        dpu_cred = decrypt_ssh_credential(host.dpu_credential)

    # Resolve jumphost chain
    jumphost_chain: list[dict] | None = None
    if host.jumphost_chain:
        jumphost_chain = _resolve_jumphost_chain(db, host.jumphost_chain)
    elif host.project and host.project.ssh_credential_id:
        # Fall back to project-level jumphost (single-hop)
        from core.encryption import decrypt_value
        from models.ssh_credential import SSHCredential

        project_cred = db.query(SSHCredential).filter(
            SSHCredential.id == host.project.ssh_credential_id
        ).first()

        if project_cred:
            # Decrypt credential fields
            decrypted_password = None
            decrypted_key = None
            decrypted_passphrase = None

            if project_cred.password_encrypted:
                decrypted_password = decrypt_value(project_cred.password_encrypted)
            if project_cred.private_key_encrypted:
                decrypted_key = decrypt_value(project_cred.private_key_encrypted)
            if project_cred.key_passphrase_encrypted:
                decrypted_passphrase = decrypt_value(project_cred.key_passphrase_encrypted)

            jumphost_chain = [{
                "host": project_cred.host,
                "port": project_cred.port or 22,
                "username": project_cred.username,
                "password": decrypted_password,
                "private_key_content": decrypted_key,
                "passphrase": decrypted_passphrase,
            }]

    # Prepare project secrets (FAR pull secret, JWT) for Layer 6 injection.
    # Mirrors the K8s task path (kubernetes_tasks._build_variables): SSH BNK-layer
    # modules (bnk-prerequisites, bnk-flo) declare required inputs cne_pull_secret /
    # jwt_token that resolve from project secrets, not host columns.
    from services.secrets_service import SecretsService

    secret_variables: dict[str, str] = {}
    try:
        module_path = module.library_module.path if module.library_module else module.path_in_project
        value_secrets, _ = SecretsService(db).prepare_secrets_for_execution(
            module.project_id, "/tmp", module_path
        )
        secret_variables = value_secrets
    except Exception as e:
        logger.warning("Failed to prepare secrets for SSH module %s: %s", module.id, e)

    # Build variables with dependency wiring + host-source injection
    variables = build_variables_for_ssh(
        db, module, host=host, secret_variables=secret_variables, operation=operation
    )

    # Resolve module target from the module definition
    module_def = _get_module_def_for_target(module)
    target = getattr(module_def, "target", "host") if module_def else "host"
    variables["target"] = target

    module_path = module.library_module.path if module.library_module else module.path_in_project
    category = module.library_module.category if module.library_module else "bare-metal"

    # Resolve DPU management IP.
    # Precedence: BareMetalHost.dpu_mgmt_ip (persisted by wait-dpu-ready —
    # carries the OOB IP for dual_dpu_obmc and the tmfifo IP otherwise) →
    # first entry in dpu_info[*].mgmt_ip (legacy discovery JSON) →
    # 192.168.100.2 (regular-topology tmfifo default).
    dpu_host: str | None = None
    if host.dpu_mgmt_ip:
        dpu_host = host.dpu_mgmt_ip
    elif host.dpu_info and isinstance(host.dpu_info, list) and host.dpu_info:
        dpu_host = host.dpu_info[0].get("mgmt_ip", "192.168.100.2")

    return ModuleContext(
        module_id=module.id,
        project_id=module.project_id,
        path=module_path,
        category=category,
        variables=variables,
        # SSH fields
        ssh_host=host.host_ip,
        ssh_port=host.ssh_port or 22,
        ssh_username=host_cred["username"],
        ssh_password=host_cred.get("password"),
        ssh_private_key_content=host_cred.get("private_key_content"),
        ssh_key_passphrase=host_cred.get("key_passphrase"),
        jumphost_chain=jumphost_chain,
        # DPU relay
        dpu_host=dpu_host or "192.168.100.2",
        dpu_username=dpu_cred["username"] if dpu_cred else None,
        dpu_password=dpu_cred.get("password") if dpu_cred else None,
        dpu_private_key_content=dpu_cred.get("private_key_content") if dpu_cred else None,
    )


def _inject_host_variables(host: BareMetalHost, module: ProjectModule, variables: dict) -> None:
    """Inject BareMetalHost discovery columns into variables for source='host' inputs."""
    # Map of host column names → variable dict keys
    host_field_map = {
        "host_ip": host.host_ip,
        "mst_device": host.mst_device,
        "nic_mode": host.nic_mode,
        "rshim_present": host.rshim_present,
        "default_route_iface": host.default_route_iface,
        "topology": host.topology,
        "vf_count": host.vf_count,
        "bare_metal_host_id": host.id,
    }

    # Only inject values that aren't already set (user overrides take precedence)
    for key, value in host_field_map.items():
        if value is not None and key not in variables:
            variables[key] = value


def _get_module_def_for_target(module: ProjectModule):
    """Look up SSHModule definition to get target."""
    try:
        from services.registry import ServiceRegistry

        registry = ServiceRegistry.get().module_registry
        path = module.library_module.path if module.library_module else module.path_in_project
        return registry.get(path)
    except Exception:
        return None


# ── Engine factory ───────────────────────────────────────────────────

def _engine() -> SSHEngine:
    return SSHEngine(SessionLocal)


# ── Cluster auto-registration ────────────────────────────────────────

def _try_auto_register_cluster(db, module: ProjectModule, outputs: dict, lock=None) -> None:
    """Attempt cluster auto-registration from SSH module outputs.

    Called after successful apply of any SSH module. Only kubeadm-init
    produces the required contract outputs (cluster_name, remote_kubeconfig_path,
    remote_host), so this is a no-op for other modules.

    `lock` (optional ModuleLock) — if provided, the in-place outputs update
    that injects ssh_credential_id will go through the fence-protected
    set_locked_module_fields helper. Callers running under module_lock
    must pass it.

    Also injects ssh_credential_id from the BareMetalHost so the
    registration service can SSH to fetch kubeconfig using the host's
    own credential (not the project-level one).
    """
    try:
        from services.cluster_auto_registration_service import (
            matches_cluster_output_contract,
            maybe_auto_register_cluster,
        )

        if not matches_cluster_output_contract(outputs):
            return

        # Inject ssh_credential_id from BareMetalHost if not already in outputs
        if "ssh_credential_id" not in outputs:
            host_id = (module.variable_overrides or {}).get("bare_metal_host_id")
            if not host_id:
                host_id = (module.variables or {}).get("bare_metal_host_id")
            if host_id:
                host = db.query(BareMetalHost).filter(BareMetalHost.id == int(host_id)).first()
                if host and host.ssh_credential_id:
                    outputs["ssh_credential_id"] = host.ssh_credential_id
                    # Update stored outputs with the credential ID
                    if lock is not None:
                        set_locked_module_fields(db, module, lock, outputs=outputs)
                    else:
                        module.outputs = outputs
                        db.commit()

        reg_result = maybe_auto_register_cluster(db, module, outputs)
        if reg_result and reg_result.get("success"):
            logger.info(
                "Auto-registered cluster '%s' (id=%s) from SSH module %s",
                reg_result.get("cluster_name"),
                reg_result.get("cluster_id"),
                module.id,
            )
            # Commit the new cluster row before enqueueing — the celery worker
            # opens a fresh DB session and would otherwise race the flush.
            db.commit()
            from tasks.cluster_scan_task import enqueue_cluster_scan
            enqueue_cluster_scan(reg_result["cluster_id"])

            # Link BareMetalHost to the new cluster
            host_id = (module.variable_overrides or {}).get("bare_metal_host_id")
            if not host_id:
                host_id = (module.variables or {}).get("bare_metal_host_id")
            if host_id:
                host = db.query(BareMetalHost).filter(BareMetalHost.id == int(host_id)).first()
                if host:
                    host.kubernetes_cluster_id = reg_result["cluster_id"]
                    db.commit()
                    logger.info(
                        "Linked BareMetalHost %s to cluster %s",
                        host.id, reg_result["cluster_id"],
                    )
        elif reg_result:
            logger.warning(
                "Cluster auto-registration failed for module %s: %s",
                module.id, reg_result.get("error"),
            )
    except Exception as e:
        # Auto-registration failure should not fail the apply task
        logger.error("Cluster auto-registration error (non-fatal): %s", e)


# ── Log formatting ───────────────────────────────────────────────────

def _ts() -> str:
    """Return a compact UTC timestamp for log prefixing."""
    return datetime.now(UTC).strftime("%H:%M:%S")


def _format_logs(
    header: str,
    output_lines: list[str],
    error_message: str | None = None,
    suggestion: str | None = None,
) -> str:
    logs = [header, "---"]
    logs.extend(output_lines)
    if error_message:
        logs.extend(["", "--- ERROR ---", error_message])
    if suggestion:
        logs.extend(["", "--- SUGGESTION ---", suggestion])
    return "\n".join(logs) + "\n"


# ── Celery tasks ─────────────────────────────────────────────────────

@celery_app.task(bind=True, base=CallbackTask, name="tasks.ssh_tasks.run_ssh_init")
def run_ssh_init(self, task_db_id: int, module_id: int, auto_apply: bool = False, **kwargs):
    """Initialize SSH module — validate connectivity and prerequisites."""
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "ssh-engine: init"
            db.commit()
            _notify_task_started(task)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                raise ValueError(f"Module {module_id} not found")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_ssh_context(db, module)
                output_lines: list[str] = []

                def _on_init_output(line: str) -> None:
                    stamped = f"[{_ts()}] {line}"
                    output_lines.append(stamped)
                    try:
                        from services.websocket_service import publish_module_log
                        publish_module_log(module_id=module.id, line=stamped)
                    except Exception:
                        pass  # WebSocket publish is best-effort

                result = _engine().init(ctx, on_output=_on_init_output)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== SSH ENGINE INIT ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    module_fields = {"status": "initialized", "last_init_at": datetime.now(UTC)}
                else:
                    task.status = "failed"
                    task.error = result.error_message or "SSH init failed"
                    module_fields = {"status": "init_failed", "deployment_error": task.error}

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "init", task.logs)

                if module.stack_instance_id:
                    _update_stack_status_if_needed(module, db)

                if result.success and auto_apply:
                    can_exec, missing = check_dependencies(db, module)
                    if can_exec:
                        apply_task = TaskModel(
                            task_type="apply",
                            status="queued",
                            project_id=module.project_id,
                            module_id=module.id,
                            created_at=datetime.now(UTC),
                        )
                        db.add(apply_task)
                        db.commit()
                        db.refresh(apply_task)

                        from services.execution.task_dispatch import dispatch_apply

                        celery_result = dispatch_apply(apply_task.id, module)
                        apply_task.celery_task_id = celery_result.id
                        set_locked_module_fields(db, module, lock, status="applying")
                    else:
                        logger.info("SSH auto-apply skipped for module %s: %s", module.id, missing)

            return {"success": result.success, "exit_code": task.exit_code}

        except ModuleLockError as e:
            logger.error("SSH init task failed - module locked: %s", e)
            module = locals().get("module")
            _mark_task_failed(task, f"Module locked: {e}", db, update_stack=False)
            # Let BUG-009 janitor recover the stale module via stack progress check
            if module and module.stack_instance_id:
                _update_stack_status_if_needed(module, db)
            raise

        except ModuleLockLostError as e:
            logger.warning("SSH init aborted - lock lost mid-operation: %s", e)
            module = locals().get("module")
            _mark_task_failed(task, f"Lock lost: {e}", db, update_stack=False)
            if module and module.stack_instance_id:
                _update_stack_status_if_needed(module, db)
            raise

        except Exception as exc:
            logger.exception("SSH init task failed: %s", exc)
            module = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=module, failed_status="init_failed")
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.ssh_tasks.run_ssh_apply",
    soft_time_limit=1800,
    time_limit=3600,
)
def run_ssh_apply(self, task_db_id: int, module_id: int, **kwargs):
    """Apply SSH module — execute bare-metal step.

    Extended time limits for connectivity-risk operations (BFB flash, etc.).
    """
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
                _publish_task_completion(task)
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "ssh-engine: apply"
            db.commit()
            _notify_task_started(task)

            can_exec, missing = check_dependencies(db, module)
            if not can_exec:
                raise ValueError(f"Dependencies not satisfied: {', '.join(missing)}")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_ssh_context(db, module)
                output_lines: list[str] = []

                def _on_apply_output(line: str) -> None:
                    stamped = f"[{_ts()}] {line}"
                    output_lines.append(stamped)
                    try:
                        from services.websocket_service import publish_module_log
                        publish_module_log(module_id=module.id, line=stamped)
                    except Exception:
                        pass  # WebSocket publish is best-effort

                result = _engine().apply(ctx, on_output=_on_apply_output)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== SSH ENGINE APPLY ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    # NOTE: Do NOT call normalize_module_outputs_in_place here.
                    # SSH engine modules use BareMetalHost credentials, not
                    # infrastructure SSH keys. The normalizer would inject a false
                    # "infrastructure_access_status: recovery_required" into outputs.
                    module_fields = {
                        "status": "applied",
                        "outputs": result.outputs or {},
                        "last_deployed_at": datetime.now(UTC),
                        "deployment_error": None,
                    }
                else:
                    task.status = "failed"
                    task.error = result.error_message or "SSH apply failed"
                    module_fields = {
                        "status": "apply_failed",
                        "deployment_error": task.error[-2000:],
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "apply", task.logs)
                _update_stack_status_if_needed(module, db)

                # Auto-register cluster if module outputs match the contract
                # (e.g., kubeadm-init produces cluster_name + remote_kubeconfig_path + remote_host)
                if result.success and result.outputs:
                    _try_auto_register_cluster(db, module, result.outputs, lock=lock)

            return {
                "success": result.success,
                "exit_code": task.exit_code,
                "outputs": result.outputs or {},
            }

        except ModuleLockError as e:
            logger.error("SSH apply task failed - module locked: %s", e)
            _mark_task_failed(task, f"Module locked: {e}", db, update_stack=False)
            # Let BUG-009 janitor recover the stale module via stack progress check
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except ModuleLockLostError as e:
            logger.warning("SSH apply aborted - lock lost mid-operation: %s", e)
            _mark_task_failed(task, f"Lock lost: {e}", db, update_stack=False)
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except Exception as exc:
            logger.exception("SSH apply task failed: %s", exc)
            mod = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=mod, failed_status="apply_failed")
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.ssh_tasks.run_ssh_destroy")
def run_ssh_destroy(self, task_db_id: int, module_id: int, **kwargs):
    """Destroy SSH module — no-op for hardware modules."""
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                task.status = "failed"
                task.error = f"Module {module_id} not found"
                task.completed_at = datetime.now(UTC)
                if task.started_at:
                    task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                db.commit()
                _publish_task_completion(task)
                return {"success": False, "error": task.error}

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "ssh-engine: destroy"
            db.commit()
            _notify_task_started(task)

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_ssh_context(db, module, operation="destroy")
                output_lines: list[str] = []

                def _on_destroy_output(line: str) -> None:
                    output_lines.append(f"[{_ts()}] {line}")

                result = _engine().destroy(ctx, on_output=_on_destroy_output)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== SSH ENGINE DESTROY ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    module_fields = {
                        "status": "destroyed",
                        "outputs": None,
                        "last_deployed_at": None,
                        "last_init_at": None,
                        "plan_output": None,
                        "plan_serial": 0,
                        "vars_hash": None,
                        "deployment_error": None,
                    }
                else:
                    task.status = "failed"
                    task.error = result.error_message or "SSH destroy failed"
                    module_fields = {
                        "status": "destroy_failed",
                        "deployment_error": task.error[-2000:],
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()
                create_deployment_record(db, task, module, "destroy", task.logs)
                _update_stack_status_if_needed(module, db)
                # D-001 Phase 3: event-chain destroy trigger hook
                _trigger_next_destroy_module(module, db)

            return {"success": result.success, "exit_code": task.exit_code}

        except ModuleLockError as e:
            logger.error("SSH destroy task failed - module locked: %s", e)
            # C1: set destroy_failed and fire trigger so terminal detection runs.
            _exc_module = locals().get("module")
            _mark_task_failed(task, f"Module locked: {e}", db, update_stack=False)
            if _exc_module:
                _exc_module.status = "destroy_failed"
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            try:
                if _exc_module:
                    db.refresh(_exc_module)
                    _trigger_next_destroy_module(_exc_module, db)
            except Exception as trigger_err:
                logger.warning("_trigger_next_destroy_module failed after SSH ModuleLockError: %s", trigger_err)
            raise

        except ModuleLockLostError as e:
            logger.warning("SSH destroy aborted - lock lost mid-operation: %s", e)
            # C1: set destroy_failed and fire trigger so terminal detection runs.
            _exc_module = locals().get("module")
            _mark_task_failed(task, f"Lock lost: {e}", db, update_stack=False)
            if _exc_module:
                _exc_module.status = "destroy_failed"
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            try:
                if _exc_module:
                    db.refresh(_exc_module)
                    _trigger_next_destroy_module(_exc_module, db)
            except Exception as trigger_err:
                logger.warning("_trigger_next_destroy_module failed after SSH ModuleLockLostError: %s", trigger_err)
            raise

        except Exception as exc:
            logger.exception("SSH destroy task failed: %s", exc)
            _exc_module = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=_exc_module, failed_status="destroy_failed")
            # C1: fire event-chain trigger so terminal detection runs even on exception
            try:
                if _exc_module is not None:
                    db.refresh(_exc_module)
                    _trigger_next_destroy_module(_exc_module, db)
            except Exception as trigger_err:
                logger.warning("_trigger_next_destroy_module failed after SSH generic exception: %s", trigger_err)
            raise
