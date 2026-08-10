"""Unit tests for DpuOsProbeService.

Uses injected SSH runners — no real network. Models the captured output of
the commands the service actually issues (cat /sys/class/net/eth0/address,
ifconfig eth0, ping-sweep + /proc/net/arp, uname -a + ip -br addr).
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401
from core.encryption import encrypt_value
from database import Base
from models.dpu import Dpu, ProjectDpuSettings
from models.project import Project
from services.dpu_os_probe_service import (
    DpuOsProbeService,
    _extract_oob_ipv4,
    _ip_mask_to_cidr,
    _mac_delta,
    _parse_bond0,
    _parse_doca_release,
    _parse_dpu_os_probe_output,
    _parse_ethtool,
    _parse_ethtool_drv_firmware,
    _parse_firmware_section,
    _parse_getent_hosts,
    _parse_ipmi_mc_firmware,
    _parse_lacp_sections,
    _parse_mlxconfig_settings,
    _parse_route_get,
    _split_marked_sections,
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


@pytest.fixture
def dpu(db: Session, project: Project) -> Dpu:
    d = Dpu(
        project_id=project.id,
        bmc_ip="192.168.68.100",
        bmc_username_encrypted=encrypt_value("root"),
        bmc_password_encrypted=encrypt_value("hb9rwm4hb9fx"),
    )
    db.add(d)
    settings = ProjectDpuSettings(
        project_id=project.id,
        default_os_username="ubuntu",
        default_os_password_encrypted=encrypt_value("ubuntu-pw"),
    )
    db.add(settings)
    db.commit()
    db.refresh(d)
    return d


# ── Helper constructions ──────────────────────────────────────────────────

BMC_MAC = "5c:25:73:e6:38:69"
OS_MAC = "5c:25:73:e6:38:68"
# Same-device sibling MAC (same first 5 bytes as BMC, different last byte).
# Represents a valid DPU-OS MAC when the exact `bmc_mac - 1` rule doesn't
# apply (e.g. older BFB with a different allocation offset).
SAME_DEVICE_SIBLING_MAC = "5c:25:73:e6:38:80"
# Another DPU's OS MAC sharing the OOB L2 — must NOT be picked up as a
# fallback for this DPU. First 5 bytes differ (different BF-3 silicon).
OTHER_DPU_OS_MAC = "5c:25:73:aa:bb:cc"

BMC_STATE_OUT = (
    f"{BMC_MAC}\n"
    "---\n"
    "eth0      Link encap:Ethernet  HWaddr 5C:25:73:E6:38:69  \n"
    "          inet addr:192.168.68.100  Bcast:192.168.71.255  Mask:255.255.252.0\n"
    "          UP BROADCAST RUNNING MULTICAST  MTU:1500  Metric:1\n"
)

ARP_TABLE_WITH_OS_MAC = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    f"192.168.68.79    0x1         0x2         {OS_MAC}     *        eth0\n"
    f"192.168.68.1     0x1         0x2         40:ed:00:86:36:ba     *        eth0\n"
    # Noise on another interface — must be ignored even if OUI matches
    f"192.168.240.6    0x1         0x2         5c:25:73:aa:bb:cc     *        vlan4040\n"
)

# No exact OS_MAC match but a SAME-DEVICE sibling is present — fallback
# must accept it because it lives on the same BF-3 silicon as the BMC.
ARP_TABLE_WITH_SAME_DEVICE_SIBLING = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    f"192.168.68.85    0x1         0x2         {SAME_DEVICE_SIBLING_MAC}     *        eth0\n"
    f"192.168.68.1     0x1         0x2         40:ed:00:86:36:ba     *        eth0\n"
)

# No exact match, and the only Mellanox MAC belongs to ANOTHER DPU
# (different first 5 bytes). Fallback must reject — returning this IP
# would be a false positive in a shared-L2 multi-DPU subnet.
ARP_TABLE_WITH_OTHER_DPU_ONLY = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    f"192.168.68.85    0x1         0x2         {OTHER_DPU_OS_MAC}     *        eth0\n"
    f"192.168.68.1     0x1         0x2         40:ed:00:86:36:ba     *        eth0\n"
)

ARP_TABLE_EMPTY = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "192.168.68.1     0x1         0x2         40:ed:00:86:36:ba     *        eth0\n"
)

# Secondary-interface neighbor ONLY: no OOB subnet entries. Probe must skip the
# vlan4040 entry even though its OUI is Mellanox.
ARP_TABLE_ONLY_SECONDARY = (
    "IP address       HW type     Flags       HW address            Mask     Device\n"
    "192.168.240.6    0x1         0x2         5c:25:73:aa:bb:cc     *        vlan4040\n"
)


def _mk_bmc_runner(outputs: dict[str, str]):
    """Return an ssh_runner that dispatches by substring match on command."""
    def runner(host, user, pw, command):  # noqa: ARG001
        for key, out in outputs.items():
            if key in command:
                return out
        raise AssertionError(f"Unexpected BMC command: {command}")
    return runner


def _mk_os_runner(uname: str = "Linux dpu-arm 6.1.0", addrs: str = "oob_net0  UP 192.168.68.79"):
    """Build a fake DPU-OS runner that mirrors the marker layout the
    real probe shell emits (`=== <key> ===` between sections).

    Tests that don't care about ipmitool fields can still call this with
    just uname + addrs; ipmitool sub-sections come back empty, which
    matches what hosts without ipmitool installed would produce.
    """
    def runner(host, user, pw, command):  # noqa: ARG001
        return (
            f"=== uname ===\n{uname}\n"
            "=== hostname ===\n"
            "=== ip_addr ===\n"
            f"{addrs}\n"
            "=== ip4_route ===\n"
            "=== ip6_route ===\n"
            "=== ipmi_sdr_temp ===\n"
            "=== ipmi_mc ===\n"
            "=== ipmi_fru0 ===\n"
            "=== ipmi_fru1 ===\n"
            "=== ipmi_sensor ===\n"
            "=== ipmi_sdr ===\n"
            "=== ipmi_lan ===\n"
            "=== mlx_q ===\n"
        )
    return runner


# ── Tests ─────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_mac_delta_minus_one(self):
        assert _mac_delta("5c:25:73:e6:38:69", -1) == "5c:25:73:e6:38:68"

    def test_mac_delta_wraps(self):
        assert _mac_delta("5c:25:73:e6:38:00", -1) == "5c:25:73:e6:38:ff"

    def test_ip_mask_to_cidr(self):
        assert _ip_mask_to_cidr("192.168.68.100", "255.255.252.0") == "192.168.68.100/22"
        assert _ip_mask_to_cidr("10.0.0.1", "255.255.255.0") == "10.0.0.1/24"


class TestHappyPath:
    def test_finds_os_ip_via_exact_mac_match(self, db, dpu):
        bmc_runner = _mk_bmc_runner({
            "cat /sys/class/net/eth0/address": BMC_STATE_OUT,
            "ping -c 1 -W 1": ARP_TABLE_WITH_OS_MAC,
        })
        os_runner = _mk_os_runner(
            uname="Linux dpu-arm 6.1.0 aarch64 GNU/Linux",
            addrs="oob_net0  UP 192.168.68.79/22",
        )
        svc = DpuOsProbeService(db, bmc_runner=bmc_runner, os_runner=os_runner)
        svc.run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.dpu_os_ip == "192.168.68.79"
        assert dpu.dpu_os_reachable is True
        assert dpu.dpu_os_status == "reachable"
        os_info = (dpu.last_discovery_payload or {}).get("dpu_os") or {}
        assert os_info["mac"] == OS_MAC
        assert "aarch64" in (os_info.get("uname") or "")


class TestMarkProbing:
    def test_marks_dpu_probing(self, db, dpu, project):
        DpuOsProbeService(db).mark_probing(project.id, dpu.id)
        db.commit()
        db.refresh(dpu)
        assert dpu.dpu_os_status == "probing"


class TestFallback:
    def test_falls_back_to_same_device_sibling_when_exact_delta_missing(self, db, dpu):
        """Exact `bmc-1` match missing but a MAC with the same first 5
        bytes is present → accept it."""
        bmc_runner = _mk_bmc_runner({
            "cat /sys/class/net/eth0/address": BMC_STATE_OUT,
            "ping -c 1 -W 1": ARP_TABLE_WITH_SAME_DEVICE_SIBLING,
        })
        os_runner = _mk_os_runner()
        svc = DpuOsProbeService(db, bmc_runner=bmc_runner, os_runner=os_runner)
        svc.run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.dpu_os_ip == "192.168.68.85"
        assert dpu.dpu_os_reachable is True

    def test_rejects_other_dpu_os_mac_in_shared_l2(self, db, dpu):
        """Regression for the multi-DPU false-positive: if the only
        Mellanox-OUI MAC in ARP belongs to a DIFFERENT DPU (first 5
        bytes don't match this BMC), the fallback must REJECT and the
        probe must fail rather than silently claim a sibling's IP."""
        bmc_runner = _mk_bmc_runner({
            "cat /sys/class/net/eth0/address": BMC_STATE_OUT,
            "ping -c 1 -W 1": ARP_TABLE_WITH_OTHER_DPU_ONLY,
        })
        svc = DpuOsProbeService(db, bmc_runner=bmc_runner, os_runner=_mk_os_runner())
        svc.run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.dpu_os_reachable is False
        assert dpu.dpu_os_status == "unreachable"
        # We must NOT have picked the sibling DPU's IP.
        assert dpu.dpu_os_ip != "192.168.68.85"


class TestSecondaryInterfaceNoise:
    """Entries on non-eth0 devices (e.g. vlan4040) must not be picked."""

    def test_ignores_vlan4040_fallback(self, db, dpu):
        bmc_runner = _mk_bmc_runner({
            "cat /sys/class/net/eth0/address": BMC_STATE_OUT,
            "ping -c 1 -W 1": ARP_TABLE_ONLY_SECONDARY,
        })
        svc = DpuOsProbeService(db, bmc_runner=bmc_runner, os_runner=_mk_os_runner())
        svc.run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.dpu_os_reachable is False
        assert dpu.dpu_os_status == "unreachable"


class TestNoCandidate:
    def test_empty_arp_cache_is_failure(self, db, dpu):
        bmc_runner = _mk_bmc_runner({
            "cat /sys/class/net/eth0/address": BMC_STATE_OUT,
            "ping -c 1 -W 1": ARP_TABLE_EMPTY,
        })
        svc = DpuOsProbeService(db, bmc_runner=bmc_runner, os_runner=_mk_os_runner())
        svc.run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.dpu_os_reachable is False
        assert dpu.dpu_os_status == "unreachable"
        assert "No DPU OS candidate" in (dpu.last_discovery_payload or {})["dpu_os"]["error"]


class TestArmSshFails:
    def test_arm_ssh_failure_captured_on_row(self, db, dpu):
        bmc_runner = _mk_bmc_runner({
            "cat /sys/class/net/eth0/address": BMC_STATE_OUT,
            "ping -c 1 -W 1": ARP_TABLE_WITH_OS_MAC,
        })
        def os_runner(host, user, pw, command):  # noqa: ARG001
            raise ConnectionRefusedError("refused")
        svc = DpuOsProbeService(db, bmc_runner=bmc_runner, os_runner=os_runner)
        svc.run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.dpu_os_reachable is False
        assert "refused" in (dpu.last_discovery_payload or {})["dpu_os"]["error"].lower()


class TestCredentialGuards:
    def test_missing_os_credentials_still_returns_arp_result(self, db, dpu):
        """Without project OS creds the probe skips SSH verification but
        still records the ARP-discovered DPU-OS IP + MAC and leaves a
        `note` explaining how to enable full verification. The probe
        must not fail just because OS creds are absent."""
        settings = db.query(ProjectDpuSettings).first()
        settings.default_os_username = None
        settings.default_os_password_encrypted = None
        db.commit()

        bmc_runner = _mk_bmc_runner({
            "cat /sys/class/net/eth0/address": BMC_STATE_OUT,
            "ping -c 1 -W 1": ARP_TABLE_WITH_OS_MAC,
        })
        svc = DpuOsProbeService(db, bmc_runner=bmc_runner, os_runner=_mk_os_runner())
        svc.run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.dpu_os_reachable is True
        assert dpu.dpu_os_ip == "192.168.68.79"
        payload = (dpu.last_discovery_payload or {}).get("dpu_os") or {}
        assert "note" in payload
        assert "set 'DPU OS Default Username'" in payload["note"]
        # SSH-only fields are explicitly None (skipped).
        assert payload["uname"] is None


class TestMissingDpu:
    def test_vanished_dpu_does_not_crash(self, db):
        DpuOsProbeService(db).run_and_persist(99999)


class TestNvidiaDefaultBmcPasswordFallback:
    """On BMC SSH auth failure, retry with Nvidia BF3 default password `0penBmc`."""

    def test_retries_with_default_password_on_paramiko_auth_failure(self, db, dpu):
        import paramiko

        attempts: list[tuple[str, str]] = []

        def bmc_runner(host, user, pw, command):  # noqa: ARG001
            attempts.append((user, pw))
            if pw == "0penBmc":
                if "cat /sys/class/net/eth0/address" in command:
                    return BMC_STATE_OUT
                if "ping -c 1 -W 1" in command:
                    return ARP_TABLE_WITH_OS_MAC
                raise AssertionError("Unexpected command")
            raise paramiko.ssh_exception.AuthenticationException("bad password")

        svc = DpuOsProbeService(
            db, bmc_runner=bmc_runner, os_runner=_mk_os_runner(),
        )
        svc.run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.dpu_os_reachable is True
        # Two commands on the BMC side, each retried once → 4 attempts total
        # (first with user-supplied pw fails, second with 0penBmc succeeds).
        assert len(attempts) == 4
        assert attempts[0] == ("root", "hb9rwm4hb9fx")
        assert attempts[1] == ("root", "0penBmc")
        assert attempts[2] == ("root", "hb9rwm4hb9fx")
        assert attempts[3] == ("root", "0penBmc")

    def test_no_retry_when_already_using_default(self, db, dpu):
        import paramiko

        from core.encryption import encrypt_value

        # Set DPU's BMC password to 0penBmc directly → no retry should happen
        dpu.bmc_password_encrypted = encrypt_value("0penBmc")
        db.commit()

        attempts: list[str] = []

        def bmc_runner(host, user, pw, command):  # noqa: ARG001
            attempts.append(pw)
            raise paramiko.ssh_exception.AuthenticationException("bad password")

        svc = DpuOsProbeService(db, bmc_runner=bmc_runner, os_runner=_mk_os_runner())
        svc.run_and_persist(dpu.id)

        db.refresh(dpu)
        assert dpu.dpu_os_reachable is False
        assert attempts == ["0penBmc"]  # single attempt, no fallback


# ── Probe output parser ─────────────────────────────────────────────────────


class TestParseDpuOsProbeOutput:
    """The probe runs uname/hostname/ip + 7 ipmitool sub-commands in one
    SSH round-trip and slices the output by `=== <key> ===` markers.

    The parser must keep the IPMI dict shape stable so the UI can render
    every sub-section without conditional null-checks.
    """

    def _sample(self) -> str:
        return (
            "=== uname ===\n"
            "Linux worker1-dpu1 6.1.0-arm64\n"
            "=== hostname ===\n"
            "worker1-dpu1\n"
            "=== ip_addr ===\n"
            "oob_net0  UP  5c:25:73:aa:bb:cc  10.0.0.5/24\n"
            "=== ip4_route ===\n"
            "default via 10.0.0.1\n"
            "=== ip6_route ===\n"
            "fe80::/64 dev oob_net0\n"
            "=== ipmi_sdr_temp ===\n"
            "DPU_TEMP         | 38 degrees C      | ok\n"
            "=== ipmi_mc ===\n"
            "Device ID                 : 32\n"
            "Manufacturer Name         : Mellanox\n"
            "=== ipmi_fru0 ===\n"
            "FRU Device Description : Builtin FRU Device\n"
            "Product Name           : BlueField-3\n"
            "=== ipmi_fru1 ===\n"
            "Device not present (Requested sensor, data, or record not found)\n"
            "=== ipmi_sensor ===\n"
            "DPU_TEMP        | 38.000   | degrees C  | ok\n"
            "=== ipmi_sdr ===\n"
            "DPU_TEMP        | 1F | ok | 7.1 | 38 degrees C\n"
            "=== ipmi_lan ===\n"
            "IP Address Source       : DHCP\n"
            "IP Address              : 192.168.68.100\n"
            "=== mlx_q ===\n"
            "Device #1:\n"
            "----------\n"
            "Device type:        BlueField3\n"
            "Description:        BlueField-3 P-Series DPU\n"
            "Device:             /dev/mst/mt41692_pciconf0\n"
            "\n"
            "Configurations:                          Next Boot\n"
            "         LINK_TYPE_P1                    IB(1)\n"
            "         LINK_TYPE_P2                    IB(1)\n"
            "         INTERNAL_CPU_MODEL              EMBEDDED_CPU(1)\n"
        )

    def test_splits_every_section(self):
        parsed = _parse_dpu_os_probe_output(self._sample())
        assert parsed["uname"] == "Linux worker1-dpu1 6.1.0-arm64"
        assert parsed["hostname"] == "worker1-dpu1"
        assert parsed["ip_addr"].startswith("oob_net0")
        assert parsed["ip4_route"] == "default via 10.0.0.1"
        assert parsed["ip6_route"] == "fe80::/64 dev oob_net0"

    def test_ipmitool_dict_has_every_subsection_in_order(self):
        parsed = _parse_dpu_os_probe_output(self._sample())
        ipmi = parsed["ipmitool"]
        # Order of the dict matters for the UI render — Python dicts
        # preserve insertion order since 3.7, so the parser's literal
        # field order is the wire order.
        assert list(ipmi.keys()) == [
            "sdr_type_temperature",
            "mc_info",
            "fru_0",
            "fru_1",
            "sensor_list",
            "sdr_elist",
            "lan_print",
        ]
        assert "DPU_TEMP" in ipmi["sdr_type_temperature"]
        assert "Manufacturer Name" in ipmi["mc_info"]
        assert "BlueField-3" in ipmi["fru_0"]
        # The non-existent FRU 1 captures the BMC's error message verbatim
        # rather than aborting the rest of the probe.
        assert "Device not present" in ipmi["fru_1"]
        assert "192.168.68.100" in ipmi["lan_print"]

    def test_missing_sections_become_empty_strings(self):
        # Truncated probe (e.g. SSH cut off mid-stream) — every key still
        # present so the UI can null-check uniformly with `value.trim()`.
        parsed = _parse_dpu_os_probe_output("=== uname ===\nLinux foo\n")
        assert parsed["uname"] == "Linux foo"
        assert parsed["hostname"] == ""
        assert parsed["ipmitool"]["sdr_type_temperature"] == ""
        assert parsed["ipmitool"]["lan_print"] == ""

    def test_split_marked_sections_ignores_text_before_first_header(self):
        # Some shells emit a login banner before the first command. Anything
        # ahead of the first `=== … ===` header must be discarded so it
        # doesn't pollute a section's value.
        out = "Welcome to ARM\nLast login Mon …\n=== uname ===\nLinux\n"
        s = _split_marked_sections(out)
        assert s == {"uname": "Linux"}

    def test_split_preserves_internal_blank_lines(self):
        # `ipmitool mc info` produces blank lines between blocks. Don't
        # collapse them — operators read these as visual separators.
        out = (
            "=== ipmi_mc ===\n"
            "Device ID                 : 32\n"
            "\n"
            "Manufacturer Name         : Mellanox\n"
        )
        s = _split_marked_sections(out)
        assert "\n\n" in s["ipmi_mc"]


# ── Phase 4: BMC-side IB detection via DPU OS mlxconfig ─────────────────────


class TestParseMlxconfigSettings:
    """The DPU OS probe runs `mlxconfig … q` on the DPU OS so the IB
    badge + Convert-to-Ethernet action work for BMC DPUs whose host
    can't run mlxconfig directly.
    """

    def test_extracts_link_type_keys(self):
        raw = (
            "Device type:        BlueField3\n"
            "Description:        BlueField-3 P-Series DPU\n"
            "Device:             /dev/mst/mt41692_pciconf0\n"
            "\n"
            "Configurations:                          Next Boot\n"
            "         LINK_TYPE_P1                    IB(1)\n"
            "         LINK_TYPE_P2                    ETH(2)\n"
            "         INTERNAL_CPU_MODEL              EMBEDDED_CPU(1)\n"
        )
        s = _parse_mlxconfig_settings(raw)
        assert s["LINK_TYPE_P1"] == "IB(1)"
        assert s["LINK_TYPE_P2"] == "ETH(2)"
        assert s["INTERNAL_CPU_MODEL"] == "EMBEDDED_CPU(1)"

    def test_ignores_lines_above_configurations_header(self):
        # mlxconfig prints device metadata (Device type, Description)
        # before the Configurations block — those must not leak into
        # the settings dict.
        raw = (
            "Device type:        BlueField3\n"
            "Configurations:           Next Boot\n"
            "         LINK_TYPE_P1     ETH(2)\n"
        )
        s = _parse_mlxconfig_settings(raw)
        assert s == {"LINK_TYPE_P1": "ETH(2)"}

    def test_empty_raw_returns_empty_dict(self):
        # Probe ran but MFT wasn't installed on the DPU OS — section
        # contains the canned "(no MST device — MFT not installed?)"
        # message, which doesn't match the parser regex → empty dict.
        assert _parse_mlxconfig_settings("") == {}
        assert _parse_mlxconfig_settings(
            "(no MST device — MFT not installed?)"
        ) == {}

    def test_record_success_promotes_mlxconfig_to_payload_top_level(
        self, db, dpu,
    ):
        """The IB-mode badge reads `payload.mlxconfig_settings`. The DPU
        OS probe must merge its parsed dict there for BMC DPUs whose
        host doesn't run an in-band mlxconfig probe."""
        svc = DpuOsProbeService(db)
        svc._record_success(  # noqa: SLF001 — internal helper, intentional
            dpu, "10.0.0.5", "5c:25:73:aa:bb:cc",
            os_info={
                "uname": "Linux", "hostname": "h", "ip_addr": "",
                "ip4_route": "", "ip6_route": "",
                "ipmitool": {},
                "mlxconfig_settings": {
                    "LINK_TYPE_P1": "IB(1)", "LINK_TYPE_P2": "IB(1)",
                },
                "raw_mlxconfig": "(...mlxconfig snapshot...)",
            },
        )
        db.refresh(dpu)
        # Settings live at the top level of last_discovery_payload, NOT
        # under dpu_os — that's where _derive_link_mode looks.
        assert dpu.last_discovery_payload["mlxconfig_settings"]["LINK_TYPE_P1"] == "IB(1)"
        # Forensic raw output also persisted so the verbose panel can
        # render it like the in-band probe does.
        assert "mlxconfig snapshot" in dpu.last_discovery_payload["raw_mlxconfig"]

    def test_empty_mlxconfig_does_not_overwrite_previous_capture(self, db, dpu):
        # Earlier in-band probe captured a good snapshot. Then the BMC
        # path runs without MFT installed (empty dict from parser). The
        # earlier snapshot must stay — overwriting with {} would break
        # the IB badge for everyone.
        dpu.last_discovery_payload = {
            "mlxconfig_settings": {"LINK_TYPE_P1": "ETH(2)", "LINK_TYPE_P2": "ETH(2)"},
        }
        db.commit()
        svc = DpuOsProbeService(db)
        svc._record_success(  # noqa: SLF001
            dpu, "10.0.0.5", None,
            os_info={
                "uname": "", "hostname": "", "ip_addr": "",
                "ip4_route": "", "ip6_route": "",
                "ipmitool": {},
                "mlxconfig_settings": {},
                "raw_mlxconfig": "",
            },
        )
        db.refresh(dpu)
        assert dpu.last_discovery_payload["mlxconfig_settings"]["LINK_TYPE_P1"] == "ETH(2)"


# ── Phase 3 diagnostics: route / DNS / bond / ethtool parsers ────────────


class TestRouteGet:
    def test_extracts_dev_via_src(self):
        out = "8.8.8.8 via 192.168.68.1 dev p0 src 192.168.68.96 uid 0 \n    cache"
        r = _parse_route_get(out)
        assert r == {"via_interface": "p0", "next_hop": "192.168.68.1", "src": "192.168.68.96"}

    def test_directly_attached_omits_via(self):
        out = "10.0.0.5 dev oob_net0 src 10.0.0.66 uid 0 \n    cache"
        r = _parse_route_get(out)
        assert r["via_interface"] == "oob_net0"
        assert r["next_hop"] is None
        assert r["src"] == "10.0.0.66"

    def test_unreachable_returns_nones(self):
        # `ip route get` prints "RTNETLINK answers: Network is unreachable"
        # to stderr (which we redirect to stdout via 2>&1) when no route
        # exists at all.
        out = "RTNETLINK answers: Network is unreachable"
        r = _parse_route_get(out)
        assert r["via_interface"] is None
        assert r["next_hop"] is None


class TestGetentHosts:
    def test_resolved_addresses(self):
        out = "104.219.111.168  f5.com\n2001:db8::1  f5.com www.f5.com"
        r = _parse_getent_hosts(out)
        assert r["ok"] is True
        assert "104.219.111.168" in r["addresses"]
        assert "2001:db8::1" in r["addresses"]
        assert r["error"] is None

    def test_no_output_means_dns_failed(self):
        # `getent hosts` prints nothing on failure; with `|| true` the rc
        # is masked so we have to detect failure from empty stdout.
        r = _parse_getent_hosts("")
        assert r["ok"] is False
        assert r["addresses"] == []
        assert "no addresses returned" in r["error"]


class TestEthtool:
    def test_link_up_speed_duplex(self):
        out = (
            "Settings for p0:\n"
            "\tSupported ports: [ FIBRE ]\n"
            "\tSpeed: 25000Mb/s\n"
            "\tDuplex: Full\n"
            "\tLink detected: yes\n"
        )
        r = _parse_ethtool(out)
        assert r == {"speed": "25000Mb/s", "duplex": "Full", "link": "yes"}

    def test_no_such_device(self):
        out = (
            "Settings for p0:\n"
            "Cannot get device settings: No such device\n"
        )
        r = _parse_ethtool(out)
        assert r == {"speed": None, "duplex": None, "link": None}


class TestBond0Parser:
    _SAMPLE = (
        "Ethernet Channel Bonding Driver: v5.15.0\n"
        "\n"
        "Bonding Mode: IEEE 802.3ad Dynamic link aggregation\n"
        "Transmit Hash Policy: layer3+4 (1)\n"
        "MII Status: up\n"
        "MII Polling Interval (ms): 100\n"
        "\n"
        "802.3ad info\n"
        "LACP active: on\n"
        "LACP rate: fast\n"
        "Min links: 0\n"
        "\n"
        "Slave Interface: p0\n"
        "MII Status: up\n"
        "Speed: 25000 Mbps\n"
        "Duplex: full\n"
        "Link Failure Count: 0\n"
        "Permanent HW addr: 5c:25:73:ab:cd:ef\n"
        "\n"
        "Slave Interface: p1\n"
        "MII Status: down\n"
        "Speed: Unknown\n"
        "Duplex: Unknown\n"
        "Link Failure Count: 3\n"
    )

    def test_parses_mode_lacp_rate_and_two_members(self):
        b = _parse_bond0(self._SAMPLE)
        assert b is not None
        assert b["bond_present"] is True
        assert b["mode"] == "IEEE 802.3ad Dynamic link aggregation"
        assert b["lacp_rate"] == "fast"
        assert len(b["members"]) == 2
        m0, m1 = b["members"]
        assert m0 == {
            "name": "p0",
            "mii_status": "up",
            "speed": "25000 Mbps",
            "duplex": "full",
            "link_failure_count": 0,
        }
        assert m1["name"] == "p1"
        assert m1["mii_status"] == "down"
        assert m1["link_failure_count"] == 3

    def test_empty_means_no_bond(self):
        # No /proc/net/bonding/bond0 → cat returns empty → parser returns
        # None so the LACP table can fall back to per-uplink ethtool.
        assert _parse_bond0("") is None
        assert _parse_bond0("   \n  ") is None


class TestLacpSections:
    def test_prefers_bond_when_present(self):
        bond = (
            "Bonding Mode: IEEE 802.3ad Dynamic link aggregation\n"
            "LACP rate: slow\n"
            "Slave Interface: p0\n"
            "MII Status: up\n"
            "Speed: 100000 Mbps\n"
            "Duplex: full\n"
            "Link Failure Count: 0\n"
        )
        out = _parse_lacp_sections(bond0=bond, ethtool_p0="", ethtool_p1="")
        assert out["bond_present"] is True
        assert out["lacp_rate"] == "slow"
        assert out["members"][0]["speed"] == "100000 Mbps"

    def test_falls_back_to_ethtool_when_no_bond(self):
        et0 = "Settings for p0:\n\tSpeed: 25000Mb/s\n\tDuplex: Full\n\tLink detected: yes\n"
        et1 = "Settings for p1:\nCannot get device settings: No such device\n"
        out = _parse_lacp_sections(bond0="", ethtool_p0=et0, ethtool_p1=et1)
        assert out["bond_present"] is False
        # p1 dropped — ethtool said nothing useful about it.
        assert [u["name"] for u in out["uplinks"]] == ["p0"]
        assert out["uplinks"][0]["link"] == "yes"


class TestProbeIntegrationAddsDiagnostics:
    def test_full_probe_output_carries_connectivity_and_lacp(self):
        # Stitch together the full marked output the new probe command
        # produces. _parse_dpu_os_probe_output should populate both
        # connectivity and lacp keys.
        out = "\n".join([
            "=== uname ===", "Linux dpu",
            "=== hostname ===", "dpu-1",
            "=== ip_addr ===", "p0 UP 5c:25:73:00:00:00",
            "=== ip4_route ===", "default via 192.168.68.1 dev p0",
            "=== ip6_route ===", "",
            "=== route_v4_pub ===",
            "8.8.8.8 via 192.168.68.1 dev p0 src 192.168.68.96 uid 0",
            "=== ping_v4_pub ===",
            "PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.\n"
            "64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=8.45 ms\n"
            "\n--- 8.8.8.8 ping statistics ---\n"
            "2 packets transmitted, 2 received, 0% packet loss, time 1001ms\n"
            "rtt min/avg/max/mdev = 8.123/8.456/8.789/0.333 ms",
            "=== dns_f5 ===",
            "104.219.111.168  f5.com",
            "=== bond0 ===",
            "Bonding Mode: IEEE 802.3ad Dynamic link aggregation\n"
            "LACP rate: fast\n"
            "Slave Interface: p0\nMII Status: up\nSpeed: 25000 Mbps\n"
            "Duplex: full\nLink Failure Count: 0\n"
            "Slave Interface: p1\nMII Status: up\nSpeed: 25000 Mbps\n"
            "Duplex: full\nLink Failure Count: 0\n",
            "=== ethtool_p0 ===", "",
            "=== ethtool_p1 ===", "",
            "=== ipmi_sdr_temp ===", "",
            "=== ipmi_mc ===", "",
            "=== ipmi_fru0 ===", "",
            "=== ipmi_fru1 ===", "",
            "=== ipmi_sensor ===", "",
            "=== ipmi_sdr ===", "",
            "=== ipmi_lan ===", "",
            "=== mlx_q ===", "",
        ])
        parsed = _parse_dpu_os_probe_output(out)
        conn = parsed["connectivity"]
        assert conn["internet"]["via_interface"] == "p0"
        assert conn["internet"]["next_hop"] == "192.168.68.1"
        assert conn["internet"]["ping_ok"] is True
        # Parser reports `min` from `rtt min/avg/max/mdev` — see the
        # ARP-outlier rationale in `_parse_connectivity_sections`.
        assert conn["internet"]["rtt_ms"] == 8.123
        assert conn["dns"]["f5_com"]["ok"] is True
        assert "104.219.111.168" in conn["dns"]["f5_com"]["addresses"]
        lacp = parsed["lacp"]
        assert lacp["bond_present"] is True
        assert lacp["lacp_rate"] == "fast"
        assert len(lacp["members"]) == 2

    def test_no_bond_uses_ethtool_uplinks(self):
        out = "\n".join([
            "=== uname ===", "Linux dpu",
            "=== hostname ===", "",
            "=== ip_addr ===", "",
            "=== ip4_route ===", "",
            "=== ip6_route ===", "",
            "=== route_v4_pub ===", "",
            "=== ping_v4_pub ===", "",
            "=== dns_f5 ===", "",
            "=== bond0 ===", "",
            "=== ethtool_p0 ===",
            "Settings for p0:\n\tSpeed: 25000Mb/s\n\tDuplex: Full\n\tLink detected: yes\n",
            "=== ethtool_p1 ===",
            "Settings for p1:\n\tSpeed: 25000Mb/s\n\tDuplex: Full\n\tLink detected: no\n",
            "=== ipmi_sdr_temp ===", "",
            "=== ipmi_mc ===", "",
            "=== ipmi_fru0 ===", "",
            "=== ipmi_fru1 ===", "",
            "=== ipmi_sensor ===", "",
            "=== ipmi_sdr ===", "",
            "=== ipmi_lan ===", "",
            "=== mlx_q ===", "",
        ])
        lacp = _parse_dpu_os_probe_output(out)["lacp"]
        assert lacp["bond_present"] is False
        names = [u["name"] for u in lacp["uplinks"]]
        assert names == ["p0", "p1"]
        assert lacp["uplinks"][0]["link"] == "yes"
        assert lacp["uplinks"][1]["link"] == "no"


class TestExtractOobIpv4:
    """Probe-side preference for oob_net0's DHCP-assigned IPv4 over the
    tmfifo SSH-tunnel target. tmfifo (192.168.100.2) is host-local and
    not what operators want surfaced as the DPU's "where am I" address.
    """

    def test_typical_dhcp_lease(self):
        out = (
            "lo               UNKNOWN        127.0.0.1/8\n"
            "oob_net0         UP             192.168.68.79/22 fe80::5e25:73ff:fee6:3868/64\n"
            "tmfifo_net0      UP             192.168.100.2/30\n"
        )
        assert _extract_oob_ipv4(out) == "192.168.68.79"

    def test_oob_with_metric_and_extra_addresses(self):
        # Real BlueField output sometimes reports 'metric 100' and a
        # global IPv6 between the IPv4 and the link-local. Parser must
        # still find the IPv4 token regardless of position.
        out = (
            "oob_net0         UP             "
            "192.168.68.79/22 metric 100 2a02:168:5e19::1011/128 fe80::abc/64\n"
        )
        assert _extract_oob_ipv4(out) == "192.168.68.79"

    def test_no_ipv4_returns_none(self):
        # Link is up but DHCP hasn't leased — only IPv6 link-local present.
        out = "oob_net0         UP             fe80::5e25:73ff:fee6:3868/64\n"
        assert _extract_oob_ipv4(out) is None

    def test_oob_absent_returns_none(self):
        out = "lo  UNKNOWN  127.0.0.1/8\ntmfifo_net0  UP  192.168.100.2/30\n"
        assert _extract_oob_ipv4(out) is None

    def test_does_not_match_tmfifo_address(self):
        # Defensive: the parser must only look at the oob_net0 line —
        # otherwise it would happily pick up tmfifo's 192.168.100.2,
        # which is exactly what we're trying to avoid surfacing.
        out = (
            "oob_net0         UP             fe80::ff/64\n"
            "tmfifo_net0      UP             192.168.100.2/30\n"
        )
        assert _extract_oob_ipv4(out) is None


# ── Firmware version parsers (DOCA / NIC FW / BMC FW) ────────────────


class TestDocaRelease:
    def test_typical_one_liner(self):
        # /etc/mlnx-release on a flashed bf-bundle is one line, no
        # trailing newline guarantees.
        assert (
            _parse_doca_release("bf-bundle-3.1.0-76_25.07_ubuntu-22.04_prod\n")
            == "bf-bundle-3.1.0-76_25.07_ubuntu-22.04_prod"
        )

    def test_strips_leading_whitespace(self):
        assert _parse_doca_release("   bf-bundle-2.9.2-32   ") == "bf-bundle-2.9.2-32"

    def test_empty_input_returns_none(self):
        # Older BFB images lack /etc/mlnx-release — parser returns None
        # rather than the literal "" so the UI can hide the cell.
        assert _parse_doca_release("") is None
        assert _parse_doca_release("\n\n   \n") is None


class TestEthtoolDrvFirmware:
    def test_extracts_firmware_version_line(self):
        out = (
            "driver: mlx5_core\n"
            "version: 6.5.0-25.07\n"
            "firmware-version: 32.46.1006 (MT_0000000884)\n"
            "expansion-rom-version: \n"
            "bus-info: 0000:03:00.0\n"
        )
        assert _parse_ethtool_drv_firmware(out) == "32.46.1006 (MT_0000000884)"

    def test_no_match_returns_none(self):
        # `ethtool -i p1` on a missing iface or one without firmware-version
        # prints "Settings for p1: Cannot get driver information".
        out = "Cannot get driver information: Operation not supported\n"
        assert _parse_ethtool_drv_firmware(out) is None


class TestIpmiMcFirmware:
    _SAMPLE = (
        "Device ID                 : 1\n"
        "Device Revision           : 1\n"
        "Firmware Revision         : 25.10\n"
        "IPMI Version              : 2.0\n"
        "Manufacturer ID           : 33049\n"
    )

    def test_extracts_revision(self):
        assert _parse_ipmi_mc_firmware(self._SAMPLE) == "25.10"

    def test_no_mc_info_block_returns_none(self):
        # Probe ran but ipmitool was missing or denied — output is the
        # error string, not a `Firmware Revision` line.
        out = "ipmitool: command not found\n"
        assert _parse_ipmi_mc_firmware(out) is None


class TestFirmwareSection:
    def test_rolls_up_all_three(self):
        fw = _parse_firmware_section(
            mlnx_release="bf-bundle-3.1.0-76_25.07_ubuntu-22.04_prod\n",
            ethtool_drv_p0="firmware-version: 32.46.1006\n",
            ipmi_mc="Firmware Revision         : 25.10\n",
        )
        assert fw == {
            "doca": "bf-bundle-3.1.0-76_25.07_ubuntu-22.04_prod",
            "nic": "32.46.1006",
            "bmc": "25.10",
        }

    def test_partial_capture_leaves_nones(self):
        # Operator hasn't installed DOCA tools yet → no /etc/mlnx-release.
        # ipmitool present, ethtool present.
        fw = _parse_firmware_section(
            mlnx_release="",
            ethtool_drv_p0="firmware-version: 32.46.1006\n",
            ipmi_mc="Firmware Revision         : 25.10\n",
        )
        assert fw == {"doca": None, "nic": "32.46.1006", "bmc": "25.10"}
