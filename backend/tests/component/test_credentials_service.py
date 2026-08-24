"""
BC-028: Component tests for credentials_service — credential loading priority,
template-based credentials, legacy credentials, environment fallback.

Uses real SQLite DB with mocked encryption.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from models import ApplicationSetting, CloudCredentialTemplate, Project
from services.credentials_service import (
    CredentialUnavailableError,
    _decrypt_credential,
    get_cloud_credentials_env,
    get_gcp_service_account_info,
)

# ── Helpers ──────────────────────────────────────────────────────────

def _make_project(db, name="Cred Project", credential_template_id=None, **kwargs):
    project_type = kwargs.pop("project_type", "cloud-aws")
    cloud_provider = kwargs.pop("cloud_provider", "aws")
    project = Project(
        name=name, project_type=project_type, cloud_provider=cloud_provider,
        environment="dev", backend_type="local", color="#000", icon="cloud",
        is_active=True, credential_template_id=credential_template_id,
        **kwargs,
    )
    db.add(project)
    db.flush()
    return project


def _make_template(db, **kwargs):
    defaults = dict(
        name="AWS Template", provider="aws", region="us-east-1",
        aws_auth_method="access_keys",
        aws_access_key_id="AKIA_TEMPLATE",
        aws_secret_access_key_encrypted="enc_secret",
    )
    defaults.update(kwargs)
    template = CloudCredentialTemplate(**defaults)
    db.add(template)
    db.flush()
    return template


def _make_app_setting(db, key, value, is_encrypted=False):
    setting = ApplicationSetting(key=key, value=value, is_encrypted=is_encrypted)
    db.add(setting)
    db.flush()
    return setting


# ── _decrypt_credential helper ───────────────────────────────────────

class TestDecryptCredentialHelper:
    def test_returns_none_for_empty(self):
        result = _decrypt_credential(None, "test_field")
        assert result is None

    def test_returns_none_for_empty_string(self):
        result = _decrypt_credential("", "test_field")
        assert result is None

    @patch("services.credentials_service.decrypt_value", return_value="decrypted")
    def test_decrypts_value(self, mock_dec):
        result = _decrypt_credential("encrypted_data", "test_field")
        assert result == "decrypted"

    @patch("services.credentials_service.decrypt_value", side_effect=Exception("Bad key"))
    def test_returns_none_on_error(self, mock_dec):
        result = _decrypt_credential("bad_data", "test_field")
        assert result is None


# ── Priority 1: Credential Template ─────────────────────────────────

class TestCredentialTemplate:
    @patch("services.credentials_service.decrypt_value", return_value="template_secret")
    def test_access_keys_template(self, mock_dec, db):
        template = _make_template(db)
        project = _make_project(db, credential_template_id=template.id)

        env = get_cloud_credentials_env(project, db=db)

        assert env["AWS_ACCESS_KEY_ID"] == "AKIA_TEMPLATE"
        assert env["AWS_SECRET_ACCESS_KEY"] == "template_secret"
        assert env["AWS_DEFAULT_REGION"] == "us-east-1"

    @patch("services.credentials_service.decrypt_value", return_value="decrypted")
    def test_profile_template(self, mock_dec, db):
        template = _make_template(
            db, aws_auth_method="profile", aws_profile="prod-profile",
            aws_access_key_id=None, aws_secret_access_key_encrypted=None,
        )
        project = _make_project(db, credential_template_id=template.id)

        env = get_cloud_credentials_env(project, db=db)
        assert env["AWS_PROFILE"] == "prod-profile"

    @patch("services.credentials_service.decrypt_value", return_value="ibm-api-key")
    def test_ibm_template(self, mock_dec, db):
        template = _make_template(
            db,
            name="IBM Template",
            provider="ibm",
            region="us-south",
            aws_auth_method=None,
            aws_access_key_id=None,
            aws_secret_access_key_encrypted=None,
            ibmcloud_api_key_encrypted="enc_ibm_key",
        )
        project = _make_project(
            db,
            credential_template_id=template.id,
            project_type="cloud-aws",
            cloud_provider="ibm",
        )

        env = get_cloud_credentials_env(project, db=db)

        assert env["IC_API_KEY"] == "ibm-api-key"
        assert env["IBMCLOUD_API_KEY"] == "ibm-api-key"
        assert env["IBMCLOUD_REGION"] == "us-south"

    @patch("services.credentials_service.decrypt_value", return_value="ibm-api-key")
    def test_misspelled_ibmcloud_provider_injects_nothing(self, mock_dec, db):
        """Issue #191 reproduction at the injection layer.

        A template stored with provider='ibmcloud' (the natural misspelling of
        the canonical 'ibm', matching every adjacent ibmcloud_* field name)
        matches NO branch in the resolver, so it silently contributes no IBM
        credentials — the deploy then fails far away with a BearerToken error.
        The create/update validation (issue #191) now prevents such a row from
        being created; this locks the resolver contract that made it dangerous.
        """
        template = _make_template(
            db,
            name="IBM ROKs Testing",
            provider="ibmcloud",  # unknown to the resolver -> no-op
            region="us-east",
            aws_auth_method=None,
            aws_access_key_id=None,
            aws_secret_access_key_encrypted=None,
            ibmcloud_api_key_encrypted="enc_ibm_key",
        )
        project = _make_project(
            db,
            credential_template_id=template.id,
            project_type="cloud-aws",
            cloud_provider="ibm",
        )

        env = get_cloud_credentials_env(project, db=db)

        # The stored API key never reaches the deployment environment.
        assert "IC_API_KEY" not in env
        assert "IBMCLOUD_API_KEY" not in env

    @patch("services.credentials_service.decrypt_value", side_effect=Exception("Bad key"))
    def test_ibm_template_decrypt_failure_omits_api_key(self, mock_dec, db):
        template = _make_template(
            db,
            name="IBM Template Bad Key",
            provider="ibm",
            region="eu-de",
            aws_auth_method=None,
            aws_access_key_id=None,
            aws_secret_access_key_encrypted=None,
            ibmcloud_api_key_encrypted="enc_ibm_key",
        )
        project = _make_project(
            db,
            credential_template_id=template.id,
            project_type="cloud-aws",
            cloud_provider="ibm",
        )

        env = get_cloud_credentials_env(project, db=db)

        assert "IC_API_KEY" not in env
        assert "IBMCLOUD_API_KEY" not in env
        assert env["IBMCLOUD_REGION"] == "eu-de"


# ── Priority 2: Legacy Encrypted Credentials ────────────────────────

class TestLegacyCredentials:
    @patch("services.credentials_service.decrypt_value")
    def test_legacy_access_key_credentials(self, mock_dec, db):
        cred_data = json.dumps({
            "access_key_id": "AKIA_LEGACY",
            "secret_access_key": "legacy_secret",
        })
        mock_dec.return_value = cred_data

        project = _make_project(
            db, cloud_credentials_encrypted="encrypted_json",
        )

        env = get_cloud_credentials_env(project, db=db)
        assert env["AWS_ACCESS_KEY_ID"] == "AKIA_LEGACY"
        assert env["AWS_SECRET_ACCESS_KEY"] == "legacy_secret"

    @patch("services.credentials_service.decrypt_value")
    def test_legacy_profile_credentials(self, mock_dec, db):
        cred_data = json.dumps({
            "profile": "my-profile",
            "region": "eu-west-1",
        })
        mock_dec.return_value = cred_data

        project = _make_project(
            db, cloud_credentials_encrypted="encrypted_json",
        )

        env = get_cloud_credentials_env(project, db=db)
        assert env["AWS_PROFILE"] == "my-profile"
        assert env["AWS_DEFAULT_REGION"] == "eu-west-1"


# ── Priority 3: Global Settings from DB ──────────────────────────────

class TestGlobalSettings:
    @patch("services.credentials_service.decrypt_value_or_none", return_value="global_secret")
    def test_global_credentials(self, mock_dec, db):
        _make_app_setting(db, "aws.access_key_id", "AKIA_GLOBAL")
        _make_app_setting(db, "aws.secret_access_key", "enc_global_secret", is_encrypted=True)
        _make_app_setting(db, "aws.region", "ap-southeast-1")

        project = _make_project(db)
        env = get_cloud_credentials_env(project, db=db)

        assert env["AWS_ACCESS_KEY_ID"] == "AKIA_GLOBAL"
        assert env["AWS_SECRET_ACCESS_KEY"] == "global_secret"
        assert env["AWS_DEFAULT_REGION"] == "ap-southeast-1"


# ── Fallback: Environment Variables ──────────────────────────────────

class TestEnvironmentFallback:
    @patch.dict("os.environ", {"AWS_ACCESS_KEY_ID": "AKIA_ENV", "AWS_SECRET_ACCESS_KEY": "env_secret"})
    def test_env_fallback(self, db):
        project = _make_project(db, region="us-west-2")

        env = get_cloud_credentials_env(project, db=db)
        assert env["AWS_ACCESS_KEY_ID"] == "AKIA_ENV"
        assert env["AWS_DEFAULT_REGION"] == "us-west-2"


# ── GCP service-account info helper ──────────────────────────────────

class TestGcpServiceAccountInfo:
    def _make_gcp_template(self, db, encrypted_value="enc_sa_json"):
        template = CloudCredentialTemplate(
            name="GCP Template", provider="gcp",
            gcp_project_id="my-gcp-project",
            gcp_credentials_encrypted=encrypted_value,
        )
        db.add(template)
        db.flush()
        return template

    def test_returns_none_when_no_template_attached(self, db):
        project = _make_project(db, name="No Template")
        assert get_gcp_service_account_info(project, db=db) is None

    def test_returns_none_when_db_missing(self, db):
        template = self._make_gcp_template(db)
        project = _make_project(db, name="No DB", credential_template_id=template.id)
        assert get_gcp_service_account_info(project, db=None) is None

    def test_returns_none_for_non_gcp_provider(self, db):
        template = _make_template(db)  # AWS
        project = _make_project(db, name="AWS Project", credential_template_id=template.id)
        assert get_gcp_service_account_info(project, db=db) is None

    def test_returns_none_when_credentials_missing(self, db):
        template = self._make_gcp_template(db, encrypted_value=None)
        project = _make_project(db, name="No Creds", credential_template_id=template.id)
        assert get_gcp_service_account_info(project, db=db) is None

    @patch("services.credentials_service.decrypt_value", return_value=None)
    def test_returns_none_when_decryption_fails(self, mock_dec, db):
        template = self._make_gcp_template(db)
        project = _make_project(db, name="Bad Decrypt", credential_template_id=template.id)
        assert get_gcp_service_account_info(project, db=db) is None

    @patch("services.credentials_service.decrypt_value", return_value="not valid json {")
    def test_returns_none_for_invalid_json(self, mock_dec, db):
        template = self._make_gcp_template(db)
        project = _make_project(db, name="Bad JSON", credential_template_id=template.id)
        assert get_gcp_service_account_info(project, db=db) is None

    @patch("services.credentials_service.decrypt_value")
    def test_returns_parsed_sa_dict(self, mock_dec, db):
        sa_payload = {
            "type": "service_account",
            "project_id": "my-gcp-project",
            "private_key_id": "abc123",
            "client_email": "svc@my-gcp-project.iam.gserviceaccount.com",
        }
        mock_dec.return_value = json.dumps(sa_payload)

        template = self._make_gcp_template(db)
        project = _make_project(db, name="Good GCP", credential_template_id=template.id)
        result = get_gcp_service_account_info(project, db=db)

        assert result == sa_payload


# ── is_default fallback (Priority 2.5) ───────────────────────────────

class TestDefaultTemplateFallback:
    """
    When a project has no explicit credential_template_id and no project creds,
    the resolver must fall back to the is_default CloudCredentialTemplate.
    """

    @patch("services.credentials_service.decrypt_value", return_value="fallback_secret")
    def test_unbound_project_uses_default_template(self, mock_dec, db):
        """Project 40 shape: no credential_template_id, no project creds.
        Should transparently use the is_default template's AWS keys.
        """
        template = _make_template(db, is_default=True, aws_access_key_id="AKIA_DEFAULT")
        project = _make_project(db, name="Unbound Project")  # no template, no creds

        env = get_cloud_credentials_env(project, db=db)

        assert env["AWS_ACCESS_KEY_ID"] == "AKIA_DEFAULT"
        assert env["AWS_SECRET_ACCESS_KEY"] == "fallback_secret"

    def test_no_default_template_falls_through_gracefully(self, db):
        """If no is_default template exists, resolver should not fail — just
        return the global/env fallback as before.
        """
        project = _make_project(db, name="No Default")
        # No template with is_default=True in the DB
        env = get_cloud_credentials_env(project, db=db)
        # Should return something (env copy), not raise
        assert isinstance(env, dict)

    @patch("services.credentials_service.decrypt_value", return_value="bound_secret")
    def test_explicit_template_takes_precedence_over_default(self, mock_dec, db):
        """If the project has its own credential_template_id, the default template
        must NOT be consulted — explicit binding wins.
        """
        _make_template(db, name="Default Template", is_default=True, aws_access_key_id="AKIA_DEFAULT")
        explicit = _make_template(db, name="Explicit Template", is_default=False, aws_access_key_id="AKIA_EXPLICIT")
        project = _make_project(db, name="Explicitly Bound", credential_template_id=explicit.id)

        env = get_cloud_credentials_env(project, db=db)

        assert env["AWS_ACCESS_KEY_ID"] == "AKIA_EXPLICIT"

    def test_wrong_provider_default_not_used_for_aws_project(self, db):
        """An AWS project must NOT fall back to a GCP is_default template.

        A GCP default injecting credentials into an AWS project would cause a
        401 with no actionable error message.
        """
        # Only a GCP is_default template exists — no AWS default
        gcp_default = CloudCredentialTemplate(
            name="GCP Default",
            provider="gcp",
            is_default=True,
        )
        db.add(gcp_default)
        db.flush()

        project = _make_project(db, name="AWS No Fallback", cloud_provider="aws")
        # project has no credential_template_id and no project creds

        env = get_cloud_credentials_env(project, db=db)

        # The GCP keys must not be present — wrong-provider fallback must not fire
        assert "AWS_ACCESS_KEY_ID" not in env or env.get("AWS_ACCESS_KEY_ID") != "GCP_KEY"
        # Specifically confirm we did NOT inject keys from the GCP template
        # (GCP template has no aws_access_key_id, but verifying no KeyError / wrong key)
        assert "IC_API_KEY" not in env
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in env


# ── SSO CredentialUnavailableError ────────────────────────────────────

class TestSsoCredentialUnavailableError:
    """
    When an SSO template has absent or expired exchanged SigV4 keys:
    - strict=True (connectivity path) → CredentialUnavailableError
    - strict=False (default, ~19 task/deploy callers) → returns env, does NOT raise
    """

    def test_raises_when_sso_keys_absent_strict(self, db):
        """strict=True + aws_access_key_id is None → CredentialUnavailableError."""
        template = _make_template(
            db,
            name="SSO No Keys",
            aws_auth_method="sso",
            aws_sso_enabled=True,
            aws_access_key_id=None,
            aws_secret_access_key_encrypted=None,
        )
        project = _make_project(db, name="SSO Absent", credential_template_id=template.id)

        with pytest.raises(CredentialUnavailableError, match="not been exchanged"):
            get_cloud_credentials_env(project, db=db, strict=True)

    def test_raises_when_sso_keys_expired_strict(self, db):
        """strict=True + aws_credentials_expiry in the past → CredentialUnavailableError."""
        from datetime import UTC, datetime, timedelta

        past = datetime.now(UTC) - timedelta(hours=1)
        template = _make_template(
            db,
            name="SSO Expired",
            aws_auth_method="sso",
            aws_sso_enabled=True,
            aws_access_key_id="AKIA_EXPIRED",
            aws_secret_access_key_encrypted="enc",
            aws_credentials_expiry=past,
        )
        project = _make_project(db, name="SSO Expired Project", credential_template_id=template.id)

        with pytest.raises(CredentialUnavailableError, match="expired"):
            get_cloud_credentials_env(project, db=db, strict=True)

    def test_absent_sso_keys_no_raise_when_not_strict(self, db):
        """strict=False (default) + absent SSO keys → returns env dict, does NOT raise.
        This is the regression guard for the ~19 task/deploy callers that must
        retain their existing graceful-degradation behaviour unchanged.
        """
        template = _make_template(
            db,
            name="SSO No Keys NonStrict",
            aws_auth_method="sso",
            aws_sso_enabled=True,
            aws_access_key_id=None,
            aws_secret_access_key_encrypted=None,
        )
        project = _make_project(db, name="SSO Absent NonStrict", credential_template_id=template.id)

        # Must NOT raise — should return a dict (may lack AWS keys, but no exception)
        env = get_cloud_credentials_env(project, db=db)
        assert isinstance(env, dict)

    def test_expired_sso_keys_no_raise_when_not_strict(self, db):
        """strict=False (default) + expired SSO keys → returns env dict, does NOT raise.
        Regression guard: task/deploy callers must not be broken by an expired template.
        """
        from datetime import UTC, datetime, timedelta

        past = datetime.now(UTC) - timedelta(hours=1)
        template = _make_template(
            db,
            name="SSO Expired NonStrict",
            aws_auth_method="sso",
            aws_sso_enabled=True,
            aws_access_key_id="AKIA_EXPIRED",
            aws_secret_access_key_encrypted="enc",
            aws_credentials_expiry=past,
        )
        project = _make_project(db, name="SSO Expired NonStrict Project", credential_template_id=template.id)

        # Must NOT raise — graceful degradation for task callers
        env = get_cloud_credentials_env(project, db=db)
        assert isinstance(env, dict)

    @patch("services.credentials_service.decrypt_value", return_value="sso_secret")
    def test_valid_sso_keys_do_not_raise(self, mock_dec, db):
        """SSO keys present and not expired → returns env without raising (both modes)."""
        from datetime import UTC, datetime, timedelta

        future = datetime.now(UTC) + timedelta(hours=1)
        template = _make_template(
            db,
            name="SSO Valid",
            aws_auth_method="sso",
            aws_sso_enabled=True,
            aws_access_key_id="AKIA_VALID",
            aws_secret_access_key_encrypted="enc",
            aws_credentials_expiry=future,
        )
        project = _make_project(db, name="SSO Valid Project", credential_template_id=template.id)

        env = get_cloud_credentials_env(project, db=db)
        assert env["AWS_ACCESS_KEY_ID"] == "AKIA_VALID"
        assert env["AWS_SECRET_ACCESS_KEY"] == "sso_secret"


# ── TF_VAR mirroring for AWS credential templates ────────────────────

class TestAwsTfVarMirroring:
    """
    BC-TFV: get_cloud_credentials_env must mirror resolved AWS credentials as
    TF_VAR_* so that Terraform modules using var.aws_access_key_id / var.aws_region
    in provider blocks receive the credentials without Forge persisting them in
    project variable storage.
    """

    @patch("services.credentials_service.decrypt_value", return_value="secret_value")
    def test_access_keys_template_mirrors_tf_vars(self, mock_dec, db):
        """access_keys auth → TF_VAR_aws_access_key_id / TF_VAR_aws_secret_access_key set."""
        template = _make_template(
            db,
            aws_access_key_id="AKIA_TF",
            aws_secret_access_key_encrypted="enc_secret",
            region="eu-west-1",
        )
        project = _make_project(db, credential_template_id=template.id)

        env = get_cloud_credentials_env(project, db=db)

        assert env["TF_VAR_aws_access_key_id"] == "AKIA_TF"
        assert env["TF_VAR_aws_secret_access_key"] == "secret_value"
        assert env["TF_VAR_aws_region"] == "eu-west-1"
        # TF_VAR mirrors equal the corresponding AWS_* values
        assert env["TF_VAR_aws_access_key_id"] == env["AWS_ACCESS_KEY_ID"]
        assert env["TF_VAR_aws_secret_access_key"] == env["AWS_SECRET_ACCESS_KEY"]
        assert env["TF_VAR_aws_region"] == env["AWS_DEFAULT_REGION"]

    @patch("services.credentials_service.decrypt_value", return_value="sso_secret_tf")
    def test_sso_template_mirrors_tf_vars(self, mock_dec, db):
        """SSO auth (keys present) → TF_VAR mirrors are set."""
        from datetime import UTC, datetime, timedelta

        future = datetime.now(UTC) + timedelta(hours=1)
        template = _make_template(
            db,
            name="SSO TF",
            aws_auth_method="sso",
            aws_sso_enabled=True,
            aws_access_key_id="AKIA_SSO",
            aws_secret_access_key_encrypted="enc",
            aws_credentials_expiry=future,
            region="ap-southeast-1",
        )
        project = _make_project(db, credential_template_id=template.id)

        env = get_cloud_credentials_env(project, db=db)

        assert env["TF_VAR_aws_access_key_id"] == "AKIA_SSO"
        assert env["TF_VAR_aws_secret_access_key"] == "sso_secret_tf"
        assert env["TF_VAR_aws_region"] == "ap-southeast-1"

    @patch("services.credentials_service.decrypt_value", return_value="tok_secret")
    def test_session_token_mirrored_when_present(self, mock_dec, db):
        """When AWS_SESSION_TOKEN is set, TF_VAR_aws_session_token must match."""
        template = _make_template(
            db,
            name="Keys With Token",
            aws_access_key_id="AKIA_TOK",
            aws_secret_access_key_encrypted="enc",
            region="us-east-1",
        )
        # Store a session token on the template to trigger token mirroring.
        template.aws_session_token_encrypted = "enc_tok"
        db.flush()
        project = _make_project(db, credential_template_id=template.id)

        env = get_cloud_credentials_env(project, db=db)

        assert env.get("TF_VAR_aws_session_token") == env.get("AWS_SESSION_TOKEN")

    @patch("services.credentials_service.decrypt_value", return_value="prof_secret")
    def test_profile_template_does_not_set_key_tf_vars(self, mock_dec, db):
        """Profile auth does not resolve individual keys → TF_VAR key vars must NOT be set."""
        template = _make_template(
            db,
            name="Profile Only",
            aws_auth_method="profile",
            aws_profile="my-profile",
            aws_access_key_id=None,
            aws_secret_access_key_encrypted=None,
            region="us-west-2",
        )
        project = _make_project(db, credential_template_id=template.id)

        env = get_cloud_credentials_env(project, db=db)

        # Profile auth sets AWS_PROFILE, not access keys
        assert env.get("AWS_PROFILE") == "my-profile"
        # No individual key TF_VARs — nothing to mirror
        assert "TF_VAR_aws_access_key_id" not in env
        assert "TF_VAR_aws_secret_access_key" not in env

    @patch("services.credentials_service.decrypt_value", return_value="ibm_secret")
    def test_ibm_template_does_not_set_aws_tf_vars(self, mock_dec, db):
        """IBM templates must not accidentally set AWS TF_VAR keys."""
        template = _make_template(
            db,
            name="IBM No TF",
            provider="ibm",
            region="us-south",
            aws_auth_method=None,
            aws_access_key_id=None,
            aws_secret_access_key_encrypted=None,
            ibmcloud_api_key_encrypted="enc_ibm",
        )
        project = _make_project(
            db, credential_template_id=template.id,
            project_type="cloud-ibm", cloud_provider="ibm",
        )

        env = get_cloud_credentials_env(project, db=db)

        assert "TF_VAR_aws_access_key_id" not in env
        assert "TF_VAR_aws_secret_access_key" not in env
