"""
Tests for _server_side_apply — the core K8s manifest apply function.

Validates that the function constructs correct API URLs for kr8s call_api
across all supported resource types (core, apps, CRDs, custom resources).

This is critical because kr8s's call_api always runs _construct_url(version,
base, namespace, url) which joins base/version/[namespaces/ns/]url.  Passing
a pre-built full path as url= causes double-prefixing and 404s on custom
resources (the original bug: /api/v1/apis/k8s.cni.cncf.io/v1/...).
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from services.execution.kubernetes_engine import _server_side_apply

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_manifest(kind: str, api_version: str, name: str, namespace: str | None = "default") -> dict:
    """Build a minimal K8s manifest dict."""
    meta = {"name": name}
    if namespace:
        meta["namespace"] = namespace
    return {"apiVersion": api_version, "kind": kind, "metadata": meta}


class CallRecorder:
    """Records all call_api invocations with their keyword args.

    kr8s call_api is an async context manager.  This class records the args
    so we can assert the correct version/base/namespace/url were passed.
    """

    def __init__(self, *, fail_patch: bool = False, fail_create: bool = False):
        self.calls: list[dict] = []
        self._fail_patch = fail_patch
        self._fail_create = fail_create
        self._call_count = 0

    @asynccontextmanager
    async def call_api(self, method, **kwargs):
        self.calls.append({"method": method, **kwargs})
        self._call_count += 1
        if method == "PATCH" and self._fail_patch:
            raise RuntimeError("simulated PATCH failure")
        if method == "POST" and self._fail_create:
            raise RuntimeError("simulated POST failure")
        yield MagicMock()


def _construct_url(version: str, base: str, namespace: str | None, url: str) -> str:
    """Replicate kr8s _construct_url logic for verification.

    This is the EXACT logic from kr8s/_api.py:123-143.  We use it to verify
    that the args we pass to call_api will produce the correct URL.
    """
    if not base:
        if version == "v1":
            base = "/api"
        elif "/" in version:
            base = "/apis"
        else:
            raise ValueError("Unknown API version, base must be specified.")
    parts = [base]
    if version:
        parts.append(version)
    if namespace:
        parts.extend(["namespaces", namespace])
    parts.append(url)
    return "/".join(parts)


def _resolve_url_from_call(recorded_call: dict) -> str:
    """Apply kr8s URL construction to a recorded call_api invocation."""
    return _construct_url(
        version=recorded_call.get("version", "v1"),
        base=recorded_call.get("base", ""),
        namespace=recorded_call.get("namespace"),
        url=recorded_call.get("url", ""),
    )


# ── Core Resources (apiVersion: v1) ─────────────────────────────────────


class TestCoreResources:
    """Test URL construction for core API resources (apiVersion: v1)."""

    @pytest.mark.asyncio
    async def test_configmap_namespaced(self):
        api = CallRecorder()
        manifest = _make_manifest("ConfigMap", "v1", "my-config", "default")
        await _server_side_apply(api, manifest)

        assert len(api.calls) == 1
        c = api.calls[0]
        assert c["method"] == "PATCH"
        assert c["base"] == "/api"
        assert c["version"] == "v1"
        assert c["namespace"] == "default"
        assert c["url"] == "configmaps/my-config"

        resolved = _resolve_url_from_call(c)
        assert resolved == "/api/v1/namespaces/default/configmaps/my-config"

    @pytest.mark.asyncio
    async def test_secret_namespaced(self):
        api = CallRecorder()
        manifest = _make_manifest("Secret", "v1", "tls-secret", "kube-system")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        resolved = _resolve_url_from_call(c)
        assert resolved == "/api/v1/namespaces/kube-system/secrets/tls-secret"

    @pytest.mark.asyncio
    async def test_service_namespaced(self):
        api = CallRecorder()
        manifest = _make_manifest("Service", "v1", "my-svc", "production")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        resolved = _resolve_url_from_call(c)
        assert resolved == "/api/v1/namespaces/production/services/my-svc"

    @pytest.mark.asyncio
    async def test_namespace_cluster_scoped(self):
        api = CallRecorder()
        manifest = _make_manifest("Namespace", "v1", "my-namespace", namespace=None)
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["namespace"] is None
        resolved = _resolve_url_from_call(c)
        assert resolved == "/api/v1/namespaces/my-namespace"

    @pytest.mark.asyncio
    async def test_serviceaccount_namespaced(self):
        api = CallRecorder()
        manifest = _make_manifest("ServiceAccount", "v1", "deployer", "default")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/api/v1/namespaces/default/serviceaccounts/deployer"


# ── Apps / Extensions API (apiVersion: apps/v1, batch/v1, etc.) ──────────


class TestAppsResources:
    """Test URL construction for apps/v1, batch/v1, rbac API groups."""

    @pytest.mark.asyncio
    async def test_deployment(self):
        api = CallRecorder()
        manifest = _make_manifest("Deployment", "apps/v1", "nginx", "default")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["base"] == "/apis"
        assert c["version"] == "apps/v1"
        resolved = _resolve_url_from_call(c)
        assert resolved == "/apis/apps/v1/namespaces/default/deployments/nginx"

    @pytest.mark.asyncio
    async def test_statefulset(self):
        api = CallRecorder()
        manifest = _make_manifest("StatefulSet", "apps/v1", "redis", "cache")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/apps/v1/namespaces/cache/statefulsets/redis"

    @pytest.mark.asyncio
    async def test_daemonset(self):
        api = CallRecorder()
        manifest = _make_manifest("DaemonSet", "apps/v1", "fluentd", "logging")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/apps/v1/namespaces/logging/daemonsets/fluentd"

    @pytest.mark.asyncio
    async def test_job(self):
        api = CallRecorder()
        manifest = _make_manifest("Job", "batch/v1", "migration", "default")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["version"] == "batch/v1"
        resolved = _resolve_url_from_call(c)
        assert resolved == "/apis/batch/v1/namespaces/default/jobs/migration"

    @pytest.mark.asyncio
    async def test_cronjob(self):
        api = CallRecorder()
        manifest = _make_manifest("CronJob", "batch/v1", "cleanup", "default")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/batch/v1/namespaces/default/cronjobs/cleanup"

    @pytest.mark.asyncio
    async def test_clusterrole_cluster_scoped(self):
        api = CallRecorder()
        manifest = _make_manifest("ClusterRole", "rbac.authorization.k8s.io/v1", "admin-role", namespace=None)
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["namespace"] is None
        assert c["version"] == "rbac.authorization.k8s.io/v1"
        resolved = _resolve_url_from_call(c)
        assert resolved == "/apis/rbac.authorization.k8s.io/v1/clusterroles/admin-role"

    @pytest.mark.asyncio
    async def test_clusterrolebinding_cluster_scoped(self):
        api = CallRecorder()
        manifest = _make_manifest(
            "ClusterRoleBinding", "rbac.authorization.k8s.io/v1", "admin-binding", namespace=None
        )
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/rbac.authorization.k8s.io/v1/clusterrolebindings/admin-binding"

    @pytest.mark.asyncio
    async def test_role_namespaced(self):
        api = CallRecorder()
        manifest = _make_manifest("Role", "rbac.authorization.k8s.io/v1", "editor", "dev")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/rbac.authorization.k8s.io/v1/namespaces/dev/roles/editor"


# ── Multus / NetworkAttachmentDefinition (the original bug) ──────────────


class TestMultusCRD:
    """Test the exact resource type that triggered the original bug.

    NetworkAttachmentDefinition (k8s.cni.cncf.io/v1) was failing because
    the old code passed a pre-built URL to call_api, which double-prefixed
    it as /api/v1/apis/k8s.cni.cncf.io/v1/... instead of /apis/k8s.cni.cncf.io/v1/...
    """

    @pytest.mark.asyncio
    async def test_nad_namespaced(self):
        api = CallRecorder()
        manifest = _make_manifest(
            "NetworkAttachmentDefinition", "k8s.cni.cncf.io/v1", "external-netdevice", "f5-operator"
        )
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["method"] == "PATCH"
        assert c["base"] == "/apis"
        assert c["version"] == "k8s.cni.cncf.io/v1"
        assert c["namespace"] == "f5-operator"
        assert c["url"] == "network-attachment-definitions/external-netdevice"

        resolved = _resolve_url_from_call(c)
        assert resolved == "/apis/k8s.cni.cncf.io/v1/namespaces/f5-operator/network-attachment-definitions/external-netdevice"

    @pytest.mark.asyncio
    async def test_nad_internal_netdevice(self):
        api = CallRecorder()
        manifest = _make_manifest(
            "NetworkAttachmentDefinition", "k8s.cni.cncf.io/v1", "internal-netdevice", "f5-operator"
        )
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/k8s.cni.cncf.io/v1/namespaces/f5-operator/network-attachment-definitions/internal-netdevice"

    @pytest.mark.asyncio
    async def test_nad_url_not_double_prefixed(self):
        """Regression: ensure /api/v1/ is NOT prepended to custom resource URLs."""
        api = CallRecorder()
        manifest = _make_manifest(
            "NetworkAttachmentDefinition", "k8s.cni.cncf.io/v1", "test-nad", "test-ns"
        )
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert not resolved.startswith("/api/v1/apis/"), (
            f"URL is double-prefixed: {resolved}"
        )
        assert resolved.startswith("/apis/k8s.cni.cncf.io/v1/")


# ── Gateway API CRDs ────────────────────────────────────────────────────


class TestGatewayAPICRDs:
    @pytest.mark.asyncio
    async def test_gatewayclass_cluster_scoped(self):
        api = CallRecorder()
        manifest = _make_manifest("GatewayClass", "gateway.networking.k8s.io/v1", "bnk-gc", namespace=None)
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["version"] == "gateway.networking.k8s.io/v1"
        resolved = _resolve_url_from_call(c)
        assert resolved == "/apis/gateway.networking.k8s.io/v1/gatewayclasses/bnk-gc"

    @pytest.mark.asyncio
    async def test_gateway_namespaced(self):
        api = CallRecorder()
        manifest = _make_manifest("Gateway", "gateway.networking.k8s.io/v1", "my-gw", "bnk-gw")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/gateway.networking.k8s.io/v1/namespaces/bnk-gw/gateways/my-gw"

    @pytest.mark.asyncio
    async def test_httproute_namespaced(self):
        api = CallRecorder()
        manifest = _make_manifest("HTTPRoute", "gateway.networking.k8s.io/v1", "web-route", "default")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/gateway.networking.k8s.io/v1/namespaces/default/httproutes/web-route"


# ── BNK / F5 CRDs ───────────────────────────────────────────────────────


class TestBNKCRDs:
    @pytest.mark.asyncio
    async def test_bnk_gateway_class_config(self):
        api = CallRecorder()
        manifest = _make_manifest("BNKGatewayClassConfig", "bnk.f5.com/v1", "bgcc-1", "f5-operator")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["version"] == "bnk.f5.com/v1"
        resolved = _resolve_url_from_call(c)
        assert resolved == "/apis/bnk.f5.com/v1/namespaces/f5-operator/bnkgatewayclassconfigs/bgcc-1"

    @pytest.mark.asyncio
    async def test_f5spkvlan(self):
        api = CallRecorder()
        manifest = _make_manifest("F5SPKVlan", "k8s.f5net.com/v1", "external-vlan", "f5-operator")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/k8s.f5net.com/v1/namespaces/f5-operator/f5-spk-vlans/external-vlan"

    @pytest.mark.asyncio
    async def test_cneinstance(self):
        api = CallRecorder()
        manifest = _make_manifest("CNEInstance", "bnk.f5.com/v1", "cne-1", "f5-operator")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/bnk.f5.com/v1/namespaces/f5-operator/cneinstances/cne-1"

    @pytest.mark.asyncio
    async def test_f5_big_fw_policy(self):
        api = CallRecorder()
        manifest = _make_manifest("F5BigFwPolicy", "k8s.f5net.com/v1", "deny-all", "f5-operator")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/k8s.f5net.com/v1/namespaces/f5-operator/f5-big-fw-policies/deny-all"

    @pytest.mark.asyncio
    async def test_l4route(self):
        api = CallRecorder()
        manifest = _make_manifest("L4Route", "bnk.f5.com/v1", "tcp-route", "bnk-gw")
        await _server_side_apply(api, manifest)

        resolved = _resolve_url_from_call(api.calls[0])
        assert resolved == "/apis/bnk.f5.com/v1/namespaces/bnk-gw/l4routes/tcp-route"


# ── Unknown / Heuristic Kinds ───────────────────────────────────────────


class TestUnknownKinds:
    """Kinds not in KNOWN_PLURALS should still produce correct URLs via heuristic."""

    @pytest.mark.asyncio
    async def test_unknown_crd_uses_lowercase_s(self):
        api = CallRecorder()
        manifest = _make_manifest("MyCustomWidget", "widgets.example.com/v1beta1", "w1", "default")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["version"] == "widgets.example.com/v1beta1"
        assert c["url"] == "mycustomwidgets/w1"
        resolved = _resolve_url_from_call(c)
        assert resolved == "/apis/widgets.example.com/v1beta1/namespaces/default/mycustomwidgets/w1"

    @pytest.mark.asyncio
    async def test_unknown_core_kind(self):
        api = CallRecorder()
        manifest = _make_manifest("FooBar", "v1", "fb-1", "default")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["version"] == "v1"
        assert c["base"] == "/api"
        assert c["url"] == "foobars/fb-1"


# ── Fallback Behavior (PATCH fails → CREATE) ────────────────────────────


class TestFallbackBehavior:
    @pytest.mark.asyncio
    async def test_patch_success_no_fallback(self):
        api = CallRecorder(fail_patch=False)
        manifest = _make_manifest("ConfigMap", "v1", "cm-1", "default")
        await _server_side_apply(api, manifest)

        assert len(api.calls) == 1
        assert api.calls[0]["method"] == "PATCH"

    @pytest.mark.asyncio
    async def test_patch_fails_falls_back_to_create(self):
        api = CallRecorder(fail_patch=True, fail_create=False)
        manifest = _make_manifest("ConfigMap", "v1", "cm-1", "default")
        await _server_side_apply(api, manifest)

        assert len(api.calls) == 2
        assert api.calls[0]["method"] == "PATCH"
        assert api.calls[1]["method"] == "POST"

    @pytest.mark.asyncio
    async def test_create_url_omits_resource_name(self):
        """POST (create) URL should NOT include the resource name — only the collection."""
        api = CallRecorder(fail_patch=True, fail_create=False)
        manifest = _make_manifest("Secret", "v1", "my-secret", "default")
        await _server_side_apply(api, manifest)

        create_call = api.calls[1]
        assert create_call["method"] == "POST"
        assert create_call["url"] == "secrets"  # no /my-secret
        resolved = _resolve_url_from_call(create_call)
        assert resolved == "/api/v1/namespaces/default/secrets"

    @pytest.mark.asyncio
    async def test_create_fallback_for_crd(self):
        """Fallback CREATE for a CRD also uses correct /apis/ base."""
        api = CallRecorder(fail_patch=True, fail_create=False)
        manifest = _make_manifest(
            "NetworkAttachmentDefinition", "k8s.cni.cncf.io/v1", "test-nad", "f5-operator"
        )
        await _server_side_apply(api, manifest)

        create_call = api.calls[1]
        assert create_call["base"] == "/apis"
        assert create_call["version"] == "k8s.cni.cncf.io/v1"
        assert create_call["url"] == "network-attachment-definitions"
        resolved = _resolve_url_from_call(create_call)
        assert resolved == "/apis/k8s.cni.cncf.io/v1/namespaces/f5-operator/network-attachment-definitions"

    @pytest.mark.asyncio
    async def test_both_fail_raises_runtime_error(self):
        api = CallRecorder(fail_patch=True, fail_create=True)
        manifest = _make_manifest("ConfigMap", "v1", "cm-1", "default")

        with pytest.raises(RuntimeError, match="Failed to apply ConfigMap/cm-1"):
            await _server_side_apply(api, manifest)

    @pytest.mark.asyncio
    async def test_error_message_includes_both_errors(self):
        api = CallRecorder(fail_patch=True, fail_create=True)
        manifest = _make_manifest("Secret", "v1", "s-1", "default")

        with pytest.raises(RuntimeError, match="server-side apply failed.*create also failed"):
            await _server_side_apply(api, manifest)

    @pytest.mark.asyncio
    async def test_fallback_preserves_namespace_for_cluster_scoped(self):
        """Cluster-scoped CREATE should also have namespace=None."""
        api = CallRecorder(fail_patch=True, fail_create=False)
        manifest = _make_manifest("ClusterRole", "rbac.authorization.k8s.io/v1", "my-role", namespace=None)
        await _server_side_apply(api, manifest)

        create_call = api.calls[1]
        assert create_call["namespace"] is None
        resolved = _resolve_url_from_call(create_call)
        assert resolved == "/apis/rbac.authorization.k8s.io/v1/clusterroles"


# ── Server-Side Apply Headers & Params ───────────────────────────────────


class TestSSAHeaders:
    @pytest.mark.asyncio
    async def test_patch_uses_apply_patch_content_type(self):
        api = CallRecorder()
        manifest = _make_manifest("ConfigMap", "v1", "cm", "default")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["headers"]["Content-Type"] == "application/apply-patch+yaml"

    @pytest.mark.asyncio
    async def test_patch_sends_field_manager(self):
        api = CallRecorder()
        manifest = _make_manifest("ConfigMap", "v1", "cm", "default")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["params"]["fieldManager"] == "bnk-forge"
        assert c["params"]["force"] == "true"

    @pytest.mark.asyncio
    async def test_create_uses_json_content_type(self):
        api = CallRecorder(fail_patch=True)
        manifest = _make_manifest("ConfigMap", "v1", "cm", "default")
        await _server_side_apply(api, manifest)

        create_call = api.calls[1]
        assert create_call["headers"]["Content-Type"] == "application/json"

    @pytest.mark.asyncio
    async def test_data_is_serialized_manifest(self):
        api = CallRecorder()
        manifest = _make_manifest("ConfigMap", "v1", "cm", "default")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert json.loads(c["data"]) == manifest

    @pytest.mark.asyncio
    async def test_raise_for_status_is_set(self):
        api = CallRecorder()
        manifest = _make_manifest("ConfigMap", "v1", "cm", "default")
        await _server_side_apply(api, manifest)

        assert api.calls[0]["raise_for_status"] is True


# ── Manifest Edge Cases ──────────────────────────────────────────────────


class TestManifestEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_apiversion_defaults_to_v1(self):
        api = CallRecorder()
        manifest = {"kind": "ConfigMap", "metadata": {"name": "cm", "namespace": "default"}}
        # No apiVersion key
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["base"] == "/api"
        assert c["version"] == "v1"

    @pytest.mark.asyncio
    async def test_name_with_dots(self):
        """Resource names can contain dots (e.g. cert-manager.io)."""
        api = CallRecorder()
        manifest = _make_manifest("ConfigMap", "v1", "cert-manager.io", "default")
        await _server_side_apply(api, manifest)

        assert api.calls[0]["url"] == "configmaps/cert-manager.io"

    @pytest.mark.asyncio
    async def test_long_namespace(self):
        api = CallRecorder()
        manifest = _make_manifest("Secret", "v1", "s1", "my-very-long-namespace-name")
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        assert c["namespace"] == "my-very-long-namespace-name"
        resolved = _resolve_url_from_call(c)
        assert "/namespaces/my-very-long-namespace-name/" in resolved


# ── Parametrized: Every KNOWN_PLURALS Entry ──────────────────────────────

# Map kinds to realistic apiVersion values for parametrized testing
_KIND_TO_API_VERSION = {
    # Core v1
    "Namespace": "v1",
    "ConfigMap": "v1",
    "Secret": "v1",
    "Service": "v1",
    "ServiceAccount": "v1",
    "ResourceQuota": "v1",
    "LimitRange": "v1",
    "PersistentVolumeClaim": "v1",
    # apps/v1
    "Deployment": "apps/v1",
    "StatefulSet": "apps/v1",
    "DaemonSet": "apps/v1",
    # batch/v1
    "Job": "batch/v1",
    "CronJob": "batch/v1",
    # RBAC
    "ClusterRole": "rbac.authorization.k8s.io/v1",
    "ClusterRoleBinding": "rbac.authorization.k8s.io/v1",
    "Role": "rbac.authorization.k8s.io/v1",
    "RoleBinding": "rbac.authorization.k8s.io/v1",
    # Multus
    "NetworkAttachmentDefinition": "k8s.cni.cncf.io/v1",
    # Gateway API
    "GatewayClass": "gateway.networking.k8s.io/v1",
    "Gateway": "gateway.networking.k8s.io/v1",
    "HTTPRoute": "gateway.networking.k8s.io/v1",
    "GRPCRoute": "gateway.networking.k8s.io/v1",
    # BNK CRDs
    "BNKGatewayClassConfig": "bnk.f5.com/v1",
    "BNKSecPolicy": "bnk.f5.com/v1",
    "BNKNetPolicy": "bnk.f5.com/v1",
    "L4Route": "bnk.f5.com/v1",
    "CNEInstance": "bnk.f5.com/v1",
    # F5 SPK
    "F5SPKVlan": "k8s.f5net.com/v1",
    "F5SPKStaticRoute": "k8s.f5net.com/v1",
    "F5SPKSnatpool": "k8s.f5net.com/v1",
    "F5SPKEgress": "k8s.f5net.com/v1",
    # F5 BIG
    "F5BigFwPolicy": "k8s.f5net.com/v1",
    "F5BigFwRulelist": "k8s.f5net.com/v1",
    "F5BigCneAddresslist": "k8s.f5net.com/v1",
    "F5BigCnePortlist": "k8s.f5net.com/v1",
    "F5BigCneIrule": "k8s.f5net.com/v1",
    "F5BigAnalyzer": "k8s.f5net.com/v1",
    "F5BigDdosGlobal": "k8s.f5net.com/v1",
    "F5BigLogHslpub": "k8s.f5net.com/v1",
    "F5BigLogProfile": "k8s.f5net.com/v1",
    "F5BigGlobalOptions": "k8s.f5net.com/v1",
    "F5BnkGateway": "k8s.f5net.com/v1",
    # IPAM
    "IPAMRange": "ipam.f5.com/v1",
}

# Cluster-scoped kinds (no namespace in URL)
_CLUSTER_SCOPED = {"Namespace", "ClusterRole", "ClusterRoleBinding", "GatewayClass"}


from services.execution.kubernetes_engine import KNOWN_PLURALS


class TestAllKnownPlurals:
    """Parametrized test: every entry in KNOWN_PLURALS produces a valid URL."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind,plural", list(KNOWN_PLURALS.items()), ids=list(KNOWN_PLURALS.keys()))
    async def test_url_construction(self, kind, plural):
        api_version = _KIND_TO_API_VERSION.get(kind, "custom.example.com/v1")
        namespace = None if kind in _CLUSTER_SCOPED else "test-ns"

        api = CallRecorder()
        manifest = _make_manifest(kind, api_version, "test-resource", namespace)
        await _server_side_apply(api, manifest)

        c = api.calls[0]
        resolved = _resolve_url_from_call(c)

        # Must NOT be double-prefixed
        assert not resolved.startswith("/api/v1/apis/"), f"Double-prefix for {kind}: {resolved}"

        # Must contain the correct plural
        assert f"/{plural}/test-resource" in resolved, f"Missing plural for {kind}: {resolved}"

        # Correct API base
        if api_version == "v1":
            assert resolved.startswith("/api/v1/")
        else:
            assert resolved.startswith(f"/apis/{api_version}/")

        # Namespace handling: cluster-scoped resources must NOT have /namespaces/{ns}/ in URL.
        # Note: the Namespace kind resolves to plural "namespaces" as a resource path segment,
        # so we check for the specific pattern /namespaces/{ns}/ rather than bare /namespaces/.
        if namespace:
            assert f"/namespaces/{namespace}/" in resolved
        else:
            assert c["namespace"] is None
            # Cluster-scoped: URL should be base/version/plural/name (no namespaces segment)
            assert "/namespaces/test-ns/" not in resolved
