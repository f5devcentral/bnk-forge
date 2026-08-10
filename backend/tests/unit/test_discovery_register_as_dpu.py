"""Unit tests for DiscoveryService.register_node_as_dpu.

Exercises the three decision branches:
  - BMC-detected discovered node → auto-register with node IP as bmc_ip.
  - DPU-OS discovered node → requires supplied bmc_ip; registers dpu_os_ip.
  - Neither → rejected.
Plus duplicate + sad-path cases, plus the Phase 3.5 "auto-populate
project OOB default credential from discovery" behaviour.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401
from core.encryption import decrypt_value_or_none, encrypt_value
from core.errors import BadRequestError, NotFoundError
from database import Base
from models.discovery import DiscoveredNode, DiscoveryJob
from models.dpu import Dpu, ProjectDpuSettings
from models.enums import DiscoveredNodeStatus, DiscoveryJobStatus
from models.project import Project
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
def job(db: Session, project: Project) -> DiscoveryJob:
    j = DiscoveryJob(
        project_id=project.id,
        ssh_port=22,
        status=DiscoveryJobStatus.COMPLETED.value,
        total_nodes=1,
        completed_nodes=1,
        failed_nodes=0,
    )
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def _make_node(
    db: Session,
    job: DiscoveryJob,
    ip: str,
    *,
    is_dpu_bmc: bool = False,
    is_dpu_node: bool = False,
    is_dpu_host: bool = False,
    dpu_details: list[dict] | None = None,
    serial: str | None = None,
    hostname: str | None = None,
) -> DiscoveredNode:
    n = DiscoveredNode(
        discovery_job_id=job.id,
        ip_address=ip,
        hostname=hostname,
        status=DiscoveredNodeStatus.COMPLETED.value,
        is_dpu_bmc=is_dpu_bmc,
        is_dpu_node=is_dpu_node,
        is_dpu_host=is_dpu_host,
        dpu_details=dpu_details,
        bmc_serial_number=serial,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


class TestBmcBranch:
    def test_registers_bmc_at_node_ip(self, db, project, job):
        node = _make_node(
            db, job, "192.168.68.100",
            is_dpu_bmc=True, serial="MT2428XZ0N1D", hostname="dpu-bmc",
        )
        result = DiscoveryService(db).register_node_as_dpu(
            project_id=project.id, node_id=node.id,
        )
        db.commit()

        assert len(result["dpus"]) == 1
        assert result["dpus"][0]["access_mode"] == "bmc"
        assert result["dpus"][0]["bmc_ip"] == "192.168.68.100"
        assert result["dpus"][0]["dpu_os_ip"] is None

        dpu = db.query(Dpu).first()
        assert dpu.access_mode == "bmc"
        assert dpu.bmc_ip == "192.168.68.100"
        assert dpu.dpu_os_ip is None
        assert dpu.serial_number == "MT2428XZ0N1D"
        assert dpu.name == "dpu-bmc"


class TestDpuOsBranch:
    def test_requires_bmc_ip_from_body(self, db, project, job):
        node = _make_node(db, job, "192.168.68.79", is_dpu_node=True)
        with pytest.raises(BadRequestError, match="BMC IP"):
            DiscoveryService(db).register_node_as_dpu(
                project_id=project.id, node_id=node.id,
            )

    def test_registers_arm_ip_with_supplied_bmc_ip(self, db, project, job):
        node = _make_node(db, job, "192.168.68.79", is_dpu_node=True)
        result = DiscoveryService(db).register_node_as_dpu(
            project_id=project.id, node_id=node.id, bmc_ip="192.168.68.100",
        )
        db.commit()
        assert len(result["dpus"]) == 1
        assert result["dpus"][0]["access_mode"] == "bmc"
        assert result["dpus"][0]["bmc_ip"] == "192.168.68.100"
        assert result["dpus"][0]["dpu_os_ip"] == "192.168.68.79"

        dpu = db.query(Dpu).first()
        assert dpu.bmc_ip == "192.168.68.100"
        assert dpu.dpu_os_ip == "192.168.68.79"
        assert dpu.dpu_os_reachable is True


class TestNeitherBranch:
    def test_rejects_non_bluefield_nodes(self, db, project, job):
        node = _make_node(db, job, "10.0.0.50")  # neither flag set
        with pytest.raises(BadRequestError, match="neither"):
            DiscoveryService(db).register_node_as_dpu(
                project_id=project.id, node_id=node.id,
            )


class TestInBandHostBranch:
    def test_registers_one_dpu_per_detail_entry(self, db, project, job):
        node = _make_node(
            db, job, "10.0.0.10",
            is_dpu_host=True, hostname="worker1",
            dpu_details=[
                {"pci_address": "0000:01:00.0", "description": "BF3"},
                {"pci_address": "0000:02:00.0", "description": "BF3"},
            ],
        )
        result = DiscoveryService(db).register_node_as_dpu(
            project_id=project.id, node_id=node.id,
        )
        db.commit()

        assert len(result["dpus"]) == 2
        modes = {d["access_mode"] for d in result["dpus"]}
        assert modes == {"in-band"}
        pci_set = {d["pci_address"] for d in result["dpus"]}
        assert pci_set == {"0000:01:00", "0000:02:00"}

        rows = db.query(Dpu).order_by(Dpu.pci_address).all()
        assert len(rows) == 2
        for row in rows:
            assert row.access_mode == "in-band"
            assert row.bmc_ip is None
            assert row.host_node_ip == "10.0.0.10"

    def test_rejects_host_without_dpus(self, db, project, job):
        node = _make_node(
            db, job, "10.0.0.11", is_dpu_host=True, dpu_details=[],
        )
        with pytest.raises(BadRequestError, match="no DPUs detected"):
            DiscoveryService(db).register_node_as_dpu(
                project_id=project.id, node_id=node.id,
            )

    def test_idempotent_skips_already_registered(self, db, project, job):
        node = _make_node(
            db, job, "10.0.0.12",
            is_dpu_host=True, hostname="worker2",
            dpu_details=[{"pci_address": "0000:03:00.0", "description": "BF3"}],
        )
        svc = DiscoveryService(db)
        first = svc.register_node_as_dpu(project_id=project.id, node_id=node.id)
        db.commit()
        assert len(first["dpus"]) == 1

        # Re-register same host → all skipped → error.
        with pytest.raises(BadRequestError, match="already registered"):
            svc.register_node_as_dpu(project_id=project.id, node_id=node.id)


class TestDuplicates:
    def test_rejects_duplicate_bmc_ip_in_project(self, db, project, job):
        node1 = _make_node(db, job, "192.168.68.100", is_dpu_bmc=True)
        node2 = _make_node(db, job, "192.168.68.101", is_dpu_bmc=True)
        svc = DiscoveryService(db)
        svc.register_node_as_dpu(project_id=project.id, node_id=node1.id)
        db.commit()
        # Register node2 explicitly targeting the same bmc_ip via body override.
        with pytest.raises(BadRequestError, match="already registered"):
            svc.register_node_as_dpu(
                project_id=project.id, node_id=node2.id, bmc_ip="192.168.68.100",
            )


class TestNotFound:
    def test_missing_node_raises_not_found(self, db, project):
        with pytest.raises(NotFoundError):
            DiscoveryService(db).register_node_as_dpu(
                project_id=project.id, node_id=9999,
            )

    def test_node_in_other_project_invisible(self, db, project, job):
        other = Project(name="other", description="")
        db.add(other)
        db.commit()
        db.refresh(other)
        # Register node as DPU in 'other' project — should not find it
        node = _make_node(db, job, "1.2.3.4", is_dpu_bmc=True)
        with pytest.raises(NotFoundError):
            DiscoveryService(db).register_node_as_dpu(
                project_id=other.id, node_id=node.id,
            )


class TestPopulateProjectOobFromDiscovery:
    """When a BMC is registered, seed ProjectDpuSettings.default_oob_*
    from the discovery SSH credential if no project default is set yet.

    The working assumption is that all BMCs in a lab share one password
    (typical for fleet-provisioned BF-3 shelves), so capturing it once
    means subsequent DPU probes and Reset actions work without any
    manual settings edit.
    """

    def test_populates_from_job_inline_password(self, db, project):
        # Job has inline username + password (no SSHCredential row).
        job = DiscoveryJob(
            project_id=project.id,
            ssh_username="root",
            ssh_password_encrypted=encrypt_value("lab-pw-123"),
            ssh_auth_type="password",
            status=DiscoveryJobStatus.COMPLETED.value,
            total_nodes=1, completed_nodes=1, failed_nodes=0,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        node = _make_node(db, job, "192.168.68.100", is_dpu_bmc=True)
        DiscoveryService(db).register_node_as_dpu(
            project_id=project.id, node_id=node.id,
        )
        db.commit()

        settings = (
            db.query(ProjectDpuSettings)
            .filter_by(project_id=project.id).first()
        )
        assert settings is not None
        assert settings.default_oob_username == "root"
        assert decrypt_value_or_none(settings.default_oob_password_encrypted) == "lab-pw-123"

    def test_does_not_overwrite_existing_default(self, db, project):
        # Operator already configured a password — don't clobber it.
        settings = ProjectDpuSettings(
            project_id=project.id,
            default_oob_username="admin",
            default_oob_password_encrypted=encrypt_value("operator-chosen"),
        )
        db.add(settings)
        job = DiscoveryJob(
            project_id=project.id,
            ssh_username="root",
            ssh_password_encrypted=encrypt_value("discovery-pw"),
            ssh_auth_type="password",
            status=DiscoveryJobStatus.COMPLETED.value,
            total_nodes=1, completed_nodes=1, failed_nodes=0,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        node = _make_node(db, job, "192.168.68.100", is_dpu_bmc=True)
        DiscoveryService(db).register_node_as_dpu(
            project_id=project.id, node_id=node.id,
        )
        db.commit()

        settings = (
            db.query(ProjectDpuSettings)
            .filter_by(project_id=project.id).first()
        )
        assert settings.default_oob_username == "admin"
        assert decrypt_value_or_none(settings.default_oob_password_encrypted) == "operator-chosen"

    def test_skips_when_discovery_used_key_auth(self, db, project):
        job = DiscoveryJob(
            project_id=project.id,
            ssh_username="ubuntu",
            ssh_key_encrypted=encrypt_value("-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END-----\n"),
            ssh_auth_type="key",
            status=DiscoveryJobStatus.COMPLETED.value,
            total_nodes=1, completed_nodes=1, failed_nodes=0,
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        node = _make_node(db, job, "192.168.68.100", is_dpu_bmc=True)
        DiscoveryService(db).register_node_as_dpu(
            project_id=project.id, node_id=node.id,
        )
        db.commit()

        settings = (
            db.query(ProjectDpuSettings)
            .filter_by(project_id=project.id).first()
        )
        # No password available to copy — settings row should either not
        # exist or have no default_oob_password.
        if settings is not None:
            assert settings.default_oob_password_encrypted is None
