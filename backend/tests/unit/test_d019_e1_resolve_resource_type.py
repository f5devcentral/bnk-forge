"""
Unit tests for D-019 E1 — shared resolve_resource_type resolver.

Tests cover:
  - resolve_resource_type: registry hit, discovery fallback, not-found.
  - Write path (create_resource): same three cases + error propagation.
  - Describe path: not-found surfaces as NotFoundError (404).
  - Aggregation helper (bnk safe_fetch): absent → [] (aggregation not broken).
"""
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, NotFoundError
from core.k8s_types import K8sResourceType
from schemas.k8s import CRDInfo, CrdListEnvelope
from services.kubernetes._resources import resolve_plural_by_kind, resolve_resource_type

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _k8s_rt(kind: str, plural: str, group: str = "example.io", namespaced: bool = True) -> K8sResourceType:
    return K8sResourceType(
        api_group=group,
        api_version="v1",
        kind=kind,
        plural=plural,
        namespaced=namespaced,
        display_name=kind,
        description=None,
    )


def _crd_info(plural: str, group: str, kind: str, namespaced: bool = True) -> CRDInfo:
    return CRDInfo(
        name=f"{plural}.{group}",
        kind=kind,
        plural=plural,
        group=group,
        version="v1",
        namespaced=namespaced,
        display_name=kind,
        category=None,
        source="discovered",
    )


def _envelope(*crds: CRDInfo) -> CrdListEnvelope:
    return CrdListEnvelope(
        crds=list(crds),
        count=len(crds),
        cluster_id=1,
        group_filter=None,
        info=None,
    )


# ---------------------------------------------------------------------------
# resolve_resource_type — the shared resolver
# ---------------------------------------------------------------------------

class TestResolveResourceType:
    """Unit tests for resolve_resource_type (registry-first → discovery)."""

    def test_registered_kind_returns_from_registry_without_discovery(self):
        """A kind in the static registry must be served from registry; discovery not called."""
        db = MagicMock()

        with patch("services.kubernetes._resources._resolve_via_discovery") as mock_disc:
            # "pod" is a well-known registered kind
            result = resolve_resource_type(db, 1, "pod")

        assert result.kind == "Pod"
        mock_disc.assert_not_called()

    def test_unregistered_installed_kind_falls_back_to_discovery(self):
        """An unregistered kind that is installed on the cluster resolves via discovery."""
        db = MagicMock()
        discovered_rt = _k8s_rt("RabbitMQ", "rabbitmqs", group="rabbitmq.com")

        with patch("services.kubernetes._resources._resolve_via_discovery", return_value=discovered_rt) as mock_disc:
            result = resolve_resource_type(db, 42, "rabbitmqs")

        mock_disc.assert_called_once_with(db, 42, "rabbitmqs", None)
        assert result.kind == "RabbitMQ"
        assert result.api_group == "rabbitmq.com"

    def test_unregistered_absent_kind_raises_not_found(self):
        """An absent CRD (NotFoundError from discovery) propagates as 404."""
        db = MagicMock()

        with patch(
            "services.kubernetes._resources._resolve_via_discovery",
            side_effect=NotFoundError("CRD", "notexists"),
        ):
            with pytest.raises(NotFoundError) as exc_info:
                resolve_resource_type(db, 1, "notexists")

        assert exc_info.value.status_code == 404

    def test_ambiguous_bare_plural_raises_bad_request(self):
        """Ambiguous bare plural (>1 group, no group param) propagates as 400."""
        db = MagicMock()

        with patch(
            "services.kubernetes._resources._resolve_via_discovery",
            side_effect=BadRequestError("Ambiguous plural", {"candidates": ["a", "b"]}),
        ):
            with pytest.raises(BadRequestError) as exc_info:
                resolve_resource_type(db, 1, "widgets")

        assert exc_info.value.status_code == 400

    def test_group_param_forwarded_to_discovery(self):
        """group= parameter is forwarded to _resolve_via_discovery."""
        db = MagicMock()
        discovered_rt = _k8s_rt("Widget", "widgets", group="group-b.io")

        with patch("services.kubernetes._resources._resolve_via_discovery", return_value=discovered_rt) as mock_disc:
            result = resolve_resource_type(db, 5, "widgets", group="group-b.io")

        mock_disc.assert_called_once_with(db, 5, "widgets", "group-b.io")
        assert result.api_group == "group-b.io"


# ---------------------------------------------------------------------------
# Write path: create_resource, update_resource, delete_resource
# ---------------------------------------------------------------------------

class TestWritePathDiscovery:
    """
    Tests the write operations route through resolve_resource_type so that
    unregistered-but-installed CRDs work and absent CRDs surface as 404.
    """

    def _make_mixin(self):
        """Return a ResourcesMixin-like object with db + cluster/kubeconfig stubs."""
        from services.kubernetes._resources import ResourcesMixin

        class _FakeSvc(ResourcesMixin):
            def __init__(self):
                self.db = MagicMock()

            def get_cluster(self, cluster_id):
                c = MagicMock()
                c.default_namespace = "default"
                return c

            def load_kubeconfig(self, cluster):
                return MagicMock()

        return _FakeSvc()

    def test_create_registered_kind_resolves_from_registry(self):
        """create_resource with a registered kind uses registry; no discovery."""
        svc = self._make_mixin()
        yaml_str = "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: test\n"

        with (
            patch("services.kubernetes._resources.resolve_resource_type") as mock_resolve,
            patch("services.kubernetes._resources.client"),
        ):
            mock_resolve.return_value = _k8s_rt("ConfigMap", "configmaps", group="")
            mock_resolve.return_value = K8sResourceType(
                api_group="",
                api_version="v1",
                kind="ConfigMap",
                plural="configmaps",
                namespaced=True,
                display_name="ConfigMap",
                description=None,
            )

            with patch.object(svc, "_write_typed_resource", return_value={"kind": "ConfigMap"}):
                result = svc.create_resource(1, "configmap", yaml_str)

        assert result["success"] is True
        mock_resolve.assert_called_once_with(svc.db, 1, "configmap")

    def test_create_absent_kind_propagates_not_found(self):
        """create_resource with a not-installed CRD raises NotFoundError (404)."""
        svc = self._make_mixin()
        yaml_str = "apiVersion: v1\nkind: Foo\nmetadata:\n  name: test\n"

        with patch(
            "services.kubernetes._resources.resolve_resource_type",
            side_effect=NotFoundError("CRD", "foos"),
        ):
            with pytest.raises(NotFoundError) as exc_info:
                svc.create_resource(1, "foos", yaml_str)

        assert exc_info.value.status_code == 404

    def test_delete_absent_kind_propagates_not_found(self):
        """delete_resource with a not-installed CRD raises NotFoundError (404)."""
        svc = self._make_mixin()

        with patch(
            "services.kubernetes._resources.resolve_resource_type",
            side_effect=NotFoundError("CRD", "foos"),
        ):
            with pytest.raises(NotFoundError) as exc_info:
                svc.delete_resource(1, "foos", "my-foo")

        assert exc_info.value.status_code == 404

    def test_update_absent_kind_propagates_not_found(self):
        """update_resource with a not-installed CRD raises NotFoundError (404)."""
        svc = self._make_mixin()
        yaml_str = "apiVersion: v1\nkind: Foo\nmetadata:\n  name: test\n"

        with patch(
            "services.kubernetes._resources.resolve_resource_type",
            side_effect=NotFoundError("CRD", "foos"),
        ):
            with pytest.raises(NotFoundError) as exc_info:
                svc.update_resource(1, "foos", "my-foo", yaml_str)

        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Describe path
# ---------------------------------------------------------------------------

class TestDescribePathDiscovery:
    """describe_resource surfaces NotFoundError (404) for absent CRDs."""

    def _make_describe_mixin(self):
        from services.kubernetes._describe import DescribeMixin

        class _FakeSvc(DescribeMixin):
            def __init__(self):
                self.db = MagicMock()

            def get_cluster(self, cluster_id):
                c = MagicMock()
                c.default_namespace = "default"
                return c

            def load_kubeconfig(self, cluster):
                return MagicMock()

        return _FakeSvc()

    def test_describe_absent_kind_raises_not_found(self):
        """describe_resource with a not-installed CRD raises NotFoundError (404).

        The exception must propagate out of the try/except in describe_resource —
        we patch at the _describe module's import site.
        """
        svc = self._make_describe_mixin()

        with patch(
            "services.kubernetes._describe.resolve_resource_type",
            side_effect=NotFoundError("CRD", "foos"),
        ):
            with pytest.raises(NotFoundError) as exc_info:
                svc.describe_resource(1, "foos", "my-foo")

        assert exc_info.value.status_code == 404

    def test_describe_unregistered_installed_kind_resolves(self):
        """describe_resource with an installed-but-unregistered CRD resolves via discovery."""
        svc = self._make_describe_mixin()
        discovered_rt = _k8s_rt("RabbitMQ", "rabbitmqs", group="rabbitmq.com")

        with (
            patch("services.kubernetes._describe.resolve_resource_type", return_value=discovered_rt) as mock_resolve,
            patch.object(svc, "_describe_fetch_resource", return_value={"metadata": {"name": "test"}, "spec": {}, "status": {}}),
            patch.object(svc, "_describe_fetch_events", return_value=[]),
        ):
            result = svc.describe_resource(1, "rabbitmqs", "test-rabbit")

        mock_resolve.assert_called_once_with(svc.db, 1, "rabbitmqs")
        assert result["kind"] == "RabbitMQ"


# ---------------------------------------------------------------------------
# Aggregation helper: bnk safe_fetch
# ---------------------------------------------------------------------------

class TestBnkSafeFetchAggregation:
    """bnk.fetch.safe_fetch: absent CRD → [] (aggregation not broken)."""

    def _make_k8s_service(self):
        svc = MagicMock()
        svc.db = MagicMock()
        return svc

    def test_absent_crd_returns_empty_list(self):
        """safe_fetch swallows NotFoundError → [] so the BNK aggregation continues."""
        from services.bnk.fetch import fetch_all_bnk_data
        from services.bnk.helpers import BNK_RESOURCE_TYPES

        k8s_service = self._make_k8s_service()
        # discovery raises NotFoundError for all kinds
        k8s_service._fetch_from_k8s.side_effect = NotFoundError("CRD", "test")

        # discover_f5_pods and fetch_crd_installer_job need mocking too
        with (
            patch("services.bnk.fetch.resolve_resource_type", side_effect=NotFoundError("CRD", "test")),
            patch("services.bnk.fetch.discover_f5_pods", return_value=([], [])),
            patch("services.bnk.fetch.classify_f5_pods", return_value={}),
        ):
            cluster = MagicMock()
            k8s_service.get_cluster.return_value = cluster
            k8s_service.load_kubeconfig.return_value = MagicMock()

            # Patch batch API call inside safe_fetch_crd_installer_job
            with patch("kubernetes.client.BatchV1Api") as mock_batch:
                mock_batch.return_value.list_namespaced_job.return_value.items = []
                result = fetch_all_bnk_data(k8s_service, 1)

        # All resource lists must be empty — aggregation not broken
        for key in BNK_RESOURCE_TYPES:
            assert result["resources"][key] == [], f"Expected [] for {key} when CRD absent"

    def test_installed_crd_resolves_and_fetches(self):
        """safe_fetch resolves an unregistered-but-installed CRD and fetches items."""
        from services.bnk.fetch import fetch_all_bnk_data
        from services.bnk.helpers import BNK_RESOURCE_TYPES

        k8s_service = self._make_k8s_service()
        first_rt_key = list(BNK_RESOURCE_TYPES)[0]
        discovered_rt = _k8s_rt("Foo", first_rt_key + "s", group="test.io")
        fake_item = {"metadata": {"name": "test"}, "kind": "Foo"}

        def resolve_side_effect(db, cluster_id, key, group=None):
            return discovered_rt

        def fetch_side_effect(api_client, rt, ns, sel):
            return [fake_item]

        with (
            patch("services.bnk.fetch.resolve_resource_type", side_effect=resolve_side_effect),
            patch("services.bnk.fetch.discover_f5_pods", return_value=([], [])),
            patch("services.bnk.fetch.classify_f5_pods", return_value={}),
        ):
            cluster = MagicMock()
            k8s_service.get_cluster.return_value = cluster
            k8s_service.load_kubeconfig.return_value = MagicMock()
            k8s_service._fetch_from_k8s.return_value = [fake_item]

            with patch("kubernetes.client.BatchV1Api") as mock_batch:
                mock_batch.return_value.list_namespaced_job.return_value.items = []
                result = fetch_all_bnk_data(k8s_service, 1)

        assert result["resources"][first_rt_key] == [fake_item]


# ---------------------------------------------------------------------------
# resolve_plural_by_kind — import/restore CRD-agnostic plural resolution
# ---------------------------------------------------------------------------

class TestResolvePluralByKind:
    """
    config_export.py / snapshot_service.py only have a raw manifest's `kind` (e.g.
    "TenantControlPlane") + apiVersion group, never the plural. resolve_resource_type
    was being called with kind.lower() (singular), which only matches resolve_crd's
    bare-plural step by coincidence — for unregistered CRDs it never matches, and the
    caller silently falls back to a naive '+s' pluralization. resolve_plural_by_kind
    adds a kind-scoped-to-group CRD-discovery fallback so unregistered CRDs resolve.
    """

    def test_registered_kind_resolves_via_registry(self):
        """A registered kind resolves via the registry without touching CRD discovery."""
        db = MagicMock()

        with patch("services.crd_discovery_service.CrdDiscoveryService.list_crds") as mock_list:
            plural = resolve_plural_by_kind(db, 1, "ConfigMap", None)

        assert plural == "configmaps"
        mock_list.assert_not_called()

    def test_unregistered_kind_resolves_via_kind_scoped_discovery(self):
        """An unregistered CRD's plural resolves by matching kind within the manifest's group.

        This is the exact case the review flagged: kind.lower() ('tenantcontrolplane')
        never matches resolve_crd's bare-plural step ('tenantcontrolplanes'), so this
        path must resolve it by kind instead.
        """
        db = MagicMock()
        cluster_crd = _crd_info("tenantcontrolplanes", "kamaji.clastix.io", "TenantControlPlane")

        with patch("services.crd_discovery_service.CrdDiscoveryService.list_crds", return_value=_envelope(cluster_crd)):
            plural = resolve_plural_by_kind(db, 1, "TenantControlPlane", "kamaji.clastix.io")

        assert plural == "tenantcontrolplanes"

    def test_unregistered_kind_without_group_returns_none(self):
        """No group (e.g. core-API manifest) means no group_filter can be built — returns None."""
        db = MagicMock()

        with patch(
            "services.kubernetes._resources._resolve_via_discovery",
            side_effect=NotFoundError("CRD", "widget"),
        ):
            plural = resolve_plural_by_kind(db, 1, "Widget", None)

        assert plural is None

    def test_unmatched_kind_in_group_returns_none(self):
        """CRD discovery succeeds but no CRD in the group matches the kind — returns None."""
        db = MagicMock()
        cluster_crd = _crd_info("otherthings", "kamaji.clastix.io", "OtherThing")

        with patch("services.crd_discovery_service.CrdDiscoveryService.list_crds", return_value=_envelope(cluster_crd)):
            plural = resolve_plural_by_kind(db, 1, "TenantControlPlane", "kamaji.clastix.io")

        assert plural is None

    def test_discovery_failure_returns_none_not_raises(self):
        """Discovery errors (e.g. unreachable cluster) must not raise — caller has its own fallback."""
        db = MagicMock()

        with patch(
            "services.crd_discovery_service.CrdDiscoveryService.list_crds",
            side_effect=NotFoundError("Cluster", "1"),
        ):
            plural = resolve_plural_by_kind(db, 1, "TenantControlPlane", "kamaji.clastix.io")

        assert plural is None
