"""
Scanner fetch — parallel K8s API data collection.

This is the ONLY I/O module in the scanner package.  All other modules
are pure functions that transform the dicts returned by ``fetch_scan_data``.
"""

import base64
import gzip
import json
import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from kubernetes import client

from core.k8s_types import ApiGroups
from services.bnk_pod_discovery import discover_f5_pods
from services.kubernetes._resources import resolve_resource_type
from services.scanner.constants import SCANNER_RELEVANT_CRD_GROUPS
from services.scanner.nodes import parse_node

logger = logging.getLogger(__name__)


# API groups whose presence gates whole sets of CRD resource fetches. If
# none of these are registered on the cluster, we skip the whole fetch
# burst — same pattern as fleet-health. Avoids 6+ sequential 404 round
# trips on clusters that don't use DPF / BNK / Kamaji.
_BNK_API_GROUPS = frozenset({
    ApiGroups.F5_NET,
    ApiGroups.F5_K8S,
    ApiGroups.F5_GATEWAY_NET,
})
_DPF_API_GROUPS = frozenset({
    ApiGroups.DPF_PROVISIONING,
    ApiGroups.DPF_SERVICE,
    ApiGroups.DPF_OPERATOR,
})
_KAMAJI_API_GROUP = "kamaji.clastix.io"
_CIS_API_GROUP = "cis.f5.com"
_CIS_CONTROLLER_IMAGE = "f5networks/k8s-bigip-ctlr"


def _discover_api_groups(api_client) -> frozenset[str]:
    """Return the set of non-core API group names registered on the cluster.

    One cheap discovery call (~100ms). Returns an empty set on failure,
    which causes gated fetches to be skipped — erring on the side of
    "probably not installed" rather than "fall through to 6 slow 404s".
    """
    try:
        apis = client.ApisApi(api_client).get_api_versions(_request_timeout=5)
        return frozenset(g.name for g in (apis.groups or []))
    except Exception as e:
        logger.warning(f"Scanner: API group discovery failed: {e}")
        return frozenset()


# ---------------------------------------------------------------------------
# Individual fetchers — each returns a safe default on error
# ---------------------------------------------------------------------------


def _fetch_version(api_client) -> dict[str, Any] | None:
    try:
        v = client.VersionApi(api_client).get_code()
        return {
            "major": v.major,
            "minor": v.minor,
            "git_version": v.git_version,
            "platform": v.platform,
            "go_version": v.go_version,
            "build_date": v.build_date,
        }
    except Exception as e:
        logger.warning(f"Failed to fetch version: {e}")
        return None


def _fetch_nodes(api_client) -> list[dict[str, Any]]:
    try:
        v1 = client.CoreV1Api(api_client)
        resp = v1.list_node(_request_timeout=10)
        return [parse_node(n) for n in resp.items]
    except Exception as e:
        logger.warning(f"Failed to fetch nodes: {e}")
        return []


def _discover_group_resources(
    api_client,
    group: str,
    timeout_sec: float = 5.0,
) -> list[dict[str, Any]]:
    """
    Return CRD-shaped dicts for every resource registered under a single
    API group, using the K8s discovery endpoints. Returns an empty list if
    the group isn't registered (404) or the call fails.

    Reconstructs the old `_fetch_crds` output shape from the APIResourceList
    the discovery endpoint returns, so downstream analyzers (which filter
    by group/kind/name) don't need to change.
    """
    try:
        # /apis/<group> returns a V1APIGroup with versions + preferred.
        group_resp, group_status, _ = api_client.call_api(
            f"/apis/{group}",
            "GET",
            auth_settings=["BearerToken"],
            response_type="object",
            _return_http_data_only=False,
            _request_timeout=timeout_sec,
        )
        if group_status == 404 or not isinstance(group_resp, dict):
            return []

        preferred = (group_resp.get("preferredVersion") or {}).get("version")
        if not preferred:
            versions = group_resp.get("versions") or []
            if not versions:
                return []
            preferred = versions[0].get("version")
        if not preferred:
            return []

        # /apis/<group>/<version> returns an APIResourceList with all resources
        # (and their subresources as separate entries, which we filter out).
        resources_resp, resources_status, _ = api_client.call_api(
            f"/apis/{group}/{preferred}",
            "GET",
            auth_settings=["BearerToken"],
            response_type="object",
            _return_http_data_only=False,
            _request_timeout=timeout_sec,
        )
        if resources_status == 404 or not isinstance(resources_resp, dict):
            return []

        out: list[dict[str, Any]] = []
        for r in resources_resp.get("resources") or []:
            plural = r.get("name") or ""
            # Skip subresources like "certificates/status".
            if not plural or "/" in plural:
                continue
            kind = r.get("kind") or ""
            out.append(
                {
                    "name": f"{plural}.{group}",
                    "group": group,
                    "kind": kind,
                    "versions": [preferred],
                }
            )
        return out
    except client.ApiException as e:
        if e.status == 404:
            return []
        logger.warning(f"Scanner: discovery of {group} failed: {e}")
        return []
    except Exception as e:
        logger.warning(f"Scanner: discovery of {group} failed: {e}")
        return []


def _fetch_crds(api_client, target_groups: Iterable[str]) -> list[dict[str, Any]]:
    """
    Enumerate CRDs via per-group discovery instead of listing every CRD on
    the cluster. Much cheaper on mature clusters where the full CRD list
    runs into tens of megabytes and flaky connections trigger urllib3 retries.

    `target_groups` should already be the intersection of
    SCANNER_RELEVANT_CRD_GROUPS with the groups actually present on the
    cluster — we don't do the filtering here because the caller has a
    cheap preflight that already knows which groups exist.
    """
    groups = list(target_groups)
    if not groups:
        return []

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(groups))) as pool:
        futures = [
            pool.submit(_discover_group_resources, api_client, g)
            for g in groups
        ]
        for f in futures:
            try:
                results.extend(f.result())
            except Exception as e:
                logger.warning(f"Scanner: per-group discovery future failed: {e}")
    return results


def _fetch_storage_classes(api_client) -> list[dict[str, Any]]:
    try:
        storage = client.StorageV1Api(api_client)
        resp = storage.list_storage_class(_request_timeout=10)
        return [
            {
                "name": sc.metadata.name,
                "provisioner": sc.provisioner,
                "is_default": (
                    any(
                        sc.metadata.annotations.get(k) == "true"
                        for k in [
                            "storageclass.kubernetes.io/is-default-class",
                            "storageclass.beta.kubernetes.io/is-default-class",
                        ]
                    )
                    if sc.metadata.annotations
                    else False
                ),
                "reclaim_policy": sc.reclaim_policy,
                "volume_binding_mode": sc.volume_binding_mode,
            }
            for sc in resp.items
        ]
    except Exception as e:
        logger.warning(f"Failed to fetch storage classes: {e}")
        return []


def _fetch_namespaces(api_client) -> list[str]:
    try:
        v1 = client.CoreV1Api(api_client)
        resp = v1.list_namespace(_request_timeout=10)
        return [ns.metadata.name for ns in resp.items]
    except Exception as e:
        logger.warning(f"Failed to fetch namespaces: {e}")
        return []


def _fetch_daemonsets(api_client) -> list[dict[str, Any]]:
    try:
        apps = client.AppsV1Api(api_client)
        resp = apps.list_daemon_set_for_all_namespaces(_request_timeout=10)
        return [
            {
                "name": ds.metadata.name,
                "namespace": ds.metadata.namespace,
                "labels": dict(ds.metadata.labels or {}),
                "desired": ds.status.desired_number_scheduled or 0,
                "ready": ds.status.number_ready or 0,
                "images": [
                    c.image for c in (ds.spec.template.spec.containers or [])
                ],
            }
            for ds in resp.items
        ]
    except Exception as e:
        logger.warning(f"Failed to fetch daemonsets: {e}")
        return []


def _fetch_pods_in_ns(api_client, namespace: str) -> list[dict[str, Any]]:
    try:
        v1 = client.CoreV1Api(api_client)
        resp = v1.list_namespaced_pod(namespace=namespace, _request_timeout=10)
        return [
            {
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,
                "phase": pod.status.phase if pod.status else "Unknown",
                "labels": dict(pod.metadata.labels or {}),
                "containers": [
                    {
                        "name": cs.name,
                        "ready": cs.ready,
                        "restart_count": cs.restart_count,
                        "image": cs.image,
                    }
                    for cs in (pod.status.container_statuses or [])
                ]
                if pod.status
                else [],
            }
            for pod in resp.items
        ]
    except Exception:
        return []


def _decode_helm_release_secret(raw: str | None) -> dict | None:
    """Decode a Helm 3 release secret payload: base64(base64(gzip(json))).

    Returns the parsed release JSON dict, or None on any failure.
    The Helm client stores the release object double-base64-gzipped in the
    ``release`` key of the secret's data map.  The k8s Python client already
    base64-decodes secret .data values once, so we need one more b64 decode
    then gunzip.
    """
    if not raw:
        return None
    try:
        # k8s client returns .data values as already-base64-decoded bytes or str
        if isinstance(raw, str):
            raw_bytes = raw.encode()
        else:
            raw_bytes = raw
        # First b64 decode (the outer envelope Helm applies on top of the k8s layer)
        inner = base64.b64decode(raw_bytes)
        # Gunzip
        payload = gzip.decompress(inner)
        return json.loads(payload)
    except Exception as exc:
        logger.debug("helm secret decode failed: %s", exc)
        return None


def _fetch_helm_releases(api_client) -> list[dict[str, Any]]:
    """Detect Helm releases by checking Helm secrets (type=helm.sh/release.v1).

    Decodes the release secret payload to extract the chart name+version so
    that downstream services (e.g. bnk_upgrade_plan_service) can reference the
    chart without a separate Helm client call.
    """
    try:
        v1 = client.CoreV1Api(api_client)
        resp = v1.list_secret_for_all_namespaces(
            field_selector="type=helm.sh/release.v1",
            _request_timeout=15,
        )
        releases: dict[str, dict] = {}
        for s in resp.items:
            labels = s.metadata.labels or {}
            name = labels.get("name", "")
            version = labels.get("version", "0")
            ns = s.metadata.namespace
            key = f"{ns}/{name}"
            # Keep only the latest version
            if key not in releases or int(version) > int(releases[key].get("version", 0)):
                entry: dict[str, Any] = {
                    "name": name,
                    "namespace": ns,
                    "version": version,
                    "status": labels.get("status", ""),
                }
                # Decode secret payload to get chart reference
                raw = (s.data or {}).get("release")
                rel_obj = _decode_helm_release_secret(raw)
                if rel_obj:
                    try:
                        meta = rel_obj["chart"]["metadata"]
                        chart_name = meta["name"]
                        chart_version = meta["version"]
                        entry["chart"] = f"{chart_name}-{chart_version}"
                    except (KeyError, TypeError) as exc:
                        logger.debug("helm release %s/%s: chart metadata missing: %s", ns, name, exc)
                releases[key] = entry
        return list(releases.values())
    except Exception as e:
        logger.warning(f"Failed to fetch helm releases: {e}")
        return []


def _fetch_cis_controller(api_client) -> list[dict[str, Any]]:
    """Fetch Deployments that look like CIS (k8s-bigip-ctlr) controllers.

    Gated on _CIS_API_GROUP ∈ api_groups in fetch_scan_data — this function
    only runs when cis.f5.com is registered on the cluster.

    Returns a list of deployment dicts with name/namespace/images/args/replicas_ready.
    """
    try:
        apps = client.AppsV1Api(api_client)
        resp = apps.list_deployment_for_all_namespaces(_request_timeout=15)
        results = []
        for dep in resp.items:
            containers = (dep.spec.template.spec.containers or []) if dep.spec else []
            images = [c.image for c in containers if c.image]
            if not any(_CIS_CONTROLLER_IMAGE in (img or "") for img in images):
                continue
            # Collect args from all containers
            args: list[str] = []
            for c in containers:
                if c.args:
                    args.extend(c.args)
                if c.command:
                    args.extend(c.command)
            results.append({
                "name": dep.metadata.name,
                "namespace": dep.metadata.namespace,
                "images": images,
                "args": args,
                "replicas_desired": (dep.spec.replicas or 0) if dep.spec else 0,
                "replicas_ready": (dep.status.ready_replicas or 0) if dep.status else 0,
            })
        return results
    except Exception as exc:
        logger.warning("Failed to fetch CIS controller deployments: %s", exc)
        return []


def _fetch_cis_crs(api_client, kind_plural: str, group: str = _CIS_API_GROUP) -> list[dict[str, Any]]:
    """Fetch CIS custom resources (VirtualServer / TransportServer / IngressLink).

    Returns a list of {name, namespace} dicts; full objects are not needed for inventory.
    """
    try:
        crd_api = client.CustomObjectsApi(api_client)
        resp = crd_api.list_cluster_custom_object(
            group=group,
            version="v1",
            plural=kind_plural,
            _request_timeout=10,
        )
        return [
            {
                "name": (item.get("metadata") or {}).get("name"),
                "namespace": (item.get("metadata") or {}).get("namespace"),
            }
            for item in (resp.get("items") or [])
        ]
    except client.ApiException as exc:
        if exc.status in (404, 405):
            return []
        logger.warning("Failed to fetch CIS %s CRs: %s", kind_plural, exc)
        return []
    except Exception as exc:
        logger.warning("Failed to fetch CIS %s CRs: %s", kind_plural, exc)
        return []


def _resolve_crd_resource_types(
    db,
    cluster_id: int,
    keys: list[str],
) -> dict[str, Any]:
    """Resolve a list of resource-type keys on the CALLING (main) thread.

    Must be called before submitting fetch workers to the ThreadPoolExecutor so
    that no worker thread ever touches the request-scoped SQLAlchemy Session.
    SQLAlchemy Sessions are not thread-safe; DB access inside a pool worker causes
    intermittent InvalidRequestError on clusters where discovery fires (e.g. Kamaji).

    Returns a dict {key: K8sResourceType} for successfully-resolved keys; keys that
    fail to resolve are omitted (caller treats missing key as empty result).
    """
    resolved: dict[str, Any] = {}
    for key in keys:
        try:
            resolved[key] = resolve_resource_type(db, cluster_id, key)
        except Exception:
            pass  # resolve failure → skip; _fetch_crd_resources_resolved returns []
    return resolved


def _fetch_cis_crs_full(api_client, kind_plural: str, group: str = _CIS_API_GROUP) -> list[dict]:
    """Fetch CIS custom resources with FULL spec — for translation (D-023 P3).

    Unlike _fetch_cis_crs (which returns {name, namespace} stubs), this
    returns the complete CR dict including spec.  All I/O lives here; the
    translator (proxy_translate_cis_service) stays pure.
    """
    try:
        crd_api = client.CustomObjectsApi(api_client)
        resp = crd_api.list_cluster_custom_object(
            group=group,
            version="v1",
            plural=kind_plural,
            _request_timeout=10,
        )
        return list(resp.get("items") or [])
    except client.ApiException as exc:
        if exc.status in (404, 405):
            return []
        logger.warning("Failed to fetch full CIS %s CRs: %s", kind_plural, exc)
        return []
    except Exception as exc:
        logger.warning("Failed to fetch full CIS %s CRs: %s", kind_plural, exc)
        return []


def _fetch_crd_resources(api_client, k8s_service, resource_type) -> list:
    """Fetch CRD resources for a pre-resolved K8sResourceType.

    resource_type must already be resolved on the main thread via
    _resolve_crd_resource_types — this function MUST NOT access k8s_service.db
    or call resolve_resource_type, since it runs inside a ThreadPoolExecutor worker.
    """
    try:
        return k8s_service._fetch_from_k8s(api_client, resource_type, None, None)
    except Exception:
        return []


def _fetch_cis_tlsprofiles_full(api_client) -> list[dict]:
    """Fetch CIS TLSProfile CRs with full spec — for VS tlsProfileName resolution (D-023 P4e).

    TLSProfile plural is ``tlsprofiles`` in the cis.f5.com API group.
    Returns [] on 404/405 (CRD not installed) and on any other exception.
    """
    return _fetch_cis_crs_full(api_client, "tlsprofiles")


def _fetch_cis_ingresslinks_full(api_client) -> list[dict]:
    """Fetch CIS IngressLink CRs with full spec — for IngressLink translation (D-023 P4e).

    IngressLink plural is ``ingresslinks`` in the cis.f5.com API group.
    Returns [] on 404/405 (CRD not installed) and on any other exception.
    """
    return _fetch_cis_crs_full(api_client, "ingresslinks")


def _fetch_cis_f5_ingresses(api_client) -> list[dict[str, Any]]:
    """Fetch Kubernetes Ingress objects that carry F5 CIS annotations.

    Client-side filtered: lists all Ingresses (networking.k8s.io is universal)
    and keeps only those with at least one annotation key starting with
    ``virtual-server.f5.com/`` or matching the CIS ingress-class convention
    (annotation ``kubernetes.io/ingress.class`` == "f5" / spec.ingressClassName == "f5").

    GATING: caller must supply a cheap preflight signal (has_cis OR any AS3
    ConfigMaps found OR CIS controller present) before calling this function.
    On a 500-pod non-CIS cluster the full Ingress list can be multi-MB; gating
    avoids that cost.

    Returns a list of plain dicts — each is a full Ingress object serialized
    as a dict with keys: ``metadata``, ``spec``, ``status``.
    """
    try:
        from kubernetes import client as k8s_client

        networking = k8s_client.NetworkingV1Api(api_client)
        resp = networking.list_ingress_for_all_namespaces(_request_timeout=15)
        results: list[dict[str, Any]] = []
        for ing in (resp.items or []):
            meta = ing.metadata or {}
            annotations: dict = dict(meta.annotations or {})
            spec = ing.spec or {}

            # Check for F5 CIS annotation prefix
            has_f5_annotation = any(
                k.startswith("virtual-server.f5.com/") for k in annotations
            )
            # Check for CIS ingress class (annotation or spec field)
            ingress_class_annotation = annotations.get("kubernetes.io/ingress.class", "")
            spec_class_name = getattr(spec, "ingress_class_name", None) or ""
            has_f5_class = ingress_class_annotation.lower() in ("f5", "f5-bigip") or \
                spec_class_name.lower() in ("f5", "f5-bigip")

            if not has_f5_annotation and not has_f5_class:
                continue

            # Serialize to plain dict
            ing_dict: dict[str, Any] = {
                "metadata": {
                    "name": meta.name or "",
                    "namespace": meta.namespace or "default",
                    "annotations": annotations,
                    "labels": dict(meta.labels or {}),
                },
                "spec": {},
                "status": {},
            }

            # spec.rules
            rules = []
            for rule in (spec.rules or []):
                rule_dict: dict[str, Any] = {}
                if rule.host:
                    rule_dict["host"] = rule.host
                if rule.http:
                    paths = []
                    for p in (rule.http.paths or []):
                        path_dict: dict[str, Any] = {
                            "path": p.path or "/",
                            "pathType": p.path_type or "Prefix",
                        }
                        if p.backend:
                            backend_dict: dict[str, Any] = {}
                            if p.backend.service:
                                svc = p.backend.service
                                port_info: dict[str, Any] = {}
                                if svc.port:
                                    if svc.port.number:
                                        port_info["number"] = svc.port.number
                                    if svc.port.name:
                                        port_info["name"] = svc.port.name
                                backend_dict["service"] = {
                                    "name": svc.name or "",
                                    "port": port_info,
                                }
                            path_dict["backend"] = backend_dict
                        paths.append(path_dict)
                    rule_dict["http"] = {"paths": paths}
                rules.append(rule_dict)
            ing_dict["spec"]["rules"] = rules

            # spec.tls
            tls_list = []
            for tls in (spec.tls or []):
                tls_dict: dict[str, Any] = {
                    "hosts": list(tls.hosts or []),
                    "secretName": tls.secret_name or "",
                }
                tls_list.append(tls_dict)
            if tls_list:
                ing_dict["spec"]["tls"] = tls_list

            # spec.defaultBackend
            if spec.default_backend:
                db_spec = spec.default_backend
                default_backend: dict[str, Any] = {}
                if db_spec.service:
                    port_info = {}
                    if db_spec.service.port:
                        if db_spec.service.port.number:
                            port_info["number"] = db_spec.service.port.number
                        if db_spec.service.port.name:
                            port_info["name"] = db_spec.service.port.name
                    default_backend["service"] = {
                        "name": db_spec.service.name or "",
                        "port": port_info,
                    }
                ing_dict["spec"]["defaultBackend"] = default_backend

            results.append(ing_dict)
        return results
    except Exception as exc:
        logger.warning("Failed to fetch CIS F5 Ingresses: %s", exc)
        return []


def _fetch_openshift_routes(api_client) -> list[dict[str, Any]]:
    """Fetch OpenShift Route objects (route.openshift.io/v1).

    Gated on ``has_routes = "route.openshift.io" in api_groups`` in
    ``fetch_scan_data`` — do NOT call on non-OpenShift clusters.

    Returns full Route objects as plain dicts with metadata + spec.
    API exception (404/405/etc.) → empty list (mirrors _fetch_cis_crs_full).
    """
    try:
        crd_api = client.CustomObjectsApi(api_client)
        resp = crd_api.list_cluster_custom_object(
            group="route.openshift.io",
            version="v1",
            plural="routes",
            _request_timeout=15,
        )
        return list(resp.get("items") or [])
    except client.ApiException as exc:
        if exc.status in (404, 405):
            return []
        logger.warning("Failed to fetch OpenShift Routes: %s", exc)
        return []
    except Exception as exc:
        logger.warning("Failed to fetch OpenShift Routes: %s", exc)
        return []


def _fetch_cis_as3_configmaps(api_client) -> list[dict[str, Any]]:
    """Fetch AS3 ConfigMaps labeled f5type=virtual-server (CIS AS3 mode).

    Run UNCONDITIONALLY — server-side label filter makes this near-zero cost
    on non-CIS clusters (the API server returns an empty list, not a 404).

    Each returned dict has:
        name, namespace, labels, template (parsed dict or None),
        template_parse_error (str or None — set when data.template is not
        valid JSON; caller sees an unmapped seed rather than a raised exception).
    """
    import json

    try:
        v1 = client.CoreV1Api(api_client)
        resp = v1.list_config_map_for_all_namespaces(
            label_selector="f5type=virtual-server",
            _request_timeout=10,
        )
        results: list[dict[str, Any]] = []
        for cm in (resp.items or []):
            name = (cm.metadata.name or "")
            namespace = (cm.metadata.namespace or "default")
            labels = dict(cm.metadata.labels or {})
            data = dict(cm.data or {})
            template_raw = data.get("template") or data.get("template.json") or ""
            template: dict | None = None
            template_parse_error: str | None = None
            if template_raw:
                try:
                    parsed = json.loads(template_raw)
                    if isinstance(parsed, dict):
                        template = parsed
                    else:
                        template_parse_error = f"template is not a JSON object (got {type(parsed).__name__})"
                except (ValueError, TypeError) as exc:
                    template_parse_error = f"JSON parse error: {exc}"
            results.append({
                "name": name,
                "namespace": namespace,
                "labels": labels,
                "template": template,
                "template_parse_error": template_parse_error,
            })
        return results
    except Exception as exc:
        logger.warning("Failed to fetch CIS AS3 ConfigMaps: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Main parallel fetch
# ---------------------------------------------------------------------------


def fetch_scan_data(
    api_client,
    k8s_service,
    cluster_id: int,
    *,
    extra_namespaces: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """
    Collect all scanner data in one parallel burst of K8s API calls.

    Returns a flat dict with all fetched data, ready for analysis functions.
    Uses up to 16 concurrent workers.

    Pass ``extra_namespaces`` (from ``cluster.discovered_namespaces``) to seed
    BNK pod discovery with namespaces where F5 components were found on a
    previous scan — these are merged with the static BNK_NAMESPACES seed.

    Gates BNK / DPF / Kamaji CRD fetches on a cheap API-group discovery
    preflight — skips ~12 sequential 404 round trips when those frameworks
    aren't installed on the cluster.
    """
    # Preflight: one /apis call (~100ms) tells us which API groups exist,
    # so we can skip whole fetch clusters that would 404 anyway AND scope
    # the CRD discovery to only the groups that both exist and matter.
    api_groups = _discover_api_groups(api_client)
    has_bnk = bool(_BNK_API_GROUPS & api_groups)
    has_dpf = bool(_DPF_API_GROUPS & api_groups)
    has_kamaji = _KAMAJI_API_GROUP in api_groups
    has_gateway_api = ApiGroups.GATEWAY in api_groups
    has_cis = _CIS_API_GROUP in api_groups
    has_routes = "route.openshift.io" in api_groups

    # Only query CRDs for groups that (a) the scanner's analyzers consume
    # AND (b) actually exist on the cluster. Replaces the 15+ MB full CRD
    # list fetch with a handful of small /apis/<group>/<version> calls.
    crd_target_groups = SCANNER_RELEVANT_CRD_GROUPS & api_groups

    # Resolve all CRD resource types on the MAIN thread before submitting to the
    # pool. CrdDiscoveryService (triggered when a key isn't in the static registry)
    # does a DB read on the request-scoped SQLAlchemy Session, which is NOT
    # thread-safe. No worker thread may call resolve_resource_type or access
    # k8s_service.db. Pre-resolve here; workers receive K8sResourceType objects.
    keys_to_resolve: list[str] = []
    if has_bnk:
        keys_to_resolve += ["cneinstance", "f5spkvlan"]
    if has_gateway_api:
        keys_to_resolve += ["gateway", "gatewayclass"]
    if has_dpf:
        keys_to_resolve += [
            "dpfoperatorconfig", "dpudevice", "dpuset",
            "dpucluster", "dpuservice", "bfb",
        ]
    if has_kamaji:
        # Use the FQ plural name — "tenantcontrolplane" (singular) never matches
        # the installed CRD plural "tenantcontrolplanes". The FQ key bypasses the
        # registry step-1 guard (not a registry key) and hits step-2 exact match.
        keys_to_resolve += ["tenantcontrolplanes.kamaji.clastix.io"]

    resolved_rts = _resolve_crd_resource_types(k8s_service.db, cluster_id, keys_to_resolve)

    def _rt(key):
        """Return the pre-resolved K8sResourceType for key, or None if resolve failed."""
        return resolved_rts.get(key)

    with ThreadPoolExecutor(max_workers=16) as pool:
        # Core cluster data (always fetched)
        version_f = pool.submit(_fetch_version, api_client)
        nodes_f = pool.submit(_fetch_nodes, api_client)
        crds_f = pool.submit(_fetch_crds, api_client, crd_target_groups)
        storage_f = pool.submit(_fetch_storage_classes, api_client)
        namespaces_f = pool.submit(_fetch_namespaces, api_client)
        daemonsets_f = pool.submit(_fetch_daemonsets, api_client)
        helm_f = pool.submit(_fetch_helm_releases, api_client)

        # BNK-specific: discover F5 pods across known namespaces (DRY with
        # health dashboard). Gate the cluster-wide sweep on BNK API group
        # presence — on a 500+-pod cluster the sweep is a multi-MB download
        # that finds zero F5 pods when BNK isn't installed.
        f5_pods_f = pool.submit(
            discover_f5_pods, api_client,
            include_sweep=has_bnk,
            extra_namespaces=extra_namespaces,
        )
        cert_manager_pods_f = pool.submit(_fetch_pods_in_ns, api_client, "cert-manager")
        kube_system_pods_f = pool.submit(_fetch_pods_in_ns, api_client, "kube-system")

        # BNK CRD resources — gated on BNK API group presence.
        if has_bnk:
            cneinstances_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("cneinstance"))
                if _rt("cneinstance") else None
            )
            vlans_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("f5spkvlan"))
                if _rt("f5spkvlan") else None
            )
        else:
            cneinstances_f = vlans_f = None

        # Gateway API CRDs — gated on gateway.networking.k8s.io presence.
        if has_gateway_api:
            gateways_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("gateway"))
                if _rt("gateway") else None
            )
            gatewayclasses_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("gatewayclass"))
                if _rt("gatewayclass") else None
            )
        else:
            gateways_f = gatewayclasses_f = None

        # DPF CRD resources — gated on DPF API group presence. Biggest win:
        # 6 fetches that otherwise 404 in sequence on non-DPF clusters.
        if has_dpf:
            dpf_operator_configs_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("dpfoperatorconfig"))
                if _rt("dpfoperatorconfig") else None
            )
            dpudevices_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("dpudevice"))
                if _rt("dpudevice") else None
            )
            dpusets_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("dpuset"))
                if _rt("dpuset") else None
            )
            dpuclusters_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("dpucluster"))
                if _rt("dpucluster") else None
            )
            dpuservices_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("dpuservice"))
                if _rt("dpuservice") else None
            )
            bfbs_f = (
                pool.submit(_fetch_crd_resources, api_client, k8s_service, _rt("bfb"))
                if _rt("bfb") else None
            )
        else:
            dpf_operator_configs_f = dpudevices_f = dpusets_f = None
            dpuclusters_f = dpuservices_f = bfbs_f = None

        # Kamaji resources — gated on kamaji.clastix.io presence.
        if has_kamaji:
            kamaji_pods_f = pool.submit(_fetch_pods_in_ns, api_client, "kamaji-system")
            kamaji_tcps_f = (
                pool.submit(
                    _fetch_crd_resources, api_client, k8s_service,
                    _rt("tenantcontrolplanes.kamaji.clastix.io"),
                )
                if _rt("tenantcontrolplanes.kamaji.clastix.io") else None
            )
        else:
            kamaji_pods_f = kamaji_tcps_f = None

        # CIS (Container Ingress Services) resources — gated on cis.f5.com presence.
        # Controller lookup adds a cluster-wide deployment list; gate it so non-CIS
        # clusters pay zero cost.
        if has_cis:
            cis_controllers_f = pool.submit(_fetch_cis_controller, api_client)
            cis_virtualservers_f = pool.submit(_fetch_cis_crs, api_client, "virtualservers")
            cis_transportservers_f = pool.submit(_fetch_cis_crs, api_client, "transportservers")
            cis_ingresslinks_f = pool.submit(_fetch_cis_crs, api_client, "ingresslinks")
        else:
            cis_controllers_f = cis_virtualservers_f = None
            cis_transportservers_f = cis_ingresslinks_f = None

        # CIS AS3 ConfigMaps — unconditional server-side label filter (near-zero cost
        # on non-CIS clusters; the API server returns an empty list, not a 404).
        cis_as3_configmaps_f = pool.submit(_fetch_cis_as3_configmaps, api_client)

        # CIS F5 Ingresses — client-side filtered Ingress list.
        # Gate on a cheap preflight signal: cis.f5.com API group present OR CIS
        # controller was requested (has_cis).  On non-CIS clusters this is a no-op.
        # The ConfigMap future result is NOT used here to avoid intra-burst deps.
        if has_cis:
            cis_f5_ingresses_f = pool.submit(_fetch_cis_f5_ingresses, api_client)
        else:
            cis_f5_ingresses_f = None

        # OpenShift Routes — gated on route.openshift.io API group presence.
        if has_routes:
            openshift_routes_f = pool.submit(_fetch_openshift_routes, api_client)
        else:
            openshift_routes_f = None

    def _or_empty(future):
        return future.result() if future is not None else []

    f5_tenant_pods, f5_utils_pods = f5_pods_f.result()
    crds = crds_f.result()

    return {
        "version_info": version_f.result(),
        "nodes": nodes_f.result(),
        "crds": crds,
        "crd_names": {c["name"] for c in crds},
        "crd_groups": {c["group"] for c in crds},
        "storage_classes": storage_f.result(),
        "namespaces": namespaces_f.result(),
        "daemonsets": daemonsets_f.result(),
        "helm_releases": helm_f.result(),
        "f5_tenant_pods": f5_tenant_pods,
        "f5_utils_pods": f5_utils_pods,
        "cert_manager_pods": cert_manager_pods_f.result(),
        "kube_system_pods": kube_system_pods_f.result(),
        "cneinstances": _or_empty(cneinstances_f),
        "vlans": _or_empty(vlans_f),
        "gateways": _or_empty(gateways_f),
        "gatewayclasses": _or_empty(gatewayclasses_f),
        # DPF resources
        "dpf_operator_configs": _or_empty(dpf_operator_configs_f),
        "dpudevices": _or_empty(dpudevices_f),
        "dpusets": _or_empty(dpusets_f),
        "dpuclusters": _or_empty(dpuclusters_f),
        "dpuservices": _or_empty(dpuservices_f),
        "bfbs": _or_empty(bfbs_f),
        # Kamaji resources
        "kamaji_pods": _or_empty(kamaji_pods_f),
        "kamaji_tcps": _or_empty(kamaji_tcps_f),
        # CIS resources — only populated when cis.f5.com ∈ api_groups
        "cis_controllers": _or_empty(cis_controllers_f),
        "cis_virtualservers": _or_empty(cis_virtualservers_f),
        "cis_transportservers": _or_empty(cis_transportservers_f),
        "cis_ingresslinks": _or_empty(cis_ingresslinks_f),
        # CIS AS3 ConfigMaps — always fetched (server-side label filter)
        "cis_as3_configmaps": cis_as3_configmaps_f.result(),
        # CIS F5 Ingresses — gated on has_cis
        "cis_f5_ingresses": _or_empty(cis_f5_ingresses_f),
        # OpenShift Routes — gated on has_routes (route.openshift.io ∈ api_groups)
        "openshift_routes": _or_empty(openshift_routes_f),
    }
