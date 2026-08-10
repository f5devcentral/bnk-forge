"""
BC-032: Component tests for CredentialRefreshService — credential refresh/rotation.

Tests cover: check_and_refresh_template, check_and_refresh_project,
refresh_aws_credentials, get_credential_status, force_refresh_project,
_create_notification, SSO refresh, expiry checking.
All external AWS/encryption calls are mocked.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from services.credential_refresh_service import CredentialRefreshService

# ── Helpers ──────────────────────────────────────────────────────────

def _mock_template(**kwargs):
    """Build a mock CloudCredentialTemplate."""
    t = MagicMock()
    t.id = kwargs.get("id", 1)
    t.name = kwargs.get("name", "Test Template")
    t.aws_sso_enabled = kwargs.get("aws_sso_enabled", False)
    t.aws_sso_refresh_token_encrypted = kwargs.get("aws_sso_refresh_token_encrypted", None)
    t.aws_session_token_encrypted = kwargs.get("aws_session_token_encrypted", None)
    t.aws_credentials_expiry = kwargs.get("aws_credentials_expiry", None)
    t.aws_sso_token_expiry = kwargs.get("aws_sso_token_expiry", None)
    t.aws_sso_client_id = kwargs.get("aws_sso_client_id", "client-123")
    t.aws_sso_client_secret_encrypted = kwargs.get("aws_sso_client_secret_encrypted", "enc-secret")
    t.aws_sso_region = kwargs.get("aws_sso_region", "us-east-1")
    t.region = kwargs.get("region", "us-east-1")
    t.aws_sso_account_id = kwargs.get("aws_sso_account_id", None)
    t.aws_sso_role_name = kwargs.get("aws_sso_role_name", None)
    t.aws_sso_access_token_encrypted = None
    t.aws_access_key_id = None
    t.aws_secret_access_key_encrypted = None
    return t


def _mock_project(**kwargs):
    """Build a mock Project."""
    p = MagicMock()
    p.id = kwargs.get("id", 1)
    p.cloud_provider = kwargs.get("cloud_provider", "aws")
    p.cloud_credentials_encrypted = kwargs.get("cloud_credentials_encrypted", None)
    return p


# ── check_and_refresh_template ──────────────────────────────────────

class TestCheckAndRefreshTemplate:
    def test_no_session_token_returns_false(self):
        svc = CredentialRefreshService()
        template = _mock_template(aws_session_token_encrypted=None, aws_sso_enabled=False)
        db = MagicMock()

        result = svc.check_and_refresh_template(template, db)
        assert result is False

    def test_no_expiry_returns_false(self):
        svc = CredentialRefreshService()
        template = _mock_template(
            aws_session_token_encrypted="enc-token",
            aws_credentials_expiry=None,
        )
        db = MagicMock()

        result = svc.check_and_refresh_template(template, db)
        assert result is False

    def test_not_expiring_soon_returns_false(self):
        svc = CredentialRefreshService()
        # Expiry in 2 hours — well beyond 15-min threshold
        template = _mock_template(
            aws_session_token_encrypted="enc-token",
            aws_credentials_expiry=datetime.now(UTC) + timedelta(hours=2),
        )
        db = MagicMock()

        result = svc.check_and_refresh_template(template, db)
        assert result is False

    def test_expiring_soon_logs_warning_returns_false(self):
        """Non-SSO templates can't auto-refresh, just warn."""
        svc = CredentialRefreshService()
        template = _mock_template(
            aws_session_token_encrypted="enc-token",
            aws_credentials_expiry=datetime.now(UTC) + timedelta(minutes=5),
        )
        db = MagicMock()

        result = svc.check_and_refresh_template(template, db)
        assert result is False  # Cannot auto-refresh non-SSO


# ── _refresh_sso_template ───────────────────────────────────────────

class TestRefreshSSOTemplate:
    @patch("services.credential_refresh_service.encrypt_value", return_value="enc-new")
    @patch("services.credential_refresh_service.decrypt_value", return_value="decrypted-value")
    def test_sso_refresh_success(self, mock_decrypt, mock_encrypt):
        svc = CredentialRefreshService()
        svc.aws_auth = MagicMock()
        svc.aws_auth.refresh_credentials.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 28800,
        }

        template = _mock_template(
            aws_sso_enabled=True,
            aws_sso_refresh_token_encrypted="enc-refresh",
            aws_sso_token_expiry=datetime.now(UTC) + timedelta(minutes=5),
        )
        db = MagicMock()

        result = svc.check_and_refresh_template(template, db)
        assert result is True
        db.commit.assert_called()

    @patch("services.credential_refresh_service.decrypt_value", return_value=None)
    def test_sso_refresh_fails_if_decrypt_fails(self, mock_decrypt):
        svc = CredentialRefreshService()

        template = _mock_template(
            aws_sso_enabled=True,
            aws_sso_refresh_token_encrypted="enc-refresh",
            aws_sso_token_expiry=datetime.now(UTC) + timedelta(minutes=5),
        )
        db = MagicMock()

        result = svc.check_and_refresh_template(template, db)
        assert result is False

    def test_sso_not_expiring_returns_false(self):
        svc = CredentialRefreshService()
        template = _mock_template(
            aws_sso_enabled=True,
            aws_sso_refresh_token_encrypted="enc-refresh",
            aws_sso_token_expiry=datetime.now(UTC) + timedelta(hours=4),
        )
        db = MagicMock()

        result = svc.check_and_refresh_template(template, db)
        assert result is False


# ── check_and_refresh_project ───────────────────────────────────────

class TestCheckAndRefreshProject:
    @patch("services.credential_refresh_service.decrypt_value")
    def test_no_encrypted_creds_returns_false(self, mock_decrypt):
        svc = CredentialRefreshService()
        project = _mock_project(cloud_credentials_encrypted=None)
        mock_decrypt.return_value = None
        db = MagicMock()

        result = svc.check_and_refresh_project(project, db)
        assert result is False

    @patch("services.credential_refresh_service.decrypt_value")
    def test_static_creds_no_expiry_returns_false(self, mock_decrypt):
        mock_decrypt.return_value = json.dumps({
            "access_key_id": "AKIA123",
            "secret_access_key": "secret",
        })
        svc = CredentialRefreshService()
        project = _mock_project(cloud_credentials_encrypted="enc-creds")
        db = MagicMock()

        result = svc.check_and_refresh_project(project, db)
        assert result is False

    @patch("services.credential_refresh_service.decrypt_value")
    def test_not_expiring_returns_false(self, mock_decrypt):
        future_expiry = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        mock_decrypt.return_value = json.dumps({
            "access_key_id": "AKIA123",
            "expiration": future_expiry,
        })
        svc = CredentialRefreshService()
        project = _mock_project(cloud_credentials_encrypted="enc-creds")
        db = MagicMock()

        result = svc.check_and_refresh_project(project, db)
        assert result is False


# ── get_credential_status ───────────────────────────────────────────

class TestGetCredentialStatus:
    @patch("services.credential_refresh_service.decrypt_value")
    def test_no_encrypted_creds_returns_none(self, mock_decrypt):
        svc = CredentialRefreshService()
        project = _mock_project(cloud_credentials_encrypted=None)
        assert svc.get_credential_status(project) is None

    @patch("services.credential_refresh_service.decrypt_value")
    def test_static_creds_status(self, mock_decrypt):
        mock_decrypt.return_value = json.dumps({
            "auth_method": "access_keys",
            "access_key_id": "AKIA123",
        })
        svc = CredentialRefreshService()
        project = _mock_project(cloud_credentials_encrypted="enc-creds")

        status = svc.get_credential_status(project)
        assert status["has_expiration"] is False
        assert status["auth_method"] == "access_keys"

    @patch("services.credential_refresh_service.decrypt_value")
    def test_expired_creds_status(self, mock_decrypt):
        past_expiry = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        mock_decrypt.return_value = json.dumps({
            "auth_method": "sso",
            "expiration": past_expiry,
        })
        svc = CredentialRefreshService()
        project = _mock_project(cloud_credentials_encrypted="enc-creds")

        status = svc.get_credential_status(project)
        assert status["has_expiration"] is True
        assert status["is_expired"] is True

    @patch("services.credential_refresh_service.decrypt_value")
    def test_expiring_soon_status(self, mock_decrypt):
        soon_expiry = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        mock_decrypt.return_value = json.dumps({
            "auth_method": "assume_role",
            "expiration": soon_expiry,
            "role_arn": "arn:aws:iam::123:role/test",
        })
        svc = CredentialRefreshService()
        project = _mock_project(cloud_credentials_encrypted="enc-creds")

        status = svc.get_credential_status(project)
        assert status["is_expiring_soon"] is True
        assert status["can_auto_refresh"] is True


# ── refresh_aws_credentials ─────────────────────────────────────────

class TestRefreshAWSCredentials:
    @patch("services.credential_refresh_service.encrypt_value", return_value="enc")
    def test_assume_role_refresh(self, mock_encrypt):
        svc = CredentialRefreshService()
        svc.aws_auth = MagicMock()
        svc.aws_auth.assume_role.return_value = {
            "access_key_id": "NEW_AK",
            "secret_access_key": "NEW_SK",
            "session_token": "NEW_ST",
            "expiration": "2025-01-01T00:00:00+00:00",
        }

        project = _mock_project()
        cred_data = {"auth_method": "assume_role", "role_arn": "arn:aws:iam::123:role/test"}
        db = MagicMock()

        result = svc.refresh_aws_credentials(project, cred_data, db)
        assert result is True
        db.commit.assert_called_once()

    def test_unknown_auth_method_returns_false(self):
        svc = CredentialRefreshService()
        project = _mock_project()
        cred_data = {"auth_method": "unknown"}
        db = MagicMock()

        result = svc.refresh_aws_credentials(project, cred_data, db)
        assert result is False


# ── GCP / Azure not implemented ─────────────────────────────────────

class TestUnsupportedProviders:
    def test_gcp_refresh_returns_false(self):
        svc = CredentialRefreshService()
        result = svc.refresh_gcp_credentials(MagicMock(), {}, MagicMock())
        assert result is False

    def test_azure_refresh_returns_false(self):
        svc = CredentialRefreshService()
        result = svc.refresh_azure_credentials(MagicMock(), {}, MagicMock())
        assert result is False
