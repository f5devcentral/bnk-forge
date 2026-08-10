"""
Kubernetes namespace topology builder (D-018 P4).

Entry point: build_namespace_topology(k8s_service, cluster_id, namespace)
  — fetches pods/services/workloads via KubernetesService.get_resources
  — delegates to assemble_topology() for pure node+edge construction

assemble_topology() is a PURE function (no I/O) to keep it unit-testable.

Key snake_case notes (to_dict() output from kubernetes-client):
  - metadata.owner_references   (list of {api_version, kind, name, uid, controller, ...})
  - status.ready_replicas       (Deployment/StatefulSet)
  - status.replicas             (Deployment/StatefulSet)
  - status.phase                (Pod)
  - status.conditions           (list of {type, status, ...})
  - spec.selector.match_labels  (Deployment/StatefulSet/DaemonSet)
  - spec.selector               (Service — dict of label→value)
"""

import logging
from typing import Any

from schemas.k8s import TopologyEdge, TopologyGraphResponse, TopologyNode
from services.kubernetes import KubernetesService

logger = logging.getLogger(__name__)

# Guard: do not build a graph for huge namespaces
_POD_TRUNCATION_THRESHOLD = 300


# ---------------------------------------------------------------------------
# Public I/O boundary
# ---------------------------------------------------------------------------

def build_namespace_topology(
    k8s_service: KubernetesService,
    cluster_id: int,
    namespace: str,
) -> TopologyGraphResponse:
    """Fetch all relevant resources then assemble the topology graph.

    Raises BreakerOpenError (503) automatically via @with_breaker on get_resources
    if the cluster is unreachable.  The route just needs @handle_route_errors.
    """
    pods = k8s_service.get_resources(cluster_id, "pod", namespace=namespace)
    services = k8s_service.get_resources(cluster_id, "service", namespace=namespace)
    deployments = k8s_service.get_resources(cluster_id, "deployment", namespace=namespace)
    replicasets = k8s_service.get_resources(cluster_id, "replicaset", namespace=namespace)
    statefulsets = k8s_service.get_resources(cluster_id, "statefulset", namespace=namespace)
    daemonsets = k8s_service.get_resources(cluster_id, "daemonset", namespace=namespace)

    return assemble_topology(
        pods=pods,
        services=services,
        deployments=deployments,
        replicasets=replicasets,
        statefulsets=statefulsets,
        daemonsets=daemonsets,
        cluster_id=cluster_id,
        namespace=namespace,
    )


# ---------------------------------------------------------------------------
# Pure assembly (unit-testable, no I/O)
# ---------------------------------------------------------------------------

def assemble_topology(
    *,
    pods: list[dict[str, Any]],
    services: list[dict[str, Any]],
    deployments: list[dict[str, Any]],
    replicasets: list[dict[str, Any]],
    statefulsets: list[dict[str, Any]],
    daemonsets: list[dict[str, Any]],
    cluster_id: int,
    namespace: str,
) -> TopologyGraphResponse:
    """Build nodes and edges from pre-fetched snake_case resource dicts.

    Edges:
      - Service → Pod  (kind='selects') via label-selector match
      - Workload → Pod (kind='owns')    via ownerReferences chain
        * Pod → ReplicaSet → Deployment: collapse RS, emit Deployment→Pod edge

    Returns TopologyGraphResponse.  Pure — no K8s I/O.
    """
    info: str | None = None
    nodes: list[TopologyNode] = []
    edges: list[TopologyEdge] = []

    # Truncation guard
    if len(pods) > _POD_TRUNCATION_THRESHOLD:
        info = (
            f"Namespace contains {len(pods)} pods — graph truncated to the first "
            f"{_POD_TRUNCATION_THRESHOLD} pods to avoid node explosion."
        )
        pods = pods[:_POD_TRUNCATION_THRESHOLD]

    # Build a UID → Deployment map (collapse ReplicaSet→Deployment)
    # key: replicaset uid → parent Deployment resource dict
    rs_uid_to_deployment: dict[str, dict[str, Any]] = {}
    deployment_uids: set[str] = set()

    for dep in deployments:
        uid = _uid(dep)
        if uid:
            deployment_uids.add(uid)

    for rs in replicasets:
        rs_uid = _uid(rs)
        if not rs_uid:
            continue
        owner_dep = _find_controller_owner(rs, "Deployment")
        if owner_dep:
            # Find the actual Deployment dict by uid so we have full metadata
            dep_dict = next(
                (d for d in deployments if _uid(d) == owner_dep["uid"]),
                None,
            )
            if dep_dict:
                rs_uid_to_deployment[rs_uid] = dep_dict

    # Workload nodes (Deployment, StatefulSet, DaemonSet)
    workload_node_ids: dict[str, str] = {}  # resource uid → node id
    seen_workload_uids: set[str] = set()

    for wl, kind in [
        *[(d, "Deployment") for d in deployments],
        *[(s, "StatefulSet") for s in statefulsets],
        *[(d, "DaemonSet") for d in daemonsets],
    ]:
        uid = _uid(wl)
        node_id = _node_id(kind, _meta_name(wl), _meta_ns(wl, namespace))
        node = TopologyNode(
            id=node_id,
            kind=kind,
            name=_meta_name(wl),
            namespace=_meta_ns(wl, namespace),
            uid=uid or None,
            severity=_workload_severity(wl),
            meta=_workload_meta(wl),
        )
        nodes.append(node)
        if uid and uid not in seen_workload_uids:
            workload_node_ids[uid] = node_id
            seen_workload_uids.add(uid)

    # Service nodes + selects edges
    svc_nodes: list[TopologyNode] = []
    for svc in services:
        svc_name = _meta_name(svc)
        svc_ns = _meta_ns(svc, namespace)
        node_id = _node_id("Service", svc_name, svc_ns)
        node = TopologyNode(
            id=node_id,
            kind="Service",
            name=svc_name,
            namespace=svc_ns,
            uid=_uid(svc) or None,
            severity="unknown",
            meta={},
        )
        svc_nodes.append(node)
        nodes.append(node)

    # Pod nodes + owns/selects edges
    pod_node_ids: dict[str, str] = {}  # pod uid → node id
    for pod in pods:
        pod_name = _meta_name(pod)
        pod_ns = _meta_ns(pod, namespace)
        pod_uid = _uid(pod)
        node_id = _node_id("Pod", pod_name, pod_ns)
        pod_node = TopologyNode(
            id=node_id,
            kind="Pod",
            name=pod_name,
            namespace=pod_ns,
            uid=pod_uid or None,
            severity=_pod_severity(pod),
            meta=_pod_meta(pod),
        )
        nodes.append(pod_node)
        if pod_uid:
            pod_node_ids[pod_uid] = node_id

        # Ownership edges: resolve ownerReferences chain
        owner_ref = _find_controller_owner_raw(pod)
        if owner_ref:
            owner_kind = owner_ref.get("kind", "")
            owner_uid = owner_ref.get("uid", "")

            if owner_kind == "ReplicaSet":
                # Collapse: find the parent Deployment
                dep_dict = rs_uid_to_deployment.get(owner_uid)
                if dep_dict:
                    dep_uid = _uid(dep_dict)
                    workload_nid = workload_node_ids.get(dep_uid or "")
                    if workload_nid:
                        edges.append(_edge(workload_nid, node_id, "owns"))
                # else: standalone RS (no parent) — skip; no workload node for it
            elif owner_kind in ("StatefulSet", "DaemonSet"):
                workload_nid = workload_node_ids.get(owner_uid)
                if workload_nid:
                    edges.append(_edge(workload_nid, node_id, "owns"))
            # Other owner kinds (Job, etc.) — out of scope; skip

    # Service → Pod selector edges
    pod_label_index = _build_pod_label_index(pods, namespace)

    for svc, svc_node in zip(services, svc_nodes):
        selector = _svc_selector(svc)
        if not selector:
            continue
        matched_pods = _match_pods_by_selector(selector, pod_label_index)
        for matched_pod_ns_name in matched_pods:
            target_nid = _node_id("Pod", *matched_pod_ns_name)
            edges.append(TopologyEdge(
                id=f"selects/{svc_node.id}/{target_nid}",
                source=svc_node.id,
                target=target_nid,
                kind="selects",
            ))

    return TopologyGraphResponse(
        nodes=nodes,
        edges=edges,
        cluster_id=cluster_id,
        namespace=namespace,
        info=info,
    )


# ---------------------------------------------------------------------------
# Helpers — snake_case key navigation
# ---------------------------------------------------------------------------

def _uid(r: dict[str, Any]) -> str | None:
    return (r.get("metadata") or {}).get("uid")


def _meta_name(r: dict[str, Any]) -> str:
    return (r.get("metadata") or {}).get("name", "")


def _meta_ns(r: dict[str, Any], fallback: str) -> str:
    return (r.get("metadata") or {}).get("namespace") or fallback


def _node_id(kind: str, name: str, namespace: str) -> str:
    return f"{kind}/{namespace}/{name}"


def _edge(source: str, target: str, kind: str) -> TopologyEdge:
    return TopologyEdge(
        id=f"{kind}/{source}/{target}",
        source=source,
        target=target,
        kind=kind,
    )


def _find_controller_owner_raw(r: dict[str, Any]) -> dict[str, Any] | None:
    """Return the ownerReference with controller==True, or None."""
    refs = (r.get("metadata") or {}).get("owner_references") or []
    for ref in refs:
        if ref.get("controller"):
            return ref
    return None


def _find_controller_owner(r: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """Return the ownerReference with controller==True and matching kind, or None."""
    ref = _find_controller_owner_raw(r)
    if ref and ref.get("kind") == kind:
        return ref
    return None


# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------

def _pod_severity(pod: dict[str, Any]) -> str:
    status = pod.get("status") or {}
    phase = status.get("phase", "")
    if phase == "Running":
        # Check the Ready condition
        conditions = status.get("conditions") or []
        for c in conditions:
            if c.get("type") == "Ready":
                return "healthy" if c.get("status") == "True" else "degraded"
        return "unknown"
    if phase in ("Pending", "Unknown"):
        return "degraded"
    if phase == "Failed":
        return "unhealthy"
    return "unknown"


def _workload_severity(wl: dict[str, Any]) -> str:
    status = wl.get("status") or {}
    replicas = status.get("replicas")
    ready = status.get("ready_replicas")
    if replicas is None:
        return "unknown"
    if replicas == 0:
        return "unknown"
    if ready is None:
        return "degraded"
    if ready == replicas:
        return "healthy"
    if ready == 0:
        return "unhealthy"
    return "degraded"


# ---------------------------------------------------------------------------
# Meta helpers
# ---------------------------------------------------------------------------

def _pod_meta(pod: dict[str, Any]) -> dict[str, Any]:
    status = pod.get("status") or {}
    spec = pod.get("spec") or {}
    containers = spec.get("containers") or []
    return {
        "phase": status.get("phase"),
        "container_count": len(containers),
    }


def _workload_meta(wl: dict[str, Any]) -> dict[str, Any]:
    status = wl.get("status") or {}
    return {
        "replicas": status.get("replicas"),
        "ready_replicas": status.get("ready_replicas"),
    }


# ---------------------------------------------------------------------------
# Label-selector matching
# ---------------------------------------------------------------------------

def _svc_selector(svc: dict[str, Any]) -> dict[str, str] | None:
    """Return the Service selector dict, or None if there is no selector."""
    spec = svc.get("spec") or {}
    selector = spec.get("selector")
    if not selector or not isinstance(selector, dict):
        return None
    return selector  # type: ignore[return-value]


def _build_pod_label_index(
    pods: list[dict[str, Any]],
    fallback_ns: str,
) -> list[tuple[str, str, dict[str, str]]]:
    """Return list of (namespace, name, labels) for all pods."""
    result = []
    for pod in pods:
        meta = pod.get("metadata") or {}
        ns = meta.get("namespace") or fallback_ns
        name = meta.get("name", "")
        labels: dict[str, str] = meta.get("labels") or {}
        result.append((ns, name, labels))
    return result


def _match_pods_by_selector(
    selector: dict[str, str],
    pod_label_index: list[tuple[str, str, dict[str, str]]],
) -> list[tuple[str, str]]:
    """Return (name, namespace) tuples for pods whose labels include all selector entries."""
    matched = []
    for ns, name, labels in pod_label_index:
        if all(labels.get(k) == v for k, v in selector.items()):
            matched.append((name, ns))
    return matched
