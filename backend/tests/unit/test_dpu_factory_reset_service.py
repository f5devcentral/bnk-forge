"""Unit tests for DpuFactoryResetService."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers Base metadata
from core.encryption import encrypt_value
from database import Base
from models.dpu import Dpu, ProjectDpuSettings
from models.project import Project
from services import dpu_credentials
from services.dpu_factory_reset_service import (
    DpuFactoryResetService,
    FactoryResetOptions,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def project(db: Session) -> Project:
    p = Project(name="p", description="d")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def dpu(db: Session, project: Project) -> Dpu:
    d = Dpu(
        project_id=project.id,
        bmc_ip="192.0.2.50",
        bmc_username_encrypted=encrypt_value("root"),
        bmc_password_encrypted=encrypt_value("configured"),
        serial_number="MT-TEST-1",
        dpu_os_status="reachable",
        dpu_os_ip="192.168.68.79",
    )
    db.add(d)
    db.add(ProjectDpuSettings(
        project_id=project.id,
        default_os_username="ubuntu",
        default_os_password_encrypted=encrypt_value("ubuntu-pw"),
    ))
    db.commit()
    db.refresh(d)
    return d


@pytest.fixture
def unreachable_dpu(db: Session, project: Project) -> Dpu:
    # Separate DPU for the NIC-skip test — no DPU OS reachability.
    d = Dpu(
        project_id=project.id,
        bmc_ip="192.0.2.51",
        bmc_username_encrypted=encrypt_value("root"),
        bmc_password_encrypted=encrypt_value("configured"),
        dpu_os_status=None,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _stub_redfish(monkeypatch, status_by_url=None):
    """Replace requests.post in dpu_credentials with a capturing stub."""
    status_by_url = status_by_url or {}
    calls: list[dict] = []

    class _Resp:
        def __init__(self, code):
            self.status_code = code
            self.text = ""

    def fake_post(url, auth, json, verify, timeout):  # noqa: ARG001
        calls.append({"url": url, "body": json})
        for key, code in status_by_url.items():
            if key in url:
                return _Resp(code)
        return _Resp(204)

    monkeypatch.setattr(dpu_credentials.requests, "post", fake_post)
    # Stub the auto-PATCH so it never hits the network.
    monkeypatch.setattr(
        dpu_credentials, "patch_bmc_root_password",
        lambda *a, **kw: None,
    )
    return calls


# ── Tests ──────────────────────────────────────────────────────────────────

class TestAllThreeSelected:
    def test_full_reset_runs_in_order_and_reports_all_steps(
        self, db, dpu, monkeypatch,
    ):
        calls = _stub_redfish(monkeypatch)
        ssh_calls = []

        def fake_ssh(host, user, password, command):  # noqa: ARG001
            ssh_calls.append({"host": host, "user": user, "command": command})
            return "Resetting /dev/mst/mt*\nok"

        svc = DpuFactoryResetService(db, ssh_runner=fake_ssh)
        summary = svc.run(
            project_id=dpu.project_id, dpu_id=dpu.id,
            opts=FactoryResetOptions(bios=True, bmc=True, nic=True),
        )

        step_names = [s.name for s in summary.steps]
        assert step_names == [
            "nic_mlxconfig_reset",
            "bios_reset",
            "power_cycle",
            "bmc_reset_to_defaults",
        ]
        assert all(s.ok for s in summary.steps)

        # NIC SSH: one call, to the DPU OS IP, with mlxconfig + mlxprivhost.
        assert len(ssh_calls) == 1
        assert ssh_calls[0]["host"] == "192.168.68.79"
        assert "mlxconfig" in ssh_calls[0]["command"]
        assert "mlxprivhost" in ssh_calls[0]["command"]

        # Redfish: three calls, in order.
        redfish_urls = [c["url"] for c in calls]
        assert redfish_urls[0].endswith("/Bios/Actions/Bios.ResetBios")
        assert redfish_urls[1].endswith("/Systems/Bluefield/Actions/ComputerSystem.Reset")
        assert calls[1]["body"] == {"ResetType": "PowerCycle"}
        assert redfish_urls[2].endswith("/Managers/Bluefield_BMC/Actions/Manager.ResetToDefaults")
        assert calls[2]["body"] == {"ResetType": "ResetAll"}


class TestBmcOnly:
    def test_only_bmc_reset_fires_no_power_cycle(self, db, dpu, monkeypatch):
        calls = _stub_redfish(monkeypatch)
        svc = DpuFactoryResetService(db, ssh_runner=MagicMock())
        summary = svc.run(
            project_id=dpu.project_id, dpu_id=dpu.id,
            opts=FactoryResetOptions(bios=False, bmc=True, nic=False),
        )

        assert [s.name for s in summary.steps] == ["bmc_reset_to_defaults"]
        assert all(s.ok for s in summary.steps)
        assert len(calls) == 1
        assert "Manager.ResetToDefaults" in calls[0]["url"]
        assert "/Oem/" not in calls[0]["url"]
        assert calls[0]["body"] == {"ResetType": "ResetAll"}

    def test_deep_variant_uses_nvidia_oem_url(self, db, dpu, monkeypatch):
        calls = _stub_redfish(monkeypatch)
        svc = DpuFactoryResetService(db, ssh_runner=MagicMock())
        summary = svc.run(
            project_id=dpu.project_id, dpu_id=dpu.id,
            opts=FactoryResetOptions(bios=False, bmc=True, nic=False, bmc_deep=True),
        )

        assert [s.name for s in summary.steps] == ["bmc_reset_to_defaults_oem"]
        assert len(calls) == 1
        assert "/Oem/NvidiaManager.ResetToDefaults" in calls[0]["url"]
        # OEM variant takes no body parameters.
        assert calls[0]["body"] == {}


class TestBiosOnly:
    def test_bios_reset_also_issues_power_cycle(self, db, dpu, monkeypatch):
        calls = _stub_redfish(monkeypatch)
        svc = DpuFactoryResetService(db, ssh_runner=MagicMock())
        summary = svc.run(
            project_id=dpu.project_id, dpu_id=dpu.id,
            opts=FactoryResetOptions(bios=True, bmc=False, nic=False),
        )

        assert [s.name for s in summary.steps] == ["bios_reset", "power_cycle"]
        assert len(calls) == 2


class TestNicSkippedWhenOsUnreachable:
    def test_nic_step_skipped_with_helpful_message(
        self, db, unreachable_dpu, monkeypatch,
    ):
        _stub_redfish(monkeypatch)
        ssh_runner = MagicMock()
        svc = DpuFactoryResetService(db, ssh_runner=ssh_runner)
        summary = svc.run(
            project_id=unreachable_dpu.project_id, dpu_id=unreachable_dpu.id,
            opts=FactoryResetOptions(bios=False, bmc=False, nic=True),
        )

        [nic_step] = summary.steps
        assert nic_step.name == "nic_mlxconfig_reset"
        assert nic_step.ran is False
        assert "not reachable" in nic_step.message
        # The SSH runner should not have been called.
        ssh_runner.assert_not_called()


class TestRedfishFailure:
    def test_bios_http_error_skips_power_cycle(self, db, dpu, monkeypatch):
        _stub_redfish(monkeypatch, status_by_url={"Bios.ResetBios": 500})
        svc = DpuFactoryResetService(db, ssh_runner=MagicMock())
        summary = svc.run(
            project_id=dpu.project_id, dpu_id=dpu.id,
            opts=FactoryResetOptions(bios=True, bmc=False, nic=False),
        )

        # BIOS failed → no power cycle (it would be a no-op reboot).
        [bios] = summary.steps
        assert bios.name == "bios_reset"
        assert bios.ok is False
        assert "HTTP 500" in bios.message


class TestSshFailure:
    def test_nic_ssh_error_captured(self, db, dpu, monkeypatch):
        _stub_redfish(monkeypatch)

        def boom(*a, **kw):  # noqa: ARG001
            raise RuntimeError("mlxconfig exit=127 — not found")

        svc = DpuFactoryResetService(db, ssh_runner=boom)
        summary = svc.run(
            project_id=dpu.project_id, dpu_id=dpu.id,
            opts=FactoryResetOptions(bios=False, bmc=False, nic=True),
        )
        [nic] = summary.steps
        assert nic.ran is True
        assert nic.ok is False
        assert "mlxconfig" in nic.message.lower() or "SSH" in nic.message


class TestCredentialGuards:
    def test_missing_bmc_creds_raises(self, db, project):
        bare = Dpu(project_id=project.id, bmc_ip="1.2.3.4")
        db.add(bare)
        db.commit()
        from core.errors import BadRequestError
        svc = DpuFactoryResetService(db)
        with pytest.raises(BadRequestError, match="No BMC credentials"):
            svc.run(
                project_id=project.id, dpu_id=bare.id,
                opts=FactoryResetOptions(bios=True),
            )
