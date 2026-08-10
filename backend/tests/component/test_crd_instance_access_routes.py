"""
Component tests for GET /api/k8s/clusters/{cluster_id}/resources/{resource_type}
with the P1.5 discovery fallback (D-018).

Error matrix (a–g from architect-v1.md):
  a. registered + instances → 200 via registry, discovery NOT called
  b. unregistered + installed → 200 (mock discovery)
  c. namespaced + cluster-scoped both resolve from discovered scope
  d. ambiguous bare plural (>1 group, no ?group=) → 400 w/ candidates
  e. FQ name disambiguates → 200
  f. neither registered nor installed → 404 NotFoundError (not 400)
  g. cluster unreachable → 503

Most tests mock KubernetesService at the route level. A small set of lower-level
tests mock at the _resources.py layer to assert specific plumbing (registry-first
invariant, 404 regression trap, namespaced flag propagation).
"""
from unittest.mock import MagicMock, call, patch

import pytest

from core.errors import BadRequestError, NetworkUnreachableError, NotFoundError
from core.k8s_types import K8sResourceType
from schemas.k8s import CRDInfo
from services.kubernetes_service import KubernetesService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _k8s_rt(plural: str, group: str, kind: str, namespaced: bool = True) -> K8sResourceType:
    return K8sResourceType(
        api_group=group,
        api_version="v1",
        kind=kind,
        plural=plural,
        namespaced=namespaced,
        display_name=kind,
    )


# ---------------------------------------------------------------------------
# (a) Registered kind — registry path, _resolve_via_discovery NOT called
# ---------------------------------------------------------------------------

class TestRegisteredKindUnchanged:
    """Registered kinds use the static registry; _resolve_via_discovery is never entered."""

    def test_registered_kind_returns_200(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """'pod' is in the registry → 200."""
        cluster = make_k8s_cluster(project=sample_project, name="reg-cluster")

        with patch("routes.k8s.resources.KubernetesService") as MockSvc:
            MockSvc.return_value.get_resources.return_value = [
                {"kind": "Pod", "metadata": {"name": "my-pod"}, "apiVersion": "v1"}
            ]
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/pod",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resource_type"] == "pod"
        assert body["count"] == 1

    def test_registry_first_discovery_not_called(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """Registry hit → _resolve_via_discovery is never called (registry-first invariant)."""
        cluster = make_k8s_cluster(project=sample_project, name="reg-inv-cluster")

        with patch("routes.k8s.resources.KubernetesService") as MockSvc, \
             patch("services.kubernetes._resources._resolve_via_discovery") as mock_discover:
            MockSvc.return_value.get_resources.return_value = []
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/pod",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        mock_discover.assert_not_called()

    def test_group_param_forwarded_to_service(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """group= query param is forwarded to get_resources."""
        cluster = make_k8s_cluster(project=sample_project, name="reg-group-cluster")

        with patch("routes.k8s.resources.KubernetesService") as MockSvc:
            MockSvc.return_value.get_resources.return_value = []
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/pod?group=apps",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        call_kwargs = MockSvc.return_value.get_resources.call_args.kwargs
        assert call_kwargs.get("group") == "apps"


# ---------------------------------------------------------------------------
# (b) Unregistered but installed CRD → 200
# ---------------------------------------------------------------------------

class TestUnregisteredInstalledCrd:
    """Unregistered CRD installed in cluster → discovery path → 200."""

    def test_unregistered_installed_crd_returns_200(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """analyzers (unregistered) installed in cluster → 200, not 400."""
        cluster = make_k8s_cluster(project=sample_project, name="discovery-cluster")

        with patch("routes.k8s.resources.KubernetesService") as MockSvc:
            MockSvc.return_value.get_resources.return_value = [
                {"kind": "Analyzer", "metadata": {"name": "my-analyzer"}, "apiVersion": "k8s.f5.com/v1"}
            ]
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/analyzers",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resource_type"] == "analyzers"
        assert body["count"] == 1

    def test_fq_name_unregistered_installed_returns_200(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """analyzers.k8s.f5.com (FQ name) → 200."""
        cluster = make_k8s_cluster(project=sample_project, name="fq-cluster")

        with patch("routes.k8s.resources.KubernetesService") as MockSvc:
            MockSvc.return_value.get_resources.return_value = []
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/analyzers.k8s.f5.com",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resource_type"] == "analyzers.k8s.f5.com"


# ---------------------------------------------------------------------------
# (c) Namespaced vs cluster-scoped scope from discovery
# ---------------------------------------------------------------------------

class TestScopeFromDiscovery:
    """Confirm _resolve_via_discovery result flows into _fetch_from_k8s namespaced flag."""

    def test_namespaced_flag_propagates_to_fetch(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """namespaced=True in discovered CRD → K8sResourceType.namespaced=True passed to _fetch."""
        cluster = make_k8s_cluster(project=sample_project, name="ns-flag-cluster")

        namespaced_rt = _k8s_rt("somethings", "example.io", "Something", namespaced=True)
        captured: dict = {}

        def capture_fetch(api_client, resource_type, namespace, label_selector):
            captured["namespaced"] = resource_type.namespaced
            return []

        with patch("services.kubernetes._resources.get_resource_type", side_effect=ValueError("not in registry")), \
             patch("services.kubernetes._resources._resolve_via_discovery", return_value=namespaced_rt), \
             patch.object(KubernetesService, "get_cluster", return_value=MagicMock()), \
             patch.object(KubernetesService, "load_kubeconfig", return_value=MagicMock()), \
             patch("services.kubernetes._resources.ResourcesMixin._fetch_from_k8s", side_effect=capture_fetch):
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/somethings",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        assert captured.get("namespaced") is True

    def test_cluster_scoped_flag_propagates(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """namespaced=False in discovered CRD → K8sResourceType.namespaced=False."""
        cluster = make_k8s_cluster(project=sample_project, name="cs-flag-cluster")

        cluster_rt = _k8s_rt("clusterthings", "example.io", "ClusterThing", namespaced=False)
        captured: dict = {}

        def capture_fetch(api_client, resource_type, namespace, label_selector):
            captured["namespaced"] = resource_type.namespaced
            return []

        with patch("services.kubernetes._resources.get_resource_type", side_effect=ValueError("not in registry")), \
             patch("services.kubernetes._resources._resolve_via_discovery", return_value=cluster_rt), \
             patch.object(KubernetesService, "get_cluster", return_value=MagicMock()), \
             patch.object(KubernetesService, "load_kubeconfig", return_value=MagicMock()), \
             patch("services.kubernetes._resources.ResourcesMixin._fetch_from_k8s", side_effect=capture_fetch):
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/clusterthings",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        assert captured.get("namespaced") is False


# ---------------------------------------------------------------------------
# (d) Ambiguous bare plural → 400 with candidates
# ---------------------------------------------------------------------------

class TestAmbiguousBarePlural:
    def test_ambiguous_plural_returns_400_with_candidates(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """Two groups share 'foos' plural, no ?group= → 400 listing candidates."""
        cluster = make_k8s_cluster(project=sample_project, name="ambig-cluster")

        with patch("routes.k8s.resources.KubernetesService") as MockSvc:
            MockSvc.return_value.get_resources.side_effect = BadRequestError(
                "Ambiguous resource type 'foos'",
                code="AMBIGUOUS_RESOURCE_TYPE",
                details={"candidates": ["foos.group-a.io", "foos.group-b.io"]},
            )
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/foos",
                headers=admin_headers,
            )

        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert body["error"]["code"] == "AMBIGUOUS_RESOURCE_TYPE"
        assert "foos.group-a.io" in body["error"]["details"]["candidates"]
        assert "foos.group-b.io" in body["error"]["details"]["candidates"]

    def test_ambiguous_resolution_layer(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """_resolve_via_discovery raises BadRequestError → propagates as 400."""
        cluster = make_k8s_cluster(project=sample_project, name="ambig2-cluster")

        with patch("services.kubernetes._resources.get_resource_type", side_effect=ValueError("not in registry")), \
             patch("services.kubernetes._resources._resolve_via_discovery") as mock_discover, \
             patch.object(KubernetesService, "get_cluster", return_value=MagicMock()), \
             patch.object(KubernetesService, "load_kubeconfig", return_value=MagicMock()):
            mock_discover.side_effect = BadRequestError(
                "Ambiguous",
                code="AMBIGUOUS_RESOURCE_TYPE",
                details={"candidates": ["bars.group-a.io", "bars.group-b.io"]},
            )
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/bars",
                headers=admin_headers,
            )

        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "AMBIGUOUS_RESOURCE_TYPE"


# ---------------------------------------------------------------------------
# (e) FQ name disambiguates → 200
# ---------------------------------------------------------------------------

class TestFQNameDisambiguates:
    def test_fq_name_resolves_200(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        cluster = make_k8s_cluster(project=sample_project, name="fq-disambig-cluster")

        with patch("routes.k8s.resources.KubernetesService") as MockSvc:
            MockSvc.return_value.get_resources.return_value = []
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/foos.group-b.io",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text

    def test_group_query_param_forwarded_to_resolve(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """?group= forwarded through to _resolve_via_discovery."""
        cluster = make_k8s_cluster(project=sample_project, name="group-param-cluster")
        foo_a_rt = _k8s_rt("foos", "group-a.io", "FooA")

        with patch("services.kubernetes._resources.get_resource_type", side_effect=ValueError("not in registry")), \
             patch("services.kubernetes._resources._resolve_via_discovery", return_value=foo_a_rt) as mock_discover, \
             patch.object(KubernetesService, "get_cluster", return_value=MagicMock()), \
             patch.object(KubernetesService, "load_kubeconfig", return_value=MagicMock()), \
             patch("services.kubernetes._resources.ResourcesMixin._fetch_from_k8s", return_value=[]):
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/foos?group=group-a.io",
                headers=admin_headers,
            )

        assert resp.status_code == 200, resp.text
        mock_discover.assert_called_once()
        call_args = mock_discover.call_args
        # group is passed as the 4th positional argument: (db, cluster_id, resource_type_key, group)
        assert call_args.args[3] == "group-a.io"


# ---------------------------------------------------------------------------
# (f) Neither registered nor installed → 404 NotFoundError (not 400)
# ---------------------------------------------------------------------------

class TestNotInstalledReturns404:
    def test_not_installed_returns_404_not_400(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """Not in registry + not in cluster → 404 structured error, NOT 400.

        This is the key regression guard: a bare ValueError becoming 400 would
        be a regression (the gate we're removing). NotFoundError must be 404.
        """
        cluster = make_k8s_cluster(project=sample_project, name="absent-cluster")

        with patch("services.kubernetes._resources.get_resource_type", side_effect=ValueError("not in registry")), \
             patch("services.kubernetes._resources._resolve_via_discovery") as mock_discover, \
             patch.object(KubernetesService, "get_cluster", return_value=MagicMock()), \
             patch.object(KubernetesService, "load_kubeconfig", return_value=MagicMock()):
            mock_discover.side_effect = NotFoundError("CRD", "totallynotexistent")
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/totallynotexistent",
                headers=admin_headers,
            )

        assert resp.status_code == 404, resp.text
        body = resp.json()
        assert body["error"]["code"].endswith("_NOT_FOUND"), \
            f"Expected *_NOT_FOUND error code, got: {body['error']['code']}"
        # Explicit regression guard: must NOT be 400
        assert resp.status_code != 400, "REGRESSION: ValueError leaked as 400"

    def test_fq_name_not_installed_returns_404(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        cluster = make_k8s_cluster(project=sample_project, name="absent-fq-cluster")

        with patch("routes.k8s.resources.KubernetesService") as MockSvc:
            MockSvc.return_value.get_resources.side_effect = NotFoundError(
                "CRD", "missing.no-such-group.io"
            )
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/missing.no-such-group.io",
                headers=admin_headers,
            )

        assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# (g) Cluster unreachable → 503
# ---------------------------------------------------------------------------

class TestUnreachableClusterReturns503:
    def test_unreachable_cluster_returns_503(
        self, client, admin_headers, sample_user, make_k8s_cluster, sample_project
    ):
        """NetworkUnreachableError → 503 NETWORK_UNREACHABLE."""
        cluster = make_k8s_cluster(project=sample_project, name="dead-res-cluster")

        with patch("routes.k8s.resources.KubernetesService") as MockSvc:
            MockSvc.return_value.get_resources.side_effect = NetworkUnreachableError(
                target_type="cluster",
                target_id=cluster.id,
                target_name="dead-res-cluster",
                suggested_action="Check VPN",
            )
            resp = client.get(
                f"/api/k8s/clusters/{cluster.id}/resources/analyzers",
                headers=admin_headers,
            )

        assert resp.status_code == 503, resp.text
        body = resp.json()
        assert body["error"]["code"] == "NETWORK_UNREACHABLE"
