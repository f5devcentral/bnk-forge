"""GitLab service-token normalization + bounded repository validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus, urlsplit

import requests


@dataclass
class GitLabValidationError(RuntimeError):
    error_kind: str
    guidance: str
    status_code: int | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.status_code is None:
            return f"{self.error_kind}: {self.guidance}"
        return f"{self.error_kind} (status={self.status_code}): {self.guidance}"


class GitLabAuthService:
    """Provider helper for GitLab project/group/deploy token flows."""

    SUPPORTED_CREDENTIAL_TYPES = {
        "gitlab_project_token",
        "gitlab_group_token",
        "gitlab_deploy_token",
    }

    @classmethod
    def normalize_metadata(
        cls,
        *,
        credential_type: str,
        metadata: dict[str, Any] | None,
        source_url: str,
    ) -> dict[str, Any]:
        if credential_type not in cls.SUPPORTED_CREDENTIAL_TYPES:
            return metadata if isinstance(metadata, dict) else {}

        raw = metadata if isinstance(metadata, dict) else {}
        gitlab = raw.get("gitlab") if isinstance(raw.get("gitlab"), dict) else {}

        parsed_url = urlsplit(source_url)
        host = cls._string_or_none(gitlab.get("host") or raw.get("host") or parsed_url.netloc)
        scheme = parsed_url.scheme or "https"
        api_base_url = cls._normalize_api_base_url(
            gitlab.get("api_base_url") or raw.get("api_base_url"),
            scheme=scheme,
            host=host,
        )
        project_path = cls._normalize_project_path(
            gitlab.get("project_path") or raw.get("project_path") or parsed_url.path
        )
        group_path = cls._normalize_project_path(gitlab.get("group_path") or raw.get("group_path"))
        deploy_username = cls._string_or_none(
            gitlab.get("deploy_username")
            or gitlab.get("username")
            or raw.get("deploy_username")
            or raw.get("username")
        )

        token_class = {
            "gitlab_project_token": "project_access_token",
            "gitlab_group_token": "group_access_token",
            "gitlab_deploy_token": "deploy_token",
        }[credential_type]

        shared_scope_intent = bool(
            gitlab.get("shared_scope_intent")
            if isinstance(gitlab.get("shared_scope_intent"), bool)
            else raw.get("shared_scope_intent")
        )

        return {
            "provider": "gitlab",
            "gitlab": {
                "token_class": token_class,
                "host": host,
                "api_base_url": api_base_url,
                "project_path": project_path,
                "group_path": group_path,
                "deploy_username": deploy_username,
                "shared_scope_intent": shared_scope_intent,
            },
        }

    @classmethod
    def validate_required_metadata(
        cls,
        *,
        credential_type: str,
        metadata: dict[str, Any] | None,
        source_url: str,
    ) -> dict[str, Any]:
        normalized = cls.normalize_metadata(
            credential_type=credential_type,
            metadata=metadata,
            source_url=source_url,
        )

        gitlab = normalized.get("gitlab") if isinstance(normalized.get("gitlab"), dict) else {}

        missing: list[str] = []
        for field in ("host", "api_base_url", "project_path"):
            if not gitlab.get(field):
                missing.append(field)

        if credential_type == "gitlab_group_token":
            if not gitlab.get("group_path"):
                missing.append("group_path")
            if not gitlab.get("shared_scope_intent"):
                raise ValueError(
                    "GitLab group token requires explicit shared_scope_intent=true "
                    "to acknowledge multi-repo/shared-scope usage"
                )

        if credential_type == "gitlab_deploy_token" and not gitlab.get("deploy_username"):
            missing.append("deploy_username")

        if missing:
            field_list = ", ".join(sorted(set(missing)))
            raise ValueError(f"Missing GitLab credential metadata field(s): {field_list}")

        return normalized

    @classmethod
    def build_scope(
        cls,
        *,
        credential_type: str,
        metadata: dict[str, Any] | None,
        source_url: str,
    ) -> str:
        normalized = cls.validate_required_metadata(
            credential_type=credential_type,
            metadata=metadata,
            source_url=source_url,
        )
        gitlab = normalized["gitlab"]
        host = gitlab["host"]
        project_path = gitlab["project_path"]

        if credential_type == "gitlab_group_token":
            group_path = gitlab["group_path"]
            return f"gitlab_group:{host}:group:{group_path}:repo:{project_path}"
        if credential_type == "gitlab_deploy_token":
            return f"gitlab_deploy_token:{host}:repo:{project_path}"
        return f"gitlab_project:{host}:repo:{project_path}"

    @classmethod
    def validate_repository_access(
        cls,
        *,
        credential_type: str,
        token: str,
        metadata: dict[str, Any] | None,
        source_url: str,
        timeout_seconds: int = 20,
    ) -> dict[str, Any]:
        normalized = cls.validate_required_metadata(
            credential_type=credential_type,
            metadata=metadata,
            source_url=source_url,
        )
        gitlab = normalized["gitlab"]

        project_path = gitlab["project_path"]
        api_base_url = gitlab["api_base_url"]
        project_ref = quote_plus(project_path)
        project_url = f"{api_base_url}/projects/{project_ref}"

        headers: dict[str, str] = {}
        auth = None
        if credential_type in {"gitlab_project_token", "gitlab_group_token"}:
            headers["PRIVATE-TOKEN"] = token
        else:
            auth = (str(gitlab["deploy_username"]), token)

        response = requests.get(
            project_url,
            headers=headers,
            auth=auth,
            timeout=timeout_seconds,
        )

        if response.status_code >= 400:
            raise cls._build_validation_error(
                credential_type=credential_type,
                status_code=response.status_code,
                response_text=response.text,
            )

        payload = response.json() if response.content else {}
        namespace = payload.get("namespace") if isinstance(payload.get("namespace"), dict) else {}
        namespace_full_path = cls._normalize_project_path(namespace.get("full_path"))

        if credential_type == "gitlab_group_token":
            declared_group_path = cls._normalize_project_path(gitlab.get("group_path"))
            if not declared_group_path or not namespace_full_path or not cls._group_scope_matches_namespace(
                declared_group_path=declared_group_path,
                namespace_full_path=namespace_full_path,
            ):
                raise GitLabValidationError(
                    error_kind="authz_scope_failure",
                    guidance=(
                        "GitLab group token validation failed: declared group_path does not match "
                        "the validated project's namespace ancestry"
                    ),
                    status_code=403,
                )

        return {
            "provider": "gitlab",
            "host": gitlab["host"],
            "project_path": project_path,
            "group_path": gitlab.get("group_path"),
            "project_id": payload.get("id"),
            "project_visibility": payload.get("visibility"),
            "default_branch": payload.get("default_branch"),
            "namespace_full_path": namespace_full_path,
            "token_class": gitlab["token_class"],
        }

    @classmethod
    def _build_validation_error(
        cls,
        *,
        credential_type: str,
        status_code: int,
        response_text: str,
    ) -> GitLabValidationError:
        _ = response_text
        if status_code == 404:
            return GitLabValidationError(
                error_kind="repo_or_ref_not_found",
                guidance="GitLab project was not found or path metadata is incorrect",
                status_code=status_code,
            )

        if status_code == 401:
            return GitLabValidationError(
                error_kind="auth_failure",
                guidance="GitLab credential is invalid, expired, or not accepted for this host",
                status_code=status_code,
            )

        if status_code == 403:
            guidance = "GitLab credential is authenticated but lacks repository read scope"
            if credential_type == "gitlab_group_token":
                guidance = (
                    "GitLab group token is authenticated but lacks access to this project; "
                    "verify group_path/shared-scope intent and repository membership"
                )
            return GitLabValidationError(
                error_kind="authz_scope_failure",
                guidance=guidance,
                status_code=status_code,
            )

        if 500 <= status_code < 600:
            return GitLabValidationError(
                error_kind="remote_service_error",
                guidance="GitLab API returned a server error",
                status_code=status_code,
            )

        return GitLabValidationError(
            error_kind="unknown_http_error",
            guidance=f"GitLab API returned status {status_code}",
            status_code=status_code,
        )

    @staticmethod
    def _string_or_none(value: Any) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None

    @classmethod
    def _normalize_api_base_url(cls, value: Any, *, scheme: str, host: str | None) -> str:
        text = cls._string_or_none(value)
        if text:
            return text.rstrip("/")
        if host:
            return f"{scheme}://{host}/api/v4"
        return "https://gitlab.com/api/v4"

    @classmethod
    def _normalize_project_path(cls, value: Any) -> str | None:
        text = cls._string_or_none(value)
        if not text:
            return None

        path = text.strip().strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return path or None

    @staticmethod
    def _group_scope_matches_namespace(*, declared_group_path: str, namespace_full_path: str) -> bool:
        if namespace_full_path == declared_group_path:
            return True
        return namespace_full_path.startswith(f"{declared_group_path}/")
