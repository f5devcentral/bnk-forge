"""
BC-ADR494-B: Component tests for ADR-494 Phase B — running release discovery.

Covers:
  - get_or_create_observed: dedup guard (repeated calls → single row)
  - get_or_create_observed: first call creates the observed row
  - resolve_ga hit path: known FLO version → correct release id, no new row
  - resolve_ga miss path: unknown FLO version → observed row upserted
  - DriftService._compute_release_drift: all four status paths
  - ClusterScanner write-back: running_release_id set on cluster after scan
"""

from unittest.mock import MagicMock, patch

import pytest

from models.bnk_release import BnkRelease
from models.enums import ReleaseSourceType
from services.release_registry_service import ReleaseRegistryService

# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_active_release(db, ga_label="BNK 2.3 GA", flo_prefix="2.21") -> BnkRelease:
    rel = BnkRelease(
        ga_label=ga_label,
        product_line="BNK",
        flo_version_prefix=flo_prefix,
        source_type=ReleaseSourceType.CLOUDDOCS,
        is_active=True,
    )
    db.add(rel)
    db.flush()
    return rel


# ---------------------------------------------------------------------------
# get_or_create_observed — dedup
# ---------------------------------------------------------------------------

class TestGetOrCreateObserved:
    def test_creates_observed_row_first_call(self, db):
        svc = ReleaseRegistryService(db)
        row_id = svc.get_or_create_observed("2.99.0-0.0.1")

        row = db.query(BnkRelease).filter_by(id=row_id).one()
        assert row.source_type == ReleaseSourceType.OBSERVED
        assert row.flo_version_min == "2.99.0-0.0.1"
        assert row.is_active is False
        assert row.flo_version_prefix is None
        assert row.manifest_version is None

    def test_dedup_second_call_same_version_no_new_row(self, db):
        svc = ReleaseRegistryService(db)
        id1 = svc.get_or_create_observed("2.99.1-0.0.5")
        id2 = svc.get_or_create_observed("2.99.1-0.0.5")

        assert id1 == id2
        rows = db.query(BnkRelease).filter(
            BnkRelease.source_type == ReleaseSourceType.OBSERVED,
            BnkRelease.flo_version_min == "2.99.1-0.0.5",
        ).all()
        assert len(rows) == 1

    def test_different_versions_create_separate_rows(self, db):
        svc = ReleaseRegistryService(db)
        id_a = svc.get_or_create_observed("2.100.0-0.0.1")
        id_b = svc.get_or_create_observed("2.100.1-0.0.1")

        assert id_a != id_b


# ---------------------------------------------------------------------------
# resolve_ga hit vs miss interaction with get_or_create_observed
# ---------------------------------------------------------------------------

class TestResolveGaHitVsMiss:
    def test_resolve_ga_hit_returns_active_row_no_upsert(self, db):
        """Known FLO version resolves to the active row; no observed row created."""
        active = _seed_active_release(db, ga_label="BNK 2.3 GA", flo_prefix="2.21")
        svc = ReleaseRegistryService(db)

        ga = svc.resolve_ga(flo_version="2.21.13-0.0.28")
        assert ga is not None
        assert ga.release_id == active.id

        # No observed row should have been created
        observed_count = db.query(BnkRelease).filter(
            BnkRelease.source_type == ReleaseSourceType.OBSERVED
        ).count()
        assert observed_count == 0

    def test_resolve_ga_miss_then_upsert_creates_observed_row(self, db):
        """Unknown FLO version: resolve_ga → None, then get_or_create_observed inserts a row."""
        svc = ReleaseRegistryService(db)

        ga = svc.resolve_ga(flo_version="9.99.0-0.0.1")
        assert ga is None

        row_id = svc.get_or_create_observed("9.99.0-0.0.1")
        row = db.query(BnkRelease).filter_by(id=row_id).one()
        assert row.source_type == ReleaseSourceType.OBSERVED
        assert row.flo_version_min == "9.99.0-0.0.1"
        assert row.is_active is False

    def test_observed_row_not_matched_by_resolve_ga(self, db):
        """Observed rows (is_active=False) must never be returned by resolve_ga."""
        svc = ReleaseRegistryService(db)
        svc.get_or_create_observed("5.55.0-0.0.1")

        ga = svc.resolve_ga(flo_version="5.55.0-0.0.1")
        assert ga is None  # is_active=False rows are invisible to resolve_ga


# ---------------------------------------------------------------------------
# DriftService._compute_release_drift — all four status paths
# ---------------------------------------------------------------------------

class TestComputeReleaseDrift:
    def test_not_forge_deployed_when_no_deployable_id(self, db):
        from services.drift_service import DriftService

        cluster = MagicMock()
        cluster.deployable_release_id = None
        cluster.running_release_id = None

        svc = DriftService(db)
        result = svc._compute_release_drift(cluster)
        assert result["status"] == "not_forge_deployed"

    def test_undiscovered_when_no_running_id(self, db):
        from models.bnk_deployable_release import BnkDeployableRelease
        from services.drift_service import DriftService

        # Need a real active BnkRelease row for resolve_ga to find.
        active_rel = _seed_active_release(db, ga_label="BNK 2.3 GA", flo_prefix="2.21")

        deployable = BnkDeployableRelease(
            name="bnk-2.3",
            display_name="BNK 2.3",
            bnk_manifest_version="2.3.0",
            bnk_cr_kind="BNKGatewayClass",
            flo_version="2.21.0",
            k8s_version="1.30",
            doca_version="2.8.0",
            containerd_version="1.7.0",
            runc_version="1.1.0",
            calico_version="3.28.0",
            cert_manager_version="1.15.0",
            gateway_api_version="1.1.0",
            multus_version="4.1.0",
            sriov_version="1.0.0",
            storage_class_type="local-path",
            storage_provisioner="rancher.io/local-path",
            bnk_release_id=active_rel.id,
        )
        db.add(deployable)
        db.flush()

        cluster = MagicMock()
        cluster.deployable_release_id = deployable.id
        cluster.running_release_id = None

        svc = DriftService(db)
        result = svc._compute_release_drift(cluster)
        assert result["status"] == "undiscovered"
        assert result["deployed_release_id"] == active_rel.id
        assert result["running_release_id"] is None

    def test_in_sync_when_same_release_ids(self, db):
        from models.bnk_deployable_release import BnkDeployableRelease
        from services.drift_service import DriftService

        active_rel = _seed_active_release(db, ga_label="BNK 2.3 GA", flo_prefix="2.21")

        deployable = BnkDeployableRelease(
            name="bnk-2.3-b",
            display_name="BNK 2.3",
            bnk_manifest_version="2.3.0",
            bnk_cr_kind="BNKGatewayClass",
            flo_version="2.21.0",
            k8s_version="1.30",
            doca_version="2.8.0",
            containerd_version="1.7.0",
            runc_version="1.1.0",
            calico_version="3.28.0",
            cert_manager_version="1.15.0",
            gateway_api_version="1.1.0",
            multus_version="4.1.0",
            sriov_version="1.0.0",
            storage_class_type="local-path",
            storage_provisioner="rancher.io/local-path",
            bnk_release_id=active_rel.id,
        )
        db.add(deployable)
        db.flush()

        cluster = MagicMock()
        cluster.deployable_release_id = deployable.id
        cluster.running_release_id = active_rel.id  # same row id

        svc = DriftService(db)
        result = svc._compute_release_drift(cluster)
        assert result["status"] == "in_sync"
        assert result["deployed_release_id"] == active_rel.id
        assert result["running_release_id"] == active_rel.id

    def test_drifted_when_different_release_ids(self, db):
        from models.bnk_deployable_release import BnkDeployableRelease
        from services.drift_service import DriftService

        rel_23 = _seed_active_release(db, ga_label="BNK 2.3 GA", flo_prefix="2.21")
        rel_24 = _seed_active_release(db, ga_label="BNK 2.4 GA", flo_prefix="2.25")

        deployable = BnkDeployableRelease(
            name="bnk-2.3-c",
            display_name="BNK 2.3",
            bnk_manifest_version="2.3.0",
            bnk_cr_kind="BNKGatewayClass",
            flo_version="2.21.0",
            k8s_version="1.30",
            doca_version="2.8.0",
            containerd_version="1.7.0",
            runc_version="1.1.0",
            calico_version="3.28.0",
            cert_manager_version="1.15.0",
            gateway_api_version="1.1.0",
            multus_version="4.1.0",
            sriov_version="1.0.0",
            storage_class_type="local-path",
            storage_provisioner="rancher.io/local-path",
            bnk_release_id=rel_23.id,
        )
        db.add(deployable)
        db.flush()

        cluster = MagicMock()
        cluster.deployable_release_id = deployable.id
        cluster.running_release_id = rel_24.id  # different release line

        svc = DriftService(db)
        result = svc._compute_release_drift(cluster)
        assert result["status"] == "drifted"
        assert result["deployed_release_id"] == rel_23.id
        assert result["running_release_id"] == rel_24.id


# ---------------------------------------------------------------------------
# ClusterScanner write-back — running_release_id set on scan
# ---------------------------------------------------------------------------

class TestScannerRunningReleaseWriteback:
    def _make_scan_data(self, flo_version: str | None = "2.21.13-0.0.28") -> dict:
        """Minimal scan data dict with a FLO version in bnk_install."""
        flo_info: dict = {}
        if flo_version is not None:
            flo_info = {"version": flo_version}
        return {
            "version_info": {},
            "nodes": [],
            "namespaces": [],
            "crds": [],
            "crd_names": set(),
            "crd_groups": set(),
            "cert_manager_pods": [],
            "helm_releases": [],
            "kube_system_pods": [],
            "daemonsets": [],
            "storage_classes": [],
            "gateways": [],
            "gatewayclasses": [],
            "f5_tenant_pods": [],
            "f5_utils_pods": [],
            "dpf_operator_configs": [],
            "dpudevices": [],
            "dpusets": [],
            "dpuclusters": [],
            "dpuservices": [],
            "bfbs": [],
            "kamaji_pods": [],
            "kamaji_tcps": [],
            "cis_controllers": [],
            "cis_virtualservers": [],
            "cis_transportservers": [],
            "cis_ingresslinks": [],
            "cis_as3_configmaps": [],
            "cis_f5_ingresses": [],
            "openshift_routes": [],
            "cneinstances": [],
            "vlans": [],
            # flo key is embedded in bnk_install, not here directly
            "_flo_version_for_test": flo_version,
        }

    def test_known_flo_version_sets_running_release_id(self, db, make_k8s_cluster):
        """resolve_ga hit → running_release_id set to the matched row, no observed upsert."""
        active_rel = _seed_active_release(db, ga_label="BNK 2.3 GA", flo_prefix="2.21")
        cluster = make_k8s_cluster()

        from services.scanner import ClusterScanner
        scanner = ClusterScanner(db)

        # bnk_install dict with flo version that will match prefix "2.21"
        bnk_install = {"flo": {"version": "2.21.13-0.0.28"}}
        platform_ctx = MagicMock()
        platform_ctx.to_dict.return_value = {}
        platform_ctx.detected_platform_profile = "generic"

        with patch.object(scanner.k8s_service, "get_cluster", return_value=cluster), \
             patch.object(scanner.k8s_service, "load_kubeconfig", return_value=MagicMock()), \
             patch("services.scanner.fetch_scan_data", return_value={
                 "version_info": {}, "nodes": [], "namespaces": [], "crds": [],
                 "crd_names": set(), "crd_groups": set(), "cert_manager_pods": [],
                 "helm_releases": [], "kube_system_pods": [], "daemonsets": [],
                 "storage_classes": [], "gateways": [], "gatewayclasses": [],
                 "f5_tenant_pods": [], "f5_utils_pods": [], "dpf_operator_configs": [],
                 "dpudevices": [], "dpusets": [], "dpuclusters": [], "dpuservices": [],
                 "bfbs": [], "kamaji_pods": [], "kamaji_tcps": [], "cis_controllers": [],
                 "cis_virtualservers": [], "cis_transportservers": [], "cis_ingresslinks": [],
                 "cis_as3_configmaps": [], "cis_f5_ingresses": [], "openshift_routes": [],
                 "cneinstances": [], "vlans": [],
             }), \
             patch("services.scanner.analyze_bnk_install", return_value=bnk_install), \
             patch("services.scanner.PlatformContextService.apply_cluster_context", return_value=platform_ctx), \
             patch("services.scanner.analyze_cluster_info", return_value={}), \
             patch("services.scanner.analyze_cert_manager", return_value={}), \
             patch("services.scanner.analyze_multus", return_value={}), \
             patch("services.scanner.analyze_sriov", return_value={}), \
             patch("services.scanner.analyze_hugepages", return_value={}), \
             patch("services.scanner.analyze_storage", return_value={}), \
             patch("services.scanner.analyze_gateway_api", return_value={}), \
             patch("services.scanner.analyze_dpf", return_value={}), \
             patch("services.scanner.analyze_kamaji", return_value={}), \
             patch("services.scanner.analyze_cis", return_value={}), \
             patch("services.scanner.build_recommendations", return_value=[]), \
             patch("services.scanner.build_proxy_recommendations", return_value=[]):
            scanner.scan(cluster.id)

        db.refresh(cluster)
        assert cluster.running_release_id == active_rel.id
        # No observed row should have been created
        assert db.query(BnkRelease).filter(
            BnkRelease.source_type == ReleaseSourceType.OBSERVED
        ).count() == 0

    def test_unknown_flo_version_upserts_observed_and_sets_running_release_id(self, db, make_k8s_cluster):
        """resolve_ga miss → observed row upserted, running_release_id set to it."""
        cluster = make_k8s_cluster()

        from services.scanner import ClusterScanner
        scanner = ClusterScanner(db)

        bnk_install = {"flo": {"version": "9.99.99-0.0.1"}}
        platform_ctx = MagicMock()
        platform_ctx.to_dict.return_value = {}
        platform_ctx.detected_platform_profile = "generic"

        with patch.object(scanner.k8s_service, "get_cluster", return_value=cluster), \
             patch.object(scanner.k8s_service, "load_kubeconfig", return_value=MagicMock()), \
             patch("services.scanner.fetch_scan_data", return_value={
                 "version_info": {}, "nodes": [], "namespaces": [], "crds": [],
                 "crd_names": set(), "crd_groups": set(), "cert_manager_pods": [],
                 "helm_releases": [], "kube_system_pods": [], "daemonsets": [],
                 "storage_classes": [], "gateways": [], "gatewayclasses": [],
                 "f5_tenant_pods": [], "f5_utils_pods": [], "dpf_operator_configs": [],
                 "dpudevices": [], "dpusets": [], "dpuclusters": [], "dpuservices": [],
                 "bfbs": [], "kamaji_pods": [], "kamaji_tcps": [], "cis_controllers": [],
                 "cis_virtualservers": [], "cis_transportservers": [], "cis_ingresslinks": [],
                 "cis_as3_configmaps": [], "cis_f5_ingresses": [], "openshift_routes": [],
                 "cneinstances": [], "vlans": [],
             }), \
             patch("services.scanner.analyze_bnk_install", return_value=bnk_install), \
             patch("services.scanner.PlatformContextService.apply_cluster_context", return_value=platform_ctx), \
             patch("services.scanner.analyze_cluster_info", return_value={}), \
             patch("services.scanner.analyze_cert_manager", return_value={}), \
             patch("services.scanner.analyze_multus", return_value={}), \
             patch("services.scanner.analyze_sriov", return_value={}), \
             patch("services.scanner.analyze_hugepages", return_value={}), \
             patch("services.scanner.analyze_storage", return_value={}), \
             patch("services.scanner.analyze_gateway_api", return_value={}), \
             patch("services.scanner.analyze_dpf", return_value={}), \
             patch("services.scanner.analyze_kamaji", return_value={}), \
             patch("services.scanner.analyze_cis", return_value={}), \
             patch("services.scanner.build_recommendations", return_value=[]), \
             patch("services.scanner.build_proxy_recommendations", return_value=[]):
            scanner.scan(cluster.id)

        db.refresh(cluster)
        assert cluster.running_release_id is not None
        observed = db.query(BnkRelease).filter_by(id=cluster.running_release_id).one()
        assert observed.source_type == ReleaseSourceType.OBSERVED
        assert observed.flo_version_min == "9.99.99-0.0.1"
        assert observed.is_active is False

    def test_repeated_scan_does_not_duplicate_observed_row(self, db, make_k8s_cluster):
        """Idempotency: two scans of the same unknown FLO version → exactly one observed row."""
        cluster = make_k8s_cluster()

        from services.scanner import ClusterScanner

        bnk_install = {"flo": {"version": "8.88.88-0.0.1"}}
        platform_ctx = MagicMock()
        platform_ctx.to_dict.return_value = {}
        platform_ctx.detected_platform_profile = "generic"

        fetch_data = {
            "version_info": {}, "nodes": [], "namespaces": [], "crds": [],
            "crd_names": set(), "crd_groups": set(), "cert_manager_pods": [],
            "helm_releases": [], "kube_system_pods": [], "daemonsets": [],
            "storage_classes": [], "gateways": [], "gatewayclasses": [],
            "f5_tenant_pods": [], "f5_utils_pods": [], "dpf_operator_configs": [],
            "dpudevices": [], "dpusets": [], "dpuclusters": [], "dpuservices": [],
            "bfbs": [], "kamaji_pods": [], "kamaji_tcps": [], "cis_controllers": [],
            "cis_virtualservers": [], "cis_transportservers": [], "cis_ingresslinks": [],
            "cis_as3_configmaps": [], "cis_f5_ingresses": [], "openshift_routes": [],
            "cneinstances": [], "vlans": [],
        }

        for _ in range(2):
            scanner = ClusterScanner(db)
            with patch.object(scanner.k8s_service, "get_cluster", return_value=cluster), \
                 patch.object(scanner.k8s_service, "load_kubeconfig", return_value=MagicMock()), \
                 patch("services.scanner.fetch_scan_data", return_value=fetch_data), \
                 patch("services.scanner.analyze_bnk_install", return_value=bnk_install), \
                 patch("services.scanner.PlatformContextService.apply_cluster_context", return_value=platform_ctx), \
                 patch("services.scanner.analyze_cluster_info", return_value={}), \
                 patch("services.scanner.analyze_cert_manager", return_value={}), \
                 patch("services.scanner.analyze_multus", return_value={}), \
                 patch("services.scanner.analyze_sriov", return_value={}), \
                 patch("services.scanner.analyze_hugepages", return_value={}), \
                 patch("services.scanner.analyze_storage", return_value={}), \
                 patch("services.scanner.analyze_gateway_api", return_value={}), \
                 patch("services.scanner.analyze_dpf", return_value={}), \
                 patch("services.scanner.analyze_kamaji", return_value={}), \
                 patch("services.scanner.analyze_cis", return_value={}), \
                 patch("services.scanner.build_recommendations", return_value=[]), \
                 patch("services.scanner.build_proxy_recommendations", return_value=[]):
                scanner.scan(cluster.id)

        observed_count = db.query(BnkRelease).filter(
            BnkRelease.source_type == ReleaseSourceType.OBSERVED,
            BnkRelease.flo_version_min == "8.88.88-0.0.1",
        ).count()
        assert observed_count == 1

    def test_no_flo_version_leaves_running_release_id_unchanged(self, db, make_k8s_cluster):
        """When detect_current_bnk_version returns None, running_release_id is not touched."""
        cluster = make_k8s_cluster()
        assert cluster.running_release_id is None

        from services.scanner import ClusterScanner
        scanner = ClusterScanner(db)

        # bnk_install with no FLO version → detect_current_bnk_version returns None
        bnk_install = {"flo": {}}
        platform_ctx = MagicMock()
        platform_ctx.to_dict.return_value = {}
        platform_ctx.detected_platform_profile = "generic"

        with patch.object(scanner.k8s_service, "get_cluster", return_value=cluster), \
             patch.object(scanner.k8s_service, "load_kubeconfig", return_value=MagicMock()), \
             patch("services.scanner.fetch_scan_data", return_value={
                 "version_info": {}, "nodes": [], "namespaces": [], "crds": [],
                 "crd_names": set(), "crd_groups": set(), "cert_manager_pods": [],
                 "helm_releases": [], "kube_system_pods": [], "daemonsets": [],
                 "storage_classes": [], "gateways": [], "gatewayclasses": [],
                 "f5_tenant_pods": [], "f5_utils_pods": [], "dpf_operator_configs": [],
                 "dpudevices": [], "dpusets": [], "dpuclusters": [], "dpuservices": [],
                 "bfbs": [], "kamaji_pods": [], "kamaji_tcps": [], "cis_controllers": [],
                 "cis_virtualservers": [], "cis_transportservers": [], "cis_ingresslinks": [],
                 "cis_as3_configmaps": [], "cis_f5_ingresses": [], "openshift_routes": [],
                 "cneinstances": [], "vlans": [],
             }), \
             patch("services.scanner.analyze_bnk_install", return_value=bnk_install), \
             patch("services.scanner.PlatformContextService.apply_cluster_context", return_value=platform_ctx), \
             patch("services.scanner.analyze_cluster_info", return_value={}), \
             patch("services.scanner.analyze_cert_manager", return_value={}), \
             patch("services.scanner.analyze_multus", return_value={}), \
             patch("services.scanner.analyze_sriov", return_value={}), \
             patch("services.scanner.analyze_hugepages", return_value={}), \
             patch("services.scanner.analyze_storage", return_value={}), \
             patch("services.scanner.analyze_gateway_api", return_value={}), \
             patch("services.scanner.analyze_dpf", return_value={}), \
             patch("services.scanner.analyze_kamaji", return_value={}), \
             patch("services.scanner.analyze_cis", return_value={}), \
             patch("services.scanner.build_recommendations", return_value=[]), \
             patch("services.scanner.build_proxy_recommendations", return_value=[]):
            scanner.scan(cluster.id)

        db.refresh(cluster)
        assert cluster.running_release_id is None


# ---------------------------------------------------------------------------
# SAVEPOINT session-safety: DB-level error in write-back must not poison session
# ---------------------------------------------------------------------------

_EMPTY_FETCH_DATA = {
    "version_info": {}, "nodes": [], "namespaces": [], "crds": [],
    "crd_names": set(), "crd_groups": set(), "cert_manager_pods": [],
    "helm_releases": [], "kube_system_pods": [], "daemonsets": [],
    "storage_classes": [], "gateways": [], "gatewayclasses": [],
    "f5_tenant_pods": [], "f5_utils_pods": [], "dpf_operator_configs": [],
    "dpudevices": [], "dpusets": [], "dpuclusters": [], "dpuservices": [],
    "bfbs": [], "kamaji_pods": [], "kamaji_tcps": [], "cis_controllers": [],
    "cis_virtualservers": [], "cis_transportservers": [], "cis_ingresslinks": [],
    "cis_as3_configmaps": [], "cis_f5_ingresses": [], "openshift_routes": [],
    "cneinstances": [], "vlans": [],
}


def _run_scan_with_bad_upsert(db, cluster, bad_upsert_side_effect):
    """Run scan() with get_or_create_observed patched to a given side_effect.

    Uses contextlib.ExitStack to apply the analysis patches list because Python
    does not support `*iterable` unpacking in `with` statements.
    """
    import contextlib

    from services.scanner import ClusterScanner

    scanner = ClusterScanner(db)
    bnk_install = {"flo": {"version": "6.66.6-0.0.1"}}
    platform_ctx = MagicMock()
    platform_ctx.to_dict.return_value = {}
    platform_ctx.detected_platform_profile = "generic"

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(scanner.k8s_service, "get_cluster", return_value=cluster))
        stack.enter_context(patch.object(scanner.k8s_service, "load_kubeconfig", return_value=MagicMock()))
        stack.enter_context(patch("services.scanner.fetch_scan_data", return_value=_EMPTY_FETCH_DATA))
        stack.enter_context(patch("services.scanner.analyze_bnk_install", return_value=bnk_install))
        stack.enter_context(patch("services.scanner.PlatformContextService.apply_cluster_context", return_value=platform_ctx))
        stack.enter_context(patch(
            "services.release_registry_service.ReleaseRegistryService.get_or_create_observed",
            side_effect=bad_upsert_side_effect,
        ))
        for name in [
            "services.scanner.analyze_cluster_info",
            "services.scanner.analyze_cert_manager",
            "services.scanner.analyze_multus",
            "services.scanner.analyze_sriov",
            "services.scanner.analyze_hugepages",
            "services.scanner.analyze_storage",
            "services.scanner.analyze_gateway_api",
            "services.scanner.analyze_dpf",
            "services.scanner.analyze_kamaji",
            "services.scanner.analyze_cis",
        ]:
            stack.enter_context(patch(name, return_value={}))
        stack.enter_context(patch("services.scanner.build_recommendations", return_value=[]))
        stack.enter_context(patch("services.scanner.build_proxy_recommendations", return_value=[]))
        return scanner.scan(cluster.id)


class TestScannerWritebackSessionSafety:
    """
    Verify that a DB-level error inside get_or_create_observed does NOT poison
    the SQLAlchemy session (Fix 2 / begin_nested SAVEPOINT guard).

    Without begin_nested(), a flush failure inside get_or_create_observed puts
    the session in 'needs rollback' state; the subsequent platform-context
    self.db.flush() then raises PendingRollbackError, turning a non-fatal
    write-back failure into a hard scan crash.

    The test triggers the exact scenario: a NOT-NULL constraint violation inside
    get_or_create_observed's flush causes an IntegrityError at the DB level.
    With begin_nested(), only the savepoint is rolled back; scan() completes
    and returns a valid result dict.
    """

    def test_db_level_flush_error_does_not_crash_scan(self, db, make_k8s_cluster):
        """
        A DB-level IntegrityError inside get_or_create_observed's flush must not
        propagate as a scan failure.  The SAVEPOINT rolls back the nested block;
        the outer session remains usable and scan() returns a valid result.
        """
        cluster = make_k8s_cluster()

        def _db_level_error(flo_version):
            # Trigger a real DB-level constraint failure: ga_label is NOT NULL.
            # db.flush() raises IntegrityError from the DB engine, which (without
            # begin_nested) would poison the outer session with PendingRollbackError.
            from models.bnk_release import BnkRelease
            row = BnkRelease(ga_label=None, product_line="BNK", source_type="manual")
            db.add(row)
            db.flush()  # raises IntegrityError → savepoint catches + rolls back

        result = _run_scan_with_bad_upsert(db, cluster, _db_level_error)

        # Scan completed — not a hard failure
        assert result["cluster_id"] == cluster.id
        # Write-back was rolled back cleanly; running_release_id is still null
        db.refresh(cluster)
        assert cluster.running_release_id is None

    def test_python_error_in_writeback_does_not_crash_scan(self, db, make_k8s_cluster):
        """
        A plain Python exception in get_or_create_observed also must not fail
        the scan (broad-except guarantee), and must not lose earlier writes.
        """
        cluster = make_k8s_cluster()

        result = _run_scan_with_bad_upsert(
            db, cluster, RuntimeError("simulated registry failure")
        )

        assert result["cluster_id"] == cluster.id
        db.refresh(cluster)
        assert cluster.running_release_id is None
