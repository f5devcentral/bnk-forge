"""
Unit tests for services.bnk.a2a_discovery — A2A agent discovery.

Tests the pure analysis logic that finds A2A agent candidates from
BNK topology data. Reuses build_route_ref_map from helpers (already tested
in test_bnk_backends.py).
"""

import pytest

from services.bnk.a2a_discovery import (
    _find_http_backend_services,
    _normalize_agent_card,
    _pick_probe_port,
    discover_a2a_agents,
)
from services.bnk.helpers import build_route_ref_map

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------


def _service(name: str, namespace: str = "default", ports=None) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "type": "ClusterIP",
            "clusterIP": "10.96.0.1",
            "ports": ports or [{"port": 8080, "name": "http", "protocol": "TCP"}],
        },
    }


def _topology_http(backend_name: str, backend_ns: str = "default", gw_name: str = "gw-prod") -> list[dict]:
    """Topology with a single HTTPRoute pointing to a backend."""
    return [{
        "name": gw_name,
        "namespace": "default",
        "listeners": [{
            "name": "http",
            "routes": [{
                "name": "agent-route",
                "namespace": "default",
                "kind": "HTTPRoute",
                "backends": [{"name": backend_name, "namespace": backend_ns, "port": 8080, "weight": None}],
            }],
        }],
    }]


def _topology_tcp(backend_name: str, backend_ns: str = "default") -> list[dict]:
    """Topology with only a TCPRoute (no HTTP)."""
    return [{
        "name": "gw-tcp",
        "namespace": "default",
        "listeners": [{
            "name": "tcp",
            "routes": [{
                "name": "tcp-route",
                "namespace": "default",
                "kind": "TCPRoute",
                "backends": [{"name": backend_name, "namespace": backend_ns, "port": 9090}],
            }],
        }],
    }]


def _data_with_services(*services) -> dict:
    return {"resources": {"service": list(services)}}


# ---------------------------------------------------------------------------
# discover_a2a_agents (integration of pure analysis)
# ---------------------------------------------------------------------------


class TestDiscoverA2AAgents:
    def test_finds_http_backend(self):
        data = _data_with_services(_service("weather-agent"))
        topology = _topology_http("weather-agent")
        agents = discover_a2a_agents(data, topology)
        assert len(agents) == 1
        assert agents[0]["name"] == "weather-agent"
        assert agents[0]["probeStatus"] == "pending"
        assert agents[0]["agentCard"] is None
        assert "gw-prod" in agents[0]["gateways"]

    def test_skips_tcp_only_backend(self):
        data = _data_with_services(_service("db-service"))
        topology = _topology_tcp("db-service")
        agents = discover_a2a_agents(data, topology)
        assert len(agents) == 0

    def test_skips_unmapped_services(self):
        data = _data_with_services(_service("orphan-svc"))
        topology = _topology_http("other-svc")
        agents = discover_a2a_agents(data, topology)
        assert len(agents) == 0

    def test_empty_topology(self):
        data = _data_with_services(_service("svc-1"))
        agents = discover_a2a_agents(data, [])
        assert agents == []

    def test_empty_services(self):
        data = _data_with_services()
        topology = _topology_http("svc-1")
        agents = discover_a2a_agents(data, topology)
        assert agents == []

    def test_multiple_gateways(self):
        """Service behind two different gateways."""
        topology = [
            *_topology_http("multi-gw-svc", gw_name="gw-a"),
            *_topology_http("multi-gw-svc", gw_name="gw-b"),
        ]
        data = _data_with_services(_service("multi-gw-svc"))
        agents = discover_a2a_agents(data, topology)
        assert len(agents) == 1
        assert set(agents[0]["gateways"]) == {"gw-a", "gw-b"}

    def test_sorted_by_namespace_name(self):
        topology = [
            *_topology_http("z-agent"),
            *_topology_http("a-agent"),
        ]
        data = _data_with_services(_service("z-agent"), _service("a-agent"))
        agents = discover_a2a_agents(data, topology)
        assert [a["name"] for a in agents] == ["a-agent", "z-agent"]


# ---------------------------------------------------------------------------
# _find_http_backend_services
# ---------------------------------------------------------------------------


class TestFindHttpBackendServices:
    def test_mixed_http_and_tcp(self):
        """Only HTTPRoute backends are included, not TCP."""
        topology = [{
            "name": "gw",
            "namespace": "ns",
            "listeners": [{
                "name": "mixed",
                "routes": [
                    {"name": "http-r", "namespace": "ns", "kind": "HTTPRoute",
                     "backends": [{"name": "http-svc", "namespace": "ns", "port": 80}]},
                    {"name": "tcp-r", "namespace": "ns", "kind": "TCPRoute",
                     "backends": [{"name": "tcp-svc", "namespace": "ns", "port": 9090}]},
                ],
            }],
        }]
        services = [_service("http-svc", "ns"), _service("tcp-svc", "ns")]
        route_ref_map = build_route_ref_map(topology)
        candidates = _find_http_backend_services(services, route_ref_map)
        assert len(candidates) == 1
        assert candidates[0]["name"] == "http-svc"

    def test_route_refs_populated(self):
        topology = _topology_http("my-svc")
        services = [_service("my-svc")]
        route_ref_map = build_route_ref_map(topology)
        candidates = _find_http_backend_services(services, route_ref_map)
        assert len(candidates) == 1
        refs = candidates[0]["routeRefs"]
        assert len(refs) == 1
        assert refs[0]["name"] == "agent-route"
        assert refs[0]["gatewayName"] == "gw-prod"


# ---------------------------------------------------------------------------
# _pick_probe_port
# ---------------------------------------------------------------------------


class TestPickProbePort:
    def test_prefers_http_named_port(self):
        ports = [
            {"port": 9090, "name": "metrics", "protocol": "TCP"},
            {"port": 8080, "name": "http", "protocol": "TCP"},
        ]
        assert _pick_probe_port(ports) == 8080

    def test_prefers_a2a_named_port(self):
        ports = [
            {"port": 9090, "name": "grpc", "protocol": "TCP"},
            {"port": 10001, "name": "a2a", "protocol": "TCP"},
        ]
        assert _pick_probe_port(ports) == 10001

    def test_falls_back_to_first(self):
        ports = [{"port": 3000, "name": "custom", "protocol": "TCP"}]
        assert _pick_probe_port(ports) == 3000

    def test_empty_ports_returns_none(self):
        assert _pick_probe_port([]) is None

    def test_none_name_handled(self):
        ports = [{"port": 80, "name": None, "protocol": "TCP"}]
        assert _pick_probe_port(ports) == 80


# ---------------------------------------------------------------------------
# _normalize_agent_card
# ---------------------------------------------------------------------------


class TestNormalizeAgentCard:
    def test_full_card(self):
        card = {
            "name": "Weather Agent",
            "description": "Get weather forecasts",
            "version": "1.0.0",
            "url": "http://weather:8080",
            "capabilities": {"streaming": True, "pushNotifications": False},
            "skills": [{"id": "forecast", "name": "Forecast", "description": "5-day forecast"}],
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["application/json"],
            "provider": {"organization": "ACME"},
            "securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}},
            "iconUrl": "https://example.com/icon.png",
        }
        result = _normalize_agent_card(card)
        assert result["name"] == "Weather Agent"
        assert result["capabilities"]["streaming"] is True
        assert len(result["skills"]) == 1
        assert result["iconUrl"] == "https://example.com/icon.png"

    def test_minimal_card(self):
        card = {"name": "Minimal"}
        result = _normalize_agent_card(card)
        assert result["name"] == "Minimal"
        assert result["skills"] == []
        assert result["capabilities"] == {}
        assert result["iconUrl"] is None

    def test_non_dict_returns_none(self):
        assert _normalize_agent_card("not a dict") is None
        assert _normalize_agent_card(None) is None
        assert _normalize_agent_card(42) is None
