"""Unit tests for services.roks_service kubeconfig token helpers."""

from unittest.mock import patch

import yaml

from services.roks_service import refresh_kubeconfig_iam_token

KUBECONFIG_YAML = """\
apiVersion: v1
clusters:
- cluster:
    server: https://roks.example.com:6443
    certificate-authority-data: dGVzdA==
  name: my-roks
contexts:
- context:
    cluster: my-roks
    user: admin
  name: my-roks-context
current-context: my-roks-context
kind: Config
users:
- name: admin
  user:
    token: stale-token
"""


@patch("services.roks_service.fetch_iam_bearer_token")
def test_refresh_kubeconfig_iam_token_rewrites_user_token(mock_fetch):
    mock_fetch.return_value = "fresh-token"
    result = refresh_kubeconfig_iam_token(KUBECONFIG_YAML, "api-key")
    assert result is not None
    cfg = yaml.safe_load(result)
    assert cfg["users"][0]["user"] == {"token": "fresh-token"}
    mock_fetch.assert_called_once_with("api-key")


@patch("services.roks_service.fetch_iam_bearer_token")
def test_refresh_kubeconfig_iam_token_returns_none_when_exchange_fails(mock_fetch):
    mock_fetch.return_value = None
    assert refresh_kubeconfig_iam_token(KUBECONFIG_YAML, "api-key") is None


@patch("services.roks_service.fetch_iam_bearer_token")
def test_refresh_kubeconfig_iam_token_returns_none_when_no_users(mock_fetch):
    mock_fetch.return_value = "fresh-token"
    no_users = yaml.dump({"apiVersion": "v1", "kind": "Config", "clusters": []})
    assert refresh_kubeconfig_iam_token(no_users, "api-key") is None


CERT_KUBECONFIG_YAML = """\
apiVersion: v1
kind: Config
clusters:
- cluster: {server: https://roks.example.com:6443}
  name: my-roks
users:
- name: admin
  user:
    client-certificate-data: Q0VSVA==
    client-key-data: S0VZ
"""


@patch("services.roks_service.fetch_iam_bearer_token")
def test_refresh_leaves_cert_kubeconfig_untouched(mock_fetch):
    # A cert-based ROKS/OpenShift kubeconfig has no token to refresh; injecting
    # one would clobber the client certs and break auth (ROKS 401s IAM tokens).
    # The refresh must return None (no change), not rewrite the cert user.
    mock_fetch.return_value = "fresh-token"
    assert refresh_kubeconfig_iam_token(CERT_KUBECONFIG_YAML, "api-key") is None
