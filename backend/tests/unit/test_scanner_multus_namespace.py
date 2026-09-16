"""Multus pod inventory is namespace-aware (Issue #202).

On ROKS/OpenShift, Multus runs in the ``openshift-multus`` namespace, not
``kube-system``. The scanner's pod fetch used to enumerate a hardcoded set of
namespaces that never included ``openshift-multus``, so ``analyze_multus``
computed ``running_pods == 0`` even on a healthy cluster (the DaemonSet was
still found cluster-wide, so status showed DETECTED with 0 running pods).

These tests exercise the REAL fetch path — ``fetch_scan_data`` calling the
kubernetes client — NOT a hand-built pod list handed to ``analyze_multus``.
The k8s API is mocked so that:

  * ``list_daemon_set_for_all_namespaces`` reports the Multus DaemonSet in its
    real namespace (openshift-multus or kube-system), and
  * ``list_namespaced_pod(namespace=...)`` returns Multus pods ONLY in that
    namespace — exactly the shape a real cluster presents.

The scan then fetches whatever namespace the DaemonSet lives in and the count
reflects reality. Before the fix, the OpenShift case reads 0 (the bug).
"""

from unittest.mock import MagicMock, patch

from services.scanner import analyze_multus
from services.scanner.fetch import fetch_scan_data


def _v1_pod(name: str, namespace: str, phase: str = "Running"):
    """A minimal V1Pod-shaped mock for _fetch_pods_in_ns to parse."""
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    pod.metadata.labels = {"app": "multus"}
    pod.status.phase = phase
    pod.status.container_statuses = []
    return pod


def _v1_daemonset(name: str, namespace: str, desired: int = 3, ready: int = 3):
    """A minimal V1DaemonSet-shaped mock for _fetch_daemonsets to parse."""
    ds = MagicMock()
    ds.metadata.name = name
    ds.metadata.namespace = namespace
    ds.metadata.labels = {}
    ds.status.desired_number_scheduled = desired
    ds.status.number_ready = ready
    ds.spec.template.spec.containers = []
    return ds


def _run_fetch(
    *,
    multus_namespace: str,
    pods_by_ns: dict[str, list],
    ds_specs: list[tuple[str, str]] | None = None,
):
    """Run the real fetch_scan_data with the k8s API mocked.

    ``pods_by_ns`` maps namespace -> list of V1Pod mocks. By default a single
    DaemonSet named ``multus`` is reported in ``multus_namespace``; pass
    ``ds_specs`` (a list of ``(name, namespace)`` tuples) to report a specific
    set of DaemonSets instead. Returns the fetched data dict and the CoreV1 mock.
    """
    core_v1 = MagicMock()

    def _list_namespaced_pod(namespace, **kwargs):
        resp = MagicMock()
        resp.items = pods_by_ns.get(namespace, [])
        return resp

    core_v1.list_namespaced_pod.side_effect = _list_namespaced_pod

    apps_v1 = MagicMock()

    specs = ds_specs if ds_specs is not None else [("multus", multus_namespace)]

    def _list_ds(**kwargs):
        resp = MagicMock()
        resp.items = [_v1_daemonset(name, ns) for name, ns in specs]
        return resp

    apps_v1.list_daemon_set_for_all_namespaces.side_effect = _list_ds

    api_client = MagicMock()
    k8s_service = MagicMock()

    with patch("services.scanner.fetch.client.CoreV1Api", return_value=core_v1), \
         patch("services.scanner.fetch.client.AppsV1Api", return_value=apps_v1), \
         patch("services.scanner.fetch._discover_api_groups", return_value=frozenset()), \
         patch("services.scanner.fetch.discover_f5_pods", return_value=([], [])):
        data = fetch_scan_data(api_client, k8s_service, cluster_id=1)

    return data, core_v1


# NAD CRD is required for DETECTED / PARTIAL; supply it in the analyze step.
_NAD_CRD = {"crd_names": {"network-attachment-definitions.k8s.cni.cncf.io"}}


class TestMultusNamespaceScopedFetch:
    def test_openshift_multus_pods_are_fetched_and_counted(self):
        """OpenShift shape: Multus DS + pods live ONLY in openshift-multus.

        Before the fix, running_pods == 0 (openshift-multus was never queried).
        After the fix, the DaemonSet's namespace is fetched and the 3 running
        pods are counted.
        """
        pods_by_ns = {
            "openshift-multus": [
                _v1_pod("multus-abc", "openshift-multus"),
                _v1_pod("multus-def", "openshift-multus"),
                _v1_pod("multus-additional-cni-plugins-xyz", "openshift-multus"),
            ],
            # kube-system has NO multus pods (as on a real OpenShift cluster).
            "kube-system": [],
        }
        data, core_v1 = _run_fetch(
            multus_namespace="openshift-multus", pods_by_ns=pods_by_ns
        )

        # The REAL fetch must have queried the openshift-multus namespace.
        queried = {
            c.kwargs.get("namespace") for c in core_v1.list_namespaced_pod.call_args_list
        }
        assert "openshift-multus" in queried, (
            "fetch must query the DaemonSet's namespace (openshift-multus)"
        )

        result = analyze_multus(
            [], _NAD_CRD["crd_names"], data["multus_pods"], data["daemonsets"]
        )
        assert result["running_pods"] == 3, (
            "Multus running pods in openshift-multus must be counted (was 0 before fix)"
        )
        assert result["daemonset"]["namespace"] == "openshift-multus"

    def test_vanilla_k8s_multus_in_kube_system_still_counted(self):
        """Vanilla k8s shape: Multus DS + pods live in kube-system.

        No extra namespace fetch is needed; the already-fetched kube-system
        pods are reused and counted (no regression).
        """
        pods_by_ns = {
            "kube-system": [
                _v1_pod("kube-multus-ds-1", "kube-system"),
                _v1_pod("kube-multus-ds-2", "kube-system"),
            ],
        }
        data, _ = _run_fetch(
            multus_namespace="kube-system", pods_by_ns=pods_by_ns
        )

        result = analyze_multus(
            [], _NAD_CRD["crd_names"], data["multus_pods"], data["daemonsets"]
        )
        assert result["running_pods"] == 2
        assert result["daemonset"]["namespace"] == "kube-system"

    def test_non_running_openshift_multus_pods_not_counted(self):
        """Only Running Multus pods in the DS namespace count."""
        pods_by_ns = {
            "openshift-multus": [
                _v1_pod("multus-abc", "openshift-multus", phase="Running"),
                _v1_pod("multus-def", "openshift-multus", phase="Pending"),
            ],
            "kube-system": [],
        }
        data, _ = _run_fetch(
            multus_namespace="openshift-multus", pods_by_ns=pods_by_ns
        )

        result = analyze_multus(
            [], _NAD_CRD["crd_names"], data["multus_pods"], data["daemonsets"]
        )
        assert result["running_pods"] == 1


def test_analyze_multus_prefers_exact_multus_daemonset_over_sibling():
    """bonnyr-f5 #203 review (MINOR 2): with both a sibling and the primary
    DaemonSet present, and the sibling FIRST in the list, analyze_multus reports
    the exact-named ``multus`` DaemonSet (deterministic, not list-order-dependent)."""
    from services.scanner.prereqs import analyze_multus

    daemonsets = [
        {"name": "multus-additional-cni-plugins", "namespace": "sib-ns", "desired": 6, "ready": 6},
        {"name": "multus", "namespace": "openshift-multus", "desired": 3, "ready": 3},
    ]
    multus_pods = [{"name": "multus-abc", "phase": "Running"}]
    result = analyze_multus(
        [], {"network-attachment-definitions.k8s.cni.cncf.io"}, multus_pods, daemonsets
    )
    assert result["daemonset"]["name"] == "multus"
    assert result["daemonset"]["namespace"] == "openshift-multus"


def test_multus_namespace_selector_prefers_exact_multus():
    """The fetch's namespace picker also prefers exact ``multus`` over a sibling,
    so the fetched namespace matches the reported DaemonSet."""
    from services.scanner.fetch import _multus_daemonset_namespace

    daemonsets = [
        {"name": "multus-additional-cni-plugins", "namespace": "sib-ns"},
        {"name": "multus", "namespace": "openshift-multus"},
    ]
    assert _multus_daemonset_namespace(daemonsets) == "openshift-multus"


# ---------------------------------------------------------------------------
# bonnyr-f5 #203 review (m-1): ranked, list-order-independent DaemonSet pick
# ---------------------------------------------------------------------------


class TestPickPrimaryMultusDaemonset:
    """The shared ``pick_primary_multus_daemonset`` ranks candidates:
    exact ``multus`` > ``kube-multus-ds`` > sorted-name tiebreak — never
    exact-match-then-first (which is list-order dependent and never fires on a
    Forge/vanilla cluster, whose installer names the DaemonSet ``kube-multus-ds``)."""

    def test_forge_vanilla_cluster_picks_kube_multus_ds(self):
        """Forge's own installer creates ``kube-multus-ds`` (no exact ``multus``);
        it must be selected over a generic sibling regardless of list order."""
        from services.scanner.prereqs import pick_primary_multus_daemonset

        daemonsets = [
            {"name": "multus-additional-cni-plugins", "namespace": "kube-system"},
            {"name": "kube-multus-ds", "namespace": "kube-system"},
        ]
        assert pick_primary_multus_daemonset(daemonsets)["name"] == "kube-multus-ds"
        # order independence: reversed list yields the same pick
        assert (
            pick_primary_multus_daemonset(list(reversed(daemonsets)))["name"]
            == "kube-multus-ds"
        )

    def test_exact_multus_outranks_kube_multus_ds(self):
        from services.scanner.prereqs import pick_primary_multus_daemonset

        daemonsets = [
            {"name": "kube-multus-ds", "namespace": "kube-system"},
            {"name": "multus", "namespace": "openshift-multus"},
        ]
        assert pick_primary_multus_daemonset(daemonsets)["name"] == "multus"

    def test_generic_siblings_broken_by_sorted_name(self):
        """With only non-preferred candidates, the tiebreak is sorted name —
        stable regardless of API list order (not first-in-list)."""
        from services.scanner.prereqs import pick_primary_multus_daemonset

        a = {"name": "multus-zeta", "namespace": "ns-z"}
        b = {"name": "multus-alpha", "namespace": "ns-a"}
        assert pick_primary_multus_daemonset([a, b])["name"] == "multus-alpha"
        assert pick_primary_multus_daemonset([b, a])["name"] == "multus-alpha"

    def test_no_multus_daemonset_returns_none(self):
        from services.scanner.prereqs import pick_primary_multus_daemonset

        assert pick_primary_multus_daemonset([]) is None
        assert (
            pick_primary_multus_daemonset(
                [{"name": "kube-proxy", "namespace": "kube-system"}]
            )
            is None
        )

    def test_tolerates_name_none_and_missing(self):
        """Safe idiom: a DaemonSet with ``name: None`` or no name key must not
        raise (the other picker idiom, ``.get('name','').lower()``, would)."""
        from services.scanner.prereqs import pick_primary_multus_daemonset

        daemonsets = [
            {"name": None, "namespace": "weird-ns"},
            {"namespace": "no-name-ns"},
            {"name": "multus", "namespace": "openshift-multus"},
        ]
        assert pick_primary_multus_daemonset(daemonsets)["name"] == "multus"


# ---------------------------------------------------------------------------
# bonnyr-f5 #203 review (m-2): fetch + analyze route through the SAME picker
# ---------------------------------------------------------------------------


class TestPickersAgree:
    """If the fetch-side namespace picker and the analyze-side DaemonSet pick
    ever diverged, fetch would query namespace A while analyze reported
    namespace B and ``running_pods`` would silently drop to 0 (#202 re-armed).
    Both now call ``pick_primary_multus_daemonset``; these tests pin agreement."""

    def test_fetch_and_analyze_agree_multi_ds_multi_namespace(self):
        from services.scanner.fetch import _multus_daemonset_namespace
        from services.scanner.prereqs import analyze_multus

        daemonsets = [
            {"name": "multus-additional-cni-plugins", "namespace": "sib-ns",
             "desired": 6, "ready": 6},
            {"name": "kube-multus-ds", "namespace": "kube-system",
             "desired": 2, "ready": 2},
            {"name": "multus", "namespace": "openshift-multus",
             "desired": 3, "ready": 3},
        ]
        fetched_ns = _multus_daemonset_namespace(daemonsets)
        reported = analyze_multus(
            [], {"network-attachment-definitions.k8s.cni.cncf.io"}, [], daemonsets
        )["daemonset"]
        assert fetched_ns == reported["namespace"] == "openshift-multus"

    def test_fetch_and_analyze_agree_on_forge_cluster(self):
        """Forge/vanilla topology (kube-multus-ds only): both sides still agree."""
        from services.scanner.fetch import _multus_daemonset_namespace
        from services.scanner.prereqs import analyze_multus

        daemonsets = [
            {"name": "multus-additional-cni-plugins", "namespace": "kube-system",
             "desired": 3, "ready": 3},
            {"name": "kube-multus-ds", "namespace": "kube-system",
             "desired": 3, "ready": 3},
        ]
        fetched_ns = _multus_daemonset_namespace(daemonsets)
        reported = analyze_multus(
            [], {"network-attachment-definitions.k8s.cni.cncf.io"}, [], daemonsets
        )["daemonset"]
        assert fetched_ns == reported["namespace"] == "kube-system"
        assert reported["name"] == "kube-multus-ds"

    def test_both_callers_tolerate_name_none(self):
        """Consistent null-handling: neither caller raises on ``name: None``."""
        from services.scanner.fetch import _multus_daemonset_namespace
        from services.scanner.prereqs import analyze_multus

        daemonsets = [
            {"name": None, "namespace": "weird-ns"},
            {"name": "multus", "namespace": "openshift-multus",
             "desired": 3, "ready": 3},
        ]
        assert _multus_daemonset_namespace(daemonsets) == "openshift-multus"
        reported = analyze_multus(
            [], {"network-attachment-definitions.k8s.cni.cncf.io"}, [], daemonsets
        )["daemonset"]
        assert reported["namespace"] == "openshift-multus"

    def test_analyze_tolerates_daemonset_without_namespace_key(self):
        """The reported daemonset info uses ``.get`` — a DaemonSet dict missing
        ``namespace`` yields ``None``, not a KeyError."""
        from services.scanner.prereqs import analyze_multus

        daemonsets = [{"name": "multus"}]  # no namespace/desired/ready
        reported = analyze_multus(
            [], {"network-attachment-definitions.k8s.cni.cncf.io"}, [], daemonsets
        )["daemonset"]
        assert reported["name"] == "multus"
        assert reported["namespace"] is None


def test_forge_cluster_end_to_end_fetches_kube_multus_ds_namespace():
    """End-to-end (real fetch path): a Forge cluster reporting ``kube-multus-ds``
    (plus a sibling) queries kube-system and counts its running pods — the exact
    ``multus`` match never fires here, so this exercises the ranked pick."""
    pods_by_ns = {
        "kube-system": [
            _v1_pod("kube-multus-ds-1", "kube-system"),
            _v1_pod("kube-multus-ds-2", "kube-system"),
            _v1_pod("multus-additional-cni-plugins-a", "kube-system"),
        ],
    }
    data, _ = _run_fetch(
        multus_namespace="kube-system",
        pods_by_ns=pods_by_ns,
        ds_specs=[
            ("multus-additional-cni-plugins", "kube-system"),
            ("kube-multus-ds", "kube-system"),
        ],
    )
    result = analyze_multus(
        [], _NAD_CRD["crd_names"], data["multus_pods"], data["daemonsets"]
    )
    assert result["daemonset"]["name"] == "kube-multus-ds"
    assert result["daemonset"]["namespace"] == "kube-system"
    assert result["running_pods"] == 3


def test_sibling_in_other_namespace_excluded_from_count():
    """m-3: only the primary DaemonSet's own namespace is fetched, so a
    ``multus``-named sibling living elsewhere is EXCLUDED from running_pods —
    matching the rewritten comment (primary@openshift-multus 3 + sibling@sib-ns 6
    → 3)."""
    pods_by_ns = {
        "openshift-multus": [
            _v1_pod("multus-a", "openshift-multus"),
            _v1_pod("multus-b", "openshift-multus"),
            _v1_pod("multus-c", "openshift-multus"),
        ],
        "sib-ns": [_v1_pod(f"multus-sib-{i}", "sib-ns") for i in range(6)],
    }
    data, _ = _run_fetch(
        multus_namespace="openshift-multus",
        pods_by_ns=pods_by_ns,
        ds_specs=[
            ("multus", "openshift-multus"),
            ("multus-additional-cni-plugins", "sib-ns"),
        ],
    )
    result = analyze_multus(
        [], _NAD_CRD["crd_names"], data["multus_pods"], data["daemonsets"]
    )
    assert result["running_pods"] == 3
