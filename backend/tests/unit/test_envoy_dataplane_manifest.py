"""
Unit tests for the Envoy data-plane manifest builder.

The upstream envoyproxy/gateway-helm chart only ships the controller; we
generate GatewayClass + EnvoyProxy + Gateway + HTTPRoute ourselves and apply
them via kubectl after Helm install.  These tests lock in the shape of that
YAML.

Document order (4-doc manifest):
  [0] GatewayClass  — cluster-scoped, shared
  [1] EnvoyProxy    — per-target ClusterIP binding (bare-metal addressing)
  [2] Gateway       — lives in the proxy/gateway namespace
  [3] HTTPRoute     — lives in the target's namespace (backendRefs are local)
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
import yaml

import services.proxy_deploy_service as pds
from core.errors import BadRequestError
from services.proxy_deploy_service import (
    AWS_INTERNAL_NLB_ANNOTATIONS,
    ENVOY_GATEWAY_CLASS_NAME,
    ENVOY_GATEWAY_CONTROLLER,
    PROXY_LISTEN_PORT,
    ProxyDeployService,
    _backend_svc_name,
    _backend_svc_port,
    _build_envoy_dataplane_manifest,
    _route_namespace,
)


def _target(llm_url: str = "http://vllm-agg-router-frontend.dynamo-system:8000",
            namespace: str = "dynamo-system") -> MagicMock:
    t = MagicMock()
    t.llm_base_url = llm_url
    t.llm_namespace = namespace
    return t


def _parse_docs(manifest: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(manifest) if d]


class TestEnvoyDataplaneManifest:
    def test_emits_four_documents_in_order(self):
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies", _target(),
        ))
        assert [d["kind"] for d in docs] == [
            "GatewayClass", "EnvoyProxy", "Gateway", "HTTPRoute",
        ]

    def test_envoyproxy_uses_clusterip_for_bare_metal(self):
        """EnvoyProxy doc forces ClusterIP so bare-metal gateways get an address."""
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies", _target(),
        ))
        ep = docs[1]
        assert ep["kind"] == "EnvoyProxy"
        assert ep["metadata"]["namespace"] == "perf-proxies"
        assert ep["metadata"]["name"] == "perf-envoy-x"
        svc_type = ep["spec"]["provider"]["kubernetes"]["envoyService"]["type"]
        assert svc_type == "ClusterIP"

    def test_gatewayclass_uses_envoy_controller(self):
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies", _target(),
        ))
        gc = docs[0]
        assert gc["metadata"]["name"] == ENVOY_GATEWAY_CLASS_NAME
        assert gc["spec"]["controllerName"] == ENVOY_GATEWAY_CONTROLLER

    def test_gateway_listener_uses_proxy_listen_port_and_allows_all_namespaces(self):
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies", _target(),
        ))
        gw = docs[2]
        assert gw["kind"] == "Gateway"
        assert gw["metadata"]["namespace"] == "perf-proxies"
        assert gw["spec"]["gatewayClassName"] == ENVOY_GATEWAY_CLASS_NAME
        listener = gw["spec"]["listeners"][0]
        assert listener["port"] == PROXY_LISTEN_PORT
        assert listener["protocol"] == "HTTP"
        # `from: All` is required because HTTPRoute lives in the target's
        # namespace, not the gateway's namespace.
        assert listener["allowedRoutes"]["namespaces"]["from"] == "All"

    def test_httproute_lives_in_target_namespace_and_backends_local_service(self):
        """HTTPRoute in target NS so backendRefs are local — no ReferenceGrant."""
        target = _target(
            "http://vllm-agg-router-frontend.dynamo-system:8000",
            namespace="dynamo-system",
        )
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies", target,
        ))
        route = docs[3]
        assert route["kind"] == "HTTPRoute"
        assert route["metadata"]["namespace"] == "dynamo-system"

        # parentRefs cross-namespace reference into the gateway namespace
        parent = route["spec"]["parentRefs"][0]
        assert parent["name"] == "perf-envoy-x"
        assert parent["namespace"] == "perf-proxies"

        # backendRefs are intentionally same-namespace (no `namespace` key)
        backend = route["spec"]["rules"][0]["backendRefs"][0]
        assert backend["name"] == "vllm-agg-router-frontend"
        assert backend["port"] == 8000
        assert "namespace" not in backend

    def test_release_name_used_as_gateway_and_route_name(self):
        """EnvoyProxy, Gateway and HTTPRoute all named after the release for cleanup symmetry."""
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-vllm-agg-router-frontend-ef1f71", "perf-proxies", _target(),
        ))
        assert docs[1]["metadata"]["name"] == "perf-envoy-vllm-agg-router-frontend-ef1f71"
        assert docs[2]["metadata"]["name"] == "perf-envoy-vllm-agg-router-frontend-ef1f71"
        assert docs[3]["metadata"]["name"] == "perf-envoy-vllm-agg-router-frontend-ef1f71"

    def test_managed_by_label_set_for_idempotent_redeploys(self):
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies", _target(),
        ))
        for d in docs[1:]:  # EnvoyProxy, Gateway, HTTPRoute (GatewayClass is shared, no label)
            assert d["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "bnk-forge"


def _target_with_tags(
    llm_url: str = "http://vllm.default:8000",
    namespace: str = "default",
    tags: dict | None = None,
) -> MagicMock:
    """Build a target mock with an explicit ``tags`` dict (not a MagicMock child)."""
    t = MagicMock()
    t.llm_base_url = llm_url
    t.llm_namespace = namespace
    t.tags = tags  # None means isinstance guard returns {} — ClusterIP path
    t.name = "test-target"
    return t


class TestEnvoyProxyNLBOptIn:
    """Opt-in tag ``tags["proxy_expose"] == "internal-nlb"`` must flip the
    EnvoyProxy to LoadBalancer + add the three AWS LB Controller annotations.
    The default (no tag / None tags) must remain byte-identical to the old
    ClusterIP shape with no ``annotations`` key.
    """

    def test_default_no_tags_is_clusterip_no_annotations(self):
        """Non-opted target (tags=None) must produce ClusterIP with no annotations key."""
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies", _target_with_tags(tags=None),
        ))
        ep = docs[1]
        assert ep["kind"] == "EnvoyProxy"
        envoy_svc = ep["spec"]["provider"]["kubernetes"]["envoyService"]
        assert envoy_svc["type"] == "ClusterIP"
        assert "annotations" not in envoy_svc, (
            "ClusterIP path must NOT emit annotations key (byte-identical to prior behaviour)"
        )

    def test_empty_tags_dict_is_clusterip_no_annotations(self):
        """Explicit empty dict tags also stays on the ClusterIP path."""
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies", _target_with_tags(tags={}),
        ))
        ep = docs[1]
        envoy_svc = ep["spec"]["provider"]["kubernetes"]["envoyService"]
        assert envoy_svc["type"] == "ClusterIP"
        assert "annotations" not in envoy_svc

    def test_internal_nlb_opt_in_sets_loadbalancer_type(self):
        """``proxy_expose: internal-nlb`` must switch envoyService.type to LoadBalancer."""
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies",
            _target_with_tags(tags={"proxy_expose": "internal-nlb"}),
        ))
        ep = docs[1]
        envoy_svc = ep["spec"]["provider"]["kubernetes"]["envoyService"]
        assert envoy_svc["type"] == "LoadBalancer"

    def test_internal_nlb_opt_in_adds_all_three_annotations(self):
        """All three AWS LB Controller annotations must be present on the NLB path."""
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies",
            _target_with_tags(tags={"proxy_expose": "internal-nlb"}),
        ))
        ep = docs[1]
        envoy_svc = ep["spec"]["provider"]["kubernetes"]["envoyService"]
        annotations = envoy_svc.get("annotations", {})
        for key, value in AWS_INTERNAL_NLB_ANNOTATIONS.items():
            assert annotations.get(key) == value, (
                f"Missing or wrong annotation {key!r}: got {annotations.get(key)!r}, "
                f"want {value!r}"
            )

    def test_internal_nlb_annotations_are_a_copy_not_the_constant(self):
        """Mutations to the emitted annotations must not pollute AWS_INTERNAL_NLB_ANNOTATIONS."""
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies",
            _target_with_tags(tags={"proxy_expose": "internal-nlb"}),
        ))
        ep = docs[1]
        envoy_svc = ep["spec"]["provider"]["kubernetes"]["envoyService"]
        emitted = envoy_svc["annotations"]
        emitted["extra-key"] = "mutated"
        assert "extra-key" not in AWS_INTERNAL_NLB_ANNOTATIONS

    def test_unrecognised_expose_intent_falls_back_to_clusterip(self):
        """An unknown proxy_expose value must not break deploy — defaults to ClusterIP."""
        docs = _parse_docs(_build_envoy_dataplane_manifest(
            "perf-envoy-x", "perf-proxies",
            _target_with_tags(tags={"proxy_expose": "future-unknown-type"}),
        ))
        ep = docs[1]
        envoy_svc = ep["spec"]["provider"]["kubernetes"]["envoyService"]
        assert envoy_svc["type"] == "ClusterIP"


class TestBackendSvcNameAndPort:
    """_backend_svc_name / _backend_svc_port override resolution and IP-literal guard."""

    def test_upstream_service_tag_overrides_url_parse(self):
        t = _target_with_tags(llm_url="http://10.0.10.108:8000", tags={"upstream_service": "vllm"})
        assert _backend_svc_name(t) == "vllm"

    def test_upstream_port_tag_overrides_url_parse(self):
        t = _target_with_tags(llm_url="http://10.0.10.108:8000", tags={"upstream_service": "vllm", "upstream_port": 80})
        assert _backend_svc_port(t) == 80

    def test_upstream_port_tag_as_string_is_cast_to_int(self):
        """Tags from JSON may come back as strings; _backend_svc_port must int-cast."""
        t = _target_with_tags(llm_url="http://svc.ns:9000", tags={"upstream_port": "80"})
        assert _backend_svc_port(t) == 80

    def test_no_override_dns_host_parses_correctly(self):
        t = _target_with_tags(llm_url="http://vllm-agg.default:8000", tags={})
        assert _backend_svc_name(t) == "vllm-agg"

    def test_no_override_port_defaults_from_url(self):
        t = _target_with_tags(llm_url="http://vllm.default:9000", tags={})
        assert _backend_svc_port(t) == 9000

    def test_ip_literal_without_override_raises_bad_request(self):
        """IP literal host + no upstream_service override must raise BadRequestError."""
        t = _target_with_tags(llm_url="http://10.0.10.108:8000", tags={})
        with pytest.raises(BadRequestError, match="upstream_service"):
            _backend_svc_name(t)

    def test_ipv6_literal_without_override_raises_bad_request(self):
        """Bracketed IPv6 llm_base_url must be caught by the IP-literal guard.

        Naive split(":") yields "[" for http://[::1]:8000 which fails
        ipaddress.ip_address with ValueError, silently bypassing the guard.
        urlparse.hostname correctly strips the brackets → "::1" which
        ipaddress.ip_address identifies as an IP → raises BadRequestError.
        """
        for v6_url in ("http://[::1]:8000", "http://[2001:db8::1]:8000"):
            t = _target_with_tags(llm_url=v6_url, tags={})
            with pytest.raises(BadRequestError, match="upstream_service"):
                _backend_svc_name(t)

    def test_ipv6_literal_with_override_does_not_raise(self):
        """Bracketed IPv6 URL + upstream_service override must NOT raise."""
        t = _target_with_tags(
            llm_url="http://[::1]:8000",
            tags={"upstream_service": "vllm"},
        )
        assert _backend_svc_name(t) == "vllm"

    def test_ip_literal_with_override_does_not_raise(self):
        """IP literal host WITH override must NOT raise — override path skips IP check."""
        t = _target_with_tags(llm_url="http://10.0.10.108:8000", tags={"upstream_service": "vllm"})
        assert _backend_svc_name(t) == "vllm"  # no exception

    def test_bare_mock_tags_attr_stays_on_clusterip_path(self):
        """Existing tests use bare MagicMock() targets; isinstance guard must keep them safe."""
        t = MagicMock()
        t.llm_base_url = "http://vllm.default:8000"
        t.llm_namespace = "default"
        # t.tags is a MagicMock child attribute — NOT a dict
        # _backend_svc_name must not call .get() on it
        result = _backend_svc_name(t)
        assert result == "vllm"  # falls through to _svc_name parse

    def test_ip_literal_scheme_less_without_override_raises_bad_request(self):
        """Bug fix: a scheme-less llm_base_url (no http:// prefix) must still be
        caught by the IP-literal guard.  urlparse("10.0.10.108:8000").hostname
        is None (no netloc without "//"), so the old code fell through to
        _svc_name() and returned the bogus service name "10" instead of raising.
        """
        t = _target_with_tags(llm_url="10.0.10.108:8000", tags={})
        with pytest.raises(BadRequestError, match="upstream_service"):
            _backend_svc_name(t)

    def test_ip_literal_scheme_less_with_override_does_not_raise(self):
        t = _target_with_tags(llm_url="10.0.10.108:8000", tags={"upstream_service": "vllm"})
        assert _backend_svc_name(t) == "vllm"

    def test_scheme_less_dns_host_still_parses_correctly(self):
        """A real DNS host with no scheme must keep resolving (not misidentified as an IP)."""
        t = _target_with_tags(llm_url="vllm-svc.ns:8000", tags={})
        assert _backend_svc_name(t) == "vllm-svc"

    def test_upstream_port_tag_non_numeric_string_raises_bad_request(self):
        """A free-form JSON tag value like "grpc" must raise a clean BadRequestError,
        not an uncaught ValueError."""
        t = _target_with_tags(llm_url="http://svc.ns:9000", tags={"upstream_port": "grpc"})
        with pytest.raises(BadRequestError, match="upstream_port"):
            _backend_svc_port(t)

    def test_upstream_port_tag_bool_raises_bad_request(self):
        """int(True) == 1 would silently produce a bogus port; bools must be rejected."""
        t = _target_with_tags(llm_url="http://svc.ns:9000", tags={"upstream_port": True})
        with pytest.raises(BadRequestError, match="upstream_port"):
            _backend_svc_port(t)

    def test_upstream_port_tag_list_raises_bad_request(self):
        t = _target_with_tags(llm_url="http://svc.ns:9000", tags={"upstream_port": [8000]})
        with pytest.raises(BadRequestError, match="upstream_port"):
            _backend_svc_port(t)

    def test_manifest_backend_uses_upstream_service_override(self):
        """End-to-end: target with upstream_service tag routes the HTTPRoute to the right Service."""
        t = _target_with_tags(
            llm_url="http://10.0.10.108:8000",
            namespace="awsbnkctl-scn-aiinference",
            tags={"upstream_service": "vllm", "upstream_port": 80},
        )
        docs = _parse_docs(_build_envoy_dataplane_manifest("perf-envoy-x", "perf-proxies", t))
        route = docs[3]
        backend = route["spec"]["rules"][0]["backendRefs"][0]
        assert backend["name"] == "vllm"
        assert backend["port"] == 80

    def test_manifest_ip_literal_without_override_raises(self):
        """Building the manifest for an IP-literal target without upstream_service must raise."""
        t = _target_with_tags(
            llm_url="http://10.0.10.108:8000",
            namespace="default",
            tags={},
        )
        with pytest.raises(BadRequestError, match="upstream_service"):
            _build_envoy_dataplane_manifest("perf-envoy-x", "perf-proxies", t)


class TestRouteNamespaceResolution:
    """The HTTPRoute (and its namespace-LESS backendRef) must land in the
    namespace that actually hosts the vLLM Service. ``llm_namespace`` is often
    forge's ``"default"`` placeholder; ``tags["upstream_namespace"]`` overrides it.
    """

    def test_upstream_namespace_tag_overrides_llm_namespace(self):
        t = _target_with_tags(
            namespace="default",
            tags={"upstream_namespace": "awsbnkctl-scn-aiinference"},
        )
        assert _route_namespace(t) == "awsbnkctl-scn-aiinference"

    def test_falls_back_to_llm_namespace_when_tag_absent(self):
        t = _target_with_tags(namespace="dynamo-system", tags={})
        assert _route_namespace(t) == "dynamo-system"

    def test_falls_back_to_default_when_both_absent(self):
        t = _target_with_tags(namespace=None, tags={})
        assert _route_namespace(t) == "default"

    def test_bare_mock_tags_attr_stays_on_llm_namespace(self):
        """Bare MagicMock().tags must not flip to the override branch (isinstance guard)."""
        t = MagicMock()
        t.llm_namespace = "dynamo-system"
        # t.tags is a MagicMock child attribute — NOT a dict
        assert _route_namespace(t) == "dynamo-system"

    def test_manifest_httproute_uses_upstream_namespace_tag(self):
        """End-to-end: upstream_namespace tag places the HTTPRoute in the right NS."""
        t = _target_with_tags(
            llm_url="http://10.0.10.108:8000",
            namespace="default",
            tags={
                "upstream_service": "vllm",
                "upstream_namespace": "awsbnkctl-scn-aiinference",
            },
        )
        docs = _parse_docs(_build_envoy_dataplane_manifest("perf-envoy-x", "perf-proxies", t))
        route = docs[3]
        assert route["kind"] == "HTTPRoute"
        assert route["metadata"]["namespace"] == "awsbnkctl-scn-aiinference"

    def test_manifest_httproute_falls_back_to_llm_namespace(self):
        """No upstream_namespace tag → HTTPRoute lands in llm_namespace (legacy behaviour)."""
        t = _target_with_tags(
            llm_url="http://vllm.dynamo-system:8000",
            namespace="dynamo-system",
            tags={},
        )
        docs = _parse_docs(_build_envoy_dataplane_manifest("perf-envoy-x", "perf-proxies", t))
        route = docs[3]
        assert route["metadata"]["namespace"] == "dynamo-system"


def _service() -> ProxyDeployService:
    return ProxyDeployService(db=MagicMock())


def _deploy_row() -> MagicMock:
    d = MagicMock()
    d.proxy_type = "envoy"
    return d


@contextmanager
def _fake_kubeconfig(cluster, db):
    yield "/tmp/fake-kubeconfig"


def _patch_dataplane(monkeypatch, address: str = "internal-nlb.elb.amazonaws.com") -> None:
    """No-op the module-level kubectl/kubeconfig helpers so post/pre-install
    envoy stay unit-scoped (no cluster contact); _wait_for_gateway_address
    returns a stable address."""
    monkeypatch.setattr(pds, "kubeconfig_for_cluster", _fake_kubeconfig)
    monkeypatch.setattr(pds, "_kubectl_apply", lambda *a, **k: None)
    monkeypatch.setattr(pds, "_kubectl_delete", lambda *a, **k: None)
    monkeypatch.setattr(pds, "_wait_for_gateway_address", lambda *a, **k: address)


class TestPostInstallEnvoyExternalUrl:
    """``_post_install_envoy`` must surface ``external_url`` so the benchmark can
    resolve the front-end endpoint. Only the internal-NLB path produces a
    reachable URL; the default ClusterIP path returns None (benchmark fail-closes).
    """

    def test_internal_nlb_returns_external_url_with_scheme(self, monkeypatch):
        addr = "internal-nlb.elb.amazonaws.com"
        _patch_dataplane(monkeypatch, address=addr)
        svc = _service()
        svc._get_cluster = lambda _id: MagicMock(context="ctx")
        target = _target_with_tags(
            llm_url="http://10.0.10.108:8000",
            namespace="awsbnkctl-scn-aiinference",
            tags={
                "upstream_service": "vllm",
                "upstream_namespace": "awsbnkctl-scn-aiinference",
                "proxy_expose": "internal-nlb",
            },
        )
        target.cluster_id = 1
        proxy_url, external_url = svc._post_install_envoy(
            _deploy_row(), target, "perf-envoy-x", "perf-proxies", None,
        )
        assert proxy_url == f"http://{addr}:{PROXY_LISTEN_PORT}"
        assert external_url == f"http://{addr}:{PROXY_LISTEN_PORT}"

    def test_clusterip_path_returns_none_external_url(self, monkeypatch):
        addr = "10.0.0.5"
        _patch_dataplane(monkeypatch, address=addr)
        svc = _service()
        svc._get_cluster = lambda _id: MagicMock(context="ctx")
        target = _target_with_tags(
            llm_url="http://vllm.dynamo-system:8000",
            namespace="dynamo-system",
            tags={},
        )
        target.cluster_id = 1
        proxy_url, external_url = svc._post_install_envoy(
            _deploy_row(), target, "perf-envoy-x", "perf-proxies", None,
        )
        assert proxy_url == f"http://{addr}:{PROXY_LISTEN_PORT}"
        assert external_url is None


class TestPreUninstallEnvoyRouteNamespace:
    """``_pre_uninstall_envoy`` must delete the HTTPRoute from the SAME namespace
    it was created in (upstream_namespace when the tag is present), else the
    route leaks after undeploy."""

    def _capture_deletes(self, monkeypatch) -> list[tuple]:
        deletes: list[tuple] = []
        monkeypatch.setattr(pds, "kubeconfig_for_cluster", _fake_kubeconfig)

        def _rec(kubeconfig_path, resource, namespace, **kwargs):
            deletes.append((resource, namespace))

        monkeypatch.setattr(pds, "_kubectl_delete", _rec)
        return deletes

    def test_deletes_httproute_from_upstream_namespace(self, monkeypatch):
        deletes = self._capture_deletes(monkeypatch)
        svc = _service()
        svc._get_cluster = lambda _id: MagicMock(context="ctx")
        target = _target_with_tags(
            namespace="default",
            tags={"upstream_namespace": "awsbnkctl-scn-aiinference"},
        )
        target.cluster_id = 1
        svc._pre_uninstall_envoy(
            _deploy_row(), target, "perf-envoy-x", "perf-proxies", None,
        )
        route_delete = next(d for d in deletes if d[0] == "httproute/perf-envoy-x")
        assert route_delete[1] == "awsbnkctl-scn-aiinference"

    def test_deletes_httproute_from_llm_namespace_when_no_tag(self, monkeypatch):
        deletes = self._capture_deletes(monkeypatch)
        svc = _service()
        svc._get_cluster = lambda _id: MagicMock(context="ctx")
        target = _target_with_tags(namespace="dynamo-system", tags={})
        target.cluster_id = 1
        svc._pre_uninstall_envoy(
            _deploy_row(), target, "perf-envoy-x", "perf-proxies", None,
        )
        route_delete = next(d for d in deletes if d[0] == "httproute/perf-envoy-x")
        assert route_delete[1] == "dynamo-system"
