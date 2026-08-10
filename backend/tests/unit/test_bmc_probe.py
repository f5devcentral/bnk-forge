"""Unit tests for bmc_probe — mocked clients."""

from unittest.mock import MagicMock

import pytest

from services.bare_metal.discovery.bmc_probe import BmcProbeResult, probe_bmc
from services.bare_metal.ipmi_client import IPMIClient, IPMIResult
from services.bare_metal.redfish.client import RedfishClient, RedfishSystemInfo


@pytest.mark.unit
class TestBmcProbe:
    def test_no_clients(self):
        result = probe_bmc("10.0.0.1")
        assert result.redfish_reachable is False
        assert result.ipmi_reachable is False

    def test_redfish_reachable(self):
        redfish = MagicMock(spec=RedfishClient)
        redfish.is_reachable.return_value = True
        redfish.detect_vendor.return_value = "supermicro"
        redfish.get_system_info.return_value = RedfishSystemInfo(
            manufacturer="Supermicro",
            model="SYS-421GE",
            serial_number="SN123",
            power_state="On",
            bios_version="3.0",
        )
        redfish.get_bios_attributes.return_value = {"InternalCpuModel": "0"}
        redfish.vendor_plugin = None

        result = probe_bmc("10.0.0.1", redfish=redfish)
        assert result.redfish_reachable is True
        assert result.vendor == "supermicro"
        assert result.power_state == "On"
        assert result.system_manufacturer == "Supermicro"

    def test_redfish_unreachable(self):
        redfish = MagicMock(spec=RedfishClient)
        redfish.is_reachable.return_value = False

        result = probe_bmc("10.0.0.1", redfish=redfish)
        assert result.redfish_reachable is False

    def test_ipmi_reachable(self):
        ipmi = MagicMock(spec=IPMIClient)
        ipmi.power_status.return_value = IPMIResult(
            exit_code=0, stdout="Chassis Power is on", stderr="", success=True
        )

        result = probe_bmc("10.0.0.1", ipmi=ipmi)
        assert result.ipmi_reachable is True
        assert result.power_state == "On"

    def test_ipmi_power_off(self):
        ipmi = MagicMock(spec=IPMIClient)
        ipmi.power_status.return_value = IPMIResult(
            exit_code=0, stdout="Chassis Power is off", stderr="", success=True
        )

        result = probe_bmc("10.0.0.1", ipmi=ipmi)
        assert result.power_state == "Off"

    def test_both_clients(self):
        redfish = MagicMock(spec=RedfishClient)
        redfish.is_reachable.return_value = True
        redfish.detect_vendor.return_value = "supermicro"
        redfish.get_system_info.return_value = RedfishSystemInfo(
            manufacturer="Supermicro",
            model="SYS",
            serial_number="S",
            power_state="On",
            bios_version="3.0",
        )
        redfish.get_bios_attributes.return_value = {}
        redfish.vendor_plugin = None

        ipmi = MagicMock(spec=IPMIClient)
        ipmi.power_status.return_value = IPMIResult(
            exit_code=0, stdout="Chassis Power is on", stderr="", success=True
        )

        result = probe_bmc("10.0.0.1", redfish=redfish, ipmi=ipmi)
        assert result.redfish_reachable is True
        assert result.ipmi_reachable is True
        # Redfish data takes precedence
        assert result.power_state == "On"
