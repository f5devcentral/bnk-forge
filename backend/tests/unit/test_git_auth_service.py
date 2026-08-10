from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.git_auth_service import GitAuthService


@patch("services.git_auth_service.decrypt_value_or_none", return_value="deploy-secret")
def test_resolve_gitlab_deploy_token_prefers_gitlab_deploy_username(_mock_decrypt):
    source = SimpleNamespace(
        credential_type="gitlab_deploy_token",
        auth_type="token",
        auth_token_encrypted="enc-token",
        credential_metadata={"gitlab": {"deploy_username": "gitlab+deploy-token-12"}},
    )

    context = GitAuthService.resolve_for_module_source(source)

    assert context.transport == "https_token"
    assert context.username == "gitlab+deploy-token-12"


def test_build_http_headers_uses_bearer_for_gitlab_urls():
    auth = SimpleNamespace(transport="https_token", secret="abc123", credential_type="legacy_pat")

    headers = GitAuthService.build_http_headers(auth, "https://gitlab.example.com/api/v4/projects/foo")

    assert headers == {"Authorization": "Bearer abc123"}


@patch("services.git_auth_service.decrypt_value_or_none", return_value="oauth-secret")
def test_resolve_oauth_token_uses_shared_https_token_transport(_mock_decrypt):
    source = SimpleNamespace(
        credential_type="oauth_token",
        auth_type="token",
        auth_token_encrypted="enc-token",
        credential_metadata={"oauth": {"principal": "alice@example.com"}},
    )

    context = GitAuthService.resolve_for_module_source(source)

    assert context.transport == "https_token"
    assert context.credential_type == "oauth_token"
    assert context.secret == "oauth-secret"
    assert context.username == "git"


def test_build_http_headers_uses_private_token_for_gitlab_project_token():
    auth = SimpleNamespace(
        transport="https_token",
        secret="glpat-project",
        credential_type="gitlab_project_token",
        username="git",
    )

    headers = GitAuthService.build_http_headers(auth, "https://gitlab.example.com/api/v4/projects/foo")

    assert headers == {"PRIVATE-TOKEN": "glpat-project"}


def test_build_http_headers_uses_private_token_for_gitlab_group_token():
    auth = SimpleNamespace(
        transport="https_token",
        secret="glpat-group",
        credential_type="gitlab_group_token",
        username="git",
    )

    headers = GitAuthService.build_http_headers(auth, "https://gitlab.example.com/api/v4/projects/foo")

    assert headers == {"PRIVATE-TOKEN": "glpat-group"}


def test_build_http_headers_uses_basic_auth_for_gitlab_deploy_token():
    auth = SimpleNamespace(
        transport="https_token",
        secret="deploy-secret",
        credential_type="gitlab_deploy_token",
        username="gitlab+deploy-token-1",
    )

    headers = GitAuthService.build_http_headers(auth, "https://gitlab.example.com/api/v4/projects/foo")

    assert headers == {"Authorization": "Basic Z2l0bGFiK2RlcGxveS10b2tlbi0xOmRlcGxveS1zZWNyZXQ="}


@patch("services.git_auth_service.decrypt_value_or_none", return_value="ssh-private-key")
def test_resolve_ssh_deploy_key_includes_known_hosts(_mock_decrypt):
    source = SimpleNamespace(
        credential_type="ssh_deploy_key",
        auth_type="ssh",
        auth_token_encrypted="enc-key",
        credential_metadata={
            "ssh": {
                "known_hosts": "git.internal.example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...",
            }
        },
    )

    context = GitAuthService.resolve_for_module_source(source)

    assert context.transport == "ssh_key"
    assert context.secret == "ssh-private-key"
    assert context.ssh_known_hosts is not None
    assert "git.internal.example.com" in context.ssh_known_hosts


def test_build_git_environment_ssh_requires_known_hosts():
    auth = SimpleNamespace(
        transport="ssh_key",
        credential_type="ssh_deploy_key",
        secret="PRIVATE KEY",
        ssh_known_hosts=None,
    )

    with pytest.raises(RuntimeError, match="known_hosts trust material is required"):
        GitAuthService.build_git_environment(auth)


def test_build_git_environment_ssh_writes_wrapper_with_strict_host_key_and_known_hosts():
    auth = SimpleNamespace(
        transport="ssh_key",
        credential_type="ssh_deploy_key",
        secret="PRIVATE KEY",
        ssh_known_hosts="git.internal.example.com ssh-ed25519 AAAAC3Nza...",
    )

    env, cleanup = GitAuthService.build_git_environment(auth)
    try:
        assert "GIT_SSH" in env
        wrapper_path = env["GIT_SSH"]
        with open(wrapper_path, encoding="utf-8") as fh:
            wrapper = fh.read()
        assert "StrictHostKeyChecking=yes" in wrapper
        assert "UserKnownHostsFile=" in wrapper
        assert "GlobalKnownHostsFile=/dev/null" in wrapper

        # Ensure known_hosts file exists in temp auth dir.
        known_hosts_path = os.path.join(os.path.dirname(wrapper_path), "known_hosts")
        assert os.path.exists(known_hosts_path)
    finally:
        cleanup()


def test_parse_ssh_git_target_accepts_supported_formats():
    assert GitAuthService.parse_ssh_git_target("ssh://git@git.internal.example.com/team/repo.git") == (
        "git.internal.example.com",
        "team/repo.git",
    )
    assert GitAuthService.parse_ssh_git_target("git@git.internal.example.com:team/repo.git") == (
        "git.internal.example.com",
        "team/repo.git",
    )


def test_parse_ssh_git_target_rejects_ambiguous_or_malformed_formats():
    invalid_urls = [
        "",
        "ssh://git.internal.example.com",
        "ssh://git@@git.internal.example.com/team/repo.git",
        "ssh://git@git.internal.example.com/team/repo.git?ref=main",
        "git@git.internal.example.com/team/repo.git",
        "git@host:with space/repo.git",
        "ssh://git@git.internal.example.com/",
        "https://git.internal.example.com/team/repo.git",
    ]
    for url in invalid_urls:
        assert GitAuthService.parse_ssh_git_target(url) is None


# ---------------------------------------------------------------------------
# #305 — no plaintext git credential store; GIT_ASKPASS per-operation auth
# ---------------------------------------------------------------------------


def test_build_git_environment_https_token_sets_askpass_not_credential_store():
    """build_git_environment must NOT configure credential.helper store.

    Instead the token is injected via GIT_ASKPASS and companion env vars
    so nothing is written to ~/.git-credentials.
    """
    auth = SimpleNamespace(
        transport="https_token",
        credential_type="legacy_pat",
        secret="tok-abc123",
        username="git",
    )

    env, cleanup = GitAuthService.build_git_environment(auth)
    try:
        # GIT_ASKPASS must point to an executable script in a temp dir.
        assert "GIT_ASKPASS" in env, "GIT_ASKPASS must be set for per-operation token injection"
        askpass = env["GIT_ASKPASS"]
        assert os.path.isfile(askpass), "GIT_ASKPASS script must exist on disk"
        assert os.access(askpass, os.X_OK), "GIT_ASKPASS script must be executable"

        # The companion env vars carry the credentials (not a credentials file).
        assert env.get("GIT_AUTH_USERNAME") == "git"
        assert env.get("GIT_AUTH_PASSWORD") == "tok-abc123"

        # GIT_TERMINAL_PROMPT must be disabled so git never falls back to a
        # terminal prompt when the askpass script is unavailable.
        assert env.get("GIT_TERMINAL_PROMPT") == "0"

        # Credential store must NOT be configured via this env path.
        # The absence of GIT_CONFIG_PARAMETERS (or any setting that would wire
        # credential.helper=store) is the contract.  We also confirm the askpass
        # script itself echoes the username/password from env, not a creds file.
        with open(askpass, encoding="utf-8") as fh:
            script = fh.read()
        assert "credential.helper" not in script, (
            "askpass script must not reference credential.helper"
        )
        assert "git-credentials" not in script, (
            "askpass script must not reference .git-credentials"
        )
        assert "$GIT_AUTH_USERNAME" in script
        assert "$GIT_AUTH_PASSWORD" in script
    finally:
        cleanup()


def test_build_git_environment_https_token_no_credentials_file_written():
    """build_git_environment must not create a ~/.git-credentials file."""
    auth = SimpleNamespace(
        transport="https_token",
        credential_type="legacy_pat",
        secret="tok-xyz",
        username="git",
    )

    home_credentials = os.path.expanduser("~/.git-credentials")
    existed_before = os.path.exists(home_credentials)

    env, cleanup = GitAuthService.build_git_environment(auth)
    try:
        existed_after = os.path.exists(home_credentials)
        # If the file didn't exist before, it must not have been created.
        if not existed_before:
            assert not existed_after, (
                "build_git_environment must not create ~/.git-credentials"
            )
    finally:
        cleanup()


def test_build_git_environment_no_transport_returns_terminal_prompt_disabled():
    """With transport=none, GIT_TERMINAL_PROMPT=0 is still set so git never hangs."""
    auth = SimpleNamespace(
        transport="none",
        credential_type="none",
        secret=None,
        username=None,
        ssh_known_hosts=None,
    )

    env, cleanup = GitAuthService.build_git_environment(auth)
    try:
        assert env.get("GIT_TERMINAL_PROMPT") == "0"
        assert "GIT_ASKPASS" not in env, "No ASKPASS when there is no token to inject"
    finally:
        cleanup()


def test_build_git_environment_sets_low_speed_timeout_vars():
    """build_git_environment must set GIT_HTTP_LOW_SPEED_LIMIT and GIT_HTTP_LOW_SPEED_TIME
    so a blackholed HTTP git host cannot stall the boot path indefinitely.
    Values must be numeric strings that git accepts as-is.
    """
    auth = SimpleNamespace(
        transport="none",
        credential_type="none",
        secret=None,
        username=None,
        ssh_known_hosts=None,
    )

    env, cleanup = GitAuthService.build_git_environment(auth)
    try:
        assert "GIT_HTTP_LOW_SPEED_LIMIT" in env, "must bound minimum transfer rate"
        assert "GIT_HTTP_LOW_SPEED_TIME" in env, "must bound stall window"
        # Values must be parseable as positive integers (git rejects non-numeric)
        assert int(env["GIT_HTTP_LOW_SPEED_LIMIT"]) > 0
        assert int(env["GIT_HTTP_LOW_SPEED_TIME"]) > 0
    finally:
        cleanup()


def test_build_git_environment_low_speed_vars_present_for_https_token():
    """Low-speed timeout vars are also set on HTTPS token auth (the common catalog path)."""
    auth = SimpleNamespace(
        transport="https_token",
        credential_type="legacy_pat",
        secret="tok-abc",
        username="git",
    )

    env, cleanup = GitAuthService.build_git_environment(auth)
    try:
        assert "GIT_HTTP_LOW_SPEED_LIMIT" in env
        assert "GIT_HTTP_LOW_SPEED_TIME" in env
    finally:
        cleanup()
