"""
Unit tests for services.bnk_pod_discovery — layered role resolution.

Tests cover the four required scenarios from E4 slice-1:
  (a) BNK pod in a custom namespace is discovered.
  (b) Label-identified pod with non-BNK name is classified correctly.
  (c) Existing prefix-named pods classify identically (regression).
  (d) Prefix fallback still works when no label/CRD signal is present.

All tests are pure (no I/O, no DB).  ``discover_f5_pods`` I/O is tested
via mocked kubernetes API clients; ``classify_f5_pods`` takes plain dicts.
"""

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest  # noqa: F401 (used by pytest fixtures and markers)

from services.bnk_pod_discovery import (
    ALL_F5_PREFIXES,
    BNK_NAMESPACES,
    TENANT_PREFIXES,
    UTILS_PREFIXES,
    _is_bnk_pod,
    _match_container_hints,
    _match_label_hints,
    classify_f5_pods,
    detect_install_shape,
    discover_f5_pods,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_v1pod(
    name: str,
    namespace: str = "f5-bnk",
    labels: dict | None = None,
    container_names: list[str] | None = None,
    container_images: list[str] | None = None,
) -> MagicMock:
    """Build a minimal V1Pod-like MagicMock."""
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    pod.metadata.labels = labels or {}
    pod.spec.node_name = None
    pod.status.phase = "Running"
    pod.status.host_ip = None
    pod.status.start_time = None
    pod.status.conditions = []
    pod.status.container_statuses = []

    # Build spec containers (used by pod_to_dict for the "containers" list)
    # pod_to_dict reads status.container_statuses, so populate that too.
    cnames = container_names or []
    cimages = container_images or ["image:latest"] * len(cnames)
    cs_list = []
    for cname, cimage in zip(cnames, cimages):
        cs = MagicMock()
        cs.name = cname
        cs.image = cimage
        cs.ready = True
        cs.restart_count = 0
        cs.state.running = True
        cs.state.waiting = None
        cs.state.terminated = None
        cs_list.append(cs)
    pod.status.container_statuses = cs_list

    return pod


def _pod_dict(
    name: str,
    namespace: str = "f5-bnk",
    labels: dict | None = None,
    containers: list[dict] | None = None,
) -> dict:
    """Build a plain pod dict (the shape classify_f5_pods receives)."""
    return {
        "name": name,
        "namespace": namespace,
        "phase": "Running",
        "labels": labels or {},
        "containers": containers or [],
    }


# ---------------------------------------------------------------------------
# _match_label_hints
# ---------------------------------------------------------------------------


class TestMatchLabelHints:
    def test_tmm_label(self):
        assert _match_label_hints({"app": "f5-tmm"}) == "tmm"

    def test_flo_app_label(self):
        assert _match_label_hints({"app": "flo"}) == "flo"

    def test_flo_aks_name_label(self):
        assert _match_label_hints({"app.kubernetes.io/name": "f5-lifecycle-operator"}) == "flo"

    def test_crd_installer_label(self):
        assert _match_label_hints({"app": "crd-installer"}) == "crd_installer"

    def test_ipam_label(self):
        assert _match_label_hints({"app": "ipam-operator"}) == "ipam"

    def test_controller_label(self):
        assert _match_label_hints({"app": "f5-cne-controller"}) == "controller"

    def test_no_match_returns_none(self):
        assert _match_label_hints({"app": "nginx"}) is None

    def test_empty_labels_returns_none(self):
        assert _match_label_hints({}) is None


# ---------------------------------------------------------------------------
# _match_container_hints
# ---------------------------------------------------------------------------


class TestMatchContainerHints:
    def test_container_named_tmm(self):
        assert _match_container_hints([{"name": "tmm", "image": "img"}]) == "tmm"

    def test_container_named_flo(self):
        assert _match_container_hints([{"name": "flo", "image": "img"}]) == "flo"

    def test_ln_operator_image(self):
        assert _match_container_hints([{"name": "mgr", "image": "f5networks/ln-operator:v1.0"}]) == "flo"

    def test_no_match_returns_none(self):
        assert _match_container_hints([{"name": "nginx", "image": "nginx:latest"}]) is None

    def test_empty_list_returns_none(self):
        assert _match_container_hints([]) is None

    def test_container_named_flower_not_classified_as_flo(self):
        """'flower' contains 'flo' as substring — exact match must reject it."""
        assert _match_container_hints([{"name": "flower", "image": "mher/flower:1.0"}]) is None

    def test_container_named_workflow_not_classified_as_flo(self):
        """'workflow' contains 'flo' as substring — exact match must reject it."""
        assert _match_container_hints([{"name": "workflow-sidecar", "image": "custom:latest"}]) is None


# ---------------------------------------------------------------------------
# _is_bnk_pod — used to filter sweep and phase-1 results
# ---------------------------------------------------------------------------


class TestIsBnkPod:
    def test_label_identified_pod_with_non_standard_name(self):
        """(b) Pod with app=f5-tmm label but arbitrary name is recognized as BNK."""
        pod = _make_v1pod("bigip-next-dataplane-0", namespace="custom-ns", labels={"app": "f5-tmm"})
        assert _is_bnk_pod(pod) is True

    def test_prefix_named_pod_with_no_labels(self):
        """(d) Pod with f5-tmm prefix and no labels is still recognized."""
        pod = _make_v1pod("f5-tmm-abc123", namespace="custom-ns")
        assert _is_bnk_pod(pod) is True

    def test_non_bnk_pod_rejected(self):
        pod = _make_v1pod("nginx-abc", namespace="default", labels={"app": "nginx"})
        assert _is_bnk_pod(pod) is False

    def test_flo_label_identified(self):
        pod = _make_v1pod("my-flo-v2-0", namespace="f5-operator", labels={"app": "flo"})
        assert _is_bnk_pod(pod) is True

    def test_third_party_ipam_operator_not_swept(self):
        """Non-F5 pod with app=ipam-operator in a random namespace must NOT be swept in."""
        pod = _make_v1pod("whereabouts-abc", namespace="kube-system", labels={"app": "ipam-operator"})
        assert _is_bnk_pod(pod) is False

    def test_third_party_crd_installer_not_swept(self):
        """Non-F5 pod with app=crd-installer in a random namespace must NOT be swept in."""
        pod = _make_v1pod("unrelated-crd-bootstrap-xyz", namespace="kube-system", labels={"app": "crd-installer"})
        assert _is_bnk_pod(pod) is False


# ---------------------------------------------------------------------------
# classify_f5_pods — label/container hint priority over name fallback
# ---------------------------------------------------------------------------


class TestClassifyF5Pods:
    # (b) Label-identified pod whose NAME matches no prefix
    def test_label_tmm_custom_name_classified(self):
        """pod with app=f5-tmm but name 'bigip-next-dataplane-0' → tmm bucket."""
        p = _pod_dict("bigip-next-dataplane-0", labels={"app": "f5-tmm"})
        result = classify_f5_pods([p], [])
        assert p in result["tmm"]
        assert p not in result["flo"]

    def test_label_flo_custom_name_classified(self):
        """pod with app=flo and arbitrary name → flo bucket."""
        p = _pod_dict("my-lifecycle-operator-v3-0", labels={"app": "flo"})
        result = classify_f5_pods([p], [])
        assert p in result["flo"]

    def test_label_flo_aks_name_classified(self):
        """pod with app.kubernetes.io/name=f5-lifecycle-operator → flo bucket."""
        p = _pod_dict(
            "flo-deployment-xyz",
            labels={"app.kubernetes.io/name": "f5-lifecycle-operator"},
        )
        result = classify_f5_pods([p], [])
        assert p in result["flo"]

    def test_label_crd_installer_in_utils(self):
        p = _pod_dict("crd-inst-custom", labels={"app": "crd-installer"})
        result = classify_f5_pods([], [p])
        assert p in result["crd_installer"]

    # (c) Existing prefix-named pods classify identically (regression)
    def test_prefix_flo_lifecycle_still_classified(self):
        """flo-f5-lifecycle-* pod with no label → flo via name fallback."""
        p = _pod_dict("flo-f5-lifecycle-abc")
        result = classify_f5_pods([p], [])
        assert p in result["flo"], "flo-f5-lifecycle prefix must still land in flo"

    def test_prefix_f5_tmm_still_classified(self):
        p = _pod_dict("f5-tmm-worker-0")
        result = classify_f5_pods([p], [])
        assert p in result["tmm"]

    def test_prefix_cne_controller_still_classified(self):
        p = _pod_dict("f5-cne-controller-abc")
        result = classify_f5_pods([p], [])
        assert p in result["controller"]

    def test_prefix_analyzer_still_classified(self):
        p = _pod_dict("f5-analyzer-0")
        result = classify_f5_pods([p], [])
        assert p in result["analyzer"]

    def test_prefix_crd_installer_still_classified(self):
        p = _pod_dict("crd-installer-job-abc")
        result = classify_f5_pods([], [p])
        assert p in result["crd_installer"]

    # (d) Prefix fallback still works when no label/CRD signal
    def test_fallback_only_prefix_no_labels(self):
        """Pod with zero labels must still resolve via name prefix."""
        p = _pod_dict("f5-lifecycle-flo-abc", labels={})
        result = classify_f5_pods([p], [])
        assert p in result["flo"]

    # Container hints
    def test_container_hint_tmm(self):
        """Pod without BNK labels but with container named 'tmm' → tmm."""
        p = _pod_dict(
            "some-custom-pod",
            containers=[{"name": "tmm", "image": "f5networks/tmm:v1.0", "ready": True}],
        )
        result = classify_f5_pods([p], [])
        assert p in result["tmm"]

    def test_container_image_ln_operator(self):
        """Pod without labels, image contains 'ln-operator' → flo."""
        p = _pod_dict(
            "flo-release-custom-name",
            containers=[{"name": "manager", "image": "registry/ln-operator:v2.0", "ready": True}],
        )
        result = classify_f5_pods([p], [])
        assert p in result["flo"]

    # Return shape
    def test_return_shape_has_all_keys(self):
        result = classify_f5_pods([], [])
        assert set(result.keys()) == {"tmm", "flo", "controller", "analyzer", "crd_installer"}

    def test_pods_not_double_counted(self):
        """A pod matching both label and name must appear exactly once."""
        p = _pod_dict("f5-tmm-abc", labels={"app": "f5-tmm"})
        result = classify_f5_pods([p], [])
        assert result["tmm"].count(p) == 1

    # Direct-helm install: f5ingress-f5ingress controller
    def test_label_f5ingress_controller_classified(self):
        """pod with app=f5ingress-f5ingress (direct-helm BNK install) → controller bucket."""
        p = _pod_dict("f5ingress-f5ingress-7d8f9c-abcde", labels={"app": "f5ingress-f5ingress"})
        result = classify_f5_pods([p], [])
        assert p in result["controller"]

    def test_prefix_f5ingress_no_labels_classified(self):
        """f5ingress-* pod with no labels still resolves to controller via name fallback."""
        p = _pod_dict("f5ingress-f5ingress-abc", labels={})
        result = classify_f5_pods([p], [])
        assert p in result["controller"]


# ---------------------------------------------------------------------------
# detect_install_shape — flo/helm/unknown classification
# ---------------------------------------------------------------------------


class TestDetectInstallShape:
    def test_flo_present_is_flo_shape(self):
        classified = {"tmm": [_pod_dict("f5-tmm-1")], "flo": [_pod_dict("flo-1")], "controller": []}
        assert detect_install_shape(classified) == "flo"

    def test_no_flo_with_controller_is_helm_shape(self):
        classified = {
            "tmm": [_pod_dict("f5-tmm-1")],
            "flo": [],
            "controller": [_pod_dict("f5ingress-f5ingress-1")],
        }
        assert detect_install_shape(classified) == "helm"

    def test_no_flo_tmm_only_is_helm_shape(self):
        classified = {"tmm": [_pod_dict("f5-tmm-1")], "flo": [], "controller": []}
        assert detect_install_shape(classified) == "helm"

    def test_empty_classified_is_unknown_shape(self):
        classified = {"tmm": [], "flo": [], "controller": []}
        assert detect_install_shape(classified) == "unknown"


# ---------------------------------------------------------------------------
# discover_f5_pods — custom namespace discovery via mocked K8s API
# ---------------------------------------------------------------------------


class TestDiscoverF5Pods:
    """
    (a) BNK pod in a custom namespace is discovered.

    We mock the kubernetes API so the cluster-wide sweep returns a pod
    in a non-standard namespace (e.g. 'acme-bnk') with label app=f5-tmm
    but a non-standard name.  The function must return it in tenant_pods.
    """

    def _make_api_client(self, sweep_pods: list, ns_pods: dict | None = None) -> Generator[tuple[MagicMock, MagicMock], None, None]:
        """
        Build a mock api_client whose CoreV1Api behaviour is:
          - list_pod_for_all_namespaces → sweep_pods
          - list_namespaced_pod(namespace=ns) → ns_pods.get(ns, [])
        """
        ns_pods = ns_pods or {}
        api_client = MagicMock()

        core_v1 = MagicMock()

        def _list_namespaced(namespace, **kwargs):
            resp = MagicMock()
            resp.items = ns_pods.get(namespace, [])
            return resp

        def _list_all(**kwargs):
            resp = MagicMock()
            resp.items = sweep_pods
            return resp

        core_v1.list_namespaced_pod.side_effect = _list_namespaced
        core_v1.list_pod_for_all_namespaces.side_effect = _list_all

        with patch("services.bnk_pod_discovery.k8s_client.CoreV1Api", return_value=core_v1):
            yield api_client, core_v1

    def test_custom_namespace_pod_discovered_via_sweep(self):
        """(a) Pod in 'acme-bnk' (not in BNK_NAMESPACES) is found via sweep."""
        custom_pod = _make_v1pod(
            "bigip-next-dataplane-0",
            namespace="acme-bnk",
            labels={"app": "f5-tmm"},
        )
        for api_client, _ in self._make_api_client(sweep_pods=[custom_pod]):
            tenant_pods, utils_pods = discover_f5_pods(api_client, include_sweep=True)

        assert any(p["name"] == "bigip-next-dataplane-0" for p in tenant_pods), (
            "Pod with app=f5-tmm in custom namespace must appear in tenant_pods"
        )
        assert "acme-bnk" in {p["namespace"] for p in tenant_pods}

    def test_custom_namespace_not_in_bnk_namespaces(self):
        """Guard: 'acme-bnk' must not be in the static BNK_NAMESPACES tuple."""
        assert "acme-bnk" not in BNK_NAMESPACES

    def test_standard_namespace_pod_still_discovered(self):
        """(c) Pod in f5-bnk with standard prefix still discovered via phase 1."""
        standard_pod = _make_v1pod("f5-tmm-worker-0", namespace="f5-bnk")
        for api_client, _ in self._make_api_client(
            sweep_pods=[],
            ns_pods={"f5-bnk": [standard_pod]},
        ):
            tenant_pods, utils_pods = discover_f5_pods(api_client, include_sweep=False)

        assert any(p["name"] == "f5-tmm-worker-0" for p in tenant_pods)

    def test_no_sweep_when_include_sweep_false(self):
        """Pass include_sweep=False and sweep must not be called."""
        standard_pod = _make_v1pod("f5-tmm-abc", namespace="f5-bnk")
        for api_client, core_v1 in self._make_api_client(
            sweep_pods=[],
            ns_pods={"f5-bnk": [standard_pod]},
        ):
            discover_f5_pods(api_client, include_sweep=False)
            core_v1.list_pod_for_all_namespaces.assert_not_called()

    def test_non_bnk_pod_not_included(self):
        """Pod with no BNK labels/prefix must not appear in results."""
        noise_pod = _make_v1pod("nginx-abc", namespace="f5-bnk", labels={"app": "nginx"})
        for api_client, _ in self._make_api_client(
            sweep_pods=[noise_pod],
            ns_pods={"f5-bnk": [noise_pod]},
        ):
            tenant_pods, utils_pods = discover_f5_pods(api_client)

        all_names = {p["name"] for p in tenant_pods + utils_pods}
        assert "nginx-abc" not in all_names

    def test_crd_installer_lands_in_utils(self):
        """crd-installer pod in known namespace (f5-utils) is discovered and lands in utils_pods.
        Name matches the crd-installer prefix so _is_bnk_pod admits it; label drives classification."""
        pod = _make_v1pod(
            "crd-installer-job-abc",
            namespace="f5-utils",
            labels={"app": "crd-installer"},
        )
        for api_client, _ in self._make_api_client(
            sweep_pods=[],
            ns_pods={"f5-utils": [pod]},
        ):
            tenant_pods, utils_pods = discover_f5_pods(api_client)

        assert any(p["name"] == "crd-installer-job-abc" for p in utils_pods)

    def test_returns_empty_on_api_failure(self):
        """discover_f5_pods must return ([], []) when K8s API is unavailable."""
        bad_client = MagicMock()
        with patch(
            "services.bnk_pod_discovery.k8s_client.CoreV1Api",
            side_effect=Exception("connection refused"),
        ):
            tenant_pods, utils_pods = discover_f5_pods(bad_client)

        assert tenant_pods == []
        assert utils_pods == []
