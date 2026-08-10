"""Unit tests for RedfishClient — mocked HTTP."""

from unittest.mock import MagicMock, patch

import pytest

from services.bare_metal.redfish.client import RedfishClient, RedfishSystemInfo


@pytest.fixture
def mock_session():
    """Create a RedfishClient with mocked requests.Session."""
    with patch("services.bare_metal.redfish.client.requests.Session") as MockSession:
        session_instance = MagicMock()
        MockSession.return_value = session_instance
        client = RedfishClient("10.0.0.1", "admin", "password")
        yield client, session_instance


@pytest.mark.unit
class TestRedfishClientURLBuilding:
    def test_base_url(self):
        with patch("services.bare_metal.redfish.client.requests.Session"):
            client = RedfishClient("10.0.0.1", "admin", "pass")
        assert client.base_url == "https://10.0.0.1"

    def test_url_building(self):
        with patch("services.bare_metal.redfish.client.requests.Session"):
            client = RedfishClient("10.0.0.1", "admin", "pass")
        assert client._url("/redfish/v1") == "https://10.0.0.1/redfish/v1"

    def test_url_absolute(self):
        with patch("services.bare_metal.redfish.client.requests.Session"):
            client = RedfishClient("10.0.0.1", "admin", "pass")
        assert client._url("https://other.host/path") == "https://other.host/path"


@pytest.mark.unit
class TestRedfishClientReachability:
    def test_is_reachable_true(self, mock_session):
        client, session = mock_session
        resp = MagicMock()
        resp.json.return_value = {"@odata.id": "/redfish/v1"}
        resp.raise_for_status.return_value = None
        session.get.return_value = resp

        assert client.is_reachable() is True

    def test_is_reachable_false(self, mock_session):
        client, session = mock_session
        session.get.side_effect = Exception("Connection refused")

        assert client.is_reachable() is False


@pytest.mark.unit
class TestRedfishClientSystemInfo:
    def test_get_system_info(self, mock_session):
        client, session = mock_session

        systems_resp = MagicMock()
        systems_resp.json.return_value = {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]}
        systems_resp.raise_for_status.return_value = None

        system_resp = MagicMock()
        system_resp.json.return_value = {
            "Manufacturer": "Supermicro",
            "Model": "SYS-421GE",
            "SerialNumber": "SN12345",
            "PowerState": "On",
            "BiosVersion": "3.0",
        }
        system_resp.raise_for_status.return_value = None

        session.get.side_effect = [systems_resp, system_resp]

        info = client.get_system_info()
        assert info is not None
        assert info.manufacturer == "Supermicro"
        assert info.power_state == "On"

    def test_get_system_info_no_members(self, mock_session):
        client, session = mock_session
        resp = MagicMock()
        resp.json.return_value = {"Members": []}
        resp.raise_for_status.return_value = None
        session.get.return_value = resp

        info = client.get_system_info()
        assert info is None


@pytest.mark.unit
class TestRedfishClientPower:
    def test_graceful_shutdown(self, mock_session):
        client, session = mock_session

        # Mock system to get reset action path
        systems_resp = MagicMock()
        systems_resp.json.return_value = {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]}
        systems_resp.raise_for_status.return_value = None

        system_resp = MagicMock()
        system_resp.json.return_value = {
            "Actions": {
                "#ComputerSystem.Reset": {"target": "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"}
            }
        }
        system_resp.raise_for_status.return_value = None

        post_resp = MagicMock()
        post_resp.json.return_value = {}
        post_resp.content = b""
        post_resp.raise_for_status.return_value = None

        session.get.side_effect = [systems_resp, system_resp]
        session.post.return_value = post_resp

        assert client.graceful_shutdown() is True
        session.post.assert_called_once()
        call_args = session.post.call_args
        assert "GracefulShutdown" in str(call_args)

    def test_power_action_no_reset_path(self, mock_session):
        client, session = mock_session
        session.get.side_effect = Exception("Failed")
        assert client.graceful_shutdown() is False


@pytest.mark.unit
class TestRedfishClientBios:
    def test_get_bios_attributes(self, mock_session):
        client, session = mock_session

        systems_resp = MagicMock()
        systems_resp.json.return_value = {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]}
        systems_resp.raise_for_status.return_value = None

        bios_resp = MagicMock()
        bios_resp.json.return_value = {
            "Attributes": {
                "InternalCpuModel": "0",
                "BootMode": "UEFI",
            }
        }
        bios_resp.raise_for_status.return_value = None

        session.get.side_effect = [systems_resp, bios_resp]

        attrs = client.get_bios_attributes()
        assert "InternalCpuModel" in attrs
        assert attrs["InternalCpuModel"] == "0"

    def test_set_bios_attribute(self, mock_session):
        client, session = mock_session

        systems_resp = MagicMock()
        systems_resp.json.return_value = {"Members": [{"@odata.id": "/redfish/v1/Systems/1"}]}
        systems_resp.raise_for_status.return_value = None

        patch_resp = MagicMock()
        patch_resp.json.return_value = {}
        patch_resp.content = b""
        patch_resp.raise_for_status.return_value = None

        session.get.return_value = systems_resp
        session.patch.return_value = patch_resp

        result = client.set_bios_attribute("InternalCpuModel", "1")
        assert result is True
        session.patch.assert_called_once()
