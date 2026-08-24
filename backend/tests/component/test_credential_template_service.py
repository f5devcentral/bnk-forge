"""
Tests for services.credential_template_service — credential template CRUD + SSO + testing.

BC-012: Credential template management — real DB + encryption, mock AWS/SSH externals.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from botocore.exceptions import ClientError, ReadTimeoutError

from core.errors import BadRequestError, InternalError, NotFoundError
from models import CloudCredentialTemplate
from services.credential_template_service import CredentialTemplateService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_template_data(**overrides):
    """Build a SimpleNamespace mimicking the Pydantic request model."""
    defaults = {
        "name": "aws-prod",
        "description": "Production AWS credentials",
        "provider": "aws",
        "aws_auth_method": "access_keys",
        "aws_profile": None,
        "region": "us-east-1",
        "aws_access_key_id": "AKIATEST123",
        "aws_secret_access_key": "secret-key-value",
        "aws_session_token": None,
        "aws_sso_enabled": False,
        "aws_sso_start_url": None,
        "aws_sso_region": None,
        "aws_sso_account_id": None,
        "aws_sso_role_name": None,
        "gcp_project_id": None,
        "gcp_credentials": None,
        "azure_subscription_id": None,
        "azure_tenant_id": None,
        "azure_credentials": None,
        "ibmcloud_api_key": None,
        "ibmcloud_resource_group": "default",
        "ssh_host": None,
        "ssh_port": None,
        "ssh_username": None,
        "ssh_auth_type": None,
        "ssh_password": None,
        "ssh_private_key": None,
        "ssh_key_passphrase": None,
        "is_default": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_update_data(**overrides):
    """Build a SimpleNamespace mimicking the Pydantic update model with model_dump."""
    defaults = {
        "name": None,
        "description": None,
        "provider": None,
        "aws_auth_method": None,
        "aws_profile": None,
        "region": None,
        "aws_access_key_id": None,
        "aws_secret_access_key": None,
        "aws_session_token": None,
        "is_default": None,
        "ssh_host": None,
        "ssh_port": None,
        "ssh_username": None,
        "ssh_auth_type": None,
        "ssh_password": None,
        "ssh_private_key": None,
        "ssh_key_passphrase": None,
        "gcp_credentials": None,
        "azure_credentials": None,
        "ibmcloud_api_key": None,
        "ibmcloud_resource_group": None,
    }
    defaults.update(overrides)
    ns = SimpleNamespace(**defaults)
    # Mimic model_dump(exclude_unset=True) — return only keys that were explicitly set
    ns.model_dump = lambda exclude_unset=False: {
        k: v for k, v in overrides.items()
    } if exclude_unset else defaults
    return ns


def _create_template_in_db(db, **overrides):
    """Directly create a CloudCredentialTemplate in DB for read/update/delete tests."""
    from core.encryption import encrypt_value
    defaults = {
        "name": "test-template",
        "provider": "aws",
        "aws_auth_method": "access_keys",
        "region": "us-east-1",
        "aws_access_key_id": "AKIATEST123",
    }
    defaults.update(overrides)

    # Handle encryption of secret fields
    secret = defaults.pop("_secret_key", None)
    ibm_api_key = defaults.pop("_ibm_api_key", None)
    template = CloudCredentialTemplate(**defaults)
    if secret:
        template.aws_secret_access_key_encrypted = encrypt_value(secret)
    if ibm_api_key:
        template.ibmcloud_api_key_encrypted = encrypt_value(ibm_api_key)

    db.add(template)
    db.flush()
    db.refresh(template)
    return template


# ---------------------------------------------------------------------------
# create_template
# ---------------------------------------------------------------------------

class TestCreateTemplate:
    """Create credential templates with field encryption."""

    @patch("services.credential_template_service.get_default", return_value="us-east-1")
    def test_creates_aws_template(self, mock_get_default, db):
        svc = CredentialTemplateService(db)
        result = svc.create_template(_make_template_data())
        assert result["name"] == "aws-prod"
        assert result["provider"] == "aws"
        assert result["aws_access_key_id"] == "AKIATEST123"
        assert result["has_aws_secret_access_key"] is True
        assert result["id"] is not None

    @patch("services.credential_template_service.get_default", return_value="us-east-1")
    def test_encrypts_secret_key(self, mock_get_default, db):
        svc = CredentialTemplateService(db)
        result = svc.create_template(_make_template_data())
        # Verify the encrypted field is not plaintext
        template = db.query(CloudCredentialTemplate).filter(CloudCredentialTemplate.id == result["id"]).first()
        assert template.aws_secret_access_key_encrypted is not None
        assert "secret-key-value" not in template.aws_secret_access_key_encrypted

    @patch("services.credential_template_service.get_default", return_value="us-east-1")
    def test_duplicate_name_raises(self, mock_get_default, db):
        svc = CredentialTemplateService(db)
        svc.create_template(_make_template_data(name="dup"))
        with pytest.raises(BadRequestError, match="already exists"):
            svc.create_template(_make_template_data(name="dup"))

    @patch("services.credential_template_service.get_default", return_value="us-west-2")
    def test_uses_default_region_when_not_provided(self, mock_get_default, db):
        svc = CredentialTemplateService(db)
        result = svc.create_template(_make_template_data(region=None))
        assert result["region"] == "us-west-2"

    @patch("services.credential_template_service.get_default", return_value="us-east-1")
    def test_sets_default_flag_and_clears_others(self, mock_get_default, db):
        svc = CredentialTemplateService(db)
        svc.create_template(_make_template_data(name="first", is_default=True))
        svc.create_template(_make_template_data(name="second", is_default=True))
        db.flush()
        # The first template should no longer be default
        first = db.query(CloudCredentialTemplate).filter(CloudCredentialTemplate.name == "first").first()
        second = db.query(CloudCredentialTemplate).filter(CloudCredentialTemplate.name == "second").first()
        assert first.is_default is False
        assert second.is_default is True

    @patch("services.credential_template_service.get_default", return_value="us-east-1")
    def test_creates_ssh_template(self, mock_get_default, db):
        svc = CredentialTemplateService(db)
        result = svc.create_template(_make_template_data(
            name="ssh-cred",
            provider="ssh",
            aws_auth_method=None,
            aws_access_key_id=None,
            aws_secret_access_key=None,
            ssh_host="10.0.0.1",
            ssh_port=22,
            ssh_username="admin",
            ssh_auth_type="password",
            ssh_password="s3cret",
        ))
        assert result["provider"] == "ssh"
        assert result["has_ssh_password"] is True
        assert result["ssh_host"] == "10.0.0.1"

    def test_creates_ibm_template(self, db):
        svc = CredentialTemplateService(db)
        result = svc.create_template(_make_template_data(
            name="ibm-prod",
            description="Production IBM Cloud credentials",
            provider="ibm",
            aws_auth_method=None,
            region="us-south",
            aws_access_key_id=None,
            aws_secret_access_key=None,
            ibmcloud_api_key="ibm-api-key-value",
            ibmcloud_resource_group="team-rg",
        ))
        assert result["name"] == "ibm-prod"
        assert result["provider"] == "ibm"
        assert result["region"] == "us-south"
        assert result["has_ibmcloud_api_key"] is True
        assert result["ibmcloud_resource_group"] == "team-rg"

    def test_encrypts_ibm_api_key(self, db):
        svc = CredentialTemplateService(db)
        result = svc.create_template(_make_template_data(
            name="ibm-encrypted",
            provider="ibm",
            aws_auth_method=None,
            region="us-south",
            aws_access_key_id=None,
            aws_secret_access_key=None,
            ibmcloud_api_key="ibm-api-key-value",
        ))
        template = db.query(CloudCredentialTemplate).filter(CloudCredentialTemplate.id == result["id"]).first()
        assert template.ibmcloud_api_key_encrypted is not None
        assert "ibm-api-key-value" not in template.ibmcloud_api_key_encrypted

    def test_ibm_resource_group_defaults_to_default(self, db):
        svc = CredentialTemplateService(db)
        result = svc.create_template(_make_template_data(
            name="ibm-default-rg",
            provider="ibm",
            aws_auth_method=None,
            region="us-south",
            aws_access_key_id=None,
            aws_secret_access_key=None,
            ibmcloud_api_key="ibm-api-key-value",
            ibmcloud_resource_group=None,
        ))
        assert result["ibmcloud_resource_group"] == "default"


# ---------------------------------------------------------------------------
# provider validation (issue #191)
# ---------------------------------------------------------------------------

class TestProviderValidation:
    """A credential template's provider must match a real injection path.

    Regression guard for issue #191: ``provider="ibmcloud"`` (a misspelling of
    the canonical ``ibm``) used to be stored happily and then inject NO
    credentials at deploy time — silently. Every provider the service accepts
    must be one a credential-resolution consumer actually handles.
    """

    @patch("services.credential_template_service.get_default", return_value="us-east-1")
    def test_create_rejects_misspelled_ibmcloud_provider(self, mock_get_default, db):
        """The exact bug from #191: 'ibmcloud' looks right but injects nothing."""
        svc = CredentialTemplateService(db)
        with pytest.raises(BadRequestError, match="Unsupported credential-template provider 'ibmcloud'"):
            svc.create_template(_make_template_data(name="ibm-roks", provider="ibmcloud"))
        # And nothing was persisted.
        assert db.query(CloudCredentialTemplate).filter(
            CloudCredentialTemplate.name == "ibm-roks"
        ).first() is None

    @patch("services.credential_template_service.get_default", return_value="us-east-1")
    def test_create_rejects_unknown_provider_with_enumerated_message(self, mock_get_default, db):
        svc = CredentialTemplateService(db)
        with pytest.raises(BadRequestError) as exc:
            svc.create_template(_make_template_data(name="bogus", provider="digitalocean"))
        msg = str(exc.value)
        # Message names the offender and enumerates the supported set.
        assert "digitalocean" in msg
        for supported in ("aws", "azure", "gcp", "ibm", "ssh"):
            assert supported in msg

    @patch("services.credential_template_service.get_default", return_value="us-east-1")
    def test_create_rejects_empty_provider(self, mock_get_default, db):
        svc = CredentialTemplateService(db)
        with pytest.raises(BadRequestError):
            svc.create_template(_make_template_data(name="empty-prov", provider=""))

    @pytest.mark.parametrize("provider", ["aws", "gcp", "azure", "ibm", "ssh"])
    @patch("services.credential_template_service.get_default", return_value="us-east-1")
    def test_create_accepts_every_supported_provider(self, mock_get_default, provider, db):
        """Each canonical provider is accepted — validation matches the injection set."""
        svc = CredentialTemplateService(db)
        result = svc.create_template(_make_template_data(
            name=f"tpl-{provider}",
            provider=provider,
            # give IBM its required key; other providers don't need extra fields here
            ibmcloud_api_key="ibm-api-key-value" if provider == "ibm" else None,
        ))
        assert result["provider"] == provider

    def test_update_rejects_switch_to_unknown_provider(self, db):
        """An update can't move a healthy template onto a no-op provider."""
        t = _create_template_in_db(db, name="aws-live", provider="aws")
        svc = CredentialTemplateService(db)
        with pytest.raises(BadRequestError, match="Unsupported credential-template provider 'ibmcloud'"):
            svc.update_template(t.id, _make_update_data(provider="ibmcloud"))
        # Provider unchanged.
        db.refresh(t)
        assert t.provider == "aws"

    def test_update_without_provider_change_is_allowed(self, db):
        """Omitting provider on update leaves it untouched (no false rejection)."""
        t = _create_template_in_db(db, name="aws-keep", provider="aws")
        svc = CredentialTemplateService(db)
        result = svc.update_template(t.id, _make_update_data(description="just a note"))
        assert result["provider"] == "aws"
        assert result["description"] == "just a note"


# ---------------------------------------------------------------------------
# list_templates / get_template
# ---------------------------------------------------------------------------

class TestListAndGetTemplates:
    """Template retrieval operations."""

    def test_list_empty(self, db):
        svc = CredentialTemplateService(db)
        result = svc.list_templates()
        assert result == []

    def test_list_all(self, db):
        _create_template_in_db(db, name="t1")
        _create_template_in_db(db, name="t2")
        svc = CredentialTemplateService(db)
        result = svc.list_templates()
        assert len(result) == 2

    def test_list_filtered_by_provider(self, db):
        _create_template_in_db(db, name="aws-cred", provider="aws")
        _create_template_in_db(db, name="ssh-cred", provider="ssh")
        svc = CredentialTemplateService(db)
        result = svc.list_templates(provider="ssh")
        assert len(result) == 1
        assert result[0]["provider"] == "ssh"

    def test_list_filtered_by_provider_ibm(self, db):
        _create_template_in_db(db, name="aws-cred", provider="aws")
        _create_template_in_db(db, name="ibm-cred", provider="ibm", _ibm_api_key="ibm-key")
        svc = CredentialTemplateService(db)
        result = svc.list_templates(provider="ibm")
        assert len(result) == 1
        assert result[0]["provider"] == "ibm"
        assert result[0]["has_ibmcloud_api_key"] is True

    def test_get_existing(self, db):
        t = _create_template_in_db(db, name="get-me")
        svc = CredentialTemplateService(db)
        result = svc.get_template(t.id)
        assert result["name"] == "get-me"

    def test_get_nonexistent_raises(self, db):
        svc = CredentialTemplateService(db)
        with pytest.raises(NotFoundError):
            svc.get_template(99999)


# ---------------------------------------------------------------------------
# update_template
# ---------------------------------------------------------------------------

class TestUpdateTemplate:
    """Update credential templates."""

    def test_update_name(self, db):
        t = _create_template_in_db(db, name="old-name")
        svc = CredentialTemplateService(db)
        result = svc.update_template(t.id, _make_update_data(name="new-name"))
        assert result["name"] == "new-name"

    def test_update_duplicate_name_raises(self, db):
        _create_template_in_db(db, name="existing")
        t2 = _create_template_in_db(db, name="to-rename")
        svc = CredentialTemplateService(db)
        with pytest.raises(BadRequestError, match="already exists"):
            svc.update_template(t2.id, _make_update_data(name="existing"))

    def test_update_secret_encrypts(self, db):
        t = _create_template_in_db(db, name="update-secret")
        svc = CredentialTemplateService(db)
        svc.update_template(t.id, _make_update_data(aws_secret_access_key="new-secret"))
        db.refresh(t)
        assert t.aws_secret_access_key_encrypted is not None

    def test_update_region(self, db):
        t = _create_template_in_db(db, name="update-region")
        svc = CredentialTemplateService(db)
        result = svc.update_template(t.id, _make_update_data(region="eu-west-1"))
        assert result["region"] == "eu-west-1"

    def test_update_ibm_api_key_encrypts(self, db):
        t = _create_template_in_db(db, name="update-ibm", provider="ibm")
        svc = CredentialTemplateService(db)
        svc.update_template(t.id, _make_update_data(ibmcloud_api_key="new-ibm-secret"))
        db.refresh(t)
        assert t.ibmcloud_api_key_encrypted is not None

    def test_update_ibm_blank_key_preserves_existing(self, db):
        t = _create_template_in_db(db, name="update-ibm-preserve", provider="ibm", _ibm_api_key="existing-ibm-secret")
        original = t.ibmcloud_api_key_encrypted
        svc = CredentialTemplateService(db)
        svc.update_template(t.id, _make_update_data(ibmcloud_api_key=""))
        db.refresh(t)
        assert t.ibmcloud_api_key_encrypted == original

    def test_update_ibm_resource_group(self, db):
        t = _create_template_in_db(db, name="update-ibm-rg", provider="ibm", _ibm_api_key="existing-ibm-secret")
        svc = CredentialTemplateService(db)
        result = svc.update_template(t.id, _make_update_data(ibmcloud_resource_group="platform-rg"))
        assert result["ibmcloud_resource_group"] == "platform-rg"

    def test_update_nonexistent_raises(self, db):
        svc = CredentialTemplateService(db)
        with pytest.raises(NotFoundError):
            svc.update_template(99999, _make_update_data(name="x"))

    def test_update_sets_default_clears_others(self, db):
        t1 = _create_template_in_db(db, name="default-t1", is_default=True)
        t2 = _create_template_in_db(db, name="default-t2", is_default=False)
        svc = CredentialTemplateService(db)
        svc.update_template(t2.id, _make_update_data(is_default=True))
        db.refresh(t1)
        assert t1.is_default is False


# ---------------------------------------------------------------------------
# delete_template
# ---------------------------------------------------------------------------

class TestDeleteTemplate:
    """Template deletion with project-usage guard."""

    def test_delete_removes_template(self, db):
        t = _create_template_in_db(db, name="to-delete")
        svc = CredentialTemplateService(db)
        svc.delete_template(t.id)
        db.flush()
        assert db.query(CloudCredentialTemplate).filter(CloudCredentialTemplate.id == t.id).first() is None

    def test_delete_nonexistent_raises(self, db):
        svc = CredentialTemplateService(db)
        with pytest.raises(NotFoundError):
            svc.delete_template(99999)

    def test_delete_in_use_raises(self, db, make_project):
        t = _create_template_in_db(db, name="in-use")
        project = make_project(credential_template_id=t.id)
        svc = CredentialTemplateService(db)
        with pytest.raises(BadRequestError, match="Cannot delete"):
            svc.delete_template(t.id)


# ---------------------------------------------------------------------------
# test_template (AWS)
# ---------------------------------------------------------------------------

class TestTestTemplateAWS:
    """AWS credential testing via STS (boto3)."""

    @patch("boto3.Session")
    def test_valid_credentials(self, mock_session, db):
        t = _create_template_in_db(db, name="test-aws", _secret_key="mysecret")
        sts = MagicMock()
        sts.get_caller_identity.return_value = {
            "Account": "123456789012",
            "UserId": "AIDATEST",
            "Arn": "arn:aws:iam::123:user/test",
        }
        mock_session.return_value.client.return_value = sts
        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is True
        assert result["account_id"] == "123456789012"

    @patch("boto3.Session")
    def test_expired_token(self, mock_session, db):
        t = _create_template_in_db(db, name="test-expired", _secret_key="mysecret")
        sts = MagicMock()
        sts.get_caller_identity.side_effect = ClientError(
            {"Error": {"Code": "ExpiredToken", "Message": "Token expired"}},
            "GetCallerIdentity",
        )
        mock_session.return_value.client.return_value = sts
        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is False
        assert "expired" in result["error"].lower()

    @patch("boto3.Session")
    def test_invalid_key(self, mock_session, db):
        t = _create_template_in_db(db, name="test-invalid", _secret_key="mysecret")
        sts = MagicMock()
        sts.get_caller_identity.side_effect = ClientError(
            {"Error": {"Code": "InvalidClientTokenId", "Message": "Bad key"}},
            "GetCallerIdentity",
        )
        mock_session.return_value.client.return_value = sts
        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is False
        assert "Invalid credentials" in result["error"]

    @patch("boto3.Session")
    def test_timeout(self, mock_session, db):
        t = _create_template_in_db(db, name="test-timeout", _secret_key="mysecret")
        sts = MagicMock()
        sts.get_caller_identity.side_effect = ReadTimeoutError(endpoint_url="https://sts.amazonaws.com")
        mock_session.return_value.client.return_value = sts
        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is False
        assert "timed out" in result["error"].lower()

    def test_no_access_key_returns_error(self, db):
        t = _create_template_in_db(db, name="no-key", aws_access_key_id=None)
        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is False
        assert "No access key" in result["error"]

    def test_profile_no_profile_returns_error(self, db):
        t = _create_template_in_db(db, name="no-profile", aws_auth_method="profile", aws_profile=None)
        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is False
        assert "No AWS profile" in result["error"]

    def test_unsupported_provider_raises(self, db):
        t = _create_template_in_db(db, name="gcp-cred", provider="gcp")
        svc = CredentialTemplateService(db)
        with pytest.raises(BadRequestError, match="only supported for AWS and SSH"):
            svc.test_template(t.id)

class TestTestTemplateIBM:
    """IBM Cloud API key testing via IAM token exchange."""

    @patch("services.credential_template_service.requests.post")
    def test_valid_ibm_api_key(self, mock_post, db):
        t = _create_template_in_db(db, name="ibm-valid", provider="ibm", _ibm_api_key="ibm-key")
        mock_post.return_value = MagicMock(
            ok=True,
            json=lambda: {"access_token": "token", "iam_id": "IBMid-123", "expires_in": 3600},
        )

        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is True
        assert result["iam_id"] == "IBMid-123"
        assert result["expires_in"] == 3600

    @patch("services.credential_template_service.requests.post")
    def test_invalid_ibm_api_key(self, mock_post, db):
        t = _create_template_in_db(db, name="ibm-invalid", provider="ibm", _ibm_api_key="ibm-key")
        mock_post.return_value = MagicMock(
            ok=False,
            status_code=401,
            text='{"errorCode":"BXNIM0415E"}',
            json=lambda: {"errorCode": "BXNIM0415E", "error_description": "Provided API key could not be found"},
        )

        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is False
        assert result["error"] == "Invalid IBM Cloud API key"

    def test_missing_ibm_api_key_returns_error(self, db):
        t = _create_template_in_db(db, name="ibm-missing", provider="ibm")
        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is False
        assert "No IBM Cloud API key" in result["error"]

    @patch("services.credential_template_service.requests.post")
    def test_ibm_timeout(self, mock_post, db):
        t = _create_template_in_db(db, name="ibm-timeout", provider="ibm", _ibm_api_key="ibm-key")
        mock_post.side_effect = requests.Timeout()

        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is False
        assert "timed out" in result["error"].lower()


# ---------------------------------------------------------------------------
# test_template (SSH)
# ---------------------------------------------------------------------------

class TestTestTemplateSSH:
    """SSH credential testing."""

    @patch("services.ssh_tunnel_manager.get_tunnel_manager")
    def test_ssh_test_delegates(self, mock_get_mgr, db):
        t = _create_template_in_db(db, name="ssh-test", provider="ssh", ssh_host="10.0.0.1",
                                    ssh_port=22, ssh_username="admin", ssh_auth_type="password")
        mock_mgr = MagicMock()
        mock_mgr.test_ssh_connection.return_value = {"success": True, "message": "Connected"}
        mock_get_mgr.return_value = mock_mgr

        svc = CredentialTemplateService(db)
        result = svc.test_template(t.id)
        assert result["success"] is True
        mock_mgr.test_ssh_connection.assert_called_once()


# ---------------------------------------------------------------------------
# initiate_sso
# ---------------------------------------------------------------------------

_SSO_DEVICE_AUTH_RESULT = {
    "client_id": "c-123",
    "client_secret": "s-456",
    "user_code": "ABCD-EFGH",
    "verification_uri": "https://device.sso.example.com/",
    "verification_uri_complete": "https://device.sso.example.com/?user_code=ABCD-EFGH",
    "expires_in": 600,
    "interval": 5,
    "device_code": "dc-789",
}


class TestInitiateSSO:
    """AWS SSO device code flow initiation."""

    @patch("services.credential_template_service.AWSAuthService")
    def test_initiate_success(self, mock_aws, db):
        t = _create_template_in_db(db, name="sso-template", aws_sso_enabled=True,
                                    aws_sso_start_url="https://start.awsapps.com/start",
                                    aws_sso_region="us-east-1",
                                    aws_sso_account_id="123456789012",
                                    aws_sso_role_name="AdministratorAccess")
        mock_auth = MagicMock()
        mock_auth.initiate_device_authorization.return_value = _SSO_DEVICE_AUTH_RESULT
        mock_aws.return_value = mock_auth

        svc = CredentialTemplateService(db)
        result = svc.initiate_sso(t.id)
        assert result["success"] is True
        assert result["data"]["user_code"] == "ABCD-EFGH"

    @patch("services.credential_template_service.AWSAuthService")
    def test_initiate_allowed_when_sso_disabled_but_config_complete(self, mock_aws, db):
        """Templates with aws_sso_enabled=False but complete SSO config can still initiate.

        This covers the regression introduced by the token-refresh PR that changed
        auth_method to 'access_keys' / aws_sso_enabled=False while preserving SSO
        config fields and previous authenticated_at/token_expiry.
        """
        t = _create_template_in_db(
            db, name="sso-disabled-complete-config",
            aws_sso_enabled=False,
            aws_auth_method="access_keys",
            aws_sso_start_url="https://start.awsapps.com/start",
            aws_sso_region="us-east-1",
            aws_sso_account_id="123456789012",
            aws_sso_role_name="AdministratorAccess",
        )
        mock_auth = MagicMock()
        mock_auth.initiate_device_authorization.return_value = _SSO_DEVICE_AUTH_RESULT
        mock_aws.return_value = mock_auth

        svc = CredentialTemplateService(db)
        result = svc.initiate_sso(t.id)
        assert result["success"] is True
        assert result["data"]["user_code"] == "ABCD-EFGH"
        mock_auth.initiate_device_authorization.assert_called_once_with(
            start_url="https://start.awsapps.com/start",
            region="us-east-1",
        )

    def test_sso_missing_config_raises(self, db):
        """Templates without SSO config fields are rejected regardless of aws_sso_enabled."""
        t = _create_template_in_db(db, name="incomplete-sso", aws_sso_enabled=False,
                                    aws_sso_start_url=None, aws_sso_region=None)
        svc = CredentialTemplateService(db)
        with pytest.raises(BadRequestError, match="incomplete"):
            svc.initiate_sso(t.id)

    def test_sso_enabled_but_partial_config_raises(self, db):
        """aws_sso_enabled=True with only start_url (missing account_id/role_name) is rejected."""
        t = _create_template_in_db(db, name="partial-sso", aws_sso_enabled=True,
                                    aws_sso_start_url="https://start.awsapps.com/start",
                                    aws_sso_region="us-east-1",
                                    aws_sso_account_id=None, aws_sso_role_name=None)
        svc = CredentialTemplateService(db)
        with pytest.raises(BadRequestError, match="incomplete"):
            sso = svc.initiate_sso(t.id)


# ---------------------------------------------------------------------------
# get_sso_status
# ---------------------------------------------------------------------------

class TestGetSSOStatus:
    """SSO authentication status checking."""

    def test_sso_disabled(self, db):
        t = _create_template_in_db(db, name="no-sso-status", aws_sso_enabled=False)
        svc = CredentialTemplateService(db)
        result = svc.get_sso_status(t.id)
        assert result["sso_enabled"] is False

    def test_sso_authenticated(self, db):
        # SQLite stores naive datetimes; service compares with datetime.now(UTC)
        # Use naive UTC times to avoid offset-naive/aware comparison errors
        now = datetime.utcnow()
        t = _create_template_in_db(
            db, name="sso-authed", aws_sso_enabled=True,
            aws_sso_authenticated_at=now,
            aws_sso_token_expiry=now + timedelta(hours=8),
            aws_access_key_id="AKIATEST",
        )
        svc = CredentialTemplateService(db)
        # Patch datetime.now(UTC) to return naive datetime for comparison compatibility
        with patch("services.credential_template_service.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = svc.get_sso_status(t.id)
        assert result["sso_enabled"] is True
        assert result["is_authenticated"] is True
        assert result["token_expired"] is False
        assert result["has_credentials"] is True

    def test_sso_expired(self, db):
        past = datetime.utcnow() - timedelta(hours=24)
        t = _create_template_in_db(
            db, name="sso-expired", aws_sso_enabled=True,
            aws_sso_authenticated_at=past,
            aws_sso_token_expiry=past + timedelta(hours=8),
            aws_credentials_expiry=past + timedelta(hours=1),
        )
        svc = CredentialTemplateService(db)
        now = datetime.utcnow()
        with patch("services.credential_template_service.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = svc.get_sso_status(t.id)
        assert result["token_expired"] is True
        assert result["credentials_expired"] is True
        assert result["needs_reauth"] is True


# ---------------------------------------------------------------------------
# serialize_template (static method, pure function)
# ---------------------------------------------------------------------------

class TestSerializeTemplate:
    """Serialization output shape."""

    def test_serializes_all_fields(self, db):
        t = _create_template_in_db(db, name="serialize-test", provider="aws")
        result = CredentialTemplateService.serialize_template(t)
        assert result["id"] == t.id
        assert result["name"] == "serialize-test"
        assert result["provider"] == "aws"
        assert "has_aws_secret_access_key" in result
        assert "has_ibmcloud_api_key" in result
        assert "has_ssh_key" in result
        assert "projects_count" in result

    def test_sensitive_fields_masked(self, db):
        from core.encryption import encrypt_value
        t = _create_template_in_db(db, name="masked-test")
        t.aws_secret_access_key_encrypted = encrypt_value("my-secret")
        db.flush()
        result = CredentialTemplateService.serialize_template(t)
        # Should show boolean, not actual secret
        assert result["has_aws_secret_access_key"] is True
        assert "my-secret" not in str(result)

    def test_ibm_sensitive_fields_masked(self, db):
        t = _create_template_in_db(db, name="masked-ibm", provider="ibm", _ibm_api_key="ibm-secret")
        result = CredentialTemplateService.serialize_template(t)
        assert result["has_ibmcloud_api_key"] is True
        assert "ibm-secret" not in str(result)


# ---------------------------------------------------------------------------
# Helper: create template with SSO tokens pre-set
# ---------------------------------------------------------------------------

def _create_sso_template(db, name, **overrides):
    """Create a CloudCredentialTemplate with encrypted SSO token fields ready for poll/refresh tests."""
    from core.encryption import encrypt_value
    t = _create_template_in_db(
        db, name=name,
        aws_sso_enabled=True,
        aws_sso_start_url="https://start.awsapps.com/start",
        aws_sso_region="us-east-1",
        aws_sso_account_id="123456789012",
        aws_sso_role_name="AdministratorAccess",
        **overrides,
    )
    t.aws_sso_client_id = "client-id-abc"
    t.aws_sso_client_secret_encrypted = encrypt_value("client-secret-xyz")
    t.aws_sso_refresh_token_encrypted = encrypt_value("refresh-token-abc")
    db.flush()
    db.refresh(t)
    return t


# ---------------------------------------------------------------------------
# poll_sso — observation clearing
# ---------------------------------------------------------------------------

class TestPollSSO:
    """poll_sso clears stale failed-observation on SSO token success."""

    @patch("services.credential_template_service.AWSAuthService")
    def test_poll_sso_clears_stale_error_on_success(self, mock_aws_cls, db):
        """A template with a pre-existing failed observation is cleared after a successful SSO poll."""
        stale_time = datetime.now(UTC) - timedelta(hours=2)
        t = _create_sso_template(db, name="poll-clear-error")
        t.last_error_at = stale_time
        t.last_error_code = "ExpiredToken"
        t.last_error_message = "Credentials have expired"
        t.last_successful_call_at = None
        db.flush()

        mock_auth = MagicMock()
        mock_auth.poll_for_token.return_value = {
            "access_token": "access-tok",
            "expires_in": 28800,
        }
        mock_auth.get_account_credentials.return_value = {
            "access_key_id": "AKIATEST",
            "secret_access_key": "secret",
            "session_token": "session",
            "expiration": (datetime.now(UTC) + timedelta(hours=8)).isoformat(),
        }
        mock_aws_cls.return_value = mock_auth

        svc = CredentialTemplateService(db)
        result = svc.poll_sso(t.id, "device-code-xyz")

        assert result["success"] is True
        db.refresh(t)
        assert t.last_error_at is None
        assert t.last_error_code is None
        assert t.last_error_message is None
        assert t.last_successful_call_at is not None

    @patch("services.credential_template_service.AWSAuthService")
    def test_poll_sso_sets_last_successful_call_at(self, mock_aws_cls, db):
        """last_successful_call_at is set to approximately now after a successful SSO poll."""
        before = datetime.now(UTC)
        t = _create_sso_template(db, name="poll-sets-success-ts")

        mock_auth = MagicMock()
        mock_auth.poll_for_token.return_value = {"access_token": "tok", "expires_in": 28800}
        mock_auth.get_account_credentials.return_value = {
            "access_key_id": "AKIATEST",
            "secret_access_key": "secret",
            "session_token": "session",
            "expiration": (datetime.now(UTC) + timedelta(hours=8)).isoformat(),
        }
        mock_aws_cls.return_value = mock_auth

        svc = CredentialTemplateService(db)
        svc.poll_sso(t.id, "device-code-xyz")

        db.refresh(t)
        assert t.last_successful_call_at is not None
        # Strip tzinfo for naive-SQLite comparison tolerance
        ts = t.last_successful_call_at
        if ts.tzinfo is None:
            from datetime import timezone
            ts = ts.replace(tzinfo=UTC)
        assert ts >= before

    @patch("services.credential_template_service.AWSAuthService")
    def test_poll_sso_pending_does_not_clear_error(self, mock_aws_cls, db):
        """A pending (202) poll response leaves the stale error intact."""
        stale_time = datetime.now(UTC) - timedelta(hours=1)
        t = _create_sso_template(db, name="poll-pending-keeps-error")
        t.last_error_at = stale_time
        t.last_error_code = "ExpiredToken"
        db.flush()

        mock_auth = MagicMock()
        mock_auth.poll_for_token.side_effect = Exception("Authorization pending")
        mock_aws_cls.return_value = mock_auth

        svc = CredentialTemplateService(db)
        result = svc.poll_sso(t.id, "device-code-xyz")

        assert result.get("pending") is True
        db.refresh(t)
        # Observation NOT touched — still stale
        assert t.last_error_code == "ExpiredToken"


# ---------------------------------------------------------------------------
# refresh_sso — observation clearing
# ---------------------------------------------------------------------------

class TestRefreshSSO:
    """refresh_sso clears stale failed-observation on successful credential refresh."""

    @patch("services.credential_template_service.AWSAuthService")
    def test_refresh_sso_clears_stale_error_on_success(self, mock_aws_cls, db):
        """A template with a pre-existing failed observation is cleared after a successful refresh."""
        stale_time = datetime.now(UTC) - timedelta(hours=3)
        t = _create_sso_template(db, name="refresh-clear-error")
        t.last_error_at = stale_time
        t.last_error_code = "ExpiredToken"
        t.last_error_message = "Credentials have expired"
        t.last_successful_call_at = None
        db.flush()

        mock_auth = MagicMock()
        mock_auth.refresh_credentials.return_value = {
            "access_token": "new-access-tok",
            "expires_in": 28800,
        }
        mock_auth.get_account_credentials.return_value = {
            "access_key_id": "AKIATEST",
            "secret_access_key": "secret",
            "session_token": "session",
            "expiration": (datetime.now(UTC) + timedelta(hours=8)).isoformat(),
        }
        mock_aws_cls.return_value = mock_auth

        svc = CredentialTemplateService(db)
        result = svc.refresh_sso(t.id)

        assert result["success"] is True
        db.refresh(t)
        assert t.last_error_at is None
        assert t.last_error_code is None
        assert t.last_error_message is None
        assert t.last_successful_call_at is not None

    @patch("services.credential_template_service.AWSAuthService")
    def test_refresh_sso_sets_last_successful_call_at(self, mock_aws_cls, db):
        """last_successful_call_at is set to approximately now after a successful refresh."""
        before = datetime.now(UTC)
        t = _create_sso_template(db, name="refresh-sets-success-ts")

        mock_auth = MagicMock()
        mock_auth.refresh_credentials.return_value = {"access_token": "tok", "expires_in": 28800}
        mock_auth.get_account_credentials.return_value = {
            "access_key_id": "AKIATEST",
            "secret_access_key": "secret",
            "session_token": "session",
            "expiration": (datetime.now(UTC) + timedelta(hours=8)).isoformat(),
        }
        mock_aws_cls.return_value = mock_auth

        svc = CredentialTemplateService(db)
        svc.refresh_sso(t.id)

        db.refresh(t)
        assert t.last_successful_call_at is not None
        ts = t.last_successful_call_at
        if ts.tzinfo is None:
            from datetime import timezone
            ts = ts.replace(tzinfo=UTC)
        assert ts >= before

    @patch("services.credential_template_service.AWSAuthService")
    def test_refresh_sso_failure_does_not_clear_error(self, mock_aws_cls, db):
        """A failed refresh leaves the stale observation untouched."""
        stale_time = datetime.now(UTC) - timedelta(hours=1)
        t = _create_sso_template(db, name="refresh-fail-keeps-error")
        t.last_error_at = stale_time
        t.last_error_code = "ExpiredToken"
        db.flush()

        mock_auth = MagicMock()
        mock_auth.refresh_credentials.side_effect = Exception("token expired")
        mock_aws_cls.return_value = mock_auth

        from core.errors import InternalError as AppInternalError
        svc = CredentialTemplateService(db)
        with pytest.raises(AppInternalError):
            svc.refresh_sso(t.id)

        db.refresh(t)
        assert t.last_error_code == "ExpiredToken"
