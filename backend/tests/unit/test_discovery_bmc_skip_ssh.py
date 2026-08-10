"""Phase 1: when Redfish identifies a node as a BlueField BMC, the
discovery flow skips the SSH probe and marks the row completed.

Without this, fresh BMCs whose default `0penBmc` triggers a forced
password change land in `failed` status with paramiko's cryptic
`SSHException: Illegal info request from server` error — even though
the Redfish probe captured `is_dpu_bmc=True` and the Register-as-DPU
button works fine in the UI.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401
from core.encryption import encrypt_value
from database import Base
from models.discovery import DiscoveredNode, DiscoveryJob
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
        ssh_username="root",
        ssh_port=22,
        ssh_auth_type="password",
        ssh_password_encrypted=encrypt_value("0penBmc"),
        status=DiscoveryJobStatus.IN_PROGRESS.value,
        total_nodes=1,
        completed_nodes=0,
        failed_nodes=0,
    )
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


def _make_node(db: Session, job: DiscoveryJob, ip: str) -> DiscoveredNode:
    n = DiscoveredNode(
        discovery_job_id=job.id,
        ip_address=ip,
        status=DiscoveredNodeStatus.PENDING.value,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


# ── Tests ──────────────────────────────────────────────────────────────────


class TestSkipSshOnBmcDetected:
    """Redfish-identified BMCs must NOT run the SSH probe afterwards."""

    def test_bmc_detected_via_redfish_skips_ssh_and_completes(
        self, db, project, job,
    ):
        node = _make_node(db, job, "192.168.68.97")
        bmc_payload = {
            "is_dpu_bmc": True,
            "product": "BlueField-3 DPU",
            "serial_number": "MT-BF3-001",
        }

        with (
            patch(
                "services.discovery_service._probe_bluefield_bmc",
                return_value=bmc_payload,
            ) as bmc_probe,
            patch(
                "services.discovery_service._probe_node_ssh",
            ) as ssh_probe,
        ):
            shared = {
                "username": "root",
                "password": "0penBmc",
                "private_key": None,
                "port": 22,
                "auth_type": "password",
            }
            DiscoveryService(db)._discover_node(node, shared)

        # SSH probe must NOT have been called — that's the whole point.
        ssh_probe.assert_not_called()
        # Redfish probe ran and was authoritative.
        bmc_probe.assert_called_once()

        db.refresh(node)
        assert node.status == DiscoveredNodeStatus.COMPLETED.value
        assert node.is_dpu_bmc is True
        assert node.bmc_product == "BlueField-3 DPU"
        assert node.bmc_serial_number == "MT-BF3-001"
        assert node.error_message is None
        # The discovery_log carries an explanatory line so the verbose
        # panel shows what we did instead of leaving it blank.
        assert node.discovery_log
        assert "BMC" in node.discovery_log
        assert node.discovered_at is not None

    def test_bmc_detection_skips_ssh_even_when_default_pwd_would_fail(
        self, db, project, job,
    ):
        # Regression for Job #3: paramiko throws "Illegal info request"
        # on `0penBmc` due to forced password change. If we still ran the
        # SSH probe, that exception would mark the node failed. We must
        # not even reach the SSH probe.
        node = _make_node(db, job, "192.168.68.97")
        from paramiko.ssh_exception import SSHException

        with (
            patch(
                "services.discovery_service._probe_bluefield_bmc",
                return_value={
                    "is_dpu_bmc": True,
                    "product": "BlueField-3 DPU",
                    "serial_number": None,
                },
            ),
            patch(
                "services.discovery_service._probe_node_ssh",
                side_effect=SSHException("Illegal info request from server"),
            ) as ssh_probe,
        ):
            shared = {
                "username": "root",
                "password": "0penBmc",
                "private_key": None,
                "port": 22,
                "auth_type": "password",
            }
            DiscoveryService(db)._discover_node(node, shared)

        ssh_probe.assert_not_called()
        db.refresh(node)
        assert node.status == DiscoveredNodeStatus.COMPLETED.value


class TestNonBmcStillRunsSsh:
    """Non-BMC nodes must continue to run the SSH probe — we only short-
    circuit the BMC path. Regression guard for accidentally widening the
    skip."""

    def test_non_bmc_node_runs_ssh_probe(self, db, project, job):
        node = _make_node(db, job, "10.0.0.10")
        ssh_results = {
            "hostname": "worker1",
            "os_type": "ubuntu",
            "os_version": "22.04",
            "os_pretty_name": "Ubuntu 22.04",
            "kernel_version": "5.15",
            "architecture": "x86_64",
            "is_dpu_host": False,
            "is_dpu_node": False,
            "dpu_count": 0,
            "dpu_details": None,
            "k8s_installed": False,
            "k8s_running": False,
            "k8s_version": None,
            "k8s_distribution": None,
            "k8s_role": None,
            "container_runtime": None,
            "network_interfaces": None,
            "log": "normal probe output",
        }

        with (
            patch(
                "services.discovery_service._probe_bluefield_bmc",
                return_value={"is_dpu_bmc": False, "product": None, "serial_number": None},
            ),
            patch(
                "services.discovery_service._probe_node_ssh",
                return_value=ssh_results,
            ) as ssh_probe,
        ):
            shared = {
                "username": "ubuntu",
                "password": "ubuntu-pw",
                "private_key": None,
                "port": 22,
                "auth_type": "password",
            }
            DiscoveryService(db)._discover_node(node, shared)

        ssh_probe.assert_called_once()
        db.refresh(node)
        assert node.status == DiscoveredNodeStatus.COMPLETED.value
        assert node.is_dpu_bmc is False
        assert node.hostname == "worker1"
        assert node.k8s_role is None  # regression: was being dropped
        assert node.container_runtime is None  # regression: was being dropped
