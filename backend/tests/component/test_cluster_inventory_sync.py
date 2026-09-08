"""
Issue #194: Cluster inventory sync — last_synced_at + pod inventory.

Two defects are locked here:

1. ClusterScanner.scan() never wrote ``last_synced_at``, so a registered
   cluster stayed "never synced" forever (NULL) even after the scan ran and
   after repeated no-op PUTs. These tests prove the scan now stamps
   ``last_synced_at`` on completion, that the stamp is only written when the
   scan GENUINELY REACHED the cluster (``fetch_scan_data`` reports ``reached`` —
   from the version/namespace/API-group preflight), so an expired-token or
   unreachable cluster (every fetcher swallows its error → a fully-shaped EMPTY
   dict) does NOT stamp a fresh time over an empty panel, that a scan raising
   early must NOT stamp, and that the stamp persists across a commit (the async
   registration/PUT task path).

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

# The expired-token / unreachable-cluster shape: every fetcher swallowed its
# exception and returned an empty default, so ``reached`` is False and no
# version / namespaces / API groups came back. This is exactly what
# fetch_scan_data returns for a 401 or an unreachable API server — a scan over
# this MUST NOT stamp last_synced_at (#194).
#
# ``multus_pods`` mirrors #203's fetch-dict shape (it adds a namespace-scoped
# multus_pods key). It is unused on THIS branch (analyze_multus still reads
# kube_system_pods here) but keeps the fixture forward-compatible so the eventual
# #203 merge does not KeyError.
_EMPTY_FETCH_DATA = {
    "reached": False,
    "version_info": None, "nodes": [], "namespaces": [], "crds": [],
    "crd_names": set(), "crd_groups": set(), "cert_manager_pods": [],
    "helm_releases": [], "kube_system_pods": [], "multus_pods": [], "daemonsets": [],
    "storage_classes": [], "gateways": [], "gatewayclasses": [],
    "f5_tenant_pods": [], "f5_utils_pods": [], "dpf_operator_configs": [],
    "dpudevices": [], "dpusets": [], "dpuclusters": [], "dpuservices": [],
    "bfbs": [], "kamaji_pods": [], "kamaji_tcps": [], "cis_controllers": [],
    "cis_virtualservers": [], "cis_transportservers": [], "cis_ingresslinks": [],
    "cis_as3_configmaps": [], "cis_f5_ingresses": [], "openshift_routes": [],
    "cneinstances": [], "vlans": [],
}


def _reached_fetch_data(**overrides):
    """A genuinely-empty-but-REACHABLE cluster's fetch dict (#194).

    ``reached`` is True and the version/namespace preflight came back, so a scan
    over this SHOULD stamp last_synced_at even though every inventory list is
    empty — that is the "scanned and genuinely empty" case the reporter needs to
    be able to tell apart from "never scanned".
    """
    data = dict(_EMPTY_FETCH_DATA)
    data.update({
        "reached": True,
        "version_info": {"git_version": "v1.29.0"},
        "namespaces": ["default", "kube-system"],
    })
    data.update(overrides)
    return data

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
        """A scan that reached the cluster sets last_synced_at (was permanently NULL — #194).

        Uses a genuinely-empty-but-REACHABLE fetch: every inventory list is
        empty but the version/namespace preflight succeeded, so the scan stamps.
        This is the "scanned and genuinely empty" case the reporter must be able
        to tell apart from "never scanned".
        """
        cluster = make_k8s_cluster()
        assert cluster.last_synced_at is None  # never scanned

        result = _run_scan(db, cluster, fetch_data=_reached_fetch_data())

        db.refresh(cluster)
        assert isinstance(cluster.last_synced_at, datetime)
        # Result metadata still reports the scan timing.
        assert "scanned_at" in result["scan_metadata"]

    def test_unreachable_scan_does_not_stamp_last_synced_at(self, db, make_k8s_cluster):
        """The expired-token / unreachable case must NOT stamp (#194).

        Every fetcher swallows its exception and returns an empty default, so an
        expired-token or unreachable cluster yields a fully-shaped EMPTY fetch
        with ``reached`` False. Stamping last_synced_at here would write a fresh
        sync time over a panel that has no data — strictly worse than the NULL
        that honestly says "we have never gotten data from this cluster".
        """
        cluster = make_k8s_cluster()
        assert cluster.last_synced_at is None

        # dict(_EMPTY_FETCH_DATA) → reached=False (the default _run_scan fetch).
        _run_scan(db, cluster)

        db.refresh(cluster)
        assert cluster.last_synced_at is None  # still never-synced

    def test_last_synced_at_persists_across_commit(self, db, make_k8s_cluster):
        """The stamp survives the commit the async registration/PUT task does."""
        cluster = make_k8s_cluster()
        _run_scan(db, cluster, fetch_data=_reached_fetch_data())
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

        multus_pods = [
            {"name": f"multus-{i}", "phase": "Running"} for i in range(6)
        ]
        fetch = _reached_fetch_data()
        fetch["crd_names"] = {"network-attachment-definitions.k8s.cni.cncf.io"}
        fetch["daemonsets"] = [
            {"name": "multus", "namespace": "kube-system", "desired": 6, "ready": 6},
        ]
        # The primary Multus DaemonSet is in kube-system, so the pods the scan
        # counts live in kube-system. Seed BOTH keys with the same list:
        #  - kube_system_pods  → analyze_multus reads this on THIS branch.
        #  - multus_pods       → analyze_multus reads this after #203 merges
        #    (its _fetch_multus_pods returns the kube_system_pods list verbatim
        #    when the DaemonSet lives in kube-system). Mirroring both keeps
        #    running_pods == 6 both before and after the #203 merge, surviving
        #    #203's filter (name contains "multus" AND phase == "Running").
        fetch["kube_system_pods"] = multus_pods
        fetch["multus_pods"] = multus_pods

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
