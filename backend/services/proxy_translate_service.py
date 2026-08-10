"""
Proxy Translation Service — Ingress/HTTPRoute → BNK Gateway API manifest.

Pure translation logic: takes plain data (source_ingresses / source_httproutes as
plain dicts/objects) and returns rendered YAML + a list of unmapped/lossy constructs.
All K8s I/O (the re-fetch of live objects) lives in the route handler; this module
has no K8s API calls so D-023 P3 (BIG-IP/CIS source) can reuse it with its own fetch.

Target shape mirrors proxy_deploy_service._build_envoy_dataplane_manifest:
  gateway.networking.k8s.io/v1 · GatewayClass + Gateway + HTTPRoute
  yaml.safe_dump, "\\n---\\n".join idiom.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# BNK Gateway target constants
# ---------------------------------------------------------------------------

BNK_GATEWAY_CLASS_NAME = "f5-bnk"
BNK_GATEWAY_CONTROLLER = "cne.f5.io/gateway-controller"
GATEWAY_API_VERSION = "gateway.networking.k8s.io/v1"
FORGE_MANAGED_LABEL = "app.kubernetes.io/managed-by"
FORGE_LABEL_VALUE = "bnk-forge"

# Annotations that have a direct, lossless mapping to Gateway API constructs.
# Every other annotation is flagged as unmapped (D-017 allowlist-first).
_MAPPED_NGINX_ANNOTATIONS: frozenset[str] = frozenset()
_MAPPED_HAPROXY_ANNOTATIONS: frozenset[str] = frozenset()

# Path type mapping: Ingress → HTTPRoute
_INGRESS_PATH_TYPE_MAP: dict[str, str] = {
    "Prefix": "PathPrefix",
    "Exact": "Exact",
}

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class UnmappedEntry:
    """A lossy/unsupported construct that could not be translated (D-017)."""

    source_kind: str
    source_name: str
    source_namespace: str
    construct: str
    """What type of construct this is (e.g. 'annotation', 'extensionRef', 'path_type')."""
    detail: str
    """The raw value of the construct (annotation key, filter type, etc.)."""
    reason: str
    """Why it cannot be mapped automatically."""


@dataclass
class TranslationResult:
    """Result of translate_to_bnk()."""

    gatewayclass_yaml: str
    gateway_yaml: str
    httproute_yaml: str
    combined_yaml: str
    """All three docs joined by '\\n---\\n'."""
    unmapped: list[UnmappedEntry] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    """Summary metadata about the source proxy (proxy_type, name, namespace, etc.)."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def translate_to_bnk(
    *,
    proxy_type: str,
    source_kind: str,
    source_ingresses: list[Any],
    source_httproutes: list[dict],
    source_gateways: list[dict] | None = None,
    gateway_class_name: str = BNK_GATEWAY_CLASS_NAME,
    gateway_name: str | None = None,
    target_namespace: str | None = None,
) -> TranslationResult:
    """Translate source proxy objects to BNK Gateway API manifests.

    PURE function — no K8s I/O. Callers must pre-fetch live Ingress/HTTPRoute
    objects and pass them in as plain data.

    Args:
        proxy_type: One of 'nginx', 'haproxy', 'envoy', 'f5-bnk', or unknown.
        source_kind: 'IngressClass' or 'GatewayClass'.
        source_ingresses: List of live Ingress objects (kubernetes client objects
            or plain dicts). Only used for Ingress-backed proxy types.
        source_httproutes: List of live HTTPRoute dicts (all-namespace, filtered
            by caller). Only used for Gateway-API-backed proxy types.
        source_gateways: List of live Gateway dicts (optional, for passthrough).
        gateway_class_name: Target BNK GatewayClass name.
        gateway_name: Override for the target Gateway name; defaults to
            a slug derived from the proxy class + 'bnk-migrate'.
        target_namespace: Override for the Gateway namespace. Defaults to the
            first backend's namespace (avoids ReferenceGrant).

    Returns:
        TranslationResult with rendered YAML and any unmapped constructs.
    """
    source_gateways = source_gateways or []

    # Derive gateway_name
    if not gateway_name:
        # Use proxy_type as slug, strip non-alphanum, max 42 chars
        slug = proxy_type.replace("/", "-").replace(".", "-")[:30]
        gateway_name = f"{slug}-bnk-migrate"

    unmapped: list[UnmappedEntry] = []

    if source_kind == "IngressClass" or proxy_type in ("nginx", "haproxy"):
        httproute_dict, listeners, gw_namespace = _ingress_to_httproute(
            proxy_type=proxy_type,
            source_ingresses=source_ingresses,
            gateway_name=gateway_name,
            target_namespace=target_namespace,
            unmapped=unmapped,
        )
    else:
        # GatewayClass → passthrough (envoy, f5-bnk, unknown Gateway API)
        httproute_dict, listeners, gw_namespace = _httproute_passthrough(
            proxy_type=proxy_type,
            source_httproutes=source_httproutes,
            source_gateways=source_gateways,
            gateway_name=gateway_name,
            target_namespace=target_namespace,
            unmapped=unmapped,
        )

    gw_namespace = gw_namespace or "default"

    # Build the three manifest dicts
    gatewayclass_dict = _build_gatewayclass(gateway_class_name)
    gateway_dict = _build_gateway(gateway_name, gw_namespace, listeners, gateway_class_name)

    # Render YAML
    gatewayclass_yaml = yaml.safe_dump(gatewayclass_dict, default_flow_style=False)
    gateway_yaml = yaml.safe_dump(gateway_dict, default_flow_style=False)
    httproute_yaml = yaml.safe_dump(httproute_dict, default_flow_style=False)
    combined_yaml = "\n---\n".join([gatewayclass_yaml, gateway_yaml, httproute_yaml])

    return TranslationResult(
        gatewayclass_yaml=gatewayclass_yaml,
        gateway_yaml=gateway_yaml,
        httproute_yaml=httproute_yaml,
        combined_yaml=combined_yaml,
        unmapped=unmapped,
        source={
            "proxy_type": proxy_type,
            "source_kind": source_kind,
            "ingress_count": len(source_ingresses),
            "httproute_count": len(source_httproutes),
            "gateway_name": gateway_name,
            "gateway_namespace": gw_namespace,
        },
    )


# ---------------------------------------------------------------------------
# Ingress → HTTPRoute translation
# ---------------------------------------------------------------------------


def _ingress_to_httproute(
    *,
    proxy_type: str,
    source_ingresses: list[Any],
    gateway_name: str,
    target_namespace: str | None,
    unmapped: list[UnmappedEntry],
) -> tuple[dict, list[dict], str | None]:
    """Translate Ingress objects to a single HTTPRoute dict + listeners.

    Returns (httproute_dict, listeners, gateway_namespace).
    """
    rules: list[dict] = []
    hostnames: list[str] = []
    tls_listeners: list[dict] = []
    gw_namespace: str | None = target_namespace

    for ing in source_ingresses:
        # Support both k8s client objects and plain dicts
        ing_dict = _obj_to_dict(ing)
        meta = ing_dict.get("metadata", {}) or {}
        spec = ing_dict.get("spec", {}) or {}
        ing_name = meta.get("name", "unknown")
        ing_ns = meta.get("namespace", "default")
        annotations = meta.get("annotations", {}) or {}

        # Collect unmapped annotations (D-017)
        _collect_unmapped_annotations(
            annotations=annotations,
            source_kind="Ingress",
            source_name=ing_name,
            source_namespace=ing_ns,
            proxy_type=proxy_type,
            unmapped=unmapped,
        )

        # TLS → HTTPS listener
        for tls_entry in (spec.get("tls") or []):
            secret_name = tls_entry.get("secretName") or tls_entry.get("secret_name")
            tls_hosts = tls_entry.get("hosts") or []
            for host in tls_hosts:
                if host and host not in hostnames:
                    hostnames.append(host)
            if secret_name:
                tls_listeners.append({
                    "name": f"https-{secret_name[:20]}",
                    "port": 443,
                    "protocol": "HTTPS",
                    "hostname": tls_hosts[0] if tls_hosts else "*",
                    "tls": {
                        "mode": "Terminate",
                        "certificateRefs": [{
                            "kind": "Secret",
                            "name": secret_name,
                            "namespace": ing_ns,
                        }],
                    },
                    "allowedRoutes": {"namespaces": {"from": "All"}},
                })

        # Rules → HTTPRoute rules
        for rule in (spec.get("rules") or []):
            host = rule.get("host")
            if host and host not in hostnames:
                hostnames.append(host)

            http = rule.get("http", {}) or {}
            for path_obj in (http.get("paths") or []):
                backend = path_obj.get("backend", {}) or {}
                path_val = path_obj.get("path") or "/"
                path_type = path_obj.get("pathType") or "Prefix"

                # Resolve backend refs
                svc_info = backend.get("service", {}) or {}
                svc_name = svc_info.get("name") or ""
                port_info = svc_info.get("port", {}) or {}
                port_num = port_info.get("number")
                port_name = port_info.get("name")

                if not svc_name:
                    continue

                # Namespace: use ingress namespace (colocation avoids ReferenceGrant)
                backend_ns = ing_ns
                if gw_namespace is None:
                    gw_namespace = backend_ns

                # Cross-namespace backend check — compare against the namespace the HTTPRoute
                # actually lands in (gw_namespace), not the caller override (target_namespace).
                # gw_namespace is set to backend_ns for the *first* backend processed; any
                # subsequent backend in a different namespace requires a ReferenceGrant.
                if gw_namespace is not None and backend_ns != gw_namespace:
                    unmapped.append(UnmappedEntry(
                        source_kind="Ingress",
                        source_name=ing_name,
                        source_namespace=ing_ns,
                        construct="cross_namespace_backend",
                        detail=f"{svc_name}.{backend_ns}",
                        reason="Cross-namespace backend requires a ReferenceGrant. "
                               "HTTPRoute is placed in the first backend's namespace; "
                               f"this backend lives in '{backend_ns}'.",
                    ))

                # Port-by-name: flag, cannot resolve without Service GET
                if port_name and not port_num:
                    unmapped.append(UnmappedEntry(
                        source_kind="Ingress",
                        source_name=ing_name,
                        source_namespace=ing_ns,
                        construct="port_by_name",
                        detail=port_name,
                        reason="Service port is referenced by name. HTTPRoute requires a "
                               "numeric port. Resolve the port number from the Service "
                               "spec.ports and update the backendRef accordingly.",
                    ))
                    # Use 80 as a placeholder
                    port_num = 80

                # Translate path type
                http_path_type = _INGRESS_PATH_TYPE_MAP.get(path_type)
                if http_path_type is None:
                    # ImplementationSpecific → PathPrefix + flag
                    http_path_type = "PathPrefix"
                    unmapped.append(UnmappedEntry(
                        source_kind="Ingress",
                        source_name=ing_name,
                        source_namespace=ing_ns,
                        construct="path_type",
                        detail=f"pathType={path_type} path={path_val}",
                        reason="ImplementationSpecific path type translated to PathPrefix. "
                               "Review behaviour — actual matching semantics may differ.",
                    ))

                # Build match
                # Host matching is carried by spec.hostnames[] on the HTTPRoute — no header match needed.
                match: dict[str, Any] = {"path": {"type": http_path_type, "value": path_val}}

                backend_ref: dict[str, Any] = {"name": svc_name, "port": port_num or 80}
                if backend_ns != (gw_namespace or backend_ns):
                    # Only add namespace if it differs; Gateway API default is same namespace
                    backend_ref["namespace"] = backend_ns

                rules.append({
                    "matches": [match],
                    "backendRefs": [backend_ref],
                })

        # Default backend
        default_backend = spec.get("defaultBackend", {}) or {}
        db_svc = default_backend.get("service", {}) or {}
        db_svc_name = db_svc.get("name")
        if db_svc_name:
            db_port_info = db_svc.get("port", {}) or {}
            db_port_num = db_port_info.get("number") or 80
            rules.append({
                "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                "backendRefs": [{"name": db_svc_name, "port": db_port_num}],
            })

    if not rules:
        # No rules found — provide a placeholder
        rules = [{
            "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
            "backendRefs": [{"name": "REPLACE_WITH_SERVICE_NAME", "port": 80}],
        }]
        unmapped.append(UnmappedEntry(
            source_kind="Ingress",
            source_name="(none)",
            source_namespace="(none)",
            construct="no_rules",
            detail="No Ingress rules found for this class",
            reason="No matching Ingress objects found. "
                   "Replace the placeholder backendRef before applying.",
        ))

    # Build listeners (HTTP always present; HTTPS added if TLS configured)
    listeners = _build_listeners(hostnames, tls_listeners)

    httproute = _build_httproute(
        name=gateway_name,
        namespace=gw_namespace or "default",
        gateway_name=gateway_name,
        gateway_namespace=gw_namespace or "default",
        hostnames=hostnames,
        rules=rules,
    )

    return httproute, listeners, gw_namespace


# ---------------------------------------------------------------------------
# HTTPRoute passthrough (Gateway API sources: envoy, f5-bnk, unknown)
# ---------------------------------------------------------------------------


def _httproute_passthrough(
    *,
    proxy_type: str,
    source_httproutes: list[dict],
    source_gateways: list[dict],
    gateway_name: str,
    target_namespace: str | None,
    unmapped: list[UnmappedEntry],
) -> tuple[dict, list[dict], str | None]:
    """Near-passthrough for Gateway API sources.

    Copies hostnames/rules/backendRefs from source; re-points parentRefs to
    new BNK Gateway. Rebuilds listeners from source Gateway spec.listeners.

    Returns (httproute_dict, listeners, gateway_namespace).
    """
    if not source_httproutes:
        # Nothing to translate
        httproute = _build_httproute(
            name=gateway_name,
            namespace=target_namespace or "default",
            gateway_name=gateway_name,
            gateway_namespace=target_namespace or "default",
            hostnames=[],
            rules=[{
                "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                "backendRefs": [{"name": "REPLACE_WITH_SERVICE_NAME", "port": 80}],
            }],
        )
        unmapped.append(UnmappedEntry(
            source_kind="HTTPRoute",
            source_name="(none)",
            source_namespace="(none)",
            construct="no_routes",
            detail="No HTTPRoute objects found for this GatewayClass",
            reason="No matching HTTPRoute objects found. "
                   "Replace the placeholder backendRef before applying.",
        ))
        return httproute, [_default_listener()], target_namespace

    # Merge all rules, hostnames from source routes
    all_rules: list[dict] = []
    all_hostnames: list[str] = []
    gw_namespace: str | None = target_namespace

    for route in source_httproutes:
        meta = route.get("metadata", {}) or {}
        spec = route.get("spec", {}) or {}
        route_name = meta.get("name", "unknown")
        route_ns = meta.get("namespace", "default")

        if gw_namespace is None:
            gw_namespace = route_ns

        # Collect hostnames
        for h in (spec.get("hostnames") or []):
            if h and h not in all_hostnames:
                all_hostnames.append(h)

        # Translate rules
        for rule in (spec.get("rules") or []):
            new_rule: dict[str, Any] = {}

            # Copy matches
            if rule.get("matches"):
                new_rule["matches"] = rule["matches"]

            # Translate backendRefs — flag extensionRef, check cross-ns
            backend_refs: list[dict] = []
            for br in (rule.get("backendRefs") or []):
                if "group" in br and br.get("group") not in ("", None, "core"):
                    # extensionRef (CRD-based) — unfamiliar group
                    unmapped.append(UnmappedEntry(
                        source_kind="HTTPRoute",
                        source_name=route_name,
                        source_namespace=route_ns,
                        construct="extensionRef",
                        detail=f"group={br.get('group')} kind={br.get('kind')} name={br.get('name')}",
                        reason="Non-core backendRef group cannot be automatically mapped to BNK. "
                               "Review whether the target resource type is supported.",
                    ))
                    continue

                br_copy: dict[str, Any] = {
                    "name": br.get("name", ""),
                    "port": br.get("port", 80),
                }
                # Only include namespace if explicitly set in source
                if "namespace" in br:
                    br_copy["namespace"] = br["namespace"]

                backend_refs.append(br_copy)

            if backend_refs:
                new_rule["backendRefs"] = backend_refs
            elif rule.get("backendRefs"):
                # All backendRefs were unmapped — keep rule with placeholder
                new_rule["backendRefs"] = [{"name": "REPLACE_WITH_SERVICE_NAME", "port": 80}]
                unmapped.append(UnmappedEntry(
                    source_kind="HTTPRoute",
                    source_name=route_name,
                    source_namespace=route_ns,
                    construct="all_backends_unmapped",
                    detail=str(rule.get("backendRefs")),
                    reason="All backends in this rule were unmapped. Placeholder inserted.",
                ))

            # Translate filters — flag all non-standard filters
            for f in (rule.get("filters") or []):
                filter_type = f.get("type", "")
                if filter_type not in (
                    "RequestHeaderModifier", "ResponseHeaderModifier",
                    "RequestRedirect", "URLRewrite", "RequestMirror",
                ):
                    unmapped.append(UnmappedEntry(
                        source_kind="HTTPRoute",
                        source_name=route_name,
                        source_namespace=route_ns,
                        construct="filter",
                        detail=f"type={filter_type}",
                        reason="Non-standard filter type may not be supported by BNK. Review manually.",
                    ))
                else:
                    # Copy standard filters
                    new_rule.setdefault("filters", []).append(f)

            all_rules.append(new_rule)

    if not all_rules:
        all_rules = [{
            "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
            "backendRefs": [{"name": "REPLACE_WITH_SERVICE_NAME", "port": 80}],
        }]

    # Rebuild listeners from source gateway specs
    tls_listeners: list[dict] = []
    for gw in source_gateways:
        gw_spec = gw.get("spec", {}) or {}
        for listener in (gw_spec.get("listeners") or []):
            if listener.get("protocol") == "HTTPS" and listener.get("tls"):
                tls_listeners.append({
                    "name": listener.get("name", "https"),
                    "port": listener.get("port", 443),
                    "protocol": "HTTPS",
                    "hostname": listener.get("hostname", "*"),
                    "tls": listener["tls"],
                    "allowedRoutes": {"namespaces": {"from": "All"}},
                })

    listeners = _build_listeners(all_hostnames, tls_listeners)

    httproute = _build_httproute(
        name=gateway_name,
        namespace=gw_namespace or "default",
        gateway_name=gateway_name,
        gateway_namespace=gw_namespace or "default",
        hostnames=all_hostnames,
        rules=all_rules,
    )

    return httproute, listeners, gw_namespace


# ---------------------------------------------------------------------------
# Listener builder
# ---------------------------------------------------------------------------


def _default_listener() -> dict:
    return {
        "name": "http",
        "port": 80,
        "protocol": "HTTP",
        "allowedRoutes": {"namespaces": {"from": "All"}},
    }


def _build_listeners(
    hostnames: list[str],
    tls_listeners: list[dict],
) -> list[dict]:
    """Consolidate distinct hosts into HTTP + HTTPS listeners.

    Always adds one HTTP:80 listener. Adds unique HTTPS listeners from tls_listeners.
    """
    listeners: list[dict] = [_default_listener()]

    seen_https: set[str] = set()
    for tls_l in tls_listeners:
        key = f"{tls_l.get('name')}:{tls_l.get('port')}"
        if key not in seen_https:
            seen_https.add(key)
            listeners.append(tls_l)

    return listeners


# ---------------------------------------------------------------------------
# Manifest builders (mirror proxy_deploy_service idiom)
# ---------------------------------------------------------------------------


def _build_gatewayclass(gateway_class_name: str) -> dict:
    return {
        "apiVersion": GATEWAY_API_VERSION,
        "kind": "GatewayClass",
        "metadata": {"name": gateway_class_name},
        "spec": {"controllerName": BNK_GATEWAY_CONTROLLER},
    }


def _build_gateway(
    name: str,
    namespace: str,
    listeners: list[dict],
    gateway_class_name: str,
) -> dict:
    return {
        "apiVersion": GATEWAY_API_VERSION,
        "kind": "Gateway",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {FORGE_MANAGED_LABEL: FORGE_LABEL_VALUE},
        },
        "spec": {
            "gatewayClassName": gateway_class_name,
            "listeners": listeners,
        },
    }


def _build_httproute(
    *,
    name: str,
    namespace: str,
    gateway_name: str,
    gateway_namespace: str,
    hostnames: list[str],
    rules: list[dict],
) -> dict:
    spec: dict[str, Any] = {
        "parentRefs": [{
            "name": gateway_name,
            "namespace": gateway_namespace,
        }],
        "rules": rules,
    }
    if hostnames:
        spec["hostnames"] = hostnames

    return {
        "apiVersion": GATEWAY_API_VERSION,
        "kind": "HTTPRoute",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {FORGE_MANAGED_LABEL: FORGE_LABEL_VALUE},
        },
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# Annotation unmapped collector (D-017)
# ---------------------------------------------------------------------------

_NGINX_ANNOTATION_PREFIX = "nginx.ingress.kubernetes.io"
_HAPROXY_ANNOTATION_PREFIX = "haproxy.org"
_F5_ANNOTATION_PREFIX = "virtual-server.f5.com"

# F5 CIS Ingress annotation → unmapped reason catalog (D-017).
# Each annotation key → human-readable reason why it cannot be auto-mapped.
_F5_ANNOTATION_REASONS: dict[str, str] = {
    "virtual-server.f5.com/ip": (
        "BIG-IP VIP address (re-IP/DNS cutover replaces it). "
        "Update DNS to point to the new BNK Gateway address after migration."
    ),
    "virtual-server.f5.com/clientssl": (
        "TLS profile referenced by name (BIG-IP Secret). "
        "Provide a Kubernetes Secret with the certificate and update the "
        "Gateway listener certificateRefs."
    ),
    "virtual-server.f5.com/health": (
        "BIG-IP health monitor has no direct Gateway API equivalent. "
        "Configure readiness probes on the backend Service/Pod."
    ),
    "virtual-server.f5.com/balance": (
        "BIG-IP load-balancing algorithm has no direct Gateway API equivalent. "
        "Gateway API does not expose an LB algorithm selector; "
        "configure at the backend Service / EndpointSlice level."
    ),
    "virtual-server.f5.com/partition": (
        "BIG-IP partition is not applicable to in-cluster Gateway API. "
        "The migration moves the data path into the Kubernetes cluster; "
        "manual BIG-IP partition cleanup required post-migration."
    ),
    "ingress.kubernetes.io/ssl-redirect": (
        "SSL redirect flag — configure an HTTPRoute filter (RequestRedirect "
        "scheme=https) to reproduce this behaviour."
    ),
    "ingress.kubernetes.io/allow-http": (
        "allow-http flag — configure an HTTPRoute filter (RequestRedirect "
        "scheme=https + deny HTTP) to reproduce this behaviour."
    ),
}


def _collect_unmapped_annotations(
    annotations: dict[str, str],
    source_kind: str,
    source_name: str,
    source_namespace: str,
    proxy_type: str,
    unmapped: list[UnmappedEntry],
) -> None:
    """Flag all proxy-specific annotations as unmapped (D-017 allowlist-first)."""
    for key, value in annotations.items():
        # Only flag controller-specific annotations
        is_nginx = key.startswith(_NGINX_ANNOTATION_PREFIX)
        is_haproxy = key.startswith(_HAPROXY_ANNOTATION_PREFIX)
        is_envoy = "envoy" in key.lower() or "envoyproxy" in key.lower()
        is_f5 = key.startswith(_F5_ANNOTATION_PREFIX) or key in _F5_ANNOTATION_REASONS

        if is_nginx and proxy_type in ("nginx",):
            if key not in _MAPPED_NGINX_ANNOTATIONS:
                unmapped.append(UnmappedEntry(
                    source_kind=source_kind,
                    source_name=source_name,
                    source_namespace=source_namespace,
                    construct="annotation",
                    detail=f"{key}={value}",
                    reason="NGINX-specific annotation has no direct Gateway API equivalent. "
                           "Implement equivalent behaviour via Gateway API filters or policies.",
                ))
        elif is_haproxy and proxy_type in ("haproxy",):
            if key not in _MAPPED_HAPROXY_ANNOTATIONS:
                unmapped.append(UnmappedEntry(
                    source_kind=source_kind,
                    source_name=source_name,
                    source_namespace=source_namespace,
                    construct="annotation",
                    detail=f"{key}={value}",
                    reason="HAProxy-specific annotation has no direct Gateway API equivalent. "
                           "Review and implement via Gateway API policies.",
                ))
        elif is_envoy:
            unmapped.append(UnmappedEntry(
                source_kind=source_kind,
                source_name=source_name,
                source_namespace=source_namespace,
                construct="annotation",
                detail=f"{key}={value}",
                reason="Envoy-specific annotation cannot be automatically mapped. Review manually.",
            ))
        elif is_f5 and proxy_type in ("cis-f5", "cis-bigip", "f5"):
            reason = _F5_ANNOTATION_REASONS.get(key)
            if reason is None:
                # Unknown F5 CIS annotation — flag generically
                reason = (
                    f"F5 CIS-specific annotation '{key}' has no direct Gateway API equivalent. "
                    "Review and implement via Gateway API filters or policies."
                )
            unmapped.append(UnmappedEntry(
                source_kind=source_kind,
                source_name=source_name,
                source_namespace=source_namespace,
                construct="annotation",
                detail=f"{key}={value}",
                reason=reason,
            ))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _obj_to_dict(obj: Any) -> dict:
    """Convert a k8s client object to a plain dict, or pass through if already a dict."""
    if isinstance(obj, dict):
        return obj

    # If the whole object has a to_dict method (e.g. kubernetes client V1Ingress), use it
    if hasattr(obj, "to_dict"):
        raw = obj.to_dict()
        return raw if isinstance(raw, dict) else {}

    # Fallback: reconstruct from known top-level attributes
    result: dict[str, Any] = {}
    for attr in ("metadata", "spec", "status"):
        val = getattr(obj, attr, None)
        if val is None:
            result[attr] = {}
            continue
        if hasattr(val, "to_dict"):
            result[attr] = val.to_dict()
        elif hasattr(val, "__dict__"):
            result[attr] = _sanitize_k8s_obj(val)
        else:
            result[attr] = val

    return result


def _sanitize_k8s_obj(obj: Any) -> Any:
    """Recursively convert a k8s client object graph to plain Python dicts/lists."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, list):
        return [_sanitize_k8s_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _sanitize_k8s_obj(v) for k, v in obj.items()}
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return {
            k: _sanitize_k8s_obj(v)
            for k, v in obj.__dict__.items()
            if not k.startswith("_")
        }
    return obj
