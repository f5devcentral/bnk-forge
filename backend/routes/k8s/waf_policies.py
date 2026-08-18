"""
WAF Policy Manager routes.

Thin CRUD routes over the existing generic KubernetesService CRD methods for
appprotect.f5.com/v1 resources (APPolicy, APLogConf, APSignatures, APUserSig),
which are watched/compiled by the unmodified nap-policy-operator PLM chart
(Policy Controller + Compiler + SeaweedFS) — no bnk-forge compile logic here.

See docs/WAF_POLICY_MANAGER_DESIGN.md for the full design.
"""

import logging

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import NotFoundError, handle_route_errors
from database import get_db
from models import User
from routes.auth import require_cluster_owner, require_viewer
from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-waf-policies"])

# Singleton APSignatures resource name (enforced by the CRD's own schema).
APSIGNATURES_NAME = "apsignatures"


# ============================================================================
# Request models (inline, per AGENTS.md convention)
# ============================================================================

class WafPolicyCreateRequest(BaseModel):
    name: str
    namespace: str
    spec: dict


class WafPolicyUpdateRequest(BaseModel):
    namespace: str
    spec: dict


class WafLogConfCreateRequest(BaseModel):
    name: str
    namespace: str
    spec: dict


class WafUserSigCreateRequest(BaseModel):
    name: str
    namespace: str
    spec: dict


class WafSignaturesUpdateRequest(BaseModel):
    namespace: str
    spec: dict


# ============================================================================
# Helpers
# ============================================================================

def _build_resource_yaml(
    kind: str, name: str, namespace: str, spec: dict, resource_version: str | None = None
) -> str:
    """Build a resource dict for appprotect.f5.com/v1 and serialize to YAML.

    Reuses the existing generic create_resource/update_resource methods
    (which parse resource_yaml), so no changes to services/kubernetes/_resources.py.

    `resource_version` is required for updates (Kubernetes' `replace` semantics use
    it for optimistic concurrency; omitting it on an update yields a 422 from the
    API server, confirmed against a live cluster).
    """
    metadata: dict = {"name": name, "namespace": namespace}
    if resource_version:
        metadata["resourceVersion"] = resource_version
    resource = {
        "apiVersion": "appprotect.f5.com/v1",
        "kind": kind,
        "metadata": metadata,
        "spec": spec,
    }
    return yaml.dump(resource)


def _find_by_name(resources: list[dict], name: str) -> dict | None:
    for r in resources:
        if r.get("metadata", {}).get("name") == name:
            return r
    return None


# ============================================================================
# APPolicy
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/waf/policies",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list WAF policies")
def list_waf_policies(cluster_id: int, namespace: str | None = None, db: Session = Depends(get_db)):
    k8s_service = KubernetesService(db)
    policies = k8s_service.get_resources(cluster_id, "appolicy", namespace)
    return {"policies": policies, "count": len(policies)}


@router.get(
    "/k8s/clusters/{cluster_id}/waf/policies/{name}",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get WAF policy")
def get_waf_policy(cluster_id: int, name: str, namespace: str, db: Session = Depends(get_db)):
    k8s_service = KubernetesService(db)
    policies = k8s_service.get_resources(cluster_id, "appolicy", namespace)
    policy = _find_by_name(policies, name)
    if not policy:
        raise NotFoundError("waf_policy", name)
    return policy


@router.post(
    "/k8s/clusters/{cluster_id}/waf/policies",
)
@handle_route_errors("create WAF policy")
def create_waf_policy(
    cluster_id: int,
    request: WafPolicyCreateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    resource_yaml = _build_resource_yaml("APPolicy", request.name, request.namespace, request.spec)
    result = k8s_service.create_resource(cluster_id, "appolicy", resource_yaml, request.namespace)
    return result.get("resource", result)


@router.put(
    "/k8s/clusters/{cluster_id}/waf/policies/{name}",
)
@handle_route_errors("update WAF policy")
def update_waf_policy(
    cluster_id: int,
    name: str,
    request: WafPolicyUpdateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    existing = _find_by_name(k8s_service.get_resources(cluster_id, "appolicy", request.namespace), name)
    if not existing:
        raise NotFoundError("waf_policy", name)
    resource_version = existing.get("metadata", {}).get("resourceVersion")
    resource_yaml = _build_resource_yaml("APPolicy", name, request.namespace, request.spec, resource_version)
    result = k8s_service.update_resource(cluster_id, "appolicy", name, resource_yaml, request.namespace)
    return result.get("resource", result)


@router.delete(
    "/k8s/clusters/{cluster_id}/waf/policies/{name}",
)
@handle_route_errors("delete WAF policy")
def delete_waf_policy(
    cluster_id: int,
    name: str,
    namespace: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    return k8s_service.delete_resource(cluster_id, "appolicy", name, namespace)


# ============================================================================
# APLogConf
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/waf/logconfs",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list WAF log profiles")
def list_waf_logconfs(cluster_id: int, namespace: str | None = None, db: Session = Depends(get_db)):
    k8s_service = KubernetesService(db)
    log_confs = k8s_service.get_resources(cluster_id, "aplogconf", namespace)
    return {"log_confs": log_confs, "count": len(log_confs)}


@router.post(
    "/k8s/clusters/{cluster_id}/waf/logconfs",
)
@handle_route_errors("create WAF log profile")
def create_waf_logconf(
    cluster_id: int,
    request: WafLogConfCreateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    resource_yaml = _build_resource_yaml("APLogConf", request.name, request.namespace, request.spec)
    result = k8s_service.create_resource(cluster_id, "aplogconf", resource_yaml, request.namespace)
    return result.get("resource", result)


@router.put(
    "/k8s/clusters/{cluster_id}/waf/logconfs/{name}",
)
@handle_route_errors("update WAF log profile")
def update_waf_logconf(
    cluster_id: int,
    name: str,
    request: WafPolicyUpdateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    existing = _find_by_name(k8s_service.get_resources(cluster_id, "aplogconf", request.namespace), name)
    if not existing:
        raise NotFoundError("waf_logconf", name)
    resource_version = existing.get("metadata", {}).get("resourceVersion")
    resource_yaml = _build_resource_yaml("APLogConf", name, request.namespace, request.spec, resource_version)
    result = k8s_service.update_resource(cluster_id, "aplogconf", name, resource_yaml, request.namespace)
    return result.get("resource", result)


@router.delete(
    "/k8s/clusters/{cluster_id}/waf/logconfs/{name}",
)
@handle_route_errors("delete WAF log profile")
def delete_waf_logconf(
    cluster_id: int,
    name: str,
    namespace: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    return k8s_service.delete_resource(cluster_id, "aplogconf", name, namespace)


# ============================================================================
# APSignatures — singleton per namespace (metadata.name must be "apsignatures")
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/waf/signatures",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get WAF signatures")
def get_waf_signatures(cluster_id: int, namespace: str, db: Session = Depends(get_db)):
    k8s_service = KubernetesService(db)
    resources = k8s_service.get_resources(cluster_id, "apsignatures", namespace)
    return _find_by_name(resources, APSIGNATURES_NAME)


@router.put(
    "/k8s/clusters/{cluster_id}/waf/signatures",
)
@handle_route_errors("save WAF signatures")
def upsert_waf_signatures(
    cluster_id: int,
    request: WafSignaturesUpdateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    existing = _find_by_name(
        k8s_service.get_resources(cluster_id, "apsignatures", request.namespace), APSIGNATURES_NAME
    )
    if existing:
        resource_version = existing.get("metadata", {}).get("resourceVersion")
        resource_yaml = _build_resource_yaml(
            "APSignatures", APSIGNATURES_NAME, request.namespace, request.spec, resource_version
        )
        result = k8s_service.update_resource(
            cluster_id, "apsignatures", APSIGNATURES_NAME, resource_yaml, request.namespace
        )
    else:
        resource_yaml = _build_resource_yaml("APSignatures", APSIGNATURES_NAME, request.namespace, request.spec)
        result = k8s_service.create_resource(cluster_id, "apsignatures", resource_yaml, request.namespace)
    return result.get("resource", result)


@router.delete(
    "/k8s/clusters/{cluster_id}/waf/signatures",
)
@handle_route_errors("delete WAF signatures")
def delete_waf_signatures(
    cluster_id: int,
    namespace: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    return k8s_service.delete_resource(cluster_id, "apsignatures", APSIGNATURES_NAME, namespace)


@router.post(
    "/k8s/clusters/{cluster_id}/waf/policies/{name}/recompile",
)
@handle_route_errors("force recompile WAF policy")
def recompile_waf_policy(
    cluster_id: int,
    name: str,
    namespace: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """Force recompile by bumping a metadata annotation (triggers reconcile loop)."""
    k8s_service = KubernetesService(db)
    existing = _find_by_name(k8s_service.get_resources(cluster_id, "appolicy", namespace), name)
    if not existing:
        raise NotFoundError("waf_policy", name)
    resource_version = existing.get("metadata", {}).get("resourceVersion")
    spec = existing.get("spec", {})
    # Rebuild with same spec — touch triggers the controller reconcile loop
    resource_yaml = _build_resource_yaml("APPolicy", name, namespace, spec, resource_version)
    result = k8s_service.update_resource(cluster_id, "appolicy", name, resource_yaml, namespace)
    return {"message": f"Recompile triggered for {name}", "resource": result.get("resource", result)}


# ============================================================================
# APUserSig
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/waf/usersigs",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list WAF user signatures")
def list_waf_usersigs(cluster_id: int, namespace: str | None = None, db: Session = Depends(get_db)):
    k8s_service = KubernetesService(db)
    user_sigs = k8s_service.get_resources(cluster_id, "apusersig", namespace)
    return {"user_sigs": user_sigs, "count": len(user_sigs)}


@router.post(
    "/k8s/clusters/{cluster_id}/waf/usersigs",
)
@handle_route_errors("create WAF user signature")
def create_waf_usersig(
    cluster_id: int,
    request: WafUserSigCreateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    resource_yaml = _build_resource_yaml("APUserSig", request.name, request.namespace, request.spec)
    result = k8s_service.create_resource(cluster_id, "apusersig", resource_yaml, request.namespace)
    return result.get("resource", result)


@router.put(
    "/k8s/clusters/{cluster_id}/waf/usersigs/{name}",
)
@handle_route_errors("update WAF user signature")
def update_waf_usersig(
    cluster_id: int,
    name: str,
    request: WafPolicyUpdateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    existing = _find_by_name(k8s_service.get_resources(cluster_id, "apusersig", request.namespace), name)
    if not existing:
        raise NotFoundError("waf_usersig", name)
    resource_version = existing.get("metadata", {}).get("resourceVersion")
    resource_yaml = _build_resource_yaml("APUserSig", name, request.namespace, request.spec, resource_version)
    result = k8s_service.update_resource(cluster_id, "apusersig", name, resource_yaml, request.namespace)
    return result.get("resource", result)


@router.delete(
    "/k8s/clusters/{cluster_id}/waf/usersigs/{name}",
)
@handle_route_errors("delete WAF user signature")
def delete_waf_usersig(
    cluster_id: int,
    name: str,
    namespace: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    k8s_service = KubernetesService(db)
    return k8s_service.delete_resource(cluster_id, "apusersig", name, namespace)
