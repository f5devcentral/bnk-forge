"""Unit tests for the BlueField Redfish probe.

Uses JSON fixtures captured from a live BlueField-3 DPU — no network.
"""

import json
from pathlib import Path

import pytest

from services.bare_metal.bluefield import BluefieldRedfishProbe, DpuInventory, ProbeError
from services.bare_metal.bluefield.explanations import (
    describe_bios_attribute,
    describe_bios_value,
)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "bluefield_redfish"


def _fixture_fetcher_factory(missing: set[str] | None = None):
    """Build a fetcher callable that reads fixtures by Redfish path.

    Any path in `missing` raises, simulating a 404 or connection error.
    """
    missing = missing or set()

    def fetcher(path: str) -> dict:
        if path in missing:
            raise FileNotFoundError(path)
        # "/redfish/v1/" → "v1.json"; "/redfish/v1/Systems/Bluefield" →
        # "v1_Systems_Bluefield.json".
        safe = path.strip("/").replace("redfish/", "").replace("/", "_")
        if safe.endswith("_"):
            safe = safe[:-1]
        return json.loads((FIXTURE_DIR / f"{safe}.json").read_text())

    return fetcher


@pytest.fixture
def probe_with_fixtures() -> BluefieldRedfishProbe:
    return BluefieldRedfishProbe(
        bmc_ip="203.0.113.1",
        username="root",
        password="secret",
        fetcher=_fixture_fetcher_factory(),
    )


class TestIdentity:
    def test_populates_system_identity(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        assert inv.manufacturer == "Nvidia"
        assert inv.model == "BlueField-3 DPU"
        assert inv.serial_number == "MT2428XZ0N1D"
        assert inv.part_number == "900-9D3B6-00CV-AA0"
        assert inv.power_state == "On"
        assert inv.system_health == "OK"
        assert inv.uuid == "1638d454-3044-ef11-8000-5c2573e63844"

    def test_populates_redfish_service_meta(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        assert inv.redfish_version == "1.17.0"
        assert inv.redfish_product == "BlueField-3 DPU"


class TestInterfaces:
    def test_all_three_interfaces_present(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        names = {iface.name for iface in inv.interfaces}
        assert names == {"oob0", "eth0", "eth1"}

    def test_falls_back_to_manager_interfaces_when_system_collection_is_empty(self):
        """BSP variation: some DPUs expose interfaces only under the Manager."""
        empty_sys_coll = {
            "@odata.id": "/redfish/v1/Systems/Bluefield/EthernetInterfaces",
            "Members": [],
        }
        mgr_coll = {
            "@odata.id": "/redfish/v1/Managers/Bluefield_BMC/EthernetInterfaces",
            "Members": [
                {"@odata.id": "/redfish/v1/Managers/Bluefield_BMC/EthernetInterfaces/eth0"},
            ],
        }
        mgr_eth0 = {
            "@odata.id": "/redfish/v1/Managers/Bluefield_BMC/EthernetInterfaces/eth0",
            "@odata.type": "#EthernetInterface.v1_6_0.EthernetInterface",
            "Id": "eth0",
            "MACAddress": "5c:25:73:e7:90:83",
            "LinkStatus": "LinkUp",
            "SpeedMbps": 1000,
            "MTUSize": 1500,
            "InterfaceEnabled": True,
            "Status": {"State": "Enabled"},
            "DHCPv4": {"DHCPEnabled": True},
            "DHCPv6": {"OperatingMode": "Enabled"},
            "IPv4Addresses": [],
            "IPv4StaticAddresses": [],
            "IPv6Addresses": [],
            "IPv6DefaultGateway": "::",
        }
        base = _fixture_fetcher_factory()

        def fetcher(path: str) -> dict:
            if path == "/redfish/v1/Systems/Bluefield/EthernetInterfaces":
                return empty_sys_coll
            if path == "/redfish/v1/Managers/Bluefield_BMC/EthernetInterfaces":
                return mgr_coll
            if path.endswith("Managers/Bluefield_BMC/EthernetInterfaces/eth0"):
                return mgr_eth0
            return base(path)

        probe = BluefieldRedfishProbe(
            bmc_ip="203.0.113.1", username="root", password="secret", fetcher=fetcher,
        )
        inv = probe.probe()
        names = {iface.name for iface in inv.interfaces}
        assert names == {"eth0"}
        mgr_eth0_result = next(i for i in inv.interfaces if i.name == "eth0")
        assert mgr_eth0_result.mac == "5c:25:73:e7:90:83"

    def test_oob0_fields(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        oob0 = next(i for i in inv.interfaces if i.name == "oob0")
        assert oob0.mac == "5c:25:73:e6:38:68"
        assert oob0.link_status == "LinkUp"
        assert oob0.speed_mbps == 1000
        assert oob0.mtu == 1500
        assert oob0.dhcp_v4_enabled is True
        assert oob0.ipv4_addresses == []  # known BSP limitation: no live DHCP v4
        assert any(a.get("AddressOrigin") == "LinkLocal" for a in oob0.ipv6_addresses)

    def test_eth1_has_jumbo_mtu_and_200gbps(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        eth1 = next(i for i in inv.interfaces if i.name == "eth1")
        assert eth1.mtu == 9266
        assert eth1.speed_mbps == 200000

    def test_eth0_has_no_ipv6_linklocal(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        eth0 = next(i for i in inv.interfaces if i.name == "eth0")
        assert eth0.ipv6_addresses == []


class TestBios:
    def test_mode_and_trust_attributes(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        assert inv.bios.host_privilege_level == "Privileged"
        assert inv.bios.nic_mode == "DpuMode"
        assert inv.bios.internal_cpu_model == "Embedded"
        assert inv.bios.field_mode is False
        assert inv.bios.enable_smmu is True
        assert inv.bios.enable_optee is False
        assert inv.bios.disable_pcie is False
        assert inv.bios.boot_partition_protection is False
        assert inv.bios.spcr_uart == "Disabled"

    def test_all_attributes_preserved_for_forensic_view(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        # all Bios attribute keys we know about should survive in the raw dict
        assert "HostPrivilegeLevel" in inv.bios.all_attributes
        assert "UefiPassword" in inv.bios.all_attributes


class TestFirmware:
    def test_core_firmware_versions(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        assert inv.firmware.bmc == "BF-25.10-15"
        assert inv.firmware.bsp == "4.13.1.13827"
        assert inv.firmware.os_image.startswith("bf-bundle-3.2.1-34")
        assert inv.firmware.uefi == "4.13.1-14-g8a01157b7f"
        assert inv.firmware.nic == "32.47.1088"
        assert inv.firmware.ofed == "MLNX_OFED_LINUX-25.10-1.7.1"
        assert inv.firmware.erot == "00.02.0195.0000_n02"
        assert inv.firmware.redfish_spec == "1.17.0"


class TestManager:
    def test_manager_info(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        assert inv.manager.firmware_version == "BF-25.10-15"
        assert inv.manager.manager_type == "BMC"
        assert inv.manager.model == "OpenBmc"
        assert inv.manager.health == "OK"
        assert inv.manager.otp_provisioned is False


class TestNvidiaDefaultPasswordFallback:
    """On 401, retry with Nvidia BF3 default password `0penBmc`."""

    def _make_live_probe(self, monkeypatch, attempted_passwords: list[str]) -> BluefieldRedfishProbe:
        """Install a fake requests.Session that records auth + returns stubbed JSON."""
        import requests as real_requests

        class _FakeResponse:
            def __init__(self, status_code: int, body: dict | None = None):
                self.status_code = status_code
                self._body = body or {}
            def json(self):
                return self._body
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise real_requests.HTTPError(f"{self.status_code}")

        class _FakeSession:
            def __init__(self, parent, password: str):
                self._parent = parent
                self.auth = None
                self.verify = True
                self.headers = {}
                self.password = password
            def get(self, url: str, timeout=None):  # noqa: ARG002
                attempted_passwords.append(self.password)
                # First pw is user-supplied (wrong) → 401; default pw → 200 with root
                if self.password == "0penBmc":
                    return _FakeResponse(200, {"Product": "BlueField-3 DPU", "RedfishVersion": "1.17.0"})
                return _FakeResponse(401)

        probe = BluefieldRedfishProbe(
            bmc_ip="203.0.113.1", username="root", password="wrong-pw",
        )
        monkeypatch.setattr(probe, "_build_session", lambda pw: _FakeSession(probe, pw))
        return probe

    def test_retries_with_nvidia_default_password_on_401(self, monkeypatch):
        attempts: list[str] = []
        probe = self._make_live_probe(monkeypatch, attempts)
        # Call _safe_get directly to test the fetcher, not a full probe.
        payload = probe._safe_get("/redfish/v1/")  # noqa: SLF001
        assert payload is not None
        assert attempts == ["wrong-pw", "0penBmc"]

    def test_does_not_retry_if_user_supplied_is_already_default(self, monkeypatch):
        attempts: list[str] = []
        import requests as real_requests

        class _AlwaysFailSession:
            def __init__(self, pw: str):
                self.auth = None
                self.verify = True
                self.headers = {}
                self.password = pw
            def get(self, url, timeout=None):  # noqa: ARG002
                attempts.append(self.password)

                class R:
                    status_code = 401
                    def json(self): return {}
                    def raise_for_status(self):
                        raise real_requests.HTTPError("401")
                return R()

        probe = BluefieldRedfishProbe(
            bmc_ip="203.0.113.1", username="root", password="0penBmc",
        )
        monkeypatch.setattr(probe, "_build_session", lambda pw: _AlwaysFailSession(pw))

        payload = probe._safe_get("/redfish/v1/")  # noqa: SLF001
        assert payload is None  # 401 swallowed by _safe_get
        assert attempts == ["0penBmc"]  # only one attempt — no retry loop


class TestPasswordChangeRequiredAutoRotate:
    """When the BMC is still in factory state, 0penBmc auth gets a 403
    PasswordChangeRequired. The probe should PATCH the configured password
    and retry once — without that the operator can never get past the chip.
    """

    def _build_probe(self, monkeypatch, attempts: list[tuple[str, int]]):
        import requests as real_requests

        class _FakeResponse:
            def __init__(self, status_code: int, body: dict | None = None):
                self.status_code = status_code
                self._body = body or {}
            def json(self):
                return self._body
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise real_requests.HTTPError(f"{self.status_code}")

        # Sequence of responses keyed by the password the session was built with.
        # First call w/ "MySecret" → 401 (BMC still on default).
        # Then 0penBmc → 403 PasswordChangeRequired.
        # After PATCH, "MySecret" → 200.
        seen = {"patched": False}

        class _FakeSession:
            def __init__(self, password: str):
                self.auth = None
                self.verify = True
                self.headers = {}
                self.password = password
            def get(self, url, timeout=None):  # noqa: ARG002
                attempts.append((self.password, len(attempts)))
                if self.password == "MySecret" and not seen["patched"]:
                    return _FakeResponse(401)
                if self.password == "0penBmc":
                    return _FakeResponse(
                        403,
                        {"error": {"MessageId": "Base.1.18.1.PasswordChangeRequired"}},
                    )
                if self.password == "MySecret" and seen["patched"]:
                    return _FakeResponse(
                        200, {"Product": "BlueField-3 DPU", "RedfishVersion": "1.17.0"},
                    )
                return _FakeResponse(500)

        probe = BluefieldRedfishProbe(
            bmc_ip="203.0.113.1", username="root", password="MySecret",
        )
        monkeypatch.setattr(probe, "_build_session", lambda pw: _FakeSession(pw))

        # Simulate a successful PATCH from inside patch_bmc_root_password.
        def _fake_patch(host, user, old, new, **_):  # noqa: ARG001
            seen["patched"] = True
        monkeypatch.setattr(
            "services.dpu_credentials.patch_bmc_root_password", _fake_patch,
        )
        return probe

    def test_rotates_password_on_pcr_and_retries_with_configured(self, monkeypatch):
        attempts: list[tuple[str, int]] = []
        probe = self._build_probe(monkeypatch, attempts)

        payload = probe._safe_get("/redfish/v1/")  # noqa: SLF001
        assert payload is not None
        assert payload.get("Product") == "BlueField-3 DPU"
        # Expect: MySecret(401) → 0penBmc(403 PCR) → PATCH → MySecret(200)
        passwords = [a[0] for a in attempts]
        assert passwords == ["MySecret", "0penBmc", "MySecret"]

    def test_does_not_rotate_when_configured_password_is_default(self, monkeypatch):
        # Operator left the configured pw at 0penBmc — nothing to rotate to.
        import requests as real_requests
        attempts: list[str] = []

        class _FakeResponse:
            def __init__(self, status_code: int, body: dict | None = None):
                self.status_code = status_code
                self._body = body or {}
            def json(self):
                return self._body
            def raise_for_status(self):
                if self.status_code >= 400:
                    raise real_requests.HTTPError(f"{self.status_code}")

        class _FakeSession:
            def __init__(self, password: str):
                self.password = password
                self.auth = None
                self.verify = True
                self.headers = {}
            def get(self, url, timeout=None):  # noqa: ARG002
                attempts.append(self.password)
                return _FakeResponse(
                    403,
                    {"error": {"MessageId": "Base.1.18.1.PasswordChangeRequired"}},
                )

        probe = BluefieldRedfishProbe(
            bmc_ip="203.0.113.1", username="root", password="0penBmc",
        )
        monkeypatch.setattr(probe, "_build_session", lambda pw: _FakeSession(pw))
        called = {"patched": False}
        def _fake_patch(*a, **kw):  # noqa: ARG001
            called["patched"] = True
        monkeypatch.setattr(
            "services.dpu_credentials.patch_bmc_root_password", _fake_patch,
        )

        result = probe._safe_get("/redfish/v1/")  # noqa: SLF001
        assert result is None  # 403 swallowed by _safe_get
        assert called["patched"] is False
        assert attempts == ["0penBmc"]


class TestResilience:
    def test_raises_if_service_root_unreachable(self):
        probe = BluefieldRedfishProbe(
            bmc_ip="203.0.113.1",
            username="root",
            password="secret",
            fetcher=_fixture_fetcher_factory(missing={"/redfish/v1/"}),
        )
        with pytest.raises(ProbeError):
            probe.probe()

    def test_missing_bios_does_not_crash(self):
        probe = BluefieldRedfishProbe(
            bmc_ip="203.0.113.1",
            username="root",
            password="secret",
            fetcher=_fixture_fetcher_factory(
                missing={"/redfish/v1/Systems/Bluefield/Bios"}
            ),
        )
        inv = probe.probe()
        # identity still populated, Bios fields left as defaults
        assert inv.serial_number == "MT2428XZ0N1D"
        assert inv.bios.host_privilege_level is None
        assert inv.bios.nic_mode is None

    def test_missing_firmware_entries_are_skipped(self):
        probe = BluefieldRedfishProbe(
            bmc_ip="203.0.113.1",
            username="root",
            password="secret",
            fetcher=_fixture_fetcher_factory(
                missing={
                    "/redfish/v1/UpdateService/FirmwareInventory/DPU_NIC",
                    "/redfish/v1/UpdateService/FirmwareInventory/DPU_OFED",
                }
            ),
        )
        inv = probe.probe()
        assert inv.firmware.bmc == "BF-25.10-15"
        assert inv.firmware.nic is None
        assert inv.firmware.ofed is None


class TestRawForensicData:
    def test_all_probed_paths_retained(self, probe_with_fixtures):
        inv = probe_with_fixtures.probe()
        assert "/redfish/v1/" in inv.raw
        assert "/redfish/v1/Systems/Bluefield" in inv.raw
        assert "/redfish/v1/Systems/Bluefield/EthernetInterfaces/oob0" in inv.raw
        assert "/redfish/v1/Systems/Bluefield/Bios" in inv.raw


class TestExplanations:
    def test_bios_attribute_descriptions(self):
        desc = describe_bios_attribute("HostPrivilegeLevel")
        assert "trusted" in desc.lower()

    def test_bios_enum_values(self):
        assert "DPU OS owns" in describe_bios_value("NicMode", "DpuMode")
        assert "Host is trusted" in describe_bios_value("HostPrivilegeLevel", "Privileged")
        assert (
            "zero-trust"
            in describe_bios_value("HostPrivilegeLevel", "Restricted").lower()
        )

    def test_unknown_value_falls_back_to_str(self):
        assert describe_bios_value("NicMode", "SomeNewMode") == "SomeNewMode"
        assert describe_bios_value("UnknownAttr", "X") == "X"
