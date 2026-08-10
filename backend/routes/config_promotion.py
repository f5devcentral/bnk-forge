"""
Cross-Cluster Config Promotion API (UX-013).

Endpoints:
  - POST /api/config/promote       — Promote config from source to target cluster
  - GET  /api/config/promotions    — List promotion history

Workflow:
  1. dry_run=true:  Export both clusters, diff, return changes (no mutations)
  2. dry_run=false: Export source, diff against target, apply delta to target,
                    log to promotion history
"""

import json
import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import BadRequestError, InternalError, NotFoundError, handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from services.config_export_service import (
    _flatten_resources,
    diff_configs,
    export_cluster_config,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config", tags=["config-promotion"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PromoteRequest(BaseModel):
    source_cluster_id: int
    target_cluster_id: int
    dry_run: bool = True


class PromotionChange(BaseModel):
    action: str  # "add" | "modify" | "remove"
    kind: str
    name: str
    namespace: str
    diff: str | None = None


class PromoteResponse(BaseModel):
    source_cluster: dict  # {"id": int, "name": str}
    target_cluster: dict  # {"id": int, "name": str}
    changes: list[PromotionChange]
    total_changes: int
    applied: bool  # false for dry_run
    apply_results: dict | None = None  # only when applied


class PromotionHistoryEntry(BaseModel):
    id: int
    source_cluster_name: str
    target_cluster_name: str
    change_count: int
    applied_by: str | None
    applied_at: str
    success: bool
    applied_count: int
    failed_count: int
    skipped_count: int
    changes_summary: list | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_user(request: Request) -> str:
    """Extract username from request."""
    user = getattr(request.state, "user", None)
    if user:
        return user.get("username", "system")
    return "system"


def _build_changes_from_diff(
    diff_result: dict,
    source_config: dict,
    target_config: dict,
) -> list[dict]:
    """
    Convert the diff_configs() output into a flat list of promotion changes.

    Each change is {action, kind, name, namespace, diff}.
    """
    changes: list[dict] = []
    resources_diff = diff_result.get("resources", {})

    # Resources only in source → "add" (need to create in target)
    source_resources = _flatten_resources(source_config.get("resources", {}))
    target_resources = _flatten_resources(target_config.get("resources", {}))

    for key in resources_diff.get("only_in_a", []):
        resource = source_resources.get(key, {})
        kind = resource.get("kind", "Unknown")
        name = resource.get("metadata", {}).get("name", "unknown")
        ns = resource.get("metadata", {}).get("namespace", "")
        changes.append({
            "action": "add",
            "kind": kind,
            "name": name,
            "namespace": ns,
            "diff": None,
        })

    # Resources only in target → "remove" (exists in target but not source)
    for key in resources_diff.get("only_in_b", []):
        resource = target_resources.get(key, {})
        kind = resource.get("kind", "Unknown")
        name = resource.get("metadata", {}).get("name", "unknown")
        ns = resource.get("metadata", {}).get("namespace", "")
        changes.append({
            "action": "remove",
            "kind": kind,
            "name": name,
            "namespace": ns,
            "diff": None,
        })

    # Resources that differ → "modify"
    for changed in resources_diff.get("changed", []):
        kind = changed.get("kind", "Unknown")
        resource_key = changed.get("resource", "")
        # Parse name/ns from resource key (format: Kind/namespace/name or Kind/name)
        parts = resource_key.split("/")
        if len(parts) == 3:
            _, ns, name = parts
        elif len(parts) == 2:
            _, name = parts
            ns = ""
        else:
            name = resource_key
            ns = ""

        # Build a human-readable diff string
        spec_a = changed.get("spec_a", {})
        spec_b = changed.get("spec_b", {})
        diff_lines = []
        all_keys = sorted(set(list(spec_a.keys()) + list(spec_b.keys())))
        for k in all_keys:
            val_a = spec_a.get(k)
            val_b = spec_b.get(k)
            if val_a != val_b:
                if val_a is not None:
                    diff_lines.append(f"- {k}: {json.dumps(val_a, default=str)[:200]}")
                if val_b is not None:
                    diff_lines.append(f"+ {k}: {json.dumps(val_b, default=str)[:200]}")

        changes.append({
            "action": "modify",
            "kind": kind,
            "name": name,
            "namespace": ns,
            "diff": "\n".join(diff_lines) if diff_lines else None,
        })

    return changes


def _apply_delta_to_target(
    target_cluster_id: int,
    source_config: dict,
    changes: list[dict],
    db: Session,
) -> dict:
    """
    Apply only the changed/added resources from source to target cluster.

    Uses the same server-side apply pattern as config_export import.
    Returns {applied: [...], failed: [...], skipped: [...]}.
    """
    from kubernetes import client as k8s_client
    from kubernetes.client.rest import ApiException

    from models import KubernetesCluster
    from services.kubernetes_service import KubernetesService

    cluster = db.query(KubernetesCluster).filter(
        KubernetesCluster.id == target_cluster_id
    ).first()
    if not cluster:
        raise NotFoundError("cluster", target_cluster_id)

    k8s_svc = KubernetesService(db)
    api_client = k8s_svc.load_kubeconfig(cluster)
    custom_api = k8s_client.CustomObjectsApi(api_client)

    # Build a flat map of source resources for lookup
    source_flat = _flatten_resources(source_config.get("resources", {}))

    results = {"applied": [], "failed": [], "skipped": []}

    for change in changes:
        action = change["action"]
        kind = change["kind"]
        name = change["name"]
        ns = change["namespace"]

        if action == "remove":
            # For promotion, we don't delete resources from target by default
            # (that's too destructive). We skip removals.
            results["skipped"].append({
                "kind": kind, "name": name, "namespace": ns,
                "reason": "Removals are skipped during promotion (safety)"
            })
            continue

        # Find the source resource to apply
        key = f"{kind}/{ns}/{name}" if ns else f"{kind}/{name}"
        resource = source_flat.get(key)
        if not resource:
            results["skipped"].append({
                "kind": kind, "name": name, "namespace": ns,
                "reason": "Resource not found in source export"
            })
            continue

        api_version = resource.get("apiVersion", "v1")
        try:
            if "/" in api_version:
                group, version = api_version.rsplit("/", 1)
            else:
                group, version = "", api_version

            if not group:
                results["skipped"].append({
                    "kind": kind, "name": name, "namespace": ns,
                    "reason": "Core API import not supported"
                })
                continue

            from services.execution.kubernetes_engine import KNOWN_PLURALS
            plural = KNOWN_PLURALS.get(kind, kind.lower() + "s")

            # Server-side apply via PATCH
            if ns:
                custom_api.patch_namespaced_custom_object(
                    group=group, version=version, namespace=ns,
                    plural=plural, name=name, body=resource,
                    field_manager="bnk-forge-promote", force=True,
                )
            else:
                custom_api.patch_cluster_custom_object(
                    group=group, version=version,
                    plural=plural, name=name, body=resource,
                    field_manager="bnk-forge-promote", force=True,
                )

            results["applied"].append({
                "kind": kind, "name": name, "namespace": ns,
            })
        except ApiException as e:
            if e.status == 404:
                results["skipped"].append({
                    "kind": kind, "name": name, "namespace": ns,
                    "reason": f"CRD not installed: {kind}"
                })
            else:
                results["failed"].append({
                    "kind": kind, "name": name, "namespace": ns,
                    "error": str(e.reason)[:200]
                })
        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "resource type" in error_str.lower():
                results["skipped"].append({
                    "kind": kind, "name": name, "namespace": ns,
                    "reason": f"CRD not installed: {kind}"
                })
            else:
                results["failed"].append({
                    "kind": kind, "name": name, "namespace": ns,
                    "error": error_str[:200]
                })

    return results


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/promote", dependencies=[Depends(require_operator)])
@handle_route_errors("promote config")
def promote_config(
    body: PromoteRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Promote configuration from source cluster to target cluster.

    When dry_run=true:
      Exports both configs, diffs them, returns the diff + list of changes
      that WOULD be applied. No mutations.

    When dry_run=false:
      Exports source config, computes diff against target, applies only the
      delta to the target cluster. Logs the promotion to history.
    """
    from models import KubernetesCluster

    if body.source_cluster_id == body.target_cluster_id:
        raise BadRequestError(
            "Source and target clusters must be different",
            code="SAME_CLUSTER"
        )

    # Verify clusters exist
    source_cluster = db.query(KubernetesCluster).filter(
        KubernetesCluster.id == body.source_cluster_id
    ).first()
    if not source_cluster:
        raise NotFoundError("cluster", body.source_cluster_id)

    target_cluster = db.query(KubernetesCluster).filter(
        KubernetesCluster.id == body.target_cluster_id
    ).first()
    if not target_cluster:
        raise NotFoundError("cluster", body.target_cluster_id)

    # Export both configs
    try:
        source_config = export_cluster_config(body.source_cluster_id, db)
    except ValueError:
        raise NotFoundError("cluster", body.source_cluster_id)

    try:
        target_config = export_cluster_config(body.target_cluster_id, db)
    except ValueError:
        raise NotFoundError("cluster", body.target_cluster_id)

    # Compute diff
    diff_result = diff_configs(source_config, target_config)

    # Build structured changes
    changes = _build_changes_from_diff(diff_result, source_config, target_config)

    source_info = {"id": source_cluster.id, "name": source_cluster.name}
    target_info = {"id": target_cluster.id, "name": target_cluster.name}

    if body.dry_run:
        return {
            "source_cluster": source_info,
            "target_cluster": target_info,
            "changes": changes,
            "total_changes": len(changes),
            "applied": False,
            "apply_results": None,
        }

    # Apply delta to target
    try:
        apply_results = _apply_delta_to_target(
            body.target_cluster_id, source_config, changes, db
        )
    except Exception as e:
        logger.error(f"Promotion apply failed: {e}", exc_info=True)
        # Log the failed promotion
        _log_promotion(
            db=db,
            source_cluster=source_cluster,
            target_cluster=target_cluster,
            changes=changes,
            applied_by=_get_user(request),
            success=False,
            error_message=str(e)[:1000],
            applied_count=0,
            failed_count=0,
            skipped_count=0,
        )
        db.commit()
        raise InternalError(f"Failed to apply promotion: {str(e)}")

    # Log the successful promotion
    _log_promotion(
        db=db,
        source_cluster=source_cluster,
        target_cluster=target_cluster,
        changes=changes,
        applied_by=_get_user(request),
        success=True,
        error_message=None,
        applied_count=len(apply_results["applied"]),
        failed_count=len(apply_results["failed"]),
        skipped_count=len(apply_results["skipped"]),
    )
    db.commit()

    return {
        "source_cluster": source_info,
        "target_cluster": target_info,
        "changes": changes,
        "total_changes": len(changes),
        "applied": True,
        "apply_results": apply_results,
    }


@router.get("/promotions", dependencies=[Depends(require_viewer)])
def list_promotions(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """List promotion history, newest first."""
    from models.config_promotion import ConfigPromotion

    total = db.query(ConfigPromotion).count()
    promotions = (
        db.query(ConfigPromotion)
        .order_by(ConfigPromotion.applied_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "items": [
            {
                "id": p.id,
                "source_cluster_name": p.source_cluster_name,
                "target_cluster_name": p.target_cluster_name,
                "change_count": p.change_count,
                "applied_by": p.applied_by,
                "applied_at": p.applied_at.isoformat() + "Z" if p.applied_at else None,
                "success": p.success,
                "applied_count": p.applied_count,
                "failed_count": p.failed_count,
                "skipped_count": p.skipped_count,
                "changes_summary": p.changes_summary,
            }
            for p in promotions
        ],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _log_promotion(
    db: Session,
    source_cluster,
    target_cluster,
    changes: list[dict],
    applied_by: str,
    success: bool,
    error_message: str | None,
    applied_count: int,
    failed_count: int,
    skipped_count: int,
):
    """Log a promotion to the config_promotions table."""
    from models.config_promotion import ConfigPromotion

    # Build compact summary (skip diff text)
    summary = [
        {"action": c["action"], "kind": c["kind"], "name": c["name"], "namespace": c["namespace"]}
        for c in changes
    ]

    record = ConfigPromotion(
        source_cluster_id=source_cluster.id,
        target_cluster_id=target_cluster.id,
        source_cluster_name=source_cluster.name,
        target_cluster_name=target_cluster.name,
        change_count=len(changes),
        changes_summary=summary,
        applied_by=applied_by,
        success=success,
        error_message=error_message,
        applied_count=applied_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
    )
    db.add(record)
    db.flush()  # ENG-006: route owns the transaction, not helpers
    logger.info(
        f"Promotion logged: {source_cluster.name} → {target_cluster.name}, "
        f"{len(changes)} changes, success={success}, by={applied_by}"
    )
