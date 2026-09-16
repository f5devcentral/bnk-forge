"""
Integration tests for F5 BNK routes — /api/k8s/clusters/{cluster_id}/f5bnk/*.

Covers: unified data fetch, health, gateway topology, policy associations,
unauthenticated access. Uses FastAPI TestClient with real SQLite DB.

Only the K8s API layer is mocked (KubernetesService + fetch_all_bnk_data).
The real analysis functions (analyze_health, analyze_topology, etc.) run
against realistic BNK data, so these tests exercise the full pipeline
from route → analysis → response.
"""

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Realistic BNK data — matches the shape returned by fetch_all_bnk_data
# ---------------------------------------------------------------------------

def _make_pod(name: str, namespace: str, phase: str = "Running", ready: bool = True) -> dict:
    """Build a realistic pod dict matching bnk_pod_discovery output."""
    return {
        "name": name,
        "namespace": namespace,
        "phase": phase,
        "containers": [
            {
                "name": name.rsplit("-", 1)[0],
                "ready": ready,
                "restartCount": 0,
                "state": "running" if ready else "waiting",
            },
        ],
    }


def _make_resource(name: str, namespace: str = "f5-bnk", **extra) -> dict:
    """Build a minimal K8s resource dict with metadata + optional spec/status."""
    resource = {
        "metadata": {"name": name, "namespace": namespace, "creationTimestamp": "2026-01-01T00:00:00Z"},
        "spec": extra.get("spec", {}),
        "status": extra.get("status", {}),
    }
    return resource


def _make_gateway(name: str, namespace: str = "f5-bnk") -> dict:
    return _make_resource(name, namespace, spec={
        "gatewayClassName": "f5-bnk",
        "listeners": [
            {"name": "http", "protocol": "HTTP", "port": 80},
        ],
    }, status={
        "addresses": [{"value": "10.0.0.1"}],
        "conditions": [
            {"type": "Programmed", "status": "True", "message": ""},
            {"type": "Accepted", "status": "True", "message": ""},
        ],
    })


def _make_httproute(name: str, gw_name: str, namespace: str = "f5-bnk") -> dict:
    return _make_resource(name, namespace, spec={
        "parentRefs": [{"name": gw_name, "namespace": namespace}],
        "hostnames": ["example.com"],
        "rules": [
            {
                "backendRefs": [
                    {"name": "my-svc", "port": 8080, "kind": "Service", "group": ""},
                ],
            },
        ],
    })


def _make_service(name: str, namespace: str = "f5-bnk") -> dict:
    return _make_resource(name, namespace, spec={
        "type": "ClusterIP",
        "clusterIP": "10.96.0.1",
        "ports": [{"port": 8080, "targetPort": 8080, "protocol": "TCP", "name": "http"}],
        "selector": {"app": name},
    })


def _make_bnk_data() -> dict:
    """Build a complete, realistic fetch_all_bnk_data return value."""
    return {
        "resources": {
            "gatewayclass": [_make_resource("f5-bnk")],
            "gateway": [_make_gateway("gw-prod")],
            "httproute": [_make_httproute("web-route", "gw-prod")],
            "grpcroute": [],
            "tcproute": [],
            "udproute": [],
            "tlsroute": [],
            "l4route": [],
            "referencegrant": [],
            "bnksecpolicy": [],
            "bnknetpolicy": [],
            "f5bigfwpolicy": [],
            "f5bigcneirule": [],
            "f5biganalyzer": [],
            "f5bigcneaddresslist": [],
            "f5bigcneportlist": [],
            "f5spkvlan": [_make_resource("internal-vlan", spec={
                "interfaces": ["1.1"],
                "selfip_v4s": ["10.1.1.1"],
                "mtu": 1500,
            }, status={
                "conditions": [{"type": "Programmed", "status": "True", "message": ""}],
            })],
            "cneinstance": [_make_resource("cne-1", spec={
                "firewallACL": {"enabled": True},
                "intelligentLB": {"enabled": False},
                "pseudoCNI": False,
                "metricSubsystem": {"enabled": True},
                "loggingSubsystem": {"enabled": False},
            })],
            "f5spkstaticroute": [],
            "f5spksnatpool": [],
            "f5spkegress": [],
            "f5bigloghslpub": [],
            "f5biglogprofile": [],
            "service": [_make_service("my-svc")],
        },
        "pods": {
            "tenant": [_make_pod("f5-tmm-abc", "f5-bnk")],
            "utils": [_make_pod("f5-spk-cwc-xyz", "f5-utils")],
        },
        "classified_pods": {
            "tmm": [_make_pod("f5-tmm-abc", "f5-bnk")],
            "flo": [_make_pod("f5-flo-def", "f5-bnk")],
            "controller": [_make_pod("f5-ctrl-ghi", "f5-bnk")],
            "analyzer": [],
            "crd_installer": [_make_pod("f5-crd-jkl", "f5-bnk", phase="Succeeded", ready=False)],
            "other": [],
        },
        "cluster_id": 1,
        "namespace": None,
    }


_BNK_DATA = _make_bnk_data()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetBnkData:
    """GET /api/k8s/clusters/{cluster_id}/f5bnk/data — unified endpoint."""

    @patch("routes.k8s.f5bnk.fetch_all_bnk_data", return_value=_BNK_DATA)
    @patch("routes.k8s.f5bnk.KubernetesService")
    def test_returns_complete_response(
        self, mock_k8s_svc_cls, mock_fetch,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Unified endpoint returns health, topology, backends, palette, policy."""
        cluster = make_k8s_cluster(project=sample_project, name="bnk-data-cluster")
        mock_k8s_svc_cls.return_value.get_cluster.return_value = cluster

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/f5bnk/data",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()

        # Response includes all expected sections
        assert data["cluster_id"] == cluster.id
        assert "health" in data
        assert "topology" in data
        assert "dataPlane" in data
        assert "topologyCounts" in data
        assert "policyAssociations" in data
        assert "backends" in data
        assert "palette" in data
        assert "referenceGrants" in data

        # Health was computed from realistic data
        assert data["health"]["overall"] in ("healthy", "warning", "critical", "unknown")
        assert "platform" in data["health"]
        assert "dataPlane" in data["health"]
        assert "networking" in data["health"]

        # Topology was computed — should find our gateway
        assert isinstance(data["topology"], list)
        assert len(data["topology"]) == 1
        assert data["topology"][0]["name"] == "gw-prod"

        # Backends — our service should be mapped via the HTTPRoute
        assert len(data["backends"]) == 1
        assert data["backends"][0]["name"] == "my-svc"
        assert data["backends"][0]["mapped"] is True

        # Palette — should include our gateway class and service
        assert len(data["palette"]["gatewayClasses"]) == 1
        assert len(data["palette"]["services"]) == 1

        # Counts
        assert data["topologyCounts"]["gateways"] == 1
        assert data["topologyCounts"]["httpRoutes"] == 1
        assert data["topologyCounts"]["totalRoutes"] == 1

        mock_fetch.assert_called_once()

    @patch("routes.k8s.f5bnk.fetch_all_bnk_data", return_value=_BNK_DATA)
    @patch("routes.k8s.f5bnk.KubernetesService")
    def test_passes_namespace_filter(
        self, mock_k8s_svc_cls, mock_fetch,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Namespace query param is forwarded to fetch_all_bnk_data."""
        cluster = make_k8s_cluster(project=sample_project, name="ns-filter-cluster")
        mock_k8s_svc_cls.return_value.get_cluster.return_value = cluster

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/f5bnk/data?namespace=f5-bnk",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        _, kwargs = mock_fetch.call_args
        assert kwargs.get("namespace") == "f5-bnk" or mock_fetch.call_args[0][2] == "f5-bnk"


class TestGetBnkHealth:
    """GET /api/k8s/clusters/{cluster_id}/f5bnk/health."""

    @patch("routes.k8s.f5bnk.fetch_all_bnk_data", return_value=_BNK_DATA)
    @patch("routes.k8s.f5bnk.KubernetesService")
    def test_returns_health_analysis(
        self, mock_k8s_svc_cls, mock_fetch,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Health endpoint runs real analysis and returns structured result."""
        cluster = make_k8s_cluster(project=sample_project, name="bnk-health-cluster")
        mock_k8s_svc_cls.return_value.get_cluster.return_value = cluster

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/f5bnk/health",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["cluster_id"] == cluster.id
        assert data["overall"] in ("healthy", "warning", "critical", "unknown")

        # Platform section has all expected components
        platform = data["platform"]
        for component in ("flo", "controller", "crdInstaller", "analyzer"):
            assert component in platform
            assert "severity" in platform[component]
            assert "total" in platform[component]

        # Data plane section
        assert "tmm" in data["dataPlane"]
        assert data["dataPlane"]["tmm"]["pods"] == 1

        # Networking
        assert data["networking"]["gateways"]["total"] == 1
        assert data["networking"]["gateways"]["programmed"] == 1

        # Security
        assert "irules" in data["security"]

        # Counts
        assert data["counts"]["gateways"] == 1
        assert data["counts"]["tmm_pods"] == 1


class TestGetGatewayTopology:
    """GET /api/k8s/clusters/{cluster_id}/f5bnk/gateway-topology."""

    @patch("routes.k8s.f5bnk.fetch_all_bnk_data", return_value=_BNK_DATA)
    @patch("routes.k8s.f5bnk.KubernetesService")
    def test_returns_topology_tree(
        self, mock_k8s_svc_cls, mock_fetch,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Topology endpoint builds real gateway→listener→route tree."""
        cluster = make_k8s_cluster(project=sample_project, name="topo-cluster")

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/f5bnk/gateway-topology",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["cluster_id"] == cluster.id

        # Topology tree is present with our gateway
        assert len(data["topology"]) == 1
        gw = data["topology"][0]
        assert gw["name"] == "gw-prod"
        assert gw["gatewayClassName"] == "f5-bnk"
        assert gw["addresses"] == ["10.0.0.1"]

        # Listener was built
        assert len(gw["listeners"]) == 1
        listener = gw["listeners"][0]
        assert listener["name"] == "http"
        assert listener["protocol"] == "HTTP"
        assert listener["port"] == 80

        # Route was matched to the listener
        assert len(listener["routes"]) == 1
        route = listener["routes"][0]
        assert route["name"] == "web-route"
        assert route["kind"] == "HTTPRoute"
        assert len(route["backends"]) == 1
        assert route["backends"][0]["name"] == "my-svc"

        # Data plane section
        assert len(data["dataPlane"]["vlans"]) == 1
        assert len(data["dataPlane"]["cneInstances"]) == 1

        # Counts
        assert data["counts"]["gateways"] == 1
        assert data["counts"]["totalRoutes"] == 1


class TestGetPolicyAssociations:
    """GET /api/k8s/clusters/{cluster_id}/f5bnk/policy-gateway-associations."""

    @patch("routes.k8s.f5bnk.fetch_all_bnk_data", return_value=_BNK_DATA)
    @patch("routes.k8s.f5bnk.KubernetesService")
    def test_returns_policy_associations(
        self, mock_k8s_svc_cls, mock_fetch,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Policy associations endpoint returns real analysis result."""
        cluster = make_k8s_cluster(project=sample_project, name="policy-cluster")

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/f5bnk/policy-gateway-associations",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["cluster_id"] == cluster.id
        assert "associations" in data
        assert "count" in data
        # No security policies in our test data, so no associations
        assert data["count"] == 0
        assert data["associations"] == []


class TestBnkUnauthenticated:
    """Unauthenticated access to BNK endpoints returns 401."""

    def test_all_endpoints_require_auth(self, client, sample_project, make_k8s_cluster):
        """All BNK endpoints reject unauthenticated requests."""
        cluster = make_k8s_cluster(project=sample_project, name="noauth-cluster")
        endpoints = [
            f"/api/k8s/clusters/{cluster.id}/f5bnk/data",
            f"/api/k8s/clusters/{cluster.id}/f5bnk/health",
            f"/api/k8s/clusters/{cluster.id}/f5bnk/gateway-topology",
            f"/api/k8s/clusters/{cluster.id}/f5bnk/policy-gateway-associations",
        ]
        for endpoint in endpoints:
            response = client.get(endpoint)
            assert response.status_code == 401, f"{endpoint} should require auth"
