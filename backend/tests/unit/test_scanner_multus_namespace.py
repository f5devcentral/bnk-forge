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


def _run_fetch(*, multus_namespace: str, pods_by_ns: dict[str, list]):
    """Run the real fetch_scan_data with the k8s API mocked.

    ``pods_by_ns`` maps namespace -> list of V1Pod mocks; the DaemonSet is
    reported in ``multus_namespace``. Returns the fetched data dict.
    """
    core_v1 = MagicMock()

    def _list_namespaced_pod(namespace, **kwargs):
        resp = MagicMock()
        resp.items = pods_by_ns.get(namespace, [])
        return resp

    core_v1.list_namespaced_pod.side_effect = _list_namespaced_pod

    apps_v1 = MagicMock()

    def _list_ds(**kwargs):
        resp = MagicMock()
        resp.items = [_v1_daemonset("multus", multus_namespace)]
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
