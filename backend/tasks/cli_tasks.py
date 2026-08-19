"""CLI Engine Celery tasks for *bnkctl-family deployments.

Modeled on tasks/ssh_tasks.py.  Builds ModuleContext with cloud credentials,
renders form variables → cluster.yaml, runs BnkctlEngine, streams output to
WebSocket, and writes the ProjectModule run record.

Key differences from ssh_tasks.py:
  - ModuleContext carries credentials_env (cloud) not SSH fields.
  - No BareMetalHost or SSHCredential resolution.
  - Subprocess runs in the celery worker; cwd = persistent workspace volume.
  - No cluster auto-registration hook (out of scope for tracer bullet).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from celery_app import celery_app
from database import SessionLocal, get_db_context
from models import ProjectModule
from models import Task as TaskModel
from services.credentials_service import get_cloud_credentials_env
from services.execution.cli_engine import _DEFAULT_BINARY, BnkctlEngine
from services.execution.engine_interface import ModuleContext
from services.execution.variable_assembler import can_execute as check_dependencies
from services.module_lock import (
    ModuleLockError,
    ModuleLockLostError,
    module_lock,
    set_locked_module_fields,
)
from services.project_service import update_project_counts
from services.secrets_service import SecretsService
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


# ── Cluster YAML rendering ───────────────────────────────────────────────────

def _render_awsbnkctl_cluster_yaml(
    variables: dict,
    *,
    far_archive_path: str | None = None,
    jwt_path: str | None = None,
) -> str:
    """Render the editable variables into a minimal awsbnkctl cluster.yaml.

    Maps form variables to the bnk-demo topology structure:
      cluster_name → metadata.name
      region → metadata.region
      vpc_cidr → network.vpcCidr
      instance_type → cluster.nodeGroups[0].instanceType
      pattern → top-level pattern (external-only | dual-interface)

    Generates valid YAML for awsbnkctl dry-run validation, including network.dataPath
    for BNK interface binding (required for intent validation). Derives subnets and
    dataPath CIDRs from vpc_cidr using ipaddress.ip_network for safety.

    All user-controlled values are serialized via yaml.safe_dump so that values
    containing special characters (colons, anchors, leading braces, quotes,
    newlines, etc.) cannot inject malformed YAML.

    When BOTH far_archive_path and jwt_path are provided (workspace-relative paths),
    a bnk: block is appended so awsbnkctl up --auto licenses and activates BNK.
    When either is absent the block is omitted (infra-only / dry-run path).
    """
    import ipaddress

    import yaml

    cluster_name = str(variables.get("cluster_name", "bnk-demo") or "bnk-demo")
    region = str(variables.get("region", "ap-southeast-2") or "ap-southeast-2")
    vpc_cidr_str = str(variables.get("vpc_cidr", "10.0.0.0/16") or "10.0.0.0/16")
    instance_type = str(variables.get("instance_type", "m5.2xlarge") or "m5.2xlarge")
    pattern = str(variables.get("pattern", "external-only") or "external-only")
    kubernetes_version = str(variables.get("kubernetes_version", "1.30") or "1.30")
    node_desired_size = int(variables.get("node_desired_size", 3) or 3)
    node_min_size = int(variables.get("node_min_size", 3) or 3)
    node_max_size = int(variables.get("node_max_size", 4) or 4)

    # awsbnkctl's BigIPVESpec requires the dual-interface pattern: BIG-IP VE needs the
    # internal subnet for its server-side NIC. Override pattern early so all downstream
    # logic (datapath_block, the emitted pattern: line) reflects dual-interface.
    def _is_truthy_early(val: object) -> bool:
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("true", "1", "yes")

    if _is_truthy_early(variables.get("bigipve_enabled", False)):
        pattern = "dual-interface"

    # Safely derive subnet CIDRs from vpc_cidr using ipaddress. Subnets MUST stay
    # inside the VPC supernet, so we keep the VPC's first two octets and vary the
    # THIRD octet (e.g. 10.0.0.0/16 → 10.0.1.0/24, 10.0.10.0/24 …), matching the
    # awsbnkctl example topology. (A previous version mutated the second octet,
    # producing subnets like 10.1.0.0/24 that fall outside a /16 VPC.)
    # ip_network(strict=False) accepts every valid IPv4 CIDR, so an invalid vpc_cidr
    # raises ValueError here — surfaced as a clear task failure rather than silently
    # falling back to a guessed (and possibly wrong) prefix.
    vpc_net = ipaddress.ip_network(vpc_cidr_str, strict=False)
    octets = str(vpc_net.network_address).split(".")
    prefix = f"{octets[0]}.{octets[1]}"

    # Public subnets: .1.0/24 and .2.0/24
    pub1_cidr = f"{prefix}.1.0/24"
    pub2_cidr = f"{prefix}.2.0/24"

    # Private subnets: .11.0/24 and .12.0/24
    priv1_cidr = f"{prefix}.11.0/24"
    priv2_cidr = f"{prefix}.12.0/24"

    # Data-path subnets (BNK TMM interfaces)
    # external: .10.0/24 (required for all patterns)
    # internal: .20.0/24 (only for dual-interface)
    ext_datapath_cidr = f"{prefix}.10.0/24"
    int_datapath_cidr = f"{prefix}.20.0/24"

    # Build the dataPath dict — internal only for dual-interface pattern
    datapath: dict = {
        "external": {"cidr": ext_datapath_cidr, "az": f"{region}a"},
    }
    if pattern == "dual-interface":
        datapath["internal"] = {"cidr": int_datapath_cidr, "az": f"{region}a"}

    # Build the top-level document as a structured dict so that yaml.safe_dump
    # handles all quoting/escaping — no user value can inject malformed YAML.
    doc: dict = {
        "apiVersion": "awsbnkctl/v1",
        "kind": "Cluster",
        "metadata": {
            "name": cluster_name,
            "region": region,
        },
        "pattern": pattern,
        "network": {
            "vpcCidr": vpc_cidr_str,
            "azs": [f"{region}a", f"{region}b"],
            "subnets": {
                "public": [
                    {"cidr": pub1_cidr, "az": f"{region}a"},
                    {"cidr": pub2_cidr, "az": f"{region}b"},
                ],
                "private": [
                    {"cidr": priv1_cidr, "az": f"{region}a"},
                    {"cidr": priv2_cidr, "az": f"{region}b"},
                ],
            },
            "dataPath": datapath,
        },
        "cluster": {
            "kubernetesVersion": kubernetes_version,
            "nodeGroups": [
                {
                    "name": "default",
                    "desiredSize": node_desired_size,
                    "minSize": node_min_size,
                    "maxSize": node_max_size,
                    "instanceType": instance_type,
                }
            ],
        },
    }

    # Append bnk: block only when BOTH license file paths are present.
    # awsbnkctl resolves these RELATIVE to its cwd (= workspace dir), so
    # paths must be workspace-relative (./secrets/<filename>).
    # When either is absent the block is omitted — dry-run / infra-only path unchanged.
    if far_archive_path and jwt_path:
        doc["bnk"] = {
            "farArchive": far_archive_path,
            "jwt": jwt_path,
        }

    # ── Demo-layer blocks (emitted only when the respective toggle is enabled) ──
    # Handle both boolean True and string "true" forms (form variables arrive as strings).
    def _is_truthy(val: object) -> bool:
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("true", "1", "yes")

    demo_enabled = _is_truthy(variables.get("demo_enabled", False))
    jumphost_enabled = _is_truthy(variables.get("jumphost_enabled", False))
    bigipve_enabled = _is_truthy(variables.get("bigipve_enabled", False))
    ai_sagemaker_enabled = _is_truthy(variables.get("ai_sagemaker_enabled", False))

    # awsbnkctl's DemoSpec requires testing.jumphost.enabled: true (schema comment).
    # Auto-enable jumphost whenever demo is requested so the rendered YAML passes
    # awsbnkctl's schema validation. Operator is not required to tick both boxes.
    if demo_enabled:
        jumphost_enabled = True

    if jumphost_enabled:
        jumphost_instance_type = str(variables.get("jumphost_instance_type", "t3.small") or "t3.small")
        doc["testing"] = {
            "jumphost": {
                "enabled": True,
                "instanceType": jumphost_instance_type,
            }
        }

    if demo_enabled:
        demo_ttl = str(variables.get("demo_ttl", "24h") or "24h")
        doc["demo"] = {
            "enabled": True,
            "ttl": demo_ttl,
        }

    if bigipve_enabled:
        bigipve_instance_type = str(variables.get("bigipve_instance_type", "c5n.2xlarge") or "c5n.2xlarge")
        bigipve_license_tier = str(variables.get("bigipve_license_tier", "Good") or "Good")
        # VIP: use explicit value if provided; otherwise derive <prefix>.10.120 from
        # the external dataPath CIDR (reusing the already-computed `prefix`).
        # The hardcoded awsbnkctl default 10.0.10.120 only works for 10.0.0.0/16.
        bigipve_vip_raw = (variables.get("bigipve_vip") or "").strip()
        bigipve_vip = bigipve_vip_raw if bigipve_vip_raw else f"{prefix}.10.120"
        doc["bigipVE"] = {
            "enabled": True,
            "instanceType": bigipve_instance_type,
            "licenseTier": bigipve_license_tier,
            "vip": bigipve_vip,
        }

    if ai_sagemaker_enabled:
        ai_sagemaker_model = (
            variables.get("ai_sagemaker_model") or "meta-llama/Meta-Llama-3-8B-Instruct"
        ).strip() or "meta-llama/Meta-Llama-3-8B-Instruct"
        doc["ai"] = {
            "sagemaker": {
                "enabled": True,
                "model": ai_sagemaker_model,
            }
        }

    return yaml.safe_dump(doc, default_flow_style=False, allow_unicode=True)


# ── Context builder ──────────────────────────────────────────────────────────

def _build_cli_context(
    db, module: ProjectModule, *, for_destroy: bool = False
) -> ModuleContext:
    """Build ModuleContext with cloud credentials for BnkctlEngine.

    `for_destroy` suppresses the cluster.yaml render. Destroy must run against
    the config that was applied -- the workspace file written at apply time,
    beside the tool's own state -- not a fresh render of the current project
    form. See #82: editing cluster_name after apply made `down` target a
    cluster that never existed, leaving the real EKS cluster live and orphaned.

    Credentials are resolved from the project credential template and injected
    into credentials_env.  They live only in memory for the duration of task
    execution — never serialized to logs, DB, or UI.

    Form values (cluster.yaml field overrides) are read from
    module.variable_overrides merged over module.variables per the SSHEngine
    pattern.  The task layer pre-renders cluster.yaml content into
    variables["cluster_yaml"] so the engine can write it to disk.
    """
    project = module.project
    if project is None:
        raise ValueError(f"Module {module.id} has no associated project")

    # Merge library defaults → module variables → overrides (rightmost wins)
    # Note: ModuleLibrary stores variable definitions in variables_schema (list format)
    # with per-entry "default" values.  Extract those into a flat dict first.
    variables: dict = {}
    if module.library_module:
        schema = module.library_module.variables_schema or []
        if isinstance(schema, list):
            for entry in schema:
                if isinstance(entry, dict) and "name" in entry and "default" in entry:
                    variables[entry["name"]] = entry["default"]
        elif isinstance(schema, dict):
            # Legacy dict format: name → default value
            variables.update(schema)
    if module.variables:
        variables.update(module.variables)
    if module.variable_overrides:
        variables.update(module.variable_overrides)

    # Render cluster.yaml from form variables if this is a cli-bnkctl module
    module_path = (
        module.library_module.path if module.library_module else (module.path_in_project or "cli-bnkctl")
    )
    # Render cluster.yaml only for the cluster module, not for the use-cases module.
    # The use-cases module (bnkctl_action=="demo-usecases") uses the existing cluster.yaml
    # written by the cluster module — we must not overwrite or re-render it.
    is_cluster_module = (
        module_path.startswith("cli-bnkctl/")
        and "cluster_yaml" not in variables
        and variables.get("bnkctl_action") != "demo-usecases"
        # Never re-render on the destroy path -- the engine reads the applied
        # cluster.yaml straight from the workspace instead (#82).
        and not for_destroy
    )
    if is_cluster_module:
        # Materialize file-secrets into the workspace before rendering cluster.yaml.
        # SecretsService writes file secrets to <workspace>/secrets/<filename> and
        # returns target_variable_name → absolute_file_path in value_secrets.
        # We reuse the SAME workspace path the BnkctlEngine uses so the written
        # files are visible to the subprocess.
        tool = variables.get("bnkctl_tool", _DEFAULT_BINARY)
        workspace_dir = f"/app/projects/{module.project_id}/{tool}"

        try:
            value_secrets, _file_paths = SecretsService(db).prepare_secrets_for_execution(
                project_id=module.project_id,
                work_dir=workspace_dir,
                module_path=module_path,
            )
        except Exception as exc:
            logger.warning(
                "SecretsService.prepare_secrets_for_execution failed for module %s: %s — continuing without bnk: block",
                module.id, exc,
            )
            value_secrets = {}

        # Convert absolute secret paths to workspace-relative paths for cluster.yaml.
        # awsbnkctl resolves farArchive/jwt relative to its cwd (= workspace_dir).
        def _to_rel(abs_path: str | None) -> str | None:
            if not abs_path:
                return None
            try:
                from pathlib import Path
                rel = Path(abs_path).relative_to(workspace_dir)
                return f"./{rel}"
            except ValueError:
                # abs_path not under workspace — shouldn't happen, but fail soft
                logger.warning("Secret path %s is not under workspace %s — skipping bnk: block", abs_path, workspace_dir)
                return None

        far_archive_path = _to_rel(value_secrets.get("bnk_far_archive"))
        jwt_path = _to_rel(value_secrets.get("bnk_jwt"))

        # Render bnk-demo cluster.yaml with optional bnk: block
        variables["cluster_yaml"] = _render_awsbnkctl_cluster_yaml(
            variables,
            far_archive_path=far_archive_path,
            jwt_path=jwt_path,
        )
    # For other modules, the demo-usecases action, or if cluster_yaml is explicitly
    # provided, leave variables as-is so the engine uses the existing workspace file.

    # Also ensure "name" is set for backward compatibility (get_outputs uses it)
    # Prefer cluster_name; fall back to project name; last resort to module ID
    if "name" not in variables:
        variables["name"] = variables.get("cluster_name") or project.name or str(project.id)

    # Resolve credentials env; graceful on missing creds (dry-run doesn't need them)
    try:
        creds_env = get_cloud_credentials_env(project, db)
    except Exception as e:
        logger.warning(
            "Could not resolve cloud credentials for project %s (module %s): %s — continuing",
            module.project_id, module.id, e,
        )
        creds_env = {}

    category = (
        module.library_module.category if module.library_module else "infra"
    )

    return ModuleContext(
        module_id=module.id,
        project_id=module.project_id,
        path=module_path,
        category=category,
        variables=variables,
        credentials_env=creds_env,
    )


# ── Engine factory ───────────────────────────────────────────────────────────

def _engine() -> BnkctlEngine:
    return BnkctlEngine(SessionLocal)


# ── Log helpers ──────────────────────────────────────────────────────────────

def _ts() -> str:
    """Compact UTC timestamp for log prefixing."""
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


# ── Celery tasks ─────────────────────────────────────────────────────────────

@celery_app.task(bind=True, base=CallbackTask, name="tasks.cli_tasks.run_cli_init")
def run_cli_init(self, task_db_id: int, module_id: int, auto_apply: bool = False, **kwargs):
    """Initialize CLI module — verify binary presence and workspace."""
    task = None
    with get_db_context() as db:
        try:
            task = fetch_task_or_raise(db, task_db_id)

            task.status = "in_progress"
            task.started_at = datetime.now(UTC)
            task.command = "cli-engine: init"
            db.commit()
            _notify_task_started(task)

            module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
            if not module:
                raise ValueError(f"Module {module_id} not found")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_cli_context(db, module)
                output_lines: list[str] = []

                def _on_init_output(line: str) -> None:
                    stamped = f"[{_ts()}] {line}"
                    output_lines.append(stamped)
                    try:
                        from services.websocket_service import publish_module_log
                        publish_module_log(module_id=module.id, line=stamped)
                    except Exception:
                        pass

                result = _engine().init(ctx, on_output=_on_init_output)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== CLI ENGINE INIT ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    module_fields = {"status": "initialized", "last_init_at": datetime.now(UTC)}
                else:
                    task.status = "failed"
                    task.error = result.error_message or "CLI init failed"
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
                        logger.info(
                            "CLI auto-apply skipped for module %s: %s", module.id, missing,
                        )

            return {"success": result.success, "exit_code": task.exit_code}

        except ModuleLockError as e:
            logger.error("CLI init task failed - module locked: %s", e)
            module = locals().get("module")
            _mark_task_failed(task, f"Module locked: {e}", db, update_stack=False)
            if module and module.stack_instance_id:
                _update_stack_status_if_needed(module, db)
            raise

        except ModuleLockLostError as e:
            logger.warning("CLI init aborted - lock lost mid-operation: %s", e)
            module = locals().get("module")
            _mark_task_failed(task, f"Lock lost: {e}", db, update_stack=False)
            if module and module.stack_instance_id:
                _update_stack_status_if_needed(module, db)
            raise

        except Exception as exc:
            logger.exception("CLI init task failed: %s", exc)
            module = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=module, failed_status="init_failed")
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.cli_tasks.run_cli_apply",
)
def run_cli_apply(self, task_db_id: int, module_id: int, **kwargs):
    """Apply CLI module — run awsbnkctl up, stream stdout.

    awsbnkctl up (non-dry-run) can take 30-60 min for a full cluster bring-up.
    Uses the app-wide Celery time limits (task_soft_time_limit / task_time_limit
    in celery_app.py) rather than a per-task override — see S14-035: a tighter
    hard limit than the workspace lock timeout kills the task while the lock
    persists, orphaning it. Do not add a lower time_limit/soft_time_limit here.
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
            task.command = "cli-engine: apply"
            db.commit()
            _notify_task_started(task)

            can_exec, missing = check_dependencies(db, module)
            if not can_exec:
                raise ValueError(f"Dependencies not satisfied: {', '.join(missing)}")

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_cli_context(db, module)
                engine = _engine()
                output_lines: list[str] = []

                def _on_apply_output(line: str) -> None:
                    stamped = f"[{_ts()}] {line}"
                    output_lines.append(stamped)
                    try:
                        from services.websocket_service import publish_module_log
                        publish_module_log(module_id=module.id, line=stamped)
                    except Exception:
                        pass

                result = engine.apply(ctx, on_output=_on_apply_output)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== CLI ENGINE APPLY ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=result.error_message,
                    suggestion=result.error_suggestion,
                )

                if result.success:
                    task.status = "completed"
                    outputs = engine.get_outputs(ctx)
                    module_fields = {
                        "status": "applied",
                        "outputs": outputs,
                        "last_deployed_at": datetime.now(UTC),
                        "deployment_error": None,
                    }
                else:
                    task.status = "failed"
                    task.error = result.error_message or "CLI apply failed"
                    module_fields = {
                        "status": "apply_failed",
                        "deployment_error": task.error[-2000:],
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()

                # Auto-register EKS cluster when the apply produced the full contract
                if result.success:
                    from services.eks_service import (
                        matches_eks_output_contract,
                        register_eks_cluster,
                    )
                    from tasks.cluster_scan_task import enqueue_cluster_scan
                    if matches_eks_output_contract(module.outputs or {}):
                        try:
                            cluster = register_eks_cluster(db, module)
                            db.commit()
                            enqueue_cluster_scan(int(cluster.id))
                            task.meta_data = task.meta_data or {}
                            task.meta_data["cluster_auto_registered"] = {
                                "cluster_id": cluster.id,
                                "cluster_name": cluster.name,
                            }
                            db.commit()
                            logger.info(
                                "Auto-registered EKS cluster %s (id=%s) from CLI apply",
                                cluster.name,
                                cluster.id,
                            )
                        except Exception as _reg_exc:
                            logger.warning(
                                "CLI apply: EKS auto-registration failed (non-fatal): %s",
                                _reg_exc,
                            )

                create_deployment_record(db, task, module, "apply", task.logs)
                _update_stack_status_if_needed(module, db)

            return {
                "success": result.success,
                "exit_code": task.exit_code,
                "outputs": module_fields.get("outputs", {}),
            }

        except ModuleLockError as e:
            logger.error("CLI apply task failed - module locked: %s", e)
            _mark_task_failed(task, f"Module locked: {e}", db, update_stack=False)
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except ModuleLockLostError as e:
            logger.warning("CLI apply aborted - lock lost mid-operation: %s", e)
            _mark_task_failed(task, f"Lock lost: {e}", db, update_stack=False)
            mod = locals().get("module")
            if mod and mod.stack_instance_id:
                _update_stack_status_if_needed(mod, db)
            raise

        except Exception as exc:
            logger.exception("CLI apply task failed: %s", exc)
            mod = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=mod, failed_status="apply_failed")
            raise


@celery_app.task(
    bind=True,
    base=CallbackTask,
    name="tasks.cli_tasks.run_cli_plan",
)
def run_cli_plan(self, task_db_id: int, module_id: int, **kwargs):
    """Plan CLI module — run awsbnkctl up --dry-run."""
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
            task.command = "cli-engine: plan (dry-run)"
            db.commit()
            _notify_task_started(task)

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_cli_context(db, module)
                output_lines: list[str] = []

                def _on_plan_output(line: str) -> None:
                    stamped = f"[{_ts()}] {line}"
                    output_lines.append(stamped)
                    try:
                        from services.websocket_service import publish_module_log
                        publish_module_log(module_id=module.id, line=stamped)
                    except Exception:
                        pass

                plan_result = _engine().plan(ctx, on_output=_on_plan_output)

                # Treat "skipped / no use-cases selected" as success: the plan did
                # exactly what was requested (nothing). Only a subprocess error (non-zero
                # exit) or a missing cluster.yaml gate is a real failure.
                # Success is decided by the engine's structural PlanResult fields
                # (has_changes / skipped), never by substring-matching `details` — details
                # can embed the tool's raw stdout on failure, and a grep for "skipped" there
                # would flip a real non-zero exit into a false "completed".
                success = plan_result.has_changes or plan_result.skipped
                task.exit_code = 0 if success else 1

                # Only include details as error_message when it represents an actual failure
                error_message = None
                if not success and plan_result.details:
                    error_message = plan_result.details[:2000]

                task.logs = _format_logs(
                    header=f"=== CLI ENGINE PLAN ===\nModule: {ctx.path}",
                    output_lines=output_lines,
                    error_message=error_message,
                )
                task.status = "completed" if success else "failed"
                if not success:
                    task.error = plan_result.details[:500]

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                # Store plan output for reference
                set_locked_module_fields(
                    db, module, lock,
                    plan_output=plan_result.details[:4000],
                )

                db.commit()
                create_deployment_record(db, task, module, "plan", task.logs)

            return {"success": success, "has_changes": plan_result.has_changes}

        except ModuleLockError as e:
            logger.error("CLI plan task failed - module locked: %s", e)
            _mark_task_failed(task, f"Module locked: {e}", db, update_stack=False)
            raise

        except Exception as exc:
            logger.exception("CLI plan task failed: %s", exc)
            _mark_task_failed(task, str(exc), db)
            raise


@celery_app.task(bind=True, base=CallbackTask, name="tasks.cli_tasks.run_cli_destroy")
def run_cli_destroy(self, task_db_id: int, module_id: int, **kwargs):
    """Destroy CLI module — run awsbnkctl down --yes."""
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
            task.command = "cli-engine: destroy"
            db.commit()
            _notify_task_started(task)

            with module_lock(db, module.id, task_id=task_db_id) as lock:
                ctx = _build_cli_context(db, module, for_destroy=True)
                output_lines: list[str] = []

                def _on_destroy_output(line: str) -> None:
                    stamped = f"[{_ts()}] {line}"
                    output_lines.append(stamped)
                    try:
                        from services.websocket_service import publish_module_log
                        publish_module_log(module_id=module.id, line=stamped)
                    except Exception:
                        pass

                result = _engine().destroy(ctx, on_output=_on_destroy_output)

                task.exit_code = 0 if result.success else 1
                task.logs = _format_logs(
                    header=f"=== CLI ENGINE DESTROY ===\nModule: {ctx.path}",
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
                    task.error = result.error_message or "CLI destroy failed"
                    module_fields = {
                        "status": "destroy_failed",
                        "deployment_error": task.error[-2000:],
                    }

                task.completed_at = datetime.now(UTC)
                task.duration_seconds = (task.completed_at - task.started_at).total_seconds()
                set_locked_module_fields(db, module, lock, **module_fields)

                update_project_counts(db, module.project_id)
                db.commit()

                # Auto-unregister EKS cluster on successful destroy
                if result.success:
                    try:
                        from services.eks_service import unregister_eks_cluster
                        unregistered = unregister_eks_cluster(db, module)
                        if unregistered:
                            logger.info(
                                "Auto-unregistered EKS cluster for module %s", module.id,
                            )
                    except Exception as _unreg_exc:
                        logger.warning(
                            "CLI destroy: EKS auto-unregistration failed (non-fatal): %s",
                            _unreg_exc,
                        )

                create_deployment_record(db, task, module, "destroy", task.logs)
                _update_stack_status_if_needed(module, db)
                _trigger_next_destroy_module(module, db, task.id)

            return {"success": result.success, "exit_code": task.exit_code}

        except ModuleLockError as e:
            logger.error("CLI destroy task failed - module locked: %s", e)
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
                    _trigger_next_destroy_module(_exc_module, db, task.id)
            except Exception as trigger_err:
                logger.warning(
                    "_trigger_next_destroy_module failed after CLI ModuleLockError: %s", trigger_err,
                )
            raise

        except ModuleLockLostError as e:
            logger.warning("CLI destroy aborted - lock lost mid-operation: %s", e)
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
                    _trigger_next_destroy_module(_exc_module, db, task.id)
            except Exception as trigger_err:
                logger.warning(
                    "_trigger_next_destroy_module failed after CLI ModuleLockLostError: %s", trigger_err,
                )
            raise

        except Exception as exc:
            logger.exception("CLI destroy task failed: %s", exc)
            _exc_module = locals().get("module")
            _mark_task_failed(task, str(exc), db, module=_exc_module, failed_status="destroy_failed")
            try:
                if _exc_module is not None:
                    db.refresh(_exc_module)
                    _trigger_next_destroy_module(_exc_module, db, task.id)
            except Exception as trigger_err:
                logger.warning(
                    "_trigger_next_destroy_module failed after CLI generic exception: %s", trigger_err,
                )
            raise
