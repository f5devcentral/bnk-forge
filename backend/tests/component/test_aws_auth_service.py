"""
BC-027: Component tests for AWSAuthService — device auth, assume role,
credential refresh, credential validation.

All boto3 clients are mocked via _import_boto3() (lazy import pattern).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from services.aws_auth_service import AWSAuthService
from tests.mocks.aws_mock import mock_sso_oidc_client, mock_sts_client

# ── Initiate Device Authorization ────────────────────────────────────

class TestInitiateDeviceAuth:
    @patch("services.aws_auth_service._import_boto3")
    def test_success(self, mock_import_boto3):
        mock_boto3 = MagicMock()
        mock_oidc = mock_sso_oidc_client()
        mock_boto3.client.return_value = mock_oidc
        mock_import_boto3.return_value = mock_boto3

        svc = AWSAuthService()
        result = svc.initiate_device_authorization(
            start_url="https://my-sso.awsapps.com/start",
            region="us-east-1",
        )

        assert result["device_code"] == "test-device-code"
        assert result["user_code"] == "TEST-CODE"
        assert result["client_id"] == "test-client-id"
        assert result["start_url"] == "https://my-sso.awsapps.com/start"

    @patch("services.aws_auth_service._import_boto3")
    def test_failure_raises(self, mock_import_boto3):
        mock_boto3 = MagicMock()
        mock_oidc = MagicMock()
        mock_oidc.register_client.side_effect = ClientError(
            {"Error": {"Code": "InternalServerException", "Message": "Service error"}},
            "RegisterClient",
        )
        mock_boto3.client.return_value = mock_oidc
        mock_import_boto3.return_value = mock_boto3

        svc = AWSAuthService()
        with pytest.raises(Exception, match="AWS SSO device authorization failed"):
            svc.initiate_device_authorization(
                start_url="https://my-sso.awsapps.com/start",
                region="us-east-1",
            )


# ── Assume Role ──────────────────────────────────────────────────────

class TestAssumeRole:
    @patch("services.aws_auth_service._import_boto3")
    def test_assume_role_success(self, mock_import_boto3):
        mock_boto3 = MagicMock()
        mock_sts = mock_sts_client()
        # The mock returns Expiration as a string, but the real client returns datetime
        mock_sts.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId": "ASIA_TEST",
                "SecretAccessKey": "test-secret",
                "SessionToken": "test-token",
                "Expiration": datetime(2026, 12, 31, 23, 59, 59),
            },
            "AssumedRoleUser": {
                "AssumedRoleId": "AROA:session",
                "Arn": "arn:aws:sts::123456789012:assumed-role/role/session",
            },
        }
        mock_boto3.client.return_value = mock_sts
        mock_import_boto3.return_value = mock_boto3

        svc = AWSAuthService()
        result = svc.assume_role(
            role_arn="arn:aws:iam::123456789012:role/TestRole",
            region="us-east-1",
        )

        assert result["access_key_id"] == "ASIA_TEST"
        assert result["secret_access_key"] == "test-secret"
        assert result["session_token"] == "test-token"

    @patch("services.aws_auth_service._import_boto3")
    def test_assume_role_failure(self, mock_import_boto3):
        mock_boto3 = MagicMock()
        mock_sts = MagicMock()
        mock_sts.assume_role.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Not authorized"}},
            "AssumeRole",
        )
        mock_boto3.client.return_value = mock_sts
        mock_import_boto3.return_value = mock_boto3

        svc = AWSAuthService()
        with pytest.raises(Exception, match="Failed to assume IAM role"):
            svc.assume_role(role_arn="arn:aws:iam::123456789012:role/Bad")


# ── Refresh Credentials ──────────────────────────────────────────────

class TestRefreshCredentials:
    @patch("services.aws_auth_service._import_boto3")
    def test_refresh_success(self, mock_import_boto3):
        mock_boto3 = MagicMock()
        mock_oidc = MagicMock()
        mock_oidc.create_token.return_value = {
            "accessToken": "new-access-token",
            "expiresIn": 28800,
            "tokenType": "Bearer",
            "refreshToken": "new-refresh-token",
        }
        mock_boto3.client.return_value = mock_oidc
        mock_import_boto3.return_value = mock_boto3

        svc = AWSAuthService()
        result = svc.refresh_credentials(
            refresh_token="old-refresh",
            client_id="client-123",
            client_secret="secret-456",
            region="us-east-1",
        )

        assert result["access_token"] == "new-access-token"
        assert result["expires_in"] == 28800
        assert "expires_at" in result


# ── Validate Credentials ─────────────────────────────────────────────

class TestValidateCredentials:
    @patch("services.aws_auth_service._import_boto3")
    def test_valid_credentials(self, mock_import_boto3):
        mock_boto3 = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.return_value = {
            "Account": "123456789012",
            "UserId": "AIDA_TEST",
            "Arn": "arn:aws:iam::123456789012:user/test",
        }
        mock_boto3.client.return_value = mock_sts
        mock_import_boto3.return_value = mock_boto3

        svc = AWSAuthService()
        is_valid, error = svc.validate_credentials({
            "access_key_id": "AKIA_TEST",
            "secret_access_key": "secret",
            "session_token": "token",
        })
        assert is_valid is True
        assert error is None

    @patch("services.aws_auth_service._import_boto3")
    def test_invalid_credentials(self, mock_import_boto3):
        mock_boto3 = MagicMock()
        mock_sts = MagicMock()
        mock_sts.get_caller_identity.side_effect = ClientError(
            {"Error": {"Code": "InvalidClientTokenId", "Message": "Invalid token"}},
            "GetCallerIdentity",
        )
        mock_boto3.client.return_value = mock_sts
        mock_import_boto3.return_value = mock_boto3

        svc = AWSAuthService()
        is_valid, error = svc.validate_credentials({
            "access_key_id": "INVALID",
            "secret_access_key": "bad",
        })
        assert is_valid is False
        assert error is not None
