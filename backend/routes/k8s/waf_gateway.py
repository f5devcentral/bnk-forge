"""
WAF Gateway API routes — manage Gateway API resources for WAF attachment.

Covers the full binding chain:
  GatewayClass → Gateway → F5BigWebSecurityProfile → APPolicy
                         ↳ HTTPRoute (traffic rules)
                         ↳ ReferenceGrant (cross-namespace permissions)

All resources are managed directly via KubernetesService.
"""

import logging
from typing import Any

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import NotFoundError, handle_route_errors
from database import get_db
from routes.auth import require_cluster_owner, require_viewer
from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-waf-gateway"])

WSP_API_VERSION   = "k8s.f5net.com/v1"
SECPOLICY_API_VERSION = "gateway.k8s.f5.com/v1alpha1"
GATEWAY_API_VERSION   = "gateway.networking.k8s.io/v1"
REFGRANT_API_VERSION  = "gateway.networking.k8s.io/v1beta1"
GATEWAYCLASS_API_VERSION = "gateway.networking.k8s.io/v1"

# Annotation key used to bind an F5BigWebSecurityProfile to a Gateway
WSP_ANNOTATION = "k8s.f5net.com/web-security-profile"


# ──────────────────────────────────────────────────────────────────────────────
# Request models
# ──────────────────────────────────────────────────────────────────────────────

class GatewayClassCreateRequest(BaseModel):
    name: str
    controller_name: str = "f5.com/default-f5-cne-controller"
    description: str = "F5 BIG-IP Kubernetes Gateway"


class ListenerModel(BaseModel):
    name: str
    protocol: str = "HTTP"
    port: int = 80
    allowed_routes_from: str = "Same"   # Same | All | Selector
    allowed_routes_selector: dict | None = None
    tls_mode: str | None = None          # Terminate | Passthrough
    tls_cert_ref_name: str | None = None
    tls_cert_ref_namespace: str | None = None


class GatewayCreateRequest(BaseModel):
    name: str
    namespace: str = "default"
    gateway_class_name: str = "f5-gatewayclass"
    listeners: list[ListenerModel]
    addresses: list[str] = []           # IP address strings
    # WAF binding — optional at creation (can add WSP binding separately)
    waf_profile_name: str | None = None  # F5BigWebSecurityProfile name (same namespace)
    annotations: dict[str, str] = {}


class GatewayUpdateRequest(BaseModel):
    namespace: str = "default"
    listeners: list[ListenerModel]
    addresses: list[str] = []
    waf_profile_name: str | None = None
    annotations: dict[str, str] = {}


class WafSecurityProfileCreateRequest(BaseModel):
    """Create an F5BigWebSecurityProfile bridging a named APPolicy to Gateway API."""
    name: str
    namespace: str = "default"
    policy_name: str               # APPolicy name (resolved by WAF enforcer — any namespace)


class WafSecurityProfileUpdateRequest(BaseModel):
    namespace: str = "default"
    policy_name: str


class BackendRefModel(BaseModel):
    name: str
    port: int
    namespace: str | None = None
    weight: int = 1


class RouteMatchModel(BaseModel):
    path_type: str = "PathPrefix"   # Exact | PathPrefix | RegularExpression
    path_value: str = "/"
    headers: list[dict] = []       # {name, value, type?}
    query_params: list[dict] = []


class HTTPRouteRuleModel(BaseModel):
    matches: list[RouteMatchModel] = []
    backend_refs: list[BackendRefModel]
    filters: list[dict] = []


class HTTPRouteCreateRequest(BaseModel):
    name: str
    namespace: str = "default"
    parent_gateway_name: str
    parent_gateway_namespace: str | None = None  # defaults to same namespace as route
    parent_gateway_section_name: str | None = None
    hostnames: list[str] = []
    rules: list[HTTPRouteRuleModel]


class HTTPRouteUpdateRequest(BaseModel):
    namespace: str = "default"
    parent_gateway_name: str
    parent_gateway_namespace: str | None = None
    parent_gateway_section_name: str | None = None
    hostnames: list[str] = []
    rules: list[HTTPRouteRuleModel]


class ReferenceGrantCreateRequest(BaseModel):
    """Allow cross-namespace references (e.g. HTTPRoute in ns-A referencing Service in ns-B)."""
    name: str
    namespace: str    # namespace WHERE the referenced resource lives
    from_group: str = "gateway.networking.k8s.io"
    from_kind: str = "HTTPRoute"
    from_namespace: str   # namespace WHERE the referring resource lives
    to_group: str = ""
    to_kind: str = "Service"
    to_name: str | None = None  # None = any resource of that kind


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _find_by_name(resources: list[dict], name: str) -> dict | None:
    for r in resources:
        if r.get("metadata", {}).get("name") == name:
            return r
    return None


def _build_listener(l: ListenerModel) -> dict:
    listener: dict[str, Any] = {
        "name": l.name,
        "protocol": l.protocol,
        "port": l.port,
    }
    # allowedRoutes
    if l.allowed_routes_from == "All":
        listener["allowedRoutes"] = {"namespaces": {"from": "All"}}
    elif l.allowed_routes_from == "Selector" and l.allowed_routes_selector:
        listener["allowedRoutes"] = {"namespaces": {"from": "Selector", "selector": l.allowed_routes_selector}}
    else:
        listener["allowedRoutes"] = {"namespaces": {"from": "Same"}}
    # TLS
    if l.tls_mode and l.tls_cert_ref_name:
        tls: dict[str, Any] = {"mode": l.tls_mode}
        if l.tls_cert_ref_name:
            cert_ref: dict[str, Any] = {"name": l.tls_cert_ref_name, "kind": "Secret"}
            if l.tls_cert_ref_namespace:
                cert_ref["namespace"] = l.tls_cert_ref_namespace
            tls["certificateRefs"] = [cert_ref]
        listener["tls"] = tls
    return listener


def _build_gateway_dict(
    name: str,
    namespace: str,
    gateway_class_name: str,
    listeners: list[ListenerModel],
    addresses: list[str],
    waf_profile_name: str | None,
    annotations: dict[str, str],
    resource_version: str | None = None,
) -> dict:
    metadata: dict[str, Any] = {"name": name, "namespace": namespace}
    if resource_version:
        metadata["resourceVersion"] = resource_version
    all_annotations = dict(annotations)
    if waf_profile_name:
        all_annotations[WSP_ANNOTATION] = waf_profile_name
    if all_annotations:
        metadata["annotations"] = all_annotations

    spec: dict[str, Any] = {
        "gatewayClassName": gateway_class_name,
        "listeners": [_build_listener(l) for l in listeners],
    }
    if addresses:
        spec["addresses"] = [{"type": "IPAddress", "value": a} for a in addresses]

    return {"apiVersion": GATEWAY_API_VERSION, "kind": "Gateway", "metadata": metadata, "spec": spec}


def _build_route_rule(rule: HTTPRouteRuleModel) -> dict:
    r: dict[str, Any] = {}
    # matches
    if rule.matches:
        r["matches"] = []
        for m in rule.matches:
            match: dict[str, Any] = {"path": {"type": m.path_type, "value": m.path_value}}
            if m.headers:
                match["headers"] = m.headers
            if m.query_params:
                match["queryParams"] = m.query_params
            r["matches"].append(match)
    # backendRefs
    r["backendRefs"] = []
    for b in rule.backend_refs:
        ref: dict[str, Any] = {"name": b.name, "port": b.port, "weight": b.weight}
        if b.namespace:
            ref["namespace"] = b.namespace
        r["backendRefs"].append(ref)
    if rule.filters:
        r["filters"] = rule.filters
    return r


def _build_httproute_dict(
    name: str,
    namespace: str,
    parent_gateway_name: str,
    parent_gateway_namespace: str | None,
    parent_gateway_section_name: str | None,
    hostnames: list[str],
    rules: list[HTTPRouteRuleModel],
    resource_version: str | None = None,
) -> dict:
    metadata: dict[str, Any] = {"name": name, "namespace": namespace}
    if resource_version:
        metadata["resourceVersion"] = resource_version

    parent_ref: dict[str, Any] = {
        "group": "gateway.networking.k8s.io",
        "kind": "Gateway",
        "name": parent_gateway_name,
    }
    if parent_gateway_namespace:
        parent_ref["namespace"] = parent_gateway_namespace
    if parent_gateway_section_name:
        parent_ref["sectionName"] = parent_gateway_section_name

    spec: dict[str, Any] = {
        "parentRefs": [parent_ref],
        "rules": [_build_route_rule(r) for r in rules],
    }
    if hostnames:
        spec["hostnames"] = hostnames

    return {"apiVersion": GATEWAY_API_VERSION, "kind": "HTTPRoute", "metadata": metadata, "spec": spec}


# ──────────────────────────────────────────────────────────────────────────────
# GatewayClass
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/k8s/clusters/{cluster_id}/waf/gateway-classes", dependencies=[Depends(require_viewer)])
@handle_route_errors("list gateway classes")
def list_gateway_classes(cluster_id: int, db: Session = Depends(get_db)):
    k8s = KubernetesService(db)
    items = k8s.get_resources(cluster_id, "gatewayclass")
    return {"gateway_classes": items, "count": len(items)}


@router.post("/k8s/clusters/{cluster_id}/waf/gateway-classes")
@handle_route_errors("create gateway class")
def create_gateway_class(
    cluster_id: int,
    req: GatewayClassCreateRequest,
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    k8s = KubernetesService(db)
    body = {
        "apiVersion": GATEWAYCLASS_API_VERSION,
        "kind": "GatewayClass",
        "metadata": {"name": req.name},
        "spec": {
            "controllerName": req.controller_name,
            "description": req.description,
        },
    }
    result = k8s.create_resource(cluster_id, "gatewayclass", yaml.dump(body))
    return result.get("resource", result)


@router.delete("/k8s/clusters/{cluster_id}/waf/gateway-classes/{name}")
@handle_route_errors("delete gateway class")
def delete_gateway_class(
    cluster_id: int, name: str,
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    return KubernetesService(db).delete_resource(cluster_id, "gatewayclass", name)


# ──────────────────────────────────────────────────────────────────────────────
# Gateway
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/k8s/clusters/{cluster_id}/waf/gateways", dependencies=[Depends(require_viewer)])
@handle_route_errors("list gateways")
def list_gateways(cluster_id: int, namespace: str | None = None, db: Session = Depends(get_db)):
    k8s = KubernetesService(db)
    items = k8s.get_resources(cluster_id, "gateway", namespace)
    return {"gateways": items, "count": len(items)}


@router.get("/k8s/clusters/{cluster_id}/waf/gateways/{name}", dependencies=[Depends(require_viewer)])
@handle_route_errors("get gateway")
def get_gateway(cluster_id: int, name: str, namespace: str = "default", db: Session = Depends(get_db)):
    k8s = KubernetesService(db)
    items = k8s.get_resources(cluster_id, "gateway", namespace)
    gw = _find_by_name(items, name)
    if not gw:
        raise NotFoundError("gateway", name)
    return gw


@router.post("/k8s/clusters/{cluster_id}/waf/gateways")
@handle_route_errors("create gateway")
def create_gateway(
    cluster_id: int,
    req: GatewayCreateRequest,
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    k8s = KubernetesService(db)
    body = _build_gateway_dict(
        req.name, req.namespace, req.gateway_class_name,
        req.listeners, req.addresses, req.waf_profile_name, req.annotations,
    )
    result = k8s.create_resource(cluster_id, "gateway", yaml.dump(body), req.namespace)
    return result.get("resource", result)


@router.put("/k8s/clusters/{cluster_id}/waf/gateways/{name}")
@handle_route_errors("update gateway")
def update_gateway(
    cluster_id: int, name: str,
    req: GatewayUpdateRequest,
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    k8s = KubernetesService(db)
    existing = _find_by_name(k8s.get_resources(cluster_id, "gateway", req.namespace), name)
    if not existing:
        raise NotFoundError("gateway", name)
    rv = existing.get("metadata", {}).get("resourceVersion")
    # Preserve existing gateway class from the live object
    existing_gc = existing.get("spec", {}).get("gatewayClassName", "f5-gatewayclass")
    body = _build_gateway_dict(
        name, req.namespace, existing_gc,
        req.listeners, req.addresses, req.waf_profile_name, req.annotations, rv,
    )
    result = k8s.update_resource(cluster_id, "gateway", name, yaml.dump(body), req.namespace)
    return result.get("resource", result)


@router.delete("/k8s/clusters/{cluster_id}/waf/gateways/{name}")
@handle_route_errors("delete gateway")
def delete_gateway(
    cluster_id: int, name: str, namespace: str = "default",
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    return KubernetesService(db).delete_resource(cluster_id, "gateway", name, namespace)


# ──────────────────────────────────────────────────────────────────────────────
# F5BigWebSecurityProfile  (WAF policy → Gateway bridge)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/k8s/clusters/{cluster_id}/waf/security-profiles", dependencies=[Depends(require_viewer)])
@handle_route_errors("list waf security profiles")
def list_security_profiles(cluster_id: int, namespace: str | None = None, db: Session = Depends(get_db)):
    k8s = KubernetesService(db)
    items = k8s.get_resources(cluster_id, "f5-big-web-security-profiles", namespace)
    return {"profiles": items, "count": len(items)}


@router.get("/k8s/clusters/{cluster_id}/waf/security-profiles/{name}", dependencies=[Depends(require_viewer)])
@handle_route_errors("get waf security profile")
def get_security_profile(cluster_id: int, name: str, namespace: str = "default", db: Session = Depends(get_db)):
    k8s = KubernetesService(db)
    items = k8s.get_resources(cluster_id, "f5-big-web-security-profiles", namespace)
    profile = _find_by_name(items, name)
    if not profile:
        raise NotFoundError("f5bigwebsecurityprofile", name)
    return profile


@router.post("/k8s/clusters/{cluster_id}/waf/security-profiles")
@handle_route_errors("create waf security profile")
def create_security_profile(
    cluster_id: int,
    req: WafSecurityProfileCreateRequest,
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    k8s = KubernetesService(db)
    body = {
        "apiVersion": WSP_API_VERSION,
        "kind": "F5BigWebSecurityProfile",
        "metadata": {"name": req.name, "namespace": req.namespace},
        "spec": {"policyName": req.policy_name},
    }
    result = k8s.create_resource(cluster_id, "f5-big-web-security-profiles", yaml.dump(body), req.namespace)
    return result.get("resource", result)


@router.put("/k8s/clusters/{cluster_id}/waf/security-profiles/{name}")
@handle_route_errors("update waf security profile")
def update_security_profile(
    cluster_id: int, name: str,
    req: WafSecurityProfileUpdateRequest,
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    k8s = KubernetesService(db)
    existing = _find_by_name(k8s.get_resources(cluster_id, "f5-big-web-security-profiles", req.namespace), name)
    if not existing:
        raise NotFoundError("f5bigwebsecurityprofile", name)
    rv = existing.get("metadata", {}).get("resourceVersion")
    body = {
        "apiVersion": WSP_API_VERSION,
        "kind": "F5BigWebSecurityProfile",
        "metadata": {"name": name, "namespace": req.namespace, "resourceVersion": rv},
        "spec": {"policyName": req.policy_name},
    }
    result = k8s.update_resource(cluster_id, "f5-big-web-security-profiles", name, yaml.dump(body), req.namespace)
    return result.get("resource", result)


@router.delete("/k8s/clusters/{cluster_id}/waf/security-profiles/{name}")
@handle_route_errors("delete waf security profile")
def delete_security_profile(
    cluster_id: int, name: str, namespace: str = "default",
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    return KubernetesService(db).delete_resource(cluster_id, "f5-big-web-security-profiles", name, namespace)


# ──────────────────────────────────────────────────────────────────────────────
# HTTPRoute
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/k8s/clusters/{cluster_id}/waf/httproutes", dependencies=[Depends(require_viewer)])
@handle_route_errors("list httproutes")
def list_httproutes(cluster_id: int, namespace: str | None = None, db: Session = Depends(get_db)):
    k8s = KubernetesService(db)
    items = k8s.get_resources(cluster_id, "httproute", namespace)
    return {"routes": items, "count": len(items)}


@router.get("/k8s/clusters/{cluster_id}/waf/httproutes/{name}", dependencies=[Depends(require_viewer)])
@handle_route_errors("get httproute")
def get_httproute(cluster_id: int, name: str, namespace: str = "default", db: Session = Depends(get_db)):
    k8s = KubernetesService(db)
    items = k8s.get_resources(cluster_id, "httproute", namespace)
    route = _find_by_name(items, name)
    if not route:
        raise NotFoundError("httproute", name)
    return route


@router.post("/k8s/clusters/{cluster_id}/waf/httproutes")
@handle_route_errors("create httproute")
def create_httproute(
    cluster_id: int,
    req: HTTPRouteCreateRequest,
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    k8s = KubernetesService(db)
    body = _build_httproute_dict(
        req.name, req.namespace,
        req.parent_gateway_name, req.parent_gateway_namespace,
        req.parent_gateway_section_name, req.hostnames, req.rules,
    )
    result = k8s.create_resource(cluster_id, "httproute", yaml.dump(body), req.namespace)
    return result.get("resource", result)


@router.put("/k8s/clusters/{cluster_id}/waf/httproutes/{name}")
@handle_route_errors("update httproute")
def update_httproute(
    cluster_id: int, name: str,
    req: HTTPRouteUpdateRequest,
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    k8s = KubernetesService(db)
    existing = _find_by_name(k8s.get_resources(cluster_id, "httproute", req.namespace), name)
    if not existing:
        raise NotFoundError("httproute", name)
    rv = existing.get("metadata", {}).get("resourceVersion")
    body = _build_httproute_dict(
        name, req.namespace,
        req.parent_gateway_name, req.parent_gateway_namespace,
        req.parent_gateway_section_name, req.hostnames, req.rules, rv,
    )
    result = k8s.update_resource(cluster_id, "httproute", name, yaml.dump(body), req.namespace)
    return result.get("resource", result)


@router.delete("/k8s/clusters/{cluster_id}/waf/httproutes/{name}")
@handle_route_errors("delete httproute")
def delete_httproute(
    cluster_id: int, name: str, namespace: str = "default",
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    return KubernetesService(db).delete_resource(cluster_id, "httproute", name, namespace)


# ──────────────────────────────────────────────────────────────────────────────
# ReferenceGrant  (cross-namespace permissions)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/k8s/clusters/{cluster_id}/waf/reference-grants", dependencies=[Depends(require_viewer)])
@handle_route_errors("list reference grants")
def list_reference_grants(cluster_id: int, namespace: str | None = None, db: Session = Depends(get_db)):
    k8s = KubernetesService(db)
    items = k8s.get_resources(cluster_id, "referencegrant", namespace)
    return {"reference_grants": items, "count": len(items)}


@router.post("/k8s/clusters/{cluster_id}/waf/reference-grants")
@handle_route_errors("create reference grant")
def create_reference_grant(
    cluster_id: int,
    req: ReferenceGrantCreateRequest,
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    k8s = KubernetesService(db)
    to_entry: dict[str, Any] = {"group": req.to_group, "kind": req.to_kind}
    if req.to_name:
        to_entry["name"] = req.to_name
    body = {
        "apiVersion": REFGRANT_API_VERSION,
        "kind": "ReferenceGrant",
        "metadata": {"name": req.name, "namespace": req.namespace},
        "spec": {
            "from": [{"group": req.from_group, "kind": req.from_kind, "namespace": req.from_namespace}],
            "to": [to_entry],
        },
    }
    result = k8s.create_resource(cluster_id, "referencegrant", yaml.dump(body), req.namespace)
    return result.get("resource", result)


@router.delete("/k8s/clusters/{cluster_id}/waf/reference-grants/{name}")
@handle_route_errors("delete reference grant")
def delete_reference_grant(
    cluster_id: int, name: str, namespace: str = "default",
    db: Session = Depends(get_db),
    _user=Depends(require_cluster_owner),
):
    return KubernetesService(db).delete_resource(cluster_id, "referencegrant", name, namespace)


# ──────────────────────────────────────────────────────────────────────────────
# Convenience: topology view — returns the full binding chain for a cluster
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/k8s/clusters/{cluster_id}/waf/gateway-topology", dependencies=[Depends(require_viewer)])
@handle_route_errors("get gateway waf topology")
def get_gateway_topology(cluster_id: int, namespace: str | None = None, db: Session = Depends(get_db)):
    """Return all Gateway API + WAF resources together so the UI can build the binding graph."""
    k8s = KubernetesService(db)
    gateways        = k8s.get_resources(cluster_id, "gateway", namespace)
    httproutes      = k8s.get_resources(cluster_id, "httproute", namespace)
    profiles        = k8s.get_resources(cluster_id, "f5-big-web-security-profiles", namespace)
    policies        = k8s.get_resources(cluster_id, "appolicy", namespace)
    refgrants       = k8s.get_resources(cluster_id, "referencegrant", namespace)
    gateway_classes = k8s.get_resources(cluster_id, "gatewayclass")

    return {
        "gateway_classes": gateway_classes,
        "gateways":        gateways,
        "httproutes":      httproutes,
        "security_profiles": profiles,
        "waf_policies":    policies,
        "reference_grants": refgrants,
    }
