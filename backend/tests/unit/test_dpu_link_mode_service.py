"""Unit tests for DpuLinkModeService.convert_to_ethernet.

Covers:
  * Preconditions — DPU must currently be IB / mixed; BMC path requires
    a reachable DPU OS + project OS creds.
  * Happy path for both access modes — mlxconfig + reset issued in order.
  * Error capture — failed mlxconfig / failed reset / unresolvable MST.

Uses paramiko fakes so no live SSH is needed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers Base
from core.encryption import encrypt_value
from core.errors import BadRequestError
from database import Base
from models.dpu import Dpu, ProjectDpuSettings
from models.project import Project
from services import dpu_link_mode_service
from services.dpu_link_mode_service import (
    ConvertResult,
    DpuLinkModeService,
)


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


def _ib_payload() -> dict[str, Any]:
    """Minimal payload that makes `_derive_link_mode` return 'ib'."""
    return {
        "rshim_device_present": True,
        "mlxconfig_settings": {"LINK_TYPE_P1": "IB(1)", "LINK_TYPE_P2": "IB(1)"},
    }


def _eth_payload() -> dict[str, Any]:
    return {
        "rshim_device_present": True,
        "mlxconfig_settings": {"LINK_TYPE_P1": "ETH(2)", "LINK_TYPE_P2": "ETH(2)"},
    }


def _make_inband_dpu(db: Session, project: Project, **overrides) -> Dpu:
    d = Dpu(
        project_id=project.id,
        access_mode="in-band",
        host_node_ip=overrides.get("host_node_ip", "10.0.0.10"),
        pci_address=overrides.get("pci_address", "0000:00:10"),
        rshim_device=overrides.get("rshim_device", "rshim0"),
        oob0_ipv4="dhcp",
        last_discovery_payload=overrides.get("last_discovery_payload", _ib_payload()),
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _make_bmc_dpu(db: Session, project: Project, **overrides) -> Dpu:
    d = Dpu(
        project_id=project.id,
        access_mode="bmc",
        bmc_ip=overrides.get("bmc_ip", "10.0.0.1"),
        oob0_ipv4="dhcp",
        last_discovery_payload=overrides.get("last_discovery_payload", _ib_payload()),
        dpu_os_ip=overrides.get("dpu_os_ip"),
        dpu_os_status=overrides.get("dpu_os_status"),
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


# ── FakeSSHClient mirrors the rshim_service test pattern ────────────────────


class _FakeStream:
    def __init__(self, data: str, rc: int):
        self._data = data.encode()
        self._rc = rc
        self.channel = SimpleNamespace(recv_exit_status=lambda: rc)

    def read(self) -> bytes:
        out, self._data = self._data, b""
        return out


class FakeSSHClient:
    """Scripted SSHClient stand-in. Each `(rc, out, err)` is consumed in order."""

    def __init__(self, scripted: list[tuple[int, str, str]]):
        self._scripted = list(scripted)
        self.commands: list[str] = []
        self.closed = False

    def exec_command(self, command: str, timeout: int | None = None) -> Any:
        self.commands.append(command)
        if not self._scripted:
            raise AssertionError(f"unexpected command: {command}")
        rc, out, err = self._scripted.pop(0)
        return None, _FakeStream(out, rc), _FakeStream(err, rc)

    def close(self) -> None:
        self.closed = True


# ── Preconditions ──────────────────────────────────────────────────────────


class TestPreconditions:
    def test_eth_mode_dpu_rejected(self, db, project):
        d = _make_inband_dpu(db, project, last_discovery_payload=_eth_payload())
        with pytest.raises(BadRequestError, match="not in InfiniBand mode"):
            DpuLinkModeService(db).convert_to_ethernet(project.id, d.id)

    def test_unknown_link_mode_rejected(self, db, project):
        # No mlxconfig_settings yet → link_mode None → not IB → reject.
        d = _make_inband_dpu(
            db, project,
            last_discovery_payload={"rshim_device_present": True},
        )
        with pytest.raises(BadRequestError, match="not in InfiniBand mode"):
            DpuLinkModeService(db).convert_to_ethernet(project.id, d.id)

    def test_inband_without_host_ip_rejected(self, db, project):
        d = _make_inband_dpu(db, project, host_node_ip=None)
        with pytest.raises(BadRequestError, match="host_node_ip"):
            DpuLinkModeService(db).convert_to_ethernet(project.id, d.id)

    def test_bmc_requires_dpu_os_reachable(self, db, project):
        d = _make_bmc_dpu(db, project, dpu_os_status="probing", dpu_os_ip=None)
        with pytest.raises(BadRequestError, match="DPU OS isn't"):
            DpuLinkModeService(db).convert_to_ethernet(project.id, d.id)

    def test_bmc_requires_project_os_credentials(self, db, project):
        d = _make_bmc_dpu(
            db, project, dpu_os_status="reachable", dpu_os_ip="10.9.0.5",
        )
        # No ProjectDpuSettings row → no OS creds.
        with pytest.raises(BadRequestError, match="DPU OS default username"):
            DpuLinkModeService(db).convert_to_ethernet(project.id, d.id)


# ── In-band happy path / failure modes ─────────────────────────────────────


class TestInbandConvert:
    @pytest.fixture
    def dpu(self, db, project):
        return _make_inband_dpu(db, project)

    def test_happy_path_runs_mlxconfig_then_sw_reset(self, db, project, dpu, monkeypatch):
        """mst start → resolve MST → mlxconfig set → SW_RESET, in order."""
        client = FakeSSHClient([
            (0, "mst started", ""),                                  # mst start
            (0, "/dev/mst/mt41692_pciconf0", ""),                    # awk MST resolution
            (0, "Apply new Configuration? [y/N] yApplied", ""),      # mlxconfig set
            (0, "", ""),                                             # SW_RESET
        ])
        monkeypatch.setattr(
            dpu_link_mode_service, "open_inband_host_ssh",
            lambda _db, _dpu: client,
            raising=False,
        )
        # The function is imported lazily inside the service — patch
        # both the module attribute and the symbol the service imports.
        import services.rshim_service as rshim
        monkeypatch.setattr(rshim, "open_inband_host_ssh", lambda _db, _dpu: client)

        result = DpuLinkModeService(db).convert_to_ethernet(project.id, dpu.id)
        assert isinstance(result, ConvertResult)
        assert result.ok is True
        # MST resolution awk-matches against the SHORT bdf, not the
        # domain-prefixed pci_address — regression guard for the
        # `lspci -Dnn` switch.
        assert any("bdf='00:10'" in cmd for cmd in client.commands)
        # rshim path uses the DPU's recorded rshim device, not the
        # hard-coded rshim0 default (multi-DPU host correctness).
        assert any("/dev/rshim0/misc" in cmd for cmd in client.commands)
        # mlxconfig set both ports to ETH(2).
        assert any(
            "mlxconfig" in cmd and "LINK_TYPE_P1=2" in cmd and "LINK_TYPE_P2=2" in cmd
            for cmd in client.commands
        )

    def test_uses_dpu_specific_rshim_device_for_reset(
        self, db, project, monkeypatch,
    ):
        # Multi-DPU host: this DPU is on rshim1, so SW_RESET must target
        # /dev/rshim1/misc — hitting rshim0 would reset the WRONG DPU.
        d = _make_inband_dpu(db, project, rshim_device="rshim1")
        client = FakeSSHClient([
            (0, "", ""),                                # mst start
            (0, "/dev/mst/mt41692_pciconf1", ""),       # MST resolution
            (0, "Applied", ""),                         # mlxconfig set
            (0, "", ""),                                # SW_RESET
        ])
        import services.rshim_service as rshim
        monkeypatch.setattr(rshim, "open_inband_host_ssh", lambda _db, _dpu: client)

        DpuLinkModeService(db).convert_to_ethernet(project.id, d.id)
        assert any("/dev/rshim1/misc" in cmd for cmd in client.commands)
        assert not any("/dev/rshim0/misc" in cmd for cmd in client.commands)

    def test_unresolved_mst_device_returns_error(self, db, project, dpu, monkeypatch):
        client = FakeSSHClient([
            (0, "", ""),                                # mst start
            (0, "", ""),                                # MST resolution → empty
        ])
        import services.rshim_service as rshim
        monkeypatch.setattr(rshim, "open_inband_host_ssh", lambda _db, _dpu: client)

        result = DpuLinkModeService(db).convert_to_ethernet(project.id, dpu.id)
        assert result.ok is False
        assert "MST device" in (result.error or "")
        # Should NOT have reached mlxconfig — bail out at the resolve step.
        assert not any("mlxconfig -d" in cmd for cmd in client.commands)

    def test_mlxconfig_failure_does_not_issue_reset(
        self, db, project, dpu, monkeypatch,
    ):
        client = FakeSSHClient([
            (0, "", ""),                                                   # mst start
            (0, "/dev/mst/mt41692_pciconf0", ""),                          # MST resolution
            (1, "Error: device locked", "permission denied"),              # mlxconfig FAILS
        ])
        import services.rshim_service as rshim
        monkeypatch.setattr(rshim, "open_inband_host_ssh", lambda _db, _dpu: client)

        result = DpuLinkModeService(db).convert_to_ethernet(project.id, dpu.id)
        assert result.ok is False
        assert "mlxconfig" in (result.error or "")
        # SW_RESET must NOT have been issued — the configuration didn't
        # take effect; rebooting now would just re-boot into IB mode.
        assert not any("SW_RESET" in cmd for cmd in client.commands)

    def test_ssh_connection_failure_returns_error(
        self, db, project, dpu, monkeypatch,
    ):
        def boom(_db, _dpu):
            raise TimeoutError("host down")

        import services.rshim_service as rshim
        monkeypatch.setattr(rshim, "open_inband_host_ssh", boom)

        result = DpuLinkModeService(db).convert_to_ethernet(project.id, dpu.id)
        assert result.ok is False
        assert "host down" in (result.error or "")


# ── BMC happy path ─────────────────────────────────────────────────────────


class TestBmcConvert:
    def test_runs_mlxconfig_via_dpu_os(self, db, project, monkeypatch):
        # Set up: project OS creds present, DPU OS reachable.
        db.add(ProjectDpuSettings(
            project_id=project.id,
            default_os_username="ubuntu",
            default_os_password_encrypted=encrypt_value("ubuntu-pw"),
        ))
        db.commit()
        d = _make_bmc_dpu(
            db, project, dpu_os_status="reachable", dpu_os_ip="10.9.0.5",
        )

        client = FakeSSHClient([
            (0, "", ""),                                # mst start
            (0, "/dev/mst/mt41692_pciconf0", ""),       # MST resolution (DPU OS only sees its own)
            (0, "Applied", ""),                         # mlxconfig set
            (0, "Sending Reset Command... Done", ""),   # mlxfwreset
        ])
        monkeypatch.setattr(
            dpu_link_mode_service,
            "_connect_dpu_os",
            lambda host, username, password: client,
        )

        result = DpuLinkModeService(db).convert_to_ethernet(project.id, d.id)
        assert result.ok is True
        # BMC path uses ls (not awk) since the DPU OS only exposes its
        # own MST device — no per-host disambiguation needed.
        assert any("ls /dev/mst/mt*pciconf0" in cmd for cmd in client.commands)
        # Reset is mlxfwreset (NOT rshim SW_RESET — there's no /dev/rshim
        # on the DPU OS).
        assert any("mlxfwreset" in cmd for cmd in client.commands)
        assert not any("SW_RESET" in cmd for cmd in client.commands)
