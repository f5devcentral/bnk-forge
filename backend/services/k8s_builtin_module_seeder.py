"""
Seeds ModuleLibrary entries for built-in k8s/kubernetes modules.

These modules are implemented in the backend's kubernetes engine (not as Python
SSH classes), so they do not appear in the Python module registry.  Without this
seeder the deploy gate checks (which require a ModuleLibrary row per path) would
reject any template that references them.

Idempotent: upserts by path on every startup.  Fields that are authoritative here
(name, description, engine_type, etc.) are refreshed on every boot so that changes
to this file take effect without a migration.
"""

import logging

from sqlalchemy.orm import Session

from models import ModuleLibrary

logger = logging.getLogger(__name__)

# Each dict must contain at minimum: path, name, description.
# engine_type should always be "kubernetes" for these modules.
# execution_engine / deploy_model / module_source_kind are required so that
# module_capabilities.get_execution_engine() resolves "kubernetes-direct" (the
# value the engine_router dispatch checks against) without relying on a catalog
# sync having run first.  The catalog sync's _backfill_official_source_association()
# would later set module_source_kind="git_catalog" — these seeder values are the
# safe pre-sync defaults that keep deployment working on a fresh install.
K8S_BUILTIN_MODULES: list[dict] = [
    {
        "path": "k8s/bnk-prerequisites",
        "name": "BNK Prerequisites",
        "description": (
            "Installs BNK prerequisites: F5 container registry secret (FAR), "
            "BNK namespace, RBAC, and required CRDs."
        ),
        "category": "k8s",
        "git_source": "builtin://k8s/bnk-prerequisites",
        "version": "1.0.0",
        "engine_type": "kubernetes",
        "module_source_kind": "builtin",
        "execution_engine": "kubernetes-direct",
        "deploy_model": "kubernetes",
        "is_active": True,
        "is_official": True,
        "is_tested": True,
        # No BNK-layer dependencies; depends on infra modules which are
        # linked at template level (bare-metal/taint-dpu-node etc.).
        "dependencies_metadata": {"required": [], "optional": []},
    },
    {
        "path": "k8s/network-setup",
        "name": "Kubernetes Network Setup (Multus NADs)",
        "description": (
            "Creates Multus NetworkAttachmentDefinitions (NADs) for internal "
            "and external BNK data-plane interfaces."
        ),
        "category": "k8s",
        "git_source": "builtin://k8s/network-setup",
        "version": "1.0.0",
        "engine_type": "kubernetes",
        "module_source_kind": "builtin",
        "execution_engine": "kubernetes-direct",
        "deploy_model": "kubernetes",
        "is_active": True,
        "is_official": True,
        "is_tested": True,
        # NADs must be created in the namespace that bnk-prerequisites sets up.
        "dependencies_metadata": {
            "required": [{"module": "k8s/bnk-prerequisites"}],
            "optional": [],
        },
    },
    {
        "path": "k8s/cert-manager",
        "name": "F5 Cert-Manager",
        "description": (
            "Installs F5's cert-manager chart for BNK TLS prerequisites. "
            "This module installs cert-manager only; issuer/certificate resources "
            "are handled by other BNK flows."
        ),
        "category": "k8s",
        "git_source": "builtin://k8s/cert-manager",
        "version": "1.0.0",
        "engine_type": "kubernetes",
        "module_source_kind": "builtin",
        "execution_engine": "kubernetes-direct",
        "deploy_model": "kubernetes",
        "is_active": True,
        "is_official": True,
        "is_tested": True,
        # cert-manager install requires BNK namespaces/CRDs and network setup to be complete first.
        "dependencies_metadata": {
            "required": [{"module": "k8s/network-setup"}],
            "optional": [],
        },
    },
    {
        "path": "k8s/bnk-cert-issuer",
        "name": "BNK Cert Issuer",
        "description": (
            "Creates the Forge-managed cert-manager issuer chain: self-signed "
            "ClusterIssuer, BNK CA certificate/secret, and bnk-ca-cluster-issuer."
        ),
        "category": "k8s",
        "git_source": "builtin://k8s/bnk-cert-issuer",
        "version": "1.0.0",
        "engine_type": "kubernetes",
        "module_source_kind": "builtin",
        "execution_engine": "kubernetes-direct",
        "deploy_model": "kubernetes",
        "is_active": True,
        "is_official": True,
        "is_tested": True,
        # Issuer chain requires cert-manager to be installed and running.
        "dependencies_metadata": {
            "required": [{"module": "k8s/cert-manager"}],
            "optional": [],
        },
    },
]

# Fields that are authoritative in this seeder and get refreshed on every boot.
_UPSERT_FIELDS = [
    "name",
    "description",
    "category",
    "git_source",
    "version",
    "engine_type",
    "module_source_kind",
    "execution_engine",
    "deploy_model",
    "is_active",
    "is_official",
    "is_tested",
    "dependencies_metadata",
]

# Fields that catalog sync enriches via bnkforge.pack.json. When a row already
# has a pack_manifest (populated by catalog sync), these fields carry
# authoritative values from the pack (e.g. deploy_model="manifests",
# module_source_kind="git_catalog", execution_engine="opentofu") that the
# seeder's pre-sync defaults ("builtin" / "kubernetes" / "kubernetes-direct")
# must NOT clobber.  Without this guard, every container restart reverts the
# enriched values — breaking output collection for manifest-based modules
# (e.g. k8s/bnk-cert-issuer) and engine routing for opentofu-based modules
# (e.g. k8s/bnk-prerequisites whose pack declares engine=opentofu).
_CATALOG_ENRICHED_FIELDS = {"module_source_kind", "deploy_model", "git_source", "execution_engine"}


def seed_k8s_builtin_modules(db: Session) -> tuple[int, int]:
    """Upsert ModuleLibrary rows for built-in k8s modules.

    Returns (created_count, updated_count).
    """
    created = 0
    updated = 0

    for attrs in K8S_BUILTIN_MODULES:
        existing = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == attrs["path"]
        ).first()

        if existing is not None and existing.content_sha256 is not None:
            # D-033: a content-hashed catalog version row owns this path now —
            # structural fields are immutable; skip rather than abort the seed.
            continue

        if existing is None:
            db.add(ModuleLibrary(**attrs))
            created += 1
            logger.debug("Created k8s builtin module: %s", attrs["path"])
        else:
            changed = False
            catalog_synced = isinstance(existing.pack_manifest, dict) and bool(existing.pack_manifest)
            for field in _UPSERT_FIELDS:
                if field not in attrs:
                    continue
                # Don't clobber fields that catalog sync has enriched
                if catalog_synced and field in _CATALOG_ENRICHED_FIELDS:
                    continue
                if getattr(existing, field, None) != attrs[field]:
                    setattr(existing, field, attrs[field])
                    changed = True
            if changed:
                updated += 1
                logger.debug("Updated k8s builtin module: %s", attrs["path"])

    if created or updated:
        db.commit()

    return created, updated
