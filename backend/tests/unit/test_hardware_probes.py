"""Unit tests for shared hardware probes."""

from unittest.mock import MagicMock

import pytest

from services.shared.hardware_probes import (
    classify_interface,
    detect_dpus,
    detect_network_interfaces,
    normalize_pci_base,
    ssh_exec,
)


@pytest.mark.unit
class TestNormalizePciBase:
    def test_full_address(self):
        assert normalize_pci_base("0000:01:00.0") == "01:00"

    def test_short_address(self):
        assert normalize_pci_base("01:00.0") == "01:00"

    def test_none(self):
        assert normalize_pci_base(None) is None

    def test_empty(self):
        assert normalize_pci_base("") is None


@pytest.mark.unit
class TestClassifyInterface:
    def test_vf_excluded(self):
        result = classify_interface(
            name="enp3s0f0v0", pci="03:00.2", is_vf=True,
            operstate="UP", link_type="ether", dpu_pci_bases=set()
        )
        assert result is None

    def test_builtin_nic(self):
        result = classify_interface(
            name="eno1", pci="01:00.0", is_vf=False,
            operstate="UP", link_type="ether", dpu_pci_bases=set()
        )
        assert result == "builtin"

    def test_bluefield_nic(self):
        result = classify_interface(
            name="enp3s0f0np0", pci="03:00.0", is_vf=False,
            operstate="UP", link_type="ether", dpu_pci_bases={"03:00"}
        )
        assert result == "bluefield"

    def test_virtual_docker(self):
        result = classify_interface(
            name="docker0", pci=None, is_vf=False,
            operstate="UP", link_type="ether", dpu_pci_bases=set()
        )
        assert result == "virtual"

    def test_loopback_excluded(self):
        result = classify_interface(
            name="lo", pci=None, is_vf=False,
            operstate="UNKNOWN", link_type="loopback", dpu_pci_bases=set()
        )
        assert result is None


@pytest.mark.unit
class TestSshExec:
    def test_returns_exit_code_and_output(self):
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b"hello world\n"
        mock_stdout.channel.recv_exit_status.return_value = 0
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

        rc, out = ssh_exec(mock_client, "echo hello world")
        assert rc == 0
        assert out == "hello world"


@pytest.mark.unit
class TestDetectDpus:
    def test_no_lspci(self):
        """No lspci -> no DPUs detected."""
        mock_client = MagicMock()
        mock_stdout = MagicMock()
        mock_stdout.read.return_value = b""
        mock_stdout.channel.recv_exit_status.return_value = 1
        mock_client.exec_command.return_value = (MagicMock(), mock_stdout, MagicMock())

        result = detect_dpus(mock_client, "x86_64", [])
        assert result["is_dpu_host"] is False
        assert result["dpu_count"] == 0
