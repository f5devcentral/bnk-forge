"""Unit tests for IPMIClient — mocked subprocess."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from services.bare_metal.ipmi_client import IPMIClient, IPMIResult


@pytest.mark.unit
class TestIPMIClientArgBuilding:
    def test_base_args(self):
        client = IPMIClient("10.0.0.1", "admin", "password")
        args = client._build_base_args()
        assert args[0] == "ipmitool"
        assert "-I" in args
        assert "lanplus" in args
        assert "-H" in args
        assert "10.0.0.1" in args
        assert "-U" in args
        assert "admin" in args


@pytest.mark.unit
class TestIPMIClientPower:
    @patch("services.bare_metal.ipmi_client.subprocess.run")
    def test_power_status(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Chassis Power is on\n", stderr="")
        client = IPMIClient("10.0.0.1", "admin", "password")
        result = client.power_status()
        assert result.success is True
        assert "on" in result.stdout.lower()

    @patch("services.bare_metal.ipmi_client.subprocess.run")
    def test_power_cycle(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Chassis Power Control: Cycle\n", stderr="")
        client = IPMIClient("10.0.0.1", "admin", "password")
        result = client.power_cycle()
        assert result.success is True

    @patch("services.bare_metal.ipmi_client.subprocess.run")
    def test_is_reachable(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Chassis Power is on\n", stderr="")
        client = IPMIClient("10.0.0.1", "admin", "password")
        assert client.is_reachable() is True

    @patch("services.bare_metal.ipmi_client.subprocess.run")
    def test_timeout_handled(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ipmitool", timeout=30)
        client = IPMIClient("10.0.0.1", "admin", "password")
        result = client.power_status()
        assert result.success is False
        assert "timed out" in result.stderr

    @patch("services.bare_metal.ipmi_client.subprocess.run")
    def test_ipmitool_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        client = IPMIClient("10.0.0.1", "admin", "password")
        result = client.power_status()
        assert result.success is False
        assert "not found" in result.stderr


@pytest.mark.unit
class TestIPMIClientAllPowerActions:
    @patch("services.bare_metal.ipmi_client.subprocess.run")
    def test_power_on(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Power on", stderr="")
        result = IPMIClient("10.0.0.1", "admin", "pass").power_on()
        assert result.success is True
        # Verify correct subcommand
        cmd = mock_run.call_args[0][0]
        assert "power" in cmd and "on" in cmd

    @patch("services.bare_metal.ipmi_client.subprocess.run")
    def test_power_off(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = IPMIClient("10.0.0.1", "admin", "pass").power_off()
        assert result.success is True

    @patch("services.bare_metal.ipmi_client.subprocess.run")
    def test_power_soft(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = IPMIClient("10.0.0.1", "admin", "pass").power_soft()
        assert result.success is True

    @patch("services.bare_metal.ipmi_client.subprocess.run")
    def test_power_reset(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = IPMIClient("10.0.0.1", "admin", "pass").power_reset()
        assert result.success is True
