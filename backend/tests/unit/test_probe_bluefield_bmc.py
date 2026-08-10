"""Unit tests for the BlueField BMC service-root probe.

Covers the signal broadening (Vendor/Name/Id fallbacks) and the
0penBmc retry on 401, so the detection works across the various
OpenBMC flavors Nvidia ships.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from services import discovery_service


class _FakeResp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}
        self.request = MagicMock()
        self.request.headers = {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body

    @property
    def content(self):
        """Bytes-shaped view of the body — refactor now returns body bytes."""
        import json as _json
        if isinstance(self._body, Exception):
            return b""
        return _json.dumps(self._body).encode("utf-8")


@pytest.fixture
def fake_requests(monkeypatch):
    """Replace requests.get inside the module with a programmable stub."""
    responses: list = []
    calls: list[dict] = []

    def fake_get(url, verify=None, timeout=None, auth=None):  # noqa: ARG001
        calls.append({"url": url, "auth": auth})
        if not responses:
            # Phase 2 issues additional Redfish reads (Managers, Bios)
            # that older tests don't bother scripting. Default-404 so
            # existing call sequences keep behaving as if those richer
            # paths weren't there, while tests that DO care about them
            # can script explicit responses.
            return _FakeResp(404)
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    # The production code imports requests locally inside the function; patch
    # the module-level namespace so our stub is what gets used.
    import requests
    monkeypatch.setattr(requests, "get", fake_get)
    return responses, calls


class TestProduct:
    def test_matches_on_product_bluefield(self, fake_requests):
        responses, calls = fake_requests
        responses.append(_FakeResp(200, {"Product": "BlueField-3"}))
        responses.append(_FakeResp(404))  # SerialNumber fetch optional
        r = discovery_service._probe_bluefield_bmc("10.0.0.1")
        assert r["is_dpu_bmc"] is True
        assert r["product"] == "BlueField-3"
        # no auth used on success
        assert calls[0]["auth"] is None


class TestVendorPlusBmcFallback:
    def test_nvidia_vendor_with_bmc_in_name(self, fake_requests):
        responses, _ = fake_requests
        # No Product field at all — only Vendor + Name signal.
        responses.append(_FakeResp(200, {"Vendor": "NVIDIA", "Name": "OpenBMC"}))
        responses.append(_FakeResp(404))
        r = discovery_service._probe_bluefield_bmc("10.0.0.2")
        assert r["is_dpu_bmc"] is True
        assert r["product"] == "NVIDIA"  # best available

    def test_no_bluefield_signal_rejects(self, fake_requests):
        responses, _ = fake_requests
        # Generic unbranded BMC — must NOT be flagged.
        responses.append(_FakeResp(200, {"Vendor": "Supermicro", "Product": "X11"}))
        r = discovery_service._probe_bluefield_bmc("10.0.0.3")
        assert r["is_dpu_bmc"] is False


class TestAuthFallback:
    def test_401_retries_with_nvidia_default(self, fake_requests):
        responses, calls = fake_requests
        responses.append(_FakeResp(401))
        responses.append(_FakeResp(200, {"Product": "BlueField-3"}))
        responses.append(_FakeResp(404))
        r = discovery_service._probe_bluefield_bmc("10.0.0.4")
        assert r["is_dpu_bmc"] is True
        assert calls[0]["auth"] is None
        auth = calls[1]["auth"]
        assert auth is not None
        assert auth.username == "root"
        assert auth.password == "0penBmc"


class TestSerialNumber:
    def test_serial_fetched_when_systems_bluefield_responds(self, fake_requests):
        responses, _ = fake_requests
        responses.append(_FakeResp(200, {"Product": "BlueField-3"}))
        responses.append(_FakeResp(200, {"SerialNumber": "MT-ABC-123"}))
        r = discovery_service._probe_bluefield_bmc("10.0.0.5")
        assert r["is_dpu_bmc"] is True
        assert r["serial_number"] == "MT-ABC-123"


class TestResilience:
    def test_connection_failure_yields_negative(self, fake_requests, monkeypatch):
        import requests
        def boom(*a, **kw):  # noqa: ARG001
            raise requests.ConnectionError("refused")
        monkeypatch.setattr(requests, "get", boom)
        r = discovery_service._probe_bluefield_bmc("10.0.0.6")
        assert r["is_dpu_bmc"] is False


class TestJumphostTransport:
    def test_probe_routed_through_jumphost_ssh_tunnel(self, monkeypatch):
        # Replace the transport wholesale: when a jumphost_cred is provided,
        # _probe_bluefield_bmc instantiates _JumphostRedfishTransport. We
        # patch its __enter__/get so the probe doesn't touch real SSH.
        import json as _json
        opens: list[dict] = []

        class _FakeJumphostTransport:
            last_auth_used = False
            def __init__(self, jumphost_cred, *, bmc_ip, timeout=10):  # noqa: ARG002
                opens.append({"jumphost": jumphost_cred, "bmc": bmc_ip})
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def get(self, path, auth=None):
                if path == "/redfish/v1/":
                    return 200, _json.dumps({"Product": "BlueField-3 DPU"}).encode()
                if path == "/redfish/v1/Systems/Bluefield":
                    return 200, _json.dumps({"SerialNumber": "MT-X"}).encode()
                return 404, b""

        monkeypatch.setattr(
            discovery_service, "_JumphostRedfishTransport", _FakeJumphostTransport,
        )

        jump = {
            "host": "jumphost.lab", "port": 22, "username": "ops",
            "auth_type": "password", "password": "secret",
            "private_key": None, "key_passphrase": None,
        }
        r = discovery_service._probe_bluefield_bmc("10.2.0.5", jump)
        assert r["is_dpu_bmc"] is True
        assert r["serial_number"] == "MT-X"
        assert opens == [{"jumphost": jump, "bmc": "10.2.0.5"}]


# ── Operator-credential auth ladder ─────────────────────────────────────────


class TestOperatorAuthLadder:
    """Phase-1.5: when discovery is run with operator-supplied SSH creds,
    use them for Redfish basic-auth too. Order: anonymous → operator → 0penBmc.
    """

    def test_operator_creds_tried_after_anonymous_401(self, fake_requests):
        responses, calls = fake_requests
        responses.append(_FakeResp(401))                              # anonymous
        responses.append(_FakeResp(200, {"Product": "BlueField-3"}))  # operator wins
        responses.append(_FakeResp(200, {"SerialNumber": "MT-OP"}))   # systems

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.7", operator_auth=("root", "lab-pw"),
        )
        assert r["is_dpu_bmc"] is True
        assert r["serial_number"] == "MT-OP"
        # Anonymous first.
        assert calls[0]["auth"] is None
        # Operator second.
        assert calls[1]["auth"].username == "root"
        assert calls[1]["auth"].password == "lab-pw"
        # Systems probe re-uses the same auth that won — no climb again.
        assert calls[2]["auth"].password == "lab-pw"

    def test_operator_creds_invalid_falls_back_to_nvidia_default(
        self, fake_requests,
    ):
        responses, calls = fake_requests
        responses.append(_FakeResp(401))                              # anonymous
        responses.append(_FakeResp(401))                              # operator (wrong pw)
        responses.append(_FakeResp(200, {"Product": "BlueField-3"}))  # 0penBmc wins
        responses.append(_FakeResp(404))                              # systems → not found

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.8", operator_auth=("root", "wrong-pw"),
        )
        assert r["is_dpu_bmc"] is True
        # Three attempts on service-root before success, then one each
        # on Systems / Managers / Bios / Oem-Nvidia /
        # ExternalHostPrivileges + 7 firmware components (the
        # diagnostic capture added when Job #3 surfaced asymmetric BSP
        # versions on two same-model DPUs). 3 + 12 = 15.
        assert len(calls) == 15
        assert calls[2]["auth"].password == "0penBmc"

    def test_operator_creds_equal_to_nvidia_default_not_tried_twice(
        self, fake_requests,
    ):
        # When the operator typed `root` / `0penBmc` the ladder should
        # dedupe — anonymous (401) then 0penBmc (200), not three calls.
        responses, calls = fake_requests
        responses.append(_FakeResp(401))                              # anonymous
        responses.append(_FakeResp(200, {"Product": "BlueField-3"}))  # 0penBmc
        responses.append(_FakeResp(404))                              # systems

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.9", operator_auth=("root", "0penBmc"),
        )
        assert r["is_dpu_bmc"] is True
        # Exactly: anonymous, then ONE basic-auth attempt (the deduped pair).
        attempts = [c for c in calls if c["url"].endswith("/redfish/v1/")]
        assert len(attempts) == 2
        assert attempts[0]["auth"] is None
        assert attempts[1]["auth"].password == "0penBmc"

    def test_anonymous_root_then_authd_systems(self, fake_requests):
        """Regression: NVIDIA OpenBMC builds expose /redfish/v1/ to
        anonymous reads while gating /Systems/Bluefield behind auth.
        The first version of operator-creds plumbing accepted anonymous
        for service-root, reused that for the systems probe, got 401,
        and lost the serial number — exactly what Job #6 hit on
        worker1's BMC. The systems-path must climb the ladder again
        instead of reusing the anonymous root auth.
        """
        responses, calls = fake_requests
        # service-root: anonymous → 200 (BMC publishes Root Service openly)
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        # systems: anonymous → 401 (Systems requires real auth)
        responses.append(_FakeResp(401))
        # systems: operator creds → 200 (this is what should happen)
        responses.append(_FakeResp(200, {"SerialNumber": "MT-OP-CREDS"}))

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.11", operator_auth=("root", "lab-pw"),
        )
        assert r["is_dpu_bmc"] is True
        assert r["serial_number"] == "MT-OP-CREDS"
        # 14 calls: anonymous root + anonymous systems (401) +
        # authed systems + Managers + Bios + Oem-Nvidia +
        # ExternalHostPrivileges + 7 firmware components. All but
        # service-root and Systems land on default-404 from the
        # fixture but still count as round-trips.
        assert len(calls) == 14
        assert calls[0]["auth"] is None
        assert calls[1]["auth"] is None  # systems first reuses sr auth
        assert calls[2]["auth"].password == "lab-pw"

    def test_no_operator_password_skips_that_rung(self, fake_requests):
        # Discovery via SSH key only — no Redfish-usable password.
        # Ladder is just anonymous + 0penBmc.
        responses, calls = fake_requests
        responses.append(_FakeResp(401))
        responses.append(_FakeResp(200, {"Product": "BlueField-3"}))
        responses.append(_FakeResp(404))

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.10", operator_auth=None,
        )
        assert r["is_dpu_bmc"] is True
        # 2 service-root attempts (anonymous 401, default 200) + 12
        # richer paths (Systems, Managers, Bios, Oem-Nvidia,
        # ExternalHostPrivileges, 7 firmware components) — 14 total.
        assert len(calls) == 14
        assert calls[1]["auth"].password == "0penBmc"


# ── Phase 2: richer Redfish capture + default-password signal ────────────────


class TestRedfishPayload:
    """The Discovery tab's BMC panel renders whatever fields the probe
    can extract from /Systems/Bluefield, /Managers/Bluefield_BMC, and
    /Systems/Bluefield/Bios. UsesDefaultPassword surfaces a warning
    chip when 0penBmc is what authenticated."""

    def test_collects_system_manager_bios_fields(self, fake_requests):
        responses, _ = fake_requests
        # service-root anonymous → 200
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        # systems anonymous (401) then operator (200, rich payload)
        responses.append(_FakeResp(401))
        responses.append(_FakeResp(200, {
            "SerialNumber": "MT-RICH-1",
            "Manufacturer": "NVIDIA",
            "Model": "BlueField-3 P-Series",
            "BiosVersion": "BlueField-3-R6.13.7",
            "PowerState": "On",
            "MemorySummary": {"TotalSystemMemoryGiB": 32},
        }))
        # managers operator → 200 with FW
        responses.append(_FakeResp(200, {
            "FirmwareVersion": "BMC-25.10",
            "PowerState": "On",
        }))
        # bios operator → 200 with attrs
        responses.append(_FakeResp(200, {
            "Attributes": {
                "NicMode": "DpuMode",
                "HostPrivilegeLevel": "Privileged",
                "InternalCPUModel": "EmbeddedCPU",
            },
        }))

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.20", operator_auth=("root", "lab-pw"),
        )
        assert r["serial_number"] == "MT-RICH-1"
        rf = r["redfish"]
        assert rf["Manufacturer"] == "NVIDIA"
        assert rf["Model"] == "BlueField-3 P-Series"
        assert rf["BiosVersion"] == "BlueField-3-R6.13.7"
        assert rf["PowerState"] == "On"
        assert rf["MemoryGiB"] == 32
        assert rf["BmcFirmwareVersion"] == "BMC-25.10"
        assert rf["BmcPowerState"] == "On"
        assert rf["NicMode"] == "DpuMode"
        assert rf["HostPrivilegeLevel"] == "Privileged"
        # Operator creds won — the BMC is not on its factory default.
        assert r["uses_default_password"] is False

    def test_uses_default_password_flagged_when_0penbmc_wins(self, fake_requests):
        responses, _ = fake_requests
        # service-root anonymous OK
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        # systems anonymous → 401, operator → 401, default 0penBmc → 200
        responses.append(_FakeResp(401))
        responses.append(_FakeResp(401))
        responses.append(_FakeResp(200, {"SerialNumber": "MT-DEFAULT-1"}))

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.21", operator_auth=("root", "wrong-pw"),
        )
        assert r["serial_number"] == "MT-DEFAULT-1"
        # 0penBmc is what authenticated — security signal flagged.
        assert r["uses_default_password"] is True

    def test_uses_default_password_false_when_anonymous_only(
        self, fake_requests,
    ):
        # Some BMCs are wide open and accept anonymous everywhere. We
        # can't infer anything about the password from that — leave the
        # flag false.
        responses, _ = fake_requests
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        responses.append(_FakeResp(200, {"SerialNumber": "MT-ANON"}))
        responses.append(_FakeResp(404))  # Managers
        responses.append(_FakeResp(404))  # Bios

        r = discovery_service._probe_bluefield_bmc("10.0.0.22")
        assert r["uses_default_password"] is False


class TestPasswordChangeRequired:
    """BlueField OpenBMC returns HTTP 403 with `PasswordChangeRequired`
    when default `0penBmc` authenticates but the password has never been
    rotated. The probe must surface `uses_default_password=True` even
    though no rich payload could be fetched — Job #7 on .97 hit this
    and lost the security signal entirely."""

    def _pcr_body(self) -> dict:
        return {
            "@Message.ExtendedInfo": [{
                "@odata.type": "#Message.v1_1_1.Message",
                "Message": (
                    "The password provided for this account must be "
                    "changed before access is granted."
                ),
                "MessageArgs": [
                    "/redfish/v1/AccountService/Accounts/root",
                ],
                "MessageId": "Base.1.18.1.PasswordChangeRequired",
                "MessageSeverity": "Critical",
            }],
            "code": "Base.1.18.1.PasswordChangeRequired",
        }

    def test_pcr_on_systems_flags_default_password(self, fake_requests):
        responses, _ = fake_requests
        # service-root: anonymous → 200 (open).
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        # systems: anonymous 401, operator 401, default → 403 PCR.
        responses.append(_FakeResp(401))
        responses.append(_FakeResp(401))
        responses.append(_FakeResp(403, self._pcr_body()))
        # managers + bios will fall through to fixture's default 404.

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.30", operator_auth=("root", "wrong-pw"),
        )
        assert r["is_dpu_bmc"] is True
        # No serial — auth couldn't actually fetch the resource.
        assert r["serial_number"] is None
        # Security signal: default password active, change required.
        assert r["uses_default_password"] is True
        assert r["redfish"].get("PasswordChangeRequired") is True

    def test_pcr_at_top_level_message_id(self, fake_requests):
        # Some Redfish builds put MessageId at the top level of the body
        # (no @Message.ExtendedInfo array). Detector must match both.
        responses, _ = fake_requests
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        responses.append(_FakeResp(401))
        responses.append(_FakeResp(403, {
            "MessageId": "Base.1.0.0.PasswordChangeRequired",
        }))

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.31", operator_auth=None,
        )
        assert r["uses_default_password"] is True

    def test_403_without_pcr_marker_does_not_flag(self, fake_requests):
        # Plain 403 (e.g. user has read-only role) is NOT a default-
        # password signal. Don't false-positive.
        responses, _ = fake_requests
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        responses.append(_FakeResp(401))
        responses.append(_FakeResp(403, {
            "MessageId": "Base.1.0.0.InsufficientPrivilege",
        }))

        r = discovery_service._probe_bluefield_bmc(
            "10.0.0.32", operator_auth=None,
        )
        assert r["uses_default_password"] is False
        assert "PasswordChangeRequired" not in r["redfish"]


class TestDiagnosticCapture:
    """Job #3 surfaced two same-model DPUs whose BMC views differed
    drastically — the legacy BIOS Attributes view was empty on the
    newer bf-bundle BSP. Capturing the OEM/Nvidia paths and the
    per-component firmware versions lets the operator see *why* two
    BMCs that look identical at the chip level expose different data.
    """

    def test_uuid_and_boot_progress_pulled_from_systems(self, fake_requests):
        responses, _ = fake_requests
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        responses.append(_FakeResp(200, {
            "SerialNumber": "MT-X",
            "UUID": "00000000-0000-0000-0000-000000000000",
            "BootProgress": {
                "LastState": "OEM",
                "LastStateTime": "1970-01-01T00:00:00.000000+00:00",
                "OemLastState": "OsIsRunning",
            },
        }))
        # Managers, Bios, Oem-Nvidia, ExternalHostPrivileges, 7 FW
        # components — all default-404.
        r = discovery_service._probe_bluefield_bmc("10.0.0.40")
        rf = r["redfish"]
        assert rf["UUID"] == "00000000-0000-0000-0000-000000000000"
        assert rf["BootProgress"]["LastStateTime"] == "1970-01-01T00:00:00.000000+00:00"

    def test_oem_nvidia_and_external_host_privileges(self, fake_requests):
        responses, _ = fake_requests
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        responses.append(_FakeResp(200, {"SerialNumber": "MT-OEM"}))
        responses.append(_FakeResp(404))  # Managers
        responses.append(_FakeResp(404))  # Bios
        responses.append(_FakeResp(200, {
            "BaseGUID": "5c25730300e7905e",
            "BaseMAC": "5c2573e7905e",
            "HostRshim": "Enabled",
        }))
        responses.append(_FakeResp(200, {
            "ExternalHostPrivilege": {
                "HostPrivFlashAccess": "Default",
                "HostPrivFwUpdate": "Default",
                "HostPrivNicReset": "Default",
            },
        }))
        # 7 FW components default-404.

        r = discovery_service._probe_bluefield_bmc("10.0.0.41")
        rf = r["redfish"]
        assert rf["BaseGUID"] == "5c25730300e7905e"
        assert rf["BaseMAC"] == "5c2573e7905e"
        assert rf["HostRshim"] == "Enabled"
        assert rf["ExternalHostPrivilege"]["HostPrivFlashAccess"] == "Default"

    def test_firmware_inventory_versions(self, fake_requests):
        responses, _ = fake_requests
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        responses.append(_FakeResp(200, {"SerialNumber": "MT-FW"}))
        responses.append(_FakeResp(404))  # Managers
        responses.append(_FakeResp(404))  # Bios
        responses.append(_FakeResp(404))  # Oem/Nvidia
        responses.append(_FakeResp(404))  # ExternalHostPrivileges
        # 7 FW components in the fixed order in _probe_bluefield_bmc.
        responses.append(_FakeResp(200, {"Version": "4.12.0-19-gdd4c9d8a10"}))  # DPU_UEFI
        responses.append(_FakeResp(200, {"Version": "v2.2(release):4.12.0-27"}))  # DPU_ATF
        responses.append(_FakeResp(200, {"Version": "4.12.0.13720"}))  # DPU_BSP
        responses.append(_FakeResp(200, {"Version": "32.46.1006"}))  # DPU_NIC
        responses.append(_FakeResp(200, {"Version": "bf-bundle-3.1.0-76"}))  # DPU_OS
        responses.append(_FakeResp(200, {"Version": "MLNX_OFED_25.07"}))  # DPU_OFED
        responses.append(_FakeResp(404))  # DPU_NODE absent on this build

        r = discovery_service._probe_bluefield_bmc("10.0.0.42")
        fw = r["redfish"].get("FirmwareInventory")
        assert fw is not None
        assert fw["DPU_UEFI"] == "4.12.0-19-gdd4c9d8a10"
        assert fw["DPU_BSP"] == "4.12.0.13720"
        assert fw["DPU_OS"] == "bf-bundle-3.1.0-76"
        assert fw["DPU_OFED"] == "MLNX_OFED_25.07"
        # Missing component is silently omitted, not a None entry.
        assert "DPU_NODE" not in fw

    def test_firmware_inventory_omitted_when_all_absent(self, fake_requests):
        # Older BMCs may not expose FirmwareInventory at all — ensure
        # we don't write an empty dict in that case.
        responses, _ = fake_requests
        responses.append(_FakeResp(200, {"Product": "BlueField-3 DPU"}))
        # Everything else falls through to default-404.

        r = discovery_service._probe_bluefield_bmc("10.0.0.43")
        assert "FirmwareInventory" not in r["redfish"]
