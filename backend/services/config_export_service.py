"""
BNK Configuration Export/Import/Diff Service.

Exports a complete YAML snapshot of a cluster's BNK configuration:
  - All F5 BNK CRDs (data-plane, gateway extension, FLO-managed)
  - Gateway API resources (GatewayClass, Gateway, HTTPRoute, etc.)
  - Module variables and deployment metadata from BNK-Forge

Import applies an exported snapshot to a different cluster.
Diff compares two cluster configs to highlight differences.

Usage:
    from services.config_export_service import export_cluster_config

    config = export_cluster_config(cluster_id, db)
    yaml_str = config_to_yaml(config)
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# API group → export category for discovery-driven export type list.
# k8s.f5net.com covers multiple sub-categories; they're merged into bnk_data_plane
# by default — categories like bnk_firewall/bnk_irules/bnk_logging/bnk_ai are
# present in the STATIC fallback only (discovery can't infer sub-categories from groups alone).
_EXPORT_GROUP_TO_CATEGORY: dict[str, str] = {
    "gateway.networking.k8s.io": "gateway_api",
    "gateway.k8s.f5net.com": "bnk_security",
    "k8s.f5net.com": "bnk_data_plane",
    "k8s.f5.com": "bnk_flo",
    "provisioning.dpu.nvidia.com": "dpf_provisioning",
    "svc.dpu.nvidia.com": "dpf_services",
    "operator.dpu.nvidia.com": "dpf_provisioning",
}

_EXPORT_TARGET_GROUPS: list[str] = list(_EXPORT_GROUP_TO_CATEGORY.keys())


def _build_export_types_from_discovery(cluster_id: int, db) -> "dict[str, list[dict]] | None":
    """Build export resource type list from CRD discovery using the cluster's served version.

    Returns None when discovery is unavailable (cluster unreachable or empty),
    signalling callers to fall back to the static EXPORT_RESOURCE_TYPES table.
    Adding a new CRD in a recognized API group requires no code change when this succeeds.
    """
    try:
        from services.crd_discovery_service import CrdDiscoveryService  # noqa: PLC0415
        svc = CrdDiscoveryService(db)
        envelope = svc.list_crds(cluster_id, group_filter=_EXPORT_TARGET_GROUPS)
        if not envelope.crds:
            return None

        result: dict[str, list[dict]] = {}
        for crd in envelope.crds:
            if not crd.group or not crd.version or not crd.plural:
                continue
            category = _EXPORT_GROUP_TO_CATEGORY.get(crd.group)
            if not category:
                continue
            entry = {
                "api_version": f"{crd.group}/{crd.version}",
                "kind": crd.kind,
                "plural": crd.plural,
                "namespaced": crd.namespaced,
            }
            result.setdefault(category, []).append(entry)

        return result if result else None
    except Exception:
        return None


# Resource types to export — grouped by category (static fallback when discovery is unavailable)
EXPORT_RESOURCE_TYPES = {
    "gateway_api": [
        {"api_version": "gateway.networking.k8s.io/v1", "kind": "GatewayClass", "plural": "gatewayclasses", "namespaced": False},
        {"api_version": "gateway.networking.k8s.io/v1", "kind": "Gateway", "plural": "gateways", "namespaced": True},
        {"api_version": "gateway.networking.k8s.io/v1", "kind": "HTTPRoute", "plural": "httproutes", "namespaced": True},
    ],
    "bnk_security": [
        {"api_version": "gateway.k8s.f5net.com/v1alpha1", "kind": "BNKSecPolicy", "plural": "bnksecpolicies", "namespaced": True},
        {"api_version": "gateway.k8s.f5net.com/v1alpha1", "kind": "BNKNetPolicy", "plural": "bnknetpolicies", "namespaced": True},
        {"api_version": "gateway.k8s.f5net.com/v1", "kind": "L4Route", "plural": "l4routes", "namespaced": True},
    ],
    "bnk_data_plane": [
        {"api_version": "k8s.f5net.com/v1", "kind": "F5SPKVlan", "plural": "f5-spk-vlans", "namespaced": True},
        {"api_version": "k8s.f5net.com/v1", "kind": "F5SPKStaticRoute", "plural": "f5-spk-staticroutes", "namespaced": True},
        {"api_version": "k8s.f5net.com/v1", "kind": "F5SPKSnatpool", "plural": "f5-spk-snatpools", "namespaced": True},
        {"api_version": "k8s.f5net.com/v3", "kind": "F5SPKEgress", "plural": "f5-spk-egresses", "namespaced": True},
        {"api_version": "k8s.f5net.com/v1", "kind": "F5BigGlobalOptions", "plural": "f5-big-global-optionses", "namespaced": True},
    ],
    "bnk_firewall": [
        {"api_version": "k8s.f5net.com/v1", "kind": "F5BigFwPolicy", "plural": "f5-big-fw-policies", "namespaced": True},
        {"api_version": "k8s.f5net.com/v1", "kind": "F5BigFwRulelist", "plural": "f5-big-fw-rulelists", "namespaced": True},
        {"api_version": "k8s.f5net.com/v1", "kind": "F5BigCneAddresslist", "plural": "f5-big-cne-addresslists", "namespaced": True},
        {"api_version": "k8s.f5net.com/v1", "kind": "F5BigCnePortlist", "plural": "f5-big-cne-portlists", "namespaced": True},
    ],
    "bnk_irules": [
        {"api_version": "k8s.f5net.com/v1", "kind": "F5BigCneIrule", "plural": "f5-big-cne-irules", "namespaced": True},
    ],
    "bnk_logging": [
        {"api_version": "k8s.f5net.com/v2", "kind": "F5BigLogHslpub", "plural": "f5-big-log-hslpubs", "namespaced": True},
        {"api_version": "k8s.f5net.com/v2", "kind": "F5BigLogProfile", "plural": "f5-big-log-profiles", "namespaced": True},
    ],
    "bnk_ai": [
        {"api_version": "k8s.f5net.com/v1alpha1", "kind": "F5BigAnalyzer", "plural": "f5-big-analyzers", "namespaced": True},
    ],
    "bnk_flo": [
        {"api_version": "k8s.f5.com/v1", "kind": "CNEInstance", "plural": "cneinstances", "namespaced": True},
    ],
    # DPF provisioning (NVIDIA DPU infrastructure config — excludes physical inventory)
    "dpf_provisioning": [
        {"api_version": "operator.dpu.nvidia.com/v1alpha1", "kind": "DPFOperatorConfig", "plural": "dpfoperatorconfigs", "namespaced": True},
        {"api_version": "provisioning.dpu.nvidia.com/v1alpha1", "kind": "DPUCluster", "plural": "dpuclusters", "namespaced": True},
        {"api_version": "provisioning.dpu.nvidia.com/v1alpha1", "kind": "DPUSet", "plural": "dpusets", "namespaced": True},
        {"api_version": "provisioning.dpu.nvidia.com/v1alpha1", "kind": "BFB", "plural": "bfbs", "namespaced": True},
        {"api_version": "provisioning.dpu.nvidia.com/v1alpha1", "kind": "DPUFlavor", "plural": "dpuflavors", "namespaced": True},
    ],
    # DPF services (NVIDIA DPU service layer — Helm deployments, chains, interfaces)
    "dpf_services": [
        {"api_version": "svc.dpu.nvidia.com/v1alpha1", "kind": "DPUService", "plural": "dpuservices", "namespaced": True},
        {"api_version": "svc.dpu.nvidia.com/v1alpha1", "kind": "DPUDeployment", "plural": "dpudeployments", "namespaced": True},
        {"api_version": "svc.dpu.nvidia.com/v1alpha1", "kind": "DPUServiceChain", "plural": "dpuservicechains", "namespaced": True},
        {"api_version": "svc.dpu.nvidia.com/v1alpha1", "kind": "DPUServiceInterface", "plural": "dpuserviceinterfaces", "namespaced": True},
        {"api_version": "svc.dpu.nvidia.com/v1alpha1", "kind": "DPUServiceIPAM", "plural": "dpuserviceipams", "namespaced": True},
    ],
}

# Fields to strip from exported resources (K8s-managed, not user config)
STRIP_METADATA_FIELDS = [
    "uid", "resourceVersion", "creationTimestamp", "generation",
    "managedFields", "selfLink", "ownerReferences",
]

STRIP_ANNOTATIONS = [
    "kubectl.kubernetes.io/last-applied-configuration",
]


def _clean_resource(resource: dict) -> dict:
    """
    Strip K8s-managed fields from a resource for clean export.
    Keeps: apiVersion, kind, metadata (name, namespace, labels, annotations), spec.
    Strips: status, managedFields, uid, resourceVersion, etc.
    """
    cleaned = deepcopy(resource)

    # Remove status
    cleaned.pop("status", None)

    # Clean metadata
    meta = cleaned.get("metadata", {})
    for field in STRIP_METADATA_FIELDS:
        meta.pop(field, None)

    # Clean annotations
    annotations = meta.get("annotations", {})
    for anno in STRIP_ANNOTATIONS:
        annotations.pop(anno, None)
    if not annotations:
        meta.pop("annotations", None)

    return cleaned


def _parse_api_version(api_version_str: str):
    """Parse 'group/version' into (group, version). Returns ('', version) for core API."""
    if "/" in api_version_str:
        group, version = api_version_str.rsplit("/", 1)
        return group, version
    return "", api_version_str


def _fetch_resources(custom_api, resource_type: dict) -> list[dict]:
    """Fetch all resources of a given type from the cluster using kubernetes client."""
    from kubernetes.client.rest import ApiException

    try:
        kind = resource_type["kind"]
        group, version = _parse_api_version(resource_type["api_version"])
        plural = resource_type["plural"]

        if not group:
            # Core API resources — shouldn't happen for BNK CRDs but handle anyway
            logger.warning(f"Core API export not supported for {kind}, skipping")
            return []

        if resource_type["namespaced"]:
            # Fetch from all namespaces
            response = custom_api.list_cluster_custom_object(
                group=group,
                version=version,
                plural=plural,
            )
        else:
            response = custom_api.list_cluster_custom_object(
                group=group,
                version=version,
                plural=plural,
            )

        items = response.get("items", [])
        result = []
        for item in items:
            # Ensure kind/apiVersion are set (list responses may omit them)
            if not item.get("kind"):
                item["kind"] = kind
            if not item.get("apiVersion"):
                item["apiVersion"] = resource_type["api_version"]
            result.append(_clean_resource(item))
        return result

    except ApiException as e:
        if e.status == 404:
            # CRD not installed — skip silently
            return []
        logger.warning(f"Error fetching {resource_type.get('kind', '?')}: {e.reason}")
        return []
    except Exception as e:
        error_str = str(e)
        if "404" in error_str or "resource type" in error_str.lower():
            return []
        logger.warning(f"Error fetching {resource_type.get('kind', '?')}: {e}")
        return []


def apply_resources(db, cluster_id: int, custom_api, resources: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """
    Server-side-apply a category -> [resources] map to a cluster via `custom_api`.

    Extracted from the `/bnk/import` route handler (`routes/config_export.py`)
    so both the legacy import path and the use-case-artifact apply path
    (`services/usecase_artifact_service.apply_usecase_artifact`) share one
    write path — same `results` shape, same `field_manager`/`force` semantics,
    same 404-to-skipped handling.
    """
    from kubernetes.client.rest import ApiException

    results: dict[str, list[dict]] = {
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

    return results


def export_cluster_config(cluster_id: int, db) -> dict[str, Any]:
    """
    Export complete BNK configuration from a cluster.

    Uses the kubernetes Python client (already installed) instead of kr8s.
    Returns a dict structure that can be serialized to YAML.
    """
    from kubernetes import client as k8s_client

    from models import KubernetesCluster, Project, ProjectModule
    from services.kubernetes_service import KubernetesService

    start = time.monotonic()

    cluster = db.query(KubernetesCluster).filter(
        KubernetesCluster.id == cluster_id
    ).first()
    if not cluster:
        raise ValueError(f"Cluster {cluster_id} not found")

    # Use KubernetesService to get a properly configured API client
    # (handles SSH tunnels, EKS auth, kubeconfig decryption, etc.)
    k8s_svc = KubernetesService(db)
    api_client = k8s_svc.load_kubeconfig(cluster)

    # Fetch K8s version
    try:
        version_api = k8s_client.VersionApi(api_client)
        version_info = version_api.get_code()
        k8s_version = f"{version_info.major}.{version_info.minor}"
    except Exception:
        k8s_version = "unknown"

    # Create CustomObjectsApi for CRD fetching
    custom_api = k8s_client.CustomObjectsApi(api_client)

    # Fetch all resource types in parallel
    all_resources: dict[str, list[dict]] = {}
    resource_counts: dict[str, int] = {}

    # Build export type list: try discovery first, fall back to static table
    export_types = _build_export_types_from_discovery(cluster_id, db) or EXPORT_RESOURCE_TYPES

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {}
        for category, types in export_types.items():
            for rt in types:
                future = executor.submit(_fetch_resources, custom_api, rt)
                futures[future] = (category, rt["kind"])

        for future in as_completed(futures):
            category, kind = futures[future]
            try:
                resources = future.result()
                if resources:
                    if category not in all_resources:
                        all_resources[category] = []
                    all_resources[category].extend(resources)
                    resource_counts[kind] = len(resources)
            except Exception as e:
                logger.warning(f"Failed to fetch {kind}: {e}")

    # Get module variables from BNK-Forge (if project linked)
    module_config = {}
    project = None
    if cluster.project_id:
        project = db.query(Project).filter(Project.id == cluster.project_id).first()
        if project:
            modules = db.query(ProjectModule).filter(
                ProjectModule.project_id == project.id,
                ProjectModule.status.in_(["applied", "initialized", "planned"]),
            ).all()

            for mod in modules:
                module_config[mod.path_in_project] = {
                    "variables": mod.variables or {},
                    "status": mod.status,
                    "outputs": mod.outputs or {},
                }

    duration_ms = int((time.monotonic() - start) * 1000)

    return {
        "bnk_forge_export": {
            "version": "1.0",
            "exported_at": datetime.now(UTC).isoformat() + "Z",
            "cluster": {
                "name": cluster.name,
                "id": cluster.id,
                "k8s_version": k8s_version,
                "api_server": cluster.api_server or "",
                "cloud_provider": cluster.cloud_provider or "",
            },
            "project": {
                "name": project.name if project else "",
                "id": project.id if project else None,
                "project_type": project.project_type if project else None,
            },
            "export_metadata": {
                "resource_counts": resource_counts,
                "total_resources": sum(resource_counts.values()),
                "categories": list(all_resources.keys()),
                "duration_ms": duration_ms,
            },
        },
        "resources": all_resources,
        "module_config": module_config,
    }


def config_to_yaml(config: dict[str, Any]) -> str:
    """Convert export config to YAML string."""
    return yaml.dump(config, default_flow_style=False, sort_keys=False, allow_unicode=True)


def diff_configs(config_a: dict[str, Any], config_b: dict[str, Any]) -> dict[str, Any]:
    """
    Compare two exported cluster configs and return differences.

    Returns:
        dict: {
            "summary": str,
            "resource_diffs": [...],
            "only_in_a": [...],
            "only_in_b": [...],
            "changed": [...],
        }
    """
    resources_a = _flatten_resources(config_a.get("resources", {}))
    resources_b = _flatten_resources(config_b.get("resources", {}))

    keys_a = set(resources_a.keys())
    keys_b = set(resources_b.keys())

    only_in_a = sorted(keys_a - keys_b)
    only_in_b = sorted(keys_b - keys_a)
    common = keys_a & keys_b

    changed = []
    for key in sorted(common):
        spec_a = resources_a[key].get("spec", {})
        spec_b = resources_b[key].get("spec", {})
        if spec_a != spec_b:
            changed.append({
                "resource": key,
                "kind": resources_a[key].get("kind", "Unknown"),
                "spec_a": spec_a,
                "spec_b": spec_b,
            })

    # Also diff module config
    config_a_mods = config_a.get("module_config", {})
    config_b_mods = config_b.get("module_config", {})
    mod_only_a = sorted(set(config_a_mods.keys()) - set(config_b_mods.keys()))
    mod_only_b = sorted(set(config_b_mods.keys()) - set(config_a_mods.keys()))
    mod_changed = []
    for path in sorted(set(config_a_mods.keys()) & set(config_b_mods.keys())):
        vars_a = config_a_mods[path].get("variables", {})
        vars_b = config_b_mods[path].get("variables", {})
        if vars_a != vars_b:
            mod_changed.append({
                "module_path": path,
                "variables_a": vars_a,
                "variables_b": vars_b,
            })

    total_diffs = len(only_in_a) + len(only_in_b) + len(changed) + len(mod_only_a) + len(mod_only_b) + len(mod_changed)

    cluster_a_name = config_a.get("bnk_forge_export", {}).get("cluster", {}).get("name", "Cluster A")
    cluster_b_name = config_b.get("bnk_forge_export", {}).get("cluster", {}).get("name", "Cluster B")

    if total_diffs == 0:
        summary = f"No differences between '{cluster_a_name}' and '{cluster_b_name}'"
    else:
        summary = f"{total_diffs} difference(s) between '{cluster_a_name}' and '{cluster_b_name}'"

    return {
        "summary": summary,
        "total_diffs": total_diffs,
        "cluster_a": cluster_a_name,
        "cluster_b": cluster_b_name,
        "resources": {
            "only_in_a": only_in_a,
            "only_in_b": only_in_b,
            "changed": changed,
        },
        "modules": {
            "only_in_a": mod_only_a,
            "only_in_b": mod_only_b,
            "changed": mod_changed,
        },
    }


def _flatten_resources(resources: dict[str, list[dict]]) -> dict[str, dict]:
    """Flatten category→[resources] into {kind/name: resource} map."""
    flat = {}
    for _category, resource_list in resources.items():
        for resource in resource_list:
            kind = resource.get("kind", "Unknown")
            name = resource.get("metadata", {}).get("name", "unknown")
            ns = resource.get("metadata", {}).get("namespace", "")
            key = f"{kind}/{ns}/{name}" if ns else f"{kind}/{name}"
            flat[key] = resource
    return flat
