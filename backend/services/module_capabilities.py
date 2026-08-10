"""Shared module engine/capability serialization helpers."""

from __future__ import annotations

from models import ModuleLibrary
from services.module_metadata import REQUIRED_PACK_LIFECYCLE_FIELDS


def _get_python_module(module: ModuleLibrary) -> object | None:
    """Return the Python module class instance for SSH (bare-metal) modules, or None.

    Bare-metal modules are registered under their path (e.g. "bare-metal/probe-dpu")
    in the Python registry maintained by ``modules.get_module_registry()``.  Non-SSH
    modules (git catalog rows) are not present in the registry and will return None.
    """
    try:
        from modules import get_module_registry

        registry = get_module_registry()
        return registry.get(module.path)
    except Exception:  # pragma: no cover — guard against import errors in tests
        return None


def _resolve_module_type(
    module: ModuleLibrary,
    *,
    module_source_kind: str | None,
) -> str:
    """Resolve module type from catalog metadata."""
    deploy_model = get_deploy_model(module)
    if deploy_model == "helm":
        return "helm_chart"
    if deploy_model == "manifests":
        return "manifest"

    return "terraform"


def get_lifecycle_capabilities(module: ModuleLibrary) -> dict[str, bool] | None:
    """Extract bounded lifecycle capability booleans from stored pack metadata."""
    if not isinstance(module.pack_manifest, dict):
        return None

    deployment_pack = module.pack_manifest.get("deployment_pack")
    if not isinstance(deployment_pack, dict):
        return None

    lifecycle = deployment_pack.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return None

    capabilities = {
        key: value
        for key in REQUIRED_PACK_LIFECYCLE_FIELDS
        if isinstance((value := lifecycle.get(key)), bool)
    }

    return capabilities or None


def get_execution_engine(
    module: ModuleLibrary,
) -> str:
    """Return normalized execution engine with metadata preference + bounded fallback."""
    module_source_kind = (
        module.module_source_kind.strip().lower()
        if isinstance(module.module_source_kind, str)
        else None
    )

    stored_execution_engine = (
        module.execution_engine.strip().lower()
        if isinstance(module.execution_engine, str)
        else None
    )
    if stored_execution_engine:
        return stored_execution_engine

    stored_engine = module.engine_type.strip().lower() if isinstance(module.engine_type, str) else None
    if stored_engine:
        if stored_engine == "kubernetes":
            if module_source_kind == "builtin":
                return "kubernetes-direct"
        else:
            return stored_engine

    if module_source_kind == "builtin":
        return "kubernetes-direct"

    return "opentofu"


def get_engine_type(
    module: ModuleLibrary,
) -> str:
    """Return backward-compatible engine alias for existing API/runtime callers."""
    execution_engine = get_execution_engine(module)
    if execution_engine in {"kubernetes-direct", "operator"}:
        return "kubernetes"
    return execution_engine


def get_deploy_model(module: ModuleLibrary) -> str | None:
    """Return canonical deploy model from stored metadata when available."""
    deploy_model = module.deploy_model.strip().lower() if isinstance(module.deploy_model, str) else None
    if deploy_model:
        return deploy_model

    if isinstance(module.pack_manifest, dict):
        deployment_pack = module.pack_manifest.get("deployment_pack")
        if isinstance(deployment_pack, dict):
            entrypoints = deployment_pack.get("entrypoints")
            entrypoints = entrypoints if isinstance(entrypoints, dict) else {}
            if isinstance(entrypoints.get("chart_path"), str) and entrypoints.get("chart_path", "").strip():
                return "helm"
            if isinstance(entrypoints.get("manifest_path"), str) and entrypoints.get("manifest_path", "").strip():
                return "manifests"

    return None


def serialize_engine_metadata(
    module: ModuleLibrary,
    *,
    include_module_classification: bool = False,
) -> dict:
    """Serialize engine metadata for API responses."""
    metadata: dict[str, object] = {
        "engine_type": get_engine_type(module),
        "execution_engine": get_execution_engine(module),
    }

    module_source_kind = (
        module.module_source_kind.strip().lower()
        if isinstance(module.module_source_kind, str)
        else None
    )
    if module_source_kind:
        metadata["module_source_kind"] = module_source_kind

    deploy_model = get_deploy_model(module)
    if deploy_model:
        metadata["deploy_model"] = deploy_model

    lifecycle_capabilities = get_lifecycle_capabilities(module)
    if lifecycle_capabilities is not None:
        metadata["lifecycle_capabilities"] = lifecycle_capabilities

    # Expose SSH module connectivity risk metadata for UI badges
    py_mod = _get_python_module(module)
    if py_mod is not None and getattr(py_mod, "connectivity_risk", False):
        metadata["connectivity_risk"] = True

    # Expose phase grouping for SSH modules (used by frontend to render groups)
    phase = getattr(py_mod, "phase", None) if py_mod is not None else None
    if phase:
        metadata["phase"] = phase

    if include_module_classification:
        metadata["is_builtin"] = module_source_kind == "builtin"
        metadata["module_type"] = _resolve_module_type(
            module,
            module_source_kind=module_source_kind,
        )

    return metadata
