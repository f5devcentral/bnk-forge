"""
Proxy Migration Service — guided, gated, reversible proxy-to-BNK cutover.

Phase 3 of D-021.  Orchestrates:
  translate → deploy_bnk → verify → cutover (operator-confirmed) → teardown_old

Design principles (DECOMPOSITION.md):
- COPY bare_metal/orchestrator.py state-machine pattern.
- CUTOVER is OPERATOR-CONFIRMED — forge has no DNS/VIP control. The cutover
  step records the operator confirmation; it does NOT flip DNS (D-017 honesty).
- VERIFY GATE is fail-closed (default-deny): missing/exception → passed=False.
  The cutover executor ASSERTS persisted verify==passed; the confirm-cutover
  route rejects unless run status is AWAITING_CUTOVER.
- SOURCE-AGNOSTIC: create_migration() takes a pre-fetched source descriptor +
  translated manifest so D-023 P3 (BIG-IP/CIS source) can reuse verbatim.
- REUSE: apply/delete_bnk_manifest delegates to ProxyDeployService helpers.
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError
from models.enums import MigrationStepStatus, ProxyMigrationStatus
from models.proxy_migration import ProxyMigration, ProxyMigrationStep

logger = logging.getLogger(__name__)


def _duration(started_at: datetime | None, completed_at: datetime | None) -> float | None:
    """Compute duration in seconds, handling naive/aware timezone mismatch (SQLite)."""
    if started_at is None or completed_at is None:
        return None
    # Strip timezone info so naive − aware or aware − naive doesn't crash
    s = started_at.replace(tzinfo=None) if started_at.tzinfo else started_at
    c = completed_at.replace(tzinfo=None) if completed_at.tzinfo else completed_at
    try:
        return (c - s).total_seconds()
    except TypeError:
        return None


# ---------------------------------------------------------------------------
# Verify result
# ---------------------------------------------------------------------------

@dataclass
class VerifyResult:
    """Result of verify_bnk_migration().

    passed = programmed AND reachable.  Default-deny: any exception/missing
    data sets passed=False and adds a reason string.
    """

    programmed: bool = False
    """Control plane: Gateway.status.conditions[Programmed]==True + HTTPRoute Accepted/ResolvedRefs."""
    reachable: bool = False
    """Data plane: K8s service-proxy probe against the backend service returned 2xx."""
    passed: bool = False
    """True IFF programmed AND reachable."""
    reasons: list[str] = field(default_factory=list)
    """Human-readable explanation for each False dimension."""


# ---------------------------------------------------------------------------
# Step definitions (frozen list — single source of truth for the state machine)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MigrationStepDef:
    name: str
    description: str
    requires_confirm: bool = False
    reversible: bool = True


MIGRATION_STEPS: list[MigrationStepDef] = [
    MigrationStepDef(
        name="translate",
        description="Snapshot translated BNK Gateway+HTTPRoute manifest",
        reversible=True,
    ),
    MigrationStepDef(
        name="deploy_bnk",
        description="Apply translated BNK manifest and wait for gateway address",
        reversible=True,
    ),
    MigrationStepDef(
        name="verify",
        description="Verify BNK gateway is programmed and backend is reachable",
        reversible=True,
    ),
    MigrationStepDef(
        name="cutover",
        description="Record operator confirmation of DNS/VIP cutover to BNK (external action)",
        requires_confirm=True,
        reversible=False,  # DNS/VIP switch is irreversible — forge cannot undo external DNS
    ),
    MigrationStepDef(
        name="teardown_old",
        description="Uninstall/delete the old proxy after confirmed cutover",
        requires_confirm=True,
        reversible=False,
    ),
]

# Map step name → definition for quick lookup
_STEP_BY_NAME: dict[str, MigrationStepDef] = {s.name: s for s in MIGRATION_STEPS}


# ---------------------------------------------------------------------------
# Orchestrator service
# ---------------------------------------------------------------------------

class ProxyMigrationService:
    """Gated, reversible proxy-to-BNK migration orchestrator."""

    def __init__(self, db: Session):
        self.db = db

    # --- Create ---

    def create_migration(
        self,
        cluster_id: int,
        source_descriptor: dict[str, Any],
        combined_yaml: str,
        *,
        project_id: int | None = None,
        triggered_by: str = "user",
        teardown_info: dict[str, Any] | None = None,
    ) -> ProxyMigration:
        """Create a new migration run.

        Caller is responsible for pre-fetching the source descriptor and
        running translate_to_bnk() to get combined_yaml.  This keeps the
        orchestrator SOURCE-AGNOSTIC (D-023 P3 reuse contract).

        Args:
            cluster_id: Target cluster ID.
            source_descriptor: Dict describing the source proxy
                (proxy_type, source_kind, class_name, namespace, etc.).
            combined_yaml: Multi-doc YAML from translate_to_bnk().combined_yaml.
            project_id: Optional project scope.
            triggered_by: User identifier.
            teardown_info: Optional teardown hint (helm_release, helm_namespace,
                kubectl_resources). If not provided, teardown step surfaces as
                a manual action (honest — D-017).

        Returns:
            Created ProxyMigration row.
        """
        # Derive BNK gateway coordinates from the combined_yaml headers
        # (gateway name / namespace).  The translate_to_bnk() result carries
        # these; callers may also embed them in source_descriptor.
        gw_name = source_descriptor.get("gateway_name") or _derive_gateway_name(source_descriptor)
        gw_ns = source_descriptor.get("gateway_namespace") or source_descriptor.get("namespace") or "default"

        migration = ProxyMigration(
            cluster_id=cluster_id,
            project_id=project_id,
            source_descriptor=source_descriptor,
            translated_manifest=combined_yaml,
            bnk_gateway_name=gw_name,
            bnk_gateway_namespace=gw_ns,
            teardown_info=teardown_info,
            status=ProxyMigrationStatus.PENDING,
            triggered_by=triggered_by,
        )
        self.db.add(migration)
        self.db.flush()

        # Create step records
        for idx, step_def in enumerate(MIGRATION_STEPS):
            step = ProxyMigrationStep(
                migration_id=migration.id,
                step_index=idx,
                name=step_def.name,
                description=step_def.description,
                status=MigrationStepStatus.PENDING,
                requires_confirm=step_def.requires_confirm,
            )
            self.db.add(step)

        self.db.flush()
        logger.info("Created proxy migration %d: cluster=%d proxy_type=%s",
                    migration.id, cluster_id, source_descriptor.get("proxy_type", "unknown"))
        return migration

    def get_migration(self, migration_id: int) -> ProxyMigration:
        m = self.db.query(ProxyMigration).filter(ProxyMigration.id == migration_id).first()
        if not m:
            raise NotFoundError("ProxyMigration", migration_id)
        return m

    def list_migrations(
        self,
        cluster_id: int,
        *,
        project_id: int | None = None,
    ) -> list[ProxyMigration]:
        q = self.db.query(ProxyMigration).filter(ProxyMigration.cluster_id == cluster_id)
        if project_id is not None:
            q = q.filter(ProxyMigration.project_id == project_id)
        return q.order_by(ProxyMigration.created_at.desc()).all()

    # --- Execute (called by Celery task) ---

    def execute_migration(self, migration_id: int) -> dict:
        """Run the migration state machine.

        Executes steps in order; commits after each step for progress
        persistence.  Steps that require operator confirmation
        (cutover, teardown_old) halt and set AWAITING_CUTOVER /
        AWAITING_TEARDOWN — the caller (Celery task) exits; resume is
        triggered by the confirm-cutover or confirm-teardown API endpoint.

        Returns:
            Summary dict.
        """
        migration = self.get_migration(migration_id)

        # Guard: only reset to IN_PROGRESS if status not already at a gate-waiting state.
        # A re-enqueue (e.g. after confirm_cutover) must not clobber CUTOVER_CONFIRMED.
        _in_progress_guard = {
            ProxyMigrationStatus.IN_PROGRESS,
            ProxyMigrationStatus.CUTOVER_CONFIRMED,
            ProxyMigrationStatus.AWAITING_CUTOVER,
            ProxyMigrationStatus.AWAITING_TEARDOWN,
        }
        if migration.status not in _in_progress_guard:
            migration.status = ProxyMigrationStatus.IN_PROGRESS
        migration.started_at = datetime.now(UTC)
        self.db.commit()

        steps = sorted(migration.steps, key=lambda s: s.step_index)

        for step in steps:
            if step.status in (
                MigrationStepStatus.COMPLETED,
                MigrationStepStatus.SKIPPED,
            ):
                continue  # Resume: skip already-done steps

            if step.status == MigrationStepStatus.AWAITING_CONFIRM:
                # Already waiting — halt
                logger.info("Migration %d halted at %s awaiting confirm", migration_id, step.name)
                return {"status": "awaiting_confirm", "step": step.name}

            # Gate: verify gate — cutover/teardown_old require verify to have passed
            if step.name in ("cutover", "teardown_old"):
                if not self._verify_passed(migration):
                    raise BadRequestError(
                        f"Cannot proceed to '{step.name}': verify gate not passed. "
                        "Run must be in AWAITING_CUTOVER status (reached only after passing verify)."
                    )

            # Run the step
            step.status = MigrationStepStatus.RUNNING
            step.started_at = datetime.now(UTC)
            migration.current_step = step.name
            self.db.commit()

            try:
                result = self._execute_step(migration, step)
            except Exception as exc:
                step.status = MigrationStepStatus.FAILED
                step.error_message = str(exc)
                step.completed_at = datetime.now(UTC)
                if step.started_at:
                    step.duration_seconds = _duration(step.started_at, step.completed_at)

                # Determine run status based on which step failed
                if step.name == "verify":
                    migration.status = ProxyMigrationStatus.VERIFY_FAILED
                else:
                    migration.status = ProxyMigrationStatus.FAILED
                migration.error_message = str(exc)
                migration.error_step = step.name
                self.db.commit()
                logger.error("Migration %d FAILED at step %s: %s", migration_id, step.name, exc)
                raise

            # Step completed normally or halted for confirmation
            if result.get("awaiting_confirm"):
                step.status = MigrationStepStatus.AWAITING_CONFIRM
                step.completed_at = datetime.now(UTC)
                self.db.commit()
                return {"status": "awaiting_confirm", "step": step.name}

            if result.get("verify_failed"):
                # Verify step returned failed — hard stop
                step.status = MigrationStepStatus.FAILED
                step.completed_at = datetime.now(UTC)
                if step.started_at:
                    step.duration_seconds = _duration(step.started_at, step.completed_at)
                migration.status = ProxyMigrationStatus.VERIFY_FAILED
                migration.error_message = result.get("error", "Verify failed")
                migration.error_step = step.name
                self.db.commit()
                logger.warning("Migration %d: verify FAILED — old proxy untouched", migration_id)
                return {"status": "verify_failed", "step": step.name, "result": result.get("verify_result")}

            step.status = MigrationStepStatus.COMPLETED
            step.output_log = result.get("output", "")
            step.result_data = result.get("result_data")
            step.completed_at = datetime.now(UTC)
            step.duration_seconds = _duration(step.started_at, step.completed_at)
            self.db.commit()

        # All steps completed
        migration.status = ProxyMigrationStatus.COMPLETED
        migration.completed_at = datetime.now(UTC)
        if migration.started_at:
            migration.duration_seconds = _duration(migration.started_at, migration.completed_at)
        self.db.commit()
        logger.info("Migration %d COMPLETED", migration_id)
        return {"status": "completed", "migration_id": migration_id}

    def _execute_step(self, migration: ProxyMigration, step: ProxyMigrationStep) -> dict:
        """Dispatch to the per-step executor."""
        dispatcher = {
            "translate": self._step_translate,
            "deploy_bnk": self._step_deploy_bnk,
            "verify": self._step_verify,
            "cutover": self._step_cutover,
            "teardown_old": self._step_teardown_old,
        }
        fn = dispatcher.get(step.name)
        if fn is None:
            raise ValueError(f"Unknown step: {step.name}")
        return fn(migration, step)

    # --- Step executors ---

    def _step_translate(self, migration: ProxyMigration, step: ProxyMigrationStep) -> dict:  # noqa: ARG002
        """Translate step: snapshot is already stored in migration.translated_manifest.

        The manifest was provided at create_migration() time (caller called
        translate_to_bnk() before creating the run).  This step just validates
        the snapshot and records it as done.
        """
        if not migration.translated_manifest:
            raise RuntimeError("translated_manifest is empty — cannot proceed")
        return {"output": "Translation snapshot confirmed", "result_data": None}

    def _step_deploy_bnk(self, migration: ProxyMigration, step: ProxyMigrationStep) -> dict:  # noqa: ARG002
        """Deploy step: apply the translated BNK manifest to the cluster."""
        from services.proxy_deploy_service import ProxyDeployService

        svc = ProxyDeployService(self.db)
        logs: list[str] = []

        def on_status(msg: str) -> None:
            logs.append(msg)
            logger.info("[migration %d deploy_bnk] %s", migration.id, msg)

        gw_name = migration.bnk_gateway_name
        gw_ns = migration.bnk_gateway_namespace

        if not gw_name or not gw_ns:
            raise RuntimeError("bnk_gateway_name/bnk_gateway_namespace not set on migration")

        address = svc.apply_bnk_manifest(
            migration.cluster_id,
            migration.translated_manifest,
            gw_name,
            gw_ns,
            on_status=on_status,
        )

        # Update run status to reflect deploy done (verify must pass before cutover)
        migration.status = ProxyMigrationStatus.AWAITING_VERIFY
        # Store gateway address in source_descriptor for FE display
        desc = dict(migration.source_descriptor or {})
        desc["bnk_gateway_address"] = address
        migration.source_descriptor = desc
        # Note: DO NOT set verify_result or mark verified here (D-017: deployed ≠ serving)

        return {
            "output": "\n".join(logs) or f"Applied {gw_name} to cluster {migration.cluster_id}",
            "result_data": {"gateway_address": address},
        }

    def _step_verify(self, migration: ProxyMigration, step: ProxyMigrationStep) -> dict:  # noqa: ARG002
        """Verify step: probe BNK gateway programmed + backend reachable.

        SAFETY: default-deny — any exception → passed=False.
        Does NOT advance to cutover automatically; sets AWAITING_CUTOVER on pass,
        VERIFY_FAILED on fail (hard stop, old proxy untouched).
        """
        result = verify_bnk_migration(
            self.db,
            cluster_id=migration.cluster_id,
            gw_name=migration.bnk_gateway_name,
            gw_ns=migration.bnk_gateway_namespace,
            source_descriptor=migration.source_descriptor or {},
        )

        # Persist verify result regardless of pass/fail
        migration.verify_result = {
            "programmed": result.programmed,
            "reachable": result.reachable,
            "passed": result.passed,
            "reasons": result.reasons,
        }
        self.db.flush()

        if result.passed:
            migration.status = ProxyMigrationStatus.AWAITING_CUTOVER
            logger.info("Migration %d: verify PASSED — awaiting operator cutover confirmation", migration.id)
            return {
                "output": "Verify passed: BNK gateway programmed and backend reachable",
                "result_data": migration.verify_result,
                "awaiting_confirm": True,  # Halt — next step (cutover) needs operator confirm
            }
        else:
            # Hard stop: verify failed — old proxy is still serving, untouched
            reasons_text = "; ".join(result.reasons) or "unknown"
            error = f"Verify failed: {reasons_text}"
            logger.warning("Migration %d: verify FAILED: %s", migration.id, reasons_text)
            return {
                "verify_failed": True,
                "error": error,
                "verify_result": migration.verify_result,
            }

    def _step_cutover(self, migration: ProxyMigration, step: ProxyMigrationStep) -> dict:  # noqa: ARG002
        """Cutover step: assert verify passed + operator confirmation recorded.

        CUTOVER is NOT automated — forge has no DNS/VIP control.  This step:
        1. ASSERTS persisted verify==passed (fail-closed two-layer gate).
        2. Records the confirmation (set by confirm_cutover() API endpoint).
        3. Surfaces old+new endpoints + checklist for the operator (stored in result_data).
        4. Advances to AWAITING_TEARDOWN.

        If the operator has not yet confirmed (migration.cutover_confirmed_at is None),
        halts with awaiting_confirm=True.
        """
        # Layer 2: executor-level assert (cutover step never reaches here unless
        # migration is AWAITING_CUTOVER, but double-check defensively)
        if not self._verify_passed(migration):
            raise BadRequestError(
                "FAIL-CLOSED GATE: verify has not passed — cannot proceed to cutover. "
                "This should not be reachable; this is a defence-in-depth assertion."
            )

        if not migration.cutover_confirmed_at:
            # Operator has not yet confirmed — halt
            return {"awaiting_confirm": True}

        # Operator confirmed (set by confirm_cutover API route)
        source = migration.source_descriptor or {}
        old_endpoint = source.get("detected_endpoint") or source.get("class_name") or "old proxy"
        new_address = source.get("bnk_gateway_address") or migration.bnk_gateway_name

        checklist = [
            f"Operator confirmed DNS/VIP cutover at {migration.cutover_confirmed_at.isoformat()}",
            f"Old proxy: {old_endpoint} (still running until teardown step)",
            f"New BNK gateway: {migration.bnk_gateway_name} ({new_address})",
            "Traffic should now be flowing to the BNK gateway",
            "If issues arise, repoint DNS/VIP back to the old proxy — forge cannot do this automatically",
        ]

        migration.status = ProxyMigrationStatus.AWAITING_TEARDOWN
        logger.info("Migration %d: cutover confirmed by %s — awaiting teardown",
                    migration.id, migration.cutover_confirmed_by)

        return {
            "output": "\n".join(checklist),
            "result_data": {
                "confirmed_by": migration.cutover_confirmed_by,
                "confirmed_at": migration.cutover_confirmed_at.isoformat(),
                "old_endpoint": old_endpoint,
                "new_endpoint": str(new_address),
                "checklist": checklist,
            },
            # No awaiting_confirm here — cutover step COMPLETES so the loop
            # advances to teardown_old, which gates on teardown_confirmed_at.
        }

    def _step_teardown_old(self, migration: ProxyMigration, step: ProxyMigrationStep) -> dict:  # noqa: ARG002
        """Teardown step: uninstall/delete the old proxy.

        Requires verify passed AND cutover confirmed AND teardown confirmed.
        Gates on teardown_confirmed_at — operator must call confirm_teardown()
        before this step will execute.  If teardown_info is not available or
        incomplete, surfaces as a manual step (honest — D-017).
        """
        # Assertions
        if not self._verify_passed(migration):
            raise BadRequestError("FAIL-CLOSED GATE: verify not passed — cannot teardown")
        if not migration.cutover_confirmed_at:
            raise BadRequestError("Cutover has not been confirmed — cannot teardown old proxy")

        # Teardown confirm gate (operator must explicitly confirm teardown)
        if not migration.teardown_confirmed_at:
            return {"awaiting_confirm": True}

        teardown = migration.teardown_info or {}
        logs: list[str] = []

        if not teardown:
            msg = (
                "No teardown_info available — please uninstall/delete the old proxy manually. "
                f"Source: {migration.source_descriptor.get('proxy_type', 'unknown')} "
                f"{migration.source_descriptor.get('class_name', '')} in namespace "
                f"{migration.source_descriptor.get('namespace', '')}"
            )
            logs.append(msg)
            logger.warning("Migration %d: teardown_info empty — manual teardown required", migration.id)
            return {
                "output": msg,
                "result_data": {"manual_required": True, "reason": "no teardown_info"},
            }

        helm_release = teardown.get("helm_release")
        helm_namespace = teardown.get("helm_namespace", "default")
        kubectl_resources = teardown.get("kubectl_resources", [])

        if helm_release:
            logs.extend(self._teardown_helm(migration, helm_release, helm_namespace))
        elif kubectl_resources:
            logs.extend(self._teardown_kubectl(migration, kubectl_resources))
        else:
            msg = (
                "teardown_info provided but has no helm_release or kubectl_resources — "
                "please uninstall the old proxy manually."
            )
            logs.append(msg)
            return {
                "output": msg,
                "result_data": {"manual_required": True, "reason": "empty teardown_info"},
            }

        logger.info("Migration %d: old proxy teardown complete", migration.id)
        return {"output": "\n".join(logs)}

    def _teardown_helm(self, migration: ProxyMigration, release: str, namespace: str) -> list[str]:
        """Uninstall old proxy via Helm."""
        from services.helm_service import HelmService
        logs = [f"Uninstalling Helm release {release} in {namespace}"]
        try:
            helm = HelmService(self.db)
            helm.uninstall_release(
                cluster_id=migration.cluster_id,
                release_name=release,
                namespace=namespace,
                wait=True,
                timeout="5m",
            )
            logs.append(f"Helm release {release} uninstalled successfully")
        except Exception as exc:
            msg = (
                f"Helm uninstall failed for release {release}: {exc}. "
                "Please uninstall manually."
            )
            logs.append(msg)
            logger.warning("Migration %d: %s", migration.id, msg)
        return logs

    def _teardown_kubectl(self, migration: ProxyMigration, resources: list[str]) -> list[str]:
        """Delete old proxy CRD resources via kubectl."""
        from services.cluster_utils import kubeconfig_for_cluster

        logs = []
        from models import KubernetesCluster
        cluster = self.db.query(KubernetesCluster).filter_by(id=migration.cluster_id).first()
        if not cluster:
            logs.append("Cluster not found — cannot kubectl delete old proxy resources. Please delete manually.")
            return logs

        with kubeconfig_for_cluster(cluster, self.db) as kube_path:
            for res_ns in resources:
                # Expected format: "kind/name namespace"
                parts = res_ns.split()
                resource = parts[0]
                namespace = parts[1] if len(parts) > 1 else "default"
                try:
                    proc = subprocess.run(
                        ["kubectl", "--kubeconfig", kube_path, "-n", namespace,
                         "delete", resource, "--ignore-not-found"],
                        capture_output=True, text=True, timeout=60,
                    )
                    if proc.returncode == 0:
                        logs.append(f"Deleted {resource} in {namespace}")
                    else:
                        logs.append(f"kubectl delete {resource}: {proc.stderr.strip()} (may need manual cleanup)")
                except Exception as exc:
                    logs.append(f"kubectl delete {resource} failed: {exc} — please delete manually")
        return logs

    # --- Confirm cutover (operator-gated) ---

    def confirm_cutover(self, migration_id: int, confirmed_by: str) -> ProxyMigration:
        """Record operator confirmation that the DNS/VIP cutover has been performed.

        LAYER 1 GATE: only allowed when migration.status == AWAITING_CUTOVER.
        Sets migration.cutover_confirmed_at and advances the run by re-enqueuing
        the execute_migration Celery task.

        Args:
            migration_id: The migration run.
            confirmed_by: User identifier of the confirming operator.

        Returns:
            Updated migration row.

        Raises:
            BadRequestError: If migration is not in AWAITING_CUTOVER status.
        """
        migration = self.get_migration(migration_id)

        if migration.status != ProxyMigrationStatus.AWAITING_CUTOVER:
            raise BadRequestError(
                f"Migration {migration_id} is in status '{migration.status}' — "
                "confirm-cutover is only allowed when status is 'awaiting_cutover'. "
                "Verify must pass before cutover can be confirmed."
            )

        migration.cutover_confirmed_by = confirmed_by
        migration.cutover_confirmed_at = datetime.now(UTC)
        migration.status = ProxyMigrationStatus.CUTOVER_CONFIRMED
        # Reset cutover step to PENDING so execute_migration can re-run it
        cutover_step = self._get_step(migration, "cutover")
        if cutover_step:
            cutover_step.status = MigrationStepStatus.PENDING
        self.db.commit()
        logger.info("Migration %d: cutover confirmed by %s", migration_id, confirmed_by)
        return migration

    def confirm_teardown(self, migration_id: int, confirmed_by: str) -> ProxyMigration:
        """Record operator confirmation to proceed with old proxy teardown.

        Only allowed when migration.status == AWAITING_TEARDOWN.
        Resets teardown_old step to PENDING for execute_migration to pick up.

        Args:
            migration_id: The migration run.
            confirmed_by: User identifier.

        Returns:
            Updated migration row.

        Raises:
            BadRequestError: If not in AWAITING_TEARDOWN status.
        """
        migration = self.get_migration(migration_id)

        if migration.status != ProxyMigrationStatus.AWAITING_TEARDOWN:
            raise BadRequestError(
                f"Migration {migration_id} is in status '{migration.status}' — "
                "confirm-teardown is only allowed when status is 'awaiting_teardown'."
            )

        migration.teardown_confirmed_by = confirmed_by
        migration.teardown_confirmed_at = datetime.now(UTC)
        # Reset teardown_old step to PENDING so execute_migration can re-run it
        teardown_step = self._get_step(migration, "teardown_old")
        if teardown_step:
            teardown_step.status = MigrationStepStatus.PENDING
        migration.status = ProxyMigrationStatus.IN_PROGRESS
        self.db.commit()
        logger.info("Migration %d: teardown confirmed by %s", migration_id, confirmed_by)
        return migration

    # --- Abort / rollback ---

    def abort_migration(self, migration_id: int, *, by: str = "user") -> ProxyMigration:
        """Abort a migration.

        Pre-cutover: delete the BNK manifest we applied (old proxy untouched →
        ROLLED_BACK).
        Post-cutover: set ABORTED with a manual-fallback note — forge cannot
        flip DNS/VIP back.
        Terminal states: raises BadRequestError.
        """
        migration = self.get_migration(migration_id)

        if migration.status in ProxyMigrationStatus.terminal_states():
            raise BadRequestError(
                f"Cannot abort migration {migration_id} in terminal status '{migration.status}'"
            )

        # Determine reversibility boundary
        is_post_cutover = migration.status in (
            ProxyMigrationStatus.AWAITING_TEARDOWN,
            ProxyMigrationStatus.CUTOVER_CONFIRMED,
        )

        if is_post_cutover:
            # Cannot auto-rollback post-cutover (DNS/VIP switch is external)
            migration.status = ProxyMigrationStatus.ABORTED
            migration.error_message = (
                f"Aborted by {by} after cutover was confirmed. "
                "DNS/VIP has already been switched to the BNK gateway. "
                "To revert: manually repoint DNS/VIP back to the old proxy. "
                "The old proxy is still running (teardown was not executed)."
            )
            self.db.commit()
            logger.warning("Migration %d: ABORTED post-cutover by %s (manual rollback required)", migration_id, by)
        else:
            # Pre-cutover: delete BNK manifest we applied (new proxy gone, old serving)
            deploy_step = self._get_step(migration, "deploy_bnk")
            if deploy_step and deploy_step.status == MigrationStepStatus.COMPLETED:
                try:
                    self._rollback_deploy(migration)
                    migration.status = ProxyMigrationStatus.ROLLED_BACK
                    migration.error_message = (
                        f"Rolled back by {by}: BNK manifest deleted, old proxy still serving."
                    )
                except Exception as exc:
                    migration.status = ProxyMigrationStatus.ABORTED
                    migration.error_message = (
                        f"Abort by {by}: BNK manifest delete failed ({exc}). "
                        "Please delete the BNK gateway/httproute manually. Old proxy still serving."
                    )
            else:
                migration.status = ProxyMigrationStatus.ABORTED
                migration.error_message = f"Aborted by {by} (no BNK resources to clean up)."

            self.db.commit()
            logger.info("Migration %d: %s by %s", migration_id, migration.status, by)

        return migration

    def _rollback_deploy(self, migration: ProxyMigration) -> None:
        """Delete the BNK manifest we deployed for this migration."""
        from services.proxy_deploy_service import ProxyDeployService
        svc = ProxyDeployService(self.db)
        svc.delete_bnk_manifest(
            migration.cluster_id,
            migration.bnk_gateway_name,
            migration.bnk_gateway_namespace,
        )

    # --- Helpers ---

    def _verify_passed(self, migration: ProxyMigration) -> bool:
        """Default-deny: missing/unknown verify result → False."""
        vr = migration.verify_result
        if not isinstance(vr, dict):
            return False
        return bool(vr.get("passed", False))

    def _get_step(self, migration: ProxyMigration, name: str) -> ProxyMigrationStep | None:
        return next((s for s in migration.steps if s.name == name), None)


# ---------------------------------------------------------------------------
# Verify gate (Slice 3) — default-deny, fail-closed
# ---------------------------------------------------------------------------

def verify_bnk_migration(
    db: Session,
    cluster_id: int,
    gw_name: str | None,
    gw_ns: str | None,
    source_descriptor: dict[str, Any],
    *,
    timeout_sec: int = 120,
    poll_interval: float = 2.0,
    reachability_retries: int = 3,
    reachability_retry_delay: float = 5.0,
) -> VerifyResult:
    """Verify the BNK migration gateway is programmed and reachable.

    DEFAULT-DENY: any exception or missing data returns passed=False.

    A. programmed (control plane):
       - Gateway.status.conditions[Programmed]==True
       - HTTPRoute.status.parents[].conditions[Accepted/ResolvedRefs]==True
       (kubectl-based poll, timeout_sec deadline)

    B. reachable (data plane):
       - K8s service-proxy probe via connect_get_namespaced_service_proxy_with_path
         against the backend service the HTTPRoute resolves to (a2a_discovery pattern)

    Args:
        db: Database session (used to get cluster kubeconfig).
        cluster_id: Target cluster.
        gw_name: Gateway resource name.
        gw_ns: Gateway resource namespace.
        source_descriptor: Source proxy descriptor (used to derive backend service).
        timeout_sec: Timeout for gateway-programmed poll.
        poll_interval: Seconds between gateway status polls.
        reachability_retries: Number of reachability probe attempts.
        reachability_retry_delay: Seconds between reachability retries.

    Returns:
        VerifyResult with passed = programmed AND reachable (default-deny).
    """
    result = VerifyResult()

    if not gw_name or not gw_ns:
        result.reasons.append("Gateway name/namespace not set — cannot verify (default-deny)")
        return result

    try:
        # ----------------------------------------------------------------
        # A. programmed (control plane poll)
        # ----------------------------------------------------------------
        programmed, prog_reasons = _check_gateway_programmed(
            db, cluster_id, gw_name, gw_ns,
            timeout_sec=timeout_sec, poll_interval=poll_interval,
        )
        result.programmed = programmed
        result.reasons.extend(prog_reasons)
    except Exception as exc:  # noqa: BLE001
        result.reasons.append(f"Gateway programmed check failed (default-deny): {exc}")
        return result

    if not result.programmed:
        # No point probing reachability if the data-plane isn't up
        result.reasons.append("Skipping reachability probe: gateway not yet programmed")
        return result

    try:
        # ----------------------------------------------------------------
        # B. reachable (data plane)
        # ----------------------------------------------------------------
        reachable, reach_reasons = _check_backend_reachable(
            db, cluster_id, gw_name, gw_ns,
            source_descriptor=source_descriptor,
            retries=reachability_retries,
            retry_delay=reachability_retry_delay,
        )
        result.reachable = reachable
        result.reasons.extend(reach_reasons)
    except Exception as exc:  # noqa: BLE001
        result.reasons.append(f"Backend reachability probe failed (default-deny): {exc}")
        return result

    result.passed = result.programmed and result.reachable
    return result


def _check_gateway_programmed(
    db: Session,
    cluster_id: int,
    gw_name: str,
    gw_ns: str,
    *,
    timeout_sec: int,
    poll_interval: float,
) -> tuple[bool, list[str]]:
    """Poll Gateway + HTTPRoute status.conditions until Programmed/Accepted.

    Returns (programmed: bool, reasons: list[str]).
    """
    # Get the cluster row
    from models import KubernetesCluster
    from services.cluster_utils import kubeconfig_for_cluster
    cluster = db.query(KubernetesCluster).filter_by(id=cluster_id).first()
    if not cluster:
        return False, [f"Cluster {cluster_id} not found"]

    reasons: list[str] = []
    deadline = time.time() + timeout_sec

    with kubeconfig_for_cluster(cluster, db) as kube_path:
        gw_programmed = False
        route_accepted = False

        while time.time() < deadline:
            # Check Gateway.status.conditions[Programmed]
            if not gw_programmed:
                try:
                    proc = subprocess.run(
                        ["kubectl", "--kubeconfig", kube_path, "-n", gw_ns,
                         "get", "gateway", gw_name, "-o", "json"],
                        capture_output=True, text=True, timeout=15,
                    )
                    if proc.returncode == 0:
                        obj = json.loads(proc.stdout)
                        conditions = _extract_conditions(obj, "status.conditions")
                        gw_programmed = _condition_true(conditions, "Programmed")
                except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception):
                    pass

            # Check HTTPRoute.status.parents[*].conditions[Accepted,ResolvedRefs]
            if not route_accepted:
                try:
                    proc = subprocess.run(
                        ["kubectl", "--kubeconfig", kube_path, "-n", gw_ns,
                         "get", "httproute", gw_name, "-o", "json"],
                        capture_output=True, text=True, timeout=15,
                    )
                    if proc.returncode == 0:
                        obj = json.loads(proc.stdout)
                        parents = (obj.get("status") or {}).get("parents") or []
                        route_accepted = any(
                            _condition_true(_extract_parent_conditions(p), "Accepted") and
                            _condition_true(_extract_parent_conditions(p), "ResolvedRefs")
                            for p in parents
                        ) if parents else False
                except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception):
                    pass

            if gw_programmed and route_accepted:
                return True, []

            time.sleep(poll_interval)

    # Timed out
    if not gw_programmed:
        reasons.append(f"Gateway '{gw_name}' did not reach Programmed=True within {timeout_sec}s")
    if not route_accepted:
        reasons.append(f"HTTPRoute '{gw_name}' Accepted/ResolvedRefs not True within {timeout_sec}s")
    return False, reasons


def _check_backend_reachable(
    db: Session,
    cluster_id: int,
    gw_name: str,
    gw_ns: str,
    source_descriptor: dict[str, Any],
    *,
    retries: int,
    retry_delay: float,
) -> tuple[bool, list[str]]:
    """Probe the backend service the HTTPRoute resolves to.

    Reuses the K8s service-proxy API pattern from a2a_discovery.
    Returns (reachable: bool, reasons: list[str]).
    """
    from models import KubernetesCluster
    from services.kubernetes_service import KubernetesService

    k8s_svc = KubernetesService(db)
    cluster = db.query(KubernetesCluster).filter_by(id=cluster_id).first()
    if not cluster:
        return False, [f"Cluster {cluster_id} not found — cannot probe reachability"]
    try:
        api_client = k8s_svc.load_kubeconfig(cluster)
    except Exception as exc:  # noqa: BLE001
        return False, [f"Cannot build K8s API client for cluster {cluster_id}: {exc}"]

    from kubernetes import client as k8s_client
    core_v1 = k8s_client.CoreV1Api(api_client)

    # Derive backend service from source_descriptor or by inspecting the HTTPRoute
    backend_svc, backend_ns, backend_port = _derive_backend_service(
        db, cluster_id, gw_name, gw_ns, source_descriptor,
    )
    if not backend_svc:
        return False, ["Cannot derive backend service from HTTPRoute — skipping reachability probe (default-deny)"]

    proxy_name = f"{backend_svc}:{backend_port}"
    health_path = source_descriptor.get("health_path", "/")

    for attempt in range(max(1, retries)):
        try:
            core_v1.connect_get_namespaced_service_proxy_with_path(
                name=proxy_name,
                namespace=backend_ns,
                path=health_path.lstrip("/"),
            )
            # 2xx → reachable
            return True, []
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if attempt < retries - 1:
                time.sleep(retry_delay)

    return False, [f"Backend service {proxy_name} in {backend_ns} not reachable: {last_error}"]


def _derive_backend_service(
    db: Session,
    cluster_id: int,
    gw_name: str,
    gw_ns: str,
    source_descriptor: dict[str, Any],
) -> tuple[str | None, str, int]:
    """Derive (service_name, namespace, port) from the HTTPRoute backendRefs.

    Falls back to source_descriptor hints if kubectl fails.
    Returns (None, "", 80) on failure (caller treats as default-deny).
    """
    from models import KubernetesCluster
    from services.cluster_utils import kubeconfig_for_cluster

    cluster = db.query(KubernetesCluster).filter_by(id=cluster_id).first()
    if not cluster:
        return None, "", 80

    try:
        with kubeconfig_for_cluster(cluster, db) as kube_path:
            proc = subprocess.run(
                ["kubectl", "--kubeconfig", kube_path, "-n", gw_ns,
                 "get", "httproute", gw_name, "-o", "json"],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0:
                obj = json.loads(proc.stdout)
                rules = (obj.get("spec") or {}).get("rules") or []
                for rule in rules:
                    for backend in (rule.get("backendRefs") or []):
                        svc_name = backend.get("name")
                        svc_ns = backend.get("namespace") or gw_ns
                        svc_port = int(backend.get("port") or 80)
                        if svc_name:
                            return svc_name, svc_ns, svc_port
    except Exception:  # noqa: BLE001
        pass

    # Fallback from source_descriptor
    svc_hint = source_descriptor.get("backend_service")
    if svc_hint:
        return (
            svc_hint,
            source_descriptor.get("namespace") or gw_ns,
            int(source_descriptor.get("backend_port") or 80),
        )
    return None, "", 80


# ---------------------------------------------------------------------------
# Condition inspection helpers
# ---------------------------------------------------------------------------

def _extract_conditions(obj: dict, path: str) -> list[dict]:
    """Walk a dotted path to a conditions list."""
    parts = path.split(".")
    cur: Any = obj
    for part in parts:
        if not isinstance(cur, dict):
            return []
        cur = cur.get(part) or []
    return cur if isinstance(cur, list) else []


def _extract_parent_conditions(parent: dict) -> list[dict]:
    return parent.get("conditions") or []


def _condition_true(conditions: list[dict], type_: str) -> bool:
    """Return True if a condition with the given type has status=='True'."""
    for c in conditions:
        if c.get("type") == type_ and str(c.get("status", "")).lower() == "true":
            return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_gateway_name(source_descriptor: dict[str, Any]) -> str:
    """Derive a stable gateway name from the source descriptor."""
    class_name = source_descriptor.get("class_name") or "migration"
    proxy_type = source_descriptor.get("proxy_type") or "proxy"
    # Sanitize: lowercase, replace non-alphanum with dash
    import re
    raw = f"forge-{proxy_type}-{class_name}"
    name = re.sub(r"[^a-z0-9-]", "-", raw.lower())
    name = re.sub(r"-+", "-", name).strip("-")
    return name[:63]
