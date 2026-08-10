"""
Unit tests for services.scanner.bnk_install — BNK installation analysis.

Pure functions, no I/O, no mocking needed.
"""

from services.scanner.bnk_install import (
    _determine_status,
    _extract_flo_version,
    _extract_image_version,
    _find_flo_helm_release,
    _parse_cneinstance,
    _parse_vlans,
    _tmm_container_summary,
    analyze_bnk_install,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pod(name, phase="Running", namespace="f5-bnk", containers=None, labels=None):
    return {
        "name": name,
        "namespace": namespace,
        "phase": phase,
        "labels": labels or {},
        "containers": containers or [],
    }


def _make_crd(name, group, kind):
    return {"name": name, "group": group, "kind": kind, "versions": ["v1"]}


# =========================================================================
# _extract_flo_version
# =========================================================================


class TestExtractFloVersion:
    def test_extracts_from_lifecycle_image(self):
        pods = [_make_pod("flo-f5-lifecycle-abc", containers=[
            {"image": "f5networks/f5-lifecycle-operator:v2.2.0-0.8.14", "name": "flo"}
        ])]
        assert _extract_flo_version(pods) == "2.2.0-0.8.14"

    def test_extracts_from_generic_tag(self):
        pods = [_make_pod("flo-abc", containers=[
            {"image": "some-registry.io/flo:v1.5.3", "name": "flo"}
        ])]
        assert _extract_flo_version(pods) == "1.5.3"

    def test_no_pods_returns_none(self):
        assert _extract_flo_version([]) is None

    def test_no_version_in_image_returns_none(self):
        pods = [_make_pod("flo-abc", containers=[
            {"image": "flo:latest", "name": "flo"}
        ])]
        assert _extract_flo_version(pods) is None


# =========================================================================
# _extract_image_version
# =========================================================================


class TestExtractImageVersion:
    def test_extracts_controller_tag_after_last_colon(self):
        pods = [_make_pod("f5ingress-f5ingress-abc", containers=[
            {"name": "f5ingress-f5ingress", "image": "repo.f5.com/images/f5ingress:v14.59.1-0.0.70"},
        ])]
        assert _extract_image_version(pods, image_hint="f5ingress") == "v14.59.1-0.0.70"

    def test_extracts_tmm_tag(self):
        pods = [_make_pod("f5-tmm-abc", containers=[
            {"name": "tmm", "image": "repo.f5.com/images/tmm-img:v10.159.3-0.1.5"},
        ])]
        assert _extract_image_version(pods, image_hint="tmm") == "v10.159.3-0.1.5"

    def test_ignores_registry_port_and_anchors_on_final_tag(self):
        pods = [_make_pod("f5ingress-abc", containers=[
            {"name": "f5ingress-f5ingress", "image": "repo.f5.com:5000/images/f5ingress:v14.59.1"},
        ])]
        assert _extract_image_version(pods, image_hint="f5ingress") == "v14.59.1"

    def test_hint_prefers_matching_container_over_sidecar(self):
        pods = [_make_pod("f5ingress-abc", containers=[
            {"name": "istio-proxy", "image": "docker.io/istio/proxyv2:1.20.0"},
            {"name": "f5ingress-f5ingress", "image": "repo.f5.com/images/f5ingress:v14.59.1-0.0.70"},
        ])]
        assert _extract_image_version(pods, image_hint="f5ingress") == "v14.59.1-0.0.70"

    def test_falls_back_to_first_tag_when_hint_absent(self):
        pods = [_make_pod("f5-tmm-abc", containers=[
            {"name": "tmm", "image": "some-registry.io/tmm:v9.9.9"},
        ])]
        assert _extract_image_version(pods) == "v9.9.9"

    def test_no_version_tag_returns_none(self):
        pods = [_make_pod("f5ingress-abc", containers=[
            {"name": "f5ingress-f5ingress", "image": "repo.f5.com/images/f5ingress:latest"},
        ])]
        assert _extract_image_version(pods, image_hint="f5ingress") is None

    def test_no_pods_returns_none(self):
        assert _extract_image_version([], image_hint="f5ingress") is None


# =========================================================================
# _find_flo_helm_release
# =========================================================================


class TestFindFloHelmRelease:
    def test_finds_f5_lifecycle_release(self):
        releases = [
            {"name": "cert-manager", "namespace": "cert-manager", "version": "1", "status": "deployed"},
            {"name": "f5-lifecycle-operator", "namespace": "f5-operator", "version": "3", "status": "deployed"},
        ]
        result = _find_flo_helm_release(releases)
        assert result["name"] == "f5-lifecycle-operator"

    def test_falls_back_to_f5_operator_namespace(self):
        releases = [
            {"name": "my-flo", "namespace": "f5-operator", "version": "1", "status": "deployed"},
        ]
        result = _find_flo_helm_release(releases)
        assert result["name"] == "my-flo"

    def test_returns_none_when_not_found(self):
        releases = [{"name": "cert-manager", "namespace": "cert-manager", "version": "1", "status": "deployed"}]
        assert _find_flo_helm_release(releases) is None

    def test_empty_releases(self):
        assert _find_flo_helm_release([]) is None


# =========================================================================
# _tmm_container_summary
# =========================================================================


class TestTmmContainerSummary:
    def test_all_ready(self):
        pods = [_make_pod("f5-tmm-abc", containers=[
            {"name": "tmm", "ready": True, "image": "f5-tmm:v1"},
            {"name": "debug", "ready": True, "image": "debug:v1"},
        ])]
        result = _tmm_container_summary(pods)
        assert result["total_containers"] == 2
        assert result["ready_containers"] == 2
        assert result["containers"] == "2/2"

    def test_partial_ready(self):
        pods = [_make_pod("f5-tmm-abc", containers=[
            {"name": "tmm", "ready": True, "image": "f5-tmm:v1"},
            {"name": "debug", "ready": False, "image": "debug:v1"},
            {"name": "sidecar", "ready": True, "image": "sidecar:v1"},
        ])]
        result = _tmm_container_summary(pods)
        assert result["ready_containers"] == 2
        assert result["containers"] == "2/3"

    def test_no_pods(self):
        assert _tmm_container_summary([]) is None


# =========================================================================
# _parse_cneinstance
# =========================================================================


class TestParseCneinstance:
    def test_parses_features(self):
        cneinstances = [{
            "metadata": {"name": "my-cne"},
            "spec": {
                "firewallACL": {"enabled": True},
                "intelligentLB": {"enabled": False},
                "pseudoCNI": {"enabled": True},
                "metricSubsystem": {"enabled": True},
                "loggingSubsystem": {"enabled": False},
            },
        }]
        result = _parse_cneinstance(cneinstances)
        assert result["name"] == "my-cne"
        assert result["features"]["firewallACL"] is True
        assert result["features"]["intelligentLB"] is False
        assert result["features"]["pseudoCNI"] is True

    def test_missing_features_default_false(self):
        cneinstances = [{"metadata": {"name": "cne"}, "spec": {}}]
        result = _parse_cneinstance(cneinstances)
        assert all(v is False for v in result["features"].values())

    def test_no_cneinstances(self):
        assert _parse_cneinstance([]) is None


# =========================================================================
# _parse_vlans
# =========================================================================


class TestParseVlans:
    def test_programmed_vlan(self):
        vlans = [{
            "metadata": {"name": "external"},
            "spec": {
                "interfaces": ["1.1"],
                "selfip_v4s": ["10.1.1.1/24"],
                "mtu": 1500,
            },
            "status": {
                "conditions": [
                    {"type": "Programmed", "status": "True"},
                ]
            },
        }]
        result = _parse_vlans(vlans)
        assert len(result) == 1
        assert result[0]["name"] == "external"
        assert result[0]["programmed"] is True
        assert result[0]["mtu"] == 1500

    def test_not_programmed_vlan(self):
        vlans = [{
            "metadata": {"name": "internal"},
            "spec": {"interfaces": [], "selfip_v4s": []},
            "status": {"conditions": [{"type": "Programmed", "status": "False"}]},
        }]
        result = _parse_vlans(vlans)
        assert result[0]["programmed"] is False

    def test_no_vlans(self):
        assert _parse_vlans([]) == []

    def test_vlan_no_conditions(self):
        vlans = [{"metadata": {"name": "v1"}, "spec": {}, "status": {}}]
        result = _parse_vlans(vlans)
        assert result[0]["programmed"] is False


# =========================================================================
# _determine_status
# =========================================================================


class TestDetermineStatus:
    def test_fully_installed_healthy(self):
        container_info = {"ready_containers": 5, "total_containers": 5, "containers": "5/5"}
        status, health = _determine_status(
            tmm_running=[1], flo_running=[1], has_f5_crds=True,
            tmm_container_info=container_info, f5_crds=[1], flo_pods=[1],
            ns_info={"f5_operator": True, "f5_bnk": True, "f5_utils": True},
        )
        assert status == "installed"
        assert health == "healthy"

    def test_fully_installed_degraded(self):
        container_info = {"ready_containers": 3, "total_containers": 5, "containers": "3/5"}
        status, health = _determine_status(
            tmm_running=[1], flo_running=[1], has_f5_crds=True,
            tmm_container_info=container_info, f5_crds=[1], flo_pods=[1],
            ns_info={},
        )
        assert status == "installed"
        assert health == "degraded"

    def test_installed_no_container_info(self):
        """TMM running but no container details → degraded."""
        status, health = _determine_status(
            tmm_running=[1], flo_running=[1], has_f5_crds=True,
            tmm_container_info=None, f5_crds=[1], flo_pods=[1],
            ns_info={},
        )
        assert status == "installed"
        assert health == "degraded"

    def test_partial_crds_only(self):
        status, health = _determine_status(
            tmm_running=[], flo_running=[], has_f5_crds=True,
            tmm_container_info=None, f5_crds=[1], flo_pods=[],
            ns_info={},
        )
        assert status == "partial"
        assert health == "degraded"

    def test_partial_flo_running_only(self):
        status, health = _determine_status(
            tmm_running=[], flo_running=[1], has_f5_crds=False,
            tmm_container_info=None, f5_crds=[], flo_pods=[1],
            ns_info={},
        )
        assert status == "partial"
        assert health == "degraded"

    def test_partial_namespace_only(self):
        status, health = _determine_status(
            tmm_running=[], flo_running=[], has_f5_crds=False,
            tmm_container_info=None, f5_crds=[], flo_pods=[],
            ns_info={"f5_operator": True},
        )
        assert status == "partial"
        assert health == "degraded"

    def test_not_installed(self):
        status, health = _determine_status(
            tmm_running=[], flo_running=[], has_f5_crds=False,
            tmm_container_info=None, f5_crds=[], flo_pods=[],
            ns_info={"f5_operator": False, "f5_bnk": False},
        )
        assert status == "not_installed"
        assert health is None

    def test_helm_shape_installed_via_controller(self):
        """Direct-helm install (TMM + f5ingress controller, no FLO) is installed."""
        container_info = {"ready_containers": 4, "total_containers": 4, "containers": "4/4"}
        status, health = _determine_status(
            tmm_running=[1], flo_running=[], has_f5_crds=True,
            tmm_container_info=container_info, f5_crds=[1], flo_pods=[],
            ns_info={},
            controller_running=[1],
        )
        assert status == "installed"
        assert health == "healthy"

    def test_helm_shape_installed_via_cne_instance(self):
        """TMM + CRDs + a live CNEInstance is enough evidence without FLO or controller."""
        status, health = _determine_status(
            tmm_running=[1], flo_running=[], has_f5_crds=True,
            tmm_container_info=None, f5_crds=[1], flo_pods=[],
            ns_info={},
            controller_running=[],
            cne_instance_present=True,
        )
        assert status == "installed"
        assert health == "degraded"

    def test_helm_shape_no_controller_no_cne_stays_partial(self):
        """TMM + CRDs alone (no FLO, no controller, no CNEInstance) is still partial."""
        status, health = _determine_status(
            tmm_running=[1], flo_running=[], has_f5_crds=True,
            tmm_container_info=None, f5_crds=[1], flo_pods=[],
            ns_info={},
        )
        assert status == "partial"
        assert health == "degraded"

    def test_flo_shape_still_installed_no_regression(self):
        """A FLO install with FLO running still reports installed."""
        container_info = {"ready_containers": 5, "total_containers": 5, "containers": "5/5"}
        status, health = _determine_status(
            tmm_running=[1], flo_running=[1], has_f5_crds=True,
            tmm_container_info=container_info, f5_crds=[1], flo_pods=[1],
            ns_info={"f5_operator": True},
        )
        assert status == "installed"
        assert health == "healthy"


# =========================================================================
# analyze_bnk_install (integration of sub-functions)
# =========================================================================


class TestAnalyzeBnkInstall:
    def test_full_installation(self):
        crds = [
            _make_crd("tmms.k8s.f5net.com", "k8s.f5net.com", "TMM"),
            _make_crd("flos.k8s.f5.com", "k8s.f5.com", "FLO"),
        ]
        crd_names = {c["name"] for c in crds}
        crd_groups = {c["group"] for c in crds}
        tenant_pods = [
            _make_pod("f5-tmm-abc", "Running", containers=[
                {"name": "tmm", "ready": True, "image": "f5-tmm:v1"},
            ]),
            _make_pod("flo-f5-lifecycle-xyz", "Running", containers=[
                {"image": "f5networks/f5-lifecycle-operator:v2.2.0-0.8.14", "name": "flo", "ready": True}
            ]),
        ]
        utils_pods = [
            _make_pod("crd-installer-abc", "Succeeded", "f5-utils"),
        ]
        result = analyze_bnk_install(
            crds, crd_names, crd_groups, tenant_pods, utils_pods,
            [], [], [], ["default", "f5-bnk", "f5-operator", "f5-utils"],
        )
        assert result["status"] == "installed"
        assert result["health"] == "healthy"
        assert result["flo"]["version"] == "2.2.0-0.8.14"
        assert result["tmm"]["running"] == 1
        assert result["crds"]["has_data_plane"] is True
        assert result["crds"]["has_flo"] is True
        assert result["namespaces"]["f5_bnk"] is True
        assert result["crd_installer"]["completed"] is True

    def test_not_installed(self):
        result = analyze_bnk_install(
            [], set(), set(), [], [], [], [], [], ["default", "kube-system"],
        )
        assert result["status"] == "not_installed"
        assert result["health"] is None
        assert result["flo"]["pods"] == 0
        assert result["tmm"]["pods"] == 0

    def test_partial_only_crds(self):
        crds = [_make_crd("tmms.k8s.f5net.com", "k8s.f5net.com", "TMM")]
        result = analyze_bnk_install(
            crds, {c["name"] for c in crds}, {c["group"] for c in crds},
            [], [], [], [], [], ["default"],
        )
        assert result["status"] == "partial"

    def test_vlans_and_cneinstance_parsed(self):
        crds = [
            _make_crd("tmms.k8s.f5net.com", "k8s.f5net.com", "TMM"),
        ]
        tenant_pods = [
            _make_pod("f5-tmm-abc", "Running", containers=[{"name": "tmm", "ready": True, "image": "f5-tmm:v1"}]),
            _make_pod("flo-f5-lifecycle-xyz", "Running", containers=[{"name": "flo", "ready": True, "image": "f5-lifecycle:v1"}]),
        ]
        cneinstances = [{"metadata": {"name": "cne1"}, "spec": {"firewallACL": {"enabled": True}}}]
        vlans = [{"metadata": {"name": "ext"}, "spec": {"interfaces": ["1.1"]}, "status": {"conditions": [{"type": "Programmed", "status": "True"}]}}]
        result = analyze_bnk_install(
            crds, {c["name"] for c in crds}, {c["group"] for c in crds},
            tenant_pods, [], [], cneinstances, vlans, ["default", "f5-bnk"],
        )
        assert result["cne_instance"]["name"] == "cne1"
        assert result["cne_instance"]["features"]["firewallACL"] is True
        assert len(result["vlans"]) == 1
        assert result["vlans"][0]["programmed"] is True

    def test_helm_shape_reports_installed_and_install_shape_helm(self):
        """Direct-helm install (kind-bnkfull ground truth): TMM + f5ingress
        controller + F5 CRDs + CNEInstance, no FLO pod → installed, shape=helm."""
        crds = [
            _make_crd("f5-big-fw-policies.k8s.f5net.com", "k8s.f5net.com", "FirewallPolicy"),
        ]
        tenant_pods = [
            _make_pod("f5-tmm-b5f85df65-mwdhq", "Running", labels={"app": "f5-tmm"}, containers=[
                {"name": "f5-tmm", "ready": True, "image": "f5-tmm:v1"},
                {"name": "debug", "ready": True, "image": "debug:v1"},
                {"name": "blobd", "ready": True, "image": "blobd:v1"},
                {"name": "observer", "ready": True, "image": "observer:v1"},
            ]),
            _make_pod("f5ingress-f5ingress-84d678d46c-v7lvv", "Running", labels={"app": "f5ingress-f5ingress"}, containers=[
                {"name": "f5ingress-f5ingress", "ready": True, "image": "f5ingress:v1"},
            ]),
        ]
        cneinstances = [{"metadata": {"name": "cne1"}, "spec": {}}]
        result = analyze_bnk_install(
            crds, {c["name"] for c in crds}, {c["group"] for c in crds},
            tenant_pods, [], [], cneinstances, [], ["default", "f5-cne-core"],
        )
        assert result["status"] == "installed"
        assert result["install_shape"] == "helm"
        assert result["flo"]["running"] == 0
        assert result["controller"]["running"] == 1
        assert result["namespaces"]["f5_cne_core"] is True

    def test_flo_shape_install_shape_flo_no_regression(self):
        crds = [_make_crd("tmms.k8s.f5net.com", "k8s.f5net.com", "TMM")]
        tenant_pods = [
            _make_pod("f5-tmm-abc", "Running", labels={"app": "f5-tmm"}, containers=[
                {"name": "tmm", "ready": True, "image": "f5-tmm:v1"},
            ]),
            _make_pod("flo-f5-lifecycle-xyz", "Running", containers=[
                {"image": "f5networks/f5-lifecycle-operator:v2.2.0-0.8.14", "name": "flo", "ready": True}
            ]),
        ]
        result = analyze_bnk_install(
            crds, {c["name"] for c in crds}, {c["group"] for c in crds},
            tenant_pods, [], [], [], [], ["default", "f5-bnk"],
        )
        assert result["status"] == "installed"
        assert result["install_shape"] == "flo"

    def test_empty_cluster_not_installed_shape_unknown(self):
        result = analyze_bnk_install(
            [], set(), set(), [], [], [], [], [], ["default", "kube-system"],
        )
        assert result["status"] == "not_installed"
        assert result["install_shape"] == "unknown"
