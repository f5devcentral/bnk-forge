"""
Unit tests for KubernetesServiceBase._generate_gcp_token.

The token generator is the Python-native replacement for
``gke-gcloud-auth-plugin``: given a service-account info dict, it mints a
short-lived OAuth access token via google-auth that GKE accepts as a bearer.

These tests mock google-auth so they do not require network access or a
real service-account key.
"""

import sys
import types
from unittest.mock import MagicMock, patch

from services.kubernetes._base import KubernetesServiceBase


def _sa_info() -> dict:
    return {
        "type": "service_account",
        "project_id": "my-gcp-project",
        "client_email": "svc@my-gcp-project.iam.gserviceaccount.com",
    }


class TestGenerateGcpToken:
    def setup_method(self):
        KubernetesServiceBase._gcp_token_cache.clear()

    def test_returns_token_from_google_auth(self):
        fake_credentials = MagicMock()
        fake_credentials.token = "ya29.fake-access-token"

        sa_module = types.ModuleType("google.oauth2.service_account")
        sa_module.Credentials = MagicMock()
        sa_module.Credentials.from_service_account_info.return_value = fake_credentials

        request_module = types.ModuleType("google.auth.transport.requests")
        request_module.Request = MagicMock()

        with patch.dict(
            sys.modules,
            {
                "google.oauth2": types.ModuleType("google.oauth2"),
                "google.oauth2.service_account": sa_module,
                "google.auth": types.ModuleType("google.auth"),
                "google.auth.transport": types.ModuleType("google.auth.transport"),
                "google.auth.transport.requests": request_module,
            },
        ):
            token = KubernetesServiceBase._generate_gcp_token(_sa_info())

        assert token == "ya29.fake-access-token"

        sa_module.Credentials.from_service_account_info.assert_called_once_with(
            _sa_info(),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        fake_credentials.refresh.assert_called_once()

    def test_returns_none_when_google_auth_missing(self):
        # Make any `from google...` import raise ImportError by stubbing the
        # parent google package as something that fails on attribute access.
        with patch.dict(sys.modules, {}, clear=False):
            for mod in [
                "google.oauth2",
                "google.oauth2.service_account",
                "google.auth.transport",
                "google.auth.transport.requests",
            ]:
                sys.modules.pop(mod, None)

            with patch.dict(sys.modules, {"google": None}):
                token = KubernetesServiceBase._generate_gcp_token(_sa_info())

        assert token is None
