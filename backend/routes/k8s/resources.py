"""
Kubernetes Resource Management routes.

Endpoints for generic K8s resource operations:
  - Resource CRUD (create, read, update, delete, patch)
  - Labels and annotations
  - Pod operations (logs, restart, containers)
  - Deployment operations (scale, rollout history/status/undo/restart)
  - Node operations (cordon, uncordon, drain)
  - Metrics (pod top, node top)
  - Events and describe
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from fastapi import APIRouter, Depends, Query
from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from sqlalchemy.orm import Session

from core.errors import AppError, handle_route_errors
from database import get_db
from models import User
from routes.auth import require_cluster_owner, require_viewer
from routes.k8s._shared import (
    AnnotateResourceRequest,
    LabelResourceRequest,
    ResourceCreateRequest,
    ResourcePatchRequest,
    ResourceUpdateRequest,
    ScaleDeploymentRequest,
)
from schemas.k8s import (
    ClusterEventsResponse,
    NodeTopResponse,
    PodLogsResponse,
    PodRestartResponse,
    PodTopResponse,
    ResourceDescribeEnvelope,
    ResourceListEnvelope,
)
from services.kubernetes._resources import API_GROUP_CLIENTS, resolve_resource_type
from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-resources"])


# ============================================================================
# Generic Resource Read
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/resources/{resource_type}",
    response_model=ResourceListEnvelope,
    # TODO(authz): Post gate-lift (D-018 P1.5), viewers can list instances of
    # any installed CRD — not just curated registry kinds.  Revisit role-gating
    # for non-curated CR instance reads when true multi-user / tenant isolation
    # lands (ref: PR #125 review, D-018 §Risks).
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch resources")
def get_cluster_resources(
    cluster_id: int,
    resource_type: str,
    namespace: str | None = None,
    label_selector: str | None = None,
    group: str | None = Query(default=None, description="API group to disambiguate bare plural CRD names (e.g. k8s.f5.com)"),
    db: Session = Depends(get_db)
):
    """
    Generic endpoint to get any Kubernetes resource type.
    Works with all cloud providers (EKS, AKS, GKE, on-prem).

    For unregistered CRDs, resolution falls back to live cluster discovery.
    Use the optional ``?group=`` parameter to disambiguate when the same plural
    exists in multiple API groups (e.g. ``analyzers`` on ``k8s.f5.com``).
    Note: ``?group=`` here is single-group disambiguation for an ambiguous
    bare-plural CRD name — it is not a multi-value filter.  Contrast with
    ``GET /crds?group=`` which accepts multiple group values.

    Examples:
    - /k8s/clusters/1/resources/service?namespace=default
    - /k8s/clusters/1/resources/pod?namespace=kube-system
    - /k8s/clusters/1/resources/analyzers.k8s.f5.com  (FQ name, no ambiguity)
    - /k8s/clusters/1/resources/analyzers?group=k8s.f5.com
    """
    try:
        k8s_service = KubernetesService(db)
        resources = k8s_service.get_resources(
            cluster_id,
            resource_type,
            namespace,
            label_selector,
            group=group,
        )

        return {
            "resources": resources,
            "count": len(resources),
            "resource_type": resource_type,
            "namespace": namespace,
            "cluster_id": cluster_id
        }

    except ApiException as e:
        if e.status == 404:
            # CRD not installed or resource type doesn't exist in cluster
            logger.warning(f"Resource type {resource_type} not found in cluster {cluster_id}: {e.reason}")
            return {
                "resources": [],
                "count": 0,
                "resource_type": resource_type,
                "namespace": namespace,
                "cluster_id": cluster_id,
                "info": f"Resource type '{resource_type}' not available in this cluster. The required CRDs may not be installed."
            }
        else:
            logger.error(f"K8s API error fetching {resource_type}: {e}")
            raise AppError(code="K8S_API_ERROR", message=str(e), status_code=e.status)


# ============================================================================
# Resource Summary — always-on counts + health per kind for sidebar badges
# ============================================================================

# Resource kinds fetched by /resource-summary. Intentionally excludes
# "bulky" kinds (configmap, secret, customresourcedefinition) because their
# list responses can be tens of MB on real clusters — 15MB+ for configmaps
# on one OCP cluster we tested against — and flaky connections cause urllib3
# retries that stretch the whole request into minutes. Those kinds still
# load on-demand when the user clicks into them in the sidebar; the badge
# just stays count-less.
_RESOURCE_SUMMARY_KINDS: tuple[str, ...] = (
    # Workloads — all health-checkable
    "pod", "deployment", "statefulset", "daemonset", "replicaset",
    # Networking
    "service", "ingress",
    # Gateway API — small payloads
    "gatewayclass", "gateway", "httproute", "grpcroute",
    "tcproute", "udproute", "tlsroute", "referencegrant",
    # Storage (kept — PVC/SC/PV are small)
    "persistentvolumeclaim", "storageclass", "persistentvolume",
    # Cluster
    "node", "namespace",
    # cert-manager — small
    "certificate", "clusterissuer", "issuer",
)

# Per-kind list timeout. Keeps a flaky CRD from blocking the whole request.
_KIND_LIST_TIMEOUT_SEC = 10

# Kinds where "unhealthy" is semantically meaningful. Others return
# `unhealthy: null` — the sidebar shows a count-only badge for them.
_HEALTH_CHECKABLE_KINDS: frozenset[str] = frozenset({
    "pod", "deployment", "statefulset", "daemonset", "replicaset",
})


# API classes keyed by api_group — imported from the single source of truth.
_CORE_API_CLASSES = API_GROUP_CLIENTS


def _kind_to_snake(kind: str) -> str:
    """Convert CamelCase kind (e.g. PersistentVolumeClaim) to snake_case."""
    return ''.join(['_' + c.lower() if c.isupper() else c for c in kind]).lstrip('_')


def _unhealthy_from_native(kind: str, items: list) -> int | None:
    """
    Count unhealthy items from native kubernetes client objects (V1Pod,
    V1Deployment, etc.). Avoids the expensive to_dict() round-trip.

    Mirrors the frontend health logic in pages/kubernetes/k8s-status.ts so
    the badge and detail view agree.
    """
    if kind not in _HEALTH_CHECKABLE_KINDS:
        return None

    unhealthy = 0
    for item in items:
        status = getattr(item, "status", None)
        spec = getattr(item, "spec", None)

        if kind == "pod":
            phase = getattr(status, "phase", None) if status else None
            # Succeeded pods (finished Jobs) aren't unhealthy; Running is healthy.
            if phase not in ("Running", "Succeeded"):
                unhealthy += 1

        elif kind in ("deployment", "statefulset", "replicaset"):
            desired = getattr(spec, "replicas", None) if spec else None
            ready = (getattr(status, "ready_replicas", None) or 0) if status else 0
            # A resource scaled to 0 is intentional, not unhealthy.
            if desired is not None and desired > 0 and ready < desired:
                unhealthy += 1

        elif kind == "daemonset":
            desired = (getattr(status, "desired_number_scheduled", None) or 0) if status else 0
            ready = (getattr(status, "number_ready", None) or 0) if status else 0
            if desired > 0 and ready < desired:
                unhealthy += 1

    return unhealthy


def _unhealthy_from_dicts(kind: str, items: list[dict]) -> int | None:
    """
    Unhealthy count path for custom resources (already dicts from the
    CustomObjectsApi response). Only used when a health-checkable kind
    turns out to be a CRD — in practice, none of ours are.
    """
    if kind not in _HEALTH_CHECKABLE_KINDS:
        return None
    return 0  # CRDs shouldn't hit this path today; return 0 defensively.


def _fetch_kind_summary(
    api_client: k8s_client.ApiClient,
    kind: str,
    namespace: str | None,
    db=None,
    cluster_id: int | None = None,
) -> dict:
    """
    Fetch count + unhealthy for a single kind. Never raises.

    Bypasses KubernetesService._fetch_from_k8s because that path calls
    to_dict() on every item, which is O(items × fields) and dominates
    latency for big collections (e.g. 571 pods → minutes). For the sidebar
    summary we only need a length and a few status fields, so we walk the
    native V1* objects directly.

    Expects the api_client to have been loaded once by the caller on the
    main thread — load_kubeconfig mutates global k8s client state and is
    not safe to call from worker threads.
    """
    _t0 = time.monotonic()
    try:
        rt = resolve_resource_type(db, cluster_id, kind)

        needs_health = kind in _HEALTH_CHECKABLE_KINDS

        if rt.api_group and rt.api_group not in _CORE_API_CLASSES:
            # Custom resource (CRD) — response items are already plain dicts.
            # CRDs we list here are all small (Gateway API, cert-manager), so
            # a full list is fine.
            custom = k8s_client.CustomObjectsApi(api_client)
            if rt.namespaced and namespace:
                response = custom.list_namespaced_custom_object(
                    group=rt.api_group,
                    version=rt.api_version,
                    namespace=namespace,
                    plural=rt.plural,
                    _request_timeout=_KIND_LIST_TIMEOUT_SEC,
                )
            else:
                response = custom.list_cluster_custom_object(
                    group=rt.api_group,
                    version=rt.api_version,
                    plural=rt.plural,
                    _request_timeout=_KIND_LIST_TIMEOUT_SEC,
                )
            items = response.get("items", []) if isinstance(response, dict) else []
            count = len(items)
            unhealthy = _unhealthy_from_dicts(kind, items)
        else:
            # Core / apps / networking — use the typed client, inspect natively.
            api_class = _CORE_API_CLASSES[rt.api_group]
            api_instance = api_class(api_client)
            method_base = _kind_to_snake(rt.kind)

            if rt.namespaced:
                if namespace:
                    method_name = f"list_namespaced_{method_base}"
                else:
                    method_name = f"list_{method_base}_for_all_namespaces"
            else:
                method_name = f"list_{method_base}"

            list_method = getattr(api_instance, method_name, None)
            if list_method is None:
                logger.warning(f"resource-summary: no list method {method_name} on {api_class.__name__}")
                return {"count": 0, "unhealthy": None, "available": False}

            list_kwargs: dict = {"_request_timeout": _KIND_LIST_TIMEOUT_SEC}
            if rt.namespaced and namespace:
                list_kwargs["namespace"] = namespace

            if kind == "pod":
                # Pods are the biggest list by far (5–20KB each × hundreds).
                # Skip the full fetch: limit=1 + remaining_item_count gives us
                # total, and a field_selector for non-Running+non-Succeeded
                # pods gives us unhealthy directly (the unhealthy set is
                # typically tiny — a dozen items max on healthy clusters).
                count_kwargs = {**list_kwargs, "limit": 1}
                count_resp = list_method(**count_kwargs)
                meta = getattr(count_resp, "metadata", None)
                remaining = getattr(meta, "remaining_item_count", None) if meta else None
                if remaining is not None:
                    count = len(count_resp.items or []) + int(remaining)
                else:
                    # Fall back to full list only if the server elided
                    # remaining_item_count (rare). Still cheaper than fetching
                    # twice most of the time.
                    full = list_method(**list_kwargs)
                    count = len(full.items or [])

                unhealthy_kwargs = {
                    **list_kwargs,
                    "field_selector": "status.phase!=Running,status.phase!=Succeeded",
                }
                unhealthy_resp = list_method(**unhealthy_kwargs)
                unhealthy = len(unhealthy_resp.items or [])

            elif needs_health:
                # Deployments / StatefulSets / DaemonSets / ReplicaSets:
                # no field selectors for "not ready", and these collections
                # are much smaller than pods, so a full list is acceptable.
                # Native V1 objects are cheap to walk — we just skip to_dict.
                response = list_method(**list_kwargs)
                items = response.items or []
                count = len(items)
                unhealthy = _unhealthy_from_native(kind, items)
            else:
                # Count-only kinds: ask the API for just 1 item + the
                # remaining-item-count metadata. Turns a multi-MB list into
                # a tiny response. If the API server doesn't populate
                # remaining_item_count (it may omit it on the last page or
                # for small collections), fall back to a full list so the
                # badge stays accurate.
                list_kwargs["limit"] = 1
                response = list_method(**list_kwargs)
                items = response.items or []
                meta = getattr(response, "metadata", None)
                remaining = getattr(meta, "remaining_item_count", None) if meta else None
                if remaining is not None:
                    count = len(items) + int(remaining)
                else:
                    # Either the collection fits in one page (count = len(items))
                    # or the server elided the field. For safety, only trust
                    # the short-circuit when we saw fewer items than the limit.
                    if len(items) < 1:
                        count = 0
                    else:
                        # Fall back to a full list to get an accurate count.
                        list_kwargs.pop("limit", None)
                        response = list_method(**list_kwargs)
                        items = response.items or []
                        count = len(items)
                unhealthy = None

        _dt = time.monotonic() - _t0
        if _dt > 2.0:
            logger.info(f"resource-summary: {kind} took {_dt:.2f}s ({count} items)")
        return {"count": count, "unhealthy": unhealthy, "available": True}

    except ApiException as e:
        if e.status == 404:
            return {"count": 0, "unhealthy": _unhealthy_from_native(kind, []), "available": False}
        logger.warning(f"resource-summary: {kind} failed: {e}")
        return {"count": 0, "unhealthy": None, "available": False, "error": str(e)}
    except Exception as e:
        logger.warning(f"resource-summary: {kind} failed: {e}")
        return {"count": 0, "unhealthy": None, "available": False, "error": str(e)}


# Per (cluster, namespace) TTL cache for resource-summary responses.
# TTL must exceed typical request duration — otherwise the entry is already
# stale by the time it's written (we had exactly that bug with TTL=20 vs a
# 30s fetch). 60s leaves room for slow fetches while still refreshing
# frequently enough for the sidebar to feel live.
_RESOURCE_SUMMARY_TTL_SEC = 60.0
_resource_summary_cache: dict[tuple[int, str | None], tuple[float, dict]] = {}
_resource_summary_lock = Lock()


@router.get(
    "/k8s/clusters/{cluster_id}/resource-summary",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch resource summary")
def get_cluster_resource_summary(
    cluster_id: int,
    namespace: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Return per-kind counts + unhealthy counts for the K8s sidebar badges.

    Parallel-fetches a fixed set of kinds so the sidebar can always show
    counts without requiring the user to click into each type. Unhealthy
    counts are only computed for kinds where health is meaningful
    (Pods / Deployments / DaemonSets / StatefulSets / ReplicaSets); others
    return `unhealthy: null` so the frontend can render count-only badges.

    `namespace=all` or `namespace=None` returns cluster-wide counts.

    Cached per (cluster, namespace) for ~20s to match the sidebar's polling
    cadence without re-hitting the k8s API on every render.
    """
    effective_ns = None if (namespace in (None, "", "all")) else namespace
    cache_key = (cluster_id, effective_ns)

    now_mono = time.monotonic()
    with _resource_summary_lock:
        cached = _resource_summary_cache.get(cache_key)
        if cached and (now_mono - cached[0]) < _RESOURCE_SUMMARY_TTL_SEC:
            return cached[1]

    k8s_service = KubernetesService(db)
    # Load kubeconfig ONCE on the main thread — load_kubeconfig mutates
    # global k8s client state and racing it across workers causes the 60s+
    # latency we saw before this refactor.
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    summary: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=min(10, len(_RESOURCE_SUMMARY_KINDS))) as executor:
        futures = {
            executor.submit(_fetch_kind_summary, api_client, kind, effective_ns, db, cluster_id): kind
            for kind in _RESOURCE_SUMMARY_KINDS
        }
        for future in as_completed(futures):
            kind = futures[future]
            try:
                summary[kind] = future.result(timeout=15)
            except Exception as e:
                logger.warning(f"resource-summary: {kind} future failed: {e}")
                summary[kind] = {"count": 0, "unhealthy": None, "available": False, "error": str(e)}

    response = {
        "cluster_id": cluster_id,
        "namespace": namespace,
        "summary": summary,
    }

    # Stamp the cache entry with the write time, not the request start —
    # otherwise a 30s fetch burns most of its own TTL before it's even stored.
    write_mono = time.monotonic()
    with _resource_summary_lock:
        # Opportunistic stale-entry eviction so the cache can't grow unbounded.
        stale_cutoff = write_mono - _RESOURCE_SUMMARY_TTL_SEC
        for key in [k for k, (t, _) in _resource_summary_cache.items() if t < stale_cutoff]:
            _resource_summary_cache.pop(key, None)
        _resource_summary_cache[cache_key] = (write_mono, response)

    return response


# ============================================================================
# Resource Write Operations (CRUD)
# ============================================================================

@router.post("/k8s/clusters/{cluster_id}/resources/{resource_type}")
@handle_route_errors("create resource")
def create_resource(
    cluster_id: int,
    resource_type: str,
    request: ResourceCreateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Create a new Kubernetes resource from YAML.
    Supports dry-run mode to preview changes without applying.
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.create_resource(
        cluster_id=cluster_id,
        resource_type_key=resource_type,
        resource_yaml=request.resource_yaml,
        namespace=request.namespace,
        dry_run=request.dry_run
    )
    return result


@router.put("/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}")
@handle_route_errors("update resource")
def update_resource(
    cluster_id: int,
    resource_type: str,
    resource_name: str,
    request: ResourceUpdateRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Update an existing Kubernetes resource from YAML.
    Supports dry-run mode to preview changes without applying.
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.update_resource(
        cluster_id=cluster_id,
        resource_type_key=resource_type,
        resource_name=resource_name,
        resource_yaml=request.resource_yaml,
        namespace=request.namespace,
        dry_run=request.dry_run
    )
    return result


@router.delete("/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}")
@handle_route_errors("delete resource")
def delete_resource(
    cluster_id: int,
    resource_type: str,
    resource_name: str,
    namespace: str | None = None,
    dry_run: bool = False,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Delete a Kubernetes resource.
    Supports dry-run mode to preview changes without applying.
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.delete_resource(
        cluster_id=cluster_id,
        resource_type_key=resource_type,
        resource_name=resource_name,
        namespace=namespace,
        dry_run=dry_run
    )
    return result


@router.patch("/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}")
@handle_route_errors("patch resource")
def patch_resource(
    cluster_id: int,
    resource_type: str,
    resource_name: str,
    request: ResourcePatchRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Patch a Kubernetes resource (partial update).
    Equivalent to: kubectl patch {resource_type} {resource_name} --patch '{json}'

    Supports three patch strategies:
        - strategic: Strategic merge patch (default, Kubernetes-native)
        - merge: JSON merge patch (RFC 7386)
        - json: JSON patch (RFC 6902)
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.patch_resource(
        cluster_id=cluster_id,
        resource_type_key=resource_type,
        resource_name=resource_name,
        patch_data=request.patch_data,
        namespace=request.namespace,
        patch_type=request.patch_type
    )
    return result


# ============================================================================
# Labels and Annotations
# ============================================================================

@router.post("/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/label")
@handle_route_errors("label resource")
def label_resource(
    cluster_id: int,
    resource_type: str,
    resource_name: str,
    request: LabelResourceRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Add or update labels on a resource.
    Equivalent to: kubectl label {resource_type} {resource_name} key=value
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.label_resource(
        cluster_id=cluster_id,
        resource_type_key=resource_type,
        resource_name=resource_name,
        labels=request.labels,
        namespace=request.namespace,
        overwrite=request.overwrite
    )
    return result


@router.post("/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/annotate")
@handle_route_errors("annotate resource")
def annotate_resource(
    cluster_id: int,
    resource_type: str,
    resource_name: str,
    request: AnnotateResourceRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Add or update annotations on a resource.
    Equivalent to: kubectl annotate {resource_type} {resource_name} key=value
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.annotate_resource(
        cluster_id=cluster_id,
        resource_type_key=resource_type,
        resource_name=resource_name,
        annotations=request.annotations,
        namespace=request.namespace,
        overwrite=request.overwrite
    )
    return result


# ============================================================================
# Deployment Operations
# ============================================================================

@router.post("/k8s/clusters/{cluster_id}/deployments/{deployment_name}/scale")
@handle_route_errors("scale deployment")
def scale_deployment(
    cluster_id: int,
    deployment_name: str,
    request: ScaleDeploymentRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """Scale a deployment to a specific number of replicas."""
    k8s_service = KubernetesService(db)
    result = k8s_service.scale_deployment(
        cluster_id=cluster_id,
        deployment_name=deployment_name,
        namespace=request.namespace,
        replicas=request.replicas
    )
    return result


# ============================================================================
# Pod Operations
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/pods/{pod_name}/logs",
    response_model=PodLogsResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get pod logs")
def get_pod_logs(
    cluster_id: int,
    pod_name: str,
    namespace: str,
    container: str | None = None,
    tail_lines: int = 100,
    db: Session = Depends(get_db)
):
    """
    Get logs from a pod.
    Use tail_lines to limit the number of log lines returned.
    """
    k8s_service = KubernetesService(db)
    logs = k8s_service.get_pod_logs(
        cluster_id=cluster_id,
        pod_name=pod_name,
        namespace=namespace,
        container=container,
        tail_lines=tail_lines,
        follow=False  # Don't use follow in HTTP endpoint (use WebSocket for streaming)
    )
    return {
        "logs": logs,
        "pod_name": pod_name,
        "namespace": namespace,
        "cluster_id": cluster_id,
        "container": container,
    }


@router.post("/k8s/clusters/{cluster_id}/pods/{pod_name}/restart", response_model=PodRestartResponse)
@handle_route_errors("restart pod")
def restart_pod(
    cluster_id: int,
    pod_name: str,
    namespace: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Restart a pod by deleting it.
    The pod will be recreated automatically by its controller (Deployment, StatefulSet, etc.).
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.restart_pod(
        cluster_id=cluster_id,
        pod_name=pod_name,
        namespace=namespace
    )
    return {
        **result,
        "pod_name": pod_name,
        "namespace": namespace,
        "cluster_id": cluster_id,
    }


@router.get("/k8s/clusters/{cluster_id}/pods/{pod_name}/containers", dependencies=[Depends(require_viewer)])
@handle_route_errors("list pod containers")
def list_pod_containers(
    cluster_id: int,
    pod_name: str,
    namespace: str,
    db: Session = Depends(get_db)
):
    """List all containers in a pod (including init containers)."""
    k8s_service = KubernetesService(db)
    containers = k8s_service.get_pod_containers(
        cluster_id=cluster_id,
        pod_name=pod_name,
        namespace=namespace
    )
    return containers


# ============================================================================
# Describe & Events
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/describe",
    response_model=ResourceDescribeEnvelope,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("describe resource")
def describe_resource(
    cluster_id: int,
    resource_type: str,
    resource_name: str,
    namespace: str | None = None,
    db: Session = Depends(get_db)
):
    """
    Describe a resource - kubectl describe equivalent.
    Returns detailed information including status, conditions, events, and relationships.
    """
    k8s_service = KubernetesService(db)
    description = k8s_service.describe_resource(
        cluster_id=cluster_id,
        resource_type_key=resource_type,
        resource_name=resource_name,
        namespace=namespace
    )
    return {
        **description,
        "cluster_id": cluster_id,
        "resource_type": resource_type,
        "resource_name": resource_name,
    }


@router.get(
    "/k8s/clusters/{cluster_id}/events",
    response_model=ClusterEventsResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get cluster events")
def get_cluster_events(
    cluster_id: int,
    namespace: str | None = None,
    resource_type: str | None = None,
    resource_name: str | None = None,
    event_type: str | None = None,
    db: Session = Depends(get_db)
):
    """
    Get events from cluster.
    Supports filtering by namespace, resource (type + name), and event type (Normal/Warning/Error).
    """
    k8s_service = KubernetesService(db)
    events = k8s_service.get_events(
        cluster_id=cluster_id,
        namespace=namespace,
        resource_type=resource_type,
        resource_name=resource_name,
        event_type=event_type
    )
    return {
        "events": events,
        "count": len(events),
        "cluster_id": cluster_id,
        "namespace": namespace,
        "resource_type": resource_type,
        "resource_name": resource_name,
        "event_type": event_type,
    }


# ============================================================================
# Metrics (kubectl top)
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/top/pods",
    response_model=PodTopResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get pod metrics")
def get_pod_metrics(
    cluster_id: int,
    namespace: str | None = None,
    sort_by: str | None = None,
    db: Session = Depends(get_db)
):
    """
    Get pod resource usage metrics (CPU and memory).
    Equivalent to: kubectl top pods

    Requires metrics-server to be installed in the cluster.
    """
    k8s_service = KubernetesService(db)
    metrics = k8s_service.get_pod_metrics(
        cluster_id=cluster_id,
        namespace=namespace,
        sort_by=sort_by
    )
    return {
        **metrics,
        "cluster_id": cluster_id,
        "namespace": namespace,
        "sort_by": sort_by,
    }


@router.get(
    "/k8s/clusters/{cluster_id}/top/nodes",
    response_model=NodeTopResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get node metrics")
def get_node_metrics(
    cluster_id: int,
    sort_by: str | None = None,
    db: Session = Depends(get_db)
):
    """
    Get node resource usage metrics (CPU and memory).
    Equivalent to: kubectl top nodes

    Requires metrics-server to be installed in the cluster.
    """
    k8s_service = KubernetesService(db)
    metrics = k8s_service.get_node_metrics(
        cluster_id=cluster_id,
        sort_by=sort_by
    )
    return {
        **metrics,
        "cluster_id": cluster_id,
        "sort_by": sort_by,
    }


# ============================================================================
# Deployment Rollout Management
# ============================================================================

@router.get("/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/history", dependencies=[Depends(require_viewer)])
@handle_route_errors("get rollout history")
def get_rollout_history(
    cluster_id: int,
    deployment_name: str,
    namespace: str,
    db: Session = Depends(get_db)
):
    """
    Get rollout history for a deployment.
    Equivalent to: kubectl rollout history deployment/{name}

    Returns list of revisions with change causes and metadata.
    """
    k8s_service = KubernetesService(db)
    history = k8s_service.get_rollout_history(
        cluster_id=cluster_id,
        deployment_name=deployment_name,
        namespace=namespace
    )
    return {"history": history, "deployment_name": deployment_name, "namespace": namespace}


@router.get("/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/status", dependencies=[Depends(require_viewer)])
@handle_route_errors("get rollout status")
def get_rollout_status(
    cluster_id: int,
    deployment_name: str,
    namespace: str,
    db: Session = Depends(get_db)
):
    """
    Get current rollout status for a deployment.
    Equivalent to: kubectl rollout status deployment/{name}

    Returns deployment status with replicas and conditions.
    """
    k8s_service = KubernetesService(db)
    status = k8s_service.get_rollout_status(
        cluster_id=cluster_id,
        deployment_name=deployment_name,
        namespace=namespace
    )
    return status


@router.post("/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/undo")
@handle_route_errors("rollback deployment")
def rollout_undo(
    cluster_id: int,
    deployment_name: str,
    namespace: str,
    revision: int | None = None,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Rollback deployment to previous or specific revision.
    Equivalent to: kubectl rollout undo deployment/{name} [--to-revision=N]
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.rollout_undo(
        cluster_id=cluster_id,
        deployment_name=deployment_name,
        namespace=namespace,
        revision=revision
    )
    return result


@router.post("/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/restart")
@handle_route_errors("restart deployment")
def rollout_restart(
    cluster_id: int,
    deployment_name: str,
    namespace: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Restart a deployment by recreating all pods.
    Equivalent to: kubectl rollout restart deployment/{name}

    This triggers a rolling restart of all pods in the deployment.
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.rollout_restart(
        cluster_id=cluster_id,
        deployment_name=deployment_name,
        namespace=namespace
    )
    return result


@router.post("/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/pause")
@handle_route_errors("pause deployment rollout")
def rollout_pause(
    cluster_id: int,
    deployment_name: str,
    namespace: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Pause a deployment rollout.
    Equivalent to: kubectl rollout pause deployment/{name}

    This prevents the deployment controller from progressing the rollout.
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.rollout_pause(
        cluster_id=cluster_id,
        deployment_name=deployment_name,
        namespace=namespace
    )
    return result


@router.post("/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/resume")
@handle_route_errors("resume deployment rollout")
def rollout_resume(
    cluster_id: int,
    deployment_name: str,
    namespace: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Resume a paused deployment rollout.
    Equivalent to: kubectl rollout resume deployment/{name}

    This allows the deployment controller to continue the rollout.
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.rollout_resume(
        cluster_id=cluster_id,
        deployment_name=deployment_name,
        namespace=namespace
    )
    return result


# ============================================================================
# Node Operations
# ============================================================================

@router.post("/k8s/clusters/{cluster_id}/nodes/{node_name}/cordon")
@handle_route_errors("cordon node")
def cordon_node(
    cluster_id: int,
    node_name: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Mark node as unschedulable (cordon).
    Equivalent to: kubectl cordon {node_name}

    This prevents new pods from being scheduled on the node.
    Existing pods continue running.
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.cordon_node(
        cluster_id=cluster_id,
        node_name=node_name
    )
    return result


@router.post("/k8s/clusters/{cluster_id}/nodes/{node_name}/uncordon")
@handle_route_errors("uncordon node")
def uncordon_node(
    cluster_id: int,
    node_name: str,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Mark node as schedulable (uncordon).
    Equivalent to: kubectl uncordon {node_name}

    This allows new pods to be scheduled on the node again.
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.uncordon_node(
        cluster_id=cluster_id,
        node_name=node_name
    )
    return result


@router.post("/k8s/clusters/{cluster_id}/nodes/{node_name}/drain")
@handle_route_errors("drain node")
def drain_node(
    cluster_id: int,
    node_name: str,
    ignore_daemonsets: bool = True,
    delete_emptydir_data: bool = False,
    force: bool = False,
    grace_period: int = -1,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Drain node by evicting all pods.
    Equivalent to: kubectl drain {node_name}

    This cordons the node and evicts all pods.
    Used for node maintenance or decommissioning.

    Query parameters:
        - ignore_daemonsets: Skip DaemonSet pods (default: True)
        - delete_emptydir_data: Allow deleting pods with emptyDir (default: False)
        - force: Force eviction even with local data (default: False)
        - grace_period: Grace period in seconds for termination (-1 = default)
    """
    k8s_service = KubernetesService(db)
    result = k8s_service.drain_node(
        cluster_id=cluster_id,
        node_name=node_name,
        ignore_daemonsets=ignore_daemonsets,
        delete_emptydir_data=delete_emptydir_data,
        force=force,
        grace_period=grace_period
    )
    return result
