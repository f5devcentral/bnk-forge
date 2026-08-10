"""Kubernetes-engine drift detection service.

Catalog modules are now the source of truth. Legacy Python module registry
lookups were removed, and K8s drift checks currently return a graceful
"not available" result until catalog-based desired-state rendering is wired in.
"""

import asyncio
import logging
import time
from copy import deepcopy
from typing import Any

logger = logging.getLogger(__name__)

# Fields to ignore when comparing manifests — K8s adds these automatically
IGNORED_METADATA_FIELDS = {
    "resourceVersion", "uid", "creationTimestamp", "generation",
    "managedFields", "selfLink", "annotations",
}

# Annotation keys to ignore (added by server-side apply, kubectl, etc.)
IGNORED_ANNOTATIONS = {
    "kubectl.kubernetes.io/last-applied-configuration",
    "deployment.kubernetes.io/revision",
    "control-plane.alpha.kubernetes.io/leader",
}

# Top-level fields to ignore in comparison
IGNORED_TOP_LEVEL = {"status", "metadata"}


def _k8s_catalog_drift_unavailable(
    module_path: str,
    start: float,
    reason: str,
) -> dict[str, Any]:
    """Return a standardized response when K8s catalog drift is unavailable."""
    return {
        "drift_detected": False,
        "resource_changes": {"add": 0, "change": 0, "destroy": 0, "ok": 0},
        "changed_resources": [],
        "summary": f"K8s drift check not available for module {module_path}: {reason}",
        "check_duration_ms": int((time.monotonic() - start) * 1000),
    }


def _normalize_for_comparison(desired: dict, actual: dict) -> tuple[dict, dict]:
    """
    Normalize desired and actual manifests for meaningful comparison.

    Strips K8s-added fields from actual, and ensures both have matching
    structure for diff. Only compares spec-level fields.
    """
    d = deepcopy(desired)
    a = deepcopy(actual)

    # Remove status (always differs, not user-controlled)
    a.pop("status", None)
    d.pop("status", None)

    # Normalize metadata — only compare labels from desired
    d_meta = d.get("metadata", {})
    a_meta = a.get("metadata", {})

    # Keep only the fields we care about in metadata
    clean_d_meta = {
        "name": d_meta.get("name"),
        "namespace": d_meta.get("namespace"),
    }
    clean_a_meta = {
        "name": a_meta.get("name"),
        "namespace": a_meta.get("namespace"),
    }

    # Compare labels if desired specifies them
    if "labels" in d_meta:
        clean_d_meta["labels"] = d_meta["labels"]
        clean_a_meta["labels"] = {
            k: v for k, v in a_meta.get("labels", {}).items()
            if k in d_meta["labels"]
        }

    d["metadata"] = clean_d_meta
    a["metadata"] = clean_a_meta

    return d, a


def _diff_dicts(desired: Any, actual: Any, path: str = "") -> list[dict[str, Any]]:
    """
    Recursively diff two dicts/values and return list of differences.

    Each difference: {"path": "spec.replicas", "desired": 3, "actual": 2, "type": "changed"}
    """
    diffs = []

    if isinstance(desired, dict) and isinstance(actual, dict):
        all_keys = set(desired.keys()) | set(actual.keys())
        for key in sorted(all_keys):
            child_path = f"{path}.{key}" if path else key
            if key in desired and key not in actual:
                diffs.append({
                    "path": child_path,
                    "desired": desired[key],
                    "actual": None,
                    "type": "missing",
                })
            elif key not in desired and key in actual:
                # Actual has extra field — K8s added it, not drift
                pass
            else:
                diffs.extend(_diff_dicts(desired[key], actual[key], child_path))
    elif isinstance(desired, list) and isinstance(actual, list):
        # For lists, compare element-by-element up to desired length
        for i in range(max(len(desired), len(actual))):
            child_path = f"{path}[{i}]"
            if i >= len(actual):
                diffs.append({
                    "path": child_path,
                    "desired": desired[i],
                    "actual": None,
                    "type": "missing",
                })
            elif i >= len(desired):
                # Extra elements in actual — not drift, K8s may add defaults
                pass
            else:
                diffs.extend(_diff_dicts(desired[i], actual[i], child_path))
    else:
        # Leaf comparison — coerce types for comparison (K8s often returns strings for ints)
        d_str = str(desired) if desired is not None else None
        a_str = str(actual) if actual is not None else None
        if d_str != a_str:
            diffs.append({
                "path": path,
                "desired": desired,
                "actual": actual,
                "type": "changed",
            })

    return diffs


def check_manifest_drift(
    kubeconfig_path: str,
    module_path: str,
    variables: dict[str, Any],
    *,
    lib_module: Any | None = None,
) -> dict[str, Any]:
    """
    Check drift for a manifest-type K8s module.

    Args:
        kubeconfig_path: Path to kubeconfig file
        module_path: Module path (e.g., "k8s/network-setup")
        variables: Resolved variables for the module

    Returns:
        dict: {
            "drift_detected": bool,
            "resource_changes": {"add": int, "change": int, "destroy": int, "ok": int},
            "changed_resources": [{"address": str, "action": str, "diffs": [...]}],
            "summary": str,
            "check_duration_ms": int,
        }
    """
    start = time.monotonic()

    if lib_module is None:
        return _k8s_catalog_drift_unavailable(module_path, start, "no catalog metadata")

    _ = kubeconfig_path, variables, lib_module
    return _k8s_catalog_drift_unavailable(module_path, start, "manifest drift not implemented for catalog modules")


def check_helm_drift(
    kubeconfig_path: str,
    module_path: str,
    variables: dict[str, Any],
    *,
    lib_module: Any | None = None,
) -> dict[str, Any]:
    """
    Check drift for a helm-type K8s module.

    Compares the rendered helm values against the currently deployed release values,
    and checks the chart version.
    """
    start = time.monotonic()

    if lib_module is None:
        return _k8s_catalog_drift_unavailable(module_path, start, "no catalog metadata")

    _ = kubeconfig_path, variables, lib_module
    return _k8s_catalog_drift_unavailable(module_path, start, "helm drift not implemented for catalog modules")


def check_k8s_module_drift(
    kubeconfig_path: str,
    module_path: str,
    variables: dict[str, Any],
    *,
    lib_module: Any | None = None,
) -> dict[str, Any]:
    """
    Check drift for any K8s-engine module (manifest or helm).

    Dispatches to check_manifest_drift or check_helm_drift based on module type.
    """
    if lib_module is None:
        return _k8s_catalog_drift_unavailable(module_path, time.monotonic(), "no catalog metadata")

    deploy_model = getattr(lib_module, "deploy_model", None)
    normalized_deploy_model = deploy_model.strip().lower() if isinstance(deploy_model, str) else "manifest"

    if normalized_deploy_model == "helm":
        return check_helm_drift(kubeconfig_path, module_path, variables, lib_module=lib_module)

    return check_manifest_drift(kubeconfig_path, module_path, variables, lib_module=lib_module)


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """Get or create an event loop for the current thread."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop
