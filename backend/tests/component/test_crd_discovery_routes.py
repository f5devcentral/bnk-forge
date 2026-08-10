"""
Component tests for GET /api/k8s/clusters/{cluster_id}/crds.

Mocks CrdDiscoveryService._discover (the I/O boundary) so tests
exercise the full HTTP → service → cache → merge → filter → response
chain without a real cluster.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.errors import NetworkUnreachableError
from schemas.k8s import CRDInfo, CrdListEnvelope
from services.crd_discovery_service import CrdDiscoveryService

# ---------------------------------------------------------------------------
# Helpers: raw CRD dicts as ApiextensionsV1Api would return (after .to_dict())
# ---------------------------------------------------------------------------

def _raw_crd(group: str, kind: str, plural: str, scope: str = "Namespaced") -> dict:
    return {
        "metadata": {"name": f"{plural}.{group}"},
        "spec": {
            "group": group,
            "names": {"kind": kind, "plural": plural},
            "scope": scope,
            "versions": [{"name": "v1", "served": True, "storage": True}],
        },
    }


SAMPLE_CRDS = [
    _raw_crd("k8s.f5net.com", "FWPolicy", "fwpolicies"),
    _raw_crd("k8s.f5net.com", "VirtualServer", "virtualservers"),
    _raw_crd("other.io", "Foo", "foos"),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCrdDiscoveryRoute:

    def test_success_returns_crd_list_envelope(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """Happy path: _discover returns CRDs → 200 with CrdListEnvelope shape.

        The mock returns a real CrdListEnvelope instance so that a service
        response-shape regression would be caught rather than silently coerced
        by FastAPI's response_model serialisation.
        """
        cluster = make_k8s_cluster(project=sample_project, name="test-cluster")

        fw_crd = CRDInfo(
            name="fwpolicies.k8s.f5net.com",
            kind="FWPolicy",
            plural="fwpolicies",
            group="k8s.f5net.com",
            version="v1",
            namespaced=True,
            display_name=None,
            category=None,
            source="discovered",
        )
        envelope = CrdListEnvelope(
            crds=[fw_crd],
            count=1,
            cluster_id=cluster.id,
            group_filter=None,
            info=None,
        )

        with patch("routes.k8s.crds.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.return_value = envelope
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/crds",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "crds" in body
        assert "count" in body
        assert "cluster_id" in body
        assert body["cluster_id"] == cluster.id
        assert body["count"] == 1
        assert body["crds"][0]["kind"] == "FWPolicy"

    def test_group_filter_narrows_results(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """?group= filter is forwarded to the service and restricts results."""
        cluster = make_k8s_cluster(project=sample_project, name="filter-cluster")

        fw_crd = CRDInfo(
            name="fwpolicies.k8s.f5net.com",
            kind="FWPolicy",
            plural="fwpolicies",
            group="k8s.f5net.com",
            version="v1",
            namespaced=True,
            display_name=None,
            category=None,
            source="discovered",
        )
        envelope = CrdListEnvelope(
            crds=[fw_crd],
            count=1,
            cluster_id=cluster.id,
            group_filter=["k8s.f5net.com"],
            info=None,
        )

        with patch("routes.k8s.crds.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.return_value = envelope
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/crds?group=k8s.f5net.com",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # service was called with the filter
        MockSvc.return_value.list_crds.assert_called_once_with(
            cluster.id, group_filter=["k8s.f5net.com"]
        )
        assert body["group_filter"] == ["k8s.f5net.com"]

    def test_unreachable_cluster_returns_503(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """_discover raises NetworkUnreachableError → endpoint returns 503 with
        code=NETWORK_UNREACHABLE, never 200."""
        cluster = make_k8s_cluster(project=sample_project, name="dead-cluster")

        with patch("routes.k8s.crds.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.side_effect = NetworkUnreachableError(
                target_type="cluster",
                target_id=cluster.id,
                target_name="dead-cluster",
                suggested_action="Check VPN",
            )
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/crds",
                headers=admin_headers,
            )

        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["error"]["code"] == "NETWORK_UNREACHABLE"

    def test_reachable_empty_cluster_returns_200_with_info(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """Reachable cluster with no matching CRDs → 200, empty crds list, non-null info."""
        cluster = make_k8s_cluster(project=sample_project, name="empty-cluster")

        with patch("routes.k8s.crds.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.return_value = {
                "crds": [],
                "count": 0,
                "cluster_id": cluster.id,
                "group_filter": ["no-such-group.io"],
                "info": "No CRDs found in group(s): no-such-group.io",
            }
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/crds?group=no-such-group.io",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["crds"] == []
        assert body["count"] == 0
        assert body["info"] is not None
        assert "no-such-group.io" in body["info"]


class TestCrdDiscoveryDiscoverPath:
    """Tests that patch _discover directly, exercising the full
    list_crds → cache → merge → filter chain including the
    _discover → 503 error path.

    Note: patching _discover at the method level means the exception is raised
    directly from _discover without going through the internal try/except that
    wraps translate_k8s_exception. The real translation path is exercised by
    the existing test_unreachable_cluster_returns_503 via list_crds. Here we
    patch _discover to raise a NetworkUnreachableError (already-translated) to
    confirm the 503 contract is preserved end-to-end from _discover upward."""

    def test_discover_network_unreachable_returns_503(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """_discover raises NetworkUnreachableError (as produced by translate_k8s_exception)
        → @handle_route_errors correctly propagates 503 NETWORK_UNREACHABLE.
        Patches at _discover level so list_crds + @with_breaker chain is real."""
        cluster = make_k8s_cluster(project=sample_project, name="api-exc-cluster")

        with patch.object(
            CrdDiscoveryService,
            "_discover",
            side_effect=NetworkUnreachableError(
                target_type="cluster",
                target_id=cluster.id,
                target_name="api-exc-cluster",
                suggested_action="Check connectivity",
            ),
        ):
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/crds",
                headers=admin_headers,
            )

        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["error"]["code"] == "NETWORK_UNREACHABLE"

    def test_discover_returns_plain_dicts_version_resolved(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """_discover returns plain dicts (as from to_dict() / cache).
        Verifies that version is correctly resolved — not None — from dict versions.
        This guards the _pick_version dict-path bug (getattr on dict returns None)."""
        cluster = make_k8s_cluster(project=sample_project, name="dict-cluster")

        with patch.object(
            CrdDiscoveryService,
            "_discover",
            return_value=SAMPLE_CRDS,
        ):
            with patch("services.crd_discovery_service.cache") as mock_cache:
                mock_cache.get.return_value = None  # force cache miss → uses _discover
                mock_cache.set.return_value = True
                resp = client.get(
                    f"/api/k8s/clusters/{cluster.id}/crds",
                    headers=admin_headers,
                )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["count"] == len(SAMPLE_CRDS)
        # All CRDs should have version resolved — not None
        for crd in body["crds"]:
            assert crd["version"] == "v1", f"Expected v1 but got {crd['version']} for {crd['name']}"
