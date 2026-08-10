"""Project-scoped on-demand SSH discovery service."""

import base64
import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

import paramiko
import yaml
from paramiko.ssh_exception import AuthenticationException, NoValidConnectionsError, SSHException
from sqlalchemy.orm import Session

from core.config import settings
from core.encryption import decrypt_value, encrypt_value
from core.errors import BadRequestError, NotFoundError
from database import SessionLocal
from models import Project, SSHCredential
from models.discovery import DiscoveredNode, DiscoveryJob
from models.enums import DiscoveredNodeStatus, DiscoveryJobStatus
from schemas.discovery import DiscoveryJobResponse
from services.shared.hardware_probes import (
    detect_dpus as _detect_dpus,
)
from services.shared.hardware_probes import (
    detect_network_interfaces as _detect_network_interfaces,
)
from services.shared.hardware_probes import (
    ssh_exec as _ssh_exec,
)
from services.ssh.paramiko_utils import load_private_key_from_content

logger = logging.getLogger(__name__)

SSH_CONNECT_TIMEOUT = 15

_active_jobs: dict[int, threading.Event] = {}
_active_jobs_lock = threading.Lock()


class DiscoveryService:
    """Service for project-scoped, on-demand bare-metal discovery via SSH."""

    def __init__(self, db: Session):
        self.db = db

    def trigger_discovery(
        self,
        *,
        project_id: int,
        payload: dict[str, Any],
        created_by: str | None,
    ) -> DiscoveryJobResponse:
        """Create a discovery job in PENDING state. Execution is started separately via start_background_execution()."""
        self._get_project(project_id)

        nodes_data = payload.get("nodes") or []
        if not nodes_data:
            raise BadRequestError("At least one discovery node is required")

        ips = [str(node.get("ip_address", "")).strip() for node in nodes_data]
        if any(not ip for ip in ips):
            raise BadRequestError("Each discovery node requires a non-empty ip_address")
        if len(set(ips)) != len(ips):
            raise BadRequestError("Duplicate discovery node IP addresses are not allowed")

        # Validate credential references up front so callers get truthful 4xx errors
        # rather than late FK failures during node persistence.
        credential_ids: set[int] = set()
        shared_credential_id = payload.get("ssh_credential_id")
        if shared_credential_id is not None:
            credential_ids.add(int(shared_credential_id))
        for node_data in nodes_data:
            node_credential_id = node_data.get("ssh_credential_id")
            if node_credential_id is not None:
                credential_ids.add(int(node_credential_id))
        for credential_id in credential_ids:
            self._resolve_ssh_credential(credential_id)

        job = DiscoveryJob(
            project_id=project_id,
            ssh_credential_id=payload.get("ssh_credential_id"),
            ssh_username=payload.get("ssh_username"),
            ssh_port=payload.get("ssh_port") or 22,
            ssh_auth_type=payload.get("ssh_auth_type"),
            status=DiscoveryJobStatus.PENDING.value,
            total_nodes=len(nodes_data),
            completed_nodes=0,
            failed_nodes=0,
            created_at=datetime.now(UTC),
            created_by=created_by,
        )
        if payload.get("ssh_password"):
            job.ssh_auth_type = job.ssh_auth_type or "password"
            job.ssh_password_encrypted = encrypt_value(payload["ssh_password"])
        if payload.get("ssh_private_key"):
            job.ssh_auth_type = job.ssh_auth_type or "key"
            job.ssh_key_encrypted = encrypt_value(payload["ssh_private_key"])

        self.db.add(job)
        self.db.flush()

        for node_data in nodes_data:
            node = DiscoveredNode(
                discovery_job_id=job.id,
                ip_address=str(node_data.get("ip_address", "")).strip(),
                ssh_credential_id=node_data.get("ssh_credential_id"),
                ssh_username=node_data.get("ssh_username"),
                ssh_port=node_data.get("ssh_port"),
                ssh_auth_type=node_data.get("ssh_auth_type"),
                status=DiscoveredNodeStatus.PENDING.value,
                created_at=datetime.now(UTC),
            )
            if node_data.get("ssh_password"):
                node.ssh_auth_type = node.ssh_auth_type or "password"
                node.ssh_password_encrypted = encrypt_value(node_data["ssh_password"])
            if node_data.get("ssh_private_key"):
                node.ssh_auth_type = node.ssh_auth_type or "key"
                node.ssh_key_encrypted = encrypt_value(node_data["ssh_private_key"])

            self.db.add(node)

        self.db.flush()
        self.db.refresh(job)
        return DiscoveryJobResponse.from_model(job)

    def get_discovery_job(self, *, project_id: int, job_id: int) -> DiscoveryJobResponse:
        """Read a project-scoped discovery job by id."""
        job = self._get_job(project_id=project_id, job_id=job_id)
        return DiscoveryJobResponse.from_model(job)

    def list_discovery_jobs(self, *, project_id: int, limit: int = 20) -> list[DiscoveryJobResponse]:
        """List recent project-scoped discovery jobs (summary surface)."""
        self._get_project(project_id)

        safe_limit = max(1, min(limit, 100))
        jobs = (
            self.db.query(DiscoveryJob)
            .filter(DiscoveryJob.project_id == project_id)
            .order_by(DiscoveryJob.created_at.desc())
            .limit(safe_limit)
            .all()
        )
        return [DiscoveryJobResponse.from_model(job, include_nodes=False) for job in jobs]

    def rerun_discovery_job(self, *, project_id: int, job_id: int) -> DiscoveryJobResponse:
        """Reset an existing discovery job to PENDING. Execution is started separately via start_background_execution()."""
        job = self._get_job(project_id=project_id, job_id=job_id)

        if job.status == DiscoveryJobStatus.IN_PROGRESS.value:
            raise BadRequestError("Discovery job is already in progress")

        with _active_jobs_lock:
            if job_id in _active_jobs:
                raise BadRequestError("Discovery job is already in progress")

        job.status = DiscoveryJobStatus.PENDING.value
        job.completed_nodes = 0
        job.failed_nodes = 0
        job.started_at = None
        job.completed_at = None
        job.error_message = None
        job.connectivity_status = None
        job.connectivity_matrix = None

        for node in job.nodes:
            node.status = DiscoveredNodeStatus.PENDING.value
            node.error_message = None
            node.hostname = None
            node.os_type = None
            node.os_version = None
            node.os_pretty_name = None
            node.kernel_version = None
            node.architecture = None
            node.is_dpu_host = False
            node.is_dpu_node = False
            node.dpu_count = 0
            node.dpu_details = None
            node.is_dpu_bmc = False
            node.bmc_product = None
            node.bmc_serial_number = None
            node.k8s_installed = False
            node.k8s_running = False
            node.k8s_version = None
            node.k8s_distribution = None
            node.k8s_role = None
            node.container_runtime = None
            node.network_interfaces = None
            node.discovery_log = None
            node.discovered_at = None

        self.db.flush()
        self.db.refresh(job)
        return DiscoveryJobResponse.from_model(job)

    def delete_discovery_job(self, *, project_id: int, job_id: int) -> None:
        """Delete a discovery job and all its nodes."""
        job = self._get_job(project_id=project_id, job_id=job_id)

        if job.status == DiscoveryJobStatus.IN_PROGRESS.value:
            raise BadRequestError("Cannot delete a discovery job that is in progress")

        with _active_jobs_lock:
            if job_id in _active_jobs:
                raise BadRequestError("Cannot delete a discovery job that is in progress")

        self.db.delete(job)

    def register_node_as_dpu(
        self,
        *,
        project_id: int,
        node_id: int,
        bmc_ip: str | None = None,
        bmc_username: str | None = None,
        bmc_password: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Create one or more `dpus` rows from a DiscoveredNode.

        Dispatches by detection flags:
          - `is_dpu_bmc=True`  → single BMC-mode DPU at node.ip_address.
          - `is_dpu_node=True` → single BMC-mode DPU; the discovered IP is
            the DPU OS, caller must supply bmc_ip.
          - `is_dpu_host=True` → one in-band DPU per entry in node.dpu_details.
            bmc_ip/bmc_username/bmc_password are ignored in this case.
          - neither            → 400.

        Returns `{"dpus": [...]}` — always a list, single-element for the
        BMC paths and N-element for the host path.
        """
        from models.dpu import Dpu

        node = (
            self.db.query(DiscoveredNode)
            .join(DiscoveryJob, DiscoveredNode.discovery_job_id == DiscoveryJob.id)
            .filter(DiscoveredNode.id == node_id, DiscoveryJob.project_id == project_id)
            .first()
        )
        if not node:
            raise NotFoundError("discovered_node", node_id)

        if node.is_dpu_host and not node.is_dpu_bmc and not node.is_dpu_node:
            return self._register_host_as_inband_dpus(
                project_id=project_id, node=node, name=name,
            )

        if node.is_dpu_bmc:
            effective_bmc = bmc_ip or node.ip_address
            effective_arm = None
        elif node.is_dpu_node:
            if not bmc_ip:
                raise BadRequestError(
                    "Discovered node is a DPU DPU OS — please supply the DPU's BMC IP."
                )
            effective_bmc = bmc_ip
            effective_arm = node.ip_address
        else:
            raise BadRequestError(
                "Discovered node is neither a BlueField BMC nor a DPU DPU OS — "
                "cannot register as a DPU."
            )

        existing = (
            self.db.query(Dpu)
            .filter(Dpu.project_id == project_id, Dpu.bmc_ip == effective_bmc)
            .first()
        )
        if existing:
            raise BadRequestError(
                f"DPU with BMC IP '{effective_bmc}' is already registered in this project."
            )

        dpu = Dpu(
            project_id=project_id,
            name=name or node.hostname,
            access_mode="bmc",
            bmc_ip=effective_bmc,
            serial_number=node.bmc_serial_number,
            dpu_os_ip=effective_arm,
            dpu_os_reachable=True if effective_arm else None,
        )
        if bmc_username:
            dpu.bmc_username_encrypted = encrypt_value(bmc_username)
        if bmc_password:
            dpu.bmc_password_encrypted = encrypt_value(bmc_password)

        self.db.add(dpu)
        self.db.flush()
        logger.info(
            "Registered discovered node %s as DPU %s (project=%s, bmc=%s, arm=%s)",
            node_id, dpu.id, project_id, effective_bmc, effective_arm,
        )

        self._maybe_populate_project_oob_from_discovery(project_id, node)

        return {
            "dpus": [
                {
                    "dpu_id": dpu.id,
                    "project_id": project_id,
                    "access_mode": "bmc",
                    "bmc_ip": effective_bmc,
                    "dpu_os_ip": effective_arm,
                    "host_node_ip": None,
                    "pci_address": None,
                }
            ],
        }

    def _register_host_as_inband_dpus(
        self, *, project_id: int, node: DiscoveredNode, name: str | None,
    ) -> dict[str, Any]:
        """Register every DPU found on a host as a separate in-band DPU row.

        Uses the host's SSH credential (per-node or job-level) for later rshim
        access. Deduplication is keyed by (project, host_ip, pci_base_address);
        duplicates are silently skipped so re-running registration is idempotent.
        """
        from models.dpu import Dpu

        details = list(node.dpu_details or [])
        if not details:
            raise BadRequestError(
                "Host has no DPUs detected — nothing to register."
            )

        # Snapshot the credentials that worked against this host during
        # discovery, so rshim install can SSH in without the operator first
        # having to create/pick a saved SSHCredential.
        snapshot = self._snapshot_discovery_credentials(node)

        created: list[dict[str, Any]] = []
        skipped_existing = 0
        for detail in details:
            pci_addr_raw = str(detail.get("pci_address") or "")
            if not pci_addr_raw:
                continue
            pci_base = (
                pci_addr_raw.rsplit(".", 1)[0] if "." in pci_addr_raw else pci_addr_raw
            )
            existing = (
                self.db.query(Dpu)
                .filter(
                    Dpu.project_id == project_id,
                    Dpu.access_mode == "in-band",
                    Dpu.host_node_ip == node.ip_address,
                    Dpu.pci_address == pci_base,
                )
                .first()
            )
            if existing:
                skipped_existing += 1
                continue

            display = name or (
                f"{node.hostname} {pci_base}" if node.hostname else f"host-{node.ip_address} {pci_base}"
            )
            dpu = Dpu(
                project_id=project_id,
                name=display,
                access_mode="in-band",
                bmc_ip=None,
                host_node_ip=node.ip_address,
                host_hostname=node.hostname,
                host_ssh_credential_id=snapshot["credential_id"],
                host_ssh_username=snapshot["username"],
                host_ssh_port=snapshot["port"],
                host_ssh_auth_type=snapshot["auth_type"],
                host_ssh_password_encrypted=snapshot["password_encrypted"],
                host_ssh_private_key_encrypted=snapshot["private_key_encrypted"],
                host_ssh_key_passphrase_encrypted=snapshot["key_passphrase_encrypted"],
                pci_address=pci_base,
            )
            self.db.add(dpu)
            self.db.flush()
            created.append({
                "dpu_id": dpu.id,
                "project_id": project_id,
                "access_mode": "in-band",
                "bmc_ip": None,
                "dpu_os_ip": None,
                "host_node_ip": node.ip_address,
                "pci_address": pci_base,
            })
            logger.info(
                "Registered in-band DPU %s on host %s (pci=%s, project=%s)",
                dpu.id, node.ip_address, pci_base, project_id,
            )

        if not created and skipped_existing > 0:
            raise BadRequestError(
                f"All {skipped_existing} DPU(s) on this host are already registered."
            )

        return {"dpus": created}

    # ── Register-as-bare-metal-host ────────────────────────────────────────
    #
    # Discovery → DPU tab handles in-band DPU rows directly. The host
    # itself (the x86 server those DPUs sit in) is also a useful unit of
    # management — it carries jumphost/SSH context, hosts the DPU OS
    # tmfifo network, runs rshim, etc. The Bare Metal tab is the natural
    # home for that. These two methods register the host from a
    # DiscoveredNode, idempotent on (project, host_ip) so re-clicking
    # *Register All Hosts* on a refreshed discovery just succeeds.

    def register_node_as_bare_metal_host(
        self,
        *,
        project_id: int,
        node_id: int,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Create a BareMetalHost row from a discovered DPU host.

        Idempotent: if a host with the same (project_id, host_ip) is
        already registered, returns its id with status='exists' and
        does NOT raise. Mirrors the spec the user asked for —
        re-clicking the button on a refreshed discovery should be a
        no-op rather than an error.

        Eligible nodes are `is_dpu_host=True` with `dpu_count > 0`. The
        BMC-mode (`is_dpu_bmc`) and DPU-OS (`is_dpu_node`) cases don't
        carry a usable x86 host IP in the discovery payload — they're
        rejected with a 400.

        Idempotency, credential synthesis, and name uniqueness all live
        in `bare_metal/host_registration.py` so the DPU-tab path can
        reuse them.
        """
        from services.bare_metal.host_registration import register_or_lookup

        node = (
            self.db.query(DiscoveredNode)
            .join(DiscoveryJob, DiscoveredNode.discovery_job_id == DiscoveryJob.id)
            .filter(DiscoveredNode.id == node_id, DiscoveryJob.project_id == project_id)
            .first()
        )
        if node is None:
            raise NotFoundError("discovered_node", node_id)
        if not node.is_dpu_host or (node.dpu_count or 0) == 0:
            raise BadRequestError(
                "Only discovered DPU-host nodes (is_dpu_host=True with at "
                "least one DPU detected via PCIe) can be registered as a "
                "bare-metal host."
            )

        snapshot = self._snapshot_discovery_credentials(node)
        return register_or_lookup(
            self.db,
            project_id=project_id,
            host_ip=node.ip_address,
            hostname=node.hostname,
            snapshot=snapshot,
            name=name,
            description=f"Registered from Discovery (job {node.discovery_job_id})",
        )

    def register_all_hosts_in_job(
        self, *, project_id: int, job_id: int,
    ) -> dict[str, Any]:
        """Register every eligible DPU-host node in a discovery job.

        Returns `{created: int, existing: int, hosts: [...]}` so the UI
        can show a useful toast even when most rows are already
        registered.
        """
        job = self._get_job(project_id=project_id, job_id=job_id)
        nodes = (
            self.db.query(DiscoveredNode)
            .filter(
                DiscoveredNode.discovery_job_id == job.id,
                DiscoveredNode.is_dpu_host.is_(True),
                DiscoveredNode.dpu_count > 0,
            )
            .all()
        )

        created = 0
        existing = 0
        hosts: list[dict[str, Any]] = []
        for node in nodes:
            try:
                r = self.register_node_as_bare_metal_host(
                    project_id=project_id, node_id=node.id,
                )
            except BadRequestError as exc:
                # Per-row failure shouldn't poison the bulk action — log,
                # carry on. Most likely cause is a row that flipped flags
                # between the bulk-iter snapshot and the call below.
                logger.warning(
                    "register_all_hosts skipped node %s: %s", node.id, exc,
                )
                continue
            if r["status"] == "created":
                created += 1
            else:
                existing += 1
            hosts.append({"node_id": node.id, **r})

        return {"created": created, "existing": existing, "hosts": hosts}

    def _snapshot_discovery_credentials(
        self, node: DiscoveredNode,
    ) -> dict[str, Any]:
        """Return a dict of credential fields that got into this host.

        Follows discovery's own precedence — per-node credential wins,
        then job credential; saved SSHCredential rows resolve to their
        own columns. Returns plaintext usernames/port/auth_type and the
        ALREADY-encrypted password/key/passphrase blobs (the DPU row
        uses the same encryption key, so no re-encrypt is needed).
        """
        from models.ssh_credential import SSHCredential

        job = node.discovery_job
        cred_id = (
            node.ssh_credential_id
            if node.ssh_credential_id is not None
            else job.ssh_credential_id
        )
        if cred_id is not None:
            cred = self.db.get(SSHCredential, cred_id)
            if cred is not None:
                return {
                    "credential_id": cred_id,
                    "username": cred.username,
                    "port": int(cred.port or 22),
                    "auth_type": cred.auth_type or "key",
                    "password_encrypted": cred.password_encrypted,
                    "private_key_encrypted": cred.private_key_encrypted,
                    "key_passphrase_encrypted": cred.key_passphrase_encrypted,
                }

        # Inline path — node overrides job.
        return {
            "credential_id": None,
            "username": node.ssh_username or job.ssh_username,
            "port": int(node.ssh_port or job.ssh_port or 22),
            "auth_type": node.ssh_auth_type or job.ssh_auth_type,
            "password_encrypted": node.ssh_password_encrypted or job.ssh_password_encrypted,
            "private_key_encrypted": node.ssh_key_encrypted or job.ssh_key_encrypted,
            "key_passphrase_encrypted": None,
        }

    def _maybe_populate_project_oob_from_discovery(
        self, project_id: int, node: DiscoveredNode,
    ) -> None:
        """Copy the discovery SSH password into ProjectDpuSettings.default_oob_*
        when no project default is configured yet.

        Skips entirely when:
          * project default already populated (don't clobber operator choices),
          * the discovery used a key-based credential (no password to copy),
          * neither per-node nor job-level credentials expose a password.
        """
        from models.dpu import ProjectDpuSettings

        settings = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == project_id)
            .first()
        )
        if settings is not None and settings.default_oob_password_encrypted:
            return  # already set — nothing to do

        user, pw = self._extract_discovery_password(node)
        if not user or not pw:
            return

        if settings is None:
            # Seed full project defaults (ubuntu user, random OS password,
            # NTP, VLANs) so later opens of DPU Project Settings see a
            # fully-primed row.
            from services.dpu_service import build_pristine_dpu_settings
            settings = build_pristine_dpu_settings(self.db, project_id)
            self.db.add(settings)
        if not settings.default_oob_username:
            settings.default_oob_username = user
        settings.default_oob_password_encrypted = encrypt_value(pw)
        self.db.flush()
        logger.info(
            "Populated project %s DPU default OOB credentials from discovery "
            "of node %s (user=%s)",
            project_id, node.id, user,
        )

    def _extract_discovery_password(
        self, node: DiscoveredNode,
    ) -> tuple[str | None, str | None]:
        """Return the (username, plaintext password) actually used during
        SSH discovery of `node`, or (None, None) if the credential was
        key-based or otherwise lacks a password."""
        # 1. Per-node SSHCredential record (takes precedence).
        if node.ssh_credential_id:
            try:
                cred = self._resolve_ssh_credential(node.ssh_credential_id)
            except Exception:  # noqa: BLE001
                cred = None
            if cred and (cred.auth_type or "").lower() == "password" and cred.password_encrypted:
                return cred.username, decrypt_value(cred.password_encrypted)
        # 2. Per-node inline password.
        if node.ssh_password_encrypted:
            return node.ssh_username, decrypt_value(node.ssh_password_encrypted)
        # 3. Job-level SSHCredential.
        job = node.discovery_job
        if job is not None and job.ssh_credential_id:
            try:
                cred = self._resolve_ssh_credential(job.ssh_credential_id)
            except Exception:  # noqa: BLE001
                cred = None
            if cred and (cred.auth_type or "").lower() == "password" and cred.password_encrypted:
                return cred.username, decrypt_value(cred.password_encrypted)
        # 4. Job-level inline password.
        if job is not None and job.ssh_password_encrypted:
            return job.ssh_username, decrypt_value(job.ssh_password_encrypted)
        return None, None

    def probe_node_kubeconfig(self, *, project_id: int, node_id: int) -> dict:
        """SSH to a discovered node and return its kubeconfig contexts for cluster registration."""
        node = (
            self.db.query(DiscoveredNode)
            .join(DiscoveryJob, DiscoveredNode.discovery_job_id == DiscoveryJob.id)
            .filter(DiscoveredNode.id == node_id, DiscoveryJob.project_id == project_id)
            .first()
        )
        if not node:
            raise NotFoundError("discovered_node", node_id)

        if not node.k8s_running:
            raise BadRequestError("This node does not have a running Kubernetes cluster")

        job = node.discovery_job
        shared_creds = self._resolve_shared_credentials(job)
        creds = self._resolve_node_credentials(node, shared_creds)
        jumphost_cred = self._resolve_jumphost_credential(project_id)

        username = creds.get("username")
        if not username:
            raise BadRequestError("No SSH username available for this node")
        if not creds.get("password") and not creds.get("private_key"):
            raise BadRequestError("No SSH credential available for this node")

        result = _probe_kubeconfig_via_ssh(
            host=node.ip_address,
            username=username,
            port=int(creds.get("port") or 22),
            auth_type=str(creds.get("auth_type") or "password"),
            password=creds.get("password"),
            private_key=creds.get("private_key"),
            key_passphrase=creds.get("key_passphrase"),
            jumphost_cred=jumphost_cred,
            k8s_distribution=node.k8s_distribution,
        )
        result["node_ip"] = node.ip_address
        return result

    def _get_project(self, project_id: int) -> Project:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise NotFoundError("project", project_id)
        return project

    def _get_job(self, *, project_id: int, job_id: int) -> DiscoveryJob:
        job = (
            self.db.query(DiscoveryJob)
            .filter(DiscoveryJob.id == job_id, DiscoveryJob.project_id == project_id)
            .first()
        )
        if not job:
            raise NotFoundError("discovery_job", job_id)
        return job

    @classmethod
    def start_background_execution(cls, job_id: int) -> None:
        """Spawn a daemon thread that runs discovery for a previously-persisted job.

        Caller MUST commit the job + nodes before invoking this so the worker thread,
        which uses a fresh SessionLocal(), can read them.
        """
        with _active_jobs_lock:
            if job_id in _active_jobs:
                logger.warning("Discovery job %s already has an active background runner", job_id)
                return
            event = threading.Event()
            _active_jobs[job_id] = event

        thread = threading.Thread(
            target=cls._background_runner,
            args=(job_id, event),
            name=f"discovery-job-{job_id}",
            daemon=True,
        )
        thread.start()

    @classmethod
    def wait_for_job(cls, job_id: int, timeout: float = 120.0) -> bool:
        """Block until the background runner for a job finishes. Returns True if it finished, False on timeout.

        Intended for tests and CLI flows that want synchronous semantics.
        """
        with _active_jobs_lock:
            event = _active_jobs.get(job_id)
        if event is None:
            return True
        return event.wait(timeout)

    @classmethod
    def _background_runner(cls, job_id: int, event: threading.Event) -> None:
        try:
            db = SessionLocal()
            try:
                service = cls(db)
                job = db.query(DiscoveryJob).filter(DiscoveryJob.id == job_id).first()
                if not job:
                    logger.warning("Discovery job %s vanished before background runner started", job_id)
                    return
                service._run_discovery_job(job)
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
        except Exception:
            logger.exception("Discovery background runner for job %s crashed", job_id)
        finally:
            with _active_jobs_lock:
                _active_jobs.pop(job_id, None)
            event.set()

    def _run_discovery_job(self, job: DiscoveryJob) -> None:
        job.status = DiscoveryJobStatus.IN_PROGRESS.value
        job.started_at = datetime.now(UTC)
        job.completed_nodes = 0
        job.failed_nodes = 0
        job.error_message = None
        self.db.commit()

        try:
            shared_creds = self._resolve_shared_credentials(job)
            jumphost_cred = self._resolve_jumphost_credential(job.project_id)

            node_ids = [int(node.id) for node in job.nodes]
            max_workers = _discovery_parallelism(len(node_ids), jumphost_cred)

            if max_workers == 1:
                # Inline path — keeps the caller's session and avoids thread/session overhead.
                for node in job.nodes:
                    self._discover_node(node, shared_creds, jumphost_cred)
            else:
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"discovery-{job.id}") as pool:
                    futures = [
                        pool.submit(_run_node_in_worker_session, node_id, shared_creds, jumphost_cred)
                        for node_id in node_ids
                    ]
                    # Tick job.completed_nodes / job.failed_nodes as each worker
                    # settles so the UI progress bar advances while probes run.
                    # Without this the row is only updated once at the end, and
                    # high-parallelism runs appear to jump 0 → total instantly.
                    for future in as_completed(futures):
                        future.result()
                        try:
                            done = (
                                self.db.query(DiscoveredNode)
                                .filter(
                                    DiscoveredNode.discovery_job_id == job.id,
                                    DiscoveredNode.status == DiscoveredNodeStatus.COMPLETED.value,
                                )
                                .count()
                            )
                            failed = (
                                self.db.query(DiscoveredNode)
                                .filter(
                                    DiscoveredNode.discovery_job_id == job.id,
                                    DiscoveredNode.status == DiscoveredNodeStatus.FAILED.value,
                                )
                                .count()
                            )
                            job.completed_nodes = done
                            job.failed_nodes = failed
                            self.db.commit()
                        except Exception:
                            self.db.rollback()
                            logger.exception(
                                "Failed to tick progress on discovery job %s", job.id
                            )

            self.db.expire(job)
            self.db.refresh(job)
            for node in job.nodes:
                self.db.refresh(node)

            completed = sum(1 for node in job.nodes if node.status == DiscoveredNodeStatus.COMPLETED.value)
            failed = sum(1 for node in job.nodes if node.status == DiscoveredNodeStatus.FAILED.value)

            job.completed_nodes = completed
            job.failed_nodes = failed

            if failed == job.total_nodes:
                job.status = DiscoveryJobStatus.FAILED.value
                job.error_message = "All nodes failed discovery"
                job.connectivity_status = "skipped"
            else:
                if failed > 0:
                    job.error_message = f"{failed} of {job.total_nodes} nodes failed"
                # Stay in IN_PROGRESS while the connectivity phase runs so the UI
                # keeps polling and shows "testing connectivity" to the user.
                self.db.commit()
                self._run_connectivity_phase(job, shared_creds, jumphost_cred)
                self.db.expire(job)
                self.db.refresh(job)
                job.status = DiscoveryJobStatus.COMPLETED.value
        except Exception as exc:
            logger.exception("Discovery job %s failed before completion", job.id)

            self.db.expire(job)
            self.db.refresh(job)
            for node in job.nodes:
                self.db.refresh(node)
                if node.status not in (DiscoveredNodeStatus.COMPLETED.value, DiscoveredNodeStatus.FAILED.value):
                    node.status = DiscoveredNodeStatus.FAILED.value
                    if not node.error_message:
                        node.error_message = "Discovery job failed before node execution"

            completed = sum(1 for node in job.nodes if node.status == DiscoveredNodeStatus.COMPLETED.value)
            failed = sum(1 for node in job.nodes if node.status == DiscoveredNodeStatus.FAILED.value)

            job.completed_nodes = completed
            job.failed_nodes = failed
            job.status = DiscoveryJobStatus.FAILED.value
            job.error_message = f"Discovery execution failed: {type(exc).__name__}"
        finally:
            job.completed_at = datetime.now(UTC)
            self.db.commit()

    def _resolve_ssh_credential(self, credential_id: int) -> SSHCredential:
        credential = self.db.query(SSHCredential).filter(SSHCredential.id == credential_id).first()
        if not credential:
            raise BadRequestError(f"SSH credential {credential_id} not found")
        return credential

    def _resolve_shared_credentials(self, job: DiscoveryJob) -> dict[str, Any]:
        creds: dict[str, Any] = {
            "username": job.ssh_username,
            "port": job.ssh_port or 22,
            "auth_type": job.ssh_auth_type,
            "password": decrypt_value(job.ssh_password_encrypted) if job.ssh_password_encrypted else None,
            "private_key": decrypt_value(job.ssh_key_encrypted) if job.ssh_key_encrypted else None,
            "key_passphrase": None,
        }

        if job.ssh_credential_id:
            ssh_cred = self._resolve_ssh_credential(job.ssh_credential_id)
            creds["username"] = creds["username"] or ssh_cred.username
            creds["port"] = ssh_cred.port or creds["port"]
            creds["auth_type"] = ssh_cred.auth_type or creds["auth_type"]
            if ssh_cred.password_encrypted:
                creds["password"] = decrypt_value(ssh_cred.password_encrypted)
            if ssh_cred.private_key_encrypted:
                creds["private_key"] = decrypt_value(ssh_cred.private_key_encrypted)
            if ssh_cred.key_passphrase_encrypted:
                creds["key_passphrase"] = decrypt_value(ssh_cred.key_passphrase_encrypted)

        return creds

    def _resolve_jumphost_credential(self, project_id: int) -> dict | None:
        """Return a credential dict for the project's jumphost, or None if not configured."""
        project = self._get_project(project_id)
        if not project.ssh_credential_id:
            return None
        cred = self._resolve_ssh_credential(project.ssh_credential_id)
        return {
            "host": cred.host,
            "port": cred.port or 22,
            "username": cred.username,
            "auth_type": cred.auth_type or "key",
            "password": decrypt_value(cred.password_encrypted) if cred.password_encrypted else None,
            "private_key": decrypt_value(cred.private_key_encrypted) if cred.private_key_encrypted else None,
            "key_passphrase": decrypt_value(cred.key_passphrase_encrypted) if cred.key_passphrase_encrypted else None,
        }

    def _resolve_node_credentials(self, node: DiscoveredNode, shared_creds: dict[str, Any]) -> dict[str, Any]:
        creds = dict(shared_creds)

        if node.ssh_credential_id:
            ssh_cred = self._resolve_ssh_credential(node.ssh_credential_id)
            creds["username"] = ssh_cred.username
            creds["port"] = ssh_cred.port or creds.get("port") or 22
            creds["auth_type"] = ssh_cred.auth_type
            creds["password"] = decrypt_value(ssh_cred.password_encrypted) if ssh_cred.password_encrypted else None
            creds["private_key"] = (
                decrypt_value(ssh_cred.private_key_encrypted) if ssh_cred.private_key_encrypted else None
            )
            creds["key_passphrase"] = (
                decrypt_value(ssh_cred.key_passphrase_encrypted) if ssh_cred.key_passphrase_encrypted else None
            )
            return creds

        if node.ssh_username:
            creds["username"] = node.ssh_username
        if node.ssh_port:
            creds["port"] = node.ssh_port
        if node.ssh_auth_type:
            creds["auth_type"] = node.ssh_auth_type
        if node.ssh_password_encrypted:
            creds["password"] = decrypt_value(node.ssh_password_encrypted)
        if node.ssh_key_encrypted:
            creds["private_key"] = decrypt_value(node.ssh_key_encrypted)

        return creds

    def _discover_node(
        self,
        node: DiscoveredNode,
        shared_creds: dict[str, Any],
        jumphost_cred: dict | None = None,
    ) -> str:
        node.status = DiscoveredNodeStatus.CONNECTING.value
        node.error_message = None
        self.db.commit()

        creds = self._resolve_node_credentials(node, shared_creds)

        username = creds.get("username")
        if not username:
            node.status = DiscoveredNodeStatus.FAILED.value
            node.error_message = "No SSH username available for discovery"
            self.db.commit()
            return node.status

        if not creds.get("password") and not creds.get("private_key"):
            node.status = DiscoveredNodeStatus.FAILED.value
            node.error_message = "No SSH credential secret available for discovery"
            self.db.commit()
            return node.status

        # BlueField BMC probe — runs regardless of SSH outcome so we can
        # detect DPU BMCs (no standard SSH on port 22) and offer
        # "Register as DPU" in the UI. Routed through the project's
        # jumphost when one is configured so remote labs that only expose
        # the BMC via SSH tunnelling still get the Redfish signal.
        #
        # Pass the operator's password (when supplied) as candidate
        # Redfish basic-auth credentials too — on a BlueField BMC the
        # SSH and Redfish accounts share the same root user, so the
        # password the operator typed for SSH is also what unlocks the
        # deeper Redfish reads (serial number, etc.). Anonymous + Nvidia
        # default still bracket the attempt.
        operator_password = creds.get("password")
        operator_auth = (
            (username, operator_password)
            if operator_password else None
        )
        bmc = _probe_bluefield_bmc(
            node.ip_address, jumphost_cred,
            operator_auth=operator_auth,
        )
        node.is_dpu_bmc = bmc["is_dpu_bmc"]
        node.bmc_product = bmc["product"]
        node.bmc_serial_number = bmc["serial_number"]
        # Phase 2: persist the richer Redfish capture + the
        # default-password security signal. Stored as JSON so the UI
        # can render every field that happens to be present without us
        # widening the column set on each new field.
        bmc_payload = dict(bmc.get("redfish") or {})
        if bmc.get("uses_default_password"):
            bmc_payload["UsesDefaultPassword"] = True
        node.bmc_redfish_payload = bmc_payload or None

        # Skip the SSH probe entirely on confirmed BlueField BMCs. SSH on
        # OpenBMC port 22 is for interactive root login only — it doesn't
        # serve any of the OS/lspci/k8s data the SSH probe collects, and
        # on a fresh BMC the default `0penBmc` triggers a forced password
        # change that paramiko can't drive (raising "Illegal info request
        # from server"). Treating BMC nodes as completed-on-Redfish gets
        # rid of the alarming red `failed` badge while still surfacing
        # the Register-as-DPU action in the UI (which keys off
        # `is_dpu_bmc`, not status).
        if node.is_dpu_bmc:
            node.discovery_log = (
                "BlueField BMC detected via Redfish — SSH probe skipped "
                "(BMC port 22 doesn't serve OS-level discovery data and "
                "default `0penBmc` triggers a forced password change)."
            )
            node.discovered_at = datetime.now(UTC)
            node.status = DiscoveredNodeStatus.COMPLETED.value
            node.error_message = None
            self.db.commit()
            return node.status

        try:
            node.status = DiscoveredNodeStatus.DISCOVERING.value
            self.db.commit()

            results = _probe_node_ssh(
                host=node.ip_address,
                username=username,
                port=int(creds.get("port") or 22),
                auth_type=str(creds.get("auth_type") or "password"),
                password=creds.get("password"),
                private_key=creds.get("private_key"),
                key_passphrase=creds.get("key_passphrase"),
                jumphost_cred=jumphost_cred,
            )
            self._validate_probe_results(results)

            node.hostname = results.get("hostname")
            node.os_type = results.get("os_type")
            node.os_version = results.get("os_version")
            node.os_pretty_name = results.get("os_pretty_name")
            node.kernel_version = results.get("kernel_version")
            node.architecture = results.get("architecture")
            node.is_dpu_host = bool(results.get("is_dpu_host"))
            node.is_dpu_node = bool(results.get("is_dpu_node"))
            node.dpu_count = int(results.get("dpu_count") or 0)
            node.dpu_details = results.get("dpu_details")
            node.k8s_installed = bool(results.get("k8s_installed"))
            node.k8s_running = bool(results.get("k8s_running"))
            node.k8s_version = results.get("k8s_version")
            node.k8s_distribution = results.get("k8s_distribution")
            node.k8s_role = results.get("k8s_role")
            node.container_runtime = results.get("container_runtime")
            node.network_interfaces = results.get("network_interfaces")
            node.discovery_log = results.get("log")
            node.discovered_at = datetime.now(UTC)
            node.status = DiscoveredNodeStatus.COMPLETED.value
            node.error_message = None
        except AuthenticationException:
            node.status = DiscoveredNodeStatus.FAILED.value
            node.error_message = "SSH authentication failed (bad credentials)"
        except (NoValidConnectionsError, TimeoutError):
            node.status = DiscoveredNodeStatus.FAILED.value
            node.error_message = "SSH host unreachable or connection refused"
        except ValueError as exc:
            node.status = DiscoveredNodeStatus.FAILED.value
            node.error_message = f"Malformed discovery output: {exc}"
        except SSHException as exc:
            node.status = DiscoveredNodeStatus.FAILED.value
            node.error_message = f"SSH connection failed: {exc}"
        except Exception as exc:  # pragma: no cover - defensive
            node.status = DiscoveredNodeStatus.FAILED.value
            node.error_message = f"Discovery failed: {type(exc).__name__}"
            logger.warning("Unexpected node discovery error for %s: %s", node.ip_address, exc)

        self.db.commit()
        return node.status

    def _run_connectivity_phase(
        self,
        job: DiscoveryJob,
        shared_creds: dict[str, Any],
        jumphost_cred: dict | None,
    ) -> None:
        """Run inter-node ICMP sweep, separately for builtin and bluefield interfaces.

        Stores the matrix on the job. Exceptions are captured and recorded so a
        failed connectivity phase does not fail the discovery job.
        """
        endpoints_by_group = _collect_connectivity_endpoints(job)
        runnable_groups = {g: eps for g, eps in endpoints_by_group.items() if len({e["node_id"] for e in eps}) >= 2}

        if not runnable_groups:
            job.connectivity_status = "skipped"
            job.connectivity_matrix = None
            self.db.commit()
            return

        job.connectivity_status = "in_progress"
        job.connectivity_matrix = None
        self.db.commit()

        try:
            # Group endpoints by source node id so each source uses one SSH session per group.
            source_node_ids: set[int] = set()
            for eps in runnable_groups.values():
                for ep in eps:
                    source_node_ids.add(ep["node_id"])

            # Build per-node credential snapshots up-front (DB-bound work).
            per_node_creds: dict[int, dict[str, Any]] = {}
            for node in job.nodes:
                if int(node.id) in source_node_ids and node.status == DiscoveredNodeStatus.COMPLETED.value:
                    per_node_creds[int(node.id)] = self._resolve_node_credentials(node, shared_creds)

            max_workers = _discovery_parallelism(len(source_node_ids), jumphost_cred)
            results: dict[str, list[dict]] = {group: [] for group in runnable_groups}

            def _per_source(node_id: int) -> dict[str, list[dict]]:
                creds = per_node_creds.get(node_id)
                if not creds:
                    return {}
                src_node_ip = next((e["node_ip"] for eps in runnable_groups.values() for e in eps if e["node_id"] == node_id), None)
                if not src_node_ip:
                    return {}
                return _run_connectivity_for_source(
                    src_node_id=node_id,
                    src_node_ip=src_node_ip,
                    creds=creds,
                    jumphost_cred=jumphost_cred,
                    runnable_groups=runnable_groups,
                )

            if max_workers == 1:
                per_source_results = [_per_source(nid) for nid in sorted(source_node_ids)]
            else:
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"conn-{job.id}") as pool:
                    per_source_results = list(pool.map(_per_source, sorted(source_node_ids)))

            for chunk in per_source_results:
                for group, rows in chunk.items():
                    results.setdefault(group, []).extend(rows)

            matrix = {
                "groups": {
                    group: {
                        "endpoints": runnable_groups[group],
                        "results": results.get(group, []),
                    }
                    for group in runnable_groups
                }
            }

            self.db.expire(job)
            self.db.refresh(job)
            job.connectivity_matrix = matrix
            job.connectivity_status = "completed"
        except Exception as exc:
            logger.exception("Connectivity phase for job %s failed", job.id)
            self.db.expire(job)
            self.db.refresh(job)
            job.connectivity_status = "failed"
            job.connectivity_matrix = {
                "groups": {},
                "error": f"Connectivity phase failed: {type(exc).__name__}",
            }
        finally:
            self.db.commit()

    @staticmethod
    def _validate_probe_results(results: Any) -> None:
        if not isinstance(results, dict):
            raise ValueError("discovery result must be an object")
        network_interfaces = results.get("network_interfaces")
        if network_interfaces is not None and not isinstance(network_interfaces, list):
            raise ValueError("network_interfaces must be a list when provided")
        dpu_details = results.get("dpu_details")
        if dpu_details is not None and not isinstance(dpu_details, list):
            raise ValueError("dpu_details must be a list when provided")


PING_TIMEOUT_SEC = 1
# 4 packets per ping run; we report `min` rather than `avg` so the
# first-packet ARP-resolution outlier doesn't drag every cell up
# (was averaging ~100 ms for an otherwise sub-ms link in Job-3
# DPU-matrix testing). 4 packets keeps the run short while giving
# enough samples that a transient drop doesn't inflate the min.
PING_COUNT = 4
CONNECTIVITY_GROUPS = ("builtin", "bluefield")


def _collect_connectivity_endpoints(job: DiscoveryJob) -> dict[str, list[dict]]:
    """Group successfully-discovered (node, interface, addresses) tuples by interface kind."""
    grouped: dict[str, list[dict]] = {group: [] for group in CONNECTIVITY_GROUPS}
    for node in job.nodes:
        if node.status != DiscoveredNodeStatus.COMPLETED.value:
            continue
        for iface in node.network_interfaces or []:
            if not isinstance(iface, dict):
                continue
            kind = iface.get("kind")
            if kind not in CONNECTIVITY_GROUPS:
                continue
            addresses = [
                addr for addr in (iface.get("addresses") or [])
                if isinstance(addr, dict)
                and addr.get("address")
                and addr.get("family") in ("inet", "inet6")
                # Skip IPv6 link-local — meaningless without zone identifiers.
                and not (addr.get("family") == "inet6" and str(addr.get("address", "")).lower().startswith("fe80"))
            ]
            if not addresses:
                continue
            grouped[kind].append({
                "node_id": int(node.id),
                "node_ip": str(node.ip_address),
                "hostname": node.hostname,
                "interface": iface.get("name"),
                "addresses": addresses,
            })
    return grouped


def _parse_ping_output(stdout: str, exit_code: int) -> dict[str, Any]:
    """Parse iputils-style ping output into {reachable, rtt_ms, error}.

    Reports `min` rather than `avg` — the first packet of a fresh ping
    run typically eats an ARP request/reply (50–200 ms), which would
    drag the avg of a 4-packet run up by ~50× even though the steady-
    state RTT is sub-millisecond. min is the cleanest single-number
    summary.
    """
    text = stdout or ""
    if exit_code == 0:
        # `rtt min/avg/max/mdev = 0.123/0.456/0.789/0.012 ms`
        m = re.search(r"rtt[^=]*=\s*([0-9.]+)/([0-9.]+)/([0-9.]+)", text)
        rtt_ms: float | None
        rtt_ms = float(m.group(1)) if m else None  # min
        return {"reachable": True, "rtt_ms": rtt_ms, "error": None}
    err = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or "ping statistics" in line or line.startswith("---"):
            continue
        if "PING " in line and " bytes of data" in line:
            continue
        err = line
        break
    return {"reachable": False, "rtt_ms": None, "error": err or f"ping exit {exit_code}"}


def _run_connectivity_for_source(
    *,
    src_node_id: int,
    src_node_ip: str,
    creds: dict[str, Any],
    jumphost_cred: dict | None,
    runnable_groups: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Open one SSH session to a source node and ping every relevant target/interface."""
    results: dict[str, list[dict]] = {group: [] for group in runnable_groups}

    jumphost_client = None
    sock = None
    if jumphost_cred:
        jumphost_client, sock = _open_jumphost_channel(jumphost_cred, src_node_ip, int(creds.get("port") or 22))

    try:
        client = _ssh_connect(
            host=src_node_ip,
            username=creds["username"],
            port=int(creds.get("port") or 22),
            auth_type=str(creds.get("auth_type") or "password"),
            password=creds.get("password"),
            private_key=creds.get("private_key"),
            key_passphrase=creds.get("key_passphrase"),
            sock=sock,
        )
        try:
            for group, endpoints in runnable_groups.items():
                # Source interfaces on this node within this group.
                src_endpoints = [ep for ep in endpoints if ep["node_id"] == src_node_id]
                target_endpoints = [ep for ep in endpoints if ep["node_id"] != src_node_id]
                for src_ep in src_endpoints:
                    for dst_ep in target_endpoints:
                        for dst_addr in dst_ep["addresses"]:
                            family = dst_addr["family"]
                            # Need a matching source address on the same family.
                            if not any(a["family"] == family for a in src_ep["addresses"]):
                                continue
                            ping_bin = "ping" if family == "inet" else "ping -6"
                            cmd = (
                                f"{ping_bin} -c {PING_COUNT} -W {PING_TIMEOUT_SEC} "
                                f"-I {src_ep['interface']} {dst_addr['address']} 2>&1"
                            )
                            rc, out = _ssh_exec(client, cmd)
                            parsed = _parse_ping_output(out, rc)
                            results[group].append({
                                "src_node_id": src_node_id,
                                "src_iface": src_ep["interface"],
                                "dst_node_id": dst_ep["node_id"],
                                "dst_iface": dst_ep["interface"],
                                "family": family,
                                "dst_address": dst_addr["address"],
                                **parsed,
                            })
        finally:
            client.close()
    finally:
        if jumphost_client is not None:
            jumphost_client.close()

    return results


def _run_node_in_worker_session(
    node_id: int,
    shared_creds: dict[str, Any],
    jumphost_cred: dict | None,
) -> None:
    """Run a single-node discovery in its own DB session.

    Used by ThreadPoolExecutor workers — paramiko releases the GIL on socket I/O,
    so a thread pool gives real parallelism for SSH probes. Each worker needs
    its own SQLAlchemy session because sessions are not thread-safe.
    """
    db = SessionLocal()
    try:
        node = db.query(DiscoveredNode).filter(DiscoveredNode.id == node_id).first()
        if not node:
            logger.warning("Discovered node %s vanished before worker started", node_id)
            return
        DiscoveryService(db)._discover_node(node, shared_creds, jumphost_cred)
    except Exception:
        db.rollback()
        logger.exception("Worker for discovered node %s crashed", node_id)
        try:
            node = db.query(DiscoveredNode).filter(DiscoveredNode.id == node_id).first()
            if node and node.status not in (
                DiscoveredNodeStatus.COMPLETED.value,
                DiscoveredNodeStatus.FAILED.value,
            ):
                node.status = DiscoveredNodeStatus.FAILED.value
                node.error_message = "Discovery worker crashed"
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("Failed to mark node %s FAILED after worker crash", node_id)
    finally:
        db.close()


def _ssh_connect(
    *,
    host: str,
    username: str,
    port: int,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    key_passphrase: str | None = None,
    sock: paramiko.Channel | None = None,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    if auth_type == "password":
        if not password:
            raise ValueError("password auth selected but no password provided")
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=SSH_CONNECT_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
            sock=sock,
        )
        return client

    if private_key:
        pkey = load_private_key_from_content(private_key, key_passphrase)

        client.connect(
            hostname=host,
            port=port,
            username=username,
            pkey=pkey,
            timeout=SSH_CONNECT_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
            sock=sock,
        )
        return client

    if password:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=SSH_CONNECT_TIMEOUT,
            look_for_keys=False,
            allow_agent=False,
            sock=sock,
        )
        return client

    raise ValueError("no SSH credential secret provided")


def _open_jumphost_channel(
    jumphost_cred: dict,
    target_host: str,
    target_port: int,
) -> tuple[paramiko.SSHClient, paramiko.Channel]:
    """Connect to the jumphost and open a direct-tcpip channel to the target.

    Returns (jumphost_client, channel). The caller is responsible for closing
    both the channel (implicitly via target client close) and jumphost_client.
    """
    try:
        jumphost_client = _ssh_connect(
            host=jumphost_cred["host"],
            username=jumphost_cred["username"],
            port=int(jumphost_cred.get("port") or 22),
            auth_type=str(jumphost_cred.get("auth_type") or "key"),
            password=jumphost_cred.get("password"),
            private_key=jumphost_cred.get("private_key"),
            key_passphrase=jumphost_cred.get("key_passphrase"),
        )
    except AuthenticationException as exc:
        raise SSHException(
            f"Jumphost {jumphost_cred['host']}: authentication failed"
        ) from exc
    except (NoValidConnectionsError, TimeoutError) as exc:
        raise SSHException(
            f"Jumphost {jumphost_cred['host']}: host unreachable or connection refused"
        ) from exc

    try:
        transport = jumphost_client.get_transport()
        if transport is None:
            raise SSHException("transport not available after connect")
        channel = transport.open_channel(
            "direct-tcpip",
            (target_host, target_port),
            ("", 0),
            timeout=SSH_CONNECT_TIMEOUT,
        )
    except Exception as exc:
        jumphost_client.close()
        raise SSHException(
            f"Jumphost {jumphost_cred['host']}: failed to open channel to {target_host}:{target_port}: {exc}"
        ) from exc

    return jumphost_client, channel


def _parse_os_release(content: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed[key.strip()] = value.strip().strip('"')
    return parsed


def _detect_kubernetes(
    client: paramiko.SSHClient, log_lines: list[str]
) -> dict[str, Any]:
    """Detect existing Kubernetes installation and status."""
    result: dict[str, Any] = {
        "k8s_installed": False,
        "k8s_running": False,
        "k8s_version": None,
        "k8s_distribution": None,
        "k8s_role": None,
    }

    # --- kind: runs K8s inside Docker containers, no host kubelet service ---
    rc, _kind_path = _ssh_exec(client, "which kind 2>/dev/null")
    if rc == 0:
        rc2, kind_clusters = _ssh_exec(client, "kind get clusters 2>/dev/null")
        if rc2 == 0 and kind_clusters.strip():
            cluster_names = [c.strip() for c in kind_clusters.strip().splitlines() if c.strip()]
            log_lines.append(f"kind clusters: {', '.join(cluster_names)}")
            result["k8s_installed"] = True
            result["k8s_running"] = True
            result["k8s_distribution"] = "kind"
            result["k8s_role"] = "kind-host"
            # Try to get the server version from the first cluster's control-plane container
            first = cluster_names[0]
            rc3, ver_out = _ssh_exec(
                client,
                f"docker exec {first}-control-plane kubectl version -o json 2>/dev/null"
                f" || docker exec {first}-control-plane kubectl version --short 2>/dev/null",
            )
            if rc3 == 0 and ver_out:
                ver_match = re.search(r'"gitVersion":\s*"(v[\d.]+)"', ver_out)
                if not ver_match:
                    ver_match = re.search(r"Server Version:\s*(v[\d.]+)", ver_out)
                if ver_match:
                    result["k8s_version"] = ver_match.group(1)
            return result
        else:
            log_lines.append("kind installed but no active clusters")

    # --- kubelet-based distributions (k3s, rke2, kubeadm, microk8s) ---
    rc, out = _ssh_exec(client, "systemctl is-active kubelet 2>/dev/null")
    kubelet_active = rc == 0 and "active" in out
    log_lines.append(f"kubelet: {out}")

    # Detect distribution
    distribution = None
    if _ssh_exec(client, "which k3s 2>/dev/null")[0] == 0:
        distribution = "k3s"
    elif _ssh_exec(client, "which rke2 2>/dev/null")[0] == 0 or \
         _ssh_exec(client, "systemctl is-enabled rke2-server 2>/dev/null || systemctl is-enabled rke2-agent 2>/dev/null")[0] == 0:
        distribution = "rke2"
    elif _ssh_exec(client, "which kubeadm 2>/dev/null")[0] == 0:
        distribution = "kubeadm"
    elif _ssh_exec(client, "which microk8s 2>/dev/null")[0] == 0:
        distribution = "microk8s"
    log_lines.append(f"k8s distribution: {distribution}")

    # Get kubectl version
    kubectl_cmds = [
        "kubectl version --client --short 2>/dev/null",
        "kubectl version --client -o json 2>/dev/null",
        "k3s kubectl version --client --short 2>/dev/null",
    ]
    for cmd in kubectl_cmds:
        rc, out = _ssh_exec(client, cmd)
        if rc == 0 and out:
            version_match = re.search(r"v(\d+\.\d+\.\d+)", out)
            if version_match:
                result["k8s_version"] = version_match.group(0)
                result["k8s_installed"] = True
                break

    if kubelet_active:
        result["k8s_installed"] = True
        result["k8s_running"] = True

    result["k8s_distribution"] = distribution

    # Detect role
    if result["k8s_running"]:
        rc, _out = _ssh_exec(client, "ls /etc/kubernetes/manifests/kube-apiserver.yaml 2>/dev/null")
        if rc == 0:
            result["k8s_role"] = "control-plane"
        else:
            rc, out = _ssh_exec(client, "systemctl is-active rke2-server 2>/dev/null")
            if rc == 0 and "active" in out:
                result["k8s_role"] = "control-plane"
            else:
                result["k8s_role"] = "worker"

    return result


def _detect_container_runtime(
    client: paramiko.SSHClient, log_lines: list[str]
) -> str | None:
    """Detect container runtime (containerd, cri-o, docker)."""
    rc, out = _ssh_exec(client, "containerd --version 2>/dev/null")
    if rc == 0 and out:
        log_lines.append(f"containerd: {out}")
        return out.strip()

    rc, out = _ssh_exec(client, "crio --version 2>/dev/null")
    if rc == 0 and out:
        version_line = out.splitlines()[0] if out else out
        log_lines.append(f"cri-o: {version_line}")
        return version_line.strip()

    rc, out = _ssh_exec(client, "docker --version 2>/dev/null")
    if rc == 0 and out:
        log_lines.append(f"docker: {out}")
        return out.strip()

    return None


def _probe_node_ssh(
    *,
    host: str,
    username: str,
    port: int,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    key_passphrase: str | None = None,
    jumphost_cred: dict | None = None,
) -> dict[str, Any]:
    """
    SSH into a node and discover OS, hardware, and K8s status.

    When jumphost_cred is provided all traffic is tunneled through it via a
    direct-tcpip channel (equivalent to ssh -J).

    Returns a dict with all discovery fields + a 'log' field with raw output.
    """
    jumphost_client = None
    sock = None

    if jumphost_cred:
        jumphost_client, sock = _open_jumphost_channel(jumphost_cred, host, port)

    client = _ssh_connect(
        host=host,
        username=username,
        port=port,
        auth_type=auth_type,
        password=password,
        private_key=private_key,
        key_passphrase=key_passphrase,
        sock=sock,
    )
    log_lines: list[str] = []
    if jumphost_cred:
        log_lines.append(f"via jumphost: {jumphost_cred['username']}@{jumphost_cred['host']}:{jumphost_cred.get('port', 22)}")

    try:
        results: dict[str, Any] = {}

        # --- Hostname ---
        rc, out = _ssh_exec(client, "hostname -f 2>/dev/null || hostname")
        if rc != 0 or not out or out == "localhost":
            rc, out = _ssh_exec(client, "hostname")
        # OpenBMC's `hostname -f` on some Phosphor builds returns a bizarre
        # whitespace-concatenated string like "openbmc-phosphor bf-24.10-17-...
        # 1732883839.0399356" — the hostname plus the BMC's version. Keep
        # only the first token so the UI label stays sane. Proper FQDNs
        # with dots are preserved as-is.
        hostname = out.split()[0] if rc == 0 and out else None
        results["hostname"] = hostname or None
        log_lines.append(f"hostname: {hostname}")

        # --- OS detection ---
        rc, out = _ssh_exec(client, "cat /etc/os-release 2>/dev/null")
        if rc == 0 and out:
            os_info = _parse_os_release(out)
            results["os_type"] = (os_info.get("ID") or "").lower() or None
            results["os_version"] = os_info.get("VERSION_ID")
            results["os_pretty_name"] = os_info.get("PRETTY_NAME")
        else:
            results["os_type"] = None
            results["os_version"] = None
            results["os_pretty_name"] = None
        log_lines.append(f"os-release: {out[:200] if out else 'N/A'}")

        # --- Kernel + architecture ---
        rc, out = _ssh_exec(client, "uname -r")
        results["kernel_version"] = out if rc == 0 and out else None

        rc, out = _ssh_exec(client, "uname -m")
        arch = out if rc == 0 and out else None
        results["architecture"] = arch
        log_lines.append(f"arch: {arch}")

        # --- DPU detection ---
        dpu_info = _detect_dpus(client, arch, log_lines)
        results.update(dpu_info)

        # --- K8s detection ---
        k8s_info = _detect_kubernetes(client, log_lines)
        results.update(k8s_info)

        # --- Container runtime ---
        results["container_runtime"] = _detect_container_runtime(client, log_lines)

        # --- Network interfaces (depends on dpu_details for classification) ---
        results["network_interfaces"] = _detect_network_interfaces(
            client, log_lines, results.get("dpu_details")
        )

        results["log"] = "\n".join(log_lines)
        return results

    finally:
        client.close()
        if jumphost_client:
            jumphost_client.close()


def _probe_kubeconfig_via_ssh(
    *,
    host: str,
    username: str,
    port: int,
    auth_type: str,
    password: str | None,
    private_key: str | None,
    key_passphrase: str | None = None,
    jumphost_cred: dict | None = None,
    k8s_distribution: str | None = None,
) -> dict:
    """SSH to a node and return its kubeconfig contexts for cluster registration.

    Returns a dict with ``contexts`` (list) and ``host`` (str).  Each context
    entry carries ``kubeconfig_b64`` so the caller can directly register the
    cluster without a second round-trip.
    """
    jumphost_client = None
    sock = None

    if jumphost_cred:
        jumphost_client, sock = _open_jumphost_channel(jumphost_cred, host, port)

    client = _ssh_connect(
        host=host,
        username=username,
        port=port,
        auth_type=auth_type,
        password=password,
        private_key=private_key,
        key_passphrase=key_passphrase,
        sock=sock,
    )

    try:
        if k8s_distribution == "kind":
            rc, clusters_out = _ssh_exec(client, "kind get clusters 2>/dev/null")
            if rc != 0 or not clusters_out.strip():
                raise BadRequestError("No kind clusters found on this node")

            cluster_names = [c.strip() for c in clusters_out.strip().splitlines() if c.strip()]
            contexts: list[dict] = []
            for cluster_name in cluster_names:
                rc2, kubeconfig_raw = _ssh_exec(
                    client, f"kind get kubeconfig --name {cluster_name} 2>/dev/null"
                )
                if rc2 != 0 or not kubeconfig_raw:
                    continue
                try:
                    kc = yaml.safe_load(kubeconfig_raw)
                except Exception:
                    continue
                cluster_map = {
                    c["name"]: (c.get("cluster") or {}).get("server", "")
                    for c in (kc.get("clusters") or [])
                    if isinstance(c, dict)
                }
                kubeconfig_b64 = base64.b64encode(kubeconfig_raw.encode()).decode()
                for ctx in kc.get("contexts") or []:
                    if not isinstance(ctx, dict):
                        continue
                    ctx_data = ctx.get("context") or {}
                    cluster_ref = ctx_data.get("cluster", "")
                    contexts.append({
                        "name": ctx.get("name", cluster_name),
                        "cluster": cluster_ref,
                        "api_server": cluster_map.get(cluster_ref, ""),
                        "is_current": len(contexts) == 0,  # mark the first one as current
                        "kubeconfig_b64": kubeconfig_b64,
                    })

            if not contexts:
                raise BadRequestError("Could not extract kubeconfig from kind cluster(s)")
            return {"contexts": contexts, "host": host}

        else:
            rc, kubeconfig_raw = _ssh_exec(client, "kubectl config view --raw 2>/dev/null")
            if rc != 0 or not kubeconfig_raw:
                raise BadRequestError("kubectl config view failed — no kubeconfig found on this node")

            try:
                kc = yaml.safe_load(kubeconfig_raw)
            except Exception as exc:
                raise BadRequestError(f"Failed to parse kubeconfig: {exc}") from exc

            kubeconfig_b64 = base64.b64encode(kubeconfig_raw.encode()).decode()
            current_context = kc.get("current-context", "")
            cluster_map = {
                c["name"]: (c.get("cluster") or {}).get("server", "")
                for c in (kc.get("clusters") or [])
                if isinstance(c, dict)
            }
            contexts = []
            for ctx in kc.get("contexts") or []:
                if not isinstance(ctx, dict):
                    continue
                ctx_data = ctx.get("context") or {}
                cluster_ref = ctx_data.get("cluster", "")
                contexts.append({
                    "name": ctx.get("name", ""),
                    "cluster": cluster_ref,
                    "api_server": cluster_map.get(cluster_ref, ""),
                    "is_current": ctx.get("name") == current_context,
                    "kubeconfig_b64": kubeconfig_b64,
                })

            if not contexts:
                raise BadRequestError("No contexts found in kubeconfig")
            return {"contexts": contexts, "host": host}

    finally:
        client.close()
        if jumphost_client:
            jumphost_client.close()


# ──────────────────────────────────────────────────────────────────────────
# BlueField BMC detection (phase 3.5 — Register-as-DPU from Discovery)
# ──────────────────────────────────────────────────────────────────────────

def _probe_bluefield_bmc(
    ip_address: str,
    jumphost_cred: dict | None = None,
    *,
    operator_auth: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Quick Redfish service-root probe to detect a BlueField DPU BMC.

    Called once per discovered node; runs regardless of SSH outcome so
    BMC-only endpoints (which don't serve standard SSH) still surface as
    registrable DPUs.

    Auth ladder for the service-root call:

      1. **Anonymous** — some OpenBMC builds expose `/redfish/v1/`
         without auth, and probing once before sending creds avoids
         leaking them to a wrong endpoint.
      2. **Operator-supplied** — when discovery was triggered with a
         BMC username + password (the SSH creds the operator entered),
         we try those next. On a BlueField BMC the SSH and Redfish
         accounts are the same root user, so this is the path that
         lets richer fetches (`Systems/Bluefield` for serial number)
         actually succeed.
      3. **`root` / `0penBmc`** — Nvidia's factory default. Last
         resort, useful when discovery was run before the operator
         knew the password.

    Positive signal (any one is enough):
      * `Product` field contains "bluefield"
      * `Vendor` contains "nvidia" AND (`Name` or `Id`) contains
        "bluefield" or "bmc"

    Whichever auth (or no auth) was accepted by the service-root is
    reused for the follow-up `/Systems/Bluefield` call so the auth
    ladder is climbed once, not twice.

    When `jumphost_cred` is provided the HTTPS call is tunneled through
    the jumphost via paramiko direct-tcpip — matches the same reach
    restriction the SSH probe honours in lab setups.
    """
    result: dict[str, Any] = {
        "is_dpu_bmc": False,
        "product": None,
        "serial_number": None,
        # Phase 2: richer Redfish capture, keyed by Redfish-style field
        # names so the UI's BMC panel can render whatever happens to be
        # present. Stays empty when nothing on the auth ladder
        # authenticates.
        "redfish": {},
        # True when the auth that unlocked an authenticated Redfish
        # endpoint was Nvidia's factory default `root` / `0penBmc` —
        # a security signal the UI surfaces with a warning chip so the
        # operator knows the BMC still needs a real password.
        "uses_default_password": False,
    }

    # Build a deduped auth ladder. None == anonymous.
    NVIDIA_DEFAULT = ("root", "0penBmc")
    auth_ladder: list[tuple[str, str] | None] = [None]
    seen: set[tuple[str, str] | None] = {None}
    for cand in (operator_auth, NVIDIA_DEFAULT):
        if cand is not None and cand not in seen:
            auth_ladder.append(cand)
            seen.add(cand)

    # Pick the transport. Both return (status_code, body_bytes) or None.
    if jumphost_cred:
        transport_ctx = _JumphostRedfishTransport(jumphost_cred, bmc_ip=ip_address)
    else:
        transport_ctx = _DirectRedfishTransport(bmc_ip=ip_address)

    with transport_ctx as transport:
        if transport is None:
            return result

        # Some OpenBMC builds expose `/redfish/v1/` anonymously while
        # gating deeper resources (Systems/Bluefield, BIOS, …) behind
        # auth. Climbing the ladder per-path means a wide-open service
        # root doesn't lock us out of the richer reads.
        sr = _redfish_get_climb(transport, "/redfish/v1/", auth_ladder)
        if sr is None:
            return result
        status, body, sr_auth, sr_pcr = sr
        # If service-root itself surfaces PasswordChangeRequired (rare;
        # most BMCs serve it openly), still flag the security signal.
        if sr_pcr:
            result["uses_default_password"] = True
            result["redfish"]["PasswordChangeRequired"] = True
        if status != 200:
            logger.debug(
                "BlueField BMC probe for %s: HTTP %s from service root "
                "(after %d auth attempt(s))",
                ip_address, status, len(auth_ladder),
            )
            return result

        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except (ValueError, AttributeError):
            return result
        if not isinstance(payload, dict):
            return result

        product = payload.get("Product") if isinstance(payload.get("Product"), str) else None
        vendor = payload.get("Vendor") if isinstance(payload.get("Vendor"), str) else None
        name = payload.get("Name") if isinstance(payload.get("Name"), str) else None
        id_ = payload.get("Id") if isinstance(payload.get("Id"), str) else None

        signal_strings = [s.lower() for s in (product, vendor, name, id_) if s]
        has_bluefield_word = any("bluefield" in s for s in signal_strings)
        is_nvidia = any("nvidia" in s for s in signal_strings)
        is_bmc = any(("bmc" in s) for s in signal_strings)

        if not (has_bluefield_word or (is_nvidia and is_bmc)):
            logger.debug(
                "BlueField BMC probe for %s: service-root did not match (Product=%r, "
                "Vendor=%r, Name=%r, Id=%r)",
                ip_address, product, vendor, name, id_,
            )
            return result

        result["is_dpu_bmc"] = True
        result["product"] = product or vendor or name or id_
        logger.info(
            "BlueField BMC detected at %s via %s (Product=%r Vendor=%r Name=%r Id=%r)",
            ip_address,
            "anonymous" if sr_auth is None else f"basic-auth ({sr_auth[0]})",
            product, vendor, name, id_,
        )

        # Each richer path climbs the ladder again — anonymous root
        # doesn't imply anonymous Systems/Bios. We thread the auth that
        # most-recently won forward as the first rung so once one path
        # authenticates with operator creds, every subsequent call hits
        # 200 on the first try instead of climbing through 401s.
        last_auth = sr_auth

        sys_payload, sys_auth, sys_pcr = _fetch_redfish_json(
            transport, "/redfish/v1/Systems/Bluefield",
            _ladder_with(last_auth, auth_ladder),
        )
        # PasswordChangeRequired implies the default password is in
        # effect and the BMC won't serve anything until rotated.
        # Flag it so the UI shows the warning chip even when no rich
        # payload could be captured.
        if sys_pcr:
            result["uses_default_password"] = True
            result["redfish"]["PasswordChangeRequired"] = True
        if sys_payload is not None:
            last_auth = sys_auth
            if sys_auth == NVIDIA_DEFAULT:
                result["uses_default_password"] = True
            sn = sys_payload.get("SerialNumber")
            if isinstance(sn, str):
                result["serial_number"] = sn
            # Capture the richer fields the UI's BMC panel renders.
            # Each key is optional — the panel ignores absent ones.
            redfish = result["redfish"]
            for k in (
                "Manufacturer", "Model", "PartNumber", "BiosVersion",
                "PowerState", "ProcessorSummary", "Status",
                # UUID == "00000000-..." and BootProgress.LastStateTime
                # == epoch zero are both signals that the SoC has not
                # pushed identity / boot data to the BMC — common with
                # newer bf-bundle BSPs (3.1.0+) where the SMBIOS push
                # path appears to have regressed.
                "UUID", "BootProgress",
            ):
                v = sys_payload.get(k)
                if v is not None:
                    redfish[k] = v
            # Memory total is nested under MemorySummary.TotalSystemMemoryGiB.
            mem = sys_payload.get("MemorySummary")
            if isinstance(mem, dict):
                gib = mem.get("TotalSystemMemoryGiB")
                if isinstance(gib, (int, float)):
                    redfish["MemoryGiB"] = gib

        # Manager probe — BMC firmware version + state.
        mgr_payload, mgr_auth, mgr_pcr = _fetch_redfish_json(
            transport, "/redfish/v1/Managers/Bluefield_BMC",
            _ladder_with(last_auth, auth_ladder),
        )
        if mgr_pcr:
            result["uses_default_password"] = True
            result["redfish"]["PasswordChangeRequired"] = True
        if mgr_payload is not None:
            last_auth = mgr_auth
            if mgr_auth == NVIDIA_DEFAULT:
                result["uses_default_password"] = True
            redfish = result["redfish"]
            fw = mgr_payload.get("FirmwareVersion")
            if isinstance(fw, str):
                redfish["BmcFirmwareVersion"] = fw
            mgr_state = mgr_payload.get("PowerState")
            if isinstance(mgr_state, str):
                redfish["BmcPowerState"] = mgr_state

        # BIOS attributes — NicMode and HostPrivilegeLevel are the two
        # Forge cares about. The Bios resource lives at
        # /Systems/Bluefield/Bios; attributes are nested under
        # `Attributes`.
        #
        # Caveat: on newer bf-bundle BSPs (3.1.0 / DPU-UEFI 4.12+) the
        # `Attributes` block is empty — the legacy view was retired.
        # The replacement values live under
        # /Systems/Bluefield/Oem/Nvidia/Connectx/ExternalHostPrivileges
        # which we fetch below.
        bios_payload, bios_auth, bios_pcr = _fetch_redfish_json(
            transport, "/redfish/v1/Systems/Bluefield/Bios",
            _ladder_with(last_auth, auth_ladder),
        )
        if bios_pcr:
            result["uses_default_password"] = True
            result["redfish"]["PasswordChangeRequired"] = True
        if bios_payload is not None:
            if bios_auth == NVIDIA_DEFAULT:
                result["uses_default_password"] = True
            attrs = bios_payload.get("Attributes")
            if isinstance(attrs, dict):
                redfish = result["redfish"]
                for k in (
                    "NicMode", "HostPrivilegeLevel", "InternalCPUModel",
                    "FieldMode", "EnableSMMU",
                ):
                    v = attrs.get(k)
                    if v is not None:
                        redfish[k] = v

        # /Systems/Bluefield/Oem/Nvidia — small but useful fields, served
        # on both old and new BSPs.
        oem_payload, oem_auth, oem_pcr = _fetch_redfish_json(
            transport, "/redfish/v1/Systems/Bluefield/Oem/Nvidia",
            _ladder_with(last_auth, auth_ladder),
        )
        if oem_pcr:
            result["uses_default_password"] = True
            result["redfish"]["PasswordChangeRequired"] = True
        if oem_payload is not None:
            last_auth = oem_auth
            if oem_auth == NVIDIA_DEFAULT:
                result["uses_default_password"] = True
            redfish = result["redfish"]
            for k in ("BaseGUID", "BaseMAC", "HostRshim"):
                v = oem_payload.get(k)
                if isinstance(v, str):
                    redfish[k] = v

        # ExternalHostPrivileges — the newer-BSP replacement for the
        # legacy BIOS `HostPrivilegeLevel` flag. Captured as a dict of
        # `HostPrivXxx` keys mapping to "Default" / "Enabled" / "Disabled".
        ehp_payload, ehp_auth, ehp_pcr = _fetch_redfish_json(
            transport,
            "/redfish/v1/Systems/Bluefield/Oem/Nvidia/Connectx/ExternalHostPrivileges",
            _ladder_with(last_auth, auth_ladder),
        )
        if ehp_pcr:
            result["uses_default_password"] = True
            result["redfish"]["PasswordChangeRequired"] = True
        if ehp_payload is not None:
            last_auth = ehp_auth
            if ehp_auth == NVIDIA_DEFAULT:
                result["uses_default_password"] = True
            ehp = ehp_payload.get("ExternalHostPrivilege")
            if isinstance(ehp, dict):
                result["redfish"]["ExternalHostPrivilege"] = ehp

        # FirmwareInventory — capture versions for the components that
        # most often differ between two same-model DPUs. When two DPUs
        # behave differently despite identical model + BMC FW, the
        # delta usually shows up in DPU_UEFI / DPU_BSP / DPU_OS — that's
        # the diagnostic Job #3 needed.
        FW_COMPONENTS = (
            "DPU_UEFI", "DPU_ATF", "DPU_BSP", "DPU_NIC",
            "DPU_OS", "DPU_OFED", "DPU_NODE",
        )
        fw_versions: dict[str, str] = {}
        for comp in FW_COMPONENTS:
            fw_payload, fw_auth, fw_pcr = _fetch_redfish_json(
                transport,
                f"/redfish/v1/UpdateService/FirmwareInventory/{comp}",
                _ladder_with(last_auth, auth_ladder),
            )
            if fw_pcr:
                result["uses_default_password"] = True
                result["redfish"]["PasswordChangeRequired"] = True
            if fw_payload is not None:
                last_auth = fw_auth
                if fw_auth == NVIDIA_DEFAULT:
                    result["uses_default_password"] = True
                ver = fw_payload.get("Version")
                if isinstance(ver, str) and ver:
                    fw_versions[comp] = ver
        if fw_versions:
            result["redfish"]["FirmwareInventory"] = fw_versions

    return result


def _ladder_with(
    preferred: tuple[str, str] | None,
    base: list[tuple[str, str] | None],
) -> list[tuple[str, str] | None]:
    """Return `base` reordered so `preferred` is tried first."""
    return [preferred] + [a for a in base if a != preferred]


def _fetch_redfish_json(
    transport,
    path: str,
    auth_ladder: list[tuple[str, str] | None],
) -> tuple[dict | None, tuple[str, str] | None, bool]:
    """Climb the ladder for `path`.

    Returns ``(payload, auth_used, password_change_required)`` —
    payload is None when none of the rungs returned 200 / parseable
    JSON; auth_used is the rung that won (or the last attempted on
    failure); password_change_required is True when at least one
    rung's response was a 403 PasswordChangeRequired (i.e. the BMC
    accepts the default but refuses to serve resources until rotated).
    """
    r = _redfish_get_climb(transport, path, auth_ladder)
    if r is None:
        return None, None, False
    status, body, auth, pcr = r
    if status != 200:
        return None, auth, pcr
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError):
        return None, auth, pcr
    return (payload if isinstance(payload, dict) else None), auth, pcr


def _redfish_get_climb(
    transport, path: str, auth_ladder: list[tuple[str, str] | None],
) -> tuple[int, bytes, tuple[str, str] | None, bool] | None:
    """Try each auth in `auth_ladder` until one returns non-401-or-PCR.

    Returns `(status_code, body, auth_used, password_change_required)`
    for the first response that wasn't 401 or 403-PasswordChangeRequired,
    or None if the transport itself failed (connection refused, TLS
    handshake error, …) — in which case no further auth attempt would
    help. A 401/PCR across every rung yields the last response with
    `password_change_required` set when at least one rung saw a PCR.

    BlueField OpenBMC returns HTTP 403 with the `PasswordChangeRequired`
    message id when the default `0penBmc` authenticated but the
    password must be changed before any resource is served. We treat
    that as "auth valid, just unusable" — keep climbing in case
    another rung is set to a properly-rotated password, but flag it
    so callers can surface the security signal.
    """
    last_status = 0
    last_body: bytes = b""
    last_auth: tuple[str, str] | None = None
    pcr = False
    for auth in auth_ladder:
        r = transport.get(path, auth=auth)
        if r is None:
            return None
        status, body = r
        last_status, last_body, last_auth = status, body, auth
        if status == 401:
            continue
        if status == 403 and _is_password_change_required(body):
            pcr = True
            continue
        return status, body, auth, pcr
    # Every rung was 401 or PCR. Surface the final response so callers
    # can log it; pcr says whether at least one of those was a default-
    # password change-required signal.
    return last_status, last_body, last_auth, pcr


def _is_password_change_required(body: bytes) -> bool:
    """True when a Redfish error body carries `PasswordChangeRequired`.

    BlueField OpenBMC returns HTTP 403 with this message id when the
    factory-default `root` / `0penBmc` authenticated but the BMC
    refuses to serve any resource until the password is rotated. We
    use this to flag `uses_default_password` even though no rich
    payload could be fetched.

    Body shape varies — some Redfish builds put `MessageId` at the
    top level, others nest it under `@Message.ExtendedInfo` — so we
    search both.
    """
    if not body:
        return False
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError):
        return False
    if not isinstance(payload, dict):
        return False

    def _has_pcr(d: dict) -> bool:
        mid = d.get("MessageId")
        return isinstance(mid, str) and "PasswordChangeRequired" in mid

    if _has_pcr(payload):
        return True
    info = payload.get("@Message.ExtendedInfo")
    if isinstance(info, list):
        for entry in info:
            if isinstance(entry, dict) and _has_pcr(entry):
                return True
    # Some BMCs wrap the error under "error".
    err = payload.get("error")
    if isinstance(err, dict):
        if _has_pcr(err):
            return True
        nested = err.get("@Message.ExtendedInfo")
        if isinstance(nested, list):
            for entry in nested:
                if isinstance(entry, dict) and _has_pcr(entry):
                    return True
    return False


# ── Redfish transport shims (direct + jumphost-tunnelled) ─────────────────


class _DirectRedfishTransport:
    """Plain `requests.get` against the BMC. Used when Forge can reach the
    BMC directly without an SSH tunnel."""

    def __init__(self, *, bmc_ip: str, timeout: int = 6):
        self.bmc_ip = bmc_ip
        self.timeout = timeout
        self.last_auth_used = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def get(self, path: str, auth: tuple[str, str] | None = None):
        import requests
        from requests.auth import HTTPBasicAuth
        try:
            resp = requests.get(
                f"https://{self.bmc_ip}{path}",
                verify=False, timeout=self.timeout,
                auth=HTTPBasicAuth(*auth) if auth else None,
            )
        except requests.RequestException:
            return None
        if auth is not None:
            self.last_auth_used = True
        return resp.status_code, resp.content


class _JumphostRedfishTransport:
    """HTTPS through a paramiko direct-tcpip channel via the discovery
    jumphost. No `requests` involved — we speak raw HTTP/1.1 over the
    TLS-wrapped channel so there's no HTTPAdapter socket acrobatics."""

    def __init__(self, jumphost_cred: dict, *, bmc_ip: str, timeout: int = 10):
        self.jumphost_cred = jumphost_cred
        self.bmc_ip = bmc_ip
        self.timeout = timeout
        self._client = None
        self.last_auth_used = False

    def __enter__(self):
        try:
            self._client = _open_jumphost_ssh(self.jumphost_cred, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "BlueField BMC probe: jumphost SSH to %s failed: %s",
                self.jumphost_cred.get("host"), exc,
            )
            self._client = None
        return self if self._client else None

    def __exit__(self, *_):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
        return False

    def get(self, path: str, auth: tuple[str, str] | None = None):
        if self._client is None:
            return None
        result = _https_get_via_channel(
            self._client,
            host=self.bmc_ip, port=443, path=path,
            auth=auth, timeout=self.timeout,
        )
        if result is not None and auth is not None:
            self.last_auth_used = True
        return result


def _open_jumphost_ssh(jumphost_cred: dict, *, timeout: int = 10):
    """Paramiko SSHClient to the jumphost, matching the auth logic of
    `_ssh_connect` used by the main discovery path."""
    import paramiko

    from services.ssh.paramiko_utils import load_private_key_from_content

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = None
    auth_type = jumphost_cred.get("auth_type") or "key"
    if auth_type == "key" and jumphost_cred.get("private_key"):
        pkey = load_private_key_from_content(
            jumphost_cred["private_key"],
            passphrase=jumphost_cred.get("key_passphrase"),
        )
    client.connect(
        hostname=jumphost_cred["host"],
        port=int(jumphost_cred.get("port") or 22),
        username=jumphost_cred["username"],
        password=jumphost_cred.get("password") if auth_type == "password" else None,
        pkey=pkey,
        timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def _https_get_via_channel(
    client,
    *,
    host: str,
    port: int,
    path: str,
    auth: tuple[str, str] | None = None,
    timeout: int = 10,
):
    """Single HTTP/1.1 GET over a paramiko direct-tcpip channel with TLS.

    Uses ssl.MemoryBIO so we don't need a true socket object — paramiko's
    Channel doesn't implement `getsockopt()` which `ssl.wrap_socket()`
    requires on 3.11+.

    Returns (status_code, body_bytes) or None on any failure.
    """
    import base64
    import ssl

    try:
        transport = client.get_transport()
        if transport is None:
            return None
        channel = transport.open_channel(
            "direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=timeout,
        )
    except Exception:  # noqa: BLE001
        return None
    channel.settimeout(timeout)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    try:
        sslobj = ctx.wrap_bio(incoming, outgoing, server_hostname=host)
    except Exception:  # noqa: BLE001
        try:
            channel.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    def _drain_outgoing() -> bool:
        data = outgoing.read()
        if data:
            try:
                channel.sendall(data)
            except Exception:  # noqa: BLE001
                return False
        return True

    def _pump_incoming() -> bool:
        try:
            chunk = channel.recv(65536)
        except Exception:  # noqa: BLE001
            return False
        if not chunk:
            incoming.write_eof()
            return True
        incoming.write(chunk)
        return True

    # --- Handshake ---------------------------------------------------
    try:
        while True:
            try:
                sslobj.do_handshake()
                break
            except ssl.SSLWantReadError:
                if not _drain_outgoing():
                    raise
                if not _pump_incoming():
                    raise ssl.SSLError("channel closed during handshake")
            except ssl.SSLWantWriteError:
                if not _drain_outgoing():
                    raise
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Redfish tunnel: TLS handshake to %s:%s failed: %s",
            host, port, exc,
        )
        try:
            channel.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    # --- Send request ------------------------------------------------
    headers = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "Accept: application/json",
        "User-Agent: bnk-forge-discovery/1.0",
        "Connection: close",
    ]
    if auth is not None:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers.append(f"Authorization: Basic {token}")
    req = ("\r\n".join(headers) + "\r\n\r\n").encode()

    try:
        written = 0
        while written < len(req):
            try:
                n = sslobj.write(req[written:])
                written += n
                _drain_outgoing()
            except ssl.SSLWantWriteError:
                _drain_outgoing()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Redfish tunnel: send failed: %s", exc)
        try:
            channel.close()
        except Exception:  # noqa: BLE001
            pass
        return None

    # --- Read response ----------------------------------------------
    raw = bytearray()
    try:
        while True:
            try:
                chunk = sslobj.read(65536)
                if not chunk:
                    break
                raw.extend(chunk)
            except ssl.SSLWantReadError:
                if not _pump_incoming():
                    break
            except ssl.SSLZeroReturnError:
                break
            except ssl.SSLError:
                break
    finally:
        try:
            channel.close()
        except Exception:  # noqa: BLE001
            pass

    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        return None
    header_blob = bytes(raw[:sep]).decode("iso-8859-1", errors="replace")
    body = bytes(raw[sep + 4:])
    status_line = header_blob.split("\r\n", 1)[0]
    try:
        status_code = int(status_line.split(" ", 2)[1])
    except (IndexError, ValueError):
        return None

    # Handle chunked transfer encoding — BlueField BMCs commonly use it.
    headers_lower = header_blob.lower()
    if "transfer-encoding: chunked" in headers_lower:
        body = _decode_chunked_body(body)

    return status_code, body


def _decode_chunked_body(body: bytes) -> bytes:
    """Minimal HTTP/1.1 chunked decoder. Returns empty bytes on malformed
    input rather than raising — callers treat that as "bad response"."""
    out = bytearray()
    i = 0
    n = len(body)
    while i < n:
        nl = body.find(b"\r\n", i)
        if nl < 0:
            break
        size_hex = body[i:nl].split(b";", 1)[0].strip()
        try:
            size = int(size_hex, 16)
        except ValueError:
            break
        i = nl + 2
        if size == 0:
            break
        if i + size > n:
            break
        out.extend(body[i : i + size])
        i += size
        # Skip trailing CRLF after the chunk
        if body[i : i + 2] == b"\r\n":
            i += 2
    return bytes(out)


def _discovery_parallelism(node_count: int, jumphost_cred: dict | None) -> int:
    """Pick the max-workers cap for a discovery run.

    Two tiers: direct probes parallelise widely; probes via a jumphost are
    throttled because they all share one intermediate SSH server whose
    MaxSessions/MaxStartups may be low. `DISCOVERY_MAX_PARALLEL` (if set)
    overrides both as a single kill-switch for legacy deployments.
    """
    if node_count <= 0:
        return 1
    if settings.DISCOVERY_MAX_PARALLEL is not None:
        cap = int(settings.DISCOVERY_MAX_PARALLEL)
    elif jumphost_cred:
        cap = int(settings.DISCOVERY_MAX_PARALLEL_VIA_JUMPHOST)
    else:
        cap = int(settings.DISCOVERY_MAX_PARALLEL_DIRECT)
    return max(1, min(node_count, cap))
