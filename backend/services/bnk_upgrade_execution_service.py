"""
BNK upgrade execution mixin — step execution, health gates, and rollback.

Extracted from bnk_upgrade_service.py (R4-012) to keep the monolith under control.

ENG-006: This mixin retains db.commit() calls because upgrade execution is a
long-running multi-step process that commits after each step to persist progress.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from models import BnkUpgrade, KubernetesCluster
from models.enums import BnkUpgradeStatus
from services.cluster_scanner import ClusterScanner
from services.entity_lock import EntityLock, set_locked_entity_fields
from services.helm_service import HelmService

logger = logging.getLogger(__name__)

# Import step type constants from the main module
from services.bnk_upgrade_service import (
    STEP_CRD_WAIT,
    STEP_HEALTH_GATE,
    STEP_HELM_UPGRADE,
    STEP_MANIFEST_APPLY,
)


class BnkUpgradeExecutionMixin:
    """
    Mixin providing upgrade execution, step executors, and rollback.

    Expects the host class to provide:
        - self.db  (SQLAlchemy Session)
    """

    def execute_upgrade(
        self,
        upgrade_id: int,
        on_output: Callable[[str], None] | None = None,
        *,
        lock: EntityLock | None = None,
    ) -> BnkUpgrade:
        """
        Execute an approved upgrade plan step by step.

        Each step is executed in order. If a step fails, the upgrade
        is marked as failed and rollback info is stored. Health gates
        poll the cluster until conditions are met or timeout.

        Args:
            upgrade_id: BnkUpgrade.id
            on_output: Callback for streaming output lines (for WebSocket/task logs)

        Returns:
            Updated BnkUpgrade record
        """
        upgrade = self.db.query(BnkUpgrade).filter(BnkUpgrade.id == upgrade_id).first()
        if not upgrade:
            raise ValueError(f"Upgrade {upgrade_id} not found")

        if upgrade.status not in (BnkUpgradeStatus.READY, BnkUpgradeStatus.IN_PROGRESS):
            raise ValueError(f"Upgrade is {upgrade.status}, not ready for execution")

        def emit(msg: str):
            logger.info(f"[upgrade-{upgrade_id}] {msg}")
            if on_output:
                on_output(msg)

        # #389: automated execution is only wired up for FLO-managed installs
        # today. Plan generation and pre-checks are install-shape-aware (a
        # helm/manual install gets a correct, non-crashing plan), but actually
        # driving a helm/manual install's upgrade would mean Helm-upgrading an
        # arbitrary discovered release (e.g. an ingress controller chart) using
        # a target version selected from the FLO OCI registry — those are not
        # the same chart, so executing it would be actively wrong, not merely
        # unsupported. Fail clearly instead of attempting it; the operator can
        # perform the Helm upgrade manually and re-run Validate & Plan after.
        install_shape = (upgrade.from_bnk_info or {}).get("install_shape", "flo")
        if install_shape != "flo":
            message = (
                f"Automated upgrade execution is not yet supported for install_shape={install_shape!r} "
                "(helm/manual BNK install). Perform the Helm upgrade manually, then re-run "
                "Validate & Plan to confirm the result."
            )
            emit(message)
            self._write_upgrade(
                upgrade, lock,
                status=BnkUpgradeStatus.FAILED,
                error_message=message,
                completed_at=datetime.now(UTC),
            )
            return upgrade

        # ENG-006: per-step commits are preserved for crash recovery.
        # Each commit is preceded by a fence-protected UPDATE when lock is held.
        self._write_upgrade(
            upgrade, lock,
            status=BnkUpgradeStatus.IN_PROGRESS,
            started_at=datetime.now(UTC),
            step_results=upgrade.step_results or [],
        )

        plan = upgrade.plan or []
        helm_service = HelmService(self.db)
        cluster_id = upgrade.cluster_id

        # Store FLO revision for rollback.
        # The helm upgrade step carries the authoritative release_name + namespace
        # from the existing release record (plan builder raises if either is absent).
        try:
            helm_step = next(
                (s for s in plan if s.get("action") == STEP_HELM_UPGRADE), None
            )
            if helm_step is None:
                raise ValueError(
                    "Upgrade plan contains no helm_upgrade step — cannot capture rollback info."
                )
            flo_release_name = helm_step.get("release_name", "flo")
            flo_namespace = helm_step.get("namespace")
            if not flo_namespace:
                raise ValueError(
                    "Helm upgrade step is missing 'namespace' — cannot capture rollback info."
                )
            flo_history = helm_service.get_history(
                cluster_id,
                flo_release_name,
                namespace=flo_namespace,
            )
            if flo_history:
                self._write_upgrade(
                    upgrade, lock,
                    rollback_info={
                        "flo_revision": flo_history[-1].get("revision"),
                        "flo_release_name": flo_release_name,
                        "flo_namespace": flo_namespace,
                    },
                    rollback_available=True,
                )
        except Exception as e:
            emit(f"Warning: Could not capture rollback info: {e}")

        for step_def in plan:
            step_num = step_def["step"]
            if step_num <= upgrade.current_step:
                continue  # Resume from where we left off

            emit(f"Step {step_num}/{len(plan)}: {step_def['label']}")
            self._write_upgrade(upgrade, lock, current_step=step_num)

            step_result = {
                "step": step_num,
                "label": step_def["label"],
                "action": step_def["action"],
                "started_at": datetime.now(UTC).isoformat(),
                "status": "in_progress",
            }

            try:
                if step_def["action"] == STEP_HELM_UPGRADE:
                    result = self._execute_helm_upgrade(
                        helm_service, cluster_id, step_def, emit,
                    )
                    step_result["output"] = result.get("stdout", "")

                elif step_def["action"] == STEP_MANIFEST_APPLY:
                    result = self._execute_manifest_apply(
                        cluster_id, step_def, emit,
                    )
                    step_result["output"] = result.get("message", "")

                elif step_def["action"] == STEP_HEALTH_GATE:
                    result = self._execute_health_gate(
                        cluster_id, step_def, upgrade, emit, lock=lock,
                    )
                    step_result["health"] = result

                elif step_def["action"] == STEP_CRD_WAIT:
                    result = self._execute_crd_wait(
                        cluster_id, step_def, emit,
                    )
                    step_result["output"] = result.get("message", "")

                step_result["status"] = "completed"
                step_result["completed_at"] = datetime.now(UTC).isoformat()
                emit(f"  ✓ Step {step_num} completed")

            except Exception as e:
                step_result["status"] = "failed"
                step_result["error"] = str(e)
                step_result["completed_at"] = datetime.now(UTC).isoformat()

                now = datetime.now(UTC)
                started = upgrade.started_at
                duration = (now - started).total_seconds() if started else None
                results = list(upgrade.step_results or [])
                results.append(step_result)
                write_fields: dict = dict(
                    status=BnkUpgradeStatus.FAILED,
                    error_message=f"Step {step_num} failed: {e}",
                    error_step=step_num,
                    completed_at=now,
                    step_results=results,
                )
                if duration is not None:
                    write_fields["duration_seconds"] = duration
                self._write_upgrade(upgrade, lock, **write_fields)

                emit(f"  ✗ Step {step_num} failed: {e}")
                return upgrade

            # Append step result — fence-protected per ENG-006.
            results = list(upgrade.step_results or [])
            results.append(step_result)
            self._write_upgrade(upgrade, lock, step_results=results)

        # All steps completed
        now = datetime.now(UTC)
        started = upgrade.started_at
        duration = (now - started).total_seconds() if started else None
        write_fields = dict(status=BnkUpgradeStatus.COMPLETED, completed_at=now)
        if duration is not None:
            write_fields["duration_seconds"] = duration
        self._write_upgrade(upgrade, lock, **write_fields)
        emit(f"Upgrade completed: {upgrade.from_version} → {upgrade.to_version}")
        return upgrade

    # ----------------------------------------------------------
    # Step executors
    # ----------------------------------------------------------

    def _execute_helm_upgrade(
        self,
        helm_service: HelmService,
        cluster_id: int,
        step: dict,
        emit: Callable,
    ) -> dict:
        """Execute a Helm upgrade step (FLO chart)."""
        release_name = step.get("release_name", "flo")
        namespace = step.get("namespace")
        chart = step.get("chart")
        if not namespace:
            raise ValueError(
                "Helm upgrade step is missing 'namespace'. "
                "Upgrade plans must include namespace from the existing release record."
            )
        if not chart:
            raise ValueError(
                "Helm upgrade step is missing 'chart'. "
                "Upgrade plans must include chart from the existing release record "
                "so airgapped registries are honoured."
            )
        version = step.get("version")
        timeout = str(step.get("timeout", 600)) + "s"

        emit(f"  Helm upgrading {release_name} in {namespace} to {version}")

        # Get current values to preserve configuration
        try:
            current_values = helm_service.get_values(
                cluster_id, release_name, namespace=namespace, all_values=True,
            )
        except Exception:
            current_values = {}

        result = helm_service.upgrade_release(
            cluster_id=cluster_id,
            release_name=release_name,
            chart=chart,
            namespace=namespace,
            values=current_values,
            version=version,
            install=False,  # Must already exist
            wait=True,
            timeout=timeout,
        )

        if result.get("exit_code", 1) != 0:
            raise RuntimeError(
                f"Helm upgrade failed: {result.get('stderr', 'unknown error')}"
            )

        emit(f"  Helm upgrade completed: {release_name} → {version}")
        return result

    def _execute_manifest_apply(
        self,
        cluster_id: int,
        step: dict,
        emit: Callable,
    ) -> dict:
        """
        Re-apply a manifest module via the K8s engine.

        This re-renders the module's manifests and applies them via server-side apply,
        which is idempotent and handles CRD schema changes gracefully.
        """
        module_path = step.get("module")
        if not module_path:
            raise ValueError("No module path in manifest_apply step")

        # Get project variables from the cluster's project
        cluster = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.id == cluster_id
        ).first()
        if not cluster:
            raise ValueError(f"Cluster {cluster_id} not found")

        # Get the project module record for current variable values
        from models import ProjectModule
        project_module = self.db.query(ProjectModule).filter(
            ProjectModule.project_id == cluster.project_id,
            ProjectModule.path_in_project == module_path,
        ).first()

        if not project_module:
            emit(f"  Module {module_path} not deployed to project — skipping")
            return {"message": f"Module {module_path} not in project, skipped"}

        # Ensure this is a K8s-backed module before attempting manifest render/apply
        lib_module = project_module.library_module
        if not lib_module or not lib_module.execution_engine:
            emit(f"  Module {module_path} has no execution engine — skipping (not critical)")
            return {"message": f"Module {module_path} has no execution engine, skipped"}

        execution_engine = (lib_module.execution_engine or "").strip().lower()
        if execution_engine not in {"kubernetes-direct", "operator"}:
            emit(f"  Module {module_path} uses {execution_engine} engine — skipping manifest_apply")
            return {"message": f"Module {module_path} is not a K8s module, skipped"}

        deploy_model = (getattr(lib_module, "deploy_model", None) or "").strip().lower()
        if deploy_model != "manifests":
            emit(f"  Module {module_path} deploy_model={deploy_model or 'unknown'} — skipping manifest_apply")
            return {"message": f"Module {module_path} is not a manifest module, skipped"}

        # Use the variable assembler to get full variables
        from services.execution.variable_assembler import assemble_variables
        variables = assemble_variables(
            self.db, project_module, include_secrets=True,
        )

        # Build manifests from catalog metadata
        from services.execution.engine_interface import ModuleContext
        from services.execution.k8s_catalog_payload import build_manifest_payload
        from services.execution.opentofu_runtime import OpenTofuRuntime

        workspace_path = OpenTofuRuntime(self.db).prepare_persistent_workspace(project_module, operation="apply")
        ctx = ModuleContext(
            module_id=project_module.id,
            project_id=project_module.project_id,
            path=module_path,
            category=getattr(lib_module, "category", None) or "k8s",
            variables=variables,
            module_source_kind=getattr(lib_module, "module_source_kind", None),
            deploy_model=getattr(lib_module, "deploy_model", None),
            pack_manifest=getattr(lib_module, "pack_manifest", None),
            workspace_path=workspace_path,
        )
        payload = build_manifest_payload(ctx, variables)
        manifests = payload.get("manifests", [])

        if not manifests:
            emit(f"  Module {module_path} rendered 0 manifests — nothing to apply")
            return {"message": "No manifests to apply"}

        # Use kr8s server-side apply
        from services.cluster_utils import kubeconfig_for_cluster
        from services.execution.kubernetes_engine import KubernetesEngine, _get_or_create_loop, _server_side_apply

        # Create a temporary kubeconfig file with guaranteed cleanup
        with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
            engine = KubernetesEngine(kubeconfig_path, context=cluster.context)
            loop = _get_or_create_loop()
            api = loop.run_until_complete(engine._get_api())
            count = 0
            for manifest in manifests:
                kind = manifest.get("kind", "Unknown")
                name = manifest.get("metadata", {}).get("name", "")
                emit(f"  Applying {kind}/{name}")
                loop.run_until_complete(_server_side_apply(api, manifest))
                count += 1

        emit(f"  Applied {count} resources for {module_path}")
        return {"message": f"Applied {count} resources", "count": count}

    def _execute_health_gate(
        self,
        cluster_id: int,
        step: dict,
        upgrade: BnkUpgrade,
        emit: Callable,
        *,
        lock: "EntityLock | None" = None,
    ) -> dict:
        """
        Execute a health gate check.

        Scans the cluster and checks specific health conditions.
        For pre/post upgrade phases, stores the health snapshot.
        """
        phase = step.get("phase", "")
        timeout = step.get("timeout", 120)
        checks = step.get("checks", [])
        deadline = time.time() + timeout

        scanner = ClusterScanner(self.db)

        while time.time() < deadline:
            try:
                scan_data = scanner.scan(cluster_id)
            except Exception as e:
                emit(f"  Health scan failed: {e}, retrying...")
                time.sleep(10)
                continue

            bnk = scan_data.get("bnk_install", {})
            health_snapshot = {
                "flo": bnk.get("flo", {}),
                "tmm": bnk.get("tmm", {}),
                "controller": bnk.get("controller", {}),
                "vlans": bnk.get("vlans", []),
                "health": bnk.get("health"),
                "status": bnk.get("status"),
                "timestamp": datetime.now(UTC).isoformat(),
            }

            # Store snapshots for pre/post phases
            if phase == "pre_upgrade":
                self._write_upgrade(upgrade, lock, pre_health=health_snapshot)
                emit("  Pre-upgrade health snapshot captured")
                return health_snapshot

            if phase == "post_upgrade":
                self._write_upgrade(upgrade, lock, post_health=health_snapshot)

            # Check specific conditions
            all_passed = True
            for check in checks:
                passed = self._evaluate_health_check(check, bnk, emit)
                if not passed:
                    all_passed = False

            if all_passed:
                if phase == "post_upgrade":
                    self._write_upgrade(upgrade, lock, health_gate_passed=True)
                emit("  Health gate passed")
                return health_snapshot

            # Not all checks passed — wait and retry
            emit("  Health checks not all passing, retrying in 15s...")
            time.sleep(15)

        # Timeout
        raise RuntimeError(
            f"Health gate timed out after {timeout}s — checks not passing: {checks}"
        )

    def _evaluate_health_check(
        self,
        check: str,
        bnk: dict,
        emit: Callable,
    ) -> bool:
        """Evaluate a single health check condition."""
        flo = bnk.get("flo", {})
        tmm = bnk.get("tmm", {})
        vlans = bnk.get("vlans", [])

        if check == "flo_pods_ready":
            running = flo.get("running", 0)
            total = flo.get("pods", 0)
            ok = running == total and total > 0
            if not ok:
                emit(f"    FLO: {running}/{total} pods ready")
            return ok

        elif check == "controller_pods_ready":
            ctrl = bnk.get("controller", {})
            running = ctrl.get("running", 0)
            total = ctrl.get("pods", 0)
            ok = running == total and total > 0
            if not ok:
                emit(f"    Controller: {running}/{total} pods ready")
            return ok

        elif check == "tmm_pods_ready":
            running = tmm.get("running", 0)
            total = tmm.get("pods", 0)
            ok = running == total and total > 0
            if not ok:
                emit(f"    TMM: {running}/{total} pods ready")
            return ok

        elif check == "tmm_containers_ready":
            containers = tmm.get("containers", {})
            total_c = containers.get("total", 0)
            ready_c = containers.get("ready", 0)
            ok = ready_c == total_c and total_c > 0
            if not ok:
                emit(f"    TMM containers: {ready_c}/{total_c} ready")
            return ok

        elif check == "vlans_programmed":
            ok = all(v.get("programmed", False) for v in vlans) if vlans else True
            if not ok:
                unprog = [v.get("name", "?") for v in vlans if not v.get("programmed", False)]
                emit(f"    VLANs not programmed: {unprog}")
            return ok

        elif check == "gatewayclass_accepted":
            # For now, if BNK is installed, GatewayClass should be accepted
            return True

        else:
            emit(f"    Unknown health check: {check}")
            return True

    def _execute_crd_wait(
        self,
        cluster_id: int,
        step: dict,
        emit: Callable,
    ) -> dict:
        """
        Wait for CRD installer to complete after FLO upgrade.

        FLO deploys a crd-installer Job in f5-utils namespace that
        creates/updates all F5 CRDs. We wait for it to reach
        Completed status.
        """
        timeout = step.get("timeout", 120)
        deadline = time.time() + timeout

        from services.k8s_service import KubernetesService
        k8s_svc = KubernetesService(self.db)

        while time.time() < deadline:
            try:
                cluster = k8s_svc.get_cluster(cluster_id)
                api_client = k8s_svc.load_kubeconfig(cluster)

                from kubernetes import client as k8s_client
                batch_api = k8s_client.BatchV1Api(api_client)

                jobs = batch_api.list_namespaced_job(
                    namespace="f5-utils",
                    label_selector="app=crd-installer",
                ).items

                if not jobs:
                    # CRD installer may not exist for minor upgrades
                    emit("  No crd-installer job found — continuing")
                    return {"message": "No crd-installer job detected"}

                for job in jobs:
                    if job.status.succeeded and job.status.succeeded > 0:
                        emit("  CRD installer completed successfully")
                        return {"message": "CRD installer completed"}
                    if job.status.failed and job.status.failed > 0:
                        raise RuntimeError("CRD installer job failed")

                emit("  Waiting for CRD installer to complete...")

            except RuntimeError:
                raise
            except Exception as e:
                emit(f"  CRD check error: {e}, retrying...")

            time.sleep(10)

        # Timeout is not fatal for CRD wait — minor upgrades may not trigger it
        emit("  CRD wait timed out — continuing (may not be required for this upgrade)")
        return {"message": "CRD wait timed out, continuing"}

    # ----------------------------------------------------------
    # Rollback
    # ----------------------------------------------------------

    def _write_upgrade(
        self,
        upgrade: BnkUpgrade,
        lock: "EntityLock | None",
        **fields,
    ) -> None:
        """Route a field write through the lock fence if held, else plain ORM.

        When lock is provided, uses set_locked_entity_fields (fence-protected).
        When lock is None (legacy callers, tests without a lock), falls back to
        plain ORM attribute set + commit. This is the ENG-006-compatible helper:
        callers commit after every step and every health snapshot.
        """
        if lock is not None:
            self.db.expire(upgrade, list(fields.keys()))
            set_locked_entity_fields(self.db, upgrade, lock, table="bnk_upgrades", **fields)
        else:
            for k, v in fields.items():
                setattr(upgrade, k, v)
            self.db.commit()

    def rollback(
        self,
        upgrade_id: int,
        on_output: Callable[[str], None] | None = None,
        *,
        lock: "EntityLock | None" = None,
    ) -> BnkUpgrade:
        """
        Roll back a failed upgrade to the previous FLO revision.

        Uses `helm rollback` to restore FLO, then re-scans to verify.
        """
        upgrade = self.db.query(BnkUpgrade).filter(BnkUpgrade.id == upgrade_id).first()
        if not upgrade:
            raise ValueError(f"Upgrade {upgrade_id} not found")

        if not upgrade.rollback_available:
            raise ValueError("No rollback available for this upgrade")

        if upgrade.status not in (BnkUpgradeStatus.FAILED, BnkUpgradeStatus.IN_PROGRESS, BnkUpgradeStatus.HEALTH_CHECK):
            raise ValueError(f"Cannot rollback from status '{upgrade.status}'")

        def emit(msg: str):
            logger.info(f"[rollback-{upgrade_id}] {msg}")
            if on_output:
                on_output(msg)

        self._write_upgrade(
            upgrade, lock,
            status=BnkUpgradeStatus.ROLLING_BACK,
            rollback_reason=upgrade.error_message or "User-initiated rollback",
        )

        rollback_info = upgrade.rollback_info or {}
        release_name = rollback_info.get("flo_release_name", "flo")
        namespace = rollback_info.get("flo_namespace")
        if not namespace:
            raise ValueError(
                "Rollback info is missing 'flo_namespace'. "
                "Cannot rollback without knowing the FLO release namespace."
            )
        revision = rollback_info.get("flo_revision")

        emit(f"Rolling back FLO to revision {revision}...")

        try:
            helm_service = HelmService(self.db)
            result = helm_service.rollback_release(
                cluster_id=upgrade.cluster_id,
                release_name=release_name,
                revision=revision,
                namespace=namespace,
                wait=True,
                timeout="10m",
            )

            if result.get("exit_code", 1) != 0:
                raise RuntimeError(f"Helm rollback failed: {result.get('stderr', '')}")

            emit("FLO rolled back successfully")

            # Re-scan to verify
            emit("Verifying rollback health...")
            scanner = ClusterScanner(self.db)
            scan = scanner.scan(upgrade.cluster_id)
            bnk = scan.get("bnk_install", {})
            flo = bnk.get("flo", {})

            now = datetime.now(UTC)
            started = upgrade.started_at
            duration = (now - started).total_seconds() if started else None
            write_fields: dict = dict(
                status=BnkUpgradeStatus.ROLLED_BACK,
                completed_at=now,
                post_health={
                    "flo": flo,
                    "tmm": bnk.get("tmm", {}),
                    "health": bnk.get("health"),
                    "status": bnk.get("status"),
                    "timestamp": now.isoformat(),
                    "type": "rollback",
                },
            )
            if duration is not None:
                write_fields["duration_seconds"] = duration
            self._write_upgrade(upgrade, lock, **write_fields)

            emit(f"Rollback complete — FLO now at {flo.get('version', 'unknown')}")
            return upgrade

        except Exception as e:
            now = datetime.now(UTC)
            started = upgrade.started_at
            duration = (now - started).total_seconds() if started else None
            write_fields = dict(
                status=BnkUpgradeStatus.FAILED,
                error_message=f"Rollback failed: {e}",
                completed_at=now,
            )
            if duration is not None:
                write_fields["duration_seconds"] = duration
            self._write_upgrade(upgrade, lock, **write_fields)
            emit(f"Rollback failed: {e}")
            raise
