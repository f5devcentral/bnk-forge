"""
Unit tests for services.bnk.backends — service/route cross-reference.

Tests the backend mapping logic that determines which K8s Services
are referenced by Gateway API routes and which are unmapped.
"""

import pytest

from services.bnk.backends import analyze_backends
from services.bnk.helpers import build_route_ref_map as _build_route_ref_map

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------


def _service(name: str, namespace: str = "f5-bnk", ports=None) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace, "creationTimestamp": "2026-01-01T00:00:00Z"},
        "spec": {
            "type": "ClusterIP",
            "clusterIP": "10.96.0.1",
            "ports": ports or [{"port": 8080, "targetPort": 8080, "protocol": "TCP", "name": "http"}],
            "selector": {"app": name},
        },
    }


def _topology_with_route(backend_name: str, backend_ns: str = "f5-bnk") -> list[dict]:
    return [{
        "name": "gw-prod",
        "namespace": "f5-bnk",
        "listeners": [{
            "name": "http",
            "routes": [{
                "name": "web-route",
                "namespace": "f5-bnk",
                "kind": "HTTPRoute",
                "backends": [{"name": backend_name, "namespace": backend_ns, "port": 8080, "weight": None}],
            }],
        }],
    }]


def _data_with_services(*services) -> dict:
    return {"resources": {"service": list(services)}}


# ---------------------------------------------------------------------------
# _build_route_ref_map
# ---------------------------------------------------------------------------


class TestBuildRouteRefMap:
    def test_empty_topology(self):
        assert _build_route_ref_map([]) == {}

    def test_single_backend(self):
        topology = _topology_with_route("svc-1")
        ref_map = _build_route_ref_map(topology)
        assert ("f5-bnk", "svc-1") in ref_map
        refs = ref_map[("f5-bnk", "svc-1")]
        assert len(refs) == 1
        assert refs[0]["gatewayName"] == "gw-prod"
        assert refs[0]["listenerName"] == "http"
        assert refs[0]["name"] == "web-route"

    def test_backend_namespace_falls_back_to_route_namespace(self):
        """When backend has no namespace, it uses the route's namespace."""
        topology = [{
            "name": "gw",
            "listeners": [{"name": "http", "routes": [{
                "name": "r1",
                "namespace": "app-ns",
                "kind": "HTTPRoute",
                "backends": [{"name": "svc", "namespace": None, "port": 80}],
            }]}],
        }]
        ref_map = _build_route_ref_map(topology)
        assert ("app-ns", "svc") in ref_map

    def test_multiple_routes_same_backend(self):
        topology = [{
            "name": "gw",
            "listeners": [{"name": "http", "routes": [
                {"name": "r1", "namespace": "ns", "kind": "HTTPRoute",
                 "backends": [{"name": "svc", "namespace": "ns", "port": 80}]},
                {"name": "r2", "namespace": "ns", "kind": "TCPRoute",
                 "backends": [{"name": "svc", "namespace": "ns", "port": 80}]},
            ]}],
        }]
        ref_map = _build_route_ref_map(topology)
        assert len(ref_map[("ns", "svc")]) == 2


# ---------------------------------------------------------------------------
# analyze_backends
# ---------------------------------------------------------------------------


class TestAnalyzeBackends:
    def test_mapped_service(self):
        data = _data_with_services(_service("svc-1"))
        topology = _topology_with_route("svc-1")

        result = analyze_backends(data, topology)
        assert len(result) == 1
        assert result[0]["name"] == "svc-1"
        assert result[0]["mapped"] is True
        assert len(result[0]["routeRefs"]) == 1

    def test_unmapped_service(self):
        data = _data_with_services(_service("orphan-svc"))
        topology = _topology_with_route("other-svc")

        result = analyze_backends(data, topology)
        assert len(result) == 1
        assert result[0]["name"] == "orphan-svc"
        assert result[0]["mapped"] is False
        assert result[0]["routeRefs"] == []

    def test_sorting_mapped_first(self):
        data = _data_with_services(
            _service("z-unmapped"),
            _service("a-mapped"),
        )
        topology = _topology_with_route("a-mapped")

        result = analyze_backends(data, topology)
        assert result[0]["name"] == "a-mapped"  # mapped first
        assert result[1]["name"] == "z-unmapped"  # unmapped second

    def test_null_ports_handled(self):
        """Services with null ports (ExternalName/headless) don't crash."""
        svc = _service("headless")
        svc["spec"]["ports"] = None

        result = analyze_backends(_data_with_services(svc), [])
        assert result[0]["ports"] == []

    def test_empty_topology_all_unmapped(self):
        data = _data_with_services(_service("svc-1"), _service("svc-2"))
        result = analyze_backends(data, [])
        assert all(not b["mapped"] for b in result)

    def test_cross_namespace_mapping(self):
        """Service in ns-b is mapped by route in ns-a via explicit namespace."""
        data = _data_with_services(_service("svc-backend", namespace="ns-b"))
        topology = _topology_with_route("svc-backend", backend_ns="ns-b")

        result = analyze_backends(data, topology)
        assert result[0]["mapped"] is True

    def test_service_metadata_preserved(self):
        svc = _service("my-svc")
        result = analyze_backends(_data_with_services(svc), [])
        b = result[0]
        assert b["type"] == "ClusterIP"
        assert b["clusterIP"] == "10.96.0.1"
        assert b["selector"] == {"app": "my-svc"}
        assert b["createdAt"] == "2026-01-01T00:00:00Z"
        assert len(b["ports"]) == 1
        assert b["ports"][0]["port"] == 8080
