"""
CIS → BNK Translator Service (D-023 P3 + P4a/P4b/P4c/P4d).

Pure translation logic: takes CIS VirtualServer / TransportServer / Policy CR
dicts (P3) and/or AS3 declarations from ConfigMaps or classic-device sources
(P4a/P4d) and returns rendered YAML + a list of unmapped/lossy constructs (D-017).

All K8s I/O lives in the route handler (_fetch_cis_crs_full in scanner/fetch.py).
All device I/O lives in icontrol_client.py.
This module has NO K8s API calls and NO iControl imports.

Reuses the target-build back-half from proxy_translate_service.py:
  UnmappedEntry, TranslationResult,
  _build_gatewayclass, _build_gateway, _build_httproute, _build_listeners,
  _obj_to_dict

Constraint — additive import-and-reuse only.  Do NOT edit proxy_translate_service.py.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from services.proxy_translate_service import (
    BNK_GATEWAY_CLASS_NAME,
    FORGE_LABEL_VALUE,
    FORGE_MANAGED_LABEL,
    TranslationResult,
    UnmappedEntry,
    _build_gateway,
    _build_gatewayclass,
    _build_httproute,
    _build_listeners,
    _ingress_to_httproute,
    _obj_to_dict,
)

logger = logging.getLogger(__name__)

# Gateway API alpha channel version for TCPRoute / UDPRoute
_GATEWAY_API_ALPHA_VERSION = "gateway.networking.k8s.io/v1alpha2"

# CIS API group
_CIS_API_GROUP = "cis.f5.com"

# AS3 class fields that are NOT tenants
_AS3_META_KEYS = frozenset({
    "class", "action", "persist", "schemaVersion", "id", "label", "remark",
    "updateMode", "historyLimit", "controls",
})

# Application class fields that are NOT VS/Pool objects
_AS3_APP_META_KEYS = frozenset({
    "class", "label", "remark", "constants", "serviceMain",
})

# Pool class
_AS3_POOL_CLASS = "Pool"

# Service classes we translate to HTTPRoute
_AS3_HTTP_CLASSES = frozenset({"Service_HTTP", "Service_HTTPS"})

# Service classes we translate to TCP/UDP route
_AS3_TCP_CLASSES = frozenset({"Service_TCP"})
_AS3_UDP_CLASSES = frozenset({"Service_UDP"})

# Common tenant (cross-tenant refs) — never translate
_AS3_COMMON_TENANT = "Common"

# ---------------------------------------------------------------------------
# Issue #268 — Verified CIS → BNK security CRD mapping
# ---------------------------------------------------------------------------
# Maps each CIS security field to the verified BNK CRD kind from
# core.k8s_resource_registry (k8s.f5net.com / gateway.k8s.f5net.com).
#
# Mapping rationale:
#   policyWAF / profileBotDefense → BNKSecPolicy
#     (gateway.k8s.f5net.com v1alpha1 — application-layer WAF/Bot security policy
#      for Gateway API integration; the app-security CRD in the BNK stack)
#   policyFirewall → F5BigFwPolicy
#     (k8s.f5net.com — network-layer firewall policy controlling ingress/egress)
#
# Unknown / future fields → None (falls through to UnmappedEntry).
# ---------------------------------------------------------------------------
_CIS_SECURITY_FIELD_TO_BNK_KIND: dict[str, str] = {
    "policyWAF": "BNKSecPolicy",
    "profileBotDefense": "BNKSecPolicy",
    "policyFirewall": "F5BigFwPolicy",
    # VirtualServer _collect_vs_unmapped field names:
    "waf": "BNKSecPolicy",
    "botDefense": "BNKSecPolicy",
    "firewallPolicy": "F5BigFwPolicy",
}

# API group + version for each mapped kind (for generated YAML skeleton)
_BNK_SECURITY_KIND_API: dict[str, tuple[str, str]] = {
    "BNKSecPolicy": ("gateway.k8s.f5net.com", "v1alpha1"),
    "F5BigFwPolicy": ("k8s.f5net.com", "v1"),
}


# ---------------------------------------------------------------------------
# AS3 declaration → BNK parser (shared between ConfigMap and device sources)
# ---------------------------------------------------------------------------


def _as3_declaration_to_bnk(
    declaration: dict,
    *,
    source_kind: str,
    source_name: str,
    source_namespace: str,
    unmapped: list[UnmappedEntry],
) -> tuple[list[dict], list[str], list[dict]]:
    """Parse an AS3 ADC declaration dict into HTTPRoute rules + hostnames + TLS listeners.

    Returns (rules, hostnames, tls_listeners).

    This is PURE — no K8s or iControl imports. All edge cases route to unmapped[]:
    - class != "AS3" / missing declaration / empty → single unmapped entry
    - /Common/Shared/* cross-tenant refs → unmapped
    - Raw-IP pool members (serverAddresses are IPs) → unmapped, no backendRef
    - Service_HTTPS clientTLS/serverTLS string refs → tls_profile_ref unmapped
    - virtualAddresses placeholders (0.0.0.0/IPAM) → vip_address unmapped
    - iRule/WAF/policy refs → security_policy / irule unmapped
    - Malformed/empty → single unmapped entry, never raise
    """
    rules: list[dict] = []
    hostnames: list[str] = []
    tls_listeners: list[dict] = []

    # Validate the outer envelope
    if not isinstance(declaration, dict):
        unmapped.append(UnmappedEntry(
            source_kind=source_kind,
            source_name=source_name,
            source_namespace=source_namespace,
            construct="as3_parse_error",
            detail="declaration is not a dict",
            reason="AS3 declaration must be a dict with class:AS3.",
        ))
        return rules, hostnames, tls_listeners

    # Accept both {class:AS3, declaration:{class:ADC,...}} and bare ADC
    top_class = declaration.get("class")
    if top_class == "AS3":
        adc = declaration.get("declaration") or {}
    elif top_class == "ADC":
        adc = declaration
    else:
        unmapped.append(UnmappedEntry(
            source_kind=source_kind,
            source_name=source_name,
            source_namespace=source_namespace,
            construct="as3_parse_error",
            detail=f"class={top_class!r}",
            reason="AS3 declaration must have class 'AS3' or 'ADC'. "
                   "This object cannot be translated.",
        ))
        return rules, hostnames, tls_listeners

    if not adc or not isinstance(adc, dict):
        unmapped.append(UnmappedEntry(
            source_kind=source_kind,
            source_name=source_name,
            source_namespace=source_namespace,
            construct="as3_parse_error",
            detail="empty ADC declaration",
            reason="AS3 declaration body is empty or missing. Nothing to translate.",
        ))
        return rules, hostnames, tls_listeners

    # Walk tenants → applications → virtual services
    for tenant_name, tenant_obj in adc.items():
        if tenant_name in _AS3_META_KEYS:
            continue
        if not isinstance(tenant_obj, dict):
            continue
        if tenant_obj.get("class") != "Tenant":
            continue

        for app_name, app_obj in tenant_obj.items():
            if app_name in _AS3_APP_META_KEYS or not isinstance(app_obj, dict):
                continue
            if app_obj.get("class") != "Application":
                continue

            # Collect Pools by name within this Application for resolution
            pools_in_app: dict[str, dict] = {}
            for obj_name, obj in app_obj.items():
                if isinstance(obj, dict) and obj.get("class") == _AS3_POOL_CLASS:
                    pools_in_app[obj_name] = obj

            # Translate each Service_* within the Application
            for vs_name, vs_obj in app_obj.items():
                if vs_name in _AS3_APP_META_KEYS or not isinstance(vs_obj, dict):
                    continue
                vs_class = vs_obj.get("class")
                if vs_class not in (_AS3_HTTP_CLASSES | _AS3_TCP_CLASSES | _AS3_UDP_CLASSES):
                    continue

                full_name = f"{tenant_name}/{app_name}/{vs_name}"
                _ctx = dict(
                    source_kind=source_kind,
                    source_name=f"{source_name}/{full_name}",
                    source_namespace=source_namespace,
                )

                # virtualAddresses — flag placeholders (0.0.0.0 / IPAM) → unmapped
                virtual_addresses = vs_obj.get("virtualAddresses") or []
                for vaddr in virtual_addresses:
                    # AS3 virtualAddresses can be strings or dicts with "use"
                    addr_str = vaddr if isinstance(vaddr, str) else (vaddr.get("use") or "")
                    if addr_str in ("0.0.0.0", "") or addr_str.startswith("/"):
                        unmapped.append(UnmappedEntry(
                            **_ctx,
                            construct="vip_address",
                            detail=f"virtualAddresses={addr_str!r}",
                            reason="BIG-IP VIP / IPAM address placeholder "
                                   "(0.0.0.0 or IPAM-managed). CIS rewrites this at deploy "
                                   "time; no real VIP known. Update DNS after migration.",
                        ))

                # iRules
                for irule in (vs_obj.get("iRules") or []):
                    irule_str = irule if isinstance(irule, str) else (irule.get("use") or str(irule))
                    unmapped.append(UnmappedEntry(
                        **_ctx,
                        construct="irule",
                        detail=irule_str,
                        reason="iRules are BIG-IP proprietary and have no Gateway API "
                               "equivalent. Replicate logic using HTTPRoute filters or "
                               "ExtensionRef policies.",
                    ))

                # WAF / policy / botDefense — verified BNK CRD kind mapping (#268)
                for policy_field in ("policyWAF", "profileBotDefense", "policyFirewall"):
                    val = vs_obj.get(policy_field)
                    if val:
                        bnk_kind = _CIS_SECURITY_FIELD_TO_BNK_KIND.get(policy_field)
                        if bnk_kind:
                            api_group, api_version = _BNK_SECURITY_KIND_API[bnk_kind]
                            unmapped.append(UnmappedEntry(
                                **_ctx,
                                construct="security_policy",
                                detail=f"{policy_field}={val!r}",
                                reason=(
                                    f"Maps to BNK CRD kind '{bnk_kind}' "
                                    f"(apiVersion: {api_group}/{api_version}). "
                                    f"Create a {bnk_kind} CR referencing this policy "
                                    f"and attach it to the target Gateway or HTTPRoute."
                                ),
                            ))
                        else:
                            unmapped.append(UnmappedEntry(
                                **_ctx,
                                construct="security_policy",
                                detail=f"{policy_field}={val!r}",
                                reason="No verified BNK security CRD kind for this field. "
                                       "Manual mapping required.",
                            ))

                # Resolve pool — handle pointer refs and direct pool name
                pool_ref = vs_obj.get("pool")
                pool_obj: dict[str, Any] = {}
                if isinstance(pool_ref, str):
                    if pool_ref.startswith("/Common/") or pool_ref.startswith("/"):
                        # Cross-tenant / Common ref
                        unmapped.append(UnmappedEntry(
                            **_ctx,
                            construct="cross_tenant_pool_ref",
                            detail=pool_ref,
                            reason="/Common or cross-tenant pool refs cannot be resolved "
                                   "without access to all tenant declarations. Update the "
                                   "backendRef to the migrated Service name.",
                        ))
                        # Skip VS — no backendRef possible
                        continue
                    # Same-app name reference
                    pool_obj = pools_in_app.get(pool_ref) or {}
                elif isinstance(pool_ref, dict):
                    # Inline {"use": "<pool_name>"} pointer
                    use_ref = pool_ref.get("use") or ""
                    if use_ref.startswith("/Common/") or use_ref.startswith("/"):
                        unmapped.append(UnmappedEntry(
                            **_ctx,
                            construct="cross_tenant_pool_ref",
                            detail=use_ref,
                            reason="/Common or cross-tenant pool refs cannot be resolved.",
                        ))
                        continue
                    pool_obj = pools_in_app.get(use_ref) or {}

                # Resolve pool members
                members = (pool_obj.get("members") or []) if pool_obj else []
                backend_service: str | None = None
                backend_port: int = 80

                for member in members:
                    if not isinstance(member, dict):
                        continue
                    # serviceAddresses → K8s Service name (the CIS mode)
                    service_name = member.get("serviceAddress") or member.get("addressDiscovery")
                    server_addresses = member.get("serverAddresses") or []
                    member_port = member.get("servicePort") or member.get("port") or 80

                    if server_addresses:
                        # Raw-IP members — classic-device case, NO K8s Service → unmapped
                        for ip in server_addresses:
                            unmapped.append(UnmappedEntry(
                                **_ctx,
                                construct="raw_ip_pool_member",
                                detail=f"{ip}:{member_port}",
                                reason="Pool member is a raw IP address (classic BIG-IP / "
                                       "static member), not a Kubernetes Service. No "
                                       "backendRef can be generated automatically. Create "
                                       "a Kubernetes Service/EndpointSlice pointing to "
                                       f"{ip}:{member_port} and update the backendRef.",
                            ))
                        # Cannot produce a valid backendRef — skip this VS
                        backend_service = None
                        break
                    elif service_name:
                        backend_service = service_name
                        if isinstance(member_port, str) and not member_port.isdigit():
                            # Port-by-name
                            unmapped.append(UnmappedEntry(
                                **_ctx,
                                construct="port_by_name",
                                detail=str(member_port),
                                reason="Service port is referenced by name. HTTPRoute "
                                       "requires a numeric port. Resolve from Service spec.",
                            ))
                        else:
                            backend_port = int(member_port) if member_port else 80
                        break  # use first member with a service name

                if backend_service is None and not members:
                    # No members at all — flag and continue
                    unmapped.append(UnmappedEntry(
                        **_ctx,
                        construct="no_pool_members",
                        detail=f"pool={pool_ref!r}",
                        reason="Pool has no members. Cannot generate a backendRef.",
                    ))

                if vs_class in _AS3_HTTP_CLASSES:
                    # HTTP/HTTPS → HTTPRoute rule
                    virtual_port = vs_obj.get("virtualPort") or (443 if vs_class == "Service_HTTPS" else 80)

                    if vs_class == "Service_HTTPS":
                        # clientTLS / serverTLS string refs → tls_profile_ref unmapped
                        client_tls = vs_obj.get("clientTLS")
                        server_tls = vs_obj.get("serverTLS")
                        for tls_field, tls_val in (("clientTLS", client_tls), ("serverTLS", server_tls)):
                            if tls_val:
                                tls_str = tls_val if isinstance(tls_val, str) else str(tls_val)
                                unmapped.append(UnmappedEntry(
                                    **_ctx,
                                    construct="tls_profile_ref",
                                    detail=f"{tls_field}={tls_str}",
                                    reason="BIG-IP-native TLS profile by name cannot be "
                                           "automatically mapped. Provide a Kubernetes Secret "
                                           "with the certificate and update the Gateway listener "
                                           "certificateRefs.",
                                ))

                    if backend_service:
                        rules.append({
                            "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
                            "backendRefs": [{"name": backend_service, "port": backend_port}],
                        })

                        # Derive hostname from pool address or VS virtualAddresses
                        for vaddr in virtual_addresses:
                            addr_str = vaddr if isinstance(vaddr, str) else ""
                            # Only add non-IP, non-placeholder hostnames
                            if addr_str and not addr_str.startswith("0.0") and not _is_ip(addr_str):
                                if addr_str not in hostnames:
                                    hostnames.append(addr_str)

                elif vs_class in _AS3_TCP_CLASSES:
                    # TCP → unmapped note (TCPRoute requires numeric listener port, flag for review)
                    virtual_port = vs_obj.get("virtualPort") or 8080
                    if backend_service:
                        # We emit a rule-like note in unmapped — actual TCPRoute is out of
                        # scope for HTTP output; note it so operator knows
                        unmapped.append(UnmappedEntry(
                            **_ctx,
                            construct="tcp_service",
                            detail=f"port={virtual_port} backend={backend_service}:{backend_port}",
                            reason="AS3 Service_TCP maps to a TCPRoute (gateway.networking.k8s.io/"
                                   "v1alpha2). Review and add a TCPRoute manifest pointing "
                                   f"{backend_service}:{backend_port} on listener port {virtual_port}.",
                        ))

                elif vs_class in _AS3_UDP_CLASSES:
                    virtual_port = vs_obj.get("virtualPort") or 8080
                    if backend_service:
                        unmapped.append(UnmappedEntry(
                            **_ctx,
                            construct="udp_service",
                            detail=f"port={virtual_port} backend={backend_service}:{backend_port}",
                            reason="AS3 Service_UDP maps to a UDPRoute (gateway.networking.k8s.io/"
                                   "v1alpha2). Review and add a UDPRoute manifest pointing "
                                   f"{backend_service}:{backend_port} on listener port {virtual_port}.",
                        ))

    return rules, hostnames, tls_listeners


def _is_ip(value: str) -> bool:
    """Return True if value looks like an IPv4 or IPv6 address (not a hostname)."""
    import ipaddress
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _route_to_bnk(
    route_dict: dict,
    *,
    gateway_name: str,
    target_namespace: str | None,
    unmapped: list[UnmappedEntry],
) -> tuple[list[dict], list[str], list[dict], str | None]:
    """Translate one OpenShift Route dict to HTTPRoute rules + hostnames + TLS listeners.

    Returns (rules, hostnames, tls_listeners, namespace).

    PURE — no K8s imports. TLS termination mapping:
      - edge: HTTP listener only; inline cert/key → unmapped (cannot fabricate Secret).
      - passthrough: → unmapped (no standard Gateway-API HTTPRoute expression).
      - reencrypt: → Terminate listener (client leg) + backend_tls unmapped note.
      - None / missing: HTTP listener, no TLS note.

    alternateBackends: mapped as weighted backendRefs when spec.to.weight and
    spec.alternateBackends[] can both be resolved; cross-namespace backends
    flagged via cross_namespace_backend unmapped.

    wildcardPolicy: Subdomain → unmapped (no Gateway-API wildcard-subdomain equivalent).
    """
    meta = route_dict.get("metadata") or {}
    spec = route_dict.get("spec") or {}
    route_name = meta.get("name", "unknown")
    route_ns = meta.get("namespace", "default")

    rules: list[dict] = []
    hostnames: list[str] = []
    tls_listeners: list[dict] = []

    gw_namespace = target_namespace or route_ns

    # spec.host → hostname
    host = spec.get("host")
    if host:
        hostnames.append(host)

    # wildcardPolicy: Subdomain → unmapped
    wildcard_policy = spec.get("wildcardPolicy")
    if wildcard_policy and wildcard_policy.lower() == "subdomain":
        unmapped.append(UnmappedEntry(
            source_kind="Route",
            source_name=route_name,
            source_namespace=route_ns,
            construct="wildcard_subdomain",
            detail=f"wildcardPolicy={wildcard_policy}",
            reason="OpenShift wildcardPolicy: Subdomain has no Gateway API equivalent. "
                   "Configure individual host-specific HTTPRoutes for each subdomain.",
        ))

    # spec.path (default "/")
    path = spec.get("path") or "/"

    # spec.to.name → primary backend service
    to_spec = spec.get("to") or {}
    primary_svc = to_spec.get("name")
    primary_weight = to_spec.get("weight")
    if primary_weight is None:
        primary_weight = 100

    # spec.port.targetPort — may be numeric or named
    port_spec = spec.get("port") or {}
    target_port = port_spec.get("targetPort")
    port_num: int = 80
    if target_port is not None:
        if isinstance(target_port, int):
            port_num = target_port
        elif isinstance(target_port, str):
            if target_port.isdigit():
                port_num = int(target_port)
            else:
                # Port-by-name → unmapped + placeholder
                unmapped.append(UnmappedEntry(
                    source_kind="Route",
                    source_name=route_name,
                    source_namespace=route_ns,
                    construct="port_by_name",
                    detail=str(target_port),
                    reason="Route spec.port.targetPort is referenced by name. "
                           "HTTPRoute requires a numeric port. Resolve from the "
                           "Service spec.ports.",
                ))
                port_num = 80  # placeholder

    # TLS termination
    tls_spec = spec.get("tls") or {}
    termination = (tls_spec.get("termination") or "").lower()

    if termination == "passthrough":
        # TLS passthrough has no standard HTTPRoute expression → unmapped, no listener
        unmapped.append(UnmappedEntry(
            source_kind="Route",
            source_name=route_name,
            source_namespace=route_ns,
            construct="tls_passthrough",
            detail="tls.termination=passthrough",
            reason="TLS passthrough has no standard Gateway-API HTTPRoute expression; "
                   "use an alpha TLSRoute with mode: Passthrough — verify the CRD is "
                   "installed (gateway.networking.k8s.io/v1alpha2).",
        ))
        # Do NOT emit an HTTP listener or backendRef for passthrough routes
        return rules, hostnames, tls_listeners, route_ns

    if termination == "edge":
        # TLS terminates at the router. We never fabricate an HTTPS listener without
        # a real Secret. Inline cert/key (if present in spec.tls) must go to unmapped.
        has_inline_cert = bool(tls_spec.get("certificate") or tls_spec.get("key"))
        if has_inline_cert:
            unmapped.append(UnmappedEntry(
                source_kind="Route",
                source_name=route_name,
                source_namespace=route_ns,
                construct="tls_edge_inline_cert",
                detail="tls.termination=edge with inline certificate/key",
                reason="Edge TLS with an inline certificate/key: the certificate and key "
                       "must be stored in a Kubernetes Secret and referenced from the Gateway "
                       "listener certificateRefs. Forge cannot fabricate a Secret from inline "
                       "Route TLS fields. Create a Secret manually and update the listener.",
            ))
        else:
            # Uses the router default certificate
            unmapped.append(UnmappedEntry(
                source_kind="Route",
                source_name=route_name,
                source_namespace=route_ns,
                construct="tls_edge_router_default_cert",
                detail="tls.termination=edge (router default certificate)",
                reason="Edge TLS used the router default certificate; provide a Kubernetes "
                       "Secret with the certificate and configure it on the Gateway listener "
                       "certificateRefs.",
            ))
        # Emit an HTTP listener for the route (no HTTPS listener without a real cert)

    if termination == "reencrypt":
        # Client leg: Terminate listener; backend leg: unmapped
        # We only emit the listener if we have a real Secret ref; otherwise unmapped
        has_inline_cert = bool(tls_spec.get("certificate") or tls_spec.get("key"))
        if has_inline_cert:
            unmapped.append(UnmappedEntry(
                source_kind="Route",
                source_name=route_name,
                source_namespace=route_ns,
                construct="tls_reencrypt_inline_cert",
                detail="tls.termination=reencrypt with inline certificate/key",
                reason="Re-encrypt TLS with inline certificate/key: store the certificate "
                       "and key in a Kubernetes Secret and reference it from the Gateway "
                       "listener certificateRefs. Forge cannot fabricate a Secret.",
            ))
        else:
            # Emit a Terminate listener (client leg) — no real cert ref available
            unmapped.append(UnmappedEntry(
                source_kind="Route",
                source_name=route_name,
                source_namespace=route_ns,
                construct="tls_reencrypt_no_secret",
                detail="tls.termination=reencrypt (router default certificate)",
                reason="Re-encrypt TLS used the router default certificate for the client leg; "
                       "provide a Kubernetes Secret and configure the Gateway listener. "
                       "Forge has emitted a Terminate listener placeholder.",
            ))
            tls_listeners.append({
                "name": f"https-reencrypt-{route_name[:20]}",
                "port": 443,
                "protocol": "HTTPS",
                "hostname": host or "*",
                "tls": {
                    "mode": "Terminate",
                    "certificateRefs": [],  # placeholder — operator must fill
                },
                "allowedRoutes": {"namespaces": {"from": "All"}},
            })
        # Backend TLS (re-encrypt to the backend) → always unmapped
        unmapped.append(UnmappedEntry(
            source_kind="Route",
            source_name=route_name,
            source_namespace=route_ns,
            construct="backend_tls",
            detail="tls.termination=reencrypt (backend leg)",
            reason="Re-encrypt to the backend has no standard Gateway-API expression; "
                   "configure backend TLS via a BackendTLSPolicy or service-mesh sidecar.",
        ))

    # Build backendRefs — primary + alternateBackends (weighted)
    if primary_svc:
        backend_refs: list[dict[str, Any]] = []

        # alternateBackends weighted backendRefs
        alternate_backends = spec.get("alternateBackends") or []
        has_alternates = bool(alternate_backends)

        primary_ref: dict[str, Any] = {"name": primary_svc, "port": port_num}
        if has_alternates:
            primary_ref["weight"] = int(primary_weight)

        # Cross-namespace check for primary backend
        primary_ns = route_ns  # Route backends are in the same namespace in OpenShift
        if primary_ns != gw_namespace:
            unmapped.append(UnmappedEntry(
                source_kind="Route",
                source_name=route_name,
                source_namespace=route_ns,
                construct="cross_namespace_backend",
                detail=f"{primary_svc}.{primary_ns}",
                reason="Cross-namespace backend requires a ReferenceGrant.",
            ))
            primary_ref["namespace"] = primary_ns

        backend_refs.append(primary_ref)

        for alt in alternate_backends:
            alt_svc = alt.get("name")
            alt_weight = alt.get("weight")
            if not alt_svc:
                continue
            alt_ref: dict[str, Any] = {"name": alt_svc, "port": port_num}
            if alt_weight is not None:
                alt_ref["weight"] = int(alt_weight)
            backend_refs.append(alt_ref)

        rules.append({
            "matches": [{"path": {"type": "PathPrefix", "value": path}}],
            "backendRefs": backend_refs,
        })

    return rules, hostnames, tls_listeners, route_ns


def _ingresslink_to_bnk(
    il_dict: dict,
    *,
    gateway_name: str,
    target_namespace: str | None,
    unmapped: list[UnmappedEntry],
) -> tuple[list[dict], list[str], list[dict], str | None]:
    """Translate one CIS IngressLink dict to HTTPRoute rules + hostnames + TLS listeners.

    Returns (rules, hostnames, tls_listeners, namespace).

    An IngressLink fronts an in-cluster Ingress controller Service.  We can
    derive host and VIP but cannot resolve the selector→Service purely, so we
    emit a placeholder backendRef and unmapped entries for the selector + VIP.
    iRules/TLS/policy are mapped to unmapped (mirror VS catalog).
    """
    meta = il_dict.get("metadata") or {}
    spec = il_dict.get("spec") or {}
    il_name = meta.get("name", "unknown")
    il_ns = meta.get("namespace", "default")

    rules: list[dict] = []
    hostnames: list[str] = []
    tls_listeners: list[dict] = []

    # spec.host → hostname
    host = spec.get("host")
    if host:
        hostnames.append(host)

    # spec.virtualServerAddress → VIP (unmapped — re-IP step)
    vip = spec.get("virtualServerAddress")
    if vip:
        unmapped.append(UnmappedEntry(
            source_kind="IngressLink",
            source_name=il_name,
            source_namespace=il_ns,
            construct="vip_address",
            detail=f"virtualServerAddress={vip}",
            reason="BIG-IP VIP / IPAM address: the re-IP/DNS cutover step "
                   "replaces this endpoint. Update DNS to the new BNK Gateway address "
                   "after verification.",
        ))

    # spec.selector (matchLabels) — cannot resolve purely; emit placeholder + unmapped
    selector = spec.get("selector") or {}
    match_labels = selector.get("matchLabels") or {}
    selector_str = ",".join(f"{k}={v}" for k, v in sorted(match_labels.items()))
    unmapped.append(UnmappedEntry(
        source_kind="IngressLink",
        source_name=il_name,
        source_namespace=il_ns,
        construct="ingresslink_selector",
        detail=f"selector.matchLabels={selector_str or '(empty)'}",
        reason="IngressLink spec.selector selects the fronted nginx-ingress Service by "
               "label. The selector cannot be resolved to a Service name without a live "
               "cluster query. Replace REPLACE_WITH_SELECTED_SERVICE in the backendRef "
               "with the actual Service name matched by these labels.",
    ))

    # iRules → unmapped
    for irule in (spec.get("iRules") or spec.get("irules") or []):
        unmapped.append(UnmappedEntry(
            source_kind="IngressLink",
            source_name=il_name,
            source_namespace=il_ns,
            construct="irule",
            detail=str(irule),
            reason="iRules are BIG-IP proprietary and have no Gateway API equivalent. "
                   "Replicate the logic using HTTPRoute filters or ExtensionRef policies.",
        ))

    # TLS → unmapped
    tls_spec = spec.get("tls") or {}
    if tls_spec:
        unmapped.append(UnmappedEntry(
            source_kind="IngressLink",
            source_name=il_name,
            source_namespace=il_ns,
            construct="tls_profile_ref",
            detail=str(tls_spec),
            reason="IngressLink TLS configuration cannot be automatically mapped. "
                   "Provide a Kubernetes Secret and configure the Gateway listener certificateRefs.",
        ))

    # Policy/WAF → verified BNK kind (#268)
    for policy_field in ("policyName", "waf", "botDefense", "firewallPolicy", "policy"):
        val = spec.get(policy_field)
        if val:
            bnk_kind = _CIS_SECURITY_FIELD_TO_BNK_KIND.get(policy_field)
            if bnk_kind:
                api_group, api_version = _BNK_SECURITY_KIND_API[bnk_kind]
                unmapped.append(UnmappedEntry(
                    source_kind="IngressLink",
                    source_name=il_name,
                    source_namespace=il_ns,
                    construct="security_policy",
                    detail=f"{policy_field}={val}",
                    reason=(
                        f"Maps to BNK CRD kind '{bnk_kind}' "
                        f"(apiVersion: {api_group}/{api_version}). "
                        f"Create a {bnk_kind} CR and attach it to the target Gateway."
                    ),
                ))
            else:
                unmapped.append(UnmappedEntry(
                    source_kind="IngressLink",
                    source_name=il_name,
                    source_namespace=il_ns,
                    construct="security_policy",
                    detail=f"{policy_field}={val}",
                    reason="BIG-IP-native policy reference; verify the policy type and map to "
                           "the appropriate BNK CRD: BNKSecPolicy, F5BigFwPolicy, or BNKNetPolicy.",
                ))

    # Emit a placeholder rule — operator must replace the backendRef
    rules.append({
        "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
        "backendRefs": [{"name": "REPLACE_WITH_SELECTED_SERVICE", "port": 80}],
    })

    return rules, hostnames, tls_listeners, il_ns


def translate_cis_to_bnk(
    *,
    virtualservers: list[dict],
    transportservers: list[dict],
    policies: list[dict] | None = None,
    as3_declarations: list[dict] | None = None,
    ingresses: list[dict] | None = None,
    routes: list[dict] | None = None,
    ingresslinks: list[dict] | None = None,
    tlsprofiles: list[dict] | None = None,
    gateway_class_name: str = BNK_GATEWAY_CLASS_NAME,
    gateway_name: str | None = None,
    target_namespace: str | None = None,
) -> TranslationResult:
    """Translate CIS VirtualServer/TransportServer CRs and/or AS3 declarations
    and/or F5-annotated Ingresses to BNK Gateway API manifests.

    PURE function — no K8s I/O.  Callers must pre-fetch full CIS CR specs
    and pass them as plain dicts.

    Args:
        virtualservers: List of full CIS VirtualServer CR dicts (with spec).
        transportservers: List of full CIS TransportServer CR dicts (with spec).
        policies: List of full CIS Policy CR dicts (optional; every Policy ref
            routes to unmapped[] — exact BNK security CRD kind unverified, see ESCALATION).
        as3_declarations: List of AS3 declaration dicts, each with optional
            ``_source_kind``, ``_source_name``, ``_source_namespace`` metadata keys
            (added by the I/O caller). Each item is a parsed AS3 declaration dict.
        ingresses: List of F5-annotated Kubernetes Ingress dicts (plain dicts,
            as returned by ``_fetch_cis_f5_ingresses``).  Translated via the
            shared ``_ingress_to_httproute`` translator (REUSE — no duplicate
            Ingress translator).  F5-specific annotations are catalogued via the
            ``f5`` branch in ``_collect_unmapped_annotations``.
        routes: List of OpenShift Route dicts (plain dicts, as returned by
            ``_fetch_openshift_routes``). Translated via ``_route_to_bnk``.
            TLS termination: edge → HTTP + cert-unmapped; passthrough → unmapped;
            reencrypt → Terminate listener + backend_tls unmapped.
        gateway_class_name: Target BNK GatewayClass name.
        gateway_name: Override for the target Gateway name (auto-derived if omitted).
        target_namespace: Override for the Gateway namespace.

    Returns:
        TranslationResult with rendered YAML and any unmapped constructs.

    ESCALATION (D-023 P3 known gap):
        Policy / policyName / waf / botDefense / firewallPolicy references are
        routed to unmapped[] with reason "BIG-IP-native security policy; verify
        target BNK security CRD kind before mapping".  The exact BNK security
        CRD kind (WAFPolicy / SecurityPolicy / other) cannot be confirmed without
        access to live clouddocs — do NOT hard-code a mapping.
    """
    policies = policies or []
    as3_declarations = as3_declarations or []
    ingresses = ingresses or []
    routes = routes or []
    ingresslinks = ingresslinks or []
    tlsprofiles = tlsprofiles or []

    # Derive gateway_name from first VS / AS3 declaration / Ingress / Route / IngressLink if not provided
    if not gateway_name:
        if virtualservers:
            meta = (virtualservers[0].get("metadata") or {})
            vs_name = meta.get("name") or "cis-migrate"
        elif transportservers:
            meta = (transportservers[0].get("metadata") or {})
            vs_name = meta.get("name") or "cis-migrate"
        elif as3_declarations:
            src_name = as3_declarations[0].get("_source_name") or "cis-migrate"
            vs_name = src_name.split("/")[-1] if "/" in src_name else src_name
        elif ingresses:
            ing_meta = (ingresses[0].get("metadata") or {})
            vs_name = ing_meta.get("name") or "cis-migrate"
        elif routes:
            route_meta = (routes[0].get("metadata") or {})
            vs_name = route_meta.get("name") or "cis-migrate"
        elif ingresslinks:
            il_meta = (ingresslinks[0].get("metadata") or {})
            vs_name = il_meta.get("name") or "cis-migrate"
        else:
            vs_name = "cis-migrate"
        slug = re.sub(r"[^a-z0-9-]", "-", vs_name.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")[:30]
        gateway_name = f"{slug}-bnk-migrate"

    unmapped: list[UnmappedEntry] = []
    policy_names_by_ns: dict[str, str] = {}  # namespace -> policy_name for lookups

    # Collect Policy names so we can flag them precisely
    for pol in policies:
        pol_meta = (pol.get("metadata") or {})
        pol_name = pol_meta.get("name", "")
        pol_ns = pol_meta.get("namespace", "default")
        policy_names_by_ns[f"{pol_ns}/{pol_name}"] = pol_name

    # Build TLSProfile lookup by "namespace/name" for VS tlsProfileName resolution
    tlsprofile_by_key: dict[str, dict] = {}
    for tp in tlsprofiles:
        tp_meta = (tp.get("metadata") or {})
        tp_name = tp_meta.get("name") or ""
        tp_ns = tp_meta.get("namespace") or "default"
        if tp_name:
            tlsprofile_by_key[f"{tp_ns}/{tp_name}"] = tp
            # Also index by name alone (for same-namespace lookups)
            tlsprofile_by_key[tp_name] = tp

    # Build listeners + HTTPRoute from VirtualServers
    gw_namespace: str | None = target_namespace
    all_hostnames: list[str] = []
    all_rules: list[dict] = []
    tls_listeners: list[dict] = []

    for vs in virtualservers:
        vs_dict = _obj_to_dict(vs)
        httproute_rules, vs_hostnames, vs_tls, vs_ns = _virtualserver_to_httproute(
            vs_dict=vs_dict,
            gateway_name=gateway_name,
            target_namespace=target_namespace,
            unmapped=unmapped,
            tlsprofile_by_key=tlsprofile_by_key,
        )
        if gw_namespace is None and vs_ns:
            gw_namespace = vs_ns

        for h in vs_hostnames:
            if h and h not in all_hostnames:
                all_hostnames.append(h)

        all_rules.extend(httproute_rules)
        tls_listeners.extend(vs_tls)

    # Fold AS3 declarations into the same accumulators
    as3_declaration_count = len(as3_declarations)
    for decl in as3_declarations:
        # Extract source metadata (added by I/O caller; strip before parsing)
        src_kind = decl.get("_source_kind") or "AS3ConfigMap"
        src_name = decl.get("_source_name") or "(unknown)"
        src_ns = decl.get("_source_namespace") or "default"

        # Build a clean copy without our metadata keys
        clean_decl = {k: v for k, v in decl.items() if not k.startswith("_")}

        as3_rules, as3_hostnames, as3_tls = _as3_declaration_to_bnk(
            clean_decl,
            source_kind=src_kind,
            source_name=src_name,
            source_namespace=src_ns,
            unmapped=unmapped,
        )
        all_rules.extend(as3_rules)
        for h in as3_hostnames:
            if h and h not in all_hostnames:
                all_hostnames.append(h)
        tls_listeners.extend(as3_tls)

    # Fold F5-annotated Ingresses via the shared Ingress→HTTPRoute translator (REUSE).
    # proxy_type="cis-bigip" triggers the f5 branch in _collect_unmapped_annotations.
    ingress_count = len(ingresses)
    if ingresses:
        ing_httproute_dict, ing_listeners, ing_gw_ns = _ingress_to_httproute(
            proxy_type="cis-bigip",
            source_ingresses=ingresses,
            gateway_name=gateway_name,
            target_namespace=target_namespace,
            unmapped=unmapped,
        )
        # Extract rules and hostnames from the returned httproute dict
        ing_spec = ing_httproute_dict.get("spec", {}) or {}
        for rule in (ing_spec.get("rules") or []):
            all_rules.append(rule)
        for h in (ing_spec.get("hostnames") or []):
            if h and h not in all_hostnames:
                all_hostnames.append(h)
        tls_listeners.extend(ing_listeners)
        if gw_namespace is None and ing_gw_ns:
            gw_namespace = ing_gw_ns
    else:
        ingress_count = 0

    # Fold OpenShift Routes via the _route_to_bnk front-half.
    route_count = len(routes)
    for route in routes:
        route_dict = _obj_to_dict(route)
        rt_rules, rt_hostnames, rt_tls, rt_ns = _route_to_bnk(
            route_dict=route_dict,
            gateway_name=gateway_name,
            target_namespace=target_namespace,
            unmapped=unmapped,
        )
        if gw_namespace is None and rt_ns:
            gw_namespace = rt_ns
        all_rules.extend(rt_rules)
        for h in rt_hostnames:
            if h and h not in all_hostnames:
                all_hostnames.append(h)
        tls_listeners.extend(rt_tls)

    # Fold CIS IngressLinks via _ingresslink_to_bnk.
    ingresslink_count = len(ingresslinks)
    for il in ingresslinks:
        il_dict = _obj_to_dict(il)
        il_rules, il_hostnames, il_tls, il_ns = _ingresslink_to_bnk(
            il_dict,
            gateway_name=gateway_name,
            target_namespace=target_namespace,
            unmapped=unmapped,
        )
        if gw_namespace is None and il_ns:
            gw_namespace = il_ns
        all_rules.extend(il_rules)
        for h in il_hostnames:
            if h and h not in all_hostnames:
                all_hostnames.append(h)
        tls_listeners.extend(il_tls)

    if not all_rules:
        all_rules = [{
            "matches": [{"path": {"type": "PathPrefix", "value": "/"}}],
            "backendRefs": [{"name": "REPLACE_WITH_SERVICE_NAME", "port": 80}],
        }]
        unmapped.append(UnmappedEntry(
            source_kind="VirtualServer",
            source_name="(none)",
            source_namespace="(none)",
            construct="no_rules",
            detail="No VirtualServer rules found",
            reason="No matching CIS VirtualServer objects found. "
                   "Replace the placeholder backendRef before applying.",
        ))

    gw_namespace = gw_namespace or "default"
    listeners = _build_listeners(all_hostnames, tls_listeners)

    # Build the Gateway API manifests
    gatewayclass_dict = _build_gatewayclass(gateway_class_name)
    gateway_dict = _build_gateway(gateway_name, gw_namespace, listeners, gateway_class_name)
    httproute_dict = _build_httproute(
        name=gateway_name,
        namespace=gw_namespace,
        gateway_name=gateway_name,
        gateway_namespace=gw_namespace,
        hostnames=all_hostnames,
        rules=all_rules,
    )

    # Build TCPRoute/UDPRoute manifests for TransportServers
    tcp_udp_yamls: list[str] = []
    for ts in transportservers:
        ts_dict = _obj_to_dict(ts)
        route_dict, route_unmapped = _transportserver_to_route(
            ts_dict=ts_dict,
            gateway_name=gateway_name,
            gateway_namespace=gw_namespace,
        )
        unmapped.extend(route_unmapped)
        if route_dict:
            tcp_udp_yamls.append(yaml.safe_dump(route_dict, default_flow_style=False))

    # Render YAML
    gatewayclass_yaml = yaml.safe_dump(gatewayclass_dict, default_flow_style=False)
    gateway_yaml = yaml.safe_dump(gateway_dict, default_flow_style=False)
    httproute_yaml = yaml.safe_dump(httproute_dict, default_flow_style=False)

    parts = [gatewayclass_yaml, gateway_yaml, httproute_yaml] + tcp_udp_yamls
    combined_yaml = "\n---\n".join(parts)

    # Determine dominant source_kind for response metadata (honest multi-source)
    source_types: list[str] = []
    if virtualservers:
        source_types.append("VirtualServer")
    if as3_declaration_count > 0:
        source_types.append("AS3ConfigMap")
    if ingress_count > 0:
        source_types.append("Ingress")
    if route_count > 0:
        source_types.append("Route")
    if ingresslink_count > 0:
        source_types.append("IngressLink")
    if len(source_types) > 1:
        result_source_kind = "Mixed"
    elif source_types:
        result_source_kind = source_types[0]
    else:
        result_source_kind = "VirtualServer"

    return TranslationResult(
        gatewayclass_yaml=gatewayclass_yaml,
        gateway_yaml=gateway_yaml,
        httproute_yaml=httproute_yaml,
        combined_yaml=combined_yaml,
        unmapped=unmapped,
        source={
            "proxy_type": "cis-bigip",
            "source_kind": result_source_kind,
            "virtualserver_count": len(virtualservers),
            "transportserver_count": len(transportservers),
            "policy_count": len(policies),
            "as3_declaration_count": as3_declaration_count,
            "ingress_count": ingress_count,
            "route_count": route_count,
            "ingresslink_count": ingresslink_count,
            "gateway_name": gateway_name,
            "gateway_namespace": gw_namespace,
        },
    )


# ---------------------------------------------------------------------------
# VirtualServer front-half
# ---------------------------------------------------------------------------


def _virtualserver_to_httproute(
    *,
    vs_dict: dict,
    gateway_name: str,
    target_namespace: str | None,
    unmapped: list[UnmappedEntry],
    tlsprofile_by_key: dict[str, dict] | None = None,
) -> tuple[list[dict], list[str], list[dict], str | None]:
    """Translate one CIS VirtualServer dict to HTTPRoute rules + listeners.

    Returns (rules, hostnames, tls_listeners, namespace).

    TLSProfile resolution (when tlsprofile_by_key is provided):
      - spec.tls.tlsProfileName → look up by name in tlsprofile_by_key
      - TLSProfile spec.tls.reference=="secret" + clientSSL present → HTTPS listener
      - TLSProfile spec.tls.reference=="bigip" → tls_profile_ref unmapped
      - TLSProfile not found → tls_profile_ref unmapped
    """
    meta = vs_dict.get("metadata") or {}
    spec = vs_dict.get("spec") or {}
    vs_name = meta.get("name", "unknown")
    vs_ns = meta.get("namespace", "default")

    rules: list[dict] = []
    hostnames: list[str] = []
    tls_listeners: list[dict] = []

    # spec.host → hostname
    host = spec.get("host")
    if host:
        hostnames.append(host)

    # Always-lossy constructs → unmapped (D-017)
    _collect_vs_unmapped(vs_name, vs_ns, spec, unmapped)

    # spec.pools[] → HTTPRoute rules
    gw_namespace = target_namespace or vs_ns

    for pool in (spec.get("pools") or []):
        service = pool.get("service") or pool.get("serviceName")
        service_port = pool.get("servicePort") or pool.get("port")
        path = pool.get("path") or "/"

        if not service:
            continue

        # servicePort-by-name → port_by_name unmapped + placeholder (mirrors P2)
        port_num: int
        if isinstance(service_port, str):
            unmapped.append(UnmappedEntry(
                source_kind="VirtualServer",
                source_name=vs_name,
                source_namespace=vs_ns,
                construct="port_by_name",
                detail=str(service_port),
                reason="Service port is referenced by name. HTTPRoute requires a "
                       "numeric port. Resolve the port number from the Service "
                       "spec.ports and update the backendRef accordingly.",
            ))
            port_num = 80  # placeholder
        else:
            port_num = int(service_port) if service_port else 80

        # Pool namespace (cross-namespace check)
        pool_ns = pool.get("namespace") or vs_ns
        if pool_ns != gw_namespace:
            unmapped.append(UnmappedEntry(
                source_kind="VirtualServer",
                source_name=vs_name,
                source_namespace=vs_ns,
                construct="cross_namespace_backend",
                detail=f"{service}.{pool_ns}",
                reason="Cross-namespace backend requires a ReferenceGrant. "
                       "HTTPRoute is placed in the first backend's namespace; "
                       f"this backend lives in '{pool_ns}'.",
            ))

        backend_ref: dict[str, Any] = {"name": service, "port": port_num}
        if pool_ns != gw_namespace:
            backend_ref["namespace"] = pool_ns

        rules.append({
            "matches": [{"path": {"type": "PathPrefix", "value": path}}],
            "backendRefs": [backend_ref],
        })

    # spec.tls → HTTPS listener
    tls_spec = spec.get("tls") or {}
    if tls_spec:
        tls_profile = tls_spec.get("clientSSL")  # k8s Secret name (direct)
        tls_profile_ref = tls_spec.get("clientSSLProfiles") or tls_spec.get("clientSSLProfile")
        tls_profile_name = tls_spec.get("tlsProfileName")  # CIS TLSProfile CR name

        # --- clientSSL (frontend TLS) ---
        if tls_profile:
            # k8s Secret → HTTPS listener (mappable)
            tls_listeners.append({
                "name": f"https-{tls_profile[:20]}",
                "port": 443,
                "protocol": "HTTPS",
                "hostname": host or "*",
                "tls": {
                    "mode": "Terminate",
                    "certificateRefs": [{
                        "kind": "Secret",
                        "name": tls_profile,
                        "namespace": vs_ns,
                    }],
                },
                "allowedRoutes": {"namespaces": {"from": "All"}},
            })
        elif tls_profile_name:
            # TLSProfile CR reference — resolve if tlsprofile_by_key was provided
            _resolve_tlsprofile(
                profile_name=tls_profile_name,
                vs_name=vs_name,
                vs_ns=vs_ns,
                host=host,
                tlsprofile_by_key=tlsprofile_by_key or {},
                tls_listeners=tls_listeners,
                unmapped=unmapped,
            )
        elif tls_profile_ref:
            # BIG-IP-native TLS profile name only → unmapped (D-017)
            profile_str = (
                tls_profile_ref if isinstance(tls_profile_ref, str)
                else str(tls_profile_ref)
            )
            unmapped.append(UnmappedEntry(
                source_kind="VirtualServer",
                source_name=vs_name,
                source_namespace=vs_ns,
                construct="tls_profile_ref",
                detail=profile_str,
                reason="BIG-IP-native TLS profile by name cannot be automatically "
                       "mapped. Provide a Kubernetes Secret with the certificate "
                       "and update the Gateway listener certificateRefs.",
            ))
        else:
            # Non-empty TLS spec but no recognized key → flag
            unmapped.append(UnmappedEntry(
                source_kind="VirtualServer",
                source_name=vs_name,
                source_namespace=vs_ns,
                construct="tls_profile_ref",
                detail=str(tls_spec),
                reason="BIG-IP-native TLS configuration cannot be automatically "
                       "mapped. Provide a Kubernetes Secret and update the Gateway "
                       "listener certificateRefs.",
            ))

        # --- serverSSL / serverSSLProfiles (backend mTLS) — checked independently (D-017) ---
        # A VS with BOTH clientSSL and serverSSL must surface serverSSL as unmapped.
        # The clientSSL branch above handles the frontend listener; this is an orthogonal check.
        server_ssl = tls_spec.get("serverSSL")
        server_ssl_profiles = tls_spec.get("serverSSLProfiles")
        backend_ssl = server_ssl or server_ssl_profiles
        if backend_ssl:
            profile_str = backend_ssl if isinstance(backend_ssl, str) else str(backend_ssl)
            unmapped.append(UnmappedEntry(
                source_kind="VirtualServer",
                source_name=vs_name,
                source_namespace=vs_ns,
                construct="server_ssl_profile",
                detail=profile_str,
                reason="BIG-IP serverSSL / serverSSLProfiles (backend mTLS) has no direct "
                       "Gateway API equivalent. Configure backend TLS via BackendTLSPolicy "
                       "or a service mesh sidecar.",
            ))

    return rules, hostnames, tls_listeners, vs_ns


def _resolve_tlsprofile(
    *,
    profile_name: str,
    vs_name: str,
    vs_ns: str,
    host: str | None,
    tlsprofile_by_key: dict[str, dict],
    tls_listeners: list[dict],
    unmapped: list[UnmappedEntry],
) -> None:
    """Resolve a CIS TLSProfile CR name to an HTTPS listener or unmapped entry.

    Resolution rules:
      - CR not found → unmapped ("referenced TLSProfile '<name>' not found in cluster scan")
      - reference==bigip (native profile) → unmapped (BIG-IP-native SSL profile)
      - reference==secret + Secret name present → HTTPS Terminate listener (mappable)
      - reference==secret but Secret name missing → unmapped
    """
    tp = tlsprofile_by_key.get(profile_name)
    if tp is None:
        unmapped.append(UnmappedEntry(
            source_kind="VirtualServer",
            source_name=vs_name,
            source_namespace=vs_ns,
            construct="tls_profile_ref",
            detail=f"tlsProfileName={profile_name!r}",
            reason=f"Referenced TLSProfile '{profile_name}' not found in cluster scan. "
                   "Ensure the TLSProfile CR exists and re-scan, or provide a Kubernetes "
                   "Secret directly via spec.tls.clientSSL.",
        ))
        return

    tp_spec = (tp.get("spec") or {}).get("tls") or {}
    reference = (tp_spec.get("reference") or "").lower()

    if reference == "bigip":
        # Native BIG-IP SSL profile — no K8s Secret
        client_ssl = tp_spec.get("clientSSL") or tp_spec.get("serverSSL") or profile_name
        unmapped.append(UnmappedEntry(
            source_kind="VirtualServer",
            source_name=vs_name,
            source_namespace=vs_ns,
            construct="tls_profile_ref",
            detail=f"tlsProfileName={profile_name!r} reference=bigip clientSSL={client_ssl!r}",
            reason="TLSProfile references a BIG-IP-native SSL profile; provide a Kubernetes "
                   "Secret with the certificate and configure it on the Gateway listener "
                   "certificateRefs.",
        ))
        return

    # reference == "secret" (or unset/other — default to secret path)
    secret_name = tp_spec.get("clientSSL") or tp_spec.get("serverSSL")
    if not secret_name:
        unmapped.append(UnmappedEntry(
            source_kind="VirtualServer",
            source_name=vs_name,
            source_namespace=vs_ns,
            construct="tls_profile_ref",
            detail=f"tlsProfileName={profile_name!r} reference=secret (no Secret name)",
            reason="TLSProfile has reference=secret but no clientSSL/serverSSL Secret name. "
                   "Update the TLSProfile CR to set spec.tls.clientSSL to a Kubernetes Secret name.",
        ))
        return

    # Mappable: emit HTTPS Terminate listener using the Secret
    tls_listeners.append({
        "name": f"https-{secret_name[:20]}",
        "port": 443,
        "protocol": "HTTPS",
        "hostname": host or "*",
        "tls": {
            "mode": "Terminate",
            "certificateRefs": [{
                "kind": "Secret",
                "name": secret_name,
                "namespace": vs_ns,
            }],
        },
        "allowedRoutes": {"namespaces": {"from": "All"}},
    })


def _collect_vs_unmapped(
    vs_name: str,
    vs_ns: str,
    spec: dict,
    unmapped: list[UnmappedEntry],
) -> None:
    """Flag all always-lossy CIS constructs as unmapped (D-017)."""
    # iRules
    for irule in (spec.get("iRules") or spec.get("irules") or []):
        unmapped.append(UnmappedEntry(
            source_kind="VirtualServer",
            source_name=vs_name,
            source_namespace=vs_ns,
            construct="irule",
            detail=str(irule),
            reason="iRules are BIG-IP proprietary and have no Gateway API equivalent. "
                   "Replicate the logic using HTTPRoute filters, ExtensionRef policies, "
                   "or application-layer middleware.",
        ))

    # GTM / ExternalDNS / hostGroup / wide-IP
    for gtm_field in ("gTMProfile", "hostGroup", "externalDNS", "wideIP"):
        val = spec.get(gtm_field)
        if val:
            unmapped.append(UnmappedEntry(
                source_kind="VirtualServer",
                source_name=vs_name,
                source_namespace=vs_ns,
                construct="gtm_externaldns",
                detail=f"{gtm_field}={val}",
                reason="GTM / ExternalDNS / hostGroup wide-IP configuration has no "
                       "direct Gateway API equivalent. Configure DNS separately.",
            ))

    # Route-domain / partition (spec.partition or addresses like '%N')
    partition = spec.get("partition")
    if partition:
        unmapped.append(UnmappedEntry(
            source_kind="VirtualServer",
            source_name=vs_name,
            source_namespace=vs_ns,
            construct="route_domain_partition",
            detail=f"partition={partition}",
            reason="BIG-IP route-domain / partition configuration is not applicable "
                   "to in-cluster Gateway API. The migration moves the data path "
                   "into the Kubernetes cluster; manual BIG-IP partition cleanup required.",
        ))

    # SNAT
    snat = spec.get("snat")
    if snat:
        unmapped.append(UnmappedEntry(
            source_kind="VirtualServer",
            source_name=vs_name,
            source_namespace=vs_ns,
            construct="snat",
            detail=str(snat),
            reason="SNAT configuration has no direct Gateway API equivalent. "
                   "Configure source NAT at the network/node level.",
        ))

    # IPAM / VIP address (the VIP the re-IP step replaces)
    vip_fields = ("virtualServerAddress", "ipamLabel", "virtualServerIP")
    for f in vip_fields:
        val = spec.get(f)
        if val:
            unmapped.append(UnmappedEntry(
                source_kind="VirtualServer",
                source_name=vs_name,
                source_namespace=vs_ns,
                construct="vip_address",
                detail=f"{f}={val}",
                reason="BIG-IP VIP / IPAM address: the re-IP/DNS cutover step "
                       "replaces this endpoint. The operator must update DNS to "
                       "point to the new BNK Gateway address after verification.",
            ))

    # Monitor / persistence / profileMultiplex
    for construct_key, construct_name in [
        ("healthMonitor", "monitor"),
        ("rewriteAppRoot", "rewrite_app_root"),
        ("allowSourceRange", "allow_source_range"),
        ("profileMultiplex", "profile_multiplex"),
        ("persistenceProfile", "persistence"),
        ("cookieRewriteRules", "cookie_rewrite"),
    ]:
        val = spec.get(construct_key)
        if val:
            unmapped.append(UnmappedEntry(
                source_kind="VirtualServer",
                source_name=vs_name,
                source_namespace=vs_ns,
                construct=construct_name,
                detail=f"{construct_key}={val}",
                reason=f"BIG-IP-native {construct_name} configuration has no direct "
                       "Gateway API equivalent. Review and implement via HTTPRoute "
                       "filters, ExtensionRef policies, or sidecar.",
            ))

    # Policy / policyName / waf / botDefense / firewallPolicy — verified BNK kind (#268)
    for policy_field in ("policyName", "waf", "botDefense", "firewallPolicy", "policy"):
        val = spec.get(policy_field)
        if val:
            bnk_kind = _CIS_SECURITY_FIELD_TO_BNK_KIND.get(policy_field)
            if bnk_kind:
                api_group, api_version = _BNK_SECURITY_KIND_API[bnk_kind]
                unmapped.append(UnmappedEntry(
                    source_kind="VirtualServer",
                    source_name=vs_name,
                    source_namespace=vs_ns,
                    construct="security_policy",
                    detail=f"{policy_field}={val}",
                    reason=(
                        f"Maps to BNK CRD kind '{bnk_kind}' "
                        f"(apiVersion: {api_group}/{api_version}). "
                        f"Create a {bnk_kind} CR referencing this policy "
                        f"and attach it to the target Gateway or HTTPRoute."
                    ),
                ))
            else:
                # policyName / policy — generic policy ref; no single BNK kind maps to all cases
                unmapped.append(UnmappedEntry(
                    source_kind="VirtualServer",
                    source_name=vs_name,
                    source_namespace=vs_ns,
                    construct="security_policy",
                    detail=f"{policy_field}={val}",
                    reason="BIG-IP-native policy reference; verify the policy type and map to "
                           "the appropriate BNK CRD: BNKSecPolicy (WAF/Bot, "
                           "gateway.k8s.f5net.com), F5BigFwPolicy (network firewall, "
                           "k8s.f5net.com), or BNKNetPolicy (iRules/TCPSettings).",
                ))

    # Check pools[].monitor
    for pool in (spec.get("pools") or []):
        monitor = pool.get("monitor") or pool.get("healthMonitor")
        if monitor:
            pool_svc = pool.get("service") or pool.get("serviceName") or "(unknown)"
            unmapped.append(UnmappedEntry(
                source_kind="VirtualServer",
                source_name=vs_name,
                source_namespace=vs_ns,
                construct="monitor",
                detail=f"pool.service={pool_svc} monitor={monitor}",
                reason="BIG-IP pool health monitor has no direct Gateway API equivalent. "
                       "Configure readiness probes on the backend Service/Pod.",
            ))


# ---------------------------------------------------------------------------
# TransportServer front-half
# ---------------------------------------------------------------------------


def _transportserver_to_route(
    *,
    ts_dict: dict,
    gateway_name: str,
    gateway_namespace: str,
) -> tuple[dict | None, list[UnmappedEntry]]:
    """Translate one CIS TransportServer dict to a TCPRoute or UDPRoute dict.

    Returns (route_dict, unmapped_list).  route_dict is None if the TS cannot
    produce a valid route (the unmapped list will explain why).

    NOTE: TCPRoute/UDPRoute use apiVersion gateway.networking.k8s.io/v1alpha2
    (experimental-channel).  This is emitted as-is; the verify gate will fail
    closed if the CRD is not installed on the target cluster.
    """
    meta = ts_dict.get("metadata") or {}
    spec = ts_dict.get("spec") or {}
    ts_name = meta.get("name", "unknown")
    ts_ns = meta.get("namespace", "default")

    unmapped: list[UnmappedEntry] = []

    # Determine route kind from spec.type
    route_type = (spec.get("type") or "tcp").lower()
    if route_type not in ("tcp", "udp"):
        unmapped.append(UnmappedEntry(
            source_kind="TransportServer",
            source_name=ts_name,
            source_namespace=ts_ns,
            construct="transport_type",
            detail=f"type={route_type}",
            reason=f"Unknown TransportServer type '{route_type}'. Only 'tcp' and 'udp' "
                   "are supported. This TransportServer is skipped.",
        ))
        return None, unmapped

    route_kind = "TCPRoute" if route_type == "tcp" else "UDPRoute"

    # Pool → backendRef
    pool = spec.get("pool") or {}
    service = pool.get("service") or pool.get("serviceName")
    service_port = pool.get("servicePort") or pool.get("port")

    if not service:
        unmapped.append(UnmappedEntry(
            source_kind="TransportServer",
            source_name=ts_name,
            source_namespace=ts_ns,
            construct="no_pool_service",
            detail=str(pool),
            reason="TransportServer pool has no service name. Cannot generate a backendRef.",
        ))
        return None, unmapped

    # servicePort-by-name → port_by_name unmapped + placeholder
    port_num: int
    if isinstance(service_port, str) and not service_port.isdigit():
        unmapped.append(UnmappedEntry(
            source_kind="TransportServer",
            source_name=ts_name,
            source_namespace=ts_ns,
            construct="port_by_name",
            detail=str(service_port),
            reason="Service port is referenced by name. TCPRoute/UDPRoute requires a "
                   "numeric port. Resolve from the Service spec.ports.",
        ))
        port_num = 80  # placeholder
    else:
        port_num = int(service_port) if service_port else 80

    # Virtual server port (listener port)
    vs_port = spec.get("virtualServerPort") or spec.get("port") or port_num

    # Always-lossy constructs → unmapped
    for irule in (spec.get("iRules") or spec.get("irules") or []):
        unmapped.append(UnmappedEntry(
            source_kind="TransportServer",
            source_name=ts_name,
            source_namespace=ts_ns,
            construct="irule",
            detail=str(irule),
            reason="iRules are BIG-IP proprietary and have no Gateway API equivalent.",
        ))

    for policy_field in ("policyName", "waf", "botDefense", "firewallPolicy", "policy"):
        val = spec.get(policy_field)
        if val:
            bnk_kind = _CIS_SECURITY_FIELD_TO_BNK_KIND.get(policy_field)
            if bnk_kind:
                api_group, api_version = _BNK_SECURITY_KIND_API[bnk_kind]
                unmapped.append(UnmappedEntry(
                    source_kind="TransportServer",
                    source_name=ts_name,
                    source_namespace=ts_ns,
                    construct="security_policy",
                    detail=f"{policy_field}={val}",
                    reason=(
                        f"Maps to BNK CRD kind '{bnk_kind}' "
                        f"(apiVersion: {api_group}/{api_version}). "
                        f"Create a {bnk_kind} CR and attach it to the target TCPRoute."
                    ),
                ))
            else:
                unmapped.append(UnmappedEntry(
                    source_kind="TransportServer",
                    source_name=ts_name,
                    source_namespace=ts_ns,
                    construct="security_policy",
                    detail=f"{policy_field}={val}",
                    reason="BIG-IP-native policy reference; verify the policy type and map to "
                           "the appropriate BNK CRD: BNKSecPolicy, F5BigFwPolicy, or BNKNetPolicy.",
                ))

    vip = spec.get("virtualServerAddress") or spec.get("ipamLabel")
    if vip:
        unmapped.append(UnmappedEntry(
            source_kind="TransportServer",
            source_name=ts_name,
            source_namespace=ts_ns,
            construct="vip_address",
            detail=str(vip),
            reason="BIG-IP VIP / IPAM address: the re-IP/DNS cutover step replaces "
                   "this endpoint.",
        ))

    # Build route dict
    route_dict = _build_tcpudp_route(
        kind=route_kind,
        name=ts_name,
        namespace=ts_ns,
        gateway_name=gateway_name,
        gateway_namespace=gateway_namespace,
        backend_service=service,
        backend_port=port_num,
        vs_port=int(vs_port),
    )

    return route_dict, unmapped


# ---------------------------------------------------------------------------
# TCPRoute / UDPRoute builders (mirror _build_httproute, v1alpha2)
# ---------------------------------------------------------------------------


def _build_tcpudp_route(
    *,
    kind: str,
    name: str,
    namespace: str,
    gateway_name: str,
    gateway_namespace: str,
    backend_service: str,
    backend_port: int,
    vs_port: int,
) -> dict:
    """Build a TCPRoute or UDPRoute dict.

    Uses gateway.networking.k8s.io/v1alpha2 (experimental-channel).
    ESCALATION NOTE: this apiVersion may not be installed on the target
    cluster — emit + flag; the verify gate (fail-closed) will catch an
    unprogrammed route.
    """
    return {
        "apiVersion": _GATEWAY_API_ALPHA_VERSION,
        "kind": kind,
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {FORGE_MANAGED_LABEL: FORGE_LABEL_VALUE},
            "annotations": {
                "bnk-forge/escalation": (
                    f"{kind} uses gateway.networking.k8s.io/v1alpha2 (experimental). "
                    "Verify the CRD is installed before applying."
                ),
            },
        },
        "spec": {
            "parentRefs": [{
                "name": gateway_name,
                "namespace": gateway_namespace,
                "sectionName": f"port-{vs_port}",
            }],
            "rules": [{
                "backendRefs": [{
                    "name": backend_service,
                    "port": backend_port,
                }],
            }],
        },
    }
