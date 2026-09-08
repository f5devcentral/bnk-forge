"""
Use-Case Artifact service (D-034 Phase 0 tracer).

Capture -> store immutable version -> render -> apply, on a single kind
(F5SPKVlan) and a single lifted param (spec.selfip_v4s).

`_CAPTURE_PATHS` is the cluster-specific-field registry for P0 — a
one-entry constant list. Capture and render both *iterate* it rather than
branching on kind, so Phase 1 can swap it for a DB-backed registry query
with zero change to the walk.

Usage:
    from services.usecase_artifact_service import capture_usecase_artifact, apply_usecase_artifact

    version, created = capture_usecase_artifact(db, cluster_id, name="east-west", version="v1")
    results, application = apply_usecase_artifact(db, cluster, version, {"selfip_v4s": ["10.0.0.1/24"]})
"""

import hashlib
import json
import logging
from copy import deepcopy
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError
from models.usecase_artifact import UseCaseApplication, UseCaseArtifact, UseCaseArtifactVersion
from services.config_export_service import _fetch_resources

logger = logging.getLogger(__name__)

# Cluster-specific-field registry (P0: one kind, one param). Capture and
# render iterate this list — never `if kind == "F5SPKVlan"`.
_CAPTURE_PATHS: list[dict[str, Any]] = [
    {"kind": "F5SPKVlan", "jsonpath": "spec.selfip_v4s", "type": "ip", "is_list": True},
]

# Fetch descriptor for the one kind P0 captures/drifts — reuses the shape
# `services.config_export_service._fetch_resources` expects.
_VLAN_RESOURCE_TYPE: dict[str, Any] = {
    "api_version": "k8s.f5net.com/v1",
    "kind": "F5SPKVlan",
    "plural": "f5-spk-vlans",
    "namespaced": True,
}


def _get_by_path(obj: dict[str, Any], path: str) -> Any:
    """Dotted-path getter. Returns None if any segment is missing."""
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_by_path(obj: dict[str, Any], path: str, value: Any) -> None:
    """Dotted-path setter. Creates intermediate dicts as needed."""
    parts = path.split(".")
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def _param_key_for_path(jsonpath: str) -> str:
    return jsonpath.rsplit(".", 1)[-1]


def lift_params(resources: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replace cluster-specific values with `${param}` tokens per `_CAPTURE_PATHS`.

    Returns (cr_templates, param_schema). One param_schema entry per distinct
    param key found — multiple matching resources share the same param.
    """
    templates = deepcopy(resources)
    lifted_keys: set[str] = set()
    param_schema: list[dict[str, Any]] = []

    for resource in templates:
        for capture_path in _CAPTURE_PATHS:
            if resource.get("kind") != capture_path["kind"]:
                continue
            value = _get_by_path(resource, capture_path["jsonpath"])
            if value is None:
                continue

            param_key = _param_key_for_path(capture_path["jsonpath"])
            _set_by_path(resource, capture_path["jsonpath"], f"${{{param_key}}}")

            if param_key not in lifted_keys:
                lifted_keys.add(param_key)
                param_schema.append({
                    "key": param_key,
                    "type": capture_path["type"],
                    "kind": "assigned",
                    "is_list": capture_path["is_list"],
                    "required": True,
                    "source_paths": [
                        {"kind": capture_path["kind"], "jsonpath": capture_path["jsonpath"]}
                    ],
                })

    return templates, param_schema


def compute_content_hash(cr_templates: list[dict[str, Any]], param_schema: list[dict[str, Any]]) -> str:
    """Hash the templated structure + param key/type/path set — excludes concrete values.

    `cr_templates` already has concrete values replaced by `${param}` tokens
    by the time this is called, so two captures of the same shape with
    different discovered values hash identically (D-034 resolved decision #5).
    """
    payload = {
        "cr_templates": cr_templates,
        "param_keys": sorted((p["key"], p["type"]) for p in param_schema),
    }
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def render(version: UseCaseArtifactVersion, param_values: dict[str, Any]) -> list[dict[str, Any]]:
    """Substitute `${param}` tokens with concrete values.

    A missing required param is a hard error listing every gap — never a
    partial apply (the footgun repair, D-034 "Render / inject / apply").
    """
    param_schema: list[dict[str, Any]] = version.param_schema
    missing = [p["key"] for p in param_schema if p.get("required", True) and p["key"] not in param_values]
    if missing:
        raise BadRequestError(
            f"Missing required param(s): {', '.join(sorted(missing))}",
            code="MISSING_REQUIRED_PARAMS",
        )

    rendered = deepcopy(version.cr_templates)
    for resource in rendered:
        for param in param_schema:
            key = param["key"]
            if key not in param_values:
                continue
            token = f"${{{key}}}"
            for source_path in param.get("source_paths", []):
                if resource.get("kind") != source_path["kind"]:
                    continue
                if _get_by_path(resource, source_path["jsonpath"]) == token:
                    _set_by_path(resource, source_path["jsonpath"], param_values[key])

    return rendered


def capture_usecase_artifact(
    db: Session,
    cluster_id: int,
    name: str,
    version: str,
    matching_bnk_version: str | None = None,
    created_by: str | None = None,
) -> tuple[UseCaseArtifactVersion, bool]:
    """Capture F5SPKVlan CRs from a cluster into a versioned use-case artifact.

    Returns (version, created) — created=False means an unchanged shape was
    already captured and the existing version was returned instead of a
    duplicate (idempotency via content_hash, D-034 resolved decision #5).
    """
    from kubernetes import client as k8s_client

    from models import KubernetesCluster
    from services.kubernetes_service import KubernetesService

    cluster = db.query(KubernetesCluster).filter(KubernetesCluster.id == cluster_id).first()
    if not cluster:
        raise NotFoundError("cluster", cluster_id)

    k8s_svc = KubernetesService(db)
    api_client = k8s_svc.load_kubeconfig(cluster)
    custom_api = k8s_client.CustomObjectsApi(api_client)

    resources = _fetch_resources(custom_api, _VLAN_RESOURCE_TYPE)
    cr_templates, param_schema = lift_params(resources)
    content_hash = compute_content_hash(cr_templates, param_schema)

    artifact = db.query(UseCaseArtifact).filter(UseCaseArtifact.name == name).first()
    if artifact:
        existing = (
            db.query(UseCaseArtifactVersion)
            .filter(
                UseCaseArtifactVersion.artifact_id == artifact.id,
                UseCaseArtifactVersion.content_hash == content_hash,
            )
            .first()
        )
        if existing:
            logger.info("Use-case artifact '%s' unchanged — already captured as %s", name, existing.version)
            return existing, False
    else:
        artifact = UseCaseArtifact(name=name, created_by=created_by)
        db.add(artifact)
        db.flush()

    new_version = UseCaseArtifactVersion(
        artifact_id=artifact.id,
        version=version,
        matching_bnk_version=matching_bnk_version,
        cr_templates=cr_templates,
        param_schema=param_schema,
        source="captured_from_cluster",
        source_cluster_id=cluster_id,
        content_hash=content_hash,
        created_by=created_by,
    )
    db.add(new_version)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise BadRequestError(
            f"Version '{version}' already exists for artifact '{name}'", code="VERSION_EXISTS"
        ) from exc

    logger.info("Captured use-case artifact '%s' version %s (id=%s)", name, version, new_version.id)
    return new_version, True


def apply_usecase_artifact(
    db: Session,
    cluster: Any,
    version: UseCaseArtifactVersion,
    param_values: dict[str, Any],
    applied_by: str | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], UseCaseApplication]:
    """Render `version` with `param_values` and apply it via the shared write path.

    Records a UseCaseApplication row so drift always compares against the
    exact desired-state that was applied.
    """
    from kubernetes import client as k8s_client

    from services.config_export_service import apply_resources
    from services.kubernetes_service import KubernetesService

    rendered = render(version, param_values)

    k8s_svc = KubernetesService(db)
    api_client = k8s_svc.load_kubeconfig(cluster)
    custom_api = k8s_client.CustomObjectsApi(api_client)

    results = apply_resources(db, cluster.id, custom_api, {"bnk_data_plane": rendered})

    application = UseCaseApplication(
        artifact_version_id=version.id,
        cluster_id=cluster.id,
        param_values=param_values,
        applied_by=applied_by,
    )
    db.add(application)
    db.flush()

    return results, application
