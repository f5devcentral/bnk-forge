"""
EKS-specific service for auto-registration and kubeconfig generation.

ENG-006: This service retains its own db.commit() calls because it is called
exclusively from Celery tasks (opentofu_tasks.py) which are the outermost callers.
"""

import logging
from typing import Any

import yaml
from sqlalchemy.orm import Session

from core.encryption import encrypt_value
from models import KubernetesCluster, ProjectModule
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig

logger = logging.getLogger(__name__)

# Required output keys that identify a deployed EKS cluster.
# This list is the single source of truth shared by register_eks_cluster (validation)
# and matches_eks_output_contract (detection) — they must not drift.
_EKS_CONTRACT_REQUIRED_KEYS = (
    "cluster_name",
    "cluster_endpoint",
    "cluster_certificate_authority_data",
    "region",
)


def matches_eks_output_contract(outputs: dict) -> bool:
    """Return True iff *outputs* satisfies the EKS registration contract.

    A module's outputs match the contract when all four required keys are
    present with non-empty values. The key set is the same one that
    ``register_eks_cluster`` validates so the two functions cannot drift.

    Args:
        outputs: Module output dict (may be None or empty — both return False).

    Returns:
        True if the outputs identify a deployable EKS cluster; False otherwise.
    """
    if not outputs or not isinstance(outputs, dict):
        return False
    return all(bool(outputs.get(key)) for key in _EKS_CONTRACT_REQUIRED_KEYS)


def generate_eks_kubeconfig(
    cluster_name: str,
    cluster_endpoint: str,
    cluster_ca_data: str,
    region: str,
    context_name: str = None
) -> str:
    """
    Generate a kubeconfig YAML for an EKS cluster.

    Uses aws eks get-token for authentication (exec plugin).
    This requires AWS CLI credentials to be configured.

    Args:
        cluster_name: EKS cluster name
        cluster_endpoint: HTTPS endpoint URL
        cluster_ca_data: Base64-encoded CA certificate
        region: AWS region (e.g., 'ap-southeast-2')
        context_name: Optional context name (defaults to cluster_name)

    Returns:
        YAML string with complete kubeconfig
    """
    if not context_name:
        context_name = cluster_name

    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": cluster_name,
                "cluster": {
                    "server": cluster_endpoint,
                    "certificate-authority-data": cluster_ca_data
                }
            }
        ],
        "contexts": [
            {
                "name": context_name,
                "context": {
                    "cluster": cluster_name,
                    "user": cluster_name
                }
            }
        ],
        "current-context": context_name,
        "users": [
            {
                "name": cluster_name,
                "user": {
                    "exec": {
                        "apiVersion": "client.authentication.k8s.io/v1beta1",
                        "command": "aws",
                        "args": [
                            "eks",
                            "get-token",
                            "--cluster-name",
                            cluster_name,
                            "--region",
                            region
                        ]
                    }
                }
            }
        ]
    }

    return yaml.dump(kubeconfig)


def register_eks_cluster(db: Session, eks_module: ProjectModule) -> KubernetesCluster:
    """
    Auto-register an EKS cluster to the Kubernetes page after deployment.

    Args:
        db: Database session
        eks_module: The deployed EKS ProjectModule with outputs

    Returns:
        The created or existing KubernetesCluster record

    Raises:
        ValueError: If required outputs are missing
    """
    # Extract required outputs
    outputs = eks_module.outputs
    cluster_name = outputs.get("cluster_name")
    cluster_endpoint = outputs.get("cluster_endpoint")
    cluster_ca_data = outputs.get("cluster_certificate_authority_data")

    # Extract region from cluster ARN (most accurate - shows actual deployment location)
    region = None
    if outputs.get("cluster_arn"):
        # ARN format: arn:aws:eks:region:account:cluster/name
        arn_parts = outputs.get("cluster_arn").split(":")
        if len(arn_parts) >= 4:
            region = arn_parts[3]

    # Fallback: use project region if ARN extraction failed
    if not region and hasattr(eks_module.project, 'region'):
        region = eks_module.project.region

    # Validate required outputs
    if not all([cluster_name, cluster_endpoint, cluster_ca_data, region]):
        missing = []
        if not cluster_name:
            missing.append("cluster_name")
        if not cluster_endpoint:
            missing.append("cluster_endpoint")
        if not cluster_ca_data:
            missing.append("cluster_certificate_authority_data")
        if not region:
            missing.append("region")
        raise ValueError(f"Missing required EKS outputs for registration: {', '.join(missing)}")

    # Check if cluster already registered
    existing = db.query(KubernetesCluster).filter(
        KubernetesCluster.name == cluster_name,
        KubernetesCluster.project_id == eks_module.project_id
    ).first()

    if existing:
        logger.info(f"Cluster {cluster_name} already registered (id={existing.id}), skipping")
        return existing

    # Generate kubeconfig
    logger.info(f"Generating kubeconfig for EKS cluster {cluster_name}")
    kubeconfig_yaml = generate_eks_kubeconfig(
        cluster_name=cluster_name,
        cluster_endpoint=cluster_endpoint,
        cluster_ca_data=cluster_ca_data,
        region=region
    )

    # Assert invariant — EKS kubeconfig builder must produce portable output.
    # Hard-fail on violation (kubeconfig_invariant_violation) so the bug surfaces loudly.
    try:
        kubeconfig_yaml = normalize_kubeconfig(
            kubeconfig_yaml, source=NormalizationSource.CLOUD_API_GENERATED
        )
    except Exception as norm_exc:
        logger.error(
            "kubeconfig_invariant_violation: EKS kubeconfig builder produced non-portable "
            "output for cluster %s: %s",
            cluster_name, norm_exc,
        )
        raise

    # Encrypt kubeconfig
    logger.info(f"Encrypting kubeconfig for cluster {cluster_name}")
    kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)

    # Create cluster record
    cluster = KubernetesCluster(
        name=cluster_name,
        context=cluster_name,  # Use cluster name as context
        api_server=cluster_endpoint,
        version=outputs.get("cluster_version"),
        status="active",
        project_id=eks_module.project_id,
        kubeconfig_encrypted=kubeconfig_encrypted,
        cloud_provider="aws",
        region=region,
        default_namespace="default",
        meta_data={
            "auto_registered": True,
            "source_module_id": eks_module.id,
            "cluster_arn": outputs.get("cluster_arn"),
            "oidc_provider_url": outputs.get("oidc_provider_url"),
            "nodegroup_name": outputs.get("nodegroup_name"),
            "nodegroup_status": outputs.get("nodegroup_status")
        }
    )

    db.add(cluster)
    db.commit()
    db.refresh(cluster)

    logger.info(f"Successfully registered EKS cluster {cluster_name} (id={cluster.id}) to Kubernetes page")
    return cluster


def unregister_eks_cluster(db: Session, eks_module: ProjectModule) -> bool:
    """
    Remove an EKS cluster from the Kubernetes page when module is destroyed.

    Args:
        db: Database session
        eks_module: The EKS ProjectModule being destroyed

    Returns:
        True if cluster was removed, False if not found
    """
    # Find cluster registered by this module
    cluster = db.query(KubernetesCluster).filter(
        KubernetesCluster.project_id == eks_module.project_id,
        KubernetesCluster.meta_data.op('->>')('source_module_id') == str(eks_module.id)
    ).first()

    if cluster:
        cluster_name = cluster.name
        db.delete(cluster)
        db.commit()
        logger.info(f"Unregistered EKS cluster {cluster_name} (id={cluster.id}) from Kubernetes page")
        return True

    logger.info(f"No registered cluster found for module {eks_module.id}")
    return False


# ---------------------------------------------------------------------------
# Credential-template-driven discovery
# ---------------------------------------------------------------------------

def _boto3_session_from_template(template) -> Any:
    """Build a boto3 Session from a CloudCredentialTemplate.

    Reuses the same credential-resolution rules as CredentialTemplateService
    so credential-template discovery never duplicates auth logic.
    """
    import boto3

    if template.aws_auth_method == 'access_keys':
        from core.encryption import decrypt_value

        secret_key = decrypt_value(template.aws_secret_access_key_encrypted) \
            if template.aws_secret_access_key_encrypted else None
        session_token = decrypt_value(template.aws_session_token_encrypted) \
            if template.aws_session_token_encrypted else None
        return boto3.Session(
            aws_access_key_id=template.aws_access_key_id,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
            region_name=template.region or None,
        )

    if template.aws_auth_method == 'profile':
        return boto3.Session(profile_name=template.aws_profile, region_name=template.region or None)

    # SSO / default session — picks up whatever the SSO flow already populated.
    return boto3.Session(region_name=template.region or None)


def list_eks_clusters_from_template(template) -> list[dict[str, Any]]:
    """List EKS clusters reachable via *template* credentials.

    Returns a list of cluster info dicts with the keys required by
    ``generate_eks_kubeconfig`` plus metadata for registration.
    """
    from botocore.exceptions import BotoCoreError, ClientError

    from core.aws_config import adaptive_retry_config

    session = _boto3_session_from_template(template)
    region = template.region or session.region_name
    if not region:
        raise ValueError("AWS region is required to list EKS clusters")

    try:
        eks = session.client('eks', region_name=region, config=adaptive_retry_config())
        response = eks.list_clusters()
        cluster_names = response.get('clusters', [])
    except (ClientError, BotoCoreError) as e:
        raise RuntimeError(f"Failed to list EKS clusters in {region}: {e}") from e

    clusters = []
    for name in cluster_names:
        try:
            detail = eks.describe_cluster(name=name)['cluster']
        except (ClientError, BotoCoreError) as e:
            logger.warning(f"Skipping EKS cluster {name}: describe_cluster failed: {e}")
            continue

        endpoint = detail.get('endpoint')
        ca_data = detail.get('certificateAuthority', {}).get('data')
        if not endpoint or not ca_data:
            logger.warning(f"Skipping EKS cluster {name}: missing endpoint or CA data")
            continue

        clusters.append({
            "name": name,
            "endpoint": endpoint,
            "certificate_authority_data": ca_data,
            "region": region,
            "version": detail.get('version'),
            "arn": detail.get('arn'),
            "account_id": detail.get('arn', '').split(":")[4] if detail.get('arn') else None,
        })

    return clusters
