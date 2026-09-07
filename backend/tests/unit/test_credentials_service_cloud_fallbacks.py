"""
Unit tests for credentials_service GCP and Azure template fallback and resolution.
"""

import json
from unittest.mock import MagicMock

from core.encryption import encrypt_value
from models import CloudCredentialTemplate, Project
from services.credentials_service import (
    get_azure_service_principal_info,
    get_gcp_service_account_info,
)


class TestCredentialsServiceCloudFallbacks:
    def test_get_gcp_service_account_info_falls_back_to_default_template(self):
        db = MagicMock()
        sa_dict = {
            "type": "service_account",
            "project_id": "gcp-proj-123",
            "client_email": "sa@gcp-proj-123.iam.gserviceaccount.com",
        }
        enc_sa = encrypt_value(json.dumps(sa_dict))

        default_template = CloudCredentialTemplate(
            id=10,
            name="default-gcp",
            provider="gcp",
            is_default=True,
            gcp_credentials_encrypted=enc_sa,
        )

        project_without_template = Project(
            id=1,
            name="gke-demo",
            cloud_provider="gcp",
            credential_template_id=None,
        )

        # Mock db.query(...).filter(...).first() to return default_template
        query_mock = MagicMock()
        filter_mock = MagicMock()
        filter_mock.first.return_value = default_template
        query_mock.filter.return_value = filter_mock
        db.query.return_value = query_mock

        result = get_gcp_service_account_info(project_without_template, db)
        assert result == sa_dict

    def test_get_azure_service_principal_info_falls_back_to_default_template(self):
        db = MagicMock()
        creds_dict = {
            "client_id": "my-client-id",
            "client_secret": "my-client-secret",
        }
        enc_creds = encrypt_value(json.dumps(creds_dict))

        default_template = CloudCredentialTemplate(
            id=20,
            name="default-azure",
            provider="azure",
            is_default=True,
            azure_tenant_id="tenant-123",
            azure_credentials_encrypted=enc_creds,
        )

        project_without_template = Project(
            id=2,
            name="aks-demo",
            cloud_provider="azure",
            credential_template_id=None,
        )

        query_mock = MagicMock()
        filter_mock = MagicMock()
        filter_mock.first.return_value = default_template
        query_mock.filter.return_value = filter_mock
        db.query.return_value = query_mock

        result = get_azure_service_principal_info(project_without_template, db)
        assert result == ("tenant-123", "my-client-id", "my-client-secret")

    def test_get_gcp_service_account_info_does_not_fall_back_when_template_id_set_to_non_gcp(self):
        db = MagicMock()
        non_gcp_template = CloudCredentialTemplate(
            id=99,
            name="aws-template",
            provider="aws",
            is_default=False,
        )
        project_with_aws_template = Project(
            id=3,
            name="gke-demo-mismatched",
            cloud_provider="gcp",
            credential_template_id=99,
        )

        query_mock = MagicMock()
        filter_mock = MagicMock()
        filter_mock.first.return_value = non_gcp_template
        query_mock.filter.return_value = filter_mock
        db.query.return_value = query_mock

        result = get_gcp_service_account_info(project_with_aws_template, db)
        assert result is None

    def test_get_azure_service_principal_info_does_not_fall_back_when_template_id_set_to_non_azure(self):
        db = MagicMock()
        non_azure_template = CloudCredentialTemplate(
            id=88,
            name="gcp-template",
            provider="gcp",
            is_default=False,
        )
        project_with_gcp_template = Project(
            id=4,
            name="aks-demo-mismatched",
            cloud_provider="azure",
            credential_template_id=88,
        )

        query_mock = MagicMock()
        filter_mock = MagicMock()
        filter_mock.first.return_value = non_azure_template
        query_mock.filter.return_value = filter_mock
        db.query.return_value = query_mock

        result = get_azure_service_principal_info(project_with_gcp_template, db)
        assert result is None
