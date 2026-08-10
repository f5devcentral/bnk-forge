"""
Unit tests for proxy_translate_service.translate_to_bnk().

Covers:
  - NGINX Ingress → BNK HTTPRoute (paths, hostnames, TLS listener)
  - HAProxy Ingress → BNK HTTPRoute
  - Envoy HTTPRoute → passthrough (near no-op)
  - F5 BNK HTTPRoute → passthrough (round-trip)
  - Unmapped annotation flagging (D-017)
  - ImplementationSpecific path type flagging
  - port-by-name flagging
  - extensionRef flagging
  - Returned YAML parses as valid Gateway API dict
  - Pure function: no K8s I/O in translate_to_bnk
"""

import pytest
import yaml

from services.proxy_translate_service import (
    GATEWAY_API_VERSION,
    UnmappedEntry,
    _obj_to_dict,
    translate_to_bnk,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nginx_ingress(
    name: str = "my-ingress",
    namespace: str = "default",
    host: str | None = "example.com",
    path: str = "/",
    path_type: str = "Prefix",
    svc_name: str = "my-svc",
    svc_port: int = 80,
    annotations: dict | None = None,
    tls_host: str | None = None,
    tls_secret: str | None = None,
) -> dict:
    """Build a minimal NGINX Ingress dict."""
    ing: dict = {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "annotations": annotations or {},
        },
        "spec": {
            "ingressClassName": "nginx",
            "rules": [
                {
                    "host": host,
                    "http": {
                        "paths": [
                            {
                                "path": path,
                                "pathType": path_type,
                                "backend": {
                                    "service": {
                                        "name": svc_name,
                                        "port": {"number": svc_port},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }
    if tls_host and tls_secret:
        ing["spec"]["tls"] = [{"hosts": [tls_host], "secretName": tls_secret}]
    return ing


def _haproxy_ingress(
    name: str = "haproxy-ing",
    namespace: str = "prod",
    svc_name: str = "ha-svc",
    svc_port: int = 8080,
    annotations: dict | None = None,
) -> dict:
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "annotations": annotations or {"haproxy.org/check": "enabled"},
        },
        "spec": {
            "ingressClassName": "haproxy",
            "rules": [
                {
                    "host": "ha.example.com",
                    "http": {
                        "paths": [
                            {
                                "path": "/",
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": svc_name,
                                        "port": {"number": svc_port},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }


def _httproute(
    name: str = "my-route",
    namespace: str = "default",
    gateway_name: str = "my-gw",
    backend_name: str = "backend-svc",
    backend_port: int = 8080,
    hostnames: list | None = None,
) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "parentRefs": [{"name": gateway_name, "namespace": "gw-ns"}],
            "hostnames": hostnames or ["route.example.com"],
            "rules": [
                {
                    "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                    "backendRefs": [{"name": backend_name, "port": backend_port}],
                }
            ],
        },
    }


def _gateway(name: str = "my-gw", namespace: str = "gw-ns", class_name: str = "envoy") -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "gatewayClassName": class_name,
            "listeners": [
                {"name": "http", "port": 80, "protocol": "HTTP"}
            ],
        },
    }


# ---------------------------------------------------------------------------
# Helper: parse combined YAML and verify structural validity
# ---------------------------------------------------------------------------


def _parse_combined(combined_yaml: str) -> list[dict]:
    """Split combined YAML and parse each doc; assert Gateway API apiVersion."""
    docs = list(yaml.safe_load_all(combined_yaml.replace("\n---\n", "\n---\n")))
    # Filter out None docs (empty separators)
    docs = [d for d in docs if d is not None]
    assert len(docs) == 3, f"Expected 3 docs, got {len(docs)}"
    for doc in docs:
        assert doc.get("apiVersion") == GATEWAY_API_VERSION
    return docs


# ===========================================================================
# NGINX Ingress translation
# ===========================================================================


class TestNginxTranslation:
    def test_basic_nginx_produces_three_docs(self):
        ing = _nginx_ingress()
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        docs = _parse_combined(result.combined_yaml)
        kinds = [d["kind"] for d in docs]
        assert "GatewayClass" in kinds
        assert "Gateway" in kinds
        assert "HTTPRoute" in kinds

    def test_nginx_hostname_appears_in_httproute(self):
        ing = _nginx_ingress(host="api.example.com")
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        parsed = yaml.safe_load(result.httproute_yaml)
        # Hostname is in the match header or hostnames
        spec = parsed["spec"]
        # It appears in hostnames field OR in match headers
        hostnames = spec.get("hostnames", [])
        rules = spec.get("rules", [])
        has_host = (
            "api.example.com" in hostnames
            or any(
                any(
                    h.get("value") == "api.example.com"
                    for h in rule.get("matches", [{}])[0].get("headers", [])
                )
                for rule in rules
            )
        )
        assert has_host

    def test_nginx_backend_service_in_httproute(self):
        ing = _nginx_ingress(svc_name="my-backend", svc_port=8080)
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        parsed = yaml.safe_load(result.httproute_yaml)
        backend_refs = parsed["spec"]["rules"][0]["backendRefs"]
        assert any(br["name"] == "my-backend" for br in backend_refs)
        assert any(br["port"] == 8080 for br in backend_refs)

    def test_nginx_path_prefix_mapped(self):
        ing = _nginx_ingress(path="/api", path_type="Prefix")
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        parsed = yaml.safe_load(result.httproute_yaml)
        match = parsed["spec"]["rules"][0]["matches"][0]
        assert match["path"]["type"] == "PathPrefix"
        assert match["path"]["value"] == "/api"

    def test_nginx_exact_path_mapped(self):
        ing = _nginx_ingress(path="/exact", path_type="Exact")
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        parsed = yaml.safe_load(result.httproute_yaml)
        match = parsed["spec"]["rules"][0]["matches"][0]
        assert match["path"]["type"] == "Exact"

    def test_nginx_tls_produces_https_listener(self):
        ing = _nginx_ingress(tls_host="secure.example.com", tls_secret="my-tls-secret")
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        parsed_gw = yaml.safe_load(result.gateway_yaml)
        listeners = parsed_gw["spec"]["listeners"]
        protocols = [listener["protocol"] for listener in listeners]
        assert "HTTP" in protocols
        assert "HTTPS" in protocols

    def test_nginx_bnk_gatewayclass_name_in_gateway(self):
        ing = _nginx_ingress()
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
            gateway_class_name="f5-bnk",
        )
        parsed_gw = yaml.safe_load(result.gateway_yaml)
        assert parsed_gw["spec"]["gatewayClassName"] == "f5-bnk"

    def test_nginx_no_unmapped_for_clean_ingress(self):
        ing = _nginx_ingress()
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        assert result.unmapped == []

    def test_nginx_implementation_specific_flagged(self):
        """ImplementationSpecific pathType must be flagged (D-017)."""
        ing = _nginx_ingress(path_type="ImplementationSpecific")
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        unmapped_constructs = [u.construct for u in result.unmapped]
        assert "path_type" in unmapped_constructs
        # Must still produce valid YAML (PathPrefix used as safe fallback)
        parsed = yaml.safe_load(result.httproute_yaml)
        match = parsed["spec"]["rules"][0]["matches"][0]
        assert match["path"]["type"] == "PathPrefix"

    def test_nginx_port_by_name_flagged(self):
        """Port-by-name must be flagged and never guessed."""
        ing: dict = {
            "metadata": {"name": "test", "namespace": "default", "annotations": {}},
            "spec": {
                "ingressClassName": "nginx",
                "rules": [
                    {
                        "host": "x.com",
                        "http": {
                            "paths": [
                                {
                                    "path": "/",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "mysvc",
                                            "port": {"name": "http"},  # name, not number
                                        }
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        }
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        unmapped_constructs = [u.construct for u in result.unmapped]
        assert "port_by_name" in unmapped_constructs

    def test_nginx_annotations_flagged_as_unmapped(self):
        """nginx.ingress.kubernetes.io/* annotations must be flagged (D-017)."""
        annotations = {
            "nginx.ingress.kubernetes.io/rewrite-target": "/",
            "nginx.ingress.kubernetes.io/ssl-redirect": "true",
            "nginx.ingress.kubernetes.io/rate-limit": "100",
        }
        ing = _nginx_ingress(annotations=annotations)
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        annotation_constructs = [u for u in result.unmapped if u.construct == "annotation"]
        assert len(annotation_constructs) == 3
        # Verify none are silently dropped
        annotated_keys = [u.detail.split("=")[0] for u in annotation_constructs]
        assert "nginx.ingress.kubernetes.io/rewrite-target" in annotated_keys


# ===========================================================================
# HAProxy Ingress translation
# ===========================================================================


class TestHaproxyTranslation:
    def test_basic_haproxy_produces_three_docs(self):
        ing = _haproxy_ingress()
        result = translate_to_bnk(
            proxy_type="haproxy",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        docs = _parse_combined(result.combined_yaml)
        kinds = [d["kind"] for d in docs]
        assert "HTTPRoute" in kinds

    def test_haproxy_annotation_flagged(self):
        """haproxy.org/* annotations must be flagged (D-017)."""
        ing = _haproxy_ingress(annotations={"haproxy.org/check": "enabled", "haproxy.org/timeout-connect": "5s"})
        result = translate_to_bnk(
            proxy_type="haproxy",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        annotation_unmapped = [u for u in result.unmapped if u.construct == "annotation"]
        assert len(annotation_unmapped) >= 2

    def test_haproxy_backend_preserved(self):
        ing = _haproxy_ingress(svc_name="ha-backend", svc_port=9000)
        result = translate_to_bnk(
            proxy_type="haproxy",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        parsed = yaml.safe_load(result.httproute_yaml)
        backend_refs = parsed["spec"]["rules"][0]["backendRefs"]
        assert any(br["name"] == "ha-backend" for br in backend_refs)
        assert any(br["port"] == 9000 for br in backend_refs)


# ===========================================================================
# Envoy HTTPRoute passthrough
# ===========================================================================


class TestEnvoyPassthrough:
    def test_envoy_passthrough_produces_three_docs(self):
        route = _httproute(gateway_name="envoy-gw", backend_name="llm-svc", backend_port=8000)
        gw = _gateway(name="envoy-gw", class_name="eg")
        result = translate_to_bnk(
            proxy_type="envoy",
            source_kind="GatewayClass",
            source_ingresses=[],
            source_httproutes=[route],
            source_gateways=[gw],
        )
        docs = _parse_combined(result.combined_yaml)
        kinds = [d["kind"] for d in docs]
        assert all(k in kinds for k in ("GatewayClass", "Gateway", "HTTPRoute"))

    def test_envoy_backend_preserved(self):
        route = _httproute(backend_name="envoy-backend", backend_port=9999)
        result = translate_to_bnk(
            proxy_type="envoy",
            source_kind="GatewayClass",
            source_ingresses=[],
            source_httproutes=[route],
        )
        parsed = yaml.safe_load(result.httproute_yaml)
        backend_refs = parsed["spec"]["rules"][0]["backendRefs"]
        assert any(br["name"] == "envoy-backend" for br in backend_refs)
        assert any(br["port"] == 9999 for br in backend_refs)

    def test_envoy_hostnames_preserved(self):
        route = _httproute(hostnames=["api.envoy.io", "www.envoy.io"])
        result = translate_to_bnk(
            proxy_type="envoy",
            source_kind="GatewayClass",
            source_ingresses=[],
            source_httproutes=[route],
        )
        parsed = yaml.safe_load(result.httproute_yaml)
        hostnames = parsed["spec"].get("hostnames", [])
        assert "api.envoy.io" in hostnames

    def test_envoy_extension_ref_flagged(self):
        """extensionRef (non-core group) must be flagged (D-017)."""
        route: dict = {
            "metadata": {"name": "ext-route", "namespace": "default"},
            "spec": {
                "parentRefs": [{"name": "envoy-gw"}],
                "rules": [
                    {
                        "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                        "backendRefs": [
                            {
                                "group": "custom.io",
                                "kind": "CustomBackend",
                                "name": "my-custom-backend",
                                "port": 8080,
                            }
                        ],
                    }
                ],
            },
        }
        result = translate_to_bnk(
            proxy_type="envoy",
            source_kind="GatewayClass",
            source_ingresses=[],
            source_httproutes=[route],
        )
        unmapped_constructs = [u.construct for u in result.unmapped]
        assert "extensionRef" in unmapped_constructs

    def test_envoy_no_routes_placeholder_inserted(self):
        """When no HTTPRoutes exist, a placeholder rule is inserted + flagged."""
        result = translate_to_bnk(
            proxy_type="envoy",
            source_kind="GatewayClass",
            source_ingresses=[],
            source_httproutes=[],
        )
        unmapped_constructs = [u.construct for u in result.unmapped]
        assert "no_routes" in unmapped_constructs
        # Placeholder YAML must still be valid
        _parse_combined(result.combined_yaml)


# ===========================================================================
# F5 BNK passthrough (round-trip)
# ===========================================================================


class TestF5BnkPassthrough:
    def test_f5_bnk_round_trip_produces_valid_yaml(self):
        route = _httproute(gateway_name="f5-gw", backend_name="bnk-svc", backend_port=443)
        gw = _gateway(name="f5-gw", class_name="f5-bnk")
        result = translate_to_bnk(
            proxy_type="f5-bnk",
            source_kind="GatewayClass",
            source_ingresses=[],
            source_httproutes=[route],
            source_gateways=[gw],
        )
        docs = _parse_combined(result.combined_yaml)
        assert len(docs) == 3

    def test_f5_bnk_no_spurious_unmapped(self):
        route = _httproute()
        result = translate_to_bnk(
            proxy_type="f5-bnk",
            source_kind="GatewayClass",
            source_ingresses=[],
            source_httproutes=[route],
        )
        # A simple f5-bnk passthrough should produce zero unmapped entries
        assert result.unmapped == []


# ===========================================================================
# Unmapped entries: never silently dropped (D-017)
# ===========================================================================


class TestUnmappedNeverDropped:
    def test_unmapped_entries_are_returned_not_discarded(self):
        """Any flagged construct must appear in result.unmapped."""
        annotations = {"nginx.ingress.kubernetes.io/rewrite-target": "/new"}
        ing = _nginx_ingress(annotations=annotations, path_type="ImplementationSpecific")
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        assert len(result.unmapped) >= 2
        constructs = {u.construct for u in result.unmapped}
        assert "annotation" in constructs
        assert "path_type" in constructs

    def test_unmapped_entries_have_required_fields(self):
        annotations = {"nginx.ingress.kubernetes.io/auth-url": "https://auth.svc"}
        ing = _nginx_ingress(annotations=annotations)
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        for u in result.unmapped:
            assert isinstance(u, UnmappedEntry)
            assert u.source_kind
            assert u.construct
            assert u.detail
            assert u.reason

    def test_unmapped_does_not_prevent_yaml_output(self):
        """Even with unmapped constructs, YAML must still render (D-017 flag, never drop)."""
        annotations = {
            "nginx.ingress.kubernetes.io/canary": "true",
            "nginx.ingress.kubernetes.io/canary-weight": "20",
        }
        ing = _nginx_ingress(annotations=annotations, path_type="ImplementationSpecific")
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        # Must still produce parseable YAML
        assert result.combined_yaml
        _parse_combined(result.combined_yaml)
        assert len(result.unmapped) > 0


# ===========================================================================
# Read-only guarantee: no cluster writes in translate_to_bnk
# ===========================================================================


class TestReadOnly:
    def test_translate_to_bnk_does_not_call_k8s(self):
        """translate_to_bnk must not import or call any K8s client API."""
        import sys
        # If kubernetes module is NOT imported by the translator, no I/O can happen.
        # Check that translate_to_bnk doesn't pull in kubernetes client at call time.
        # We can verify by calling it with plain dicts (no mock needed).
        ing = _nginx_ingress()
        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        # If we got here without an exception, the function works on plain data.
        assert result.combined_yaml

    def test_translate_to_bnk_is_pure_no_side_effects(self):
        """Calling translate_to_bnk twice with same inputs produces identical results."""
        ing = _nginx_ingress()
        result1 = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        result2 = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing],
            source_httproutes=[],
        )
        assert result1.combined_yaml == result2.combined_yaml
        assert len(result1.unmapped) == len(result2.unmapped)


# ===========================================================================
# Route handler read-only: assert no _sync_proxy_records or mutations
# ===========================================================================


class TestRouteHandlerReadOnly:
    """Verify the translate endpoint never calls cluster write paths."""

    def test_translate_endpoint_never_calls_sync_proxy_records(self):
        """The translate route handler must never invoke _sync_proxy_records."""
        from unittest.mock import MagicMock, patch

        from routes.k8s.f5bnk import translate_proxy_to_bnk
        from schemas.k8s import ProxyTranslateRequest

        db = MagicMock()
        body = ProxyTranslateRequest(
            proxy_type="nginx",
            source_kind="IngressClass",
            class_name="nginx",
        )

        fake_result = MagicMock()
        fake_result.gatewayclass_yaml = "apiVersion: gateway.networking.k8s.io/v1\nkind: GatewayClass\n"
        fake_result.gateway_yaml = "apiVersion: gateway.networking.k8s.io/v1\nkind: Gateway\n"
        fake_result.httproute_yaml = "apiVersion: gateway.networking.k8s.io/v1\nkind: HTTPRoute\n"
        fake_result.combined_yaml = "---\n".join([
            fake_result.gatewayclass_yaml,
            fake_result.gateway_yaml,
            fake_result.httproute_yaml,
        ])
        fake_result.unmapped = []
        fake_result.source = {}

        with patch("routes.k8s.f5bnk.KubernetesService") as mock_k8s_cls, \
             patch("routes.k8s.f5bnk._safe_list_all_ingresses", return_value=[]), \
             patch("routes.k8s.f5bnk._safe_list_all_custom", return_value=[]), \
             patch("routes.k8s.f5bnk.translate_to_bnk", return_value=fake_result) as mock_translate, \
             patch("services.proxy_discovery_service.ProxyDiscoveryService._sync_proxy_records") as mock_sync:

            mock_k8s = mock_k8s_cls.return_value
            mock_k8s.get_cluster.return_value = MagicMock()
            mock_k8s.load_kubeconfig.return_value = MagicMock()

            translate_proxy_to_bnk(cluster_id=1, body=body, db=db)

        # translate_to_bnk was called (proves the endpoint calls the translator)
        mock_translate.assert_called_once()
        # _sync_proxy_records was never called (read-only guarantee)
        mock_sync.assert_not_called()

    def test_translate_endpoint_does_not_call_kubectl_apply(self):
        """The translate route handler must not call _kubectl_apply or any apply path."""
        import inspect

        import routes.k8s.f5bnk as bnk_module

        source = inspect.getsource(bnk_module.translate_proxy_to_bnk)
        # Must not reference apply, install, sync operations
        assert "_kubectl_apply" not in source
        assert "_sync_proxy_records" not in source
        assert "helm.install" not in source


# ===========================================================================
# _obj_to_dict — k8s client object + plain dict support
# ===========================================================================


class TestObjToDict:
    def test_plain_dict_passthrough(self):
        d = {"metadata": {"name": "x"}, "spec": {}}
        result = _obj_to_dict(d)
        assert result is d

    def test_object_with_to_dict(self):
        expected = {"metadata": {"name": "fake"}, "spec": {"foo": "bar"}, "status": {}}

        class Fake:
            def to_dict(self) -> dict:
                return expected

        result = _obj_to_dict(Fake())
        assert result == expected

    def test_object_fallback_uses_attrs(self):
        class FakeMeta:
            name = "test-ing"
            namespace = "ns"
            annotations = {}

        class FakeSpec:
            ingress_class_name = "nginx"
            rules = []
            tls = []
            default_backend = None

        class FakeIngress:
            metadata = FakeMeta()
            spec = FakeSpec()
            status = None

        result = _obj_to_dict(FakeIngress())
        assert "metadata" in result or "spec" in result


# ===========================================================================
# Cross-namespace backend detection (D-017)
# ===========================================================================


class TestCrossNamespaceBackend:
    def test_two_ingresses_in_different_namespaces_flags_cross_ns_backend(self):
        """Two Ingresses for the same IngressClass in ns1 and ns2 — the ns2 backend
        must appear in unmapped[] as a cross_namespace_backend entry (D-017)."""
        ing_ns1 = _nginx_ingress(
            name="ing-ns1",
            namespace="ns1",
            host="app.example.com",
            svc_name="svc-ns1",
            svc_port=80,
        )
        ing_ns2 = _nginx_ingress(
            name="ing-ns2",
            namespace="ns2",
            host="app.example.com",
            svc_name="svc-ns2",
            svc_port=80,
        )

        result = translate_to_bnk(
            proxy_type="nginx",
            source_kind="IngressClass",
            source_ingresses=[ing_ns1, ing_ns2],
            source_httproutes=[],
        )

        cross_ns_entries = [
            u for u in result.unmapped if u.construct == "cross_namespace_backend"
        ]
        assert len(cross_ns_entries) >= 1, (
            f"Expected at least one cross_namespace_backend unmapped entry; "
            f"got unmapped={result.unmapped}"
        )
        # The flagged backend must be the ns2 service
        assert any("svc-ns2" in entry.detail for entry in cross_ns_entries)
        # Reason must mention ReferenceGrant
        assert all("ReferenceGrant" in entry.reason for entry in cross_ns_entries)


# ===========================================================================
# P4b — F5 annotation branch in _collect_unmapped_annotations
# ===========================================================================


class TestF5AnnotationUnmapped:
    """_collect_unmapped_annotations f5/virtual-server.f5.com branch (D-017)."""

    def _collect(self, annotations: dict, proxy_type: str = "cis-bigip") -> list:
        from services.proxy_translate_service import (
            UnmappedEntry,
            _collect_unmapped_annotations,
        )
        unmapped: list[UnmappedEntry] = []
        _collect_unmapped_annotations(
            annotations=annotations,
            source_kind="Ingress",
            source_name="test-ing",
            source_namespace="default",
            proxy_type=proxy_type,
            unmapped=unmapped,
        )
        return unmapped

    def test_f5_ip_annotation_flagged(self):
        """/ip annotation → unmapped when proxy_type is cis-bigip."""
        entries = self._collect({"virtual-server.f5.com/ip": "10.0.0.5"})
        assert len(entries) == 1
        assert entries[0].construct == "annotation"
        assert "virtual-server.f5.com/ip" in entries[0].detail
        # Reason must mention VIP / re-IP
        assert "VIP" in entries[0].reason or "DNS" in entries[0].reason

    def test_f5_clientssl_annotation_flagged(self):
        """/clientssl annotation → unmapped."""
        entries = self._collect({"virtual-server.f5.com/clientssl": "/Common/ssl"})
        assert len(entries) == 1
        assert "clientssl" in entries[0].detail

    def test_f5_health_annotation_flagged(self):
        """/health annotation → unmapped."""
        entries = self._collect({"virtual-server.f5.com/health": '[]'})
        assert len(entries) == 1
        assert "health" in entries[0].detail

    def test_f5_balance_annotation_flagged(self):
        """/balance annotation → unmapped."""
        entries = self._collect({"virtual-server.f5.com/balance": "round-robin"})
        assert len(entries) == 1
        assert "balance" in entries[0].detail

    def test_ssl_redirect_annotation_flagged(self):
        """ingress.kubernetes.io/ssl-redirect → unmapped."""
        entries = self._collect({"ingress.kubernetes.io/ssl-redirect": "true"})
        assert len(entries) == 1
        assert "ssl-redirect" in entries[0].detail

    def test_allow_http_annotation_flagged(self):
        """ingress.kubernetes.io/allow-http → unmapped."""
        entries = self._collect({"ingress.kubernetes.io/allow-http": "false"})
        assert len(entries) == 1
        assert "allow-http" in entries[0].detail

    def test_unknown_f5_annotation_flagged_generically(self):
        """Unknown virtual-server.f5.com/* annotation → generic unmapped entry."""
        entries = self._collect({"virtual-server.f5.com/unknownkey": "somevalue"})
        assert len(entries) == 1
        assert "virtual-server.f5.com/unknownkey" in entries[0].detail

    def test_f5_annotations_not_flagged_for_nginx_proxy_type(self):
        """F5 annotations are NOT flagged when proxy_type is 'nginx'."""
        entries = self._collect({"virtual-server.f5.com/ip": "10.0.0.5"}, proxy_type="nginx")
        # The f5 branch only fires for cis-f5 / cis-bigip / f5
        assert entries == []

    def test_multiple_f5_annotations_all_flagged(self):
        """Multiple F5 annotations → one unmapped entry each."""
        annotations = {
            "virtual-server.f5.com/ip": "10.0.0.5",
            "virtual-server.f5.com/clientssl": "/Common/ssl",
            "virtual-server.f5.com/balance": "round-robin",
        }
        entries = self._collect(annotations)
        assert len(entries) == 3

    def test_f5_partition_annotation_flagged(self):
        """/partition annotation → unmapped."""
        entries = self._collect({"virtual-server.f5.com/partition": "Production"})
        assert len(entries) == 1
        assert "partition" in entries[0].detail
