"""Unit tests for the connectivity-phase helpers in services.discovery_service."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.discovery_service import (
    _collect_connectivity_endpoints,
    _parse_ping_output,
    _run_connectivity_for_source,
)
from services.shared.hardware_probes import (
    classify_interface as _classify_interface,
)
from services.shared.hardware_probes import (
    normalize_pci_base as _normalize_pci_base,
)


class TestNormalizePci:
    def test_full_form(self):
        assert _normalize_pci_base("0000:01:00.0") == "01:00"

    def test_short_form(self):
        assert _normalize_pci_base("01:00.1") == "01:00"

    def test_none(self):
        assert _normalize_pci_base(None) is None

    def test_empty(self):
        assert _normalize_pci_base("") is None


class TestClassifyInterface:
    def test_vf_excluded(self):
        assert _classify_interface(
            name="enp1s0f0v0", pci="0000:01:00.2", is_vf=True,
            operstate="UP", link_type="ether", dpu_pci_bases=set(),
        ) is None

    def test_loopback_excluded(self):
        assert _classify_interface(
            name="lo", pci=None, is_vf=False,
            operstate="UNKNOWN", link_type="loopback", dpu_pci_bases=set(),
        ) is None

    def test_docker_marked_virtual(self):
        assert _classify_interface(
            name="docker0", pci=None, is_vf=False,
            operstate="UP", link_type="ether", dpu_pci_bases=set(),
        ) == "virtual"

    def test_no_pci_is_virtual(self):
        assert _classify_interface(
            name="bond0", pci=None, is_vf=False,
            operstate="UP", link_type="ether", dpu_pci_bases=set(),
        ) == "virtual"

    def test_pci_match_dpu_is_bluefield(self):
        assert _classify_interface(
            name="enp1s0f0np0", pci="0000:01:00.0", is_vf=False,
            operstate="UP", link_type="ether", dpu_pci_bases={"01:00"},
        ) == "bluefield"

    def test_other_pci_is_builtin(self):
        assert _classify_interface(
            name="eno1", pci="0000:00:1f.6", is_vf=False,
            operstate="UP", link_type="ether", dpu_pci_bases={"01:00"},
        ) == "builtin"

    def test_non_ether_link_is_virtual(self):
        assert _classify_interface(
            name="ip6tnl0", pci="0000:00:1f.6", is_vf=False,
            operstate="DOWN", link_type="tunnel6", dpu_pci_bases=set(),
        ) == "virtual"


class TestParsePingOutput:
    def test_success_with_rtt(self):
        out = (
            "PING 10.0.0.2 (10.0.0.2) 56(84) bytes of data.\n"
            "64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.418 ms\n"
            "64 bytes from 10.0.0.2: icmp_seq=2 ttl=64 time=0.392 ms\n"
            "\n--- 10.0.0.2 ping statistics ---\n"
            "2 packets transmitted, 2 received, 0% packet loss, time 1015ms\n"
            "rtt min/avg/max/mdev = 0.392/0.405/0.418/0.013 ms\n"
        )
        result = _parse_ping_output(out, 0)
        assert result["reachable"] is True
        # Reports `min` (first capture group) — see `_parse_ping_output`
        # for the ARP-outlier rationale.
        assert result["rtt_ms"] == pytest.approx(0.392)
        assert result["error"] is None

    def test_success_without_rtt_block(self):
        result = _parse_ping_output("ok", 0)
        assert result["reachable"] is True
        assert result["rtt_ms"] is None

    def test_failure_unreachable(self):
        out = (
            "PING 10.0.0.99 (10.0.0.99) 56(84) bytes of data.\n"
            "From 10.0.0.1 icmp_seq=1 Destination Host Unreachable\n"
            "\n--- 10.0.0.99 ping statistics ---\n"
            "2 packets transmitted, 0 received, +1 errors, 100% packet loss, time 1023ms\n"
        )
        result = _parse_ping_output(out, 1)
        assert result["reachable"] is False
        assert result["rtt_ms"] is None
        assert "Destination Host Unreachable" in result["error"]


def _make_node(node_id, ip, status, hostname=None, network_interfaces=None):
    return SimpleNamespace(
        id=node_id,
        ip_address=ip,
        status=status,
        hostname=hostname,
        network_interfaces=network_interfaces,
    )


class TestCollectConnectivityEndpoints:
    def test_groups_by_kind_and_skips_failed_nodes(self):
        node_a = _make_node(1, "10.0.0.1", "completed", "node-a", [
            {"name": "eno1", "kind": "builtin", "addresses": [
                {"family": "inet", "address": "10.0.0.1", "prefix": 24, "scope": "global"},
            ]},
            {"name": "enp1s0f0", "kind": "bluefield", "addresses": [
                {"family": "inet", "address": "192.168.100.1", "prefix": 24},
            ]},
            {"name": "docker0", "kind": "virtual", "addresses": [
                {"family": "inet", "address": "172.17.0.1", "prefix": 16},
            ]},
        ])
        node_b = _make_node(2, "10.0.0.2", "completed", "node-b", [
            {"name": "eno1", "kind": "builtin", "addresses": [
                {"family": "inet", "address": "10.0.0.2", "prefix": 24},
            ]},
        ])
        node_failed = _make_node(3, "10.0.0.3", "failed", "node-c", [
            {"name": "eno1", "kind": "builtin", "addresses": [
                {"family": "inet", "address": "10.0.0.3", "prefix": 24},
            ]},
        ])
        job = SimpleNamespace(nodes=[node_a, node_b, node_failed])

        groups = _collect_connectivity_endpoints(job)
        builtin = groups["builtin"]
        bluefield = groups["bluefield"]

        assert len(builtin) == 2
        assert {ep["node_id"] for ep in builtin} == {1, 2}
        assert len(bluefield) == 1
        assert bluefield[0]["interface"] == "enp1s0f0"

    def test_skips_link_local_ipv6(self):
        node = _make_node(1, "10.0.0.1", "completed", "node-a", [
            {"name": "eno1", "kind": "builtin", "addresses": [
                {"family": "inet6", "address": "fe80::aaaa", "prefix": 64},
            ]},
        ])
        job = SimpleNamespace(nodes=[node])
        groups = _collect_connectivity_endpoints(job)
        assert groups["builtin"] == []  # link-local stripped, no usable addresses

    def test_skips_unclassified_interfaces(self):
        node = _make_node(1, "10.0.0.1", "completed", "node-a", [
            {"name": "weird", "addresses": [
                {"family": "inet", "address": "10.0.0.1", "prefix": 24},
            ]},
        ])
        job = SimpleNamespace(nodes=[node])
        groups = _collect_connectivity_endpoints(job)
        assert all(eps == [] for eps in groups.values())


class TestRunConnectivityForSource:
    def test_pings_each_target_in_each_group(self):
        runnable_groups = {
            "builtin": [
                {"node_id": 1, "node_ip": "10.0.0.1", "hostname": "a", "interface": "eno1",
                 "addresses": [{"family": "inet", "address": "10.0.0.1", "prefix": 24}]},
                {"node_id": 2, "node_ip": "10.0.0.2", "hostname": "b", "interface": "eno1",
                 "addresses": [{"family": "inet", "address": "10.0.0.2", "prefix": 24}]},
            ],
        }

        creds = {
            "username": "ubuntu",
            "port": 22,
            "auth_type": "password",
            "password": "secret",
        }

        fake_client = MagicMock()
        with patch(
            "services.discovery_service._ssh_connect",
            return_value=fake_client,
        ), patch(
            "services.discovery_service._ssh_exec",
            return_value=(0, "PING 10.0.0.2 (10.0.0.2) 56(84) bytes of data.\nrtt min/avg/max/mdev = 0.1/0.2/0.3/0.0 ms\n"),
        ) as mock_exec:
            results = _run_connectivity_for_source(
                src_node_id=1,
                src_node_ip="10.0.0.1",
                creds=creds,
                jumphost_cred=None,
                runnable_groups=runnable_groups,
            )

        assert len(results["builtin"]) == 1
        row = results["builtin"][0]
        assert row["src_node_id"] == 1
        assert row["dst_node_id"] == 2
        assert row["src_iface"] == "eno1"
        assert row["dst_iface"] == "eno1"
        assert row["family"] == "inet"
        assert row["dst_address"] == "10.0.0.2"
        assert row["reachable"] is True
        # Parser reports `min` (first capture group of the rtt summary).
        assert row["rtt_ms"] == pytest.approx(0.1)

        # ping was issued with -I and the target address
        cmd = mock_exec.call_args[0][1]
        assert "-I eno1" in cmd
        assert "10.0.0.2" in cmd
        assert cmd.startswith("ping -c")
