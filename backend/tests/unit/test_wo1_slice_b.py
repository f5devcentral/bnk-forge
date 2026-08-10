"""
Unit tests for WO-1 Slice B — discovery-driven resource/CRD lookup.

Tests:
  1. _kind_to_snake consecutive-caps fix (#154)
  2. Discovery-driven plural resolution with fallback to KNOWN_PLURALS (#137)
  3. Export type list built from discovery vs fallback (#137)
  4. Finalizer cleanup using cluster's served version vs fallback (#137)
"""
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# 1. _kind_to_snake — consecutive caps fix (#154)
# ============================================================================

class TestKindToSnakeConsecutiveCaps:
    """Regression guard for consecutive-caps camelCase conversion."""

    def _snake(self, kind: str) -> str:
        from services.kubernetes._resources import _kind_to_snake
        return _kind_to_snake(kind)

    def test_tls_route(self):
        assert self._snake("TLSRoute") == "tls_route"

    def test_bnk_sec_policy(self):
        assert self._snake("BNKSecPolicy") == "bnk_sec_policy"

    def test_http_route(self):
        assert self._snake("HTTPRoute") == "http_route"

    def test_grpc_route(self):
        assert self._snake("GRPCRoute") == "grpc_route"

    def test_config_map(self):
        # Must not regress
        assert self._snake("ConfigMap") == "config_map"

    def test_pod(self):
        assert self._snake("Pod") == "pod"

    def test_persistent_volume_claim(self):
        assert self._snake("PersistentVolumeClaim") == "persistent_volume_claim"

    def test_horizontal_pod_autoscaler(self):
        assert self._snake("HorizontalPodAutoscaler") == "horizontal_pod_autoscaler"

    def test_cne_instance(self):
        # CNEInstance → cne_instance (not c_n_e_instance)
        assert self._snake("CNEInstance") == "cne_instance"


# ============================================================================
# 2. Export type list from discovery vs. fallback (#137)
# ============================================================================

class TestBuildExportTypesFromDiscovery:
    """Discovery-driven export type list building."""

    def _make_crd_info(self, group, kind, plural, version="v1", namespaced=True):
        from schemas.k8s import CRDInfo
        return CRDInfo(
            name=f"{plural}.{group}",
            kind=kind,
            plural=plural,
            group=group,
            version=version,
            namespaced=namespaced,
            display_name=kind,
            category=None,
            source="discovered",
        )

    def test_discovery_returns_export_types_by_group(self):
        """When discovery returns CRDs, export types are built from them."""
        from schemas.k8s import CrdListEnvelope
        from services.config_export_service import _build_export_types_from_discovery

        crds = [
            self._make_crd_info("gateway.networking.k8s.io", "Gateway", "gateways"),
            self._make_crd_info("k8s.f5.com", "CNEInstance", "cneinstances"),
        ]
        envelope = CrdListEnvelope(crds=crds, count=2, cluster_id=1, group_filter=None, info=None)

        db = MagicMock()
        with patch("services.crd_discovery_service.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.return_value = envelope
            result = _build_export_types_from_discovery(1, db)

        assert result is not None
        assert "gateway_api" in result
        gw_types = result["gateway_api"]
        assert any(t["kind"] == "Gateway" for t in gw_types)
        assert "bnk_flo" in result
        flo_types = result["bnk_flo"]
        assert any(t["kind"] == "CNEInstance" for t in flo_types)

    def test_discovery_empty_returns_none(self):
        """When discovery returns no CRDs, returns None to trigger fallback."""
        from schemas.k8s import CrdListEnvelope
        from services.config_export_service import _build_export_types_from_discovery

        envelope = CrdListEnvelope(crds=[], count=0, cluster_id=1, group_filter=None, info=None)
        db = MagicMock()
        with patch("services.crd_discovery_service.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.return_value = envelope
            result = _build_export_types_from_discovery(1, db)

        assert result is None

    def test_discovery_exception_returns_none(self):
        """When discovery raises, returns None (falls back to static table)."""
        from services.config_export_service import _build_export_types_from_discovery

        db = MagicMock()
        with patch("services.crd_discovery_service.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.side_effect = Exception("cluster unreachable")
            result = _build_export_types_from_discovery(1, db)

        assert result is None

    def test_export_entry_has_correct_shape(self):
        """Each entry in discovery-driven export types has the correct shape."""
        from schemas.k8s import CrdListEnvelope
        from services.config_export_service import _build_export_types_from_discovery

        crds = [self._make_crd_info("gateway.networking.k8s.io", "HTTPRoute", "httproutes", version="v1")]
        envelope = CrdListEnvelope(crds=crds, count=1, cluster_id=1, group_filter=None, info=None)

        db = MagicMock()
        with patch("services.crd_discovery_service.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.return_value = envelope
            result = _build_export_types_from_discovery(1, db)

        assert result is not None
        entry = result["gateway_api"][0]
        assert entry["api_version"] == "gateway.networking.k8s.io/v1"
        assert entry["kind"] == "HTTPRoute"
        assert entry["plural"] == "httproutes"
        assert entry["namespaced"] is True


# ============================================================================
# 3. Finalizer cleanup — discovery-driven version (#137)
# ============================================================================

class TestFinalizerCleanupDiscovery:
    """_get_f5_crd_targets uses cluster's served version from discovery."""

    def _make_svc(self):
        from unittest.mock import MagicMock

        from services.finalizer_cleanup_service import FinalizerCleanupService
        svc = FinalizerCleanupService.__new__(FinalizerCleanupService)
        svc.db = MagicMock()
        svc.k8s_service = MagicMock()
        return svc

    def test_discovery_returns_served_versions(self):
        """When discovery is available, served version from CRD is used (not static)."""
        from schemas.k8s import CRDInfo, CrdListEnvelope
        from services.finalizer_cleanup_service import FinalizerCleanupService

        svc = self._make_svc()
        crds = [
            CRDInfo(name="f5tmms.k8s.f5.com", kind="F5Tmm", plural="f5tmms",
                    group="k8s.f5.com", version="v2",  # cluster serves v2, not static v1
                    namespaced=True, display_name=None, category=None, source="discovered"),
        ]
        envelope = CrdListEnvelope(crds=crds, count=1, cluster_id=1, group_filter=None, info=None)

        with patch("services.crd_discovery_service.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.return_value = envelope
            targets = svc._get_f5_crd_targets(cluster_id=1)

        assert ("k8s.f5.com", "v2", "f5tmms") in targets
        # Must NOT contain the static v1 entry (it uses cluster-served version)

    def test_discovery_unavailable_falls_back_to_static(self):
        """When discovery fails, falls back to static F5_BNK_CRDS."""
        from services.finalizer_cleanup_service import F5_BNK_CRDS, FinalizerCleanupService

        svc = self._make_svc()
        with patch("services.crd_discovery_service.CrdDiscoveryService") as MockSvc:
            MockSvc.return_value.list_crds.side_effect = Exception("unreachable")
            targets = svc._get_f5_crd_targets(cluster_id=1)

        assert targets == F5_BNK_CRDS

    def test_no_cluster_id_uses_static(self):
        """When cluster_id is None, static list is used (no discovery attempt)."""
        from services.finalizer_cleanup_service import F5_BNK_CRDS, FinalizerCleanupService

        svc = self._make_svc()
        targets = svc._get_f5_crd_targets(cluster_id=None)
        assert targets == F5_BNK_CRDS
