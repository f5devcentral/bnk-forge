"""GitHub App installation-token acquisition + bounded source validation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)


class GitHubAppAuthService:
    """Provider helper for GitHub App credential flows."""

    DEFAULT_API_BASE_URL = "https://api.github.com"
    GITHUB_API_VERSION = "2022-11-28"

    @classmethod
    def normalize_metadata(cls, metadata: dict[str, Any] | None) -> dict[str, Any]:
        raw = metadata if isinstance(metadata, dict) else {}
        github_app = raw.get("github_app") if isinstance(raw.get("github_app"), dict) else {}

        app_id = cls._string_or_none(github_app.get("app_id") or raw.get("app_id"))
        installation_id = cls._string_or_none(github_app.get("installation_id") or raw.get("installation_id"))
        owner = cls._string_or_none(github_app.get("owner") or raw.get("owner"))
        repository = cls._string_or_none(github_app.get("repository") or raw.get("repository"))
        api_base_url = cls._normalize_api_base_url(github_app.get("api_base_url") or raw.get("api_base_url"))

        return {
            "provider": "github",
            "github_app": {
                "app_id": app_id,
                "installation_id": installation_id,
                "owner": owner,
                "repository": repository,
                "api_base_url": api_base_url,
            },
        }

    @classmethod
    def validate_required_metadata(cls, metadata: dict[str, Any] | None) -> dict[str, Any]:
        normalized = cls.normalize_metadata(metadata)
        github_app = normalized["github_app"]
        missing: list[str] = []
        for field in ("app_id", "installation_id", "owner", "repository"):
            if not github_app.get(field):
                missing.append(field)

        if missing:
            field_list = ", ".join(missing)
            raise ValueError(f"Missing GitHub App credential metadata field(s): {field_list}")

        return normalized

    @classmethod
    def build_scope(cls, metadata: dict[str, Any] | None) -> str:
        normalized = cls.normalize_metadata(metadata)
        github_app = normalized["github_app"]
        owner = github_app.get("owner") or "unknown"
        repository = github_app.get("repository") or "unknown"
        installation_id = github_app.get("installation_id") or "unknown"
        return f"github_app_installation:{installation_id}:repo:{owner}/{repository}"

    @classmethod
    def acquire_installation_token(
        cls,
        *,
        private_key: str,
        metadata: dict[str, Any] | None,
        timeout_seconds: int = 20,
    ) -> tuple[str, str | None]:
        normalized = cls.validate_required_metadata(metadata)
        github_app = normalized["github_app"]

        app_id = str(github_app["app_id"])
        installation_id = str(github_app["installation_id"])
        api_base_url = str(github_app["api_base_url"])

        app_jwt = cls._build_app_jwt(app_id=app_id, private_key=private_key)
        token_url = f"{api_base_url}/app/installations/{installation_id}/access_tokens"

        response = requests.post(
            token_url,
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": cls.GITHUB_API_VERSION,
            },
            timeout=timeout_seconds,
        )

        if response.status_code >= 400:
            text = (response.text or "").strip()
            raise RuntimeError(
                "GitHub App installation token request failed "
                f"(status={response.status_code}): {text[:300]}"
            )

        payload = response.json() if response.content else {}
        token = cls._string_or_none(payload.get("token"))
        expires_at = cls._string_or_none(payload.get("expires_at"))
        if not token:
            raise RuntimeError("GitHub App installation token response did not include token")

        return token, expires_at

    @classmethod
    def validate_repository_access(
        cls,
        *,
        private_key: str,
        metadata: dict[str, Any] | None,
        timeout_seconds: int = 20,
    ) -> dict[str, Any]:
        normalized = cls.validate_required_metadata(metadata)
        github_app = normalized["github_app"]
        owner = str(github_app["owner"])
        repository = str(github_app["repository"])
        api_base_url = str(github_app["api_base_url"])

        token, expires_at = cls.acquire_installation_token(
            private_key=private_key,
            metadata=normalized,
            timeout_seconds=timeout_seconds,
        )

        repo_url = f"{api_base_url}/repos/{owner}/{repository}"
        response = requests.get(
            repo_url,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": cls.GITHUB_API_VERSION,
            },
            timeout=timeout_seconds,
        )

        if response.status_code >= 400:
            text = (response.text or "").strip()
            raise RuntimeError(
                "GitHub App repository validation failed "
                f"(status={response.status_code}): {text[:300]}"
            )

        payload = response.json() if response.content else {}
        permissions = payload.get("permissions") if isinstance(payload.get("permissions"), dict) else {}

        return {
            "provider": "github",
            "owner": owner,
            "repository": repository,
            "installation_id": github_app["installation_id"],
            "app_id": github_app["app_id"],
            "api_base_url": api_base_url,
            "repository_private": bool(payload.get("private", False)),
            "repository_full_name": cls._string_or_none(payload.get("full_name")) or f"{owner}/{repository}",
            "permissions": permissions,
            "token_expires_at": expires_at,
        }

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    @classmethod
    def _normalize_api_base_url(cls, value: Any) -> str:
        text = cls._string_or_none(value)
        if not text:
            return cls.DEFAULT_API_BASE_URL
        return text.rstrip("/")

    @staticmethod
    def _build_app_jwt(*, app_id: str, private_key: str) -> str:
        try:
            import jwt as pyjwt
        except Exception as e:  # pragma: no cover - environment dependency
            raise RuntimeError("PyJWT is required for GitHub App authentication") from e

        now = datetime.now(UTC)
        payload = {
            "iat": int((now - timedelta(seconds=60)).timestamp()),
            "exp": int((now + timedelta(minutes=9)).timestamp()),
            "iss": app_id,
        }
        return str(pyjwt.encode(payload, private_key, algorithm="RS256"))
