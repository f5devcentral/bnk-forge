"""
Live CRD discovery service (D-018, P1).

Queries the cluster's apiextensions.k8s.io/v1 API for installed
CustomResourceDefinitions, merges results with the static resource
registry for curated metadata, and caches per-cluster with a short TTL.

Reach: direct kubeconfig only (same set as the generic resource route).
Operator-relayed CRD discovery is deferred (see D-018 Out of Scope).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from kubernetes import client
from sqlalchemy.orm import Session

from core.cache import cache
from core.errors import AppError, BadRequestError, NotFoundError
from core.k8s_resource_registry import get_resource_type, list_resource_types
from core.k8s_types import K8sResourceType
from schemas.k8s import CRDInfo, CrdListEnvelope
from services.kubernetes._base import KubernetesServiceBase
from services.kubernetes._hardened import translate_k8s_exception
from services.reachability import with_breaker

logger = logging.getLogger(__name__)

CRD_CACHE_TTL_SECONDS = 120


def _get_attr(obj: Any, key: str, default: Any = None) -> Any:
    """Attribute accessor that handles both plain dicts and attribute-bearing objects.

    Kubernetes client objects use real attributes; the cache path stores/restores
    plain dicts (via ``to_dict()`` → JSON). This helper makes ``_pick_version``
    work correctly in both cases.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _pick_version(versions: list[Any]) -> str | None:
    """Return the served version, preferring storage=true, then first served.

    Works for both Kubernetes client objects and plain dicts (from cache).
    """
    if not versions:
        return None
    # Prefer storage+served
    for v in versions:
        if _get_attr(v, "served") and _get_attr(v, "storage"):
            return _get_attr(v, "name")
    # Fall back to first served
    for v in versions:
        if _get_attr(v, "served", False):
            return _get_attr(v, "name")
    # Last resort: first entry
    return _get_attr(versions[0], "name")


def _build_registry_index(resource_types: dict[str, K8sResourceType]) -> dict[tuple[str, str], K8sResourceType]:
    """Build a (api_group, kind) → K8sResourceType reverse index.

    Keying on (api_group, kind) avoids kind collisions across API groups
    (e.g., 'Gateway' in gateway.networking.k8s.io vs. a vendor group).
    """
    index: dict[tuple[str, str], K8sResourceType] = {}
    for rt in resource_types.values():
        key = (rt.api_group, rt.kind)
        index[key] = rt
    return index


@lru_cache(maxsize=1)
def _get_registry_index() -> dict[tuple[str, str], K8sResourceType]:
    """Return the memoised (api_group, kind) registry index.

    Built once from list_resource_types() and cached for the process lifetime.
    The static registry never changes at runtime, so a single build is correct.

    Call ``_get_registry_index.cache_clear()`` in tests that monkeypatch
    list_resource_types or the registry contents.
    """
    return _build_registry_index(list_resource_types())


def merge_crd_with_registry(
    discovered: dict[str, Any],
    registry_index: dict[tuple[str, str], K8sResourceType],
) -> CRDInfo:
    """Pure function: build a CRDInfo from a raw CRD object dict, enriched
    from the static registry when a matching (api_group, kind) entry exists.

    ``discovered`` is the dict form of a V1CustomResourceDefinition:
      - spec.group
      - spec.names.kind
      - spec.names.plural
      - spec.scope ("Namespaced" | "Cluster")
      - spec.versions (list)
      - metadata.name
    """
    spec = discovered.get("spec") or {}
    names = spec.get("names") or {}
    versions_raw = spec.get("versions") or []

    group = spec.get("group") or ""
    kind = names.get("kind") or ""
    plural = names.get("plural") or ""
    namespaced = spec.get("scope") == "Namespaced"
    name = discovered.get("metadata", {}).get("name") or f"{plural}.{group}"

    version = _pick_version(versions_raw)

    registry_entry = registry_index.get((group, kind))
    if registry_entry:
        return CRDInfo(
            name=name,
            kind=kind,
            plural=plural,
            group=group,
            version=version,
            namespaced=namespaced,
            display_name=registry_entry.display_name,
            category=registry_entry.category,
            source="registry-enriched",
        )

    return CRDInfo(
        name=name,
        kind=kind,
        plural=plural,
        group=group,
        version=version,
        namespaced=namespaced,
        display_name=None,
        category=None,
        source="discovered",
    )


class CrdDiscoveryService(KubernetesServiceBase):
    """Discover CRDs installed in a cluster and merge with the static registry."""

    def __init__(self, db: Session):
        super().__init__(db)

    @with_breaker("cluster", target_id_arg="cluster_id")
    def list_crds(
        self,
        cluster_id: int,
        group_filter: list[str] | None = None,
    ) -> CrdListEnvelope:
        """Return all CRDs installed in the cluster, optionally filtered by API group.

        Cache key: crd_discovery:{cluster_id} (unfiltered discovery result).
        group_filter is applied in Python after the cache read to avoid the
        @cached list-arg key-drop footgun.

        Raises NetworkUnreachableError (503) when the cluster is unreachable.
        Returns 200 with empty crds + non-null info when reachable but no CRDs
        match the filter.
        """
        cluster = self.get_cluster(cluster_id)

        # --- Cache check (unfiltered) ---
        cache_key = f"crd_discovery:{cluster_id}"
        cached_raw: list[dict[str, Any]] | None = cache.get(cache_key)

        if cached_raw is None:
            raw_crds = self._discover(cluster)
            # Silently skip if Redis is unavailable (set returns False)
            cache.set(cache_key, raw_crds, CRD_CACHE_TTL_SECONDS)
        else:
            raw_crds = cached_raw

        # --- Build registry index for enrichment ---
        registry_index = _get_registry_index()

        # --- Merge ---
        all_infos = [merge_crd_with_registry(raw, registry_index) for raw in raw_crds]

        # --- Apply group filter (post-cache) ---
        if group_filter:
            filtered = [c for c in all_infos if c.group in group_filter]
        else:
            filtered = all_infos

        info_msg: str | None = None
        if not filtered:
            if group_filter:
                info_msg = f"No CRDs found in group(s): {', '.join(group_filter)}"
            else:
                info_msg = "No CRDs installed in this cluster"

        return CrdListEnvelope(
            crds=filtered,
            count=len(filtered),
            cluster_id=cluster_id,
            group_filter=group_filter,
            info=info_msg,
        )

    def resolve_crd(
        self,
        cluster_id: int,
        resource_type: str,
        group: str | None = None,
    ) -> CRDInfo:
        """Resolve a resource_type string to a discovered CRDInfo.

        Precedence ladder (matches architect-pinned decisions):
          1. Registry-key exact match — raises ValueError.
             Callers MUST resolve registered kinds via the registry before invoking
             this method. Calling resolve_crd for a registered kind is a programming
             error; this loud guard prevents silent misbehaviour.
          2. Dotted FQ name (<plural>.<group>) → match CRDInfo.name.
             If the matched CRD's (group, kind) is curated in the static registry,
             the returned CRDInfo carries the registry's curated api_version instead
             of the cluster's served/storage version (version-shadowing guard).
          3. Bare plural → match CRDInfo.plural; exactly one → resolve;
             optional ``group`` narrows; >1 match without group → BadRequestError.

        Raises ValueError if called for a resource_type that is a registered kind
        (callers registry-check first; this is a loud guard against misuse).
        Raises BadRequestError (400) for ambiguous bare plural without group.
        Raises NotFoundError (404) when CRD is not installed.

        Never constructs a fresh ApiextensionsV1Api; reuses list_crds cache.
        """
        # Step 1: registry-key exact — callers must NOT reach here for registered kinds.
        try:
            get_resource_type(resource_type)
        except ValueError:
            pass  # not in registry — continue to discovery
        else:
            raise ValueError(
                f"resolve_crd called for registered kind '{resource_type}'; "
                "callers must resolve registered kinds via the registry before invoking discovery."
            )

        # Step 2 & 3: discovery via cached list_crds
        envelope = self.list_crds(cluster_id)
        crds = envelope.crds

        # Step 2: dotted FQ name — exact match on CRDInfo.name (<plural>.<group>)
        if "." in resource_type:
            for crd in crds:
                if crd.name == resource_type:
                    # Version-shadowing guard: if this (group, kind) is curated in
                    # the static registry, use the registry's api_version rather than
                    # the cluster's served/storage version (e.g. Gateway API v1alpha2).
                    registry_index = _get_registry_index()
                    registry_entry = registry_index.get((crd.group, crd.kind))
                    if registry_entry:
                        return crd.model_copy(update={"version": registry_entry.api_version})
                    return crd
            raise NotFoundError("CRD", resource_type)

        # Step 3: bare plural match
        matches = [c for c in crds if c.plural == resource_type]

        if not matches:
            raise NotFoundError("CRD", resource_type)

        # Narrow by group if provided
        if group:
            matches = [c for c in matches if c.group == group]
            if not matches:
                raise NotFoundError("CRD", f"{resource_type} (group={group})")
            crd = matches[0]
        elif len(matches) == 1:
            crd = matches[0]
        else:
            # Ambiguous: >1 match with no group provided
            candidates = [c.name for c in matches]
            raise BadRequestError(
                f"Ambiguous resource type '{resource_type}': found in multiple API groups. "
                f"Use a fully-qualified name (<plural>.<group>) or add ?group=. "
                f"Candidates: {', '.join(candidates)}",
                code="AMBIGUOUS_RESOURCE_TYPE",
                details={"candidates": candidates},
            )

        # Version-shadowing guard (mirrors Step 2): if this (group, kind) is curated in
        # the static registry, use the registry's pinned api_version rather than the
        # cluster's served/storage version (e.g. v1alpha2 for tcproutes/tlsroutes/grpcroutes).
        registry_index = _get_registry_index()
        registry_entry = registry_index.get((crd.group, crd.kind))
        if registry_entry:
            return crd.model_copy(update={"version": registry_entry.api_version})
        return crd

    def _discover(self, cluster: Any) -> list[dict[str, Any]]:
        """Fetch all CRDs from the cluster via ApiextensionsV1Api.

        This is the I/O mock point for component tests.
        Raises NetworkUnreachableError (via translate_k8s_exception) on failure.
        """
        try:
            api_client = self.load_kubeconfig(cluster)
            ext_api = client.ApiextensionsV1Api(api_client)
            result = ext_api.list_custom_resource_definition(_request_timeout=(3, 8))
            raw = []
            for item in (result.items or []):
                if hasattr(item, "to_dict"):
                    raw.append(item.to_dict())
                elif isinstance(item, dict):
                    raw.append(item)
            return raw
        except Exception as exc:
            if isinstance(exc, AppError):
                raise  # let load_kubeconfig's AppError propagate with its original message
            raise translate_k8s_exception(
                exc,
                target_id=cluster.id,
                target_name=cluster.name,
            ) from exc
