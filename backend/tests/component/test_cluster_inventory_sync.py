"""
Issue #194: Cluster inventory sync — last_synced_at + pod inventory.

Two defects are locked here:

1. ClusterScanner.scan() never wrote ``last_synced_at``, so a registered
   cluster stayed "never synced" forever (NULL) even after the scan ran and
   after repeated no-op PUTs. These tests prove the scan now stamps
   ``last_synced_at`` on completion, that the stamp is only written on success
   (a scan that raises early must NOT stamp), and that it persists across a
   commit (the async registration/PUT task path).

2. Over a fetch that surfaces Multus pods in a namespace the scan actually reads
   (kube-system), real analyze_multus counts them (not 0) and the scan records
   ``last_synced_at`` — so "genuinely empty" is decidable from "never scanned".
   NOTE: the reporter's own "0 Multus pods on OpenShift" was a SEPARATE,
   pre-existing namespace-scoping gap — Multus runs in ``openshift-multus``,
   which the pod fetch never queries (tracked in #202) — retracted by the
   reporter; this PR does not fix or claim to fix that symptom.
"""

import contextlib
from datetime import datetime
from unittest.mock import MagicMock, patch

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

# Analysis functions patched to no-ops when a test isolates one code path.
_ANALYZERS = [
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
    "services.scanner.analyze_bnk_install",
]


def _run_scan(db, cluster, *, fetch_data=None, skip_analyzers=(), fetch_side_effect=None):
    """Run ClusterScanner.scan() with I/O and (optionally) analyzers mocked.

    ``skip_analyzers`` names analyzers to leave REAL so a test can assert on
    their output; the rest are patched to return ``{}``. ``fetch_side_effect``
    (e.g. an exception) simulates a scan that fails before completion.
    """
    from services.scanner import ClusterScanner

    scanner = ClusterScanner(db)
    platform_ctx = MagicMock()
    platform_ctx.to_dict.return_value = {}
    platform_ctx.detected_platform_profile = "roks"

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(scanner.k8s_service, "get_cluster", return_value=cluster))
        stack.enter_context(patch.object(scanner.k8s_service, "load_kubeconfig", return_value=MagicMock()))
        if fetch_side_effect is not None:
            stack.enter_context(patch("services.scanner.fetch_scan_data", side_effect=fetch_side_effect))
        else:
            stack.enter_context(patch(
                "services.scanner.fetch_scan_data",
                return_value=fetch_data if fetch_data is not None else dict(_EMPTY_FETCH_DATA),
            ))
        stack.enter_context(patch(
            "services.scanner.PlatformContextService.apply_cluster_context",
            return_value=platform_ctx,
        ))
        for name in _ANALYZERS:
            if name in skip_analyzers:
                continue
            stack.enter_context(patch(name, return_value={}))
        stack.enter_context(patch("services.scanner.build_recommendations", return_value=[]))
        stack.enter_context(patch("services.scanner.build_proxy_recommendations", return_value=[]))
        return scanner.scan(cluster.id)


class TestLastSyncedAtStamp:
    def test_scan_stamps_last_synced_at(self, db, make_k8s_cluster):
        """A completed scan sets last_synced_at (was permanently NULL — #194)."""
        cluster = make_k8s_cluster()
        assert cluster.last_synced_at is None  # never scanned

        result = _run_scan(db, cluster)

        db.refresh(cluster)
        assert isinstance(cluster.last_synced_at, datetime)
        # Result metadata still reports the scan timing.
        assert "scanned_at" in result["scan_metadata"]

    def test_last_synced_at_persists_across_commit(self, db, make_k8s_cluster):
        """The stamp survives the commit the async registration/PUT task does."""
        cluster = make_k8s_cluster()
        _run_scan(db, cluster)
        db.commit()  # mirrors scan_cluster_async's own commit

        db.expire_all()
        reloaded = db.query(type(cluster)).filter_by(id=cluster.id).one()
        assert reloaded.last_synced_at is not None

    def test_failed_scan_does_not_stamp_last_synced_at(self, db, make_k8s_cluster):
        """A scan that raises before completion must NOT stamp last_synced_at.

        Mutation guard: moving the stamp above the analysis (or dropping the
        'only on success' property) would let a failed scan look synced.
        """
        cluster = make_k8s_cluster()
        assert cluster.last_synced_at is None

        import pytest
        with pytest.raises(RuntimeError, match="cluster unreachable"):
            _run_scan(db, cluster, fetch_side_effect=RuntimeError("cluster unreachable"))

        db.refresh(cluster)
        assert cluster.last_synced_at is None  # still never-synced


class TestPodInventoryPopulated:
    def test_multus_pods_are_counted_not_zero(self, db, make_k8s_cluster):
        """analyze_multus counts Multus pods the fetch surfaced, and the scan stamps.

        Locks the analysis + ``last_synced_at`` behaviour: over a fetch whose
        Multus pods sit in kube-system (the namespace the scan actually reads),
        the running count is reported, not 0. This is NOT a proof of the
        reporter's OpenShift "0 pods" symptom, which is a separate
        namespace-scoping gap (#202: Multus lives in openshift-multus, unfetched).
        """
        cluster = make_k8s_cluster()

        fetch = dict(_EMPTY_FETCH_DATA)
        fetch["crd_names"] = {"network-attachment-definitions.k8s.cni.cncf.io"}
        fetch["daemonsets"] = [
            {"name": "multus", "namespace": "kube-system", "desired": 6, "ready": 6},
        ]
        fetch["kube_system_pods"] = [
            {"name": f"multus-{i}", "phase": "Running"} for i in range(6)
        ]

        from services.scanner.constants import PrerequisiteStatus

        result = _run_scan(
            db, cluster, fetch_data=fetch,
            skip_analyzers=("services.scanner.analyze_multus",),
        )

        multus = result["prerequisites"]["multus"]
        assert multus["running_pods"] == 6
        assert multus["status"] == PrerequisiteStatus.DETECTED
        assert multus["nad_crd_installed"] is True
        # And the scan is recorded, so "empty" vs "never scanned" is decidable.
        db.refresh(cluster)
        assert cluster.last_synced_at is not None
