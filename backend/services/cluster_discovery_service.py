"""Credential-template-driven Kubernetes cluster discovery.

This service queries cloud provider APIs using a project's credential templates
and registers discovered clusters in the same way as module-output-driven
discovery (``services/cluster_management_service.py``).

Supported providers: aws, ibm, azure, gcp.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from core.encryption import encrypt_value
from models import CloudCredentialTemplate, KubernetesCluster
from services.azure_service import (
    fetch_aks_bearer_token,
    fetch_aks_cluster_credentials,
    generate_aks_kubeconfig,
    list_aks_clusters_from_template,
)
from services.base_service import BaseService
from services.eks_service import generate_eks_kubeconfig, list_eks_clusters_from_template
from services.gcp_service import (
    fetch_gke_cluster_credentials,
    generate_gke_kubeconfig,
    list_gke_clusters_from_template,
)
from services.ibm_cloud_service import (
    IBMCloudService,
    describe_roks_cluster,
    generate_roks_kubeconfig,
    list_roks_clusters_from_template,
)
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig
from services.platform_context_service import PlatformContextService

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = {"aws", "ibm", "azure", "gcp"}


def register_discovered_cluster(
    db: Session,
    project_id: int,
    name: str,
    api_server: str,
    cloud_provider: str,
    region: str | None,
    kubeconfig_yaml: str,
    context_name: str | None = None,
    version: str | None = None,
    meta_data: dict[str, Any] | None = None,
) -> KubernetesCluster:
    """Register a cloud-discovered cluster, reusing existing normalization.

    Idempotent: if a cluster with the same name already exists in the project,
    the existing record is returned as a skip.

    This helper is the single registration path for credential-template
    discovery so EKS/ROKS/Azure/GCP discovery do not duplicate cluster creation
    logic.
    """
    existing = db.query(KubernetesCluster).filter(
        KubernetesCluster.name == name,
        KubernetesCluster.project_id == project_id,
    ).first()
    if existing:
        logger.info(f"Cluster {name} already registered (id={existing.id}), skipping")
        return existing

    kubeconfig_yaml = normalize_kubeconfig(
        kubeconfig_yaml, source=NormalizationSource.CLOUD_API_GENERATED
    )
    kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)

    cluster = KubernetesCluster(
        name=name,
        context=context_name or name,
        api_server=api_server,
        version=version,
        status="active",
        project_id=project_id,
        kubeconfig_encrypted=kubeconfig_encrypted,
        cloud_provider=cloud_provider,
        region=region,
        default_namespace="default",
        meta_data={
            "auto_registered": True,
            "discovery_source": "credential_template",
            **(meta_data or {}),
        },
    )
    PlatformContextService.apply_cluster_context(cluster)
    db.add(cluster)
    db.flush()
    db.refresh(cluster)

    # D-022: upsert fleet membership for the newly discovered cluster.
    try:
        from services.fleet_reconcile_service import reconcile_fleet_member
        reconcile_fleet_member(db, "cluster", cluster.id)
    except Exception:
        logger.exception("Fleet reconcile failed for discovered cluster id=%s", cluster.id)

    logger.info(f"Registered discovered {cloud_provider} cluster {name} (id={cluster.id})")
    return cluster


class ClusterDiscoveryService(BaseService):
    """Discover Kubernetes clusters from a project's credential templates."""

    def _project_templates(self, project_id: int) -> list[CloudCredentialTemplate]:
        """Return cloud credential templates relevant to *project_id*.

        Priority:
        1. The project's explicitly bound credential template (if any).
        2. Default templates for each supported provider that has no explicit
           template yet.
        """
        project = self._get_project(project_id)
        templates: list[CloudCredentialTemplate] = []
        seen_providers: set[str] = set()

        if project.credential_template_id and project.credential_template:
            templates.append(project.credential_template)
            seen_providers.add(project.credential_template.provider)

        for provider in SUPPORTED_PROVIDERS - seen_providers:
            default = self.db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.provider == provider,
                CloudCredentialTemplate.is_default.is_(True),
            ).first()
            if default:
                templates.append(default)

        return templates

    def detect_clusters_from_credentials(self, project_id: int) -> dict[str, Any]:
        """Query cloud APIs from credential templates and register clusters."""
        self._get_project(project_id)
        templates = self._project_templates(project_id)

        if not templates:
            return {
                "success": True,
                "message": "No cloud credential templates configured for this project",
                "registered": [],
                "skipped": [],
                "errors": [],
            }

        registered_clusters: list[dict[str, Any]] = []
        skipped_clusters: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for template in templates:
            provider = template.provider
            try:
                provider_registered, provider_skipped, provider_errors = self._detect_for_provider(
                    project_id, template
                )
                registered_clusters.extend(provider_registered)
                skipped_clusters.extend(provider_skipped)
                errors.extend(provider_errors)
            except Exception as e:
                logger.exception(f"Credential discovery failed for provider {provider}")
                errors.append({
                    "provider": provider,
                    "name": None,
                    "error": str(e),
                })

        total_found = len(registered_clusters) + len(skipped_clusters)
        message = (
            f"Discovered {total_found} cluster(s) from credential templates, "
            f"registered {len(registered_clusters)} new cluster(s)"
        )
        if not total_found and not errors:
            message = "No clusters found via credential templates"

        return {
            "success": True,
            "message": message,
            "registered": registered_clusters,
            "skipped": skipped_clusters,
            "errors": errors,
        }

    def _detect_for_provider(
        self,
        project_id: int,
        template: CloudCredentialTemplate,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Dispatch to the correct provider discovery implementation."""
        provider = template.provider
        if provider == "aws":
            return self._detect_aws_clusters(project_id, template)
        if provider == "ibm":
            return self._detect_ibm_clusters(project_id, template)
        if provider == "azure":
            return self._detect_azure_clusters(project_id, template)
        if provider == "gcp":
            return self._detect_gcp_clusters(project_id, template)

        return [], [], [{"provider": provider, "name": None, "error": "Unsupported provider"}]

    def _detect_aws_clusters(
        self,
        project_id: int,
        template: CloudCredentialTemplate,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        registered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        try:
            clusters = list_eks_clusters_from_template(template)
        except Exception as e:
            return [], [], [{"provider": "aws", "name": None, "error": str(e)}]

        for cluster in clusters:
            name = cluster["name"]
            try:
                existing = self.db.query(KubernetesCluster).filter(
                    KubernetesCluster.name == name,
                    KubernetesCluster.project_id == project_id,
                ).first()
                if existing:
                    skipped.append({
                        "provider": "aws",
                        "name": name,
                        "reason": "already_registered",
                    })
                    continue

                kubeconfig_yaml = generate_eks_kubeconfig(
                    cluster_name=name,
                    cluster_endpoint=cluster["endpoint"],
                    cluster_ca_data=cluster["certificate_authority_data"],
                    region=cluster["region"],
                )
                registered_cluster = register_discovered_cluster(
                    self.db,
                    project_id=project_id,
                    name=name,
                    api_server=cluster["endpoint"],
                    cloud_provider="aws",
                    region=cluster["region"],
                    kubeconfig_yaml=kubeconfig_yaml,
                    context_name=name,
                    version=cluster["version"],
                    meta_data={
                        "cluster_arn": cluster["arn"],
                        "account_id": cluster["account_id"],
                    },
                )
                registered.append({
                    "id": registered_cluster.id,
                    "name": registered_cluster.name,
                    "provider": "aws",
                    "status": "registered",
                })
            except Exception as e:
                logger.warning(f"Failed to register discovered AWS cluster {name}: {e}")
                errors.append({"provider": "aws", "name": name, "error": str(e)})

        return registered, skipped, errors

    def _detect_ibm_clusters(
        self,
        project_id: int,
        template: CloudCredentialTemplate,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        from core.encryption import decrypt_value

        registered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        try:
            clusters = list_roks_clusters_from_template(template)
        except Exception as e:
            return [], [], [{"provider": "ibm", "name": None, "error": str(e)}]

        api_key = decrypt_value(template.ibmcloud_api_key_encrypted) if template.ibmcloud_api_key_encrypted else None
        access_token = IBMCloudService(None)._exchange_api_key(api_key, template=template) if api_key else None

        for cluster in clusters:
            name = cluster["name"]
            try:
                existing = self.db.query(KubernetesCluster).filter(
                    KubernetesCluster.name == name,
                    KubernetesCluster.project_id == project_id,
                ).first()
                if existing:
                    skipped.append({
                        "provider": "ibm",
                        "name": name,
                        "reason": "already_registered",
                    })
                    continue

                config = describe_roks_cluster(name, access_token)
                server_url = config.get("serverURL") or cluster.get("server_url")
                ca_cert = config.get("caCert")
                if not server_url or not ca_cert:
                    errors.append({
                        "provider": "ibm",
                        "name": name,
                        "error": "Cluster config missing server URL or CA certificate",
                    })
                    continue

                if not access_token:
                    errors.append({
                        "provider": "ibm",
                        "name": name,
                        "error": "Unable to obtain IBM IAM token",
                    })
                    continue

                kubeconfig_yaml = generate_roks_kubeconfig(
                    cluster_name=name,
                    server_url=server_url,
                    ca_cert=ca_cert,
                    token=access_token,
                )
                registered_cluster = register_discovered_cluster(
                    self.db,
                    project_id=project_id,
                    name=name,
                    api_server=server_url,
                    cloud_provider="ibm",
                    region=cluster.get("region") or template.region,
                    kubeconfig_yaml=kubeconfig_yaml,
                    context_name=name,
                    meta_data={
                        "cluster_id": cluster.get("id"),
                        "resource_group": cluster.get("resource_group"),
                    },
                )
                registered.append({
                    "id": registered_cluster.id,
                    "name": registered_cluster.name,
                    "provider": "ibm",
                    "status": "registered",
                })
            except Exception as e:
                logger.warning(f"Failed to register discovered IBM cluster {name}: {e}")
                errors.append({"provider": "ibm", "name": name, "error": str(e)})

        return registered, skipped, errors

    def _detect_azure_clusters(
        self,
        project_id: int,
        template: CloudCredentialTemplate,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        registered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        try:
            clusters = list_aks_clusters_from_template(template)
        except Exception as e:
            return [], [], [{"provider": "azure", "name": None, "error": str(e)}]

        for cluster in clusters:
            name = cluster["name"]
            try:
                existing = self.db.query(KubernetesCluster).filter(
                    KubernetesCluster.name == name,
                    KubernetesCluster.project_id == project_id,
                ).first()
                if existing:
                    skipped.append({
                        "provider": "azure",
                        "name": name,
                        "reason": "already_registered",
                    })
                    continue

                creds = fetch_aks_cluster_credentials(
                    cluster_name=name,
                    resource_group=cluster["resource_group"],
                    subscription_id=cluster["subscription_id"],
                    template=template,
                )
                token = fetch_aks_bearer_token(template)
                kubeconfig_yaml = generate_aks_kubeconfig(
                    cluster_name=name,
                    server=creds["server"],
                    ca_data=creds["certificate_authority_data"],
                    token=token,
                )
                registered_cluster = register_discovered_cluster(
                    self.db,
                    project_id=project_id,
                    name=name,
                    api_server=f"https://{creds['server']}:443",
                    cloud_provider="azure",
                    region=cluster.get("location"),
                    kubeconfig_yaml=kubeconfig_yaml,
                    context_name=name,
                    version=cluster.get("version"),
                    meta_data={
                        "subscription_id": cluster["subscription_id"],
                        "tenant_id": cluster["tenant_id"],
                    },
                )
                registered.append({
                    "id": registered_cluster.id,
                    "name": registered_cluster.name,
                    "provider": "azure",
                    "status": "registered",
                })
            except Exception as e:
                logger.warning(f"Failed to register discovered Azure cluster {name}: {e}")
                errors.append({"provider": "azure", "name": name, "error": str(e)})

        return registered, skipped, errors

    def _detect_gcp_clusters(
        self,
        project_id: int,
        template: CloudCredentialTemplate,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        registered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        try:
            clusters = list_gke_clusters_from_template(template)
        except Exception as e:
            return [], [], [{"provider": "gcp", "name": None, "error": str(e)}]

        for cluster in clusters:
            name = cluster["name"]
            try:
                existing = self.db.query(KubernetesCluster).filter(
                    KubernetesCluster.name == name,
                    KubernetesCluster.project_id == project_id,
                ).first()
                if existing:
                    skipped.append({
                        "provider": "gcp",
                        "name": name,
                        "reason": "already_registered",
                    })
                    continue

                creds = fetch_gke_cluster_credentials(
                    cluster_name=name,
                    project_id=cluster["project_id"],
                    location=cluster["location"],
                    template=template,
                )
                kubeconfig_yaml = generate_gke_kubeconfig(
                    cluster_name=name,
                    project_id=cluster["project_id"],
                    location=cluster["location"],
                    server=creds["server"],
                    certificate_authority_data=creds["certificate_authority_data"],
                )
                registered_cluster = register_discovered_cluster(
                    self.db,
                    project_id=project_id,
                    name=name,
                    api_server=creds["server"],
                    cloud_provider="gcp",
                    region=cluster.get("location"),
                    kubeconfig_yaml=kubeconfig_yaml,
                    context_name=name,
                    version=cluster.get("version"),
                    meta_data={
                        "project_id": cluster["project_id"],
                        "full_name": cluster["full_name"],
                    },
                )
                registered.append({
                    "id": registered_cluster.id,
                    "name": registered_cluster.name,
                    "provider": "gcp",
                    "status": "registered",
                })
            except Exception as e:
                logger.warning(f"Failed to register discovered GCP cluster {name}: {e}")
                errors.append({"provider": "gcp", "name": name, "error": str(e)})

        return registered, skipped, errors
