"""Unit tests for credential-template-driven cluster discovery."""

from unittest.mock import MagicMock, patch

import pytest

from core.encryption import encrypt_value
from models import CloudCredentialTemplate
from services.cluster_discovery_service import ClusterDiscoveryService


def _aws_template() -> CloudCredentialTemplate:
    return CloudCredentialTemplate(
        name="aws-default",
        provider="aws",
        aws_auth_method="access_keys",
        aws_access_key_id="AKIA_TEST",
        aws_secret_access_key_encrypted=encrypt_value("secret"),
        region="us-east-1",
        is_default=True,
    )


def _ibm_template() -> CloudCredentialTemplate:
    return CloudCredentialTemplate(
        name="ibm-default",
        provider="ibm",
        ibmcloud_api_key_encrypted=encrypt_value("ibm-api-key"),
        region="us-south",
        is_default=True,
    )


class TestDetectClustersFromCredentials:
    """Credential-template discovery dispatches per provider and registers clusters."""

    def test_no_templates_returns_empty(self, db, make_project):
        project = make_project()
        svc = ClusterDiscoveryService(db)
        result = svc.detect_clusters_from_credentials(project.id)

        assert result["success"] is True
        assert result["registered"] == []
        assert result["skipped"] == []
        assert result["errors"] == []
        assert "No cloud credential templates" in result["message"]

    @patch("services.cluster_discovery_service.list_eks_clusters_from_template")
    def test_registers_aws_cluster(self, mock_list, db, make_project):
        project = make_project()
        template = _aws_template()
        db.add(template)
        db.flush()
        project.credential_template_id = template.id
        db.flush()

        mock_list.return_value = [
            {
                "name": "eks-prod",
                "endpoint": "https://ABC123.eks.amazonaws.com",
                "certificate_authority_data": "LS0tLS1CRUdJTi...",
                "region": "us-east-1",
                "version": "1.29",
                "arn": "arn:aws:eks:us-east-1:123456789012:cluster/eks-prod",
                "account_id": "123456789012",
            }
        ]

        svc = ClusterDiscoveryService(db)
        result = svc.detect_clusters_from_credentials(project.id)

        assert result["success"] is True
        assert len(result["registered"]) == 1
        assert result["registered"][0]["name"] == "eks-prod"
        assert result["registered"][0]["provider"] == "aws"
        assert result["registered"][0]["status"] == "registered"
        assert len(result["errors"]) == 0

    @patch("services.cluster_discovery_service.list_eks_clusters_from_template")
    def test_skips_already_registered_aws_cluster(self, mock_list, db, make_project, make_k8s_cluster):
        project = make_project()
        make_k8s_cluster(project=project, name="eks-prod", cloud_provider="aws")
        template = _aws_template()
        db.add(template)
        db.flush()
        project.credential_template_id = template.id
        db.flush()

        mock_list.return_value = [
            {
                "name": "eks-prod",
                "endpoint": "https://ABC123.eks.amazonaws.com",
                "certificate_authority_data": "LS0tLS1CRUdJTi...",
                "region": "us-east-1",
            }
        ]

        svc = ClusterDiscoveryService(db)
        result = svc.detect_clusters_from_credentials(project.id)

        assert result["registered"] == []
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["name"] == "eks-prod"
        assert result["skipped"][0]["reason"] == "already_registered"

    @patch("services.cluster_discovery_service.list_eks_clusters_from_template")
    def test_captures_provider_error(self, mock_list, db, make_project):
        project = make_project()
        template = _aws_template()
        db.add(template)
        db.flush()
        project.credential_template_id = template.id
        db.flush()

        mock_list.side_effect = RuntimeError("AWS API unreachable")

        svc = ClusterDiscoveryService(db)
        result = svc.detect_clusters_from_credentials(project.id)

        assert result["success"] is True
        assert result["registered"] == []
        assert len(result["errors"]) == 1
        assert result["errors"][0]["provider"] == "aws"
        assert "AWS API unreachable" in result["errors"][0]["error"]

    @patch("services.cluster_discovery_service.list_eks_clusters_from_template")
    def test_uses_default_template_when_project_has_none(self, mock_list, db, make_project):
        project = make_project()
        template = _aws_template()
        db.add(template)
        db.flush()

        mock_list.return_value = []

        svc = ClusterDiscoveryService(db)
        result = svc.detect_clusters_from_credentials(project.id)

        assert result["success"] is True
        assert "No clusters found" in result["message"]
        mock_list.assert_called_once()

    @patch("services.cluster_discovery_service.list_roks_clusters_from_template")
    @patch("services.cluster_discovery_service.describe_roks_cluster")
    @patch("services.cluster_discovery_service.IBMCloudService")
    def test_registers_ibm_cluster(
        self,
        mock_ibm_svc_cls,
        mock_describe,
        mock_list,
        db,
        make_project,
    ):
        project = make_project()
        template = _ibm_template()
        db.add(template)
        db.flush()
        project.credential_template_id = template.id
        db.flush()

        mock_ibm_svc = MagicMock()
        mock_ibm_svc._exchange_api_key.return_value = "fresh-ibm-token"
        mock_ibm_svc_cls.return_value = mock_ibm_svc

        mock_list.return_value = [
            {
                "name": "roks-prod",
                "id": "roks-id-1",
                "region": "us-south",
                "resource_group": "default",
            }
        ]
        mock_describe.return_value = {
            "serverURL": "https://roks-prod.example.com:6443",
            "caCert": "LS0tLS1CRUdJTi...",
        }

        svc = ClusterDiscoveryService(db)
        result = svc.detect_clusters_from_credentials(project.id)

        assert result["success"] is True
        assert len(result["registered"]) == 1
        assert result["registered"][0]["name"] == "roks-prod"
        assert result["registered"][0]["provider"] == "ibm"

    @patch("services.cluster_discovery_service.list_aks_clusters_from_template")
    @patch("services.cluster_discovery_service.fetch_aks_cluster_credentials")
    @patch("services.cluster_discovery_service.fetch_aks_bearer_token")
    def test_registers_azure_cluster(
        self,
        mock_token,
        mock_fetch,
        mock_list,
        db,
        make_project,
    ):
        mock_token.return_value = "test-aad-token"
        from core.encryption import encrypt_value

        project = make_project()
        template = CloudCredentialTemplate(
            name="azure-default",
            provider="azure",
            azure_subscription_id="sub-123",
            azure_tenant_id="tenant-456",
            azure_credentials_encrypted=encrypt_value(
                '{"client_id": "cid", "client_secret": "csecret"}'
            ),
            is_default=True,
        )
        db.add(template)
        db.flush()
        project.credential_template_id = template.id
        db.flush()

        mock_list.return_value = [
            {
                "name": "aks-prod",
                "resource_group": "rg-prod",
                "subscription_id": "sub-123",
                "tenant_id": "tenant-456",
                "location": "eastus",
                "version": "1.29",
            }
        ]
        mock_fetch.return_value = {
            "server": "aks-prod.hcp.eastus.azmk8s.io",
            "certificate_authority_data": "LS0tLS1CRUdJTi...",
        }

        svc = ClusterDiscoveryService(db)
        result = svc.detect_clusters_from_credentials(project.id)

        assert result["success"] is True
        assert len(result["registered"]) == 1
        assert result["registered"][0]["name"] == "aks-prod"
        assert result["registered"][0]["provider"] == "azure"

    @patch("services.cluster_discovery_service.list_gke_clusters_from_template")
    @patch("services.cluster_discovery_service.fetch_gke_cluster_credentials")
    def test_registers_gcp_cluster(
        self,
        mock_fetch,
        mock_list,
        db,
        make_project,
    ):
        from core.encryption import encrypt_value

        project = make_project()
        template = CloudCredentialTemplate(
            name="gcp-default",
            provider="gcp",
            gcp_project_id="my-gcp-project",
            gcp_credentials_encrypted=encrypt_value(
                '{"type": "service_account", "project_id": "my-gcp-project"}'
            ),
            is_default=True,
        )
        db.add(template)
        db.flush()
        project.credential_template_id = template.id
        db.flush()

        mock_list.return_value = [
            {
                "name": "gke-prod",
                "project_id": "my-gcp-project",
                "location": "us-central1",
                "full_name": "projects/my-gcp-project/locations/us-central1/clusters/gke-prod",
                "version": "1.29",
            }
        ]
        mock_fetch.return_value = {
            "server": "https://1.2.3.4",
            "certificate_authority_data": "LS0tLS1CRUdJTi...",
        }

        svc = ClusterDiscoveryService(db)
        result = svc.detect_clusters_from_credentials(project.id)

        assert result["success"] is True
        assert len(result["registered"]) == 1
        assert result["registered"][0]["name"] == "gke-prod"
        assert result["registered"][0]["provider"] == "gcp"
