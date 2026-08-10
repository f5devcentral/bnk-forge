"""
Configuration Export/Import/Diff API routes.

Endpoints:
  - GET  /api/clusters/{id}/bnk/export      — Export cluster config as YAML
  - GET  /api/clusters/{id}/bnk/export/json  — Export cluster config as JSON
  - POST /api/clusters/{id}/bnk/import       — Import/apply config to cluster
  - POST /api/clusters/bnk/diff              — Compare two cluster configs
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError, handle_route_errors
from database import get_db
from models import KubernetesCluster, User
from routes.auth import require_cluster_owner, require_operator, require_viewer
from schemas.system import ConfigImportRequest
from services.config_export_service import (
    config_to_yaml,
    diff_configs,
    export_cluster_config,
)
from services.platform_context_service import PlatformContextService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["config-export"])


def _build_platform_context_for_cluster_diff(cluster_a: KubernetesCluster, cluster_b: KubernetesCluster) -> dict[str, object]:
    """Additive platform-context payload for mixed-environment config diff truthfulness."""
    context_a = PlatformContextService.serialize_cluster_context(cluster_a)
    context_b = PlatformContextService.serialize_cluster_context(cluster_b)

    profile_a = context_a.detected_platform_profile
    profile_b = context_b.detected_platform_profile
    mixed_profiles = (
        profile_a != "unknown"
        and profile_b != "unknown"
        and profile_a != profile_b
    )

    caveats: list[str] = []
    support_semantics: list[str] = []
    if mixed_profiles:
        caveats.append(
            "Clusters have different detected platform profiles. "
            "Interpret config differences with platform capability/constraint context."
        )
        support_semantics.append(
            "Platform-aware support semantics can differ between source and target clusters; "
            "validate prerequisites and constraints before promotion."
        )

    return {
        "cluster_a": {
            "id": cluster_a.id,
            "name": cluster_a.name,
            "detected_platform_profile": context_a.detected_platform_profile,
            "detected_platform_provider": context_a.detected_platform_provider,
            "platform_capabilities": context_a.platform_capabilities,
            "platform_constraints": context_a.platform_constraints,
        },
        "cluster_b": {
            "id": cluster_b.id,
            "name": cluster_b.name,
            "detected_platform_profile": context_b.detected_platform_profile,
            "detected_platform_provider": context_b.detected_platform_provider,
            "platform_capabilities": context_b.platform_capabilities,
            "platform_constraints": context_b.platform_constraints,
        },
        "mixed_platform_profiles": mixed_profiles,
        "comparison_caveats": caveats,
        "support_semantics": support_semantics,
    }


@router.get("/clusters/{cluster_id}/bnk/export", dependencies=[Depends(require_viewer)])
@handle_route_errors("export cluster config as YAML")
def export_config_yaml(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    Export complete BNK configuration from a cluster as YAML.

    Returns downloadable YAML file containing:
    - All F5 BNK CRDs (data-plane, gateway, security, AI)
    - Gateway API resources
    - Module variables from BNK-Forge
    """
    try:
        config = export_cluster_config(cluster_id, db)
    except ValueError:
        raise NotFoundError("cluster", cluster_id)

    yaml_content = config_to_yaml(config)
    cluster_name = config.get("bnk_forge_export", {}).get("cluster", {}).get("name", f"cluster-{cluster_id}")
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"bnk-config-{cluster_name}-{timestamp}.yaml"

    return Response(
        content=yaml_content,
        media_type="application/x-yaml",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/clusters/{cluster_id}/bnk/export/json", dependencies=[Depends(require_viewer)])
@handle_route_errors("export cluster config as JSON")
def export_config_json(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """Export complete BNK configuration from a cluster as JSON."""
    try:
        config = export_cluster_config(cluster_id, db)
    except ValueError:
        raise NotFoundError("cluster", cluster_id)

    return config


@router.post("/clusters/{cluster_id}/bnk/import")
def import_config(
    cluster_id: int,
    body: ConfigImportRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Import/apply an exported BNK configuration to a cluster.

    Accepts a previously exported config (JSON) and applies all resources
    to the target cluster via server-side apply.

    Note: This applies CRDs only — does not modify BNK-Forge module state.
    Use the deploy workflow for full module management.
    """
    from kubernetes import client as k8s_client
    from kubernetes.client.rest import ApiException

    from models import KubernetesCluster
    from services.kubernetes_service import KubernetesService

    cluster = db.query(KubernetesCluster).filter(
        KubernetesCluster.id == cluster_id
    ).first()
    if not cluster:
        raise NotFoundError("cluster", cluster_id)

    k8s_svc = KubernetesService(db)
    api_client = k8s_svc.load_kubeconfig(cluster)
    custom_api = k8s_client.CustomObjectsApi(api_client)

    config = body.config
    resources = config.get("resources", {})
    if not resources:
        raise BadRequestError("No resources in config to import", code="EMPTY_CONFIG")

    results = {
        "applied": [],
        "failed": [],
        "skipped": [],
    }

    for category, resource_list in resources.items():
        for resource in resource_list:
            kind = resource.get("kind", "Unknown")
            name = resource.get("metadata", {}).get("name", "unknown")
            ns = resource.get("metadata", {}).get("namespace", "")
            api_version = resource.get("apiVersion", "v1")

            try:
                # Parse group/version from apiVersion
                if "/" in api_version:
                    group, version = api_version.rsplit("/", 1)
                else:
                    group, version = "", api_version

                if not group:
                    results["skipped"].append({
                        "kind": kind, "name": name, "namespace": ns,
                        "reason": "Core API import not supported",
                    })
                    continue

                from services.execution.kubernetes_engine import KNOWN_PLURALS
                from services.kubernetes._resources import resolve_plural_by_kind
                plural = (
                    resolve_plural_by_kind(db, cluster_id, kind, group or None)
                    or KNOWN_PLURALS.get(kind, kind.lower() + "s")
                )

                # Server-side apply via PATCH with application/apply-patch+yaml
                if ns:
                    custom_api.patch_namespaced_custom_object(
                        group=group, version=version, namespace=ns,
                        plural=plural, name=name, body=resource,
                        field_manager="bnk-forge", force=True,
                    )
                else:
                    custom_api.patch_cluster_custom_object(
                        group=group, version=version,
                        plural=plural, name=name, body=resource,
                        field_manager="bnk-forge", force=True,
                    )

                results["applied"].append({
                    "kind": kind, "name": name, "namespace": ns,
                })
            except ApiException as e:
                if e.status == 404:
                    results["skipped"].append({
                        "kind": kind, "name": name, "namespace": ns,
                        "reason": f"CRD not installed: {kind}",
                    })
                else:
                    results["failed"].append({
                        "kind": kind, "name": name, "namespace": ns,
                        "error": str(e.reason)[:200],
                    })
            except Exception as e:
                error_str = str(e)
                if "404" in error_str or "resource type" in error_str.lower():
                    results["skipped"].append({
                        "kind": kind, "name": name, "namespace": ns,
                        "reason": f"CRD not installed: {kind}",
                    })
                else:
                    results["failed"].append({
                        "kind": kind, "name": name, "namespace": ns,
                        "error": error_str[:200],
                    })

    return {
        "message": f"Import complete: {len(results['applied'])} applied, {len(results['failed'])} failed, {len(results['skipped'])} skipped",
        "results": results,
        "target_cluster": cluster.name,
    }


class DiffRequest(BaseModel):
    cluster_a_id: int
    cluster_b_id: int


@router.post("/clusters/bnk/diff", dependencies=[Depends(require_operator)])
@handle_route_errors("diff cluster configs")
def diff_cluster_configs(
    request: DiffRequest,
    db: Session = Depends(get_db),
):
    """
    Compare BNK configuration between two clusters.

    Exports config from both clusters and returns the differences.
    """
    cluster_a = db.query(KubernetesCluster).filter(KubernetesCluster.id == request.cluster_a_id).first()
    if not cluster_a:
        raise NotFoundError("cluster", request.cluster_a_id)

    cluster_b = db.query(KubernetesCluster).filter(KubernetesCluster.id == request.cluster_b_id).first()
    if not cluster_b:
        raise NotFoundError("cluster", request.cluster_b_id)

    try:
        config_a = export_cluster_config(request.cluster_a_id, db)
    except ValueError:
        raise NotFoundError("cluster", request.cluster_a_id)

    try:
        config_b = export_cluster_config(request.cluster_b_id, db)
    except ValueError:
        raise NotFoundError("cluster", request.cluster_b_id)

    diff_result = diff_configs(config_a, config_b)
    diff_result["platform_context"] = _build_platform_context_for_cluster_diff(cluster_a, cluster_b)
    return diff_result
