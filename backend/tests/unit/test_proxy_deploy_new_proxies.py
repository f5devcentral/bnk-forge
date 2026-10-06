"""
Unit tests for the envoy-ai-gateway proxy type.

Covers:
  - the `_values_envoy_ai_gateway` Helm-values builder,
  - the singleton control-plane install path (`_deploy_envoy_ai_gateway`),
  - the per-target EPP data-plane manifest builder (`_build_inference_epp_manifest`),
  - the GAIE Gateway+HTTPRoute manifest builder (`_build_gaie_gateway_manifest`),
  - the Envoy Gateway base values builder (`_envoy_gateway_base_values`),
  - the `_context_args` free-function helper that threads `--context`.
"""

import os
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
import yaml

import services.proxy_deploy_service as pds
from core.errors import BadRequestError, ReleaseNotFoundError
from services.proxy_deploy_service import (
    AGENTGATEWAY_CLASS_NAME,
    AGENTGATEWAY_CRDS_RELEASE,
    AGENTGATEWAY_PARAMS_KIND,
    AGENTGATEWAY_RELEASE,
    AI_GATEWAY_CONTROLLER_RELEASE,
    AI_GATEWAY_CRDS_RELEASE,
    AI_GATEWAY_NAMESPACE,
    ENVOY_GATEWAY_CLASS_NAME,
    ENVOY_GATEWAY_CONTROLLER,
    ENVOY_GATEWAY_RELEASE,
    GIE_EPP_IMAGE,
    LLM_D_ROUTER_CHART,
    LLM_D_ROUTER_DEFAULT_KV_EVENTS_PORT,
    LLM_D_ROUTER_VERSION,
    PROXY_LISTEN_PORT,
    ProxyDeployService,
    TargetRouting,
    _build_agentgateway_manifest,
    _build_gaie_gateway_manifest,
    _build_inference_epp_manifest,
    _build_llm_d_router_values,
    _context_args,
    _envoy_gateway_base_values,
    _f5_epp_kv_events,
    _hf_model_from_pods,
    _is_llm_d_simulator,
    _kv_events_port_from_pods,
    _model_from_container,
    _parse_kv_events_port,
    _pod_port_from_service,
    _port_from_kv_events_config,
    _precise_prefix_cache_config,
    _served_model_from_container,
)


def _target(
    llm_url: str = "http://vllm-qwen.default:8000",
    namespace: str = "default",
    llm_model: str = "Qwen/Qwen3-32B",
) -> MagicMock:
    t = MagicMock()
    t.llm_base_url = llm_url
    t.llm_namespace = namespace
    t.proxy_namespace = "perf-proxies"
    t.llm_model = llm_model
    return t


def _service() -> ProxyDeployService:
    # The values builders don't touch the DB; a stub session is enough.
    return ProxyDeployService(db=MagicMock())


def _parse_docs(manifest: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(manifest) if d]


@contextmanager
def _env(key: str, value: str):
    """Temporarily set an env var, restoring the prior value on exit."""
    prior = os.environ.get(key)
    os.environ[key] = value
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prior


def _patch_dataplane(monkeypatch) -> None:
    """Stub the module-level kubectl/kubeconfig helpers the deploy path now calls.

    The singleton control-plane tests mock ``self.helm`` but the per-target data
    plane goes through module-level free functions + ``kubeconfig_for_cluster``;
    no-op them so the tests stay unit-scoped (no cluster contact).
    """

    @contextmanager
    def _fake_kubeconfig(cluster, db):
        yield "/tmp/fake-kubeconfig"

    monkeypatch.setattr(pds, "kubeconfig_for_cluster", _fake_kubeconfig)
    monkeypatch.setattr(pds, "_kubectl_apply", lambda *a, **k: None)
    monkeypatch.setattr(pds, "_kubectl_apply_url", lambda *a, **k: None)
    monkeypatch.setattr(pds, "_wait_for_gateway_address", lambda *a, **k: "10.0.0.5")
    monkeypatch.setattr(pds, "_render_endpoint_available", lambda *a, **k: True)
    monkeypatch.setattr(pds, "_gie_crds_present", lambda *a, **k: True)


class TestEnvoyAiGatewayValues:
    def test_returns_empty_defaults(self):
        """Controller chart installs with defaults; routing is via CRs out of band."""
        svc = _service()
        assert svc._values_envoy_ai_gateway(MagicMock(), _target()) == {}


class TestEnvoyAiGatewaySingletonControlPlane:
    """The AI Gateway control plane is a cluster-wide singleton: fixed release
    names, installed only when absent, never under a per-target release name."""

    def _svc(self, *, control_plane_exists: bool) -> ProxyDeployService:
        svc = _service()
        svc.helm = MagicMock()
        if control_plane_exists:
            svc.helm.get_release.return_value = {"name": "present"}
        else:
            # get_release raises the TYPED not-found error for a genuinely absent
            # release; _release_exists catches only this (not generic RuntimeError).
            svc.helm.get_release.side_effect = ReleaseNotFoundError("eg")
        return svc

    def _deploy(self):
        d = MagicMock()
        d.helm_chart = None
        d.helm_version = None
        d.helm_values = None
        d.proxy_type = "envoy-ai-gateway"
        return d

    def test_installs_fixed_singleton_releases_when_absent(self, monkeypatch):
        _patch_dataplane(monkeypatch)
        svc = self._svc(control_plane_exists=False)
        target = _target()
        target.cluster_id = 1
        url = svc._deploy_envoy_ai_gateway(
            self._deploy(), target, MagicMock(),
            "perf-envoy-ai-gateway-per-target", AI_GATEWAY_NAMESPACE, None, MagicMock(),
        )
        installed = [c.kwargs["release_name"] for c in svc.helm.install_chart.call_args_list]
        assert installed == [
            ENVOY_GATEWAY_RELEASE, AI_GATEWAY_CRDS_RELEASE, AI_GATEWAY_CONTROLLER_RELEASE,
        ]
        # Never installs the controller under a per-target release (the original bug).
        assert "perf-envoy-ai-gateway-per-target" not in installed
        # URL now comes from the per-target Gateway data-plane address.
        assert url == f"http://10.0.0.5:{PROXY_LISTEN_PORT}"

    def test_skips_install_when_control_plane_present(self, monkeypatch):
        _patch_dataplane(monkeypatch)
        svc = self._svc(control_plane_exists=True)
        target = _target()
        target.cluster_id = 1
        svc._deploy_envoy_ai_gateway(
            self._deploy(), target, MagicMock(),
            "perf-envoy-ai-gateway-per-target", AI_GATEWAY_NAMESPACE, None, MagicMock(),
        )
        svc.helm.install_chart.assert_not_called()


class TestInferenceEppManifest:
    """The per-target EPP data plane (InferencePool + EPP + RBAC) backing the AI
    Gateway. ``prefix-cache-scorer`` in the config is what makes routing approximate
    prefix-cache aware."""

    def _docs(self, release="rel", ns="default", labels=None, port=8000):
        return _parse_docs(_build_inference_epp_manifest(release, ns, labels or {"app": "vllm-qwen"}, port))

    def _by_kind(self, docs, kind):
        return next(d for d in docs if d["kind"] == kind)

    def test_inferencepool_selector_ports_and_epp_ref(self):
        pool = self._by_kind(self._docs(), "InferencePool")
        assert pool["spec"]["selector"]["matchLabels"] == {"app": "vllm-qwen"}
        assert pool["spec"]["targetPorts"] == [{"number": 8000}]
        assert pool["spec"]["endpointPickerRef"]["name"] == "rel-epp"
        assert pool["spec"]["endpointPickerRef"]["port"]["number"] == 9002

    def test_inferencepool_uses_v1_api(self):
        pool = self._by_kind(self._docs(), "InferencePool")
        assert pool["apiVersion"] == "inference.networking.k8s.io/v1"

    def test_configmap_enables_prefix_cache_scorer(self):
        cm = self._by_kind(self._docs(), "ConfigMap")
        config = cm["data"]["default-plugins.yaml"]
        assert "prefix-cache-scorer" in config
        assert "queue-scorer" in config
        assert "kv-cache-utilization-scorer" in config

    def test_deployment_image_and_pool_args(self):
        dep = self._by_kind(self._docs(), "Deployment")
        container = dep["spec"]["template"]["spec"]["containers"][0]
        assert container["image"] == GIE_EPP_IMAGE
        args = container["args"]
        assert args[args.index("--pool-name") + 1] == "rel"
        assert args[args.index("--pool-namespace") + 1] == "default"

    def test_deployment_serviceaccount_and_grace_period(self):
        dep = self._by_kind(self._docs(), "Deployment")
        spec = dep["spec"]["template"]["spec"]
        assert spec["serviceAccountName"] == "rel-epp"
        assert spec["terminationGracePeriodSeconds"] == 130

    def test_service_targets_epp_grpc_port(self):
        svc = self._by_kind(self._docs(), "Service")
        assert svc["spec"]["selector"] == {"app": "rel-epp"}
        port = svc["spec"]["ports"][0]
        assert port["port"] == 9002
        assert port["targetPort"] == 9002
        assert port["appProtocol"] == "http2"

    def test_cluster_scoped_names_are_release_prefixed(self):
        docs = self._docs(release="myrel")
        cr = self._by_kind(docs, "ClusterRole")
        crb = self._by_kind(docs, "ClusterRoleBinding")
        assert cr["metadata"]["name"] == "myrel-epp-auth"
        assert crb["metadata"]["name"] == "myrel-epp-auth"
        # Cluster-scoped resources carry no namespace.
        assert "namespace" not in cr["metadata"]
        assert "namespace" not in crb["metadata"]

    def test_every_resource_managed_by_bnk_forge(self):
        for d in self._docs():
            assert d["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "bnk-forge"


class TestGatewayClusterIPAddressing:
    """Gateways must bind a ClusterIP EnvoyProxy so an address materializes on
    bare-metal clusters with no LoadBalancer provider (otherwise the Gateway stays
    Programmed=False / AddressNotAssigned)."""

    def test_gaie_gateway_binds_nodeport_envoyproxy(self):
        docs = _parse_docs(_build_gaie_gateway_manifest("rel", "perf-proxies", "default"))
        ep = next(d for d in docs if d["kind"] == "EnvoyProxy")
        assert ep["metadata"]["name"] == "rel"
        envoy_svc = ep["spec"]["provider"]["kubernetes"]["envoyService"]
        assert envoy_svc["type"] == "NodePort"
        assert envoy_svc["patch"]["value"]["spec"]["ports"] == [{"port": 10080, "nodePort": 30893}]
        gw = next(d for d in docs if d["kind"] == "Gateway")
        ref = gw["spec"]["infrastructure"]["parametersRef"]
        assert ref == {"group": "gateway.envoyproxy.io", "kind": "EnvoyProxy", "name": "rel"}


class TestEnvoyGatewayBaseValues:
    def test_extension_manager_targets_ai_gateway_controller(self):
        from services.proxy_deploy_service import AI_GATEWAY_CONTROLLER_DEPLOYMENT
        vals = _envoy_gateway_base_values()
        fqdn = vals["config"]["envoyGateway"]["extensionManager"]["service"]["fqdn"]
        assert fqdn["hostname"] == (
            f"{AI_GATEWAY_CONTROLLER_DEPLOYMENT}.{AI_GATEWAY_NAMESPACE}.svc.cluster.local"
        )
        assert fqdn["port"] == 1063

    def test_enables_backend_api_for_ai_backends(self):
        vals = _envoy_gateway_base_values()
        ext = vals["config"]["envoyGateway"]["extensionApis"]
        assert ext["enableBackend"] is True

    def test_includes_inferencepool_backend_resource(self):
        vals = _envoy_gateway_base_values()
        resources = vals["config"]["envoyGateway"]["extensionManager"]["backendResources"]
        assert {
            "group": "inference.networking.k8s.io",
            "kind": "InferencePool",
            "version": "v1",
        } in resources


class TestGaieGatewayManifest:
    @staticmethod
    def _kind(docs, kind):
        return next(d for d in docs if d["kind"] == kind)

    def test_emits_gatewayclass_envoyproxy_gateway_httproute(self):
        docs = _parse_docs(_build_gaie_gateway_manifest("rel", "perf-proxies", "default"))
        assert [d["kind"] for d in docs] == [
            "GatewayClass", "EnvoyProxy", "Gateway", "HTTPRoute",
            "ClientTrafficPolicy", "BackendTrafficPolicy",
        ]

    def test_traffic_policies_lift_long_stream_timeouts(self):
        docs = _parse_docs(_build_gaie_gateway_manifest("rel", "perf-proxies", "ns-x"))
        ctp = self._kind(docs, "ClientTrafficPolicy")
        assert ctp["spec"]["targetRefs"][0] == {"group": "gateway.networking.k8s.io", "kind": "Gateway", "name": "rel"}
        assert ctp["spec"]["timeout"]["http"]["streamIdleTimeout"] == "30m"
        btp = self._kind(docs, "BackendTrafficPolicy")
        assert btp["spec"]["targetRefs"][0]["kind"] == "HTTPRoute"
        assert btp["spec"]["timeout"]["http"]["requestTimeout"] == "0s"
        assert btp["spec"]["timeout"]["http"]["maxStreamDuration"] == "0s"

    def test_gatewayclass_is_the_shared_eg_class(self):
        docs = _parse_docs(_build_gaie_gateway_manifest("rel", "perf-proxies", "default"))
        gc = self._kind(docs, "GatewayClass")
        assert gc["metadata"]["name"] == ENVOY_GATEWAY_CLASS_NAME
        assert gc["spec"]["controllerName"] == ENVOY_GATEWAY_CONTROLLER

    def test_gateway_listens_on_proxy_port_in_gateway_namespace(self):
        docs = _parse_docs(_build_gaie_gateway_manifest("rel", "perf-proxies", "default"))
        gw = self._kind(docs, "Gateway")
        assert gw["metadata"]["namespace"] == "perf-proxies"
        assert gw["spec"]["listeners"][0]["port"] == PROXY_LISTEN_PORT

    def test_httproute_backends_the_inferencepool(self):
        docs = _parse_docs(_build_gaie_gateway_manifest("rel", "perf-proxies", "ns-x"))
        route = self._kind(docs, "HTTPRoute")
        assert route["metadata"]["namespace"] == "ns-x"
        backend = route["spec"]["rules"][0]["backendRefs"][0]
        assert backend["group"] == "inference.networking.k8s.io"
        assert backend["kind"] == "InferencePool"
        assert backend["name"] == "rel"

    def test_httproute_parent_is_the_gateway(self):
        docs = _parse_docs(_build_gaie_gateway_manifest("rel", "perf-proxies", "ns-x"))
        parent = self._kind(docs, "HTTPRoute")["spec"]["parentRefs"][0]
        assert parent["name"] == "rel"
        assert parent["namespace"] == "perf-proxies"


class TestContextArgs:
    def test_emits_context_flag_when_set(self):
        assert _context_args("my-ctx") == ["--context", "my-ctx"]

    def test_omits_context_flag_when_none(self):
        assert _context_args(None) == []

    def test_omits_context_flag_when_empty_string(self):
        # Empty string is falsy — behave like None to keep argv identical to today.
        assert _context_args("") == []

    def test_leading_dash_context_rejected(self):
        # M6: a context that could be parsed as a flag must be rejected.
        import pytest
        with pytest.raises(ValueError, match="context"):
            _context_args("--kubeconfig=/evil")


class TestReleaseExistsErrorClassification:
    """H3: _release_exists must only treat a TYPED not-found as 'absent'.

    A transient backend error must propagate, never be swallowed into False —
    otherwise the singleton control-plane installer re-runs over a shared release.
    """

    def test_not_found_returns_false(self):
        svc = _service()
        svc.helm = MagicMock()
        svc.helm.get_release.side_effect = ReleaseNotFoundError("eg")
        assert svc._release_exists(1, "eg", "envoy-gateway-system", None) is False

    def test_present_returns_true(self):
        svc = _service()
        svc.helm = MagicMock()
        svc.helm.get_release.return_value = {"name": "eg"}
        assert svc._release_exists(1, "eg", "envoy-gateway-system", None) is True

    def test_transient_error_propagates(self):
        import pytest
        svc = _service()
        svc.helm = MagicMock()
        svc.helm.get_release.side_effect = RuntimeError("cluster unreachable")
        with pytest.raises(RuntimeError, match="unreachable"):
            svc._release_exists(1, "eg", "envoy-gateway-system", None)


class TestControllerVersionIndependentOfCrds:
    """M9: the controller version must not silently reuse the CRDs version."""

    def test_controller_version_is_its_own_constant(self):
        from services.proxy_deploy_service import (
            AI_GATEWAY_CONTROLLER_VERSION,
            AI_GATEWAY_CRDS_VERSION,
        )
        # They may coincide by default, but the controller pin is a distinct,
        # env-overridable constant — not a fallback to the CRDs version.
        assert AI_GATEWAY_CONTROLLER_VERSION == "v0.6.0"
        # Independence proof: overriding the controller env var moves only it.
        import importlib

        import services.proxy_deploy_service as mod
        with _env("AI_GATEWAY_CONTROLLER_VERSION", "v9.9.9"):
            importlib.reload(mod)
            try:
                assert mod.AI_GATEWAY_CONTROLLER_VERSION == "v9.9.9"
                assert mod.AI_GATEWAY_CRDS_VERSION == AI_GATEWAY_CRDS_VERSION
            finally:
                importlib.reload(mod)

    def test_controller_install_uses_controller_version(self, monkeypatch):
        _patch_dataplane(monkeypatch)
        svc = _service()
        svc.helm = MagicMock()
        svc.helm.get_release.side_effect = ReleaseNotFoundError("absent")
        deploy = MagicMock()
        deploy.helm_chart = None
        deploy.helm_version = None
        deploy.helm_values = None
        deploy.proxy_type = "envoy-ai-gateway"
        target = _target()
        target.cluster_id = 1
        svc._deploy_envoy_ai_gateway(
            deploy, target, MagicMock(), "rel", AI_GATEWAY_NAMESPACE, None, MagicMock(),
        )
        ctrl_call = next(
            c for c in svc.helm.install_chart.call_args_list
            if c.kwargs["release_name"] == AI_GATEWAY_CONTROLLER_RELEASE
        )
        from services.proxy_deploy_service import AI_GATEWAY_CONTROLLER_VERSION
        assert ctrl_call.kwargs["version"] == AI_GATEWAY_CONTROLLER_VERSION


class TestGieCrdSourceConfigurable:
    """M8: the GIE CRD manifest source is env-overridable (URL or local path)."""

    def test_default_is_pinned_github_release_url(self):
        import importlib

        import services.proxy_deploy_service as mod
        importlib.reload(mod)
        assert mod.GIE_CRD_MANIFEST_SOURCE.startswith("https://github.com/")
        assert mod.GIE_CRD_VERSION in mod.GIE_CRD_MANIFEST_SOURCE

    def test_env_override_changes_source(self):
        import importlib

        import services.proxy_deploy_service as mod
        with _env("GIE_CRD_MANIFEST_SOURCE", "/opt/vendored/gie-manifests.yaml"):
            importlib.reload(mod)
            try:
                assert mod.GIE_CRD_MANIFEST_SOURCE == "/opt/vendored/gie-manifests.yaml"
                assert mod.GIE_CRD_MANIFEST_URL == "/opt/vendored/gie-manifests.yaml"
            finally:
                importlib.reload(mod)


class TestMultiStepPersistsReleaseBeforeInstall:
    """H4: helm_release must be persisted BEFORE install steps so undeploy can
    always reach leaked per-target + cluster-scoped resources on a mid-deploy
    failure."""

    def test_helm_release_persisted_before_install_on_failure(self, monkeypatch):
        svc = _service()
        # Make the actual deploy step fail AFTER the release name is computed.
        monkeypatch.setattr(
            svc, "_deploy_envoy_ai_gateway",
            MagicMock(side_effect=RuntimeError("gateway address timeout")),
        )

        writes: list[dict] = []
        monkeypatch.setattr(svc, "_write", lambda deploy, lock, **f: writes.append(f))

        deploy = MagicMock()
        deploy.helm_release = None
        deploy.proxy_type = "envoy-ai-gateway"
        deploy.helm_values = None
        target = _target()
        target.name = "vllm-qwen"
        cluster = MagicMock()
        cluster.context = "prod"

        import pytest
        with pytest.raises(RuntimeError, match="timeout"):
            svc._deploy_multi_step(deploy, target, cluster, None, MagicMock())

        # The FIRST write must carry helm_release, and it must precede the
        # FAILED status write — proving persistence happened before install.
        assert writes[0].get("helm_release"), "release not persisted before install"
        statuses = [w.get("status") for w in writes if "status" in w]
        assert statuses, "expected a failure status write after the release write"

    def test_release_persisted_before_dataplane_apply(self, monkeypatch):
        """End-to-end-ish: the release write lands before any kubectl apply."""
        order: list[str] = []
        _patch_dataplane(monkeypatch)
        # Re-wrap kubectl_apply to record ordering.
        monkeypatch.setattr(pds, "_kubectl_apply", lambda *a, **k: order.append("apply"))
        monkeypatch.setattr(pds, "_kubectl_apply_url", lambda *a, **k: order.append("apply_url"))

        svc = _service()
        svc.helm = MagicMock()
        svc.helm.get_release.side_effect = ReleaseNotFoundError("absent")

        def _record_write(deploy, lock, **f):
            if "helm_release" in f and "status" not in f:
                order.append("persist_release")

        monkeypatch.setattr(svc, "_write", _record_write)

        deploy = MagicMock()
        deploy.helm_release = None
        deploy.proxy_type = "envoy-ai-gateway"
        deploy.helm_chart = None
        deploy.helm_version = None
        deploy.helm_values = None
        target = _target()
        target.name = "vllm-qwen"
        target.cluster_id = 1
        cluster = MagicMock()
        cluster.context = None

        svc._deploy_multi_step(deploy, target, cluster, None, MagicMock())
        assert "persist_release" in order
        # The release write must precede any data-plane kubectl apply. (The GIE-CRD
        # apply_url is now gated/skipped when the CRD is present, so assert against the
        # EPP/Gateway manifest apply, which still happens.)
        assert order.index("persist_release") < order.index("apply")


class TestUndeployMultiStepResourceSet:
    """The per-target teardown must delete the full leaked set with --context,
    while leaving the control-plane singletons (eg / aieg-crd / aieg) alone."""

    def test_deletes_dataplane_and_cluster_scoped_with_context(self, monkeypatch):
        deletes: list[tuple] = []
        cluster_deletes: list[str] = []

        @contextmanager
        def _fake_kubeconfig(cluster, db):
            yield "/tmp/kc"

        monkeypatch.setattr(pds, "kubeconfig_for_cluster", _fake_kubeconfig)
        monkeypatch.setattr(
            pds, "_kubectl_delete",
            lambda kc, resource, ns, ignore_missing=False, context=None: deletes.append(
                (resource, ns, context)
            ),
        )
        monkeypatch.setattr(
            pds, "_kubectl_delete_cluster_scoped",
            lambda kc, resource, ignore_missing=False, context=None: cluster_deletes.append(
                (resource, context)
            ),
        )

        svc = _service()
        monkeypatch.setattr(svc, "_write", lambda *a, **k: None)

        deploy = MagicMock()
        deploy.helm_release = "rel"
        deploy.proxy_type = "envoy-ai-gateway"
        target = _target()
        target.llm_namespace = "default"
        target.proxy_namespace = "perf-proxies"
        cluster = MagicMock()
        cluster.context = "prod-ctx"

        svc._undeploy_multi_step(deploy, target, cluster, None, MagicMock())

        deleted_resources = {r for (r, _ns, _ctx) in deletes}
        # Per-target data plane + RBAC must all be targeted.
        assert "deployment/rel-epp" in deleted_resources
        assert "inferencepool.inference.networking.k8s.io/rel" in deleted_resources
        assert "gateway/rel" in deleted_resources
        assert "role/rel-epp-pod-read" in deleted_resources
        # Every namespaced delete carries the context.
        assert all(ctx == "prod-ctx" for (_r, _ns, ctx) in deletes)
        # Cluster-scoped *-epp-auth ClusterRole/Binding deleted with context.
        assert {r for (r, _ctx) in cluster_deletes} == {
            "clusterrole/rel-epp-auth", "clusterrolebinding/rel-epp-auth",
        }
        assert all(ctx == "prod-ctx" for (_r, ctx) in cluster_deletes)
        # Control-plane singletons are NEVER deleted here.
        assert "eg" not in deleted_resources
        assert "aieg-crd" not in deleted_resources
        assert "aieg" not in deleted_resources


# ---------------------------------------------------------------------------
# Backend-DNS fix: nginx + haproxy values use _backend_svc_name/_backend_svc_port
# ---------------------------------------------------------------------------

def _target_with_tags(
    llm_url: str = "http://vllm.default:8000",
    namespace: str = "default",
    tags: dict | None = None,
) -> MagicMock:
    t = MagicMock()
    t.llm_base_url = llm_url
    t.llm_namespace = namespace
    t.tags = tags
    t.name = "test-target"
    return t


class TestNginxBackendOverride:
    """_values_nginx must use tags["upstream_service"] / tags["upstream_port"] overrides."""

    def test_uses_upstream_service_tag(self):
        svc = _service()
        t = _target_with_tags(
            llm_url="http://10.0.10.108:8000",
            namespace="awsbnkctl-scn-aiinference",
            tags={"upstream_service": "vllm", "upstream_port": 80},
        )
        vals = svc._values_nginx(MagicMock(), t)
        upstream = vals["tcp"][str(PROXY_LISTEN_PORT)]
        assert upstream == "awsbnkctl-scn-aiinference/vllm:80"

    def test_ip_literal_without_override_raises(self):
        svc = _service()
        t = _target_with_tags(llm_url="http://10.0.10.108:8000", tags={})
        with pytest.raises(BadRequestError, match="upstream_service"):
            svc._values_nginx(MagicMock(), t)

    def test_dns_host_uses_url_parse_when_no_override(self):
        svc = _service()
        t = _target_with_tags(llm_url="http://vllm.default:9000", namespace="default", tags={})
        vals = svc._values_nginx(MagicMock(), t)
        upstream = vals["tcp"][str(PROXY_LISTEN_PORT)]
        assert upstream == "default/vllm:9000"
        # Fixed NodePort on the TCP proxy port: the cluster firewall opens exactly this.
        assert vals["controller"]["service"]["nodePorts"]["tcp"] == {str(PROXY_LISTEN_PORT): 30891}

    def test_upstream_namespace_tag_overrides_llm_namespace(self):
        """Bug fix: nginx must route to tags["upstream_namespace"], not the
        target's llm_namespace placeholder (was hardcoded, producing a dead
        upstream when llm_namespace is forge's "default" but the real Service
        lives in e.g. awsbnkctl-scn-aiinference)."""
        svc = _service()
        t = _target_with_tags(
            llm_url="http://10.0.10.108:8000",
            namespace="default",
            tags={
                "upstream_service": "vllm",
                "upstream_port": 80,
                "upstream_namespace": "awsbnkctl-scn-aiinference",
            },
        )
        vals = svc._values_nginx(MagicMock(), t)
        upstream = vals["tcp"][str(PROXY_LISTEN_PORT)]
        assert upstream == "awsbnkctl-scn-aiinference/vllm:80"

    def test_no_upstream_namespace_tag_falls_back_to_llm_namespace(self):
        svc = _service()
        t = _target_with_tags(
            llm_url="http://vllm.default:9000",
            namespace="default",
            tags={},
        )
        vals = svc._values_nginx(MagicMock(), t)
        upstream = vals["tcp"][str(PROXY_LISTEN_PORT)]
        assert upstream == "default/vllm:9000"


class TestHaproxyBackendOverride:
    """_values_haproxy must use tags["upstream_service"] / tags["upstream_port"] overrides."""

    def test_uses_upstream_service_tag(self):
        svc = _service()
        t = _target_with_tags(
            llm_url="http://10.0.10.108:8000",
            namespace="awsbnkctl-scn-aiinference",
            tags={"upstream_service": "vllm", "upstream_port": 80},
        )
        vals = svc._values_haproxy(MagicMock(), t)
        assert vals["service"]["ports"]["http"] == PROXY_LISTEN_PORT
        # Fixed NodePort: the cluster firewall (awsbnkctl) opens exactly this port.
        assert vals["service"]["nodePorts"]["http"] == 30890
        assert vals["containerPorts"]["http"] == PROXY_LISTEN_PORT
        config = vals["config"]
        assert "vllm.awsbnkctl-scn-aiinference.svc.cluster.local:80" in config

    def test_ip_literal_without_override_raises(self):
        svc = _service()
        t = _target_with_tags(llm_url="http://10.0.10.108:8000", tags={})
        with pytest.raises(BadRequestError, match="upstream_service"):
            svc._values_haproxy(MagicMock(), t)

    def test_dns_host_uses_url_parse_when_no_override(self):
        svc = _service()
        t = _target_with_tags(llm_url="http://vllm.default:9000", namespace="default", tags={})
        vals = svc._values_haproxy(MagicMock(), t)
        config = vals["config"]
        assert "vllm.default.svc.cluster.local:9000" in config

    def test_upstream_namespace_tag_overrides_llm_namespace(self):
        """Bug fix: haproxy must route to tags["upstream_namespace"], not the
        target's llm_namespace placeholder (was hardcoded, producing a dead
        upstream)."""
        svc = _service()
        t = _target_with_tags(
            llm_url="http://10.0.10.108:8000",
            namespace="default",
            tags={
                "upstream_service": "vllm",
                "upstream_port": 80,
                "upstream_namespace": "awsbnkctl-scn-aiinference",
            },
        )
        vals = svc._values_haproxy(MagicMock(), t)
        config = vals["config"]
        assert "vllm.awsbnkctl-scn-aiinference.svc.cluster.local:80" in config

    def test_no_upstream_namespace_tag_falls_back_to_llm_namespace(self):
        svc = _service()
        t = _target_with_tags(
            llm_url="http://vllm.default:9000",
            namespace="default",
            tags={},
        )
        vals = svc._values_haproxy(MagicMock(), t)
        config = vals["config"]
        assert "vllm.default.svc.cluster.local:9000" in config


# ===========================================================================
# llm-d-router — precise (KV-events) prefix-cache-aware inference scheduler
# ===========================================================================


class TestKvEventsPortParsing:
    """Discovering the ZMQ port the target's vLLM publishes KV-cache events on."""

    def test_port_from_kv_events_config_wildcard_endpoint(self):
        raw = '{"enable_kv_cache_events": true, "publisher": "zmq", "endpoint": "tcp://*:5557"}'
        assert _port_from_kv_events_config(raw) == 5557

    def test_port_from_kv_events_config_host_endpoint(self):
        raw = '{"endpoint": "tcp://gaie-kv-events-epp.ns.svc.cluster.local:6001"}'
        assert _port_from_kv_events_config(raw) == 6001

    def test_port_from_kv_events_config_garbage_returns_none(self):
        assert _port_from_kv_events_config("not-json") is None
        assert _port_from_kv_events_config('{"endpoint": "tcp://host"}') is None

    def test_parse_kv_events_port_prefers_named_container_port(self):
        container = {
            "name": "vllm",
            "ports": [
                {"name": "vllm", "containerPort": 8000},
                {"name": "kv-events", "containerPort": 5557},
            ],
        }
        assert _parse_kv_events_port(container) == 5557

    def test_parse_kv_events_port_from_args(self):
        container = {
            "name": "vllm",
            "args": [
                "--block-size=64",
                "--kv-events-config",
                '{"endpoint": "tcp://*:5599"}',
            ],
        }
        assert _parse_kv_events_port(container) == 5599

    def test_parse_kv_events_port_from_equals_arg(self):
        container = {"args": ['--kv-events-config={"endpoint": "tcp://*:5560"}']}
        assert _parse_kv_events_port(container) == 5560

    def test_parse_kv_events_port_absent_returns_none(self):
        assert _parse_kv_events_port({"name": "vllm", "ports": [{"name": "vllm", "containerPort": 8000}]}) is None

    def test_kv_events_port_from_pods_scans_items(self):
        pods = {
            "items": [
                {"spec": {"containers": [
                    {"name": "sidecar", "ports": [{"name": "http", "containerPort": 80}]},
                    {"name": "vllm", "ports": [{"name": "kv-events", "containerPort": 7001}]},
                ]}},
            ],
        }
        assert _kv_events_port_from_pods(pods) == 7001

    def test_kv_events_port_from_pods_none_when_empty(self):
        assert _kv_events_port_from_pods(None) is None
        assert _kv_events_port_from_pods({"items": []}) is None


class TestPrecisePrefixCacheConfig:
    """The llm-d v0.11 EndpointPickerConfig: token-producer -> precise producer -> scorer."""

    def _plugin(self, cfg, ptype):
        return next(p for p in cfg["plugins"] if p["type"] == ptype)

    def test_vllm_per_pod_kv_events_and_render_tokens(self):
        cfg = yaml.safe_load(_precise_prefix_cache_config("llama3", "http://vllm.ns:80", 6123))
        assert cfg["apiVersion"] == "llm-d.ai/v1alpha1"
        tok = self._plugin(cfg, "token-producer")["parameters"]
        assert tok == {"modelName": "llama3", "vllm": {"url": "http://vllm.ns:80"}}
        kv = self._plugin(cfg, "precise-prefix-cache-producer")["parameters"]["kvEventsConfig"]
        assert kv["discoverPods"] is True and kv["podDiscoveryConfig"]["socketPort"] == 6123
        # Per-pod subscriptions are driven by endpoint notifications.
        refs = [s["pluginRef"] for s in cfg["dataLayer"]["sources"]]
        assert "endpoint-notification-source" in refs

    def test_estimate_tokens_without_render_url(self):
        cfg = yaml.safe_load(_precise_prefix_cache_config("llama3", None, 20080))
        assert "parameters" not in self._plugin(cfg, "token-producer")


class TestDeployLlmDRouter:
    """The multi-step install: agentgateway control plane + GIE CRDs + router chart + Gateway."""

    def _svc(self):
        svc = _service()
        svc.helm = MagicMock()
        # Control-plane releases absent → they install; lookups raise typed not-found.
        svc.helm.get_release.side_effect = ReleaseNotFoundError("absent")
        return svc

    def _deploy(self):
        d = MagicMock()
        d.helm_chart = None
        d.helm_version = None
        d.helm_values = None
        d.proxy_type = "llm-d-router"
        return d

    def _run(self, monkeypatch, routing, render=True):
        _patch_dataplane(monkeypatch)
        applied = []
        monkeypatch.setattr(pds, "_kubectl_apply", lambda kc, manifest, **k: applied.append(manifest))
        monkeypatch.setattr(pds, "_render_endpoint_available", lambda *a, **k: render)
        svc = self._svc()
        monkeypatch.setattr(svc, "_discover_target_routing", lambda *a, **k: routing)
        target = _target()
        target.cluster_id = 1
        url, values, info = svc._deploy_llm_d_router(
            self._deploy(), target, MagicMock(), "perf-llm-d-router-t", "perf-proxies", None, MagicMock(),
        )
        return svc, url, values, info, applied

    def _cfg(self, values):
        return yaml.safe_load(values["router"]["epp"]["pluginsCustomConfig"]["precise-prefix-cache-config.yaml"])

    def test_vllm_target_installs_router_chart_with_discovered_routing(self, monkeypatch):
        routing = TargetRouting({"app": "vllm-qwen"}, kv_port=6543, pod_port=8000, served_model="qwen3")
        svc, url, values, info, _ = self._run(monkeypatch, routing)
        assert url == f"http://10.0.0.5:{PROXY_LISTEN_PORT}"
        call = next(c for c in svc.helm.install_chart.call_args_list if c.kwargs["release_name"] == "perf-llm-d-router-t")
        assert call.kwargs["chart"] == LLM_D_ROUTER_CHART
        assert call.kwargs["version"] == LLM_D_ROUTER_VERSION
        assert call.kwargs["namespace"] == "default"  # target.llm_namespace
        servers = values["router"]["modelServers"]
        assert servers["matchLabels"] == {"app": "vllm-qwen"} and servers["targetPorts"] == [{"number": 8000}]
        assert values["provider"] == {"name": "none"}
        kv = next(p for p in self._cfg(values)["plugins"] if p["type"] == "precise-prefix-cache-producer")
        assert kv["parameters"]["kvEventsConfig"]["podDiscoveryConfig"]["socketPort"] == 6543
        assert info["model_server"] == "vLLM" and info["kv_events"].startswith("per pod")
        # Agentgateway control plane only — none of the Envoy AI Gateway one.
        installed = [c.kwargs["release_name"] for c in svc.helm.install_chart.call_args_list]
        assert AGENTGATEWAY_CRDS_RELEASE in installed and AGENTGATEWAY_RELEASE in installed
        assert ENVOY_GATEWAY_RELEASE not in installed and AI_GATEWAY_CONTROLLER_RELEASE not in installed

    def test_no_render_endpoint_falls_back_to_estimate(self, monkeypatch):
        _, _, values, info, _ = self._run(monkeypatch, TargetRouting({"app": "x"}), render=False)
        tok = next(p for p in self._cfg(values)["plugins"] if p["type"] == "token-producer")
        assert "parameters" not in tok
        assert info["tokens"].startswith("estimate")

    def test_simulator_with_kv_relay_is_connected_per_pod(self, monkeypatch):
        routing = TargetRouting({"app": "vllm"}, kv_port=20080, simulator=True)
        _, _, values, info, applied = self._run(monkeypatch, routing)
        kv = next(p for p in self._cfg(values)["plugins"] if p["type"] == "precise-prefix-cache-producer")
        assert kv["parameters"]["kvEventsConfig"]["podDiscoveryConfig"]["socketPort"] == 20080
        assert "extraContainerPorts" not in values["router"]["epp"]
        assert all(d["kind"] != "Service" for m in applied for d in _parse_docs(m))
        assert info["kv_events"] == "per pod, EPP connects to :20080"
        assert info["model_server"] == "llm-d inference simulator"


class TestSimulatorDetection:
    SIM = "ghcr.io/llm-d/llm-d-inference-sim:v0.11.2"

    def _pods(self, *containers):
        return {"items": [{"spec": {"containers": list(containers)}}]}

    def test_simulator_with_relay_sidecar(self):
        pods = self._pods(
            {"name": "vllm", "image": self.SIM, "args": ["--zmq-endpoint=tcp://127.0.0.1:5557"],
             "ports": [{"containerPort": 8000}, {"name": "kv-replay", "containerPort": 20081}]},
            {"name": "kv-relay", "image": "python:3.12-slim", "ports": [{"name": "kv-events", "containerPort": 20080}]},
        )
        assert _is_llm_d_simulator(pods)
        assert _kv_events_port_from_pods(pods) == 20080

    def test_vllm_is_not_the_simulator(self):
        assert not _is_llm_d_simulator(self._pods({"image": "vllm/vllm-openai:v0.11.0", "args": ["--model", "m"]}))

    def test_f5_epp_kv_events(self):
        assert _f5_epp_kv_events(20080).startswith("per pod")
        assert _f5_epp_kv_events(5557).startswith("none") and _f5_epp_kv_events(None).startswith("none")


class TestGieCrdsPresent:
    """The BNK-critical apply-if-absent gate: we must NOT re-apply the cluster-scoped
    GIE InferencePool CRD when it already exists (F5 BNK is pinned to it)."""

    def test_present_when_get_succeeds(self, monkeypatch):
        monkeypatch.setattr(
            pds.subprocess, "run",
            lambda cmd, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        )
        assert pds._gie_crds_present("/kc") is True

    def test_absent_when_get_fails(self, monkeypatch):
        monkeypatch.setattr(
            pds.subprocess, "run",
            lambda cmd, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "NotFound"})(),
        )
        assert pds._gie_crds_present("/kc") is False

    def test_queries_the_inferencepool_crd(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            pds.subprocess, "run",
            lambda cmd, **kw: seen.update(cmd=cmd) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        )
        pds._gie_crds_present("/kc")
        assert "inferencepools.inference.networking.k8s.io" in seen["cmd"]
        assert "get" in seen["cmd"] and "crd" in seen["cmd"]


class TestHfModelDiscovery:
    """Discovering the real HF repo id from the target vLLM pod's --model arg."""

    def _pods(self, containers):
        return {"items": [{"spec": {"containers": containers}}]}

    def test_discrete_token_args(self):
        pods = self._pods([{"name": "vllm", "args": ["--model", "Qwen/Qwen3-32B", "--port", "8000"]}])
        assert _hf_model_from_pods(pods) == "Qwen/Qwen3-32B"

    def test_single_shell_string_arg(self):
        pods = self._pods([{
            "name": "vllm",
            "args": ["python3 -m dynamo.vllm --model Qwen/Qwen3-32B --served-model-name qwen3-32b --tensor-parallel-size 1"],
        }])
        assert _hf_model_from_pods(pods) == "Qwen/Qwen3-32B"

    def test_does_not_match_served_model_name(self):
        # Only --served-model-name present (no --model) → no false positive.
        pods = self._pods([{"name": "vllm", "args": ["vllm serve --served-model-name qwen3-32b"]}])
        assert _hf_model_from_pods(pods) is None

    def test_equals_form(self):
        pods = self._pods([{"name": "vllm", "command": ["sh", "-c", "vllm --model=meta-llama/Llama-3.1-70B"]}])
        assert _hf_model_from_pods(pods) == "meta-llama/Llama-3.1-70B"

    def test_none_when_absent(self):
        assert _hf_model_from_pods(self._pods([{"name": "x", "args": ["--foo", "bar"]}])) is None
        assert _hf_model_from_pods(None) is None

class TestAgentgatewayManifest:
    """The agentgateway Gateway data plane for llm-d-router (replaces the Envoy one)."""

    def _docs(self, release="rel", gw_ns="perf-proxies", pool_ns="default"):
        return _parse_docs(_build_agentgateway_manifest(release, gw_ns, pool_ns))

    def _kind(self, docs, kind):
        return next(d for d in docs if d["kind"] == kind)

    def test_emits_params_gateway_httproute_only(self):
        # No GatewayClass (chart owns it), no EnvoyProxy, no Envoy traffic policies.
        kinds = [d["kind"] for d in self._docs()]
        assert kinds == [AGENTGATEWAY_PARAMS_KIND, "Gateway", "HTTPRoute"]

    def test_params_pin_nodeport_service(self):
        params = self._kind(self._docs(), AGENTGATEWAY_PARAMS_KIND)
        # Fixed NodePort (not LoadBalancer) so the Gateway gets an address on LB-less
        # clusters and the external load generator reaches one opened port.
        assert params["spec"]["service"]["spec"] == {
            "type": "NodePort", "externalTrafficPolicy": "Cluster",
            "ports": [{"port": PROXY_LISTEN_PORT, "nodePort": 30894}],
        }
        assert params["metadata"]["namespace"] == "perf-proxies"

    def test_gateway_uses_agentgateway_class_and_params_ref(self):
        gw = self._kind(self._docs(), "Gateway")
        assert gw["spec"]["gatewayClassName"] == AGENTGATEWAY_CLASS_NAME
        ref = gw["spec"]["infrastructure"]["parametersRef"]
        assert ref == {"group": "agentgateway.dev", "kind": AGENTGATEWAY_PARAMS_KIND, "name": "rel"}
        assert gw["spec"]["listeners"][0]["port"] == PROXY_LISTEN_PORT

    def test_httproute_backends_inferencepool_with_disabled_timeout(self):
        route = self._kind(self._docs(pool_ns="ns-x"), "HTTPRoute")
        assert route["metadata"]["namespace"] == "ns-x"
        backend = route["spec"]["rules"][0]["backendRefs"][0]
        assert backend["group"] == "inference.networking.k8s.io"
        assert backend["kind"] == "InferencePool"
        assert backend["name"] == "rel"
        # request timeout disabled so long generations aren't cut off.
        assert route["spec"]["rules"][0]["timeouts"]["request"] == "0s"

    def test_no_envoy_specific_kinds(self):
        kinds = {d["kind"] for d in self._docs()}
        assert "EnvoyProxy" not in kinds
        assert "ClientTrafficPolicy" not in kinds
        assert "BackendTrafficPolicy" not in kinds
        assert "GatewayClass" not in kinds

    def test_deploy_uses_agentgateway_manifest(self, monkeypatch):
        # End-to-end: _deploy_llm_d_router applies the agentgateway manifest, not the Envoy one.
        _patch_dataplane(monkeypatch)
        applied = {}
        monkeypatch.setattr(pds, "_kubectl_apply", lambda kc, manifest, **k: applied.update(manifest=manifest))
        svc = _service()
        svc.helm = MagicMock()
        svc.helm.get_release.side_effect = ReleaseNotFoundError("absent")
        monkeypatch.setattr(svc, "_discover_target_routing", lambda *a, **k: TargetRouting({"app": "x"}))
        d = MagicMock()
        d.helm_chart = None
        d.helm_version = None
        d.helm_values = None
        d.proxy_type = "llm-d-router"
        target = _target()
        target.cluster_id = 1
        svc._deploy_llm_d_router(d, target, MagicMock(), "rel", "perf-proxies", None, MagicMock())
        docs = _parse_docs(applied["manifest"])
        assert any(x["kind"] == AGENTGATEWAY_PARAMS_KIND for x in docs)
        assert all(x["kind"] != "EnvoyProxy" for x in docs)


class TestShellWrappedDiscovery:
    """Real-world targets launch vLLM via `/bin/sh -c '<script>'` — discovery must
    parse the positional model and the embedded --kv-events-config port out of the
    single shell-string arg (the dynamo-system vllm-qwen3-32b case)."""

    # Mirrors the actual vllm-qwen3-32b-* pod on the HGX cluster.
    SHELL_ARG = (
        'KV_TOPIC="kv@${POD_IP}@Qwen/Qwen3-32B"\n'
        'exec vllm serve Qwen/Qwen3-32B \\\n'
        '  --served-model-name Qwen/Qwen3-32B \\\n'
        '  --port 8000 \\\n'
        '  --block-size 64 \\\n'
        '  --kv-events-config "{\\"publisher\\":\\"zmq\\",\\"endpoint\\":\\"tcp://*:20080\\",'
        '\\"replay_endpoint\\":\\"tcp://*:20081\\",\\"enable_kv_cache_events\\":true,'
        '\\"topic\\":\\"${KV_TOPIC}\\"}"\n'
    )

    def _container(self):
        return {"name": "vllm", "image": "vllm/vllm-openai:v0.17.1",
                "command": ["/bin/sh", "-c"], "args": [self.SHELL_ARG]}

    def test_model_from_positional_vllm_serve_in_shell(self):
        assert _model_from_container(self._container()) == "Qwen/Qwen3-32B"

    def test_model_ignores_served_model_name_only(self):
        c = {"command": ["/bin/sh", "-c"], "args": ["vllm serve --served-model-name qwen3-32b"]}
        assert _model_from_container(c) is None

    def test_kv_port_from_embedded_config_picks_primary_endpoint(self):
        # 20080 (endpoint), NOT 20081 (replay_endpoint).
        assert _parse_kv_events_port(self._container()) == 20080

    def test_pods_helpers_extract_both(self):
        pods = {"items": [{"spec": {"containers": [self._container()]}}]}
        assert _hf_model_from_pods(pods) == "Qwen/Qwen3-32B"
        assert _kv_events_port_from_pods(pods) == 20080


class TestServedModelFromContainer:
    """--served-model-name is the request model id (can differ from --model)."""

    def test_discrete_token(self):
        c = {"args": ["--served-model-name", "llama70b", "--model", "neuralmagic/Llama"]}
        assert _served_model_from_container(c) == "llama70b"

    def test_shell_wrapped(self):
        c = {"command": ["/bin/sh", "-c"],
             "args": ["exec vllm serve neuralmagic/Llama --served-model-name llama70b --port 8000"]}
        assert _served_model_from_container(c) == "llama70b"

    def test_equals_form(self):
        c = {"args": ["--served-model-name=Qwen/Qwen3-32B"]}
        assert _served_model_from_container(c) == "Qwen/Qwen3-32B"

    def test_none_when_absent(self):
        # Only --model present → None (caller falls back to _model_from_container).
        assert _served_model_from_container({"args": ["--model", "Qwen/Qwen3-32B"]}) is None


class TestGieCrdNonClobber:
    """The shared GIE InferencePool CRD must never be force-overwritten (it's
    cross-tenant with F5 BNK's f5-epp); applied only-if-absent, no --force-conflicts."""

    def test_apply_url_omits_force_conflicts_when_false(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            pds.subprocess, "run",
            lambda cmd, **kw: seen.update(cmd=list(cmd)) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        )
        pds._kubectl_apply_url("/kc", "http://x", force_conflicts=False)
        assert "--force-conflicts" not in seen["cmd"]
        assert "--server-side" in seen["cmd"]

    def test_apply_url_defaults_to_force_conflicts(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            pds.subprocess, "run",
            lambda cmd, **kw: seen.update(cmd=list(cmd)) or type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        )
        pds._kubectl_apply_url("/kc", "http://x")
        assert "--force-conflicts" in seen["cmd"]

    def test_envoy_ai_gateway_skips_gie_apply_when_present(self, monkeypatch):
        from contextlib import contextmanager
        applied = []

        @contextmanager
        def _fake_kc(cluster, db):
            yield "/tmp/fake"

        monkeypatch.setattr(pds, "kubeconfig_for_cluster", _fake_kc)
        monkeypatch.setattr(pds, "_kubectl_apply", lambda *a, **k: None)
        monkeypatch.setattr(pds, "_wait_for_gateway_address", lambda *a, **k: "10.0.0.5")
        monkeypatch.setattr(pds, "_gie_crds_present", lambda *a, **k: True)
        monkeypatch.setattr(pds, "_kubectl_apply_url", lambda *a, **k: applied.append((a, k)))
        svc = _service()
        svc.helm = MagicMock()
        svc.helm.get_release.side_effect = ReleaseNotFoundError("eg")
        target = _target()
        target.cluster_id = 1
        d = MagicMock()
        d.helm_chart = None
        d.helm_version = None
        d.helm_values = None
        d.proxy_type = "envoy-ai-gateway"
        svc._deploy_envoy_ai_gateway(d, target, MagicMock(), "rel", AI_GATEWAY_NAMESPACE, None, MagicMock())
        # GIE CRD present → the shared CRD is never (re)applied.
        assert applied == []


class TestInstallWithRepo:
    """A first-time Forge has no Helm repos: deploys add the chart's repo, then install."""

    def test_adds_missing_repo_then_retries_once_on_stale_index(self):
        svc = _service()
        svc.helm = MagicMock()
        svc.helm.list_repositories.return_value = []
        svc.helm.install_chart.side_effect = [ValueError("Chart not found: no chart version"), None]
        svc._install_with_repo(None, chart="ingress-nginx/ingress-nginx", release_name="r")
        svc.helm.add_repository.assert_called_once_with("ingress-nginx", "https://kubernetes.github.io/ingress-nginx")
        svc.helm.update_repositories.assert_called_once()
        assert svc.helm.install_chart.call_count == 2

    def test_oci_chart_needs_no_repo(self):
        svc = _service()
        svc.helm = MagicMock()
        svc._install_with_repo(None, chart="oci://docker.io/envoyproxy/gateway-helm", release_name="r")
        svc.helm.list_repositories.assert_not_called()
        svc.helm.add_repository.assert_not_called()


class TestNodePortUrls:
    """Gateway proxies on a pinned NodePort: in-cluster URL = Service DNS, external = node IP."""

    def test_urls_from_the_service_holding_the_node_port(self, monkeypatch):
        from types import SimpleNamespace

        def svc(name, ns, port, node_port):
            return SimpleNamespace(
                metadata=SimpleNamespace(name=name, namespace=ns),
                spec=SimpleNamespace(ports=[SimpleNamespace(port=port, node_port=node_port)]),
            )

        core_v1 = MagicMock()
        core_v1.list_service_for_all_namespaces.return_value = SimpleNamespace(
            items=[svc("other", "default", 80, 31000), svc("envoy-perf-abc", "perf-proxies", PROXY_LISTEN_PORT, 30892)]
        )
        monkeypatch.setattr(pds, "KubernetesService", MagicMock())
        monkeypatch.setattr(pds.k8s_client, "CoreV1Api", lambda _api: core_v1)
        monkeypatch.setattr(pds, "_get_node_ip", lambda _core: "10.0.1.181")

        assert _service()._node_port_urls(MagicMock(), 30892) == (
            f"http://envoy-perf-abc.perf-proxies:{PROXY_LISTEN_PORT}",
            "http://10.0.1.181:30892",
        )


class TestPodPortFromService:
    """An InferencePool targets pods: Service 80 -> vLLM 8000 must give 8000."""

    def _svc(self, target_port):
        port = {"port": 80, "protocol": "TCP"}
        if target_port is not None:
            port["targetPort"] = target_port
        return {"spec": {"ports": [port]}}

    def test_numeric_target_port(self):
        assert _pod_port_from_service(self._svc(8000), 80, None) == 8000

    def test_named_target_port_from_pods(self):
        pods = {"items": [{"spec": {"containers": [{"ports": [{"name": "http", "containerPort": 8000}]}]}}]}
        assert _pod_port_from_service(self._svc("http"), 80, pods) == 8000

    def test_omitted_target_port_is_the_service_port(self):
        assert _pod_port_from_service(self._svc(None), 80, None) == 80

    def test_unknown_service_port(self):
        assert _pod_port_from_service(self._svc(8000), 8080, None) is None


class TestF5EppManifest:
    def test_alias_gets_rewrite_and_route_has_own_host(self):
        docs = _parse_docs(pds._build_f5_epp_manifest(
            "rel", "ai", {"app": "vllm"}, 8000, 64, "org/Model-8B", "llama3", "org/Model-8B", "gw", "ai", "rel.forge.local",
        ))
        kinds = {d["kind"]: d for d in docs}
        assert kinds["F5EPP"]["spec"] == {"poolRef": {"name": "rel"}, "engine": "vllm", "blockSize": 64, "tokenizer": {"name": "org/Model-8B"}}
        assert kinds["InferencePool"]["spec"]["endpointPickerRef"]["name"] == "rel-epp"
        rule = kinds["InferenceModelRewrite"]["spec"]["rules"][0]
        assert rule["matches"][0]["model"]["value"] == "llama3" and rule["targets"][0]["modelRewrite"] == "org/Model-8B"
        assert kinds["HTTPRoute"]["spec"]["hostnames"] == ["rel.forge.local"]

    def test_served_models_and_request_settings(self):
        pods = {"items": [{"spec": {"containers": [{"args": ["--model=m", "--served-model-name", "llama3", "org/M", "--port=8000"]}]}}]}
        assert pds._served_models_from_pods(pods) == ["llama3", "org/M"]
        deploy = MagicMock(routing_info={"host_header": "rel.forge.local"})
        assert pds.proxy_request_settings(deploy) == {"header": ["Host:rel.forge.local"], "host_header": "rel.forge.local"}
        assert pds.proxy_request_settings(MagicMock(routing_info=None)) == {}


class TestModelServerTags:
    def test_simulator_profile_from_pod_annotations(self, monkeypatch):
        from types import SimpleNamespace
        pod = SimpleNamespace(
            status=SimpleNamespace(phase="Running"),
            spec=SimpleNamespace(containers=[SimpleNamespace(image="ghcr.io/llm-d/llm-d-inference-sim:v0.11.2")]),
            metadata=SimpleNamespace(annotations={"awsbnkctl.io/sim-profile": "llama-3.3-70b", "awsbnkctl.io/sim-model": "org/M-70B"}),
        )
        core = MagicMock()
        core.read_namespaced_service.return_value = SimpleNamespace(spec=SimpleNamespace(selector={"app": "vllm"}))
        core.list_namespaced_pod.return_value = SimpleNamespace(items=[pod, pod])
        monkeypatch.setattr(pds, "KubernetesService", MagicMock())
        monkeypatch.setattr(pds.k8s_client, "CoreV1Api", lambda _api: core)
        target = _target()
        target.cluster_id = 1
        assert pds.model_server_tags(MagicMock(), target) == {
            "model_server": "llm-d-inference-sim", "model_server_replicas": 2,
            "sim_profile": "llama-3.3-70b", "sim_model": "org/M-70B",
        }
