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
    LLM_D_ROUTER_EPP_IMAGE_NAME,
    LLM_D_ROUTER_VERSION,
    PROXY_LISTEN_PORT,
    ProxyDeployService,
    _build_agentgateway_manifest,
    _build_gaie_gateway_manifest,
    _build_inference_epp_manifest,
    _build_llm_d_router_values,
    _context_args,
    _envoy_gateway_base_values,
    _hf_model_from_pods,
    _kv_events_port_from_pods,
    _model_from_container,
    _parse_kv_events_port,
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
    monkeypatch.setattr(pds, "_ensure_hf_token_secret", lambda *a, **k: True)
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

    def _docs(self, release="rel", ns="default", label="vllm-qwen", port=8000):
        return _parse_docs(_build_inference_epp_manifest(release, ns, label, port))

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

    def test_gaie_gateway_binds_clusterip_envoyproxy(self):
        docs = _parse_docs(_build_gaie_gateway_manifest("rel", "perf-proxies", "default"))
        ep = next(d for d in docs if d["kind"] == "EnvoyProxy")
        assert ep["metadata"]["name"] == "rel"
        assert ep["spec"]["provider"]["kubernetes"]["envoyService"]["type"] == "ClusterIP"
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

    def test_gatewayclass_shared_with_envoy_flow(self):
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
    """The EndpointPickerConfig that turns on precise (KV-event) prefix scoring."""

    def _cfg(self, model="Qwen/Qwen3-32B", port=5557):
        return yaml.safe_load(_precise_prefix_cache_config(model, port))

    def test_uses_precise_scorer_with_pod_discovery_and_socket_port(self):
        cfg = self._cfg(port=6123)
        scorer = next(p for p in cfg["plugins"] if p["type"] == "precise-prefix-cache-scorer")
        kv = scorer["parameters"]["kvEventsConfig"]
        assert kv["discoverPods"] is True
        assert kv["podDiscoveryConfig"]["socketPort"] == 6123

    def test_tokenizer_bound_to_target_model(self):
        cfg = self._cfg(model="meta-llama/Llama-3.1-70B")
        tok = next(p for p in cfg["plugins"] if p["type"] == "tokenizer")
        assert tok["parameters"]["modelName"] == "meta-llama/Llama-3.1-70B"

    def test_default_profile_weights_precise_highest(self):
        cfg = self._cfg()
        plugins = cfg["schedulingProfiles"][0]["plugins"]
        weights = {p["pluginRef"]: p.get("weight") for p in plugins}
        assert weights["precise-prefix-cache-scorer"] == 3.0
        assert "max-score-picker" in weights


class TestLlmDRouterValues:
    """InferencePool chart values for the precise llm-d scheduler."""

    def _vals(self, **kw):
        defaults = dict(
            model_name="Qwen/Qwen3-32B",
            target_port=8000,
            match_labels={"app": "vllm-qwen"},
            kv_events_port=5557,
        )
        defaults.update(kw)
        return _build_llm_d_router_values(**defaults)

    def test_epp_uses_llm_d_inference_scheduler_image(self):
        img = self._vals()["inferenceExtension"]["image"]
        assert img["name"] == LLM_D_ROUTER_EPP_IMAGE_NAME
        assert img["hub"] == "ghcr.io/llm-d"

    def test_tokenizer_sidecar_enabled(self):
        sidecar = self._vals()["inferenceExtension"]["sidecar"]
        assert sidecar["enabled"] is True
        assert sidecar["name"] == "tokenizer-uds"

    def test_hf_token_env_from_secret(self):
        env = self._vals()["inferenceExtension"]["env"]
        hf = next(e for e in env if e["name"] == "HF_TOKEN")
        assert hf["valueFrom"]["secretKeyRef"]["key"] == "HF_TOKEN"

    def test_inferencepool_selector_and_target_port(self):
        pool = self._vals(match_labels={"app": "vllm-qwen"}, target_port=8000)["inferencePool"]
        assert pool["modelServers"]["matchLabels"] == {"app": "vllm-qwen"}
        assert pool["targetPorts"] == [{"number": 8000}]

    def test_precise_config_carries_discovered_port(self):
        vals = self._vals(kv_events_port=6789)
        raw = vals["inferenceExtension"]["pluginsCustomConfig"]["precise-prefix-cache-config.yaml"]
        cfg = yaml.safe_load(raw)
        scorer = next(p for p in cfg["plugins"] if p["type"] == "precise-prefix-cache-scorer")
        assert scorer["parameters"]["kvEventsConfig"]["podDiscoveryConfig"]["socketPort"] == 6789

    def test_prometheus_auth_disabled_for_self_contained_install(self):
        mon = self._vals()["inferenceExtension"]["monitoring"]
        assert mon["prometheus"]["enabled"] is False


class TestDeployLlmDRouter:
    """The multi-step install: agentgateway control plane + GIE CRDs + inferencepool chart + Gateway."""

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

    def test_installs_inferencepool_chart_with_discovered_port(self, monkeypatch):
        _patch_dataplane(monkeypatch)
        svc = self._svc()
        # Stub discovery → known selector + port.
        monkeypatch.setattr(
            svc, "_discover_target_routing",
            lambda *a, **k: ({"app": "vllm-qwen"}, 6543, "Qwen/Qwen3-32B"),
        )
        target = _target()
        target.cluster_id = 1
        url, values = svc._deploy_llm_d_router(
            self._deploy(), target, MagicMock(), "perf-llm-d-router-t", "perf-proxies", None, MagicMock(),
        )
        # Returns the per-target Gateway data-plane URL.
        assert url == f"http://10.0.0.5:{PROXY_LISTEN_PORT}"
        # The inferencepool chart was installed under the per-target release in the
        # model-server namespace.
        ipool_call = next(
            c for c in svc.helm.install_chart.call_args_list
            if c.kwargs["release_name"] == "perf-llm-d-router-t"
        )
        assert ipool_call.kwargs["chart"] == LLM_D_ROUTER_CHART
        assert ipool_call.kwargs["version"] == LLM_D_ROUTER_VERSION
        assert ipool_call.kwargs["namespace"] == "default"  # target.llm_namespace
        # Discovered port flows into the applied values + returned snapshot.
        raw = values["inferenceExtension"]["pluginsCustomConfig"]["precise-prefix-cache-config.yaml"]
        cfg = yaml.safe_load(raw)
        scorer = next(p for p in cfg["plugins"] if p["type"] == "precise-prefix-cache-scorer")
        assert scorer["parameters"]["kvEventsConfig"]["podDiscoveryConfig"]["socketPort"] == 6543

    def test_ensures_agentgateway_control_plane_not_envoy(self, monkeypatch):
        _patch_dataplane(monkeypatch)
        svc = self._svc()
        monkeypatch.setattr(svc, "_discover_target_routing", lambda *a, **k: ({"app": "x"}, None, None))
        target = _target()
        target.cluster_id = 1
        svc._deploy_llm_d_router(
            self._deploy(), target, MagicMock(), "rel", "perf-proxies", None, MagicMock(),
        )
        installed = [c.kwargs["release_name"] for c in svc.helm.install_chart.call_args_list]
        # agentgateway control plane (CRDs + controller) is ensured...
        assert AGENTGATEWAY_CRDS_RELEASE in installed
        assert AGENTGATEWAY_RELEASE in installed
        # ...and NONE of the Envoy AI Gateway control plane is touched (fully decoupled).
        assert ENVOY_GATEWAY_RELEASE not in installed
        assert AI_GATEWAY_CONTROLLER_RELEASE not in installed
        # The controller install enables the GIE inference extension.
        agw_call = next(
            c for c in svc.helm.install_chart.call_args_list
            if c.kwargs["release_name"] == AGENTGATEWAY_RELEASE
        )
        assert agw_call.kwargs["values"] == {"inferenceExtension": {"enabled": True}}

    def test_defaults_port_when_discovery_finds_none(self, monkeypatch):
        _patch_dataplane(monkeypatch)
        svc = self._svc()
        monkeypatch.setattr(svc, "_discover_target_routing", lambda *a, **k: ({"app": "x"}, None, None))
        target = _target()
        target.cluster_id = 1
        _url, values = svc._deploy_llm_d_router(
            self._deploy(), target, MagicMock(), "rel", "perf-proxies", None, MagicMock(),
        )
        raw = values["inferenceExtension"]["pluginsCustomConfig"]["precise-prefix-cache-config.yaml"]
        cfg = yaml.safe_load(raw)
        scorer = next(p for p in cfg["plugins"] if p["type"] == "precise-prefix-cache-scorer")
        port = scorer["parameters"]["kvEventsConfig"]["podDiscoveryConfig"]["socketPort"]
        assert port == LLM_D_ROUTER_DEFAULT_KV_EVENTS_PORT


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


class TestEnsureHfTokenSecret:
    """The deploy always ensures an HF-token secret exists (empty), idempotently."""

    class _R:
        def __init__(self, rc, stderr=""):
            self.returncode = rc
            self.stderr = stderr
            self.stdout = ""

    def test_skips_create_when_secret_present(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return self._R(0)  # `get` succeeds → secret present

        monkeypatch.setattr(pds.subprocess, "run", fake_run)
        created = pds._ensure_hf_token_secret("/kc", "ns", "llm-d-hf-token")
        assert created is False
        assert all("create" not in c for c in calls)  # never attempted create

    def test_creates_empty_secret_when_absent(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return self._R(1) if "get" in cmd else self._R(0)

        monkeypatch.setattr(pds.subprocess, "run", fake_run)
        created = pds._ensure_hf_token_secret("/kc", "ns", "llm-d-hf-token")
        assert created is True
        create_cmd = next(c for c in calls if "create" in c)
        assert "--from-literal=HF_TOKEN=" in create_cmd
        assert "llm-d-hf-token" in create_cmd

    def test_tolerates_already_exists_race(self, monkeypatch):
        def fake_run(cmd, **kw):
            if "get" in cmd:
                return self._R(1)  # not found at check time
            return self._R(1, stderr='secrets "llm-d-hf-token" already exists (AlreadyExists)')

        monkeypatch.setattr(pds.subprocess, "run", fake_run)
        # Concurrent creator won the race — must not raise.
        assert pds._ensure_hf_token_secret("/kc", "ns", "llm-d-hf-token") is True

    def test_raises_on_real_create_failure(self, monkeypatch):
        import pytest

        def fake_run(cmd, **kw):
            if "get" in cmd:
                return self._R(1)
            return self._R(1, stderr="forbidden: cannot create secrets")

        monkeypatch.setattr(pds.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="failed to create HF token secret"):
            pds._ensure_hf_token_secret("/kc", "ns", "llm-d-hf-token")


class TestDeployLlmDRouterEnsuresSecret:
    """_deploy_llm_d_router must ensure the HF-token secret before the chart install."""

    def test_ensure_secret_called_for_pool_namespace(self, monkeypatch):
        _patch_dataplane(monkeypatch)
        seen = {}
        monkeypatch.setattr(
            pds, "_ensure_hf_token_secret",
            lambda kc, ns, name, **k: seen.update(ns=ns, name=name) or True,
        )
        svc = _service()
        svc.helm = MagicMock()
        svc.helm.get_release.side_effect = ReleaseNotFoundError("eg")
        monkeypatch.setattr(svc, "_discover_target_routing", lambda *a, **k: ({"app": "x"}, None, None))
        d = MagicMock()
        d.helm_chart = None
        d.helm_version = None
        d.helm_values = None
        d.proxy_type = "llm-d-router"
        target = _target(namespace="dynamo-system")
        target.cluster_id = 1
        svc._deploy_llm_d_router(d, target, MagicMock(), "rel", "perf-proxies", None, MagicMock())
        # Ensured in the model-server (pool) namespace, with the configured secret name.
        assert seen["ns"] == "dynamo-system"
        from services.proxy_deploy_service import LLM_D_ROUTER_HF_TOKEN_SECRET
        assert seen["name"] == LLM_D_ROUTER_HF_TOKEN_SECRET


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

    def test_deploy_uses_discovered_model_for_tokenizer(self, monkeypatch):
        # End-to-end: discovered --model id flows into the tokenizer config, not llm_model.
        _patch_dataplane(monkeypatch)
        svc = _service()
        svc.helm = MagicMock()
        svc.helm.get_release.side_effect = ReleaseNotFoundError("eg")
        monkeypatch.setattr(
            svc, "_discover_target_routing",
            lambda *a, **k: ({"app": "vllm-qwen"}, 5557, "Qwen/Qwen3-32B"),
        )
        d = MagicMock()
        d.helm_chart = None
        d.helm_version = None
        d.helm_values = None
        d.proxy_type = "llm-d-router"
        target = _target(llm_model="qwen3-32b")  # served-name, NOT a valid HF id
        target.cluster_id = 1
        _url, values = svc._deploy_llm_d_router(
            d, target, MagicMock(), "rel", "perf-proxies", None, MagicMock(),
        )
        raw = values["inferenceExtension"]["pluginsCustomConfig"]["precise-prefix-cache-config.yaml"]
        cfg = yaml.safe_load(raw)
        tok = next(p for p in cfg["plugins"] if p["type"] == "tokenizer")
        # The discovered HF id wins over the target's served-model-name.
        assert tok["parameters"]["modelName"] == "Qwen/Qwen3-32B"


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

    def test_params_force_clusterip_service(self):
        params = self._kind(self._docs(), AGENTGATEWAY_PARAMS_KIND)
        # ClusterIP override so the Gateway gets an address on LB-less clusters.
        assert params["spec"]["service"]["spec"]["type"] == "ClusterIP"
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
        monkeypatch.setattr(svc, "_discover_target_routing", lambda *a, **k: ({"app": "x"}, None, None))
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
