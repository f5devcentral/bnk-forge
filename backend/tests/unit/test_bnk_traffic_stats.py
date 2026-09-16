"""
Unit tests for services.bnk.traffic_stats — TMM traffic stat mapping.

Tests the pure ``analyze_traffic_stats`` function with constructed TMM
output.  No mocking / no DB / no Kubernetes access.
"""

import pytest

from services.bnk.traffic_stats import (
    _build_configview_index,
    _build_egress_index,
    _build_listener_index,
    _fetch_configview_mappings,
    _match_virtual_server_row,
    _parse_configview_uuid_output,
    _pick_tmm_pod,
    analyze_traffic_stats,
    fetch_tmm_traffic_stats,
)

# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------


def _topology() -> list[dict]:
    return [
        {
            "name": "gw-prod",
            "namespace": "f5-bnk",
            "gatewayClassName": "f5-bnk",
            "addresses": ["10.0.0.1"],
            "listeners": [
                {"name": "http", "protocol": "HTTP", "port": 80},
                {"name": "https", "protocol": "HTTPS", "port": 443},
            ],
            "securityPolicies": [],
        },
    ]


def _egresses() -> list[dict]:
    return [
        {
            "name": "egress-demo",
            "namespace": "f5-bnk",
            "snatType": "auto",
            "egressSnatpool": None,
            "firewallEnforcedPolicy": None,
            "logProfile": None,
            "capturedNamespaces": ["app"],
            "vxlan": None,
            "ready": True,
        },
    ]


def _data(topology=None, egresses=None) -> dict:
    return {
        "topology": topology or _topology(),
        "dataPlane": {"egresses": egresses or _egresses()},
        "resources": {
            "f5bigfwpolicy": [
                {
                    "metadata": {"name": "fw-deny", "namespace": "f5-bnk"},
                    "spec": {
                        "rule": [
                            {"name": "deny-ssh", "action": "drop", "ipProtocol": "tcp", "logging": False},
                        ],
                    },
                },
            ],
        },
    }


def _raw_stats(
    vs_rows=None,
    fw_rows=None,
    configview_mappings=None,
    error=None,
) -> dict:
    return {
        "source": "tmctl",
        "podName": "f5-tmm-abc123",
        "namespace": "f5-bnk",
        "virtualServerStat": {
            "columns": ["name", "clientside.bytes_in", "clientside.bytes_out",
                        "clientside.cur_conns", "clientside.tot_conns"],
            "rows": vs_rows or [],
            "exit_code": 0,
        },
        "fwRuleStat": {
            "columns": ["name", "hit_count", "action"],
            "rows": fw_rows or [],
            "exit_code": 0,
        },
        "configviewMappings": configview_mappings or [],
        "error": error,
    }


# ---------------------------------------------------------------------------
# analyze_traffic_stats
# ---------------------------------------------------------------------------


class TestAnalyzeTrafficStats:
    def test_returns_empty_envelope_when_no_raw_stats(self):
        result = analyze_traffic_stats(_data(), raw_stats=None)

        assert result["available"] is False
        assert result["listeners"] == []
        assert result["egresses"] == []
        assert result["firewallRules"] == []

    def test_returns_empty_envelope_on_tmm_error(self):
        raw = _raw_stats(error="debug sidecar unreachable")
        result = analyze_traffic_stats(_data(), raw)

        assert result["available"] is False
        assert result["error"] == "debug sidecar unreachable"

    def test_maps_virtual_server_stat_to_listener(self):
        raw = _raw_stats(vs_rows=[
            ["gw-prod_http", "1024", "2048", "5", "100"],
        ])
        result = analyze_traffic_stats(_data(), raw)

        assert result["available"] is True
        assert len(result["listeners"]) == 1
        listener = result["listeners"][0]
        assert listener["gatewayName"] == "gw-prod"
        assert listener["listenerName"] == "http"
        assert listener["clientsideTotConns"] == 100
        assert listener["clientsideCurConns"] == 5

    def test_sums_virtual_server_stat_for_same_listener(self):
        raw = _raw_stats(vs_rows=[
            ["gw-prod_http", "1024", "2048", "1", "10"],
            ["gw-prod_http", "100", "200", "2", "20"],
        ])
        result = analyze_traffic_stats(_data(), raw)

        assert len(result["listeners"]) == 1
        assert result["listeners"][0]["clientsideTotConns"] == 30

    def test_maps_virtual_server_stat_to_egress(self):
        raw = _raw_stats(vs_rows=[
            ["egress-demo", "512", "256", "1", "42"],
        ])
        result = analyze_traffic_stats(_data(), raw)

        assert len(result["egresses"]) == 1
        egress = result["egresses"][0]
        assert egress["egressName"] == "egress-demo"
        assert egress["clientsideTotConns"] == 42

    def test_maps_firewall_rule_hits(self):
        raw = _raw_stats(fw_rows=[
            ["fw-deny_deny-ssh", "7", "drop"],
        ])
        result = analyze_traffic_stats(_data(), raw)

        assert len(result["firewallRules"]) == 1
        rule = result["firewallRules"][0]
        assert rule["policyName"] == "fw-deny"
        assert rule["ruleName"] == "deny-ssh"
        assert rule["hitCount"] == 7

    def test_keeps_unmatched_firewall_rule_for_observability(self):
        raw = _raw_stats(fw_rows=[
            ["some-unknown-rule", "3", "accept"],
        ])
        result = analyze_traffic_stats(_data(), raw)

        assert len(result["firewallRules"]) == 1
        assert result["firewallRules"][0]["ruleName"] == "some-unknown-rule"
        assert result["firewallRules"][0]["hitCount"] == 3

    def test_configview_hints_override_name_matching(self):
        raw = _raw_stats(
            vs_rows=[
                ["vs-custom-name", "100", "200", "1", "10"],
            ],
            configview_mappings=[{
                "uuid": "uuid-1",
                "virtual_server_name": "vs-custom-name",
                "gateway_name": "gw-prod",
                "listener_name": "https",
                "namespace": "f5-bnk",
            }],
        )
        result = analyze_traffic_stats(_data(), raw)

        assert len(result["listeners"]) == 1
        assert result["listeners"][0]["listenerName"] == "https"


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------


class TestIndexBuilders:
    def test_build_listener_index(self):
        topology = _topology()
        index = _build_listener_index(topology)

        assert _normalize_key("gw-prod_http") in index
        assert index[_normalize_key("gw-prod_http")]["listenerName"] == "http"

    def test_build_egress_index(self):
        index = _build_egress_index(_egresses())

        assert _normalize_key("egress-demo") in index
        assert index[_normalize_key("egress-demo")]["egressName"] == "egress-demo"


# ---------------------------------------------------------------------------
# configview parsing
# ---------------------------------------------------------------------------


class TestConfigviewParsing:
    def test_parse_line_oriented_output(self):
        raw = """
        name: vs-custom-name
        gateway: gw-prod
        listener: https
        namespace: f5-bnk
        """
        hints = _parse_configview_uuid_output(raw)
        assert hints["virtual_server_name"] == "vs-custom-name"
        assert hints["gateway_name"] == "gw-prod"
        assert hints["listener_name"] == "https"

    def test_parse_json_output(self):
        raw = '{"name": "vs-json", "gateway": "gw-prod", "listener": "http"}'
        hints = _parse_configview_uuid_output(raw)
        assert hints["virtual_server_name"] == "vs-json"

    def test_build_configview_index(self):
        mappings = [
            {"uuid": "a", "virtual_server_name": "vs-one", "gateway_name": "gw"},
            {"uuid": "b", "virtual_server_name": "vs-two", "egress_name": "eg"},
        ]
        index = _build_configview_index(mappings)
        assert _normalize_key("vs-one") in index
        assert _normalize_key("vs-two") in index


# ---------------------------------------------------------------------------
# Virtual server row matching
# ---------------------------------------------------------------------------


class TestMatchVirtualServerRow:
    def test_matches_listener_by_name(self):
        listener_index = _build_listener_index(_topology())
        kind, matched = _match_virtual_server_row(
            {"name": "gw-prod_http"}, listener_index, {}, {},
        )
        assert kind == "listener"
        assert matched["listenerName"] == "http"

    def test_prefers_configview_hint(self):
        listener_index = _build_listener_index(_topology())
        configview_index = _build_configview_index([{
            "virtual_server_name": "gw-prod_http",
            "egress_name": "egress-demo",
            "namespace": "f5-bnk",
        }])
        kind, matched = _match_virtual_server_row(
            {"name": "gw-prod_http"}, listener_index, {}, configview_index,
        )
        assert kind == "egress"
        assert matched["egressName"] == "egress-demo"


# ---------------------------------------------------------------------------
# TMM pod selection
# ---------------------------------------------------------------------------


class TestPickTmmPod:
    def test_picks_running_pod_with_debug_container(self):
        pods = [
            {"name": "f5-tmm-a", "namespace": "f5-bnk", "phase": "Running",
             "containers": [{"name": "tmm"}, {"name": "debug"}]},
            {"name": "f5-tmm-b", "namespace": "f5-bnk", "phase": "Pending",
             "containers": [{"name": "tmm"}, {"name": "debug"}]},
        ]
        assert _pick_tmm_pod(pods) == pods[0]

    def test_skips_pod_without_debug_container(self):
        pods = [
            {"name": "f5-tmm-a", "namespace": "f5-bnk", "phase": "Running",
             "containers": [{"name": "tmm"}]},
        ]
        assert _pick_tmm_pod(pods) is None


# ---------------------------------------------------------------------------
# fetch_tmm_traffic_stats — thin wrapper around exec helpers
# ---------------------------------------------------------------------------


class TestFetchTmmTrafficStats:
    def test_returns_error_when_no_tmm_pods(self):
        result = fetch_tmm_traffic_stats(None, {"tmm": []})  # type: ignore[arg-type]
        assert result["error"] == "No TMM pods with debug sidecar found"
        assert result["podName"] is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_key(name: str) -> str:
    """Use the module's normalization logic directly."""
    from services.bnk.traffic_stats import _normalize_name
    return _normalize_name(name)
