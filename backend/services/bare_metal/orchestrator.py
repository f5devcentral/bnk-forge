"""Bare-metal deployment orchestration — step registries + state machine."""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from models.bare_metal import BareMetalDeployment, BareMetalHost, DeploymentStep
from models.enums import (
    BareMetalDeploymentStatus,
    DeploymentPhase,
    DeploymentStepStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Step Definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepDefinition:
    """Definition of a deployment step."""

    phase: DeploymentPhase
    name: str  # Machine name, e.g., "flash_dpu"
    description: str  # Human description
    connectivity_risk: bool = False  # May disrupt SSH?
    connectivity_mechanism: str | None = None  # Which preservation mech
    # Composability metadata:
    estimated_duration_seconds: int = 60  # Rough estimate for UI progress
    prerequisites: list[str] = field(default_factory=list)  # Step names that must complete first
    idempotent: bool = False  # Can safely re-run (probe-before-act)


# ---------------------------------------------------------------------------
# Step registries per topology
# ---------------------------------------------------------------------------

REGULAR_STEPS: list[StepDefinition] = [
    # Phase 1: DPU Provisioning
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "probe_dpu_state", "Probe DPU current state",
        estimated_duration_seconds=30, prerequisites=[], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "flash_dpu", "Flash DPU with BFB image",
        connectivity_risk=False,
        estimated_duration_seconds=600, prerequisites=["probe_dpu_state"], idempotent=False
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "wait_dpu_ready", "Wait for DPU to become SSH-reachable",
        estimated_duration_seconds=300, prerequisites=["flash_dpu"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "install_dpu_prereqs", "Install containerd, runc, kubeadm on DPU",
        estimated_duration_seconds=120, prerequisites=["wait_dpu_ready"], idempotent=True
    ),
    # Phase 2: Host K8s Setup
    StepDefinition(
        DeploymentPhase.PHASE_2_K8S, "install_k8s_prereqs", "Install kubeadm, kubelet, kubectl on host",
        estimated_duration_seconds=120, prerequisites=[], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_2_K8S, "kubeadm_init", "Initialize K8s control plane",
        estimated_duration_seconds=180, prerequisites=["install_k8s_prereqs"], idempotent=False
    ),
    StepDefinition(
        DeploymentPhase.PHASE_2_K8S, "install_cni", "Install Calico CNI",
        estimated_duration_seconds=60, prerequisites=["kubeadm_init"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_2_K8S, "install_cert_manager", "Install cert-manager",
        estimated_duration_seconds=60, prerequisites=["kubeadm_init"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_2_K8S, "install_sriov", "Install SR-IOV network operator",
        estimated_duration_seconds=60, prerequisites=["kubeadm_init"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_2_K8S, "install_multus", "Install Multus CNI",
        estimated_duration_seconds=60, prerequisites=["kubeadm_init"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_2_K8S, "install_storage", "Install storage provisioner",
        estimated_duration_seconds=60, prerequisites=["kubeadm_init"], idempotent=True
    ),
    # Phase 3: DPU Join
    StepDefinition(
        DeploymentPhase.PHASE_3_JOIN, "kubeadm_join", "Join DPU node to cluster",
        estimated_duration_seconds=120, prerequisites=["install_k8s_prereqs"], idempotent=False
    ),
    StepDefinition(
        DeploymentPhase.PHASE_3_JOIN, "label_dpu_node", "Label DPU node (app=f5-tmm)",
        estimated_duration_seconds=10, prerequisites=["kubeadm_join"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_3_JOIN, "taint_dpu_node", "Taint DPU node (dpu=true:NoSchedule)",
        estimated_duration_seconds=10, prerequisites=["kubeadm_join"], idempotent=True
    ),
    # Phase 4: Platform
    StepDefinition(
        DeploymentPhase.PHASE_4_PLATFORM, "install_flo", "Install FLO via Helm",
        estimated_duration_seconds=180, prerequisites=["kubeadm_join"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_4_PLATFORM, "deploy_bnk_cr", "Deploy BNK CR (CNEInstance/BNKGatewayClass)",
        estimated_duration_seconds=120, prerequisites=["install_flo"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_4_PLATFORM, "install_cwc_certs", "Install CWC certificates",
        estimated_duration_seconds=30, prerequisites=["deploy_bnk_cr"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_4_PLATFORM, "install_otel_certs", "Install OTEL certificates",
        estimated_duration_seconds=30, prerequisites=["deploy_bnk_cr"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_4_PLATFORM, "deploy_observability", "Deploy observability stack",
        estimated_duration_seconds=120, prerequisites=["deploy_bnk_cr"], idempotent=True
    ),
]

# Phase 2-4 reusable slice (from install_k8s_prereqs onward)
_SHARED_PHASE_2_4 = REGULAR_STEPS[4:]

BF3_STEPS: list[StepDefinition] = [
    # Phase 1: DPU Provisioning (with connectivity preservation)
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "probe_dpu_state", "Probe DPU current state",
        estimated_duration_seconds=30, prerequisites=[], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "stage_vf_netplan",
        "Pre-stage VF netplan config",
        connectivity_risk=False,
        estimated_duration_seconds=30, prerequisites=["probe_dpu_state"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "install_fallback_timer",
        "Install connectivity fallback timer",
        connectivity_risk=False,
        estimated_duration_seconds=30, prerequisites=["probe_dpu_state"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "flash_dpu",
        "Flash DPU with BFB image (mode change via bfb_post_install)",
        connectivity_risk=True,
        connectivity_mechanism="pre_staged_netplan",
        estimated_duration_seconds=600, prerequisites=["probe_dpu_state"], idempotent=False
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "wait_connectivity_restore",
        "Wait for host connectivity via VF",
        estimated_duration_seconds=300, prerequisites=["flash_dpu"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "install_dpu_prereqs", "Install containerd, runc, kubeadm on DPU",
        estimated_duration_seconds=120, prerequisites=["wait_connectivity_restore"], idempotent=True
    ),
    # Phase 2-4: same as REGULAR
    *_SHARED_PHASE_2_4,
]

BF3_IPMI_STEPS: list[StepDefinition] = [
    # Phase 1: DPU Provisioning (mlxconfig + IPMI power cycle)
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "probe_dpu_state", "Probe DPU current state",
        estimated_duration_seconds=30, prerequisites=[], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "set_nic_mode_mlxconfig", "Set NIC mode via mlxconfig",
        estimated_duration_seconds=30, prerequisites=["probe_dpu_state"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "deposit_phase_c",
        "Deposit Phase C bootstrap service",
        connectivity_risk=False,
        estimated_duration_seconds=60, prerequisites=["set_nic_mode_mlxconfig"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "ipmi_power_cycle",
        "IPMI cold boot",
        connectivity_risk=True,
        connectivity_mechanism="phase_c_bootstrap",
        estimated_duration_seconds=120, prerequisites=["deposit_phase_c"], idempotent=False
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "wait_connectivity_restore",
        "Wait for host connectivity via VF",
        estimated_duration_seconds=300, prerequisites=["ipmi_power_cycle"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "flash_dpu",
        "Flash DPU with BFB image",
        connectivity_risk=True,
        connectivity_mechanism="post_flash_watcher",
        estimated_duration_seconds=600, prerequisites=["wait_connectivity_restore"], idempotent=False
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "wait_dpu_ready", "Wait for DPU to become SSH-reachable",
        estimated_duration_seconds=300, prerequisites=["flash_dpu"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "install_dpu_prereqs", "Install containerd, runc, kubeadm on DPU",
        estimated_duration_seconds=120, prerequisites=["wait_dpu_ready"], idempotent=True
    ),
    # Phase 2-4: same as REGULAR
    *_SHARED_PHASE_2_4,
]

BMC_STEPS: list[StepDefinition] = [
    # Phase 1: DPU Provisioning (Redfish BIOS + Redfish cold boot)
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "probe_dpu_state", "Probe DPU current state",
        estimated_duration_seconds=30, prerequisites=[], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "set_nic_mode_redfish",
        "Set NIC mode via Redfish BIOS attribute",
        estimated_duration_seconds=30, prerequisites=["probe_dpu_state"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "deposit_phase_c",
        "Deposit Phase C bootstrap service",
        connectivity_risk=False,
        estimated_duration_seconds=60, prerequisites=["set_nic_mode_redfish"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "redfish_cold_boot",
        "Redfish cold boot (GracefulShutdown → On)",
        connectivity_risk=True,
        connectivity_mechanism="phase_c_bootstrap",
        estimated_duration_seconds=120, prerequisites=["deposit_phase_c"], idempotent=False
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "wait_connectivity_restore",
        "Wait for host connectivity via VF",
        estimated_duration_seconds=300, prerequisites=["redfish_cold_boot"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "flash_dpu",
        "Flash DPU with BFB image",
        connectivity_risk=True,
        connectivity_mechanism="post_flash_watcher",
        estimated_duration_seconds=600, prerequisites=["wait_connectivity_restore"], idempotent=False
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "wait_dpu_ready", "Wait for DPU to become SSH-reachable",
        estimated_duration_seconds=300, prerequisites=["flash_dpu"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "install_dpu_prereqs", "Install containerd, runc, kubeadm on DPU",
        estimated_duration_seconds=120, prerequisites=["wait_dpu_ready"], idempotent=True
    ),
    # Phase 2-4: same as REGULAR
    *_SHARED_PHASE_2_4,
]

DUAL_DPU_OBMC_STEPS: list[StepDefinition] = [
    # Phase 1: DPU Provisioning (flash via DPU OpenBMC rshim)
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "probe_dpu_state", "Probe DPU current state (multi-DPU + BMC rshim)",
        estimated_duration_seconds=45, prerequisites=[], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "flash_dpu", "Flash DPU via BMC rshim (SFTP to /dev/rshim0/boot)",
        connectivity_risk=False,
        estimated_duration_seconds=600, prerequisites=["probe_dpu_state"], idempotent=False
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU,
        "wait_dpu_ready",
        "Wait for DPU reachable via IPv6 link-local on host PF",
        estimated_duration_seconds=300, prerequisites=["flash_dpu"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "setup_dpu_networking",
        "Configure DPU internet access via IPv6 link-local NAT through host",
        estimated_duration_seconds=30, prerequisites=["wait_dpu_ready"], idempotent=True
    ),
    StepDefinition(
        DeploymentPhase.PHASE_1_DPU, "install_dpu_prereqs", "Install containerd, runc, kubeadm on DPU",
        estimated_duration_seconds=120, prerequisites=["setup_dpu_networking"], idempotent=True
    ),
    # Phase 2-4: same as REGULAR
    *_SHARED_PHASE_2_4,
]

TOPOLOGY_STEPS: dict[str, list[StepDefinition]] = {
    "regular": REGULAR_STEPS,
    "bf3": BF3_STEPS,
    "bf3_ipmi": BF3_IPMI_STEPS,
    "bmc": BMC_STEPS,
    "dual_dpu_obmc": DUAL_DPU_OBMC_STEPS,
}


def get_steps_for_topology(topology: str) -> list[StepDefinition]:
    """Get the step list for a given topology.

    Raises ValueError if topology is unknown.
    """
    steps = TOPOLOGY_STEPS.get(topology)
    if steps is None:
        raise ValueError(f"Unknown topology: {topology}. Valid: {list(TOPOLOGY_STEPS.keys())}")
    return steps


# ---------------------------------------------------------------------------
# Deployment Orchestrator Service
# ---------------------------------------------------------------------------


class BareMetalDeploymentService:
    """Multi-phase bare-metal deployment orchestrator.

    Manages the lifecycle of deployments:
    - Create deployment with step plan from topology
    - Execute steps sequentially (within Celery task)
    - Resume from failed/paused step
    - Cancel running deployment

    Each step delegates to the appropriate phase service (Phase 1-4).
    Phase services are NOT implemented yet — the orchestrator uses
    a dispatcher that can be extended as services are built.
    """

    def __init__(self, db: Session):
        self.db = db

    # --- CRUD ---

    def create_deployment(
        self,
        host_id: int,
        project_id: int,
        *,
        resume_from_step: int | None = None,
        triggered_by: str = "user",
        selected_phases: list[str] | None = None,
        selected_steps: list[str] | None = None,
    ) -> BareMetalDeployment:
        """Create a new deployment for a host.

        Validates:
        - Host exists and belongs to project
        - No active deployment for this host
        - Topology is set on the host

        Creates DeploymentStep records for each step in the topology's plan.
        """
        host = self._get_host_for_project(host_id, project_id)

        if not host.topology:
            from core.errors import BadRequestError

            raise BadRequestError("Host topology not set — run discovery first")

        # Topology-specific pre-deployment validation
        self._validate_topology_prerequisites(host)

        # Check for active deployment
        active = (
            self.db.query(BareMetalDeployment)
            .filter(
                BareMetalDeployment.host_id == host_id,
                BareMetalDeployment.status.notin_(
                    [s.value for s in BareMetalDeploymentStatus.terminal_states()]
                ),
            )
            .first()
        )
        if active:
            from core.errors import BadRequestError

            raise BadRequestError(
                f"Host {host_id} already has an active deployment (id={active.id}, status={active.status})"
            )

        # Get steps for topology
        step_defs = get_steps_for_topology(host.topology)

        # Determine which steps are selected
        selected_step_names = self._determine_selected_steps(
            step_defs, selected_phases, selected_steps
        )

        # Snapshot version profile
        profile_snapshot = None
        if host.version_profile:
            profile_snapshot = {
                "name": host.version_profile.name,
                "bnk_manifest_version": host.version_profile.bnk_manifest_version,
                "k8s_version": host.version_profile.k8s_version,
                "doca_version": host.version_profile.doca_version,
                "flo_version": host.version_profile.flo_version,
            }

        # Create deployment
        deployment = BareMetalDeployment(
            host_id=host_id,
            project_id=project_id,
            topology=host.topology,
            status=BareMetalDeploymentStatus.PENDING,
            version_profile_snapshot=profile_snapshot,
            resume_from_step=resume_from_step,
            triggered_by=triggered_by,
            selected_phases=selected_phases,
            selected_steps=selected_steps,
        )
        self.db.add(deployment)
        self.db.flush()  # Get deployment.id

        # Create step records — non-selected steps get SKIPPED
        for idx, step_def in enumerate(step_defs):
            is_selected = step_def.name in selected_step_names
            step = DeploymentStep(
                deployment_id=deployment.id,
                step_index=idx,
                phase=step_def.phase.value,
                name=step_def.name,
                description=step_def.description,
                status=DeploymentStepStatus.PENDING if is_selected else DeploymentStepStatus.SKIPPED,
                connectivity_risk=step_def.connectivity_risk,
                connectivity_mechanism=step_def.connectivity_mechanism,
            )
            self.db.add(step)

        self.db.flush()
        logger.info(
            "Created deployment %s for host %d (%s topology, %d steps)",
            deployment.id,
            host_id,
            host.topology,
            len(step_defs),
        )
        return deployment

    def get_deployment(self, deployment_id: int) -> BareMetalDeployment:
        """Get a deployment by ID."""
        deployment = (
            self.db.query(BareMetalDeployment)
            .filter(
                BareMetalDeployment.id == deployment_id,
            )
            .first()
        )
        if not deployment:
            from core.errors import NotFoundError

            raise NotFoundError("BareMetalDeployment", str(deployment_id))
        return deployment

    def list_deployments(self, project_id: int, host_id: int | None = None) -> list[BareMetalDeployment]:
        """List deployments for a project, optionally filtered by host."""
        query = self.db.query(BareMetalDeployment).filter(
            BareMetalDeployment.project_id == project_id,
        )
        if host_id:
            query = query.filter(BareMetalDeployment.host_id == host_id)
        return query.order_by(BareMetalDeployment.created_at.desc()).all()

    # --- Execution ---

    def execute_deployment(self, deployment_id: int, task: object = None) -> dict:  # noqa: ARG002
        """Execute a deployment — called by Celery task.

        Iterates through steps, executing each one. Commits after each step
        for progress persistence. Skips steps that probes detect as already done.

        Args:
            deployment_id: The deployment to execute
            task: Optional Celery task instance for WebSocket updates

        Returns:
            Summary dict with status and step counts
        """
        deployment = self.get_deployment(deployment_id)

        # Transition to IN_PROGRESS
        deployment.status = BareMetalDeploymentStatus.IN_PROGRESS
        deployment.started_at = datetime.now(UTC)
        self.db.commit()

        steps = sorted(deployment.steps, key=lambda s: s.step_index)
        start_index = deployment.resume_from_step or 0

        completed_count = 0
        skipped_count = 0

        try:
            for step in steps[start_index:]:
                # Execute step
                step.status = DeploymentStepStatus.RUNNING
                step.started_at = datetime.now(UTC)
                deployment.current_step_index = step.step_index
                deployment.current_phase = step.phase
                self.db.commit()

                try:
                    result = self._execute_step(deployment, step)

                    if result.get("skipped"):
                        step.status = DeploymentStepStatus.SKIPPED
                        step.already_done = True
                        skipped_count += 1
                    else:
                        step.status = DeploymentStepStatus.COMPLETED
                        step.exit_code = result.get("exit_code", 0)
                        step.output_log = result.get("output", "")
                        completed_count += 1

                except Exception as e:
                    step.status = DeploymentStepStatus.FAILED
                    step.error_message = str(e)
                    step.completed_at = datetime.now(UTC)
                    if step.started_at:
                        step.duration_seconds = (step.completed_at - step.started_at).total_seconds()

                    deployment.status = BareMetalDeploymentStatus.FAILED
                    deployment.error_message = str(e)
                    deployment.error_phase = step.phase
                    deployment.error_step_index = step.step_index
                    self.db.commit()

                    logger.error(
                        "Deployment %d failed at step %d (%s): %s",
                        deployment_id,
                        step.step_index,
                        step.name,
                        e,
                    )
                    raise

                step.completed_at = datetime.now(UTC)
                if step.started_at:
                    step.duration_seconds = (step.completed_at - step.started_at).total_seconds()
                self.db.commit()

            # All steps completed
            deployment.status = BareMetalDeploymentStatus.COMPLETED
            deployment.completed_at = datetime.now(UTC)
            if deployment.started_at:
                deployment.duration_seconds = (deployment.completed_at - deployment.started_at).total_seconds()
            self.db.commit()

            logger.info(
                "Deployment %d completed: %d steps completed, %d skipped",
                deployment_id,
                completed_count,
                skipped_count,
            )

            return {
                "status": "completed",
                "deployment_id": deployment_id,
                "completed": completed_count,
                "skipped": skipped_count,
                "total": len(steps),
            }

        except Exception:
            # Already handled in the step loop — just re-raise
            raise

    def _execute_step(self, deployment: BareMetalDeployment, step: DeploymentStep) -> dict:  # noqa: ARG002
        """Execute a single deployment step.

        Dispatches to the appropriate phase service based on step name.
        Phase services are plugged in as they're implemented.

        Returns dict with keys: exit_code, output, skipped
        """
        # Phase service dispatch — to be filled in as phase services are built
        logger.info("Executing step %d: %s (%s)", step.step_index, step.name, step.description)

        # TODO: Dispatch to phase services:
        # - Phase 1 steps → Phase1DpuService
        # - Phase 2 steps → Phase2K8sService
        # - Phase 3 steps → Phase3JoinService
        # - Phase 4 steps → Phase4PlatformService

        return {
            "exit_code": 0,
            "output": f"Step {step.name} executed (placeholder)",
            "skipped": False,
        }

    # --- Resume/Cancel ---

    def resume_deployment(self, deployment_id: int, from_step: int | None = None) -> BareMetalDeployment:
        """Resume a failed or paused deployment.

        If from_step is None, resumes from the failed step.
        """
        deployment = self.get_deployment(deployment_id)

        if deployment.status not in (
            BareMetalDeploymentStatus.FAILED,
            BareMetalDeploymentStatus.PAUSED,
        ):
            from core.errors import BadRequestError

            raise BadRequestError(
                f"Cannot resume deployment in status '{deployment.status}' — must be 'failed' or 'paused'"
            )

        # Determine resume point
        if from_step is not None:
            deployment.resume_from_step = from_step
        elif deployment.error_step_index is not None:
            deployment.resume_from_step = deployment.error_step_index
        else:
            deployment.resume_from_step = 0

        # Reset error state
        deployment.error_message = None
        deployment.error_phase = None
        deployment.error_step_index = None
        deployment.status = BareMetalDeploymentStatus.PENDING

        # Reset failed steps to pending
        for step in deployment.steps:
            if step.step_index >= deployment.resume_from_step and step.status == DeploymentStepStatus.FAILED:
                step.status = DeploymentStepStatus.PENDING
                step.error_message = None
                step.output_log = None
                step.exit_code = None
                step.started_at = None
                step.completed_at = None
                step.duration_seconds = None

        self.db.flush()
        logger.info("Deployment %d set to resume from step %d", deployment_id, deployment.resume_from_step)
        return deployment

    def cancel_deployment(self, deployment_id: int) -> BareMetalDeployment:
        """Cancel a running or pending deployment."""
        deployment = self.get_deployment(deployment_id)

        if deployment.status in BareMetalDeploymentStatus.terminal_states():
            from core.errors import BadRequestError

            raise BadRequestError(
                f"Cannot cancel deployment in terminal status '{deployment.status}'"
            )

        deployment.status = BareMetalDeploymentStatus.CANCELLED
        deployment.completed_at = datetime.now(UTC)
        if deployment.started_at:
            deployment.duration_seconds = (deployment.completed_at - deployment.started_at).total_seconds()

        # Revoke Celery task if running
        if deployment.celery_task_id:
            try:
                from celery_app import celery_app

                celery_app.control.revoke(deployment.celery_task_id, terminate=True)
                logger.info(
                    "Revoked Celery task %s for deployment %d",
                    deployment.celery_task_id,
                    deployment_id,
                )
            except Exception as e:
                logger.warning("Failed to revoke Celery task: %s", e)

        self.db.flush()
        logger.info("Deployment %d cancelled", deployment_id)
        return deployment

    # --- Plan Preview & Selection Helpers ---

    @staticmethod
    def _determine_selected_steps(
        step_defs: list[StepDefinition],
        selected_phases: list[str] | None,
        selected_steps: list[str] | None,
    ) -> set[str]:
        """Determine which step names are selected.

        Priority: selected_steps > selected_phases > all.
        """
        if selected_steps is not None:
            # Explicit step selection overrides everything
            return set(selected_steps)

        if selected_phases is not None:
            # Select all steps in the given phases
            return {
                s.name for s in step_defs
                if s.phase.value in selected_phases
            }

        # Default: all steps
        return {s.name for s in step_defs}

    def _validate_phase_prerequisites(
        self,
        host: BareMetalHost,
        selected_phases: list[str] | None,
    ) -> list[str]:
        """Validate that prerequisites are met for selected phases.

        Returns list of warning messages (not errors — expert override allowed).
        """
        if not selected_phases:
            return []

        warnings = []

        phases_set = set(selected_phases)

        # Phase 2 (K8s) needs DPU provisioned
        if "phase_2_k8s" in phases_set and "phase_1_dpu" not in phases_set:
            if host.rshim_present is False:
                warnings.append("Phase 2 selected without Phase 1, but no DPU detected (rshim not present)")

        # Phase 3 (Join) needs K8s running
        if "phase_3_join" in phases_set and "phase_2_k8s" not in phases_set:
            if not (host.k8s_info and host.k8s_info.get("running")):
                warnings.append("Phase 3 selected without Phase 2, but K8s is not running on host")

        # Phase 4 (Platform) needs K8s cluster linked
        if "phase_4_platform" in phases_set and "phase_3_join" not in phases_set:
            if not host.kubernetes_cluster_id:
                warnings.append("Phase 4 selected without Phase 3, but no K8s cluster is linked to this host")

        return warnings

    def _get_host_for_project(self, host_id: int, project_id: int) -> BareMetalHost:
        """Get a host by ID and validate it belongs to the project."""
        host = (
            self.db.query(BareMetalHost)
            .filter(
                BareMetalHost.id == host_id,
                BareMetalHost.project_id == project_id,
            )
            .first()
        )
        if not host:
            from core.errors import NotFoundError
            raise NotFoundError("BareMetalHost", str(host_id))
        return host

    def _validate_topology_prerequisites(self, host: BareMetalHost) -> None:
        """Validate topology-specific prerequisites before deployment creation.

        For dual_dpu_obmc:
        - bmc_ip is required (flash via BMC rshim)
        - deploy_dpu_pci_address must be set (auto-detected or user-provided)
        """
        from core.errors import BadRequestError

        if host.topology == "dual_dpu_obmc":
            missing: list[str] = []

            if not host.bmc_ip:
                missing.append("bmc_ip (required for BMC rshim access)")

            if not host.deploy_dpu_pci_address:
                missing.append("deploy_dpu_pci_address (run discovery to auto-detect or set manually)")

            if missing:
                raise BadRequestError(
                    f"dual_dpu_obmc deployment requires: {', '.join(missing)}. "
                    "Configure these in host settings before creating deployment."
                )

    def preview_deployment(
        self,
        host_id: int,
        project_id: int,
        selected_phases: list[str] | None = None,
        selected_steps: list[str] | None = None,
    ) -> dict:
        """Preview a deployment plan without creating it.

        Returns the proposed step plan with selection status and prerequisite warnings.
        """
        host = self._get_host_for_project(host_id, project_id)

        if not host.topology:
            from core.errors import BadRequestError
            raise BadRequestError("Host topology not set — run discovery first")

        step_defs = get_steps_for_topology(host.topology)
        selected_step_names = self._determine_selected_steps(
            step_defs, selected_phases, selected_steps
        )

        prerequisite_warnings = self._validate_phase_prerequisites(host, selected_phases)

        steps = []
        total_duration = 0
        selected_count = 0
        for idx, step_def in enumerate(step_defs):
            is_selected = step_def.name in selected_step_names
            if is_selected:
                total_duration += step_def.estimated_duration_seconds
                selected_count += 1
            steps.append({
                "step_index": idx,
                "phase": step_def.phase.value,
                "name": step_def.name,
                "description": step_def.description,
                "connectivity_risk": step_def.connectivity_risk,
                "estimated_duration_seconds": step_def.estimated_duration_seconds,
                "prerequisites": list(step_def.prerequisites),
                "idempotent": step_def.idempotent,
                "selected": is_selected,
            })

        return {
            "topology": host.topology,
            "total_steps": len(step_defs),
            "selected_steps": selected_count,
            "estimated_total_duration_seconds": total_duration,
            "steps": steps,
            "prerequisite_warnings": prerequisite_warnings,
        }
