"""
Tests for services.execution.kubernetes_engine — K8s engine helpers.

Covers _get_resource_info, _suggest_fix, _suggest_helm_fix, _get_or_create_loop,
KubernetesEngine._resolve_defaults, KubernetesEngine._inject_credentials,
and _server_side_apply URL construction.
"""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.execution.engine_interface import ModuleContext, OperationResult
from services.execution.kubernetes_engine import (
    KNOWN_PLURALS,
    KubernetesEngine,
    _get_or_create_loop,
    _get_resource_info,
    _server_side_apply,
    _suggest_fix,
    _suggest_helm_fix,
)

# ── _get_resource_info ───────────────────────────────────────────────────

class TestGetResourceInfo:
    def test_known_kind(self):
        assert _get_resource_info("v1", "Namespace") == "namespaces"
        assert _get_resource_info("v1", "ConfigMap") == "configmaps"
        assert _get_resource_info("v1", "Secret") == "secrets"

    def test_gateway_api_kinds(self):
        assert _get_resource_info("gateway.networking.k8s.io/v1", "Gateway") == "gateways"
        assert _get_resource_info("gateway.networking.k8s.io/v1", "HTTPRoute") == "httproutes"

    def test_bnk_crd_kinds(self):
        assert _get_resource_info("bnk.f5.com/v1", "BNKGatewayClassConfig") == "bnkgatewayclassconfigs"
        assert _get_resource_info("bnk.f5.com/v1", "BNKSecPolicy") == "bnksecpolicies"

    def test_unknown_kind_heuristic(self):
        result = _get_resource_info("v1", "FooBar")
        assert result == "foobars"

    def test_known_plurals_completeness(self):
        # Sanity: KNOWN_PLURALS has at least the core K8s resources
        core_kinds = ["Namespace", "ConfigMap", "Secret", "Service", "Deployment"]
        for kind in core_kinds:
            assert kind in KNOWN_PLURALS


# ── _suggest_fix ─────────────────────────────────────────────────────────

class TestSuggestFix:
    def test_forbidden_error(self):
        err = RuntimeError("Forbidden: insufficient permissions")
        result = _suggest_fix(err)
        assert result is not None
        assert "RBAC" in result

    def test_connection_refused(self):
        err = RuntimeError("Connection refused to cluster")
        result = _suggest_fix(err)
        assert "API server" in result

    def test_timeout_error(self):
        err = RuntimeError("Operation timed out waiting for resource")
        result = _suggest_fix(err)
        assert "timed out" in result.lower() or "timeout" in result.lower()

    def test_no_suggestion_for_generic_error(self):
        err = RuntimeError("something unexpected")
        result = _suggest_fix(err)
        assert result is None


# ── _suggest_helm_fix ────────────────────────────────────────────────────

class TestSuggestHelmFix:
    def test_unauthorized(self):
        result = _suggest_helm_fix("Error: 401 Unauthorized")
        assert result is not None
        assert "authentication" in result.lower()

    def test_chart_not_found(self):
        result = _suggest_helm_fix("Error: chart not found in repo")
        assert "chart" in result.lower()

    def test_timed_out(self):
        result = _suggest_helm_fix("Error: timed out waiting for condition")
        assert "timed out" in result.lower()

    def test_image_pull(self):
        result = _suggest_helm_fix("Warning: ImagePullBackOff for pod")
        assert "pull" in result.lower()

    def test_no_suggestion_for_generic(self):
        result = _suggest_helm_fix("some random error")
        assert result is None


# ── _get_or_create_loop ──────────────────────────────────────────────────

class TestGetOrCreateLoop:
    def test_returns_event_loop(self):
        loop = _get_or_create_loop()
        assert isinstance(loop, asyncio.AbstractEventLoop)
        assert not loop.is_closed()


# ── KubernetesEngine._inject_credentials ─────────────────────────────────

class TestInjectCredentials:
    def test_injects_and_restores_env(self):
        engine = KubernetesEngine("/tmp/kubeconfig")
        original = os.environ.get("TEST_CRED_KEY")
        assert original is None  # should not be set

        with engine._inject_credentials({"TEST_CRED_KEY": "secret123"}):
            assert os.environ["TEST_CRED_KEY"] == "secret123"

        assert os.environ.get("TEST_CRED_KEY") is None

    def test_empty_credentials_noop(self):
        engine = KubernetesEngine("/tmp/kubeconfig")
        with engine._inject_credentials({}):
            pass  # should not raise

    def test_restores_previous_value(self):
        engine = KubernetesEngine("/tmp/kubeconfig")
        os.environ["RESTORE_TEST"] = "original"
        try:
            with engine._inject_credentials({"RESTORE_TEST": "temp"}):
                assert os.environ["RESTORE_TEST"] == "temp"
            assert os.environ["RESTORE_TEST"] == "original"
        finally:
            del os.environ["RESTORE_TEST"]


# ── catalog-backed execution path ──────────────────────────────────────

class TestCatalogBackedExecution:
    def test_apply_catalog_manifest_uses_pack_artifacts_not_python_registry(self, tmp_path):
        engine = KubernetesEngine("/tmp/kubeconfig")

        manifest_dir = tmp_path / "manifests"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "cm.yaml").write_text(
            """
apiVersion: v1
kind: ConfigMap
metadata:
  name: ${config_name}
  namespace: ${namespace}
data:
  key: ${value}
""".strip(),
            encoding="utf-8",
        )

        ctx = ModuleContext(
            module_id=1,
            project_id=1,
            path="k8s/catalog-manifest",
            category="k8s",
            variables={"config_name": "demo", "namespace": "default", "value": "ok"},
            module_source_kind="git_catalog",
            deploy_model="manifests",
            workspace_path=str(tmp_path),
            pack_manifest={
                "deployment_pack": {
                    "working_directory": ".",
                    "entrypoints": {"manifest_path": "manifests"},
                }
            },
        )

        with patch.object(engine, "_apply_manifests_from_payload") as mock_apply:
            mock_apply.return_value = OperationResult(success=True, stdout="ok")
            result = engine.apply(ctx)

        assert result.success is True
        payload = mock_apply.call_args.args[0]
        assert payload["module_name"] == "k8s/catalog-manifest"
        assert payload["manifests"][0]["metadata"]["name"] == "demo"

    def test_apply_catalog_helm_uses_pack_artifacts_not_python_registry(self, tmp_path):
        engine = KubernetesEngine("/tmp/kubeconfig")

        chart_dir = tmp_path / "chart"
        chart_dir.mkdir(parents=True)
        values_file = tmp_path / "values.yaml"
        values_file.write_text("replicaCount: ${replicas}\n", encoding="utf-8")

        ctx = ModuleContext(
            module_id=2,
            project_id=1,
            path="k8s/catalog-helm",
            category="k8s",
            variables={"replicas": 2},
            module_source_kind="git_catalog",
            deploy_model="helm",
            workspace_path=str(tmp_path),
            pack_manifest={
                "module": {"name": "catalog-helm"},
                "deployment_pack": {
                    "working_directory": ".",
                    "entrypoints": {
                        "chart_path": "chart",
                        "values_path": "values.yaml",
                        "release_name": "demo-release",
                        "namespace": "apps",
                    },
                },
            },
        )

        with patch.object(engine, "_apply_helm_from_payload") as mock_apply:
            mock_apply.return_value = OperationResult(success=True, stdout="ok")
            result = engine.apply(ctx)

        assert result.success is True
        payload = mock_apply.call_args.args[0]
        assert payload["release_name"] == "demo-release"
        assert payload["namespace"] == "apps"
        # Pure variable reference preserves type (int, not string "2")
        assert payload["values"]["replicaCount"] == 2
        assert Path(payload["chart_ref"]).name == "chart"


# ── _server_side_apply URL construction ──────────────────────────────────

class TestServerSideApplyURL:
    """Verify _server_side_apply passes correct URL components to kr8s call_api.

    The critical bug (fixed): code was passing a pre-built full URL like
    /api/v1/namespaces/f5-operator as the `url` param, but kr8s call_api()
    prepends /api/v1 again via _construct_url(), producing a mangled
    double-prefixed URL like /api/v1//api/v1/namespaces/f5-operator → 404.

    The fix: pass version, base, namespace, and url as separate components
    so _construct_url() assembles them correctly.
    """

    def _make_api_mock(self):
        """Create a mock kr8s API that captures call_api arguments."""
        captured = {}

        @asynccontextmanager
        async def fake_call_api(method, **kwargs):
            captured["method"] = method
            captured.update(kwargs)
            yield MagicMock(status_code=200)

        api = MagicMock()
        api.call_api = fake_call_api
        return api, captured

    def test_core_v1_namespace_cluster_scoped(self):
        """Namespace is cluster-scoped v1 — no namespace in URL components."""
        api, captured = self._make_api_mock()
        loop = _get_or_create_loop()

        manifest = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": "f5-operator"},
        }
        loop.run_until_complete(_server_side_apply(api, manifest))

        assert captured["method"] == "PATCH"
        assert captured["version"] == "v1"
        assert captured["base"] == "/api"
        assert captured["namespace"] is None
        assert captured["url"] == "namespaces/f5-operator"

    def test_core_v1_secret_namespaced(self):
        """Secret is namespaced v1 — namespace passed separately."""
        api, captured = self._make_api_mock()
        loop = _get_or_create_loop()

        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "my-secret", "namespace": "f5-operator"},
        }
        loop.run_until_complete(_server_side_apply(api, manifest))

        assert captured["version"] == "v1"
        assert captured["base"] == "/api"
        assert captured["namespace"] == "f5-operator"
        assert captured["url"] == "secrets/my-secret"

    def test_crd_namespaced_resource(self):
        """CRD with group/version — uses /apis base."""
        api, captured = self._make_api_mock()
        loop = _get_or_create_loop()

        manifest = {
            "apiVersion": "k8s.f5net.com/v1",
            "kind": "F5SPKVlan",
            "metadata": {"name": "external", "namespace": "f5-operator"},
        }
        loop.run_until_complete(_server_side_apply(api, manifest))

        assert captured["version"] == "k8s.f5net.com/v1"
        assert captured["base"] == "/apis"
        assert captured["namespace"] == "f5-operator"
        assert captured["url"] == "f5-spk-vlans/external"

    def test_gateway_api_cluster_scoped(self):
        """GatewayClass is cluster-scoped with group/version."""
        api, captured = self._make_api_mock()
        loop = _get_or_create_loop()

        manifest = {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "GatewayClass",
            "metadata": {"name": "bnk-gatewayclass"},
        }
        loop.run_until_complete(_server_side_apply(api, manifest))

        assert captured["version"] == "gateway.networking.k8s.io/v1"
        assert captured["base"] == "/apis"
        assert captured["namespace"] is None
        assert captured["url"] == "gatewayclasses/bnk-gatewayclass"

    def test_bnk_crd_namespaced(self):
        """BNKGatewayClassConfig — namespaced CRD in gateway.f5.com group."""
        api, captured = self._make_api_mock()
        loop = _get_or_create_loop()

        manifest = {
            "apiVersion": "gateway.f5.com/v1",
            "kind": "BNKGatewayClassConfig",
            "metadata": {"name": "bnk-config", "namespace": "f5-bnk"},
        }
        loop.run_until_complete(_server_side_apply(api, manifest))

        assert captured["version"] == "gateway.f5.com/v1"
        assert captured["base"] == "/apis"
        assert captured["namespace"] == "f5-bnk"
        assert captured["url"] == "bnkgatewayclassconfigs/bnk-config"

    def test_cneinstance_crd(self):
        """CNEInstance — namespaced CRD in k8s.f5.com group."""
        api, captured = self._make_api_mock()
        loop = _get_or_create_loop()

        manifest = {
            "apiVersion": "k8s.f5.com/v1",
            "kind": "CNEInstance",
            "metadata": {"name": "bnk-instance", "namespace": "f5-operator"},
        }
        loop.run_until_complete(_server_side_apply(api, manifest))

        assert captured["version"] == "k8s.f5.com/v1"
        assert captured["base"] == "/apis"
        assert captured["namespace"] == "f5-operator"
        assert captured["url"] == "cneinstances/bnk-instance"

    def test_network_attachment_definition(self):
        """NetworkAttachmentDefinition — namespaced CRD in k8s.cni.cncf.io group."""
        api, captured = self._make_api_mock()
        loop = _get_or_create_loop()

        manifest = {
            "apiVersion": "k8s.cni.cncf.io/v1",
            "kind": "NetworkAttachmentDefinition",
            "metadata": {"name": "external-netdevice", "namespace": "f5-operator"},
        }
        loop.run_until_complete(_server_side_apply(api, manifest))

        assert captured["version"] == "k8s.cni.cncf.io/v1"
        assert captured["base"] == "/apis"
        assert captured["namespace"] == "f5-operator"
        assert captured["url"] == "network-attachment-definitions/external-netdevice"

    def test_crd_destroy_cleanup(self):
        """CustomResourceDefinition is cluster-scoped in apiextensions group."""
        api, captured = self._make_api_mock()
        loop = _get_or_create_loop()

        manifest = {
            "apiVersion": "apiextensions.k8s.io/v1",
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "cneinstances.k8s.f5.com"},
        }
        loop.run_until_complete(_server_side_apply(api, manifest))

        assert captured["version"] == "apiextensions.k8s.io/v1"
        assert captured["base"] == "/apis"
        assert captured["namespace"] is None
        assert captured["url"] == "customresourcedefinitions/cneinstances.k8s.f5.com"

    def test_server_side_apply_headers(self):
        """Verify PATCH uses correct SSA headers and params."""
        api, captured = self._make_api_mock()
        loop = _get_or_create_loop()

        manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "test", "namespace": "default"},
        }
        loop.run_until_complete(_server_side_apply(api, manifest))

        assert captured["headers"]["Content-Type"] == "application/apply-patch+yaml"
        assert captured["params"]["fieldManager"] == "bnk-forge"
        assert captured["params"]["force"] == "true"
        assert captured["raise_for_status"] is True
