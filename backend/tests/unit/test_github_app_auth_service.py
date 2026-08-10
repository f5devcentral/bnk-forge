"""Focused tests for GitHubAppAuthService helper behavior."""

from unittest.mock import MagicMock, patch

import pytest

from services.github_app_auth_service import GitHubAppAuthService


class TestNormalizeMetadata:
    def test_normalize_metadata_sets_default_api_base_url(self):
        metadata = {
            "github_app": {
                "app_id": "12345",
                "installation_id": "67890",
                "owner": "example-org",
                "repository": "infra-modules",
            }
        }

        normalized = GitHubAppAuthService.normalize_metadata(metadata)

        assert normalized["provider"] == "github"
        assert normalized["github_app"]["api_base_url"] == "https://api.github.com"


class TestValidateRequiredMetadata:
    def test_validate_required_metadata_raises_on_missing_fields(self):
        with pytest.raises(ValueError, match=r"Missing GitHub App credential metadata field\(s\):"):
            GitHubAppAuthService.validate_required_metadata({"github_app": {"app_id": "12345"}})


class TestAcquireInstallationToken:
    @patch("services.github_app_auth_service.GitHubAppAuthService._build_app_jwt", return_value="app-jwt")
    @patch("services.github_app_auth_service.requests.post")
    def test_acquire_installation_token_success(self, mock_post, _mock_jwt):
        response = MagicMock()
        response.status_code = 201
        response.content = b'{"token":"inst-token","expires_at":"2030-01-01T00:00:00Z"}'
        response.json.return_value = {
            "token": "inst-token",
            "expires_at": "2030-01-01T00:00:00Z",
        }
        mock_post.return_value = response

        token, expires_at = GitHubAppAuthService.acquire_installation_token(
            private_key="---KEY---",
            metadata={
                "github_app": {
                    "app_id": "12345",
                    "installation_id": "67890",
                    "owner": "example-org",
                    "repository": "infra-modules",
                }
            },
        )

        assert token == "inst-token"
        assert expires_at == "2030-01-01T00:00:00Z"

    @patch("services.github_app_auth_service.GitHubAppAuthService._build_app_jwt", return_value="app-jwt")
    @patch("services.github_app_auth_service.requests.post")
    def test_acquire_installation_token_fails_on_non_2xx(self, mock_post, _mock_jwt):
        response = MagicMock()
        response.status_code = 403
        response.text = "forbidden"
        response.content = b"forbidden"
        mock_post.return_value = response

        with pytest.raises(RuntimeError, match="installation token request failed"):
            GitHubAppAuthService.acquire_installation_token(
                private_key="---KEY---",
                metadata={
                    "github_app": {
                        "app_id": "12345",
                        "installation_id": "67890",
                        "owner": "example-org",
                        "repository": "infra-modules",
                    }
                },
            )

    @patch("services.github_app_auth_service.GitHubAppAuthService._build_app_jwt", return_value="app-jwt")
    @patch("services.github_app_auth_service.requests.post")
    def test_acquire_installation_token_fails_when_token_missing(self, mock_post, _mock_jwt):
        response = MagicMock()
        response.status_code = 201
        response.content = b'{"expires_at":"2030-01-01T00:00:00Z"}'
        response.json.return_value = {"expires_at": "2030-01-01T00:00:00Z"}
        mock_post.return_value = response

        with pytest.raises(RuntimeError, match="did not include token"):
            GitHubAppAuthService.acquire_installation_token(
                private_key="---KEY---",
                metadata={
                    "github_app": {
                        "app_id": "12345",
                        "installation_id": "67890",
                        "owner": "example-org",
                        "repository": "infra-modules",
                    }
                },
            )


class TestValidateRepositoryAccess:
    @patch("services.github_app_auth_service.requests.get")
    @patch("services.github_app_auth_service.GitHubAppAuthService.acquire_installation_token")
    def test_validate_repository_access_fails_on_repo_non_2xx(self, mock_acquire, mock_get):
        mock_acquire.return_value = ("inst-token", "2030-01-01T00:00:00Z")

        response = MagicMock()
        response.status_code = 404
        response.text = "not found"
        response.content = b"not found"
        mock_get.return_value = response

        with pytest.raises(RuntimeError, match="repository validation failed"):
            GitHubAppAuthService.validate_repository_access(
                private_key="---KEY---",
                metadata={
                    "github_app": {
                        "app_id": "12345",
                        "installation_id": "67890",
                        "owner": "example-org",
                        "repository": "infra-modules",
                    }
                },
            )
