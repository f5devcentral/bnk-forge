"""Unit tests for the rshim install service.

Only exercises pieces that don't need a live SSHD:
  - Credential resolution (DPU → SSHCredential row).
  - Preconditions (in-band only, host_ip required, credential required).
  - The pure-function parts of status/install logic, with paramiko fakes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers every model on the metadata
from core.encryption import encrypt_value
from core.errors import BadRequestError, NotFoundError
from database import Base
from models.dpu import Dpu
from models.project import Project
from models.ssh_credential import SSHCredential
from services import rshim_service
from services.rshim_service import (
    RshimInstallResult,
    RshimService,
    _build_host_tmfifo_netplan,
    _ensure_host_tmfifo_ips,
    _enumerate_rshim_devices,
    _parse_inband_inventory,
    _probe_rshim_state,
    _resolve_host_credential,
    _run_install,
    _select_rshim_device,
    _short_bdf_for_mst,
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


def _make_ssh_cred(db: Session, **overrides) -> SSHCredential:
    c = SSHCredential(
        name=overrides.get("name", "host-cred"),
        host=overrides.get("host", "unused"),
        port=overrides.get("port", 22),
        username=overrides.get("username", "ubuntu"),
        auth_type=overrides.get("auth_type", "password"),
        password_encrypted=encrypt_value(overrides.get("password", "pw")),
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_inband_dpu(db: Session, project: Project, **overrides) -> Dpu:
    d = Dpu(
        project_id=project.id,
        access_mode="in-band",
        host_node_ip=overrides.get("host_node_ip", "10.0.0.10"),
        host_ssh_credential_id=overrides.get("host_ssh_credential_id"),
        pci_address=overrides.get("pci_address", "0000:01:00"),
        oob0_ipv4="dhcp",
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


# ── Preconditions ────────────────────────────────────────────────────────────

class TestPreconditions:
    def test_missing_dpu_raises_not_found(self, db, project):
        svc = RshimService(db)
        with pytest.raises(NotFoundError):
            svc.probe_status(project.id, 9999)

    def test_bmc_mode_dpu_rejected(self, db, project):
        d = Dpu(
            project_id=project.id, access_mode="bmc",
            bmc_ip="10.0.0.1", oob0_ipv4="dhcp",
        )
        db.add(d)
        db.commit()
        db.refresh(d)
        with pytest.raises(BadRequestError, match="not in-band"):
            RshimService(db).probe_status(project.id, d.id)

    def test_inband_without_host_ip_rejected(self, db, project):
        d = _make_inband_dpu(db, project, host_node_ip=None)
        with pytest.raises(BadRequestError, match="host_node_ip"):
            RshimService(db).probe_status(project.id, d.id)

    def test_no_host_credential_set(self, db, project):
        d = _make_inband_dpu(db, project, host_ssh_credential_id=None)
        with pytest.raises(BadRequestError, match="no host SSH credential"):
            _resolve_host_credential(db, d)

    def test_credential_fk_dangling(self, db, project):
        d = _make_inband_dpu(db, project, host_ssh_credential_id=9999)
        with pytest.raises(BadRequestError, match="no longer exists"):
            _resolve_host_credential(db, d)

    def test_credential_decrypts(self, db, project):
        cred = _make_ssh_cred(db, username="ubuntu", password="s3cret")
        d = _make_inband_dpu(db, project, host_ssh_credential_id=cred.id)
        resolved = _resolve_host_credential(db, d)
        assert resolved["username"] == "ubuntu"
        assert resolved["password"] == "s3cret"
        assert resolved["port"] == 22
        assert resolved["auth_type"] == "password"


# ── Command-log helpers with a paramiko fake ────────────────────────────────

class FakeSSHClient:
    """Minimal stand-in exposing `exec_command` + `close` the way paramiko does."""

    def __init__(self, scripted: list[tuple[int, str, str]]):
        # list of (exit_code, stdout, stderr) tuples, consumed in order.
        self._scripted = list(scripted)
        self.commands: list[str] = []
        self.closed = False

    def exec_command(self, command: str, timeout: int | None = None) -> Any:
        self.commands.append(command)
        if not self._scripted:
            raise AssertionError(f"unexpected command: {command}")
        rc, out, err = self._scripted.pop(0)
        return (
            None,  # stdin
            _FakeStream(out, rc),
            _FakeStream(err, rc),
        )

    def close(self) -> None:
        self.closed = True


class _FakeStream:
    def __init__(self, data: str, rc: int):
        self._data = data.encode()
        self._rc = rc
        self.channel = SimpleNamespace(recv_exit_status=lambda: rc)

    def read(self) -> bytes:
        out, self._data = self._data, b""
        return out


class TestProbeState:
    def test_rshim_running_and_device_present(self):
        client = FakeSSHClient([
            (0, "", ""),                      # :
            (0, "", ""),                      # systemctl list-unit-files
            (0, "active", ""),                # is-active
            (0, "", ""),                      # test -e /dev/rshim0/misc
            (0, "", ""),                      # dpkg -s or rpm -q
            (0, "", ""),                      # command -v mlxfwmanager
            (0, "", ""),                      # ls /dev/mst/mt*
        ])
        st = _probe_rshim_state(client)  # type: ignore[arg-type]
        assert st.installed is True
        assert st.active is True
        assert st.device_present is True
        assert st.mft_installed is True
        assert st.mst_running is True
        assert "ready" in st.message

    def test_rshim_not_installed(self):
        client = FakeSSHClient([
            (0, "", ""),                      # :
            (1, "", ""),                      # systemctl list-unit-files
            (0, "inactive", ""),              # is-active
            (1, "", ""),                      # test -e /dev/rshim0/misc
            (1, "", ""),                      # dpkg -s / rpm -q
            (1, "", ""),                      # mlxfwmanager missing
            (1, "", ""),                      # mst devices missing
        ])
        st = _probe_rshim_state(client)  # type: ignore[arg-type]
        assert st.installed is False
        assert st.active is False
        assert st.device_present is False
        assert st.mft_installed is False
        assert "not installed" in st.message


_PROBE_ALL_READY = [
    (0, "", ""),                      # :
    (0, "", ""),                      # unit files
    (0, "active", ""),                # is-active
    (0, "", ""),                      # test -e
    (0, "", ""),                      # dpkg/rpm
    (0, "", ""),                      # mlxfwmanager
    (0, "", ""),                      # mst devices
]

_PROBE_RSHIM_ONLY_MISSING = [
    (0, "", ""),                      # :
    (1, "", ""),                      # unit files
    (0, "inactive", ""),              # is-active
    (1, "", ""),                      # test -e
    (1, "", ""),                      # dpkg/rpm
    (1, "", ""),                      # mlxfwmanager
    (1, "", ""),                      # mst devices
]


class TestInstallFlow:
    def test_short_circuits_when_device_already_present(self):
        # Pre-probe says everything's up.
        client = FakeSSHClient(list(_PROBE_ALL_READY))
        result = _run_install(client)  # type: ignore[arg-type]
        assert isinstance(result, RshimInstallResult)
        assert result.ok is True
        assert len(result.log) == 1
        assert "already" in result.log[0]["command"]

    def test_fails_fast_when_not_apt_based(self):
        # Pre-probe says rshim missing, then detect apt returns "no".
        client = FakeSSHClient(
            list(_PROBE_RSHIM_ONLY_MISSING)
            + [(0, "no", "")],                # detect apt → no
        )
        result = _run_install(client)  # type: ignore[arg-type]
        assert result.ok is False
        assert "does not use apt" in (result.error or "")

    def test_success_path_installs_rshim_and_mft(self):
        # pre-probe: nothing installed; full apt path runs; post-probe ok.
        script = (
            list(_PROBE_RSHIM_ONLY_MISSING)
            + [
                (0, "apt", ""),               # detect apt
                (0, "", ""),                  # apt update
                (0, "", ""),                  # apt install rshim
                (0, "1700000000", ""),        # date +%s (journal since-filter)
                (0, "", ""),                  # systemctl enable rshim
                (0, "", ""),                  # systemctl restart rshim
                (0, "", ""),                  # wait for /dev/rshim0/misc
                (0, "", ""),                  # apt install linux-headers
                (0, "", ""),                  # apt install mft + kernel-mft-dkms
                (0, "", ""),                  # mst start
            ]
            + list(_PROBE_ALL_READY)          # post-probe
        )
        client = FakeSSHClient(script)
        result = _run_install(client)  # type: ignore[arg-type]
        assert result.ok is True
        assert result.status is not None
        assert result.status.device_present is True
        assert result.status.mft_installed is True

    def test_mft_install_failure_is_not_fatal(self):
        # rshim succeeds; mft install fails (apt error) — still returns ok.
        script = (
            list(_PROBE_RSHIM_ONLY_MISSING)
            + [
                (0, "apt", ""),               # detect apt
                (0, "", ""),                  # apt update
                (0, "", ""),                  # apt install rshim
                (0, "1700000000", ""),        # date +%s (journal since-filter)
                (0, "", ""),                  # systemctl enable rshim
                (0, "", ""),                  # systemctl restart rshim
                (0, "", ""),                  # wait for /dev/rshim0/misc
                (0, "", ""),                  # apt install linux-headers
                (100, "", "E: Unable to locate"),  # apt install mft (fail)
                (0, "", ""),                  # mst start
            ]
            # post-probe: rshim ready but mft still missing
            + [
                (0, "", ""),                  # :
                (0, "", ""),                  # unit files
                (0, "active", ""),            # is-active
                (0, "", ""),                  # /dev/rshim0/misc
                (0, "", ""),                  # dpkg/rpm
                (1, "", ""),                  # mlxfwmanager missing
                (1, "", ""),                  # mst devices missing
            ]
        )
        client = FakeSSHClient(script)
        result = _run_install(client)  # type: ignore[arg-type]
        assert result.ok is True
        assert result.status is not None
        assert result.status.device_present is True
        assert result.status.mft_installed is False


# ── Full happy-path end-to-end with paramiko fake ───────────────────────────

def test_service_probe_status_uses_resolved_credential_and_status(
    db, project, monkeypatch,
):
    cred = _make_ssh_cred(db, username="ubuntu", password="pw")
    dpu = _make_inband_dpu(db, project, host_ssh_credential_id=cred.id)

    captured: dict[str, Any] = {}

    def fake_open_ssh(_self: Any, project_id: int, d: Dpu) -> FakeSSHClient:
        captured["project_id"] = project_id
        captured["dpu_id"] = d.id
        # `probe_status` first enumerates /dev/rshim*/misc to map this DPU
        # to its rshim index — script that as a single-rshim host so the
        # downstream `_probe_rshim_state` runs against rshim0 as before.
        return FakeSSHClient(
            [(0, "rshim0|DEV_NAME pcie-0000:01:00.2", "")]
            + list(_PROBE_ALL_READY),
        )

    monkeypatch.setattr(
        rshim_service.RshimService, "_open_host_ssh", fake_open_ssh,
    )

    status = RshimService(db).probe_status(project.id, dpu.id)
    assert status.device_present is True
    assert captured == {"project_id": project.id, "dpu_id": dpu.id}
    # probe_status must persist the readiness snapshot so the list view
    # pill updates without a secondary call.
    db.refresh(dpu)
    assert dpu.rshim_state == "ready"
    assert dpu.rshim_checked_at is not None


def test_service_probe_status_persists_inactive_when_device_missing(
    db, project, monkeypatch,
):
    """A probe that succeeds over SSH but finds rshim inactive must still
    persist the outcome so the UI can prompt the user to install."""
    cred = _make_ssh_cred(db, username="ubuntu", password="pw")
    dpu = _make_inband_dpu(db, project, host_ssh_credential_id=cred.id)

    # All probe steps return the "nothing installed" pattern — matches the
    # 7-call shape used by TestProbeState.test_rshim_not_installed, with a
    # leading `_enumerate_rshim_devices` mapping that probe_status now runs
    # before `_probe_rshim_state`.
    script = [
        (0, "rshim0|DEV_NAME pcie-0000:01:00.2", ""),  # rshim enumeration
        (0, "", ""),   # : (warm-up)
        (1, "", ""),   # systemctl list-unit-files rshim.service → missing
        (0, "inactive", ""),  # systemctl is-active rshim
        (1, "", ""),   # test -e /dev/rshimN/misc → missing
        (1, "", ""),   # dpkg -s / rpm -q rshim → missing
        (1, "", ""),   # command -v mlxfwmanager → missing
        (1, "", ""),   # ls /dev/mst/mt* → missing
    ]

    monkeypatch.setattr(
        rshim_service.RshimService,
        "_open_host_ssh",
        lambda _s, _p, _d: FakeSSHClient(list(script)),
    )

    status = RshimService(db).probe_status(project.id, dpu.id)
    assert status.device_present is False
    db.refresh(dpu)
    assert dpu.rshim_state == "inactive"
    assert dpu.rshim_checked_at is not None


def test_service_probe_status_persists_unreachable_on_ssh_failure(
    db, project, monkeypatch,
):
    cred = _make_ssh_cred(db, username="ubuntu", password="pw")
    dpu = _make_inband_dpu(db, project, host_ssh_credential_id=cred.id)

    def boom(*_a, **_kw):
        raise TimeoutError("host down")

    monkeypatch.setattr(rshim_service.RshimService, "_open_host_ssh", boom)

    with pytest.raises(TimeoutError):
        RshimService(db).probe_status(project.id, dpu.id)

    db.refresh(dpu)
    assert dpu.rshim_state == "unreachable"
    assert "host down" in (dpu.rshim_state_detail or "")
    assert dpu.rshim_checked_at is not None


class TestParseInbandInventory:
    def test_extracts_rshim_misc_fields(self):
        misc = (
            "DEV_NAME     rshim0\n"
            "DEV_INFO     BLUEFIELD3,0x1,BlueField3,0x3aba,pciehca\n"
            "OPN_STR      900-9D3B6-00CV-AA0\n"
            "UUID         12345678-1234-1234-1234-abcdef123456\n"
            "MAC          e2:b5:1f:98:fa:10\n"
        )
        inv = _parse_inband_inventory(misc, "", "")
        assert inv.rshim_device_present is True
        assert inv.opn == "900-9D3B6-00CV-AA0"
        assert inv.uuid == "12345678-1234-1234-1234-abcdef123456"
        assert inv.mac == "e2:b5:1f:98:fa:10"

    def test_extracts_mlxconfig_header_and_settings(self):
        mlx = (
            "Device #1:\n"
            "----------\n"
            "\n"
            "Device type:        BlueField3\n"
            "Name:               N/A\n"
            "Description:        BlueField-3 P-Series DPU\n"
            "Device:             /dev/mst/mt41692_pciconf0\n"
            "\n"
            "Configurations:                                    Next Boot\n"
            "         MEMIC_BAR_SIZE                               0\n"
            "         INTERNAL_CPU_MODEL                           EMBEDDED_CPU(1)\n"
            "         LINK_TYPE_P1                                 ETH(2)\n"
            "         LINK_TYPE_P2                                 ETH(2)\n"
        )
        inv = _parse_inband_inventory("", "", mlx)
        assert inv.mlxconfig_device_type == "BlueField3"
        assert inv.mlxconfig_pci_device == "/dev/mst/mt41692_pciconf0"
        assert inv.description == "BlueField-3 P-Series DPU"
        assert inv.mlxconfig_settings["INTERNAL_CPU_MODEL"] == "EMBEDDED_CPU(1)"
        assert inv.mlxconfig_settings["LINK_TYPE_P1"] == "ETH(2)"
        assert inv.mlxconfig_settings["LINK_TYPE_P2"] == "ETH(2)"
        assert inv.mlxconfig_settings["MEMIC_BAR_SIZE"] == "0"

    def test_missing_fields_are_none(self):
        inv = _parse_inband_inventory("", "", "")
        assert inv.opn is None
        assert inv.mac is None
        assert inv.serial is None
        assert inv.mlxconfig_settings == {}

    def test_sysfs_gives_pci_ids(self):
        sysfs = (
            "vendor=0x15b3\n"
            "device=0xa2dc\n"
            "subsystem_vendor=0x15b3\n"
            "subsystem_device=0x0041\n"
            "class=0x020000\n"
        )
        inv = _parse_inband_inventory("", "", "", sysfs)
        assert inv.pci_id == "15b3:a2dc"
        assert inv.subsystem_id == "15b3:0041"

    def test_lspci_subsystem_line_fallback(self):
        lspci = (
            "00:10.0 Ethernet controller: Mellanox Technologies MT43244\n"
            "        Subsystem: Mellanox Technologies Device 0041\n"
        )
        sysfs = "subsystem_vendor=0x15b3\n"
        inv = _parse_inband_inventory("", lspci, "", sysfs)
        # sysfs gave only the subvendor — subsystem_line_id completes the pair.
        assert inv.subsystem_id == "15b3:0041"

    def test_rshim_na_becomes_none(self):
        misc = "OPN_STR         N/A\nDEV_INFO        BlueField-3(Rev 1)\n"
        inv = _parse_inband_inventory(misc, "", "")
        assert inv.opn is None
        assert inv.dev_info == "BlueField-3(Rev 1)"


# ── Multi-DPU /dev/rshim* mapping ───────────────────────────────────────────


class TestRshimDeviceMapping:
    def test_enumerate_parses_two_dpus(self):
        out = (
            "rshim0|DEV_NAME     pcie-0000:00:01.2\n"
            "rshim1|DEV_NAME     pcie-0001:00:01.2\n"
        )
        client = FakeSSHClient([(0, out, "")])
        mapping = _enumerate_rshim_devices(client)  # type: ignore[arg-type]
        # Both the function-suffixed and stripped forms must resolve.
        assert mapping["0000:00:01.2"] == "rshim0"
        assert mapping["0000:00:01"] == "rshim0"
        assert mapping["0001:00:01.2"] == "rshim1"
        assert mapping["0001:00:01"] == "rshim1"

    def test_enumerate_empty_when_no_rshim(self):
        client = FakeSSHClient([(1, "", "")])
        assert _enumerate_rshim_devices(client) == {}  # type: ignore[arg-type]

    def test_select_matches_pci_address(self):
        mapping = {
            "0000:00:01.2": "rshim0", "0000:00:01": "rshim0",
            "0001:00:01.2": "rshim1", "0001:00:01": "rshim1",
        }
        assert _select_rshim_device(mapping, "0000:00:01") == "rshim0"
        assert _select_rshim_device(mapping, "0001:00:01") == "rshim1"

    def test_select_falls_back_to_rshim0_when_no_pci(self):
        # Multi-rshim host but no PCI address → safe default.
        mapping = {
            "0000:00:01": "rshim0",
            "0001:00:01": "rshim1",
        }
        assert _select_rshim_device(mapping, None) == "rshim0"

    def test_select_returns_only_rshim_when_single(self):
        mapping = {"0000:00:01": "rshim0"}
        # Even with no PCI, single-rshim host can pick the only one.
        assert _select_rshim_device(mapping, None) == "rshim0"

    def test_select_unmatched_pci_falls_back(self):
        mapping = {"0001:00:01": "rshim1"}
        # PCI doesn't match any entry → conservative rshim0 default.
        assert _select_rshim_device(mapping, "0000:00:01") == "rshim0"


# ── Host-side tmfifo IP auto-config (Discover path) ─────────────────────────


class TestEnsureHostTmfifoIps:
    """Multi-DPU hosts need 192.168.{100+N}.1/30 on each tmfifo_netN.

    The helper is invoked from probe_status / probe_inventory (auto on
    Discover), not from the manual Install rshim action.
    """

    def test_single_rshim_host_is_skipped(self):
        # Only rshim0 → kernel default is enough → no SSH commands issued.
        client = FakeSSHClient([])
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:00:01.2": "rshim0", "0000:00:01": "rshim0"},
        )
        assert client.commands == []

    def test_multi_rshim_writes_and_applies_when_missing(self):
        # cat returns empty (no existing file), tee succeeds, netplan apply succeeds.
        client = FakeSSHClient([
            (0, "", ""),  # cat existing netplan → empty
            (0, "", ""),  # tee + chmod
            (0, "", ""),  # netplan apply
        ])
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {
                "0000:00:01": "rshim0",
                "0001:00:01": "rshim1",
            },
        )
        assert len(client.commands) == 3
        # Second command should base64-decode and tee to the bnk-forge file.
        assert "50-bnk-forge-tmfifo.yaml" in client.commands[1]
        assert "base64 -d" in client.commands[1]
        # Last call is the apply.
        assert client.commands[2] == "sudo -n netplan apply"

    def test_idempotent_when_existing_file_matches(self):
        # cat returns a body that exactly matches what we'd render → skip.
        target = _build_host_tmfifo_netplan({0, 1})
        client = FakeSSHClient([(0, target, "")])
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {
                "0000:00:01": "rshim0",
                "0001:00:01": "rshim1",
            },
        )
        # Just the cat — no write, no apply.
        assert len(client.commands) == 1

    def test_apply_failure_logged_not_raised(self):
        # File writes OK, but `netplan apply` fails. Helper must not raise.
        client = FakeSSHClient([
            (0, "", ""),         # cat → no existing
            (0, "", ""),         # tee succeeds
            (1, "", "boom"),     # netplan apply fails
        ])
        # Should not raise — failure is best-effort.
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:00:01": "rshim0", "0001:00:01": "rshim1"},
        )

    def test_netplan_body_includes_every_index(self):
        body = _build_host_tmfifo_netplan({0, 2})
        assert "tmfifo_net0:" in body
        assert "192.168.100.1/30" in body
        assert "tmfifo_net2:" in body
        assert "192.168.102.1/30" in body
        # Files we don't know about must NOT be touched.
        assert "tmfifo_net1:" not in body

    # ── ADR-424 cluster-scoped IPAM overrides ─────────────────────────────

    def test_netplan_body_uses_ip_override_for_index(self):
        # When IPAM assigns .5 to the host end on rshim0, the override
        # must replace the formula 192.168.100.1.
        body = _build_host_tmfifo_netplan({0, 1}, ip_overrides={1: "192.168.100.5"})
        assert "192.168.100.1/30" in body      # rshim0: formula (no override)
        assert "192.168.100.5/30" in body      # rshim1: persisted IPAM address
        assert "192.168.101.1/30" not in body  # formula for rshim1 must NOT appear

    def test_single_rshim_with_nondefault_host_ip_writes_netplan(self):
        # Multi-host cluster, 1 DPU per host. The 2nd host has rshim0 but
        # IPAM allocated .5 (not the kernel default .1). Early-return must
        # NOT fire — netplan must be written.
        client = FakeSSHClient([
            (0, "", ""),   # cat → no existing file
            (0, "", ""),   # tee + chmod
            (0, "", ""),   # netplan apply
        ])
        dpu = SimpleNamespace(
            pci_address="0000:0d:00.0",
            rshim_device="rshim0",
            host_tmfifo_ip="192.168.100.5",
            kubernetes_cluster_id=1,
        )
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:0d:00.0": "rshim0"},
            dpu=dpu,
        )
        assert len(client.commands) == 3, (
            "single-DPU host with non-default host_tmfifo_ip must write netplan"
        )
        assert "50-bnk-forge-tmfifo.yaml" in client.commands[1]

    def test_single_rshim_no_host_ip_still_skipped(self):
        # A single-DPU host with no IPAM override must not touch netplan —
        # the kernel auto-assigns 192.168.100.1/30 on tmfifo_net0.
        client = FakeSSHClient([])
        dpu = SimpleNamespace(
            pci_address="0000:0d:00.0",
            rshim_device="rshim0",
            host_tmfifo_ip=None,
        )
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:0d:00.0": "rshim0"},
            dpu=dpu,
        )
        assert client.commands == []

    def test_nondefault_host_ip_renders_correct_cidr_in_netplan(self):
        # Verify the netplan written for a single-DPU host with a
        # cluster-allocated host_tmfifo_ip contains the right CIDR.
        existing_content = ""  # no existing file
        client = FakeSSHClient([
            (0, existing_content, ""),  # cat
            (0, "", ""),                # tee + chmod
            (0, "", ""),                # netplan apply
        ])
        dpu = SimpleNamespace(
            pci_address="0000:0d:00.0",
            rshim_device="rshim0",
            host_tmfifo_ip="192.168.100.5",
            kubernetes_cluster_id=1,
        )
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:0d:00.0": "rshim0"},
            dpu=dpu,
        )
        # The tee command base64-encodes the netplan YAML — decode and verify.
        import base64 as _b64
        import re as _re
        tee_cmd = client.commands[1]
        m = _re.search(r"printf '%s' '([A-Za-z0-9+/=]+)'", tee_cmd)
        assert m, f"expected base64 payload in tee cmd: {tee_cmd!r}"
        rendered = _b64.b64decode(m.group(1)).decode()
        # The addresses: entry must use the persisted IP (- 192.168.100.5/30),
        # not the formula (- 192.168.100.1/30). The comment section of the
        # template mentions 192.168.100.1/30 so we check the address line.
        assert "        - 192.168.100.5/30" in rendered, (
            f"persisted host_tmfifo_ip must appear in netplan addresses, got:\n{rendered}"
        )
        assert "        - 192.168.100.1/30" not in rendered, (
            "formula address entry must not appear in addresses when override is present"
        )

    def test_idempotent_with_correct_override_file_already_present(self):
        # No write when the existing netplan already matches the override.
        dpu = SimpleNamespace(
            pci_address="0000:0d:00.0",
            rshim_device="rshim0",
            host_tmfifo_ip="192.168.100.5",
            kubernetes_cluster_id=1,
        )
        target = _build_host_tmfifo_netplan({0}, {0: "192.168.100.5"})
        client = FakeSSHClient([(0, target, "")])
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:0d:00.0": "rshim0"},
            dpu=dpu,
        )
        assert len(client.commands) == 1, "only cat — no write when file already matches"

    def test_multi_dpu_per_host_pins_both_interfaces_from_db(self, db: Session, project):
        """Probing either DPU on a 2-DPU host must pin BOTH tmfifo interfaces.

        Without a DB query of sibling DPUs, probing DPU-B with its own
        host_tmfifo_ip would write tmfifo_net0=formula, clobbering DPU-A's
        IPAM-allocated address.  With the DB query the netplan gets both
        overrides so neither DPU loses connectivity after the other is probed.
        """
        # Two DPUs on the same host with distinct rshim indexes and IPAM IPs.
        # Both must be assigned to the same cluster so the isnot(None) guard
        # in the DB query allows them to contribute (ADR-424 finding A fix).
        dpu_a = _make_inband_dpu(
            db, project,
            host_node_ip="10.0.0.42",
            pci_address="0000:0d:00.0",
        )
        dpu_a.host_tmfifo_ip = "192.168.100.5"   # IPAM-allocated, rshim0
        dpu_a.rshim_device = "rshim0"
        dpu_a.kubernetes_cluster_id = 42
        db.commit()

        dpu_b = _make_inband_dpu(
            db, project,
            host_node_ip="10.0.0.42",
            pci_address="0001:0d:00.0",
        )
        dpu_b.host_tmfifo_ip = "192.168.101.5"   # IPAM-allocated, rshim1
        dpu_b.rshim_device = "rshim1"
        dpu_b.kubernetes_cluster_id = 42
        db.commit()

        rshim_map = {
            "0000:0d:00.0": "rshim0",
            "0001:0d:00.0": "rshim1",
        }
        client = FakeSSHClient([
            (0, "", ""),   # cat → no existing file
            (0, "", ""),   # tee + chmod
            (0, "", ""),   # netplan apply
        ])

        # Probe DPU-B — the function must still pin DPU-A's interface too.
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client, rshim_map, dpu=dpu_b, db=db,
        )

        import base64 as _b64
        import re as _re
        tee_cmd = client.commands[1]
        m = _re.search(r"printf '%s' '([A-Za-z0-9+/=]+)'", tee_cmd)
        assert m, f"no base64 payload in tee cmd: {tee_cmd!r}"
        rendered = _b64.b64decode(m.group(1)).decode()

        assert "        - 192.168.100.5/30" in rendered, (
            f"DPU-A's IPAM address must be in netplan:\n{rendered}"
        )
        assert "        - 192.168.101.5/30" in rendered, (
            f"DPU-B's IPAM address must be in netplan:\n{rendered}"
        )
        # Formula addresses must NOT appear as the address entries.
        assert "        - 192.168.100.1/30" not in rendered, (
            "rshim0 formula must not appear when IPAM override exists"
        )
        assert "        - 192.168.101.1/30" not in rendered, (
            "rshim1 formula must not appear when IPAM override exists"
        )

    def test_ignores_sibling_dpu_in_other_project(self, db: Session, project):
        """A DPU sharing host_node_ip but owned by ANOTHER project must not
        pin an address into this host's netplan (host_node_ip is unique only
        per (project_id, host_node_ip, pci_address))."""
        probed = _make_inband_dpu(db, project, host_node_ip="10.0.0.99", pci_address="0000:0d:00.0")
        probed.host_tmfifo_ip = "192.168.100.5"
        probed.rshim_device = "rshim0"
        probed.kubernetes_cluster_id = 1
        db.commit()

        other_project = Project(name="p-other", description="")
        db.add(other_project)
        db.commit()
        foreign = _make_inband_dpu(db, other_project, host_node_ip="10.0.0.99", pci_address="0001:0d:00.0")
        foreign.host_tmfifo_ip = "192.168.101.5"
        foreign.rshim_device = "rshim1"
        foreign.kubernetes_cluster_id = 2
        db.commit()

        client = FakeSSHClient([(0, "", ""), (0, "", ""), (0, "", "")])
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:0d:00.0": "rshim0", "0001:0d:00.0": "rshim1"},
            dpu=probed, db=db,
        )

        import base64 as _b64
        import re as _re
        m = _re.search(r"printf '%s' '([A-Za-z0-9+/=]+)'", client.commands[1])
        assert m
        rendered = _b64.b64decode(m.group(1)).decode()
        assert "        - 192.168.100.5/30" in rendered, "probed DPU's IPAM address must appear"
        assert "        - 192.168.101.5/30" not in rendered, (
            "foreign-project DPU's address must NOT leak into this host's netplan"
        )

    def test_sibling_dpu_in_other_cluster_on_same_host_contributes(self, db: Session, project):
        """A sibling DPU on the same host joined to a DIFFERENT cluster MUST
        contribute its persisted host_tmfifo_ip — a host belongs to one cluster,
        so scoping to the host (not the probe subject's cluster_id) is correct.
        The old cluster-equality filter would clobber a sibling's IPAM address
        when the probe subject was in a different cluster (B1 fix)."""
        probed = _make_inband_dpu(db, project, host_node_ip="10.0.0.77", pci_address="0000:0d:00.0")
        probed.host_tmfifo_ip = "192.168.100.5"
        probed.rshim_device = "rshim0"
        probed.kubernetes_cluster_id = 10
        db.commit()

        sibling = _make_inband_dpu(db, project, host_node_ip="10.0.0.77", pci_address="0001:0d:00.0")
        sibling.host_tmfifo_ip = "192.168.101.5"
        sibling.rshim_device = "rshim1"
        sibling.kubernetes_cluster_id = 20  # different cluster, same project
        db.commit()

        client = FakeSSHClient([(0, "", ""), (0, "", ""), (0, "", "")])
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:0d:00.0": "rshim0", "0001:0d:00.0": "rshim1"},
            dpu=probed, db=db,
        )

        import base64 as _b64
        import re as _re
        m = _re.search(r"printf '%s' '([A-Za-z0-9+/=]+)'", client.commands[1])
        assert m
        rendered = _b64.b64decode(m.group(1)).decode()
        assert "        - 192.168.100.5/30" in rendered, (
            "probed DPU's IPAM address must appear"
        )
        # The sibling is on the same host — its address must also be pinned.
        assert "        - 192.168.101.5/30" in rendered, (
            "sibling DPU's IPAM address must appear; scoping to host not cluster (B1)"
        )

    def test_orphan_probe_subject_db_branch_writes_nothing(self, db: Session, project):
        """When the PROBED DPU is itself an orphan (kubernetes_cluster_id=None),
        the db-branch filter must produce no results, so no host_tmfifo_ip is
        written to netplan (ADR-424 finding A).

        Without the isnot(None) guard: kubernetes_cluster_id IS NULL matches
        every other orphan, pinning a stale host_tmfifo_ip while
        derive_tmfifo_dpu_ip returns the rshim formula → dead tmfifo link.
        """
        orphan = _make_inband_dpu(db, project, host_node_ip="10.0.0.55", pci_address="0000:0d:00.0")
        orphan.host_tmfifo_ip = "192.168.100.5"  # stale persisted IP
        orphan.rshim_device = "rshim0"
        orphan.kubernetes_cluster_id = None  # unregistered / orphan
        db.commit()

        client = FakeSSHClient([(0, "", ""), (0, "", ""), (0, "", "")])
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:0d:00.0": "rshim0"},
            dpu=orphan, db=db,
        )

        # Single-rshim host with no IPAM override → early return, no SSH commands.
        assert client.commands == [], (
            "Orphan probe subject must not trigger netplan write; "
            f"got {len(client.commands)} SSH command(s)"
        )

    def test_orphan_probe_survives_clustered_sibling_ipam(self, db: Session, project):
        """B1 regression: probing an orphan DPU on a two-rshim host must NOT
        clobber the clustered sibling DPU's persisted host_tmfifo_ip.

        Pre-fix: the DB query filtered by probe_subject.kubernetes_cluster_id.
        For an orphan, that clause became (IS NOT NULL AND = NULL) → empty
        result → ip_overrides empty → sibling's rshim interface got the
        formula address, breaking its tmfifo link.

        Post-fix: scope to host_node_ip only (no cluster_id equality); the
        isnot(None) guard still keeps orphans from contributing.
        """
        dpu_a = _make_inband_dpu(
            db, project,
            host_node_ip="10.0.0.66",
            pci_address="0000:0d:00.0",
        )
        dpu_a.host_tmfifo_ip = "192.168.100.5"   # IPAM-allocated, rshim0
        dpu_a.rshim_device = "rshim0"
        dpu_a.kubernetes_cluster_id = 42           # cluster member
        db.commit()

        dpu_b = _make_inband_dpu(
            db, project,
            host_node_ip="10.0.0.66",
            pci_address="0001:0d:00.0",
        )
        dpu_b.host_tmfifo_ip = None                # no IPAM yet
        dpu_b.rshim_device = "rshim1"
        dpu_b.kubernetes_cluster_id = None         # orphan — not yet joined
        db.commit()

        rshim_map = {
            "0000:0d:00.0": "rshim0",
            "0001:0d:00.0": "rshim1",
        }
        client = FakeSSHClient([
            (0, "", ""),   # cat → no existing file
            (0, "", ""),   # tee + chmod
            (0, "", ""),   # netplan apply
        ])

        # Probe DPU-B (orphan). DPU-A's persisted IP must survive.
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client, rshim_map, dpu=dpu_b, db=db,
        )

        import base64 as _b64
        import re as _re
        tee_cmd = client.commands[1]
        m = _re.search(r"printf '%s' '([A-Za-z0-9+/=]+)'", tee_cmd)
        assert m, f"expected base64 payload in tee cmd: {tee_cmd!r}"
        rendered = _b64.b64decode(m.group(1)).decode()

        assert "        - 192.168.100.5/30" in rendered, (
            f"DPU-A's persisted IPAM address must survive an orphan-DPU probe:\n{rendered}"
        )
        # DPU-B has no IPAM IP and is orphan — formula applies for rshim1.
        assert "        - 192.168.101.1/30" in rendered, (
            f"DPU-B's rshim1 interface must get the formula address:\n{rendered}"
        )
        # rshim0 must NOT fall back to its formula.
        assert "        - 192.168.100.1/30" not in rendered, (
            "rshim0 formula must not appear when DPU-A has an IPAM override"
        )

    def test_orphan_probe_subject_no_db_branch_writes_nothing(self):
        """No-db branch: orphan probe subject (kubernetes_cluster_id=None) must
        not contribute its stale host_tmfifo_ip (ADR-424 finding A)."""
        orphan = SimpleNamespace(
            pci_address="0000:0d:00.0",
            rshim_device="rshim0",
            host_tmfifo_ip="192.168.100.5",  # stale persisted IP
            kubernetes_cluster_id=None,       # orphan
            host_node_ip="10.0.0.55",
            id=99,
        )
        client = FakeSSHClient([(0, "", ""), (0, "", ""), (0, "", "")])
        _ensure_host_tmfifo_ips(  # type: ignore[arg-type]
            client,
            {"0000:0d:00.0": "rshim0"},
            dpu=orphan,
        )

        # ip_overrides is empty → single-rshim early return → no SSH commands.
        assert client.commands == [], (
            "Orphan probe subject (no-db branch) must not trigger netplan write"
        )


class TestShortBdfForMst:
    """`mst status -v` always prints short BDFs even on multi-domain hosts.

    Stored `pci_address` may now carry the domain prefix (we switched
    discovery to `lspci -Dnn` so DPUs that share a short BDF across
    domains stay unique). The helper drops the domain so awk-matching
    against mst output keeps working.
    """

    def test_strips_domain_prefix(self):
        assert _short_bdf_for_mst("0000:00:10") == "00:10"

    def test_short_form_passes_through(self):
        assert _short_bdf_for_mst("00:10") == "00:10"

    def test_drops_function_suffix(self):
        assert _short_bdf_for_mst("0000:00:10.2") == "00:10"

    def test_empty_inputs_return_empty_string(self):
        assert _short_bdf_for_mst("") == ""
        assert _short_bdf_for_mst(None) == ""

    def test_garbage_passes_through_lowercase(self):
        # Single-segment input has no clear short form — return as-is so
        # the caller's awk match either succeeds (if mst happens to print
        # the same thing) or harmlessly fails.
        assert _short_bdf_for_mst("nonsense") == "nonsense"
