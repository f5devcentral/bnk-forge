"""
AWS mock objects for testing.

Provides mocks for boto3 clients used by:
- services/aws_auth_service.py (SSO device auth flow)
- services/credentials_service.py (STS assume-role)
- services/eks_service.py (EKS cluster operations)
"""

from unittest.mock import MagicMock


def mock_sso_oidc_client() -> MagicMock:
    """
    Mock boto3 sso-oidc client for device authorization flow.

    Simulates the 3-step SSO flow:
    1. register_client() -> clientId, clientSecret
    2. start_device_authorization() -> deviceCode, userCode, verificationUri
    3. create_token() -> accessToken (or raises AuthorizationPendingException)
    """
    client = MagicMock()

    client.register_client.return_value = {
        "clientId": "test-client-id",
        "clientSecret": "test-client-secret",
        "clientIdIssuedAt": 1700000000,
        "clientSecretExpiresAt": 1700086400,
    }

    client.start_device_authorization.return_value = {
        "deviceCode": "test-device-code",
        "userCode": "TEST-CODE",
        "verificationUri": "https://device.sso.us-east-1.amazonaws.com/",
        "verificationUriComplete": "https://device.sso.us-east-1.amazonaws.com/?user_code=TEST-CODE",
        "expiresIn": 600,
        "interval": 5,
    }

    client.create_token.return_value = {
        "accessToken": "test-access-token",
        "tokenType": "Bearer",
        "expiresIn": 28800,
        "refreshToken": "test-refresh-token",
    }

    return client


def mock_sts_client() -> MagicMock:
    """
    Mock boto3 STS client for assume-role and get-caller-identity.

    Used by credentials_service.py for role chaining.
    """
    client = MagicMock()

    client.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "ASIA_TEST_KEY_ID",
            "SecretAccessKey": "test-secret-key",
            "SessionToken": "test-session-token",
            "Expiration": "2026-12-31T23:59:59Z",
        },
        "AssumedRoleUser": {
            "AssumedRoleId": "AROA_TEST:test-session",
            "Arn": "arn:aws:sts::123456789012:assumed-role/test-role/test-session",
        },
    }

    client.get_caller_identity.return_value = {
        "Account": "123456789012",
        "UserId": "AIDA_TEST",
        "Arn": "arn:aws:iam::123456789012:user/test-user",
    }

    return client


def mock_boto3_session(
    *,
    sso_oidc: MagicMock | None = None,
    sts: MagicMock | None = None,
) -> MagicMock:
    """
    Mock boto3.Session that returns preconfigured client mocks.

    Usage:
        with patch("boto3.Session", return_value=mock_boto3_session()):
            ...
    """
    session = MagicMock()

    def _client_factory(service_name, **kwargs):
        if service_name == "sso-oidc":
            return sso_oidc or mock_sso_oidc_client()
        if service_name == "sts":
            return sts or mock_sts_client()
        # Return a generic mock for unknown services
        return MagicMock()

    session.client.side_effect = _client_factory
    return session


def mock_sso_pending_exception():
    """
    Create an exception that mimics boto3's AuthorizationPendingException.

    Usage:
        mock_oidc.create_token.side_effect = mock_sso_pending_exception()
    """
    import botocore.exceptions

    error_response = {
        "Error": {
            "Code": "AuthorizationPendingException",
            "Message": "User has not yet completed authorization",
        }
    }
    return botocore.exceptions.ClientError(error_response, "CreateToken")
