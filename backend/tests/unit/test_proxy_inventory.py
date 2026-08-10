"""
Unit tests for ProxyDiscoveryService inventory mode (target=None).

Tests:
  - inventory mode returns items, never writes ProxyDeployment rows
  - dynamic enumeration with generic fallback (unknown controller kept)
  - _classify_controller overlay
  - build_proxy_recommendations (migrate-to-bnk, no action button status)
  - benchmarks path unchanged (regression: discover_all(target=...) still works)
"""

from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from services.proxy_discovery_service import (
    _classify_controller,
    _map_httproute_backends,
    _map_ingress_backends,
)
from services.scanner.recommendations import build_proxy_recommendations

# ---------------------------------------------------------------------------
# _classify_controller — overlay + generic fallback
# ---------------------------------------------------------------------------


class TestClassifyController:
    def test_known_nginx(self):
        pt, dn = _classify_controller("k8s.io/ingress-nginx")
        assert pt == "nginx"
        assert "NGINX" in dn

    def test_known_haproxy(self):
        pt, dn = _classify_controller("haproxy.org/ingress-controller")
        assert pt == "haproxy"

    def test_known_envoy(self):
        pt, dn = _classify_controller("gateway.envoyproxy.io/gatewayclass-controller")
        assert pt == "envoy"

    def test_known_f5_bnk_gateway_class(self):
        """BNK GatewayClass controller f5.io/... is classified as f5-bnk."""
        pt, dn = _classify_controller("f5.io/gateway-controller", kind="GatewayClass")
        assert pt == "f5-bnk"

    def test_known_f5_bnk_cne_gateway_class(self):
        """BNK GatewayClass controller cne.f5.io/... is classified as f5-bnk (cne marker)."""
        pt, dn = _classify_controller("cne.f5.io/gateway-controller", kind="GatewayClass")
        assert pt == "f5-bnk"

    def test_known_bnk_marker(self):
        pt, _ = _classify_controller("bnk.example.com/controller", kind="GatewayClass")
        assert pt == "f5-bnk"

    # --- CIS tests ---

    def test_cis_controller_ingress_class_is_not_bnk(self):
        """CIS IngressClass controller f5.com/cntr-ingress-svcs must be f5-cis, not f5-bnk."""
        pt, dn = _classify_controller("f5.com/cntr-ingress-svcs", kind="IngressClass")
        assert pt == "f5-cis"
        assert dn == "F5 CIS"

    def test_cis_ingress_class_is_bnk_false(self):
        """is_bnk must be False for f5-cis (the key regression check)."""
        pt, _ = _classify_controller("f5.com/cntr-ingress-svcs", kind="IngressClass")
        assert pt != "f5-bnk"

    def test_bnk_gateway_class_is_bnk_true(self):
        """BNK GatewayClass must still be classified as f5-bnk."""
        pt, _ = _classify_controller("f5.io/gateway-controller", kind="GatewayClass")
        assert pt == "f5-bnk"

    def test_cis_legacy_bare_f5_name_no_controller(self):
        """Legacy CIS IngressClass named 'f5' with no controller → f5-cis."""
        pt, dn = _classify_controller("", kind="IngressClass", class_name="f5")
        assert pt == "f5-cis"
        assert dn == "F5 CIS"

    def test_nginx_unaffected_by_kind(self):
        """nginx is still nginx regardless of kind."""
        pt, _ = _classify_controller("k8s.io/ingress-nginx", kind="IngressClass")
        assert pt == "nginx"
        pt2, _ = _classify_controller("k8s.io/ingress-nginx", kind="GatewayClass")
        assert pt2 == "nginx"

    def test_haproxy_unaffected_by_kind(self):
        """haproxy is still haproxy regardless of kind."""
        pt, _ = _classify_controller("haproxy.org/ingress-controller", kind="IngressClass")
        assert pt == "haproxy"

    def test_unknown_traefik_kept(self):
        """Traefik is not in the overlay — must be kept (D-019 generic fallback)."""
        pt, dn = _classify_controller("traefik.io/ingress-controller")
        assert pt == "traefik.io/ingress-controller"  # raw controller preserved
        # display_name is the last segment
        assert "traefik" in dn.lower() or pt == dn or "/" not in dn

    def test_unknown_kong_kept(self):
        pt, dn = _classify_controller("konghq.com/kic-gateway-controller")
        assert pt == "konghq.com/kic-gateway-controller"
        assert "kic-gateway-controller" in dn

    def test_unknown_contour_kept(self):
        pt, _ = _classify_controller("projectcontour.io/contour")
        # Must not be dropped — type is the raw controller string
        assert pt == "projectcontour.io/contour"

    def test_empty_string(self):
        pt, dn = _classify_controller("")
        assert isinstance(pt, str)
        assert isinstance(dn, str)


# ---------------------------------------------------------------------------
# _map_ingress_backends — pure logic, no I/O
# ---------------------------------------------------------------------------


def _make_ingress(class_name: str, rules_services: list[str], ns: str = "default"):
    """Build a minimal fake Ingress object."""
    paths = []
    for svc in rules_services:
        backend = MagicMock()
        backend.service = MagicMock()
        backend.service.name = svc
        path = MagicMock()
        path.backend = backend
        paths.append(path)

    rule = MagicMock()
    rule.http = MagicMock()
    rule.http.paths = paths

    spec = MagicMock()
    spec.ingress_class_name = class_name
    spec.rules = [rule]
    spec.default_backend = None

    meta = MagicMock()
    meta.namespace = ns
    meta.annotations = {}

    ing = MagicMock()
    ing.spec = spec
    ing.metadata = meta
    return ing


class TestMapIngressBackends:
    def test_returns_matching_backends(self):
        ing = _make_ingress("nginx", ["svc-a", "svc-b"], ns="prod")
        backends = _map_ingress_backends([ing], "nginx")
        svc_names = {b["service"] for b in backends}
        assert "svc-a" in svc_names
        assert "svc-b" in svc_names

    def test_skips_non_matching_class(self):
        ing = _make_ingress("haproxy", ["svc-ha"], ns="prod")
        backends = _map_ingress_backends([ing], "nginx")
        assert backends == []

    def test_deduplicates(self):
        # Two ingresses with the same backend
        ing1 = _make_ingress("nginx", ["shared-svc"])
        ing2 = _make_ingress("nginx", ["shared-svc"])
        backends = _map_ingress_backends([ing1, ing2], "nginx")
        assert len(backends) == 1

    def test_empty_ingress_list(self):
        assert _map_ingress_backends([], "nginx") == []


# ---------------------------------------------------------------------------
# _map_httproute_backends — pure logic
# ---------------------------------------------------------------------------


class TestMapHttprouteBackends:
    def test_returns_backends_for_attached_routes(self):
        route = {
            "metadata": {"namespace": "default"},
            "spec": {
                "parentRefs": [{"name": "my-gateway"}],
                "rules": [{"backendRefs": [{"name": "llm-svc", "namespace": "prod"}]}],
            },
        }
        backends = _map_httproute_backends([route], {"my-gateway"}, [])
        assert any(b["service"] == "llm-svc" for b in backends)

    def test_skips_routes_not_attached(self):
        route = {
            "metadata": {"namespace": "default"},
            "spec": {
                "parentRefs": [{"name": "other-gateway"}],
                "rules": [{"backendRefs": [{"name": "unrelated-svc"}]}],
            },
        }
        backends = _map_httproute_backends([route], {"my-gateway"}, [])
        assert backends == []

    def test_deduplicates(self):
        route1 = {
            "metadata": {"namespace": "default"},
            "spec": {
                "parentRefs": [{"name": "gw"}],
                "rules": [{"backendRefs": [{"name": "shared"}]}],
            },
        }
        route2 = {
            "metadata": {"namespace": "default"},
            "spec": {
                "parentRefs": [{"name": "gw"}],
                "rules": [{"backendRefs": [{"name": "shared"}]}],
            },
        }
        backends = _map_httproute_backends([route1, route2], {"gw"}, [])
        assert len(backends) == 1


# ---------------------------------------------------------------------------
# build_proxy_recommendations — Slice 5
# ---------------------------------------------------------------------------


def _proxy(proxy_type: str, display_name: str, *, is_bnk: bool = False, backends: int = 0) -> dict:
    return {
        "proxy_type": proxy_type,
        "display_name": display_name,
        "controller": f"k8s.io/{proxy_type}",
        "kind": "IngressClass",
        "found": True,
        "namespace": None,
        "proxy_url": None,
        "external_url": None,
        "backends": [{"service": f"svc-{i}", "namespace": "default", "via": "Ingress"} for i in range(backends)],
        "is_bnk": is_bnk,
        "details": {"ingress_class_name": proxy_type},
        "error": None,
    }


class TestBuildProxyRecommendations:
    def test_nginx_gets_rec(self):
        ep = {"status": "detected", "proxies": [_proxy("nginx", "NGINX Ingress")], "discovered_count": 1, "total_scanned": 1}
        recs = build_proxy_recommendations(ep)
        assert len(recs) == 1
        rec = recs[0]
        assert rec["id"].startswith("migrate-proxy-nginx")
        assert "Migrate" in rec["title"]
        # Critical: status must NOT be "deploy"
        assert rec["status"] == "investigate"
        assert rec["status"] != "deploy"

    def test_bnk_excluded(self):
        ep = {
            "status": "detected",
            "proxies": [
                _proxy("nginx", "NGINX Ingress"),
                _proxy("f5-bnk", "F5 BNK", is_bnk=True),
            ],
            "discovered_count": 2,
            "total_scanned": 2,
        }
        recs = build_proxy_recommendations(ep)
        # Only nginx gets a rec; BNK is excluded
        assert len(recs) == 1
        assert "nginx" in recs[0]["id"]

    def test_no_proxies_returns_empty(self):
        ep = {"status": "none", "proxies": [], "discovered_count": 0, "total_scanned": 0}
        recs = build_proxy_recommendations(ep)
        assert recs == []

    def test_none_returns_empty(self):
        assert build_proxy_recommendations(None) == []

    def test_unknown_controller_gets_rec(self):
        """Traefik (unknown) must also get a migration recommendation."""
        ep = {
            "status": "detected",
            "proxies": [_proxy("traefik.io/ingress-controller", "traefik.io/ingress-controller")],
            "discovered_count": 1,
            "total_scanned": 1,
        }
        recs = build_proxy_recommendations(ep)
        assert len(recs) == 1
        assert all(rec["status"] == "investigate" for rec in recs)

    def test_backend_count_in_description(self):
        ep = {
            "status": "detected",
            "proxies": [_proxy("nginx", "NGINX Ingress", backends=3)],
            "discovered_count": 1,
            "total_scanned": 1,
        }
        recs = build_proxy_recommendations(ep)
        assert "3 backend" in recs[0]["description"]

    def test_category_is_optimization(self):
        ep = {"status": "detected", "proxies": [_proxy("nginx", "NGINX Ingress")], "discovered_count": 1, "total_scanned": 1}
        recs = build_proxy_recommendations(ep)
        assert recs[0]["category"] == "optimization"

    def test_module_is_none(self):
        ep = {"status": "detected", "proxies": [_proxy("nginx", "NGINX Ingress")], "discovered_count": 1, "total_scanned": 1}
        recs = build_proxy_recommendations(ep)
        assert recs[0]["module"] is None


# ---------------------------------------------------------------------------
# ProxyDiscoveryService.discover_inventory — read-only guarantee
# ---------------------------------------------------------------------------


class TestDiscoverInventory:
    """Test that inventory mode never touches ProxyDeployment records."""

    def _make_api_client(self, ingress_classes=None, gateway_classes=None, ingresses=None, httproutes=None, gateways=None):
        """Build a fake api_client that returns controlled K8s data."""
        from unittest.mock import MagicMock, patch

        api_client = MagicMock()
        ingress_classes = ingress_classes or []
        gateway_classes = gateway_classes or []
        ingresses = ingresses or []
        httproutes = httproutes or []
        gateways = gateways or []

        def _fake_list_cluster_custom(group, version, plural, **_kwargs):
            resp = MagicMock()
            if plural == "ingressclasses":
                resp.get = lambda key, default=None: ingress_classes if key == "items" else default
                return resp
            elif plural == "gatewayclasses":
                resp.get = lambda key, default=None: gateway_classes if key == "items" else default
                return resp
            elif plural == "httproutes":
                resp.get = lambda key, default=None: httproutes if key == "items" else default
                return resp
            elif plural == "gateways":
                resp.get = lambda key, default=None: gateways if key == "items" else default
                return resp
            resp.get = lambda key, default=None: default
            return resp

        # We'll patch the module-level helpers directly in the test
        return api_client

    def test_inventory_returns_ingress_class_items(self):
        """Inventory discovers IngressClasses and returns items."""
        from unittest.mock import MagicMock, patch

        from services.proxy_discovery_service import ProxyDiscoveryService

        db = MagicMock()

        nginx_ic = {
            "metadata": {"name": "nginx"},
            "spec": {"controller": "k8s.io/ingress-nginx"},
        }

        api_client = MagicMock()

        with patch("services.proxy_discovery_service._safe_list_cluster_custom") as mock_list_cluster, \
             patch("services.proxy_discovery_service._safe_list_all_custom") as mock_list_all, \
             patch("services.proxy_discovery_service._safe_list_all_ingresses") as mock_ingresses:

            def list_cluster_side(custom, *, group, version, plural):
                if plural == "ingressclasses":
                    return [nginx_ic]
                return []  # gatewayclasses

            mock_list_cluster.side_effect = list_cluster_side
            mock_list_all.return_value = []  # no httproutes/gateways
            mock_ingresses.return_value = []

            svc = ProxyDiscoveryService(db)
            items = svc.discover_inventory(api_client)

        assert len(items) == 1
        assert items[0]["proxy_type"] == "nginx"
        assert items[0]["kind"] == "IngressClass"
        assert items[0]["found"] is True

    def test_inventory_never_writes_proxy_deployment(self):
        """Read-only guarantee: _sync_proxy_records must never be called."""
        from unittest.mock import MagicMock, patch

        from services.proxy_discovery_service import ProxyDiscoveryService

        db = MagicMock()
        api_client = MagicMock()

        with patch("services.proxy_discovery_service._safe_list_cluster_custom") as mock_list_cluster, \
             patch("services.proxy_discovery_service._safe_list_all_custom") as mock_list_all, \
             patch("services.proxy_discovery_service._safe_list_all_ingresses") as mock_ingresses, \
             patch.object(ProxyDiscoveryService, "_sync_proxy_records") as mock_sync:

            mock_list_cluster.return_value = []
            mock_list_all.return_value = []
            mock_ingresses.return_value = []

            svc = ProxyDiscoveryService(db)
            svc.discover_inventory(api_client)

        mock_sync.assert_not_called()

    def test_unknown_controller_not_dropped(self):
        """D-019: Traefik IngressClass must appear in inventory output."""
        from unittest.mock import MagicMock, patch

        from services.proxy_discovery_service import ProxyDiscoveryService

        db = MagicMock()
        api_client = MagicMock()

        traefik_ic = {
            "metadata": {"name": "traefik"},
            "spec": {"controller": "traefik.io/ingress-controller"},
        }

        with patch("services.proxy_discovery_service._safe_list_cluster_custom") as mock_list_cluster, \
             patch("services.proxy_discovery_service._safe_list_all_custom") as mock_list_all, \
             patch("services.proxy_discovery_service._safe_list_all_ingresses") as mock_ingresses:

            def list_cluster_side(custom, *, group, version, plural):
                if plural == "ingressclasses":
                    return [traefik_ic]
                return []

            mock_list_cluster.side_effect = list_cluster_side
            mock_list_all.return_value = []
            mock_ingresses.return_value = []

            svc = ProxyDiscoveryService(db)
            items = svc.discover_inventory(api_client)

        assert len(items) == 1
        # Raw controller string preserved as proxy_type (generic fallback)
        assert items[0]["proxy_type"] == "traefik.io/ingress-controller"
        assert items[0]["is_bnk"] is False

    def test_discover_all_target_mode_unchanged(self):
        """Regression: discover_all(target=...) still dispatches to _scan_* path."""
        from unittest.mock import MagicMock, patch

        from services.proxy_discovery_service import ProxyDiscoveryService

        db = MagicMock()
        target = MagicMock()
        target.cluster_id = 42

        with patch.object(ProxyDiscoveryService, "__init__", return_value=None):
            svc = ProxyDiscoveryService.__new__(ProxyDiscoveryService)
            svc.db = db
            svc.k8s = MagicMock()
            svc.k8s.get_cluster.return_value = MagicMock()
            svc.k8s.load_kubeconfig.return_value = MagicMock()

            with patch.object(svc, "_scan_envoy", return_value=MagicMock(proxy_type="envoy", found=False, to_dict=lambda: {})), \
                 patch.object(svc, "_scan_nginx", return_value=MagicMock(proxy_type="nginx", found=False, to_dict=lambda: {})), \
                 patch.object(svc, "_scan_haproxy", return_value=MagicMock(proxy_type="haproxy", found=False, to_dict=lambda: {})), \
                 patch.object(svc, "_scan_f5_bnk", return_value=MagicMock(proxy_type="f5-bnk", found=False, to_dict=lambda: {})), \
                 patch.object(svc, "_scan_nodeport", return_value=MagicMock(proxy_type="nodeport", found=False, to_dict=lambda: {})), \
                 patch.object(svc, "_sync_proxy_records") as mock_sync:

                result = svc.discover_all(target, auto_create=True)

        # _sync_proxy_records should have been called (target mode with auto_create=True)
        mock_sync.assert_called_once()

    def test_discover_all_inventory_mode_no_sync(self):
        """discover_all(target=None, cluster_id=N) must never call _sync_proxy_records."""
        from unittest.mock import MagicMock, patch

        from services.proxy_discovery_service import ProxyDiscoveryService

        db = MagicMock()

        with patch.object(ProxyDiscoveryService, "__init__", return_value=None):
            svc = ProxyDiscoveryService.__new__(ProxyDiscoveryService)
            svc.db = db
            svc.k8s = MagicMock()
            svc.k8s.get_cluster.return_value = MagicMock()
            svc.k8s.load_kubeconfig.return_value = MagicMock()

            with patch.object(svc, "discover_inventory", return_value=[]) as mock_inv, \
                 patch.object(svc, "_sync_proxy_records") as mock_sync:

                svc.discover_all(target=None, cluster_id=99)

        mock_inv.assert_called_once()
        mock_sync.assert_not_called()


# ---------------------------------------------------------------------------
# ClusterScanner.scan — integration wiring for existing_proxies (AC7)
# ---------------------------------------------------------------------------


class TestClusterScannerProxyIntegration:
    """
    Prove that the collector wiring in services/scanner/__init__.py
    correctly flows ProxyDiscoveryService.discover_inventory results into
    the scan response under result["prerequisites"]["existing_proxies"].
    """

    def _minimal_fetch_data(self) -> dict:
        """Return a minimal fetch_scan_data payload so analyzer functions don't crash."""
        from unittest.mock import MagicMock
        return {
            "version_info": {},
            "nodes": [],
            "namespaces": [],
            "crds": MagicMock(items=[]),
            "crd_names": set(),
            "crd_groups": set(),
            "cert_manager_pods": [],
            "kube_system_pods": [],
            "daemonsets": [],
            "storage_classes": [],
            "gateways": [],
            "gatewayclasses": [],
            "dpf_operator_configs": [],
            "dpudevices": [],
            "dpusets": [],
            "dpuclusters": [],
            "dpuservices": [],
            "bfbs": [],
            "kamaji_pods": [],
            "kamaji_tcps": [],
            "f5_tenant_pods": [],
            "f5_utils_pods": [],
            "helm_releases": [],
            "cneinstances": [],
            "vlans": [],
            "cis_controllers": [],
            "cis_virtualservers": [],
            "cis_transportservers": [],
            "cis_ingresslinks": [],
        }

    def test_scan_includes_existing_proxies_from_discovery(self):
        """
        When ProxyDiscoveryService.discover_inventory returns one non-BNK proxy,
        scan() must surface it in result["prerequisites"]["existing_proxies"]
        with discovered_count==1 and status=="detected".
        """
        from unittest.mock import MagicMock, patch

        from services.scanner import ClusterScanner

        db = MagicMock()
        cluster = MagicMock()
        cluster.name = "test-cluster"
        cluster.enabled_prerequisites = None

        mock_platform_ctx = MagicMock()
        mock_platform_ctx.detected_platform_profile = "generic"
        mock_platform_ctx.to_dict.return_value = {"detected_platform_profile": "generic"}

        found_proxy = {
            "proxy_type": "nginx",
            "display_name": "NGINX Ingress",
            "controller": "k8s.io/ingress-nginx",
            "kind": "IngressClass",
            "found": True,
            "namespace": None,
            "proxy_url": None,
            "external_url": None,
            "backends": [],
            "is_bnk": False,
            "details": {},
            "error": None,
        }

        with patch("services.scanner.KubernetesService") as mock_k8s_cls, \
             patch("services.scanner.fetch_scan_data", return_value=self._minimal_fetch_data()), \
             patch("services.scanner.PlatformContextService.apply_cluster_context", return_value=mock_platform_ctx), \
             patch("services.proxy_discovery_service.ProxyDiscoveryService.discover_inventory", return_value=[found_proxy]):

            mock_k8s = mock_k8s_cls.return_value
            mock_k8s.get_cluster.return_value = cluster
            mock_k8s.load_kubeconfig.return_value = MagicMock()

            scanner = ClusterScanner(db)
            result = scanner.scan(cluster_id=1)

        ep = result["prerequisites"]["existing_proxies"]
        assert ep["discovered_count"] == 1
        assert ep["status"] == "detected"
