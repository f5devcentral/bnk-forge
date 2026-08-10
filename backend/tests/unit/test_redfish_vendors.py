"""Unit tests for Redfish vendor plugins."""

import pytest

from services.bare_metal.redfish.dell import DellPlugin
from services.bare_metal.redfish.hpe import HpePlugin
from services.bare_metal.redfish.lenovo import LenovoPlugin
from services.bare_metal.redfish.supermicro import SupermicroPlugin


@pytest.mark.unit
class TestSuperMicroPlugin:
    def test_vendor_name(self):
        assert SupermicroPlugin().vendor_name == "supermicro"

    def test_parse_nic_mode_supernic(self):
        attrs = {"InternalCpuModel": "1"}
        assert SupermicroPlugin().parse_nic_mode(attrs) == "supernic"

    def test_parse_nic_mode_dpu(self):
        attrs = {"InternalCpuModel": "0"}
        assert SupermicroPlugin().parse_nic_mode(attrs) == "dpu"

    def test_parse_nic_mode_not_found(self):
        attrs = {"BootMode": "UEFI"}
        assert SupermicroPlugin().parse_nic_mode(attrs) is None

    def test_build_payload_supernic(self):
        payload = SupermicroPlugin().build_nic_mode_payload("supernic")
        assert payload == {"Attributes": {"InternalCpuModel": "1"}}

    def test_build_payload_dpu(self):
        payload = SupermicroPlugin().build_nic_mode_payload("dpu")
        assert payload == {"Attributes": {"InternalCpuModel": "0"}}

    def test_detect_vendor(self):
        root = {"Oem": {"Supermicro": {}}}
        assert SupermicroPlugin().detect_vendor(root) is True

    def test_detect_vendor_no_match(self):
        root = {"Oem": {"Dell": {}}}
        assert SupermicroPlugin().detect_vendor(root) is False


@pytest.mark.unit
class TestLenovoPlugin:
    def test_vendor_name(self):
        assert LenovoPlugin().vendor_name == "lenovo"

    def test_detect_vendor(self):
        root = {"Oem": {"Lenovo": {}}}
        assert LenovoPlugin().detect_vendor(root) is True


@pytest.mark.unit
class TestDellPlugin:
    def test_vendor_name(self):
        assert DellPlugin().vendor_name == "dell"

    def test_detect_vendor(self):
        root = {"Oem": {"Dell": {}}}
        assert DellPlugin().detect_vendor(root) is True

    def test_bios_path_uses_dell_format(self):
        assert "System.Embedded.1" in DellPlugin().get_nic_mode_attribute_path()


@pytest.mark.unit
class TestHpePlugin:
    def test_vendor_name(self):
        assert HpePlugin().vendor_name == "hpe"

    def test_detect_vendor_hpe(self):
        root = {"Oem": {"Hpe": {}}}
        assert HpePlugin().detect_vendor(root) is True
