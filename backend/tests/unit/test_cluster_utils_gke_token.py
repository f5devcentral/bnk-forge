"""
Unit tests for cluster_utils GKE token-rewrite path.

When a GKE cluster's kubeconfig is materialized for shell-out clients
(helm, kubectl), cluster_utils._write_kubeconfig must rewrite the `users[]`
block to a static bearer token minted via google-auth.  Without this, helm
fails with `executable gke-gcloud-auth-plugin not found` because the binary
isn't in the backend container.

These tests mock google-auth and the credentials service so they don't need
network access or a real service-account key.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import yaml

from services import cluster_utils


def _sa_info() -> dict:
    return {
        "type": "service_account",
        "project_id": "my-gcp-project",
        "client_email": "svc@my-gcp-project.iam.gserviceaccount.com",
    }


def _exec_plugin_kubeconfig() -> str:
    """A kubeconfig shaped like one emitted by `gcloud container clusters get-credentials`."""
    return yaml.dump(
        {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": "gke_proj_us-central1-a_spk-central",
                    "cluster": {
                        "server": "https://34.61.186.37",
                        "certificate-authority-data": "REDACTED",
                    },
                }
            ],
            "users": [
                {
                    "name": "gke_proj_us-central1-a_spk-central",
                    "user": {
                        "exec": {
                            "apiVersion": "client.authentication.k8s.io/v1beta1",
                            "command": "gke-gcloud-auth-plugin",
                            "interactiveMode": "IfAvailable",
                            "provideClusterInfo": True,
                        }
                    },
                }
            ],
            "contexts": [
                {
                    "name": "gke_proj_us-central1-a_spk-central",
                    "context": {
                        "cluster": "gke_proj_us-central1-a_spk-central",
                        "user": "gke_proj_us-central1-a_spk-central",
                    },
                }
            ],
            "current-context": "gke_proj_us-central1-a_spk-central",
        }
    )


def _make_cluster(provider: str = "gke") -> MagicMock:
    cluster = MagicMock()
    cluster.kubeconfig_encrypted = "ignored-by-mock"
    cluster.cloud_provider = provider
    cluster.ssh_tunnel_enabled = False
    cluster.ssh_credential_id = None
    cluster.name = "spk-central"
    cluster.project = MagicMock(name="proj")
    cluster.project.name = "spk-dev"
    return cluster


def _patched_google_auth(token_value: str):
    """Stub google-auth modules so _generate_gcp_token returns token_value."""
    fake_credentials = MagicMock()
    fake_credentials.token = token_value

    sa_module = types.ModuleType("google.oauth2.service_account")
    sa_module.Credentials = MagicMock()
    sa_module.Credentials.from_service_account_info.return_value = fake_credentials

    request_module = types.ModuleType("google.auth.transport.requests")
    request_module.Request = MagicMock()

    return {
        "google.oauth2": types.ModuleType("google.oauth2"),
        "google.oauth2.service_account": sa_module,
        "google.auth": types.ModuleType("google.auth"),
        "google.auth.transport": types.ModuleType("google.auth.transport"),
        "google.auth.transport.requests": request_module,
    }


class TestGenerateGcpToken:
    def test_returns_token_from_google_auth(self):
        with patch.dict(sys.modules, _patched_google_auth("ya29.fake-token")):
            assert cluster_utils._generate_gcp_token(_sa_info()) == "ya29.fake-token"

    def test_returns_none_when_google_auth_missing(self):
        for mod in [
            "google.oauth2",
            "google.oauth2.service_account",
            "google.auth.transport",
            "google.auth.transport.requests",
        ]:
            sys.modules.pop(mod, None)
        with patch.dict(sys.modules, {"google": None}):
            assert cluster_utils._generate_gcp_token(_sa_info()) is None


class TestWriteKubeconfigGkeRewrite:
    def test_gke_kubeconfig_user_rewritten_to_bearer_token(self):
        cluster = _make_cluster("gke")
        db = MagicMock()
        kubeconfig_yaml = _exec_plugin_kubeconfig()

        with patch.object(cluster_utils, "decrypt_value", return_value=kubeconfig_yaml), \
             patch("services.credentials_service.get_gcp_service_account_info",
                   return_value=_sa_info()), \
             patch.dict(sys.modules, _patched_google_auth("ya29.fresh-token")):
            path = cluster_utils._write_kubeconfig(cluster, db)

        try:
            with open(path) as f:
                rewritten = yaml.safe_load(f)
        finally:
            cluster_utils._cleanup_kubeconfig(path)

        users = rewritten["users"]
        assert len(users) == 1
        # Exec block must be gone, replaced by static bearer token
        assert users[0]["user"] == {"token": "ya29.fresh-token"}
        assert "exec" not in users[0]["user"]

    def test_gke_no_sa_credentials_falls_through_unchanged(self):
        """If the project has no GCP creds configured, leave the kubeconfig alone
        so the (broken) exec plugin path runs and produces a clear error."""
        cluster = _make_cluster("gke")
        db = MagicMock()
        kubeconfig_yaml = _exec_plugin_kubeconfig()

        with patch.object(cluster_utils, "decrypt_value", return_value=kubeconfig_yaml), \
             patch("services.credentials_service.get_gcp_service_account_info",
                   return_value=None):
            path = cluster_utils._write_kubeconfig(cluster, db)

        try:
            with open(path) as f:
                rewritten = yaml.safe_load(f)
        finally:
            cluster_utils._cleanup_kubeconfig(path)

        # Original exec block intact
        assert "exec" in rewritten["users"][0]["user"]
        assert rewritten["users"][0]["user"]["exec"]["command"] == "gke-gcloud-auth-plugin"

    def test_non_gke_cluster_kubeconfig_passthrough(self):
        """A non-GKE provider must not trigger the GKE rewrite path."""
        cluster = _make_cluster("on-prem")
        db = MagicMock()
        kubeconfig_yaml = _exec_plugin_kubeconfig()

        with patch.object(cluster_utils, "decrypt_value", return_value=kubeconfig_yaml):
            path = cluster_utils._write_kubeconfig(cluster, db)

        try:
            with open(path) as f:
                content = f.read()
        finally:
            cluster_utils._cleanup_kubeconfig(path)

        # Bytes-for-bytes equal — no parse-and-redump
        assert content == kubeconfig_yaml
