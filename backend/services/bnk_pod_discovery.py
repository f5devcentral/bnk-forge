"""
BNK Pod Discovery — shared utility for finding F5 BNK pods.

Used by:
  - ClusterScanner (upgrade pre-checks, cluster scan)
  - f5bnk.py health dashboard endpoint

Two-phase discovery:
  1. Fast path: query known BNK namespaces in parallel (~1-2s)
  2. Cluster-wide sweep: list_pod_for_all_namespaces filtered by BNK
     signals (name prefix OR label hints) to catch F5 components
     deployed in non-standard namespaces.

Phase 2 is cheap because the K8s API does the filtering server-side
and we de-duplicate pods already found in phase 1.

Role resolution is layered (highest-priority first):
  1. Label hints  — verified labels emitted by F5 charts (e.g. app=f5-tmm)
  2. Container hints — container name / image substring matches
  3. Name-prefix fallback — ALL_F5_PREFIXES / TENANT_PREFIXES / UTILS_PREFIXES

The prefix tuples are retained as the lowest-priority fallback and are
not deleted (gated on live-label validation per D-019 deletion test).
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from kubernetes import client as k8s_client

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# Known BNK namespaces — queried in parallel (fast path)
# -----------------------------------------------------------------------
# Includes "default" so manual/test deployments are discovered too.

BNK_NAMESPACES = ("f5-bnk", "f5-operator", "f5-utils", "default")

# All F5 pod name prefixes — used for cluster-wide sweep (fallback)
ALL_F5_PREFIXES = (
    # Tenant (core data/control plane)
    "f5-tmm",
    "flo-f5-lifecycle",
    "f5-lifecycle",
    "f5-cne-controller",
    "f5ingress",
    "f5-analyzer",
    "f5-afm",
    "f5-observer",
    "otel-collector",
    # Utils (infrastructure / one-shot jobs)
    "crd-installer",
    "f5-coremond",
    "f5-crdconversion",
    "f5-dssm",
    "f5-ipam",
    "f5-rabbit",
    "f5-spk-",
    "spk-csrc",
    "f5-toda",
    "f5-cwc",
    "f5-spk-cwc",
)

# -----------------------------------------------------------------------
# Pod name prefixes for classification (lowest-priority fallback)
# -----------------------------------------------------------------------

# "Tenant" pods — the core BNK data/control plane
TENANT_PREFIXES = (
    "f5-tmm",
    "flo-f5-lifecycle",
    "f5-lifecycle",
    "f5-cne-controller",
    "f5ingress",
    "f5-analyzer",
    "f5-afm",
    "f5-observer",
    "otel-collector",
)

# "Utils" pods — infrastructure / one-shot jobs
UTILS_PREFIXES = (
    "crd-installer",
    "f5-coremond",
    "f5-crdconversion",
    "f5-dssm",
    "f5-ipam",
    "f5-rabbit",
    "f5-spk-",
    "spk-csrc",
    "f5-toda",
    "f5-cwc",
    "f5-spk-cwc",
)

# -----------------------------------------------------------------------
# Label hints — Layer 1 role classifiers (verified from F5 charts/TF)
# -----------------------------------------------------------------------
# Maps role → list of (label_key, label_value) pairs (any match wins).
# Source: TMM from f5-controller/_helpers.tpl; FLO from flo/main.tf;
#         IPAM from flo/main.tf; crd-installer from _execute_crd_wait.
_LABEL_HINTS: dict[str, list[tuple[str, str]]] = {
    "tmm": [("app", "f5-tmm")],
    "flo": [
        ("app", "flo"),
        ("app.kubernetes.io/name", "f5-lifecycle-operator"),
    ],
    "controller": [
        ("app", "f5-cne-controller"),
        ("app", "f5ingress-f5ingress"),
        ("app.kubernetes.io/name", "f5ingress"),
    ],
    "ipam": [("app", "ipam-operator")],
    "crd_installer": [("app", "crd-installer")],
    # analyzer has no label hint — ownerRef traversal via f5biganalyzers CR is deferred to Slice 2+.
}

# Roles whose labels are used as cluster-wide sweep triggers (phase-2).
# These labels classify a pod's role *once admitted*; in phase-1, admission is by
# name prefix via _is_bnk_pod, not by label.  "ipam" (app=ipam-operator) and
# "crd_installer" (app=crd-installer) are excluded here because those labels are
# too generic — third-party IPAM operators (Whereabouts, MetalLB derivatives) and
# CRD bootstrap Jobs commonly use the same values.  ipam/crd_installer pods are
# found via the name-prefix path in _is_bnk_pod, not via this label set.
_SWEEP_LABEL_ROLES: frozenset[str] = frozenset({"tmm", "flo", "controller"})

# -----------------------------------------------------------------------
# Container hints — Layer 2 role classifiers
# -----------------------------------------------------------------------
# Maps role → list of container name substrings or image substrings.
# A pod is matched if ANY container name/image contains the hint.
_CONTAINER_NAME_HINTS: dict[str, list[str]] = {
    "tmm": ["tmm"],
    "flo": ["flo"],
    "controller": ["f5ingress-f5ingress"],
}
_CONTAINER_IMAGE_HINTS: dict[str, list[str]] = {
    "flo": ["ln-operator"],
}

# Roles that belong to the "tenant" bucket (vs utils)
_TENANT_ROLES = frozenset({"tmm", "flo", "controller", "analyzer"})


def _pod_labels(pod_or_dict) -> dict[str, str]:
    """Extract labels from either a V1Pod or a pod dict."""
    if isinstance(pod_or_dict, dict):
        return pod_or_dict.get("labels") or {}
    meta = getattr(pod_or_dict, "metadata", None)
    return dict(meta.labels or {}) if meta and meta.labels else {}


def _match_label_hints(labels: dict[str, str]) -> str | None:
    """Return the first role whose label hints match, or None."""
    for role, pairs in _LABEL_HINTS.items():
        if any(labels.get(k) == v for k, v in pairs):
            return role
    return None


def _match_container_hints(containers: list[dict[str, str]]) -> str | None:
    """Return the first role matched by container name or image hints, or None."""
    for c in containers:
        name = c.get("name", "")
        image = c.get("image", "")
        # Exact equality for container NAME — avoids "flo" matching "flower"/"workflow",
        # or "tmm" matching "stmm-agent". F5 containers are literally named "flo" / "tmm".
        for role, hints in _CONTAINER_NAME_HINTS.items():
            if any(name == h for h in hints):
                return role
        # Substring match for IMAGE is acceptable — image strings are longer and
        # less likely to collide (e.g. "ln-operator" in a full registry URL).
        for role, hints in _CONTAINER_IMAGE_HINTS.items():
            if any(h in image for h in hints):
                return role
    return None


def _is_bnk_pod(pod) -> bool:
    """
    Return True if a V1Pod looks like a BNK component.

    Checks a restricted label set first (fast), then name prefixes (fallback).
    Used to filter the cluster-wide sweep and phase-1 namespace lists.

    Only roles in _SWEEP_LABEL_ROLES are used as label-based sweep triggers.
    ipam/crd_installer labels are too generic for cluster-wide matching — those
    pods are found via the name-prefix fallback or during phase-1 namespace queries.
    """
    # Restricted label hint check for sweep — only distinctive-label roles
    labels = _pod_labels(pod)
    for role in _SWEEP_LABEL_ROLES:
        pairs = _LABEL_HINTS[role]
        if any(labels.get(k) == v for k, v in pairs):
            return True
    # Name-prefix fallback (Layer 3)
    meta = getattr(pod, "metadata", None)
    name = (meta.name if meta else "") or ""
    return any(name.startswith(p) for p in ALL_F5_PREFIXES)


# -----------------------------------------------------------------------
# Pod → dict conversion
# -----------------------------------------------------------------------

def pod_to_dict(pod) -> dict[str, Any]:
    """
    Convert a kubernetes.client.V1Pod to a plain dict.

    The dict shape is compatible with both the health dashboard and
    the cluster scanner analysis functions.
    """
    meta = pod.metadata
    spec = pod.spec
    status = pod.status

    # Extract pod start time for uptime calculation
    start_time = None
    if status and status.start_time:
        start_time = status.start_time.isoformat() if hasattr(status.start_time, "isoformat") else str(status.start_time)

    return {
        "name": meta.name if meta else "",
        "namespace": meta.namespace if meta else "",
        "nodeName": spec.node_name if spec and spec.node_name else None,
        "hostIP": status.host_ip if status and status.host_ip else None,
        "phase": status.phase if status else "Unknown",
        "startTime": start_time,
        "conditions": [
            {"type": c.type, "status": c.status}
            for c in (status.conditions or [])
        ] if status and status.conditions else [],
        "containers": [
            {
                "name": cs.name,
                "ready": cs.ready,
                "restart_count": cs.restart_count or 0,
                "restartCount": cs.restart_count or 0,  # alias for health dashboard
                "image": cs.image,
                "state": (
                    "running" if cs.state and cs.state.running else
                    "waiting" if cs.state and cs.state.waiting else
                    "terminated" if cs.state and cs.state.terminated else
                    "unknown"
                ),
            }
            for cs in (status.container_statuses or [])
        ] if status and status.container_statuses else [],
        "labels": dict(meta.labels or {}) if meta and meta.labels else {},
    }


# -----------------------------------------------------------------------
# Discovery
# -----------------------------------------------------------------------

def _fetch_pods_in_namespace(api_client, namespace: str) -> list:
    """Fetch raw V1Pod objects from a single namespace. Returns [] if namespace doesn't exist."""
    try:
        v1 = k8s_client.CoreV1Api(api_client)
        resp = v1.list_namespaced_pod(namespace=namespace, _request_timeout=10)
        return resp.items
    except Exception as e:
        logger.debug(f"Could not fetch pods in {namespace} (may not exist): {e}")
        return []


def _sweep_all_namespaces(api_client) -> list:
    """
    Cluster-wide pod sweep — find F5 pods in ANY namespace.

    Uses ``list_pod_for_all_namespaces`` and filters by BNK signals
    (label hints OR name prefix) client-side.  Returns raw V1Pod objects.
    """
    try:
        v1 = k8s_client.CoreV1Api(api_client)
        resp = v1.list_pod_for_all_namespaces(_request_timeout=15)
        return [pod for pod in resp.items if pod.metadata and _is_bnk_pod(pod)]
    except Exception as e:
        logger.debug(f"Cluster-wide pod sweep failed (non-fatal): {e}")
        return []


def discover_f5_pods(
    api_client,
    *,
    include_sweep: bool = True,
    extra_namespaces: tuple[str, ...] | list[str] = (),
) -> tuple[list[dict], list[dict]]:
    """
    Discover F5 BNK pods using two-phase discovery.

    Phase 1 (fast path): Query known BNK namespaces + any persisted
                         ``extra_namespaces`` in parallel (~1-2s).
    Phase 2 (sweep):     Query all namespaces to catch F5 components
                         deployed in non-standard namespaces.

    Pods from phase 2 are de-duplicated against phase 1 by
    (namespace, name) key, so there are no double-counts.

    Pass `include_sweep=False` when the caller already knows the cluster
    has no BNK installed (e.g. no BNK API groups registered). On large
    clusters the sweep's cluster-wide `list_pod_for_all_namespaces` is a
    multi-MB download that finds zero F5 pods — skipping it cuts ~20s
    off a cold scan on a 500+-pod cluster.

    Pass ``extra_namespaces`` to seed discovery from the namespaces
    previously persisted on a cluster record (``cluster.discovered_namespaces``).
    These are unioned with the static BNK_NAMESPACES seed to form the
    fast-path set; duplicates are handled safely.

    Returns:
        (tenant_pods, utils_pods) — both as lists of pod dicts.

    tenant_pods: FLO, TMM, controller, analyzer, observer, otel-collector
    utils_pods:  crd-installer, DSSM, IPAM, rabbit, etc.
    """
    # Union static seed with any caller-supplied persisted namespaces.
    # Preserve ordering: static seed first, extra namespaces appended.
    fast_path_ns: tuple[str, ...] = BNK_NAMESPACES + tuple(
        ns for ns in extra_namespaces if ns not in BNK_NAMESPACES
    )

    try:
        # Phase 1 (known namespaces) always runs; Phase 2 (cluster-wide
        # sweep) is conditional on include_sweep.
        worker_count = len(fast_path_ns) + (1 if include_sweep else 0)
        with ThreadPoolExecutor(max_workers=max(worker_count, 1)) as executor:
            ns_futures = {
                ns: executor.submit(_fetch_pods_in_namespace, api_client, ns)
                for ns in fast_path_ns
            }
            sweep_future = (
                executor.submit(_sweep_all_namespaces, api_client)
                if include_sweep
                else None
            )

            # Collect phase 1 pods
            phase1_pods: list = []
            for ns, future in ns_futures.items():
                phase1_pods.extend(future.result())

            # Collect phase 2 pods (if the sweep ran)
            sweep_pods = sweep_future.result() if sweep_future is not None else []

        # De-duplicate: track (namespace, name) from phase 1
        seen: set[tuple[str, str]] = set()
        all_pods: list = []
        for pod in phase1_pods:
            name = pod.metadata.name if pod.metadata else ""
            ns = pod.metadata.namespace if pod.metadata else ""
            if not name:
                continue
            # Keep BNK pods: label hints (layer 1) OR name-prefix fallback (layer 3).
            # Phase 1 queries entire namespaces, so we must filter to BNK pods only.
            if _is_bnk_pod(pod):
                seen.add((ns, name))
                all_pods.append(pod)

        # Merge phase 2 pods not already seen
        extra_count = 0
        for pod in sweep_pods:
            name = pod.metadata.name if pod.metadata else ""
            ns = pod.metadata.namespace if pod.metadata else ""
            key = (ns, name)
            if key not in seen:
                seen.add(key)
                all_pods.append(pod)
                extra_count += 1

        if extra_count:
            extra_ns_found = {
                pod.metadata.namespace
                for pod in sweep_pods
                if (pod.metadata.namespace if pod.metadata else "") not in fast_path_ns
                and (pod.metadata.namespace or "", pod.metadata.name or "") in seen
            }
            logger.info(
                f"Cluster-wide sweep found {extra_count} F5 pods in "
                f"non-standard namespaces: {sorted(extra_ns_found)}"
            )

        # Classify into tenant vs utils buckets using layered resolution.
        # Layer 1: label hints. Layer 2: container hints. Layer 3: name prefix.
        tenant_pods = []
        utils_pods = []

        for pod in all_pods:
            pod_dict = pod_to_dict(pod)
            labels = pod_dict.get("labels") or {}
            containers = pod_dict.get("containers") or []
            name = pod_dict.get("name", "")

            role = _match_label_hints(labels)
            if role is None:
                role = _match_container_hints(
                    [{"name": c.get("name", ""), "image": c.get("image", "")} for c in containers]
                )
            if role is None:
                # Name-prefix fallback (layer 3): derive role from bucket membership
                if any(name.startswith(p) for p in TENANT_PREFIXES):
                    role = "tenant"
                elif any(name.startswith(p) for p in UTILS_PREFIXES):
                    role = "utils"

            if role in _TENANT_ROLES or role == "tenant":
                tenant_pods.append(pod_dict)
            elif role in ("ipam", "crd_installer", "utils"):
                utils_pods.append(pod_dict)
            # role is None here: layer-3 already checked both prefix sets and found
            # no match, so a redundant prefix re-check would never fire.

        logger.debug(
            f"Discovered {len(tenant_pods)} tenant pods, "
            f"{len(utils_pods)} utils pods "
            f"(fast-path namespaces: {list(fast_path_ns)} + cluster-wide sweep)"
        )
        return tenant_pods, utils_pods

    except Exception as e:
        logger.warning(f"Could not discover F5 pods: {e}")
        return [], []


def discover_f5_pods_with_ns_tracking(
    api_client,
    *,
    include_sweep: bool = True,
    extra_namespaces: tuple[str, ...] | list[str] = (),
) -> tuple[list[dict], list[dict], list[str]]:
    """
    Discover F5 BNK pods and return the set of namespaces where pods were found.

    Wraps ``discover_f5_pods`` and adds namespace tracking so callers can
    persist the result back to the cluster record
    (``cluster.discovered_namespaces``).

    Returns:
        (tenant_pods, utils_pods, discovered_namespaces)

    ``discovered_namespaces`` is a sorted list of namespace strings where at
    least one F5 BNK pod was found — suitable for JSON serialisation.
    """
    tenant_pods, utils_pods = discover_f5_pods(
        api_client,
        include_sweep=include_sweep,
        extra_namespaces=extra_namespaces,
    )
    found_ns: set[str] = set()
    for pod in tenant_pods + utils_pods:
        ns = pod.get("namespace")
        if ns:
            found_ns.add(ns)
    return tenant_pods, utils_pods, sorted(found_ns)


# -----------------------------------------------------------------------
# Classification helpers
# -----------------------------------------------------------------------

def classify_f5_pods(tenant_pods: list[dict], utils_pods: list[dict]) -> dict[str, list[dict]]:
    """
    Classify discovered pods into named groups.

    Resolution is layered (highest-priority first):
      1. Label hints  (e.g. app=f5-tmm, app=flo)
      2. Container name / image hints (container named "tmm", image "*ln-operator*")
      3. Name-PREFIX fallback — consistent with _is_bnk_pod (uses startswith, not substring)

    Layer 3 uses PREFIX matching (``name.startswith(prefix)``) to align with the
    ``_is_bnk_pod`` sweep filter, which also uses prefix semantics.  The old
    substring ``"f5-tmm" in name`` check was inconsistent — it would match names
    like ``"my-f5-tmm-copy"`` which no prefix rule would admit.

    Pods that appear in both ``tenant_pods`` and ``utils_pods`` are de-duplicated
    by (namespace, name) key — each pod is classified at most once.

    Returns a dict with keys:
        tmm, flo, controller, analyzer, crd_installer
    """
    result: dict[str, list[dict]] = {
        "tmm": [], "flo": [], "controller": [], "analyzer": [], "crd_installer": [],
    }

    # Role-to-prefix mapping for Layer-3 — mirrors TENANT_PREFIXES / UTILS_PREFIXES
    # but maps to the named groups that classify_f5_pods returns.
    _ROLE_PREFIXES: list[tuple[str, str]] = [
        ("tmm", "f5-tmm"),
        ("flo", "flo-f5-lifecycle"),
        ("flo", "f5-lifecycle"),
        ("controller", "f5-cne-controller"),
        ("controller", "f5ingress"),
        ("analyzer", "f5-analyzer"),
        ("crd_installer", "crd-installer"),
    ]

    def _classify_pod(p: dict) -> str | None:
        labels = p.get("labels") or {}
        containers = [
            {"name": c.get("name", ""), "image": c.get("image", "")}
            for c in (p.get("containers") or [])
        ]
        name = p.get("name", "")

        # Layer 1: label hints
        role = _match_label_hints(labels)
        if role:
            return role

        # Layer 2: container hints
        role = _match_container_hints(containers)
        if role:
            return role

        # Layer 3: name-PREFIX fallback (aligned with _is_bnk_pod prefix semantics)
        for r, prefix in _ROLE_PREFIXES:
            if name.startswith(prefix):
                return r
        return None

    # Dedup by (namespace, name) across both input lists
    seen_keys: set[tuple[str, str]] = set()
    for p in list(tenant_pods) + list(utils_pods):
        key = (p.get("namespace", ""), p.get("name", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        role = _classify_pod(p)
        if role in result:
            result[role].append(p)

    return result


def detect_install_shape(classified: dict[str, list[dict]]) -> str:
    """Classify how BNK was installed, from discovered pods.

    "flo"     — a FLO/lifecycle-operator pod is present (Forge deploy-flow install).
    "helm"    — TMM and/or controller present but no FLO (direct helm/kind install).
    "unknown" — no TMM and no controller discovered (BNK not installed / not reachable).
    """
    if classified.get("flo"):
        return "flo"
    if classified.get("tmm") or classified.get("controller"):
        return "helm"
    return "unknown"


def pod_is_healthy(pod: dict) -> bool:
    """Check if a pod dict represents a healthy running pod."""
    return (
        isinstance(pod, dict)
        and pod.get("phase") == "Running"
        and all(c.get("ready", False) for c in pod.get("containers", []))
    )


def pod_is_completed(pod: dict) -> bool:
    """Check if a pod dict represents a completed (succeeded) pod."""
    return isinstance(pod, dict) and pod.get("phase") in ("Succeeded", "Completed")
