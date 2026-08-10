"""Unit tests for DiscoveryService.register_node_as_bare_metal_host
and register_all_hosts_in_job.

The Bare Metal tab is the natural home for the x86 servers carrying
the registered DPUs. These actions create / look up BareMetalHost
rows from a Discovery job. Idempotent on (project, host_ip) — a
bulk re-register on a refreshed discovery is a no-op for already-
registered hosts.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401
from core.encryption import encrypt_value
from core.errors import BadRequestError
from database import Base
from models.bare_metal import BareMetalHost
from models.discovery import DiscoveredNode, DiscoveryJob
from models.enums import DiscoveredNodeStatus, DiscoveryJobStatus
from models.project import Project
from models.ssh_credential import SSHCredential
from services.discovery_service import DiscoveryService


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    s = Session(engine)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def project(db: Session) -> Project:
    p = Project(name="p", description="")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def saved_cred(db: Session) -> SSHCredential:
    c = SSHCredential(
        name="test-saved",
        host="placeholder",
        port=22,
        username="ubuntu",
        auth_type="password",
        password_encrypted=encrypt_value("pw"),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture
def job(db: Session, project: Project, saved_cred: SSHCredential) -> DiscoveryJob:
    j = DiscoveryJob(
        project_id=project.id,
        ssh_port=22,
        ssh_credential_id=saved_cred.id,
        status=DiscoveryJobStatus.COMPLETED.value,
        total_nodes=1,
        completed_nodes=1,
        failed_nodes=0,
    )
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def _make_dpu_host_node(
    db: Session, job: DiscoveryJob, ip: str, *,
    hostname: str | None = None,
    dpu_count: int = 1,
) -> DiscoveredNode:
    n = DiscoveredNode(
        discovery_job_id=job.id,
        ip_address=ip,
        hostname=hostname,
        status=DiscoveredNodeStatus.COMPLETED.value,
        is_dpu_host=True,
        dpu_count=dpu_count,
        dpu_details=[
            {"pci_address": "0000:00:10.0", "description": "BlueField-3"},
        ],
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


# ── Single-node registration ───────────────────────────────────────────────


class TestRegisterNodeAsHost:
    def test_creates_host_with_saved_credential(self, db, project, job, saved_cred):
        node = _make_dpu_host_node(db, job, "10.0.0.10", hostname="worker1")
        result = DiscoveryService(db).register_node_as_bare_metal_host(
            project_id=project.id, node_id=node.id,
        )
        db.commit()
        assert result["status"] == "created"
        assert result["host_ip"] == "10.0.0.10"
        assert result["name"] == "worker1"
        # The job's saved credential is reused — no synthetic row created.
        host = db.get(BareMetalHost, result["host_id"])
        assert host.ssh_credential_id == saved_cred.id

    def test_falls_back_to_ip_when_no_hostname(self, db, project, job):
        node = _make_dpu_host_node(db, job, "10.0.0.11")
        result = DiscoveryService(db).register_node_as_bare_metal_host(
            project_id=project.id, node_id=node.id,
        )
        db.commit()
        assert result["name"] == "10.0.0.11"

    def test_idempotent_on_same_project_host_ip(self, db, project, job):
        node = _make_dpu_host_node(db, job, "10.0.0.12", hostname="worker2")
        svc = DiscoveryService(db)
        first = svc.register_node_as_bare_metal_host(
            project_id=project.id, node_id=node.id,
        )
        db.commit()
        assert first["status"] == "created"
        # Second call — same project + host_ip → existing, NOT a duplicate.
        second = svc.register_node_as_bare_metal_host(
            project_id=project.id, node_id=node.id,
        )
        db.commit()
        assert second["status"] == "exists"
        assert second["host_id"] == first["host_id"]
        # Only one row in the table — the guard worked.
        rows = (
            db.query(BareMetalHost)
            .filter(
                BareMetalHost.project_id == project.id,
                BareMetalHost.host_ip == "10.0.0.12",
            )
            .all()
        )
        assert len(rows) == 1

    def test_rejects_non_dpu_host_node(self, db, project, job):
        # Plain compute node — no DPUs detected.
        n = DiscoveredNode(
            discovery_job_id=job.id,
            ip_address="10.0.0.50",
            status=DiscoveredNodeStatus.COMPLETED.value,
            is_dpu_host=False, dpu_count=0,
        )
        db.add(n)
        db.commit()
        with pytest.raises(BadRequestError, match="DPU-host"):
            DiscoveryService(db).register_node_as_bare_metal_host(
                project_id=project.id, node_id=n.id,
            )

    def test_rejects_dpu_host_with_zero_dpus(self, db, project, job):
        # Edge case: discovery flagged the node but counted no DPUs.
        n = DiscoveredNode(
            discovery_job_id=job.id,
            ip_address="10.0.0.51",
            status=DiscoveredNodeStatus.COMPLETED.value,
            is_dpu_host=True, dpu_count=0,
        )
        db.add(n)
        db.commit()
        with pytest.raises(BadRequestError, match="DPU-host"):
            DiscoveryService(db).register_node_as_bare_metal_host(
                project_id=project.id, node_id=n.id,
            )


class TestSynthesisedCredential:
    """When discovery used inline username + password (no saved row),
    the bare-metal registration mints an SSHCredential row labelled
    `Discovery: <hostname>` so BareMetalHost can reference it."""

    def test_synthesises_when_no_saved_credential(self, db, project):
        # Job with NO saved cred, only inline.
        j = DiscoveryJob(
            project_id=project.id,
            ssh_port=22,
            ssh_credential_id=None,
            ssh_username="ubuntu",
            ssh_auth_type="password",
            ssh_password_encrypted=encrypt_value("inline-pw"),
            status=DiscoveryJobStatus.COMPLETED.value,
            total_nodes=1, completed_nodes=1, failed_nodes=0,
        )
        db.add(j)
        db.commit()
        db.refresh(j)
        node = _make_dpu_host_node(db, j, "10.0.0.20", hostname="worker3")

        result = DiscoveryService(db).register_node_as_bare_metal_host(
            project_id=project.id, node_id=node.id,
        )
        db.commit()
        host = db.get(BareMetalHost, result["host_id"])
        assert host.ssh_credential_id is not None
        cred = db.get(SSHCredential, host.ssh_credential_id)
        assert cred.name.startswith("Discovery: worker3")
        assert cred.username == "ubuntu"
        # Encrypted password is the SAME blob discovery captured —
        # no re-encrypt needed.
        assert cred.password_encrypted == j.ssh_password_encrypted


# ── Bulk register-all ──────────────────────────────────────────────────────


class TestRegisterAllHostsInJob:
    def test_creates_only_eligible_nodes(self, db, project, job):
        # Eligible
        _make_dpu_host_node(db, job, "10.0.0.30", hostname="worker-a")
        _make_dpu_host_node(db, job, "10.0.0.31", hostname="worker-b")
        # NOT eligible — non-DPU compute node, BMC-only DPU.
        db.add(DiscoveredNode(
            discovery_job_id=job.id, ip_address="10.0.0.32",
            status=DiscoveredNodeStatus.COMPLETED.value,
            is_dpu_host=False, dpu_count=0,
        ))
        db.add(DiscoveredNode(
            discovery_job_id=job.id, ip_address="10.0.0.33",
            status=DiscoveredNodeStatus.COMPLETED.value,
            is_dpu_bmc=True, dpu_count=0,
        ))
        db.commit()

        result = DiscoveryService(db).register_all_hosts_in_job(
            project_id=project.id, job_id=job.id,
        )
        db.commit()
        assert result["created"] == 2
        assert result["existing"] == 0
        # Only the two DPU-host rows landed in BareMetalHost.
        rows = (
            db.query(BareMetalHost.host_ip)
            .filter(BareMetalHost.project_id == project.id)
            .all()
        )
        ips = {r[0] for r in rows}
        assert ips == {"10.0.0.30", "10.0.0.31"}

    def test_rerun_is_idempotent(self, db, project, job):
        _make_dpu_host_node(db, job, "10.0.0.40", hostname="worker-x")
        svc = DiscoveryService(db)

        first = svc.register_all_hosts_in_job(
            project_id=project.id, job_id=job.id,
        )
        db.commit()
        assert first["created"] == 1 and first["existing"] == 0

        # Rerun on the same job — every host already exists.
        second = svc.register_all_hosts_in_job(
            project_id=project.id, job_id=job.id,
        )
        db.commit()
        assert second["created"] == 0 and second["existing"] == 1
        # Still only one row.
        assert (
            db.query(BareMetalHost)
            .filter(BareMetalHost.project_id == project.id)
            .count()
        ) == 1
