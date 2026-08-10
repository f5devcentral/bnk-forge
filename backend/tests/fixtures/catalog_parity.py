"""ADR-204 parity harness — render the catalog path from the pinned snapshot.

The catalog modules 18–25 render via ``build_manifest_payload`` /
``build_helm_payload`` from pack content fetched from the external
``bnk-forge-modules`` catalog. That content is vendored into
``catalog_snapshot/`` (pinned to ``CATALOG_SHA``) so the ADR-204 parity gate is a
*concrete* diff — not a guess — between each ``bare-metal/bnk-*`` SSH port's
render and the catalog render for the DPU case.

Usage in a parity test::

    from tests.fixtures.catalog_parity import render_catalog_manifests
    catalog = render_catalog_manifests("k8s/network-setup", dpu_vars)
    ours = NetworkSetupSSHModule().render_manifests(dpu_vars)
    assert normalize(ours) == normalize(catalog)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.execution.engine_interface import ModuleContext
from services.execution.k8s_catalog_payload import (
    build_helm_payload,
    build_manifest_payload,
)

SNAPSHOT_ROOT = Path(__file__).parent / "catalog_snapshot"


def catalog_sha() -> str:
    return (SNAPSHOT_ROOT / "CATALOG_SHA").read_text().strip()


def load_pack(module_rel: str) -> dict[str, Any]:
    """Load a module's vendored bnkforge.pack.json (the catalog pack_manifest)."""
    return json.loads((SNAPSHOT_ROOT / module_rel / "bnkforge.pack.json").read_text())


def _deploy_model(pack: dict[str, Any]) -> str:
    entrypoints = pack.get("deployment_pack", {}).get("entrypoints", {})
    if entrypoints.get("chart_ref") or entrypoints.get("chart_path"):
        return "helm"
    return "manifests"


def _ctx(module_rel: str, variables: dict[str, Any]) -> ModuleContext:
    pack = load_pack(module_rel)
    return ModuleContext(
        module_id=0,
        project_id=0,
        path=pack["module"]["path"],
        category=pack["module"].get("category", ""),
        variables=dict(variables),
        workspace_path=str(SNAPSHOT_ROOT / module_rel),
        deploy_model=_deploy_model(pack),
        module_source_kind="git_catalog",
        pack_manifest=pack,
    )


def render_catalog_manifests(module_rel: str, variables: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the catalog path's rendered manifest documents for the DPU case."""
    return build_manifest_payload(_ctx(module_rel, variables), variables)["manifests"]


def render_catalog_helm(module_rel: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Return the catalog path's full helm payload (chart_ref, version, values, ...)."""
    return build_helm_payload(_ctx(module_rel, variables), variables)


# ── normalization helpers for order-insensitive structural comparison ────────

def normalize_manifests(manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort manifests by (kind, namespace, name) so apply-order doesn't affect parity."""

    def key(m: dict[str, Any]) -> tuple[str, str, str]:
        meta = m.get("metadata", {}) if isinstance(m.get("metadata"), dict) else {}
        return (
            str(m.get("kind", "")),
            str(meta.get("namespace", "")),
            str(meta.get("name", "")),
        )

    return sorted((m for m in manifests if m), key=key)
