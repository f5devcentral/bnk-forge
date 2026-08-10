"""
Config Writer — extracted from execution_engine.py.

Generates OpenTofu configuration files for module workspaces:
  - terraform.tfvars.json  — variable values
  - backend_override.tf    — state backend (local or S3)
  - encryption.tf          — state encryption (pbkdf2, AWS KMS, GCP KMS, Azure, OpenBao)
  - bnk_forge_providers.tf — injected K8s/Helm/AWS provider blocks

These are pure file-generation functions. They take data, write files,
and return nothing meaningful — no DB access, no subprocess calls.
"""

import json
import logging
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models import ProjectModule

from core.encryption import decrypt_value, encrypt_value
from services.defaults_service import get_default
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig

logger = logging.getLogger(__name__)

# Keys that credentials_service.get_cloud_credentials_env mirrors to TF_VAR_* env vars.
# Writing these into terraform.tfvars.json would override the env-injected values
# (tfvars take precedence over TF_VAR_* in OpenTofu variable precedence), so we
# exclude them from the file entirely and let the env vars win.
ENV_INJECTED_TFVARS: frozenset[str] = frozenset({
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
})


def write_tfvars(work_dir: str, variables: dict) -> str:
    """
    Generate terraform.tfvars.json file from variables dict.

    Uses JSON format for proper type handling.

    Args:
        work_dir: Workspace directory
        variables: Variable dict

    Returns:
        Path to tfvars file
    """
    # Filter out:
    # 1. Credential keys injected via TF_VAR_* env (tfvars would override env → creds clobbered)
    # 2. Any key with a None value (null in tfvars.json overrides env/defaults with null)
    filtered = {
        k: v
        for k, v in variables.items()
        if k not in ENV_INJECTED_TFVARS and v is not None
    }

    tfvars_path = os.path.join(work_dir, "terraform.tfvars.json")
    with open(tfvars_path, 'w') as f:
        json.dump(filtered, f, indent=2)

    logger.info(f"Wrote {len(filtered)} variables to {tfvars_path}")
    return tfvars_path


def write_backend_config(work_dir: str, module: "ProjectModule", db=None):
    """
    Generate backend configuration for state storage.

    Args:
        work_dir: Workspace directory
        module: ProjectModule (for project config)
        db: Optional DB session (needed to resolve defaults for S3 region)
    """
    project = module.project
    # IMP-015: Read from extracted backend_config if available, fall back to direct columns
    bk = project.backend_config if project.backend_config else project

    if project.backend_type == "s3":
        # S3 backend with OpenTofu 1.10+ native locking
        bucket = bk.s3_state_bucket or f"{project.name}-terraform-state"
        region = bk.s3_state_region or project.region
        if not region and db:
            region = get_default(db, "cloud.aws.default_region")
        region = region or "us-east-1"
        key_prefix = bk.s3_state_key_prefix or ""
        state_storage_provider = getattr(bk, 'state_storage_provider', None) or (
            'ibm' if project.cloud_provider == 'ibm' else 'aws'
        )
        s3_endpoint = getattr(bk, 's3_endpoint', None)

        # Build state key path
        state_key = f"{key_prefix}{project.name}/{module.path_in_project}/terraform.tfstate"

        if state_storage_provider == 'ibm':
            endpoint = s3_endpoint or f"https://s3.{region}.cloud-object-storage.appdomain.cloud"
            backend_content = f'''
terraform {{
  backend "s3" {{
    bucket                      = "{bucket}"
    key                         = "{state_key}"
    region                      = "{region}"
    encrypt                     = true
    use_lockfile                = true
    endpoints                   = {{ s3 = "{endpoint}" }}
    skip_region_validation      = true
    skip_credentials_validation = true
    skip_requesting_account_id  = true
  }}
}}
'''
        # Use native S3 locking (OpenTofu 1.10+) or legacy DynamoDB
        elif bk.s3_use_native_locking:
            # Native S3 locking - no DynamoDB required!
            backend_content = f'''
terraform {{
  backend "s3" {{
    bucket       = "{bucket}"
    key          = "{state_key}"
    region       = "{region}"
    encrypt      = true
    use_lockfile = true
  }}
}}
'''
        else:
            # Legacy: DynamoDB locking (for backward compatibility)
            lock_table = bk.s3_dynamodb_table or "terraform-locks"
            backend_content = f'''
terraform {{
  backend "s3" {{
    bucket         = "{bucket}"
    key            = "{state_key}"
    region         = "{region}"
    encrypt        = true
    dynamodb_table = "{lock_table}"
  }}
}}
'''
    else:
        # Local backend - state in persistent location
        state_dir = f"/app/state/{project.id}/{module.id}"
        os.makedirs(state_dir, exist_ok=True)

        backend_content = f'''
terraform {{
  backend "local" {{
    path = "{state_dir}/terraform.tfstate"
  }}
}}
'''

    backend_path = os.path.join(work_dir, "backend_override.tf")
    with open(backend_path, 'w') as f:
        f.write(backend_content)

    logger.info(f"Wrote backend config to {backend_path}")


def write_encryption_config(work_dir: str, module: "ProjectModule", db=None):
    """
    Generate OpenTofu 1.8+ state encryption configuration.

    Args:
        work_dir: Workspace directory
        module: ProjectModule (for project config)
        db: Optional DB session (needed to resolve defaults)
    """
    project = module.project
    # IMP-014: Read from extracted encryption_config if available, fall back to direct columns
    enc = project.encryption_config if project.encryption_config else project

    # Skip if encryption is disabled
    if not enc.state_encryption_enabled:
        logger.info("State encryption disabled for project")
        return

    provider = enc.encryption_provider or "pbkdf2"
    logger.info(f"Generating state encryption config with provider: {provider}")

    # Generate key provider block based on provider type
    if provider == "pbkdf2":
        # Passphrase-based encryption (default, no cloud dependencies)
        # OpenTofu requires minimum 16 character passphrase
        if enc.encryption_passphrase_encrypted:
            passphrase = decrypt_value(enc.encryption_passphrase_encrypted)
        else:
            # CRITICAL: Derive a passphrase and persist it immediately so it survives
            # project renames.  Previous code derived from project.name which is mutable —
            # renaming a project silently changed the passphrase and made existing
            # encrypted state unreadable (see: AWS-BNK-EKS-Sydney VPC destroy failure).
            #
            # Backward-compat: use the same formula that was in production so any
            # already-encrypted state (derived from current project.name) stays readable.
            # Once persisted, the passphrase is frozen and immune to future renames.
            import hashlib
            passphrase = hashlib.sha256(f"bnk-forge-{project.id}-{project.name}".encode()).hexdigest()[:32]

            # Persist so we never re-derive (requires a DB session)
            if db is not None:
                try:
                    enc.encryption_passphrase_encrypted = encrypt_value(passphrase)
                    db.commit()
                    logger.info(
                        "Persisted auto-generated encryption passphrase for project %s "
                        "(immune to future renames)",
                        project.id,
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to persist encryption passphrase for project %s: %s "
                        "(passphrase will be re-derived next run — rename risk remains)",
                        project.id, e,
                    )

        key_provider_block = f'''    key_provider "pbkdf2" "main" {{
      passphrase = "{passphrase}"
      key_length = 32
      iterations = 200000
      salt_length = 32
      hash_function = "sha256"
    }}
'''

    elif provider == "aws_kms":
        # AWS Key Management Service
        kms_key_id = enc.encryption_kms_key_id
        if not kms_key_id:
            raise ValueError("AWS KMS encryption requires kms_key_id to be set")
        kms_region = enc.encryption_kms_region or project.region
        if not kms_region and db:
            kms_region = get_default(db, "cloud.aws.default_region")
        kms_region = kms_region or "us-east-1"

        key_provider_block = f'''    key_provider "aws_kms" "main" {{
      kms_key_id = "{kms_key_id}"
      region     = "{kms_region}"
      key_spec   = "AES_256"
    }}
'''

    elif provider == "gcp_kms":
        # Google Cloud KMS
        kms_key_id = enc.encryption_kms_key_id
        # S15-031: Validate required fields before generating HCL
        if not kms_key_id:
            raise ValueError("GCP KMS encryption requires 'encryption_kms_key_id' to be set on the project")

        key_provider_block = f'''    key_provider "gcp_kms" "main" {{
      kms_encryption_key = "{kms_key_id}"
      key_length         = 32
    }}
'''

    elif provider == "azure_key_vault":
        # Azure Key Vault
        vault_url = enc.encryption_vault_url
        key_name = enc.encryption_vault_key_name
        # S15-031: Validate required fields before generating HCL
        if not vault_url:
            raise ValueError("Azure Key Vault encryption requires 'encryption_vault_url' to be set on the project")
        if not key_name:
            raise ValueError("Azure Key Vault encryption requires 'encryption_vault_key_name' to be set on the project")

        key_provider_block = f'''    key_provider "azure_key_vault" "main" {{
      vault_url = "{vault_url}"
      key_name  = "{key_name}"
      key_type  = "RSA-HSM"
    }}
'''

    elif provider == "openbao":
        # OpenBao/Vault Transit Engine
        vault_address = enc.encryption_openbao_address
        # S15-031: Validate required fields before generating HCL
        if not vault_address:
            raise ValueError("OpenBao encryption requires 'encryption_openbao_address' to be set on the project")
        vault_token = decrypt_value(enc.encryption_openbao_token_encrypted) if enc.encryption_openbao_token_encrypted else ""
        transit_key = enc.encryption_openbao_transit_key or "tofu-state"

        key_provider_block = f'''    key_provider "openbao" "main" {{
      address = "{vault_address}"
      token   = "{vault_token}"
      key     = "{transit_key}"
    }}
'''

    else:
        # S14-037: Raise error instead of silently using insecure fallback
        raise ValueError(
            f"Unknown encryption provider: {provider}. "
            f"Supported providers: pbkdf2, aws_kms, gcp_kms, azure_key_vault, openbao"
        )

    # Generate full encryption configuration
    encryption_config = f'''
terraform {{
  encryption {{
{key_provider_block}
    method "unencrypted" "migrate" {{
    }}

    method "aes_gcm" "main" {{
      keys = key_provider.{provider}.main
    }}

    state {{
      method = method.aes_gcm.main
      fallback {{
        method = method.unencrypted.migrate
      }}
    }}

    plan {{
      method = method.aes_gcm.main
      fallback {{
        method = method.unencrypted.migrate
      }}
    }}
  }}
}}
'''

    encryption_path = os.path.join(work_dir, "encryption.tf")
    with open(encryption_path, 'w') as f:
        f.write(encryption_config)

    logger.info(f"Wrote encryption config to {encryption_path} using provider: {provider}")


def _resolve_project_kubeconfig_path(project) -> str | None:
    """Resolve the kubeconfig file path for a project's cluster.

    Writes the kubeconfig to disk if not already present, using the
    cluster's encrypted kubeconfig from the DB.

    This is critical for bare-metal/on-prem deployments where the cluster
    kubeconfig is stored encrypted in the KubernetesCluster record. The
    provider config writer runs BEFORE engine_router writes the kubeconfig,
    so we must write it here from the DB when needed.
    """
    if not project:
        return None

    kubeconfig_path = f"/tmp/bnk-forge-kubeconfigs/project-{project.id}.yaml"

    # Always write from DB — a stale file from a previous kubeadm init will
    # have the old CA cert and fail TLS verification against the new cluster.
    try:
        cluster = None
        # Try k8s_clusters relationship (Project.k8s_clusters → list of KubernetesCluster)
        if hasattr(project, 'k8s_clusters') and project.k8s_clusters:
            cluster = project.k8s_clusters[0]  # Take the first/primary cluster

        if not cluster:
            # Fall back to DB query (for detached session or missing relationship)
            from database import SessionLocal
            from models import KubernetesCluster
            db = SessionLocal()
            try:
                cluster = db.query(KubernetesCluster).filter(
                    KubernetesCluster.project_id == project.id
                ).first()
            finally:
                db.close()

        if cluster and cluster.kubeconfig_encrypted:
            kubeconfig_content = decrypt_value(cluster.kubeconfig_encrypted)
            if kubeconfig_content:
                # Defense-in-depth: assert portability before writing to disk.
                # KubeconfigUnportableError propagates to the job with a re-upload message.
                kubeconfig_content = normalize_kubeconfig(
                    kubeconfig_content, source=NormalizationSource.INTERNAL_REREAD
                )

                # If the cluster has SSH tunnelling enabled (e.g. on-prem
                # clusters whose API IP isn't routable from the worker
                # container, like the `dual_dpu_obmc` host's VLAN IP),
                # open / reuse the tunnel and rewrite the kubeconfig's
                # `server` URL to point at the local tunnel port. Without
                # this the Terraform Kubernetes provider tries to connect
                # directly to the API IP and gets "connection refused".
                # Same rewrite shape `cluster_utils._write_kubeconfig` uses
                # for the in-process Python clients.
                try:
                    from services.cluster_utils import _maybe_open_ssh_tunnel

                    tunnel_port = _maybe_open_ssh_tunnel(cluster)
                except Exception as exc:
                    logger.warning(
                        "SSH tunnel setup for cluster %s failed (%s); "
                        "kubeconfig will be written without rewrite — "
                        "Terraform may fail if API isn't directly routable",
                        cluster.name, exc,
                    )
                    tunnel_port = None

                if tunnel_port:
                    try:
                        import yaml as yaml_lib
                        kc_dict = yaml_lib.safe_load(kubeconfig_content)
                        for c in kc_dict.get("clusters", []):
                            # 127.0.0.1 not "localhost" — see cluster_utils
                            # comment: localhost resolves to ::1+127.0.0.1
                            # and the tunnel listener is IPv4-only.
                            c["cluster"]["server"] = f"https://127.0.0.1:{tunnel_port}"
                            c["cluster"]["insecure-skip-tls-verify"] = True
                            c["cluster"].pop("certificate-authority-data", None)
                            c["cluster"].pop("certificate-authority", None)
                        kubeconfig_content = yaml_lib.dump(kc_dict, default_flow_style=False)
                        logger.info(
                            "Rewrote kubeconfig server URL to "
                            "https://localhost:%d (SSH tunnel to cluster %s)",
                            tunnel_port, cluster.name,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to rewrite kubeconfig for SSH tunnel "
                            "(cluster %s): %s — using original content",
                            cluster.name, exc,
                        )

                os.makedirs(os.path.dirname(kubeconfig_path), exist_ok=True)
                with open(kubeconfig_path, "w") as f:
                    f.write(kubeconfig_content)
                logger.info("Wrote kubeconfig for project %d to %s", project.id, kubeconfig_path)
                return kubeconfig_path
    except Exception as exc:
        logger.warning("Failed to resolve kubeconfig for project %d: %s", project.id, exc)

    logger.debug(
        "Kubeconfig not found/written for project %s — provider config will need "
        "kubeconfig_path in variables or /app/kubeconfig must exist",
        project.id,
    )
    return None


def _inject_forge_kubeconfig_locals(work_dir: str, variables: dict | None, project) -> None:
    """Write only the ``locals { forge_kubeconfig }`` block into the workspace.

    Catalog modules ship their own provider blocks (``bnk_forge_providers.tf``)
    but do **not** define ``local.forge_kubeconfig``.  Their HCL references
    ``try(local.forge_kubeconfig, …)`` which would fall through to a non-existent
    file path without this injection.

    This is the minimal counterpart of ``write_provider_config`` — it skips
    provider block generation (the module already has them) and only writes
    the kubeconfig locals (plus any AWS EKS data sources the locals reference).

    Only injects if the module actually references ``local.forge_kubeconfig``
    or ``forge_kubeconfig_content`` in its own ``.tf`` files.  Modules that
    create a cluster (e.g. ``eks-cluster-create``) declare neither reference
    and must NOT receive this injection — it would introduce dangling
    ``data.aws_eks_cluster`` references.
    """
    from utils.provider_config import generate_forge_kubeconfig_local

    variables = variables or {}
    cloud_provider = (project.cloud_provider or "") if project else ""

    # Guard: don't write if forge_kubeconfig is already defined in any .tf file
    # (e.g. from a prior run that wrote bnk_forge_providers.tf with the local embedded).
    # Usage gate: also skip if no .tf file (other than bnk_forge_locals.tf) actually
    # *references* local.forge_kubeconfig or forge_kubeconfig_content.  Modules that
    # CREATE a cluster (e.g. eks-cluster-create) reference neither — injecting would
    # introduce dangling data.aws_eks_cluster references → tofu validate failure.
    references_forge_kubeconfig = False
    for tf_file in os.listdir(work_dir):
        if not tf_file.endswith(".tf") or tf_file == "bnk_forge_locals.tf":
            continue
        tf_path = os.path.join(work_dir, tf_file)
        try:
            with open(tf_path) as f:
                tf_content = f.read()
        except OSError:
            continue
        # Definition guard: if forge_kubeconfig is already defined here, bail out.
        # A definition looks like `forge_kubeconfig = ...` inside a locals block.
        if re.search(r'forge_kubeconfig\s*=', tf_content):
            logger.info("forge_kubeconfig already defined in %s, skipping locals injection", tf_file)
            return
        # Reference detection: module consumes it via local.forge_kubeconfig or
        # var.forge_kubeconfig_content / try(local.forge_kubeconfig, ...).
        if "local.forge_kubeconfig" in tf_content or "forge_kubeconfig_content" in tf_content:
            references_forge_kubeconfig = True

    if not references_forge_kubeconfig:
        logger.debug(
            "No .tf file in %s references forge_kubeconfig — skipping locals injection "
            "(module likely creates the cluster, not consumes it)",
            work_dir,
        )
        return

    # Resolve kubeconfig path from cluster record if not in variables
    if "kubeconfig_path" not in variables and project:
        resolved = _resolve_project_kubeconfig_path(project)
        if resolved:
            variables = {**variables, "kubeconfig_path": resolved}

    kubeconfig_local = generate_forge_kubeconfig_local(
        cloud_provider=cloud_provider,
        variables=variables,
        project=project,
    )

    if not kubeconfig_local:
        return

    locals_path = os.path.join(work_dir, "bnk_forge_locals.tf")
    content = "# Auto-generated by BNK-Forge\n# Kubeconfig locals for catalog modules\n"

    # For AWS, the kubeconfig locals reference data.aws_eks_cluster / data.aws_eks_cluster_auth.
    # If those data sources are not yet declared in the workspace, emit them here so the
    # module workspace is self-contained (no "Reference to undeclared resource" errors).
    if cloud_provider == "aws":
        declared_data_sources: set[str] = set()
        for tf_file in os.listdir(work_dir):
            if not tf_file.endswith(".tf") or tf_file == "bnk_forge_locals.tf":
                continue
            try:
                with open(os.path.join(work_dir, tf_file)) as f:
                    tf_content = f.read()
                    if 'data "aws_eks_cluster" "cluster"' in tf_content:
                        declared_data_sources.add("aws_eks_cluster")
                    if 'data "aws_eks_cluster_auth" "cluster"' in tf_content:
                        declared_data_sources.add("aws_eks_cluster_auth")
            except OSError:
                continue

        missing_sources: list[str] = []
        # kubectl-only modules (e.g. multus) declare no inputs, so their per-module
        # `variables` dict carries no cluster name. Fall back to the project-level
        # variables (where the blueprint stores eks_cluster_name) BEFORE the bare
        # project name — the project may be named differently from its cluster.
        project_vars = (getattr(project, "project_variables", None) or {}) if project else {}
        # Project-level values are stored nested under "variable_defaults".
        defaults = project_vars.get("variable_defaults") or {}
        cluster_name = (
            variables.get("cluster_name")
            or variables.get("eks_cluster_name")
            or defaults.get("cluster_name")
            or defaults.get("eks_cluster_name")
            or (project.name if project else "cluster")
        )
        cluster_name_literal = json.dumps(cluster_name)
        if "aws_eks_cluster" not in declared_data_sources:
            missing_sources.append(f'''
data "aws_eks_cluster" "cluster" {{
  name = {cluster_name_literal}
}}
''')
        if "aws_eks_cluster_auth" not in declared_data_sources:
            missing_sources.append(f'''
data "aws_eks_cluster_auth" "cluster" {{
  name = {cluster_name_literal}
}}
''')

        if missing_sources:
            content += "# AWS EKS data sources required by forge_kubeconfig locals\n"
            content += "".join(missing_sources)

    content += kubeconfig_local

    with open(locals_path, "w") as f:
        f.write(content)

    logger.info("Wrote forge_kubeconfig locals to %s (provider injection skipped)", locals_path)


def write_provider_config(work_dir: str, module: "ProjectModule", variables: dict = None, db=None):
    """
    Generate provider configuration for modules that need K8s/Helm access.

    Checks module's required providers and generates appropriate config.
    This enables community modules to work without modification.

    Args:
        work_dir: Workspace directory
        module: ProjectModule (for project config and module metadata)
        variables: Assembled variables dict (from build_variables) - contains cluster_name, region, etc.
        db: Optional DB session (needed to resolve defaults)
    """
    lib_module = module.library_module
    project = module.project
    variables = variables or {}

    if not lib_module:
        return

    # Check what providers the module needs from module.json or versions.tf
    providers_metadata = lib_module.inputs_metadata.get("providers", {}) if lib_module.inputs_metadata else {}
    required_providers = list(providers_metadata.get("required", []))  # Make a copy

    # If no metadata, try to detect from versions.tf in workspace
    versions_tf = os.path.join(work_dir, "versions.tf")
    if not required_providers and os.path.exists(versions_tf):
        with open(versions_tf) as f:
            content = f.read()
            if 'kubernetes' in content.lower():
                required_providers.append('kubernetes')
            if 'helm' in content.lower():
                required_providers.append('helm')
            if 'hashicorp/aws' in content.lower():
                required_providers.append('aws')
            if 'ibm-cloud/ibm' in content.lower():
                required_providers.append('ibm')

    if not required_providers:
        # Path 1: kubectl-only modules (local+null providers, no kubernetes/helm/aws).
        # Inject forge_kubeconfig locals here, before returning, so modules that
        # reference local.forge_kubeconfig (e.g. multus, tmm-nads, cneinstall) get
        # bnk_forge_locals.tf with the data sources (none are declared yet at this point).
        _inject_forge_kubeconfig_locals(work_dir, variables, project)
        logger.debug(f"No provider injection needed for module {module.id}")
        return

    # Check if module already has provider config (don't override)
    for tf_file in os.listdir(work_dir):
        if tf_file.endswith('.tf'):
            tf_path = os.path.join(work_dir, tf_file)
            with open(tf_path) as f:
                content = f.read()
                # Look for provider blocks (not required_providers)
                if re.search(r'^provider\s+"(kubernetes|helm|aws|ibm)"', content, re.MULTILINE):
                    logger.info("Module already has provider config, skipping provider injection")
                    # Path 2: module supplies its own provider blocks (and likely its own data
                    # sources).  Inject forge_kubeconfig locals AFTER the module's own .tf files
                    # are present so _inject's missing-sources check sees what's already declared
                    # and skips duplicating data sources the module defines itself.
                    _inject_forge_kubeconfig_locals(work_dir, variables, project)
                    return

    # Import provider config utilities
    from utils.provider_config import (
        detect_helm_version_syntax,
        generate_aws_eks_providers,
        generate_azure_aks_providers,
        generate_gcp_gke_providers,
        generate_generic_providers,
        generate_ibm_roks_providers,
    )

    # Build provider configuration based on cloud provider
    provider_blocks = []
    data_sources = []

    # Determine cloud provider from project settings
    # IMPORTANT: Do NOT default to "aws" — null/empty means on-prem/generic kubeconfig
    cloud_provider = project.cloud_provider or ""

    # Detect Helm provider version to use correct syntax
    helm_use_assignment_syntax = detect_helm_version_syntax(versions_tf)
    if helm_use_assignment_syntax:
        logger.info(f"Using Helm 3.x assignment syntax for module {module.id}")

    # Check if we need K8s/Helm providers
    needs_k8s = 'kubernetes' in required_providers or 'helm' in required_providers

    if needs_k8s:
        # Get cluster_name from assembled variables
        cluster_name = (
            variables.get('cluster_name')
            or variables.get('eks_cluster_name')
            or variables.get('aks_cluster_name')
            or variables.get('gke_cluster_name')
            or variables.get('roks_cluster_name')
            or variables.get('openshift_cluster_name')
        )

        if not cluster_name:
            logger.warning(f"No cluster_name found in variables for module {module.id}, K8s provider may fail")
            return

        logger.info(f"Using cluster_name='{cluster_name}' for {cloud_provider} provider injection")

        # Generate cloud-specific provider configuration using utility functions
        if cloud_provider == "aws":
            region = variables.get('region') or variables.get('aws_region') or project.region
            if not region and db:
                region = get_default(db, "cloud.aws.default_region")
            region = region or "us-east-1"
            provider_blocks, data_sources = generate_aws_eks_providers(
                cluster_name=cluster_name,
                aws_region=region,
                required_providers=required_providers,
                helm_use_assignment_syntax=helm_use_assignment_syntax
            )

        elif cloud_provider == "azure":
            resource_group = variables.get('resource_group_name') or variables.get('azure_resource_group')
            provider_blocks, data_sources = generate_azure_aks_providers(
                cluster_name=cluster_name,
                resource_group=resource_group,
                required_providers=required_providers,
                helm_use_assignment_syntax=helm_use_assignment_syntax
            )

        elif cloud_provider == "gcp":
            gcp_project = variables.get('gcp_project') or variables.get('project_id')
            gcp_region = variables.get('gcp_region') or variables.get('region')
            provider_blocks, data_sources = generate_gcp_gke_providers(
                cluster_name=cluster_name,
                gcp_project=gcp_project,
                gcp_region=gcp_region,
                required_providers=required_providers,
                helm_use_assignment_syntax=helm_use_assignment_syntax
            )

        elif cloud_provider == "ibm":
            ibm_region = variables.get('ibm_region') or variables.get('region') or project.region
            resource_group_id = variables.get('ibm_resource_group_id') or variables.get('resource_group_id')
            if not ibm_region:
                logger.warning(f"No IBM region found in variables for module {module.id}, provider injection may fail")
                return
            provider_blocks, data_sources = generate_ibm_roks_providers(
                cluster_name=cluster_name,
                ibm_region=ibm_region,
                resource_group_id=resource_group_id,
                required_providers=required_providers,
                helm_use_assignment_syntax=helm_use_assignment_syntax,
            )

        else:
            # Generic/on-prem: Use kubeconfig file if available
            # Resolve from project cluster when not in variables (bare-metal/on-prem)
            kubeconfig_path = variables.get('kubeconfig_path')
            if not kubeconfig_path:
                kubeconfig_path = _resolve_project_kubeconfig_path(project)
            if not kubeconfig_path:
                kubeconfig_path = "/app/kubeconfig"
            k8s_context = variables.get('k8s_context') or project.k8s_context

            logger.info(f"Using generic kubeconfig provider for cloud_provider='{cloud_provider}'")

            provider_blocks, data_sources = generate_generic_providers(
                kubeconfig_path=kubeconfig_path,
                k8s_context=k8s_context,
                required_providers=required_providers,
                helm_use_assignment_syntax=helm_use_assignment_syntax
            )

    if not provider_blocks and not data_sources:
        return

    # Generate platform-agnostic kubeconfig local for kubectl usage in modules.
    # Modules reference local.forge_kubeconfig via try(local.forge_kubeconfig, var.forge_kubeconfig_content).
    # This bridges the gap between provider-based auth (K8s/Helm providers) and
    # kubectl local-exec provisioners that need a kubeconfig file on disk.
    # Resolve kubeconfig path for the locals block (on-prem/generic)
    from utils.provider_config import generate_forge_kubeconfig_local
    resolved_kc = variables.get('kubeconfig_path') or _resolve_project_kubeconfig_path(project)
    kubeconfig_local = generate_forge_kubeconfig_local(
        cloud_provider=cloud_provider,
        variables=variables,
        project=project,
        kubeconfig_path=resolved_kc,
    )

    # Write provider config file (NOT _override.tf - that's a special OpenTofu file type)
    provider_content = "# Auto-generated by BNK-Forge\n# Provider configuration for community modules\n"
    provider_content += "\n".join(data_sources)
    provider_content += "\n".join(provider_blocks)
    if kubeconfig_local:
        provider_content += "\n" + kubeconfig_local

    provider_path = os.path.join(work_dir, "bnk_forge_providers.tf")
    with open(provider_path, 'w') as f:
        f.write(provider_content)

    logger.info(f"Wrote provider config to {provider_path} for providers: {required_providers}")

    # Path 3: full provider-generation path.  bnk_forge_providers.tf is now on disk, so
    # _inject's missing-sources check sees the data "aws_eks_cluster" block already declared
    # and skips re-emitting it — only writing the forge_kubeconfig local when needed.
    # If generate_forge_kubeconfig_local already embedded the local in bnk_forge_providers.tf
    # above, _inject's definition guard ("forge_kubeconfig =") fires and this is a no-op.
    _inject_forge_kubeconfig_locals(work_dir, variables, project)
