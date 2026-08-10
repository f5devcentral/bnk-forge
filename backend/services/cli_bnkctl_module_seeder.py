"""
Seeds ModuleLibrary entries for cli-bnkctl (awsbnkctl) single-module blueprints.

These modules are executed by the CliEngine (local subprocess), not as Python SSH classes
or kubernetes objects. Without this seeder, the deploy gate checks would reject any
template that references them.

Idempotent: upserts by path on every startup. Fields that are authoritative here
(name, description, execution_engine, etc.) are refreshed on every boot so that changes
to this file take effect without a migration.
"""

import logging
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from models import ModuleLibrary

logger = logging.getLogger(__name__)

# Matches BnkctlToolDescriptor.binary_path for "awsbnkctl" in cli_engine.py — the worker
# image ships no CLI tools by default; the binary is only present when the deploy's
# worker-volumes mount it in (dev docker-compose today; dist/registry installs do not).
_AWSBNKCTL_BINARY_PATH = "/usr/local/bin/awsbnkctl"


def _bnkctl_binary_available() -> bool:
    """True if the awsbnkctl binary is present on this worker/API container.

    Checked at both the fixed path cli_engine.py resolves and via PATH, matching
    BnkctlEngine's own `shutil.which(descriptor.binary_path) or descriptor.binary_path`
    resolution.
    """
    return Path(_AWSBNKCTL_BINARY_PATH).is_file() or shutil.which("awsbnkctl") is not None

# CLI_BNKCTL_MODULES registry: paths, names, descriptions, and variable schemas.
# Each module is a standalone (single-module) deploy with stack_instance_id=NULL.
# Execution engine is "cli-bnkctl" (tool-agnostic BnkctlEngine).
# Variables schema defines the 5 editable inputs per the bnk-demo topology.
CLI_BNKCTL_MODULES: list[dict] = [
    {
        "path": "cli-bnkctl/awsbnkctl/bnk-demo",
        "name": "AWS BNK Demo (CLI Deploy)",
        "description": (
            "Deploy awsbnkctl bnk-demo topology via the forge UI. "
            "Pick the interface pattern, instance type, node sizing, region and CIDR, "
            "then Plan to validate the rendered cluster.yaml with awsbnkctl up --dry-run "
            "(zero AWS spend), or Deploy to provision the real AWS infrastructure with "
            "awsbnkctl up --auto."
        ),
        "category": "cli-bnkctl",
        "git_source": "builtin://cli-bnkctl/awsbnkctl/bnk-demo",
        "version": "1.2.0",
        "execution_engine": "cli-bnkctl",
        "deploy_model": "cli-exec",
        "module_source_kind": "builtin",
        "is_active": True,
        "is_official": True,
        "is_tested": False,  # Will be True after tracer validation.
        # Topology parameters, all provided with sensible defaults. The blueprint
        # manifest exposes these as selectable inputs (dropdowns where the CLI has
        # a fixed choice set); anything the user leaves untouched falls back to the
        # default below. Every entry is required=False so the deploy is never blocked.
        "variables_schema": [
            {
                "name": "cluster_name",
                "type": "string",
                "description": "Cluster name (metadata.name)",
                "default": "bnk-demo",
                "required": False,
            },
            {
                "name": "region",
                "type": "string",
                "description": "AWS region (metadata.region)",
                "default": "ap-southeast-2",
                "required": False,
            },
            {
                "name": "vpc_cidr",
                "type": "string",
                "description": "VPC CIDR block (network.vpcCidr)",
                "default": "10.0.0.0/16",
                "required": False,
            },
            {
                "name": "instance_type",
                "type": "string",
                "description": "EC2 instance type for worker nodes (cluster.nodeGroups[0].instanceType)",
                "default": "m5.2xlarge",
                "required": False,
            },
            {
                "name": "pattern",
                "type": "string",
                "description": "BNK interface pattern (external-only, dual-interface or sriov-external)",
                "default": "external-only",
                "required": False,
            },
            {
                "name": "kubernetes_version",
                "type": "string",
                "description": "EKS control-plane Kubernetes version (cluster.kubernetesVersion)",
                "default": "1.30",
                "required": False,
            },
            {
                "name": "node_desired_size",
                "type": "number",
                "description": "Desired worker node count (cluster.nodeGroups[0].desiredSize)",
                "default": 3,
                "required": False,
            },
            {
                "name": "node_min_size",
                "type": "number",
                "description": "Minimum worker node count (cluster.nodeGroups[0].minSize)",
                "default": 3,
                "required": False,
            },
            {
                "name": "node_max_size",
                "type": "number",
                "description": "Maximum worker node count (cluster.nodeGroups[0].maxSize)",
                "default": 4,
                "required": False,
            },
            # Demo-layer cluster.yaml blocks (awsbnkctl up --auto, not post-up demo run)
            {
                "name": "demo_enabled",
                "type": "boolean",
                "description": "Mark this as a demo deployment (writes DEMO_MODE/EXPIRY to state.env). "
                               "Requires jumphost_enabled=true per awsbnkctl schema.",
                "default": False,
                "required": False,
            },
            {
                "name": "demo_ttl",
                "type": "string",
                "description": "Demo cluster lifetime as a Go duration string (e.g. 24h, 48h, 72h). "
                               "Used when demo_enabled=true.",
                "default": "24h",
                "required": False,
            },
            {
                "name": "jumphost_enabled",
                "type": "boolean",
                "description": "Provision an EC2 jumphost (SSH bastion) inside the BNK_EXT subnet "
                               "(testing.jumphost.enabled). Required for demo_enabled.",
                "default": False,
                "required": False,
            },
            {
                "name": "jumphost_instance_type",
                "type": "string",
                "description": "EC2 instance type for the jumphost (testing.jumphost.instanceType).",
                "default": "t3.small",
                "required": False,
            },
            {
                "name": "bigipve_enabled",
                "type": "boolean",
                "description": "Provision a chargeable F5 BIG-IP VE instance alongside the cluster "
                               "(bigipVE.enabled). WARNING: incurs AWS charges (~15 min extra). "
                               "Requires AWSBNKCTL_BIGIP_PASSWORD env var at provisioning time. "
                               "Enabling BIG-IP VE forces the dual-interface pattern "
                               "(BIG-IP VE needs the internal subnet).",
                "default": False,
                "required": False,
            },
            {
                "name": "bigipve_instance_type",
                "type": "string",
                "description": "EC2 instance type for the BIG-IP VE instance (bigipVE.instanceType).",
                "default": "c5n.2xlarge",
                "required": False,
            },
            {
                "name": "bigipve_license_tier",
                "type": "string",
                "description": "PAYG BIG-IP license tier: Good, Better, or Best (bigipVE.licenseTier).",
                "default": "Good",
                "required": False,
            },
            {
                "name": "bigipve_vip",
                "type": "string",
                "description": "BIG-IP virtual-server IP inside network.dataPath.external.cidr "
                               "(bigipVE.vip). Leave empty to auto-derive as <prefix>.10.120.",
                "default": "",
                "required": False,
            },
            {
                "name": "ai_sagemaker_enabled",
                "type": "boolean",
                "description": "Create a disposable SageMaker LMI endpoint on up, deleted on down "
                               "(ai.sagemaker.enabled). Incurs SageMaker instance charges while running.",
                "default": False,
                "required": False,
            },
            {
                "name": "ai_sagemaker_model",
                "type": "string",
                "description": "Hugging Face model ID for the SageMaker LLM endpoint "
                               "(required when AI demo enabled).",
                "default": "meta-llama/Meta-Llama-3-8B-Instruct",
                "required": False,
            },
        ],
        # No dependencies: bnk-demo is a standalone, single-topology deploy.
        "dependencies_metadata": {"required": [], "optional": []},
        # inputs_metadata documents the two BNK license file-secrets.
        # These are NOT topology variables — they are project secrets uploaded via
        # the forge secrets UI with:
        #   secret_type=file, target_module_path="cli-bnkctl/awsbnkctl/bnk-demo"
        #   target_variable_name="bnk_far_archive" | "bnk_jwt"
        # SecretsService.prepare_secrets_for_execution materializes them into
        # <workspace>/secrets/ at task time; the renderer then references them as
        # ./secrets/<filename> in the bnk: block.
        # Use validation.type="file_path" so the secrets service reports them as
        # required project secrets (get_required_secrets_for_module logic).
        "inputs_metadata": {
            "required": [],
            "optional": [
                {
                    "name": "bnk_far_archive",
                    "description": (
                        "F5 FAR pull-credentials JSON (cne_pull_64.json). "
                        "Upload as a project file-secret with "
                        "target_variable_name=bnk_far_archive. "
                        "Required for real awsbnkctl up --auto; omit for dry-run."
                    ),
                    "sensitive": True,
                    "validation": {"type": "file_path"},
                    # Resolved from the secrets service, not user-supplied form field
                    "source": "project",
                },
                {
                    "name": "bnk_jwt",
                    "description": (
                        "F5 subscription JWT (license.jwt). "
                        "Upload as a project file-secret with "
                        "target_variable_name=bnk_jwt. "
                        "Required for real awsbnkctl up --auto; omit for dry-run."
                    ),
                    "sensitive": True,
                    "validation": {"type": "file_path"},
                    # Resolved from the secrets service, not user-supplied form field
                    "source": "project",
                },
            ],
        },
    },
    # ── Use-case runner module ────────────────────────────────────────────────
    # Runs awsbnkctl demo/scenarios commands against a demo cluster that was
    # already deployed by cli-bnkctl/awsbnkctl/bnk-demo.  The engine reuses
    # the existing workspace cluster.yaml; it never re-renders it.
    {
        "path": "cli-bnkctl/awsbnkctl/bnk-demo-usecases",
        "name": "AWS BNK Demo Use-Cases (CLI Run)",
        "description": (
            "Run awsbnkctl demo/scenarios use-cases against a demo BNK cluster. "
            "Requires the 'AWS BNK Demo (CLI Deploy)' cluster module to have been "
            "applied first (with demo_enabled=true). Choose a use-case set via the "
            "usecases dropdown: all-green (10 demos+scenarios), all-demos (4), all (13), "
            "or any individual use-case name."
        ),
        "category": "cli-bnkctl",
        "git_source": "builtin://cli-bnkctl/awsbnkctl/bnk-demo-usecases",
        "version": "1.0.0",
        "execution_engine": "cli-bnkctl",
        "deploy_model": "cli-exec",
        "module_source_kind": "builtin",
        "is_active": True,
        "is_official": True,
        "is_tested": False,
        "variables_schema": [
            {
                "name": "usecases",
                "type": "string",
                "description": (
                    "Use-case set to run after the cluster is up. "
                    "Options: "
                    "'none' (default, deploy the cluster only — skips use-cases); "
                    "'all-green' (demos + green scenarios, 10 total); "
                    "'all-demos' (4 demos only); "
                    "'all' (all 13 including amber scenarios); "
                    "or any individual name: "
                    "http2, diameter, ingress-migration, bigip-cis, "
                    "http-routing-e2e, http-traffic-split, proxy-protocol-l4, multi-vip, "
                    "external-resource-pool, ai-inference-e2e, "
                    "egress-snat, ai-token-counting, ai-semantic-cache. "
                    "Requires demo_enabled=true on the cluster to actually run."
                ),
                "default": "none",
                "required": False,
            },
        ],
        # Requires the cluster module to be applied first.
        "dependencies_metadata": {
            "required": [{"module": "cli-bnkctl/awsbnkctl/bnk-demo"}],
            "optional": [],
        },
        # No additional file-secrets needed: the use-cases module shares the
        # workspace with the cluster module and reads state/kubeconfig from there.
        "inputs_metadata": {"required": [], "optional": []},
    },
]

_UPSERT_FIELDS = [
    "name",
    "category",
    "git_source",
    "version",
    "execution_engine",
    "deploy_model",
    "module_source_kind",
    "description",
    "variables_schema",
    "is_active",
    "is_official",
    "is_tested",
    "dependencies_metadata",
    "inputs_metadata",
]

_CATALOG_ENRICHED_FIELDS = {
    "pack_manifest",
    "inputs_metadata",
    "outputs_metadata",
    "dependencies_metadata",
    "tags",
}


def seed_cli_bnkctl_modules(db: Session) -> tuple[int, int]:
    """
    Upsert CLI-bnkctl module entries into ModuleLibrary. Idempotent.

    Gated on the awsbnkctl binary being present on this container: dist/registry
    installs don't mount it, and seeding the module unconditionally there advertises
    a blueprint that fails every deploy at init with "Binary not found". If a row
    from a previous boot exists (e.g. the binary was mounted and later removed),
    it is deactivated rather than left advertising a broken module.

    Returns: (created_count, updated_count)
    """
    created = 0
    updated = 0

    if not _bnkctl_binary_available():
        deactivated = (
            db.query(ModuleLibrary)
            .filter(
                ModuleLibrary.path.in_([m["path"] for m in CLI_BNKCTL_MODULES]),
                ModuleLibrary.is_active.is_(True),
            )
            .update({"is_active": False}, synchronize_session=False)
        )
        if deactivated:
            db.commit()
            logger.info(
                "cli-bnkctl: awsbnkctl binary not found at %s — deactivated %d existing module(s)",
                _AWSBNKCTL_BINARY_PATH, deactivated,
            )
        else:
            logger.debug(
                "cli-bnkctl: awsbnkctl binary not found at %s — skipping seeder",
                _AWSBNKCTL_BINARY_PATH,
            )
        return created, updated

    for attrs in CLI_BNKCTL_MODULES:
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
            logger.debug("Created cli-bnkctl module: %s", attrs["path"])
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
                logger.debug("Updated cli-bnkctl module: %s", attrs["path"])

    if created or updated:
        db.commit()

    return created, updated
