from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.gitlab_auth_service import GitLabAuthService, GitLabValidationError


def test_validate_required_metadata_requires_shared_scope_for_group_token():
    with pytest.raises(ValueError, match="shared_scope_intent"):
        GitLabAuthService.validate_required_metadata(
            credential_type="gitlab_group_token",
            metadata={
                "gitlab": {
                    "project_path": "team/repo",
                    "group_path": "team",
                }
            },
            source_url="https://gitlab.example.com/team/repo.git",
        )


def test_validate_required_metadata_requires_deploy_username_for_deploy_token():
    with pytest.raises(ValueError, match="deploy_username"):
        GitLabAuthService.validate_required_metadata(
            credential_type="gitlab_deploy_token",
            metadata={"gitlab": {"project_path": "team/repo"}},
            source_url="https://gitlab.example.com/team/repo.git",
        )


def test_build_scope_for_project_group_and_deploy_tokens():
    project_scope = GitLabAuthService.build_scope(
        credential_type="gitlab_project_token",
        metadata={"gitlab": {"project_path": "team/repo"}},
        source_url="https://gitlab.example.com/team/repo.git",
    )
    group_scope = GitLabAuthService.build_scope(
        credential_type="gitlab_group_token",
        metadata={
            "gitlab": {
                "project_path": "team/repo",
                "group_path": "team",
                "shared_scope_intent": True,
            }
        },
        source_url="https://gitlab.example.com/team/repo.git",
    )
    deploy_scope = GitLabAuthService.build_scope(
        credential_type="gitlab_deploy_token",
        metadata={
            "gitlab": {
                "project_path": "team/repo",
                "deploy_username": "gitlab+deploy-token-1",
            }
        },
        source_url="https://gitlab.example.com/team/repo.git",
    )

    assert project_scope == "gitlab_project:gitlab.example.com:repo:team/repo"
    assert group_scope == "gitlab_group:gitlab.example.com:group:team:repo:team/repo"
    assert deploy_scope == "gitlab_deploy_token:gitlab.example.com:repo:team/repo"


@patch("services.gitlab_auth_service.requests.get")
def test_validate_repository_access_uses_private_token_header_for_project_token(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"{}"
    mock_response.json.return_value = {
        "id": 7,
        "visibility": "private",
        "default_branch": "main",
        "namespace": {"full_path": "team"},
    }
    mock_get.return_value = mock_response

    result = GitLabAuthService.validate_repository_access(
        credential_type="gitlab_project_token",
        token="glpat-abc",
        metadata={"gitlab": {"project_path": "team/repo"}},
        source_url="https://gitlab.example.com/team/repo.git",
    )

    assert result["project_id"] == 7
    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["PRIVATE-TOKEN"] == "glpat-abc"
    assert kwargs["auth"] is None


@patch("services.gitlab_auth_service.requests.get")
def test_validate_repository_access_uses_basic_auth_for_deploy_token(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"{}"
    mock_response.json.return_value = {
        "id": 9,
        "visibility": "private",
        "default_branch": "main",
        "namespace": {"full_path": "team"},
    }
    mock_get.return_value = mock_response

    result = GitLabAuthService.validate_repository_access(
        credential_type="gitlab_deploy_token",
        token="deploy-secret",
        metadata={
            "gitlab": {
                "project_path": "team/repo",
                "deploy_username": "gitlab+deploy-token-1",
            }
        },
        source_url="https://gitlab.example.com/team/repo.git",
    )

    assert result["project_id"] == 9
    _, kwargs = mock_get.call_args
    assert kwargs["auth"] == ("gitlab+deploy-token-1", "deploy-secret")
    assert kwargs["headers"] == {}


@patch("services.gitlab_auth_service.requests.get")
def test_validate_repository_access_maps_403_to_scope_failure(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "forbidden"
    mock_response.content = b""
    mock_get.return_value = mock_response

    with pytest.raises(GitLabValidationError) as exc_info:
        GitLabAuthService.validate_repository_access(
            credential_type="gitlab_group_token",
            token="glpat-abc",
            metadata={
                "gitlab": {
                    "project_path": "team/repo",
                    "group_path": "team",
                    "shared_scope_intent": True,
                }
            },
            source_url="https://gitlab.example.com/team/repo.git",
        )

    err = exc_info.value
    assert err.error_kind == "authz_scope_failure"
    assert err.status_code == 403


@patch("services.gitlab_auth_service.requests.get")
def test_validate_repository_access_group_token_rejects_non_matching_namespace(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"{}"
    mock_response.json.return_value = {
        "id": 88,
        "visibility": "private",
        "default_branch": "main",
        "namespace": {"full_path": "other-team/subgroup"},
    }
    mock_get.return_value = mock_response

    with pytest.raises(GitLabValidationError) as exc_info:
        GitLabAuthService.validate_repository_access(
            credential_type="gitlab_group_token",
            token="glpat-abc",
            metadata={
                "gitlab": {
                    "project_path": "team/repo",
                    "group_path": "team/platform",
                    "shared_scope_intent": True,
                }
            },
            source_url="https://gitlab.example.com/team/repo.git",
        )

    err = exc_info.value
    assert err.error_kind == "authz_scope_failure"
    assert err.status_code == 403
    assert "group_path" in str(err)


@patch("services.gitlab_auth_service.requests.get")
def test_validate_repository_access_group_token_accepts_group_ancestor_namespace(mock_get):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"{}"
    mock_response.json.return_value = {
        "id": 99,
        "visibility": "private",
        "default_branch": "main",
        "namespace": {"full_path": "team/platform/subgroup"},
    }
    mock_get.return_value = mock_response

    result = GitLabAuthService.validate_repository_access(
        credential_type="gitlab_group_token",
        token="glpat-abc",
        metadata={
            "gitlab": {
                "project_path": "team/platform/subgroup/repo",
                "group_path": "team/platform",
                "shared_scope_intent": True,
            }
        },
        source_url="https://gitlab.example.com/team/platform/subgroup/repo.git",
    )

    assert result["project_id"] == 99
    assert result["namespace_full_path"] == "team/platform/subgroup"
