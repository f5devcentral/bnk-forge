"""
Module Source Service — DB and business logic for module source management.

Extracted from routes/module_sources.py to separate HTTP handling from domain logic.

Covers:
- Module source CRUD (list, get, create, update, delete)
- Source module listing
- Source sync triggering
"""

import logging
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from core.cache import invalidate_cache
from core.encryption import decrypt_value_or_none, encrypt_value
from core.errors import BadRequestError, ConflictError, NotFoundError, ServiceError
from models import BlueprintSource, ModuleLibrary, ModuleSource, ProjectModule
from services.audit_service import create_audit_log
from services.blueprint_catalog_service import BlueprintCatalogService
from services.blueprint_sync_service import BlueprintSyncService
from services.defaults_service import get_default, set_default
from services.git_auth_service import GitAuthService
from services.github_app_auth_service import GitHubAppAuthService
from services.gitlab_auth_service import GitLabAuthService, GitLabValidationError

logger = logging.getLogger(__name__)

ALLOWED_CREDENTIAL_TYPES = {
    "none",
    "github_app",
    "gitlab_project_token",
    "gitlab_group_token",
    "gitlab_deploy_token",
    "oauth_token",
    "ssh_deploy_key",
    "service_account_token",
    "legacy_pat",
}

ALLOWED_CREDENTIAL_VALIDATION_STATUSES = {
    "unknown",
    "valid",
    "invalid",
    "expired",
    "error",
}

OAUTH_SUPPORTED_PROVIDERS = {"github", "gitlab"}

_LEGACY_AUTH_TO_CREDENTIAL_TYPE = {
    "none": "none",
    "token": "legacy_pat",
    "ssh": "ssh_deploy_key",
    "basic": "service_account_token",
}

_CREDENTIAL_TYPE_TO_LEGACY_AUTH = {
    "none": "none",
    "ssh_deploy_key": "ssh",
    "github_app": "token",
    "gitlab_project_token": "token",
    "gitlab_group_token": "token",
    "gitlab_deploy_token": "token",
    "oauth_token": "token",
    "service_account_token": "token",
    "legacy_pat": "token",
}


class ModuleSourceService:
    """Service layer for module source operations."""

    def __init__(self, db: Session):
        self.db = db

    # ================================================================
    # Lookups
    # ================================================================

    def _get_source(self, source_id: int) -> ModuleSource:
        source = self.db.query(ModuleSource).filter(ModuleSource.id == source_id).first()
        if not source:
            raise NotFoundError("module_source", source_id)
        return source

    def _validate_credential_type(self, credential_type: str | None) -> None:
        if credential_type is None:
            return
        if credential_type not in ALLOWED_CREDENTIAL_TYPES:
            raise BadRequestError(
                f"Unsupported credential_type '{credential_type}'",
                code="INVALID_CREDENTIAL_TYPE",
            )

    def _validate_credential_validation_status(self, validation_status: str | None) -> None:
        if validation_status is None:
            return
        if validation_status not in ALLOWED_CREDENTIAL_VALIDATION_STATUSES:
            raise BadRequestError(
                f"Unsupported credential_validation_status '{validation_status}'",
                code="INVALID_CREDENTIAL_VALIDATION_STATUS",
            )

    def _field_provided(self, source_data: Any, field_name: str) -> bool:
        model_fields_set = getattr(source_data, "model_fields_set", None)
        if model_fields_set is not None:
            return field_name in model_fields_set

        legacy_fields_set = getattr(source_data, "__fields_set__", None)
        if legacy_fields_set is not None:
            return field_name in legacy_fields_set

        return getattr(source_data, field_name, None) is not None

    def _derive_credential_type(self, source: ModuleSource) -> str:
        if source.credential_type in ALLOWED_CREDENTIAL_TYPES:
            return str(source.credential_type)

        legacy_auth_type = (source.auth_type or "none").lower()
        if legacy_auth_type in _LEGACY_AUTH_TO_CREDENTIAL_TYPE:
            return _LEGACY_AUTH_TO_CREDENTIAL_TYPE[legacy_auth_type]

        if source.auth_token_encrypted:
            return "legacy_pat"

        return "none"

    def _resolve_credential_fields(self, source_data, existing_source: ModuleSource | None = None) -> tuple[str, str]:
        requested_credential_type = getattr(source_data, "credential_type", None)
        requested_auth_type = getattr(source_data, "auth_type", None)
        has_secret_input = getattr(source_data, "auth_token", None) is not None

        if requested_credential_type is not None:
            self._validate_credential_type(requested_credential_type)

        if requested_credential_type is not None:
            credential_type = requested_credential_type
        elif requested_auth_type is not None:
            credential_type = _LEGACY_AUTH_TO_CREDENTIAL_TYPE.get(requested_auth_type, "legacy_pat")
        elif has_secret_input:
            credential_type = "legacy_pat"
        elif existing_source is not None:
            credential_type = self._derive_credential_type(existing_source)
        else:
            credential_type = "none"

        if requested_auth_type is not None:
            auth_type = requested_auth_type
        elif has_secret_input:
            auth_type = "token"
        elif existing_source is not None:
            auth_type = existing_source.auth_type or _CREDENTIAL_TYPE_TO_LEGACY_AUTH.get(credential_type, "none")
        else:
            auth_type = _CREDENTIAL_TYPE_TO_LEGACY_AUTH.get(credential_type, "none")

        return credential_type, auth_type

    @staticmethod
    def _infer_git_provider(source_url: str) -> str:
        host = (urlsplit(source_url).hostname or "").lower()
        if host == "github.com" or host.endswith(".github.com"):
            return "github"
        if host == "gitlab.com" or host.startswith("gitlab.") or ".gitlab." in host:
            return "gitlab"
        return "generic_git"

    @staticmethod
    def _parse_repo_scope_path(source_url: str) -> str | None:
        path = (urlsplit(source_url).path or "").strip().strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return path or None

    def _oauth_recommended_defaults(self, provider: str) -> list[str]:
        if provider == "github":
            return ["github_app", "ssh_deploy_key"]
        if provider == "gitlab":
            return ["gitlab_project_token", "gitlab_group_token", "ssh_deploy_key"]
        return []

    def _build_credential_recommendation(self, source: ModuleSource, credential_type: str) -> dict[str, Any] | None:
        if credential_type != "oauth_token":
            return None

        metadata = source.credential_metadata if isinstance(source.credential_metadata, dict) else {}
        oauth_obj = metadata.get("oauth") if isinstance(metadata.get("oauth"), dict) else {}
        provider = str(
            oauth_obj.get("provider")
            or metadata.get("provider")
            or self._infer_git_provider(source.url)
        ).strip().lower() or self._infer_git_provider(source.url)

        if provider not in OAUTH_SUPPORTED_PROVIDERS:
            return None

        return {
            "supported": True,
            "is_recommended_default": False,
            "recommendation_tier": "supported_non_default",
            "recommended_default_credential_types": self._oauth_recommended_defaults(provider),
            "reason": "OAuth is supported as an explicit choice, but provider-native/non-human credentials remain preferred defaults.",
        }

    def _serialize_source(self, source: ModuleSource) -> dict[str, Any]:
        credential_type = self._derive_credential_type(source)
        default_url = self._get_default_module_source_url()
        default_ref = self._get_default_module_source_ref()
        return {
            "id": source.id,
            "name": source.name,
            "source_type": source.source_type,
            "url": source.url,
            "branch": source.branch,
            "git_ref": source.git_ref,
            "auth_type": source.auth_type,
            "credential_type": credential_type,
            "credential_scope": source.credential_scope,
            "credential_capabilities": source.credential_capabilities,
            "credential_metadata": source.credential_metadata,
            "credential_recommendation": self._build_credential_recommendation(source, credential_type),
            "credential_expires_at": source.credential_expires_at,
            "credential_last_rotated_at": source.credential_last_rotated_at,
            "credential_validation_status": source.credential_validation_status,
            "credential_last_validated_at": source.credential_last_validated_at,
            "credential_validation_error": source.credential_validation_error,
            "has_secret": bool(source.auth_token_encrypted),
            "last_synced_at": source.last_synced_at,
            "sync_status": source.sync_status,
            "sync_error": source.sync_error,
            "module_count": source.module_count,
            "is_active": source.is_active,
            "is_default": (
                source.source_type == "git"
                and source.url == default_url
                and (source.git_ref or source.branch or "main") == default_ref
            ),
            "auto_sync": source.auto_sync,
            "sync_interval_hours": source.sync_interval_hours,
            "description": source.description,
            "created_at": source.created_at,
            "updated_at": source.updated_at,
        }

    def _get_default_module_source_url(self) -> str | None:
        value = get_default(self.db, "module_library.git_url")
        return str(value).strip() if value else None

    def _get_default_module_source_ref(self) -> str:
        value = get_default(self.db, "module_library.git_ref")
        return str(value).strip() if value else "main"

    def _set_default_module_source(self, source: ModuleSource) -> None:
        if source.source_type != "git":
            raise BadRequestError("Only git module sources can be configured as default", code="DEFAULT_SOURCE_REQUIRES_GIT")

        set_default(self.db, "module_library.git_url", source.url)
        set_default(self.db, "module_library.git_ref", source.git_ref or source.branch or "main")

    def _create_module_source_audit(
        self,
        *,
        action: str,
        source: ModuleSource,
        status: str,
        details: dict[str, Any],
        user_obj: Any | None = None,
    ) -> None:
        create_audit_log(
            self.db,
            action=action,
            resource_type="module_source",
            user=getattr(user_obj, "username", None),
            user_id=getattr(user_obj, "id", None),
            resource_id=str(source.id),
            resource_name=source.name,
            status=status,
            details=details,
        )

    @staticmethod
    def _parse_datetime_or_none(value: str | None) -> datetime | None:
        if not value:
            return None
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)

    def _normalize_github_app_fields(
        self,
        *,
        credential_type: str,
        credential_metadata: Any,
        credential_scope: str | None,
        credential_capabilities: list[str] | None,
    ) -> tuple[Any, str | None, list[str] | None]:
        if credential_type != "github_app":
            return credential_metadata, credential_scope, credential_capabilities

        try:
            normalized_metadata = GitHubAppAuthService.validate_required_metadata(credential_metadata)
        except ValueError as e:
            raise BadRequestError(str(e), code="INVALID_GITHUB_APP_METADATA") from e

        derived_scope = GitHubAppAuthService.build_scope(normalized_metadata)
        capabilities = credential_capabilities or ["repo:read"]
        return normalized_metadata, derived_scope, capabilities

    def _normalize_gitlab_fields(
        self,
        *,
        credential_type: str,
        source_url: str,
        credential_metadata: Any,
        credential_scope: str | None,
        credential_capabilities: list[str] | None,
    ) -> tuple[Any, str | None, list[str] | None]:
        if credential_type not in GitLabAuthService.SUPPORTED_CREDENTIAL_TYPES:
            return credential_metadata, credential_scope, credential_capabilities

        try:
            normalized_metadata = GitLabAuthService.validate_required_metadata(
                credential_type=credential_type,
                metadata=credential_metadata,
                source_url=source_url,
            )
        except ValueError as e:
            raise BadRequestError(str(e), code="INVALID_GITLAB_METADATA") from e

        derived_scope = GitLabAuthService.build_scope(
            credential_type=credential_type,
            metadata=normalized_metadata,
            source_url=source_url,
        )
        capabilities = credential_capabilities or ["repo:read"]
        return normalized_metadata, derived_scope, capabilities

    def _normalize_ssh_fields(
        self,
        *,
        credential_type: str,
        source_url: str,
        credential_metadata: Any,
        credential_scope: str | None,
        credential_capabilities: list[str] | None,
        require_known_hosts: bool,
    ) -> tuple[Any, str | None, list[str] | None]:
        if credential_type != "ssh_deploy_key":
            return credential_metadata, credential_scope, credential_capabilities

        metadata_obj: dict[str, Any] = credential_metadata if isinstance(credential_metadata, dict) else {}
        ssh_obj: dict[str, Any] = metadata_obj.get("ssh") if isinstance(metadata_obj.get("ssh"), dict) else {}

        known_hosts_value = ssh_obj.get("known_hosts")
        known_hosts = GitAuthService.normalize_known_hosts(known_hosts_value)
        if require_known_hosts and not known_hosts:
            raise BadRequestError(
                "SSH credential metadata must include non-empty ssh.known_hosts trust material",
                code="INVALID_SSH_METADATA",
            )

        parsed_target = GitAuthService.parse_ssh_git_target(source_url)
        if not parsed_target:
            raise BadRequestError(
                "SSH deploy-key sources require an SSH Git URL with a resolvable host",
                code="INVALID_SSH_SOURCE_URL",
            )
        host, repo_path = parsed_target

        normalized_metadata = {
            "provider": "ssh",
            "ssh": {
                "host": host,
                "repo_path": repo_path,
                "known_hosts": known_hosts,
                "host_key_policy": "strict",
                "read_only": True,
            },
        }
        normalized_scope = f"ssh_host:{host}:repo:{repo_path}" if repo_path else f"ssh_host:{host}"

        capabilities = list(credential_capabilities or ["repo:read"])
        lowered = [cap.lower() for cap in capabilities]
        if any("write" in cap for cap in lowered):
            raise BadRequestError(
                "SSH deploy-key credentials must remain read-only",
                code="INVALID_CREDENTIAL_CAPABILITIES",
            )

        if "repo:read" not in capabilities:
            capabilities = ["repo:read", *capabilities]

        # de-duplicate while preserving order
        deduped_caps: list[str] = []
        for cap in capabilities:
            if cap not in deduped_caps:
                deduped_caps.append(cap)

        return normalized_metadata, normalized_scope, deduped_caps

    def _normalize_oauth_fields(
        self,
        *,
        credential_type: str,
        source_url: str,
        credential_metadata: Any,
        credential_scope: str | None,
        credential_capabilities: list[str] | None,
    ) -> tuple[Any, str | None, list[str] | None]:
        if credential_type != "oauth_token":
            return credential_metadata, credential_scope, credential_capabilities

        metadata_obj: dict[str, Any] = credential_metadata if isinstance(credential_metadata, dict) else {}
        oauth_obj: dict[str, Any] = metadata_obj.get("oauth") if isinstance(metadata_obj.get("oauth"), dict) else {}

        provider_raw = str(oauth_obj.get("provider") or metadata_obj.get("provider") or "").strip().lower()
        if not provider_raw:
            raise BadRequestError(
                "OAuth credential metadata must include oauth.provider",
                code="INVALID_OAUTH_METADATA",
            )
        provider = provider_raw
        if provider not in OAUTH_SUPPORTED_PROVIDERS:
            raise BadRequestError(
                f"OAuth provider '{provider}' is not supported; eligible providers are github and gitlab",
                code="UNSUPPORTED_OAUTH_PROVIDER",
            )

        principal = str(
            oauth_obj.get("principal")
            or oauth_obj.get("subject")
            or metadata_obj.get("principal")
            or metadata_obj.get("subject")
            or ""
        ).strip() or None
        if not principal:
            raise BadRequestError(
                "OAuth credential metadata must include oauth.principal",
                code="INVALID_OAUTH_METADATA",
            )
        issuer = str(oauth_obj.get("issuer") or metadata_obj.get("issuer") or "").strip() or None

        raw_scopes = oauth_obj.get("scopes") if oauth_obj.get("scopes") is not None else metadata_obj.get("scopes")
        if isinstance(raw_scopes, str):
            scopes = [scope for scope in raw_scopes.split() if scope]
        elif isinstance(raw_scopes, list):
            scopes = [str(scope).strip() for scope in raw_scopes if str(scope).strip()]
        else:
            scopes = []
        if not scopes:
            raise BadRequestError(
                "OAuth credential metadata must include non-empty oauth.scopes",
                code="INVALID_OAUTH_METADATA",
            )

        normalized_scope = credential_scope
        if normalized_scope is None:
            scope_path = self._parse_repo_scope_path(source_url)
            normalized_scope = f"oauth:{provider}:repo:{scope_path}" if scope_path else f"oauth:{provider}"

        capabilities = list(credential_capabilities or ["repo:read"])
        if "repo:read" not in capabilities:
            capabilities = ["repo:read", *capabilities]

        deduped_caps: list[str] = []
        for cap in capabilities:
            if cap not in deduped_caps:
                deduped_caps.append(cap)

        normalized_metadata = {
            "provider": provider,
            "oauth": {
                "provider": provider,
                "token_class": "oauth_user_token",
                "issuer": issuer,
                "principal": principal,
                "scopes": scopes,
                "supported_choice": True,
                "recommended_default": False,
                "recommendation_tier": "supported_non_default",
                "recommended_default_credential_types": self._oauth_recommended_defaults(provider),
            },
        }
        return normalized_metadata, normalized_scope, deduped_caps

    # ================================================================
    # CRUD
    # ================================================================

    def list_sources(self, source_type: str | None = None,
                     is_active: bool | None = None) -> list[dict[str, Any]]:
        """List all module sources with optional filters."""
        query = self.db.query(ModuleSource)
        if source_type:
            query = query.filter(ModuleSource.source_type == source_type)
        if is_active is not None:
            query = query.filter(ModuleSource.is_active == is_active)
        rows = query.order_by(ModuleSource.created_at.desc()).all()
        return [self._serialize_source(row) for row in rows]

    def get_source(self, source_id: int) -> dict[str, Any]:
        """Get a specific module source by ID."""
        return self._serialize_source(self._get_source(source_id))

    def create_source(self, source_data) -> dict[str, Any]:
        """Create a new module source. Auth tokens are encrypted before storage."""
        # Duplicate name check
        existing = self.db.query(ModuleSource).filter(
            ModuleSource.name == source_data.name
        ).first()
        if existing:
            raise ConflictError("module_source", f"Module source '{source_data.name}' already exists")

        if source_data.source_type not in ['git', 'registry', 'builtin']:
            raise BadRequestError("source_type must be 'git', 'registry', or 'builtin'",
                                  code="INVALID_SOURCE_TYPE")

        auth_token_encrypted = None
        if source_data.auth_token:
            auth_token_encrypted = encrypt_value(source_data.auth_token)

        credential_type, auth_type = self._resolve_credential_fields(source_data)

        credential_validation_status = getattr(source_data, "credential_validation_status", None)
        self._validate_credential_validation_status(credential_validation_status)
        if credential_validation_status is None:
            credential_validation_status = "unknown"

        credential_metadata, credential_scope, credential_capabilities = self._normalize_github_app_fields(
            credential_type=credential_type,
            credential_metadata=getattr(source_data, "credential_metadata", None),
            credential_scope=getattr(source_data, "credential_scope", None),
            credential_capabilities=getattr(source_data, "credential_capabilities", None),
        )
        credential_metadata, credential_scope, credential_capabilities = self._normalize_gitlab_fields(
            credential_type=credential_type,
            source_url=source_data.url,
            credential_metadata=credential_metadata,
            credential_scope=credential_scope,
            credential_capabilities=credential_capabilities,
        )
        credential_metadata, credential_scope, credential_capabilities = self._normalize_ssh_fields(
            credential_type=credential_type,
            source_url=source_data.url,
            credential_metadata=credential_metadata,
            credential_scope=credential_scope,
            credential_capabilities=credential_capabilities,
            require_known_hosts=True,
        )
        credential_metadata, credential_scope, credential_capabilities = self._normalize_oauth_fields(
            credential_type=credential_type,
            source_url=source_data.url,
            credential_metadata=credential_metadata,
            credential_scope=credential_scope,
            credential_capabilities=credential_capabilities,
        )

        source = ModuleSource(
            name=source_data.name,
            source_type=source_data.source_type,
            url=source_data.url,
            branch=source_data.branch,
            git_ref=source_data.git_ref,
            auth_type=auth_type,
            auth_token_encrypted=auth_token_encrypted,
            credential_type=credential_type,
            credential_scope=credential_scope,
            credential_capabilities=credential_capabilities,
            credential_metadata=credential_metadata,
            credential_expires_at=getattr(source_data, "credential_expires_at", None),
            credential_last_rotated_at=getattr(source_data, "credential_last_rotated_at", None),
            credential_validation_status=credential_validation_status,
            credential_last_validated_at=getattr(source_data, "credential_last_validated_at", None),
            credential_validation_error=getattr(source_data, "credential_validation_error", None),
            auto_sync=source_data.auto_sync,
            sync_interval_hours=source_data.sync_interval_hours,
            description=source_data.description,
            sync_status='pending'
        )

        self.db.add(source)
        self.db.flush()
        if getattr(source_data, "make_default", False):
            self._set_default_module_source(source)
        self.db.refresh(source)

        if source.source_type == 'git':
            try:
                from services.module_sync_service import ModuleSyncService

                ModuleSyncService(self.db).sync_git_source(source)
                self._auto_sync_blueprints_for_git_source(source)
                self.db.refresh(source)
            except Exception as exc:
                logger.warning("Initial sync failed for new module source %s: %s", source.name, exc)

        logger.info(f"Created module source: {source.name} ({source.source_type})")
        return self._serialize_source(source)

    def update_source(self, source_id: int, source_data) -> dict[str, Any]:
        """Update an existing module source."""
        source = self._get_source(source_id)

        if source.source_type == 'builtin':
            raise BadRequestError("Cannot modify built-in module source",
                                  code="BUILTIN_SOURCE_IMMUTABLE")

        if source_data.name is not None:
            existing = self.db.query(ModuleSource).filter(
                ModuleSource.name == source_data.name,
                ModuleSource.id != source_id
            ).first()
            if existing:
                raise ConflictError("module_source",
                                    f"Module source '{source_data.name}' already exists")
            source.name = source_data.name

        if source_data.url is not None:
            source.url = source_data.url
        if source_data.branch is not None:
            source.branch = source_data.branch
        if source_data.git_ref is not None:
            source.git_ref = source_data.git_ref
        credential_type, auth_type = self._resolve_credential_fields(source_data, existing_source=source)
        source.auth_type = auth_type
        source.credential_type = credential_type

        if credential_type == "github_app":
            effective_metadata = (
                source_data.credential_metadata
                if self._field_provided(source_data, "credential_metadata")
                else source.credential_metadata
            )
            effective_capabilities = (
                source_data.credential_capabilities
                if self._field_provided(source_data, "credential_capabilities")
                else source.credential_capabilities
            )
            normalized_metadata, derived_scope, normalized_capabilities = self._normalize_github_app_fields(
                credential_type=credential_type,
                credential_metadata=effective_metadata,
                credential_scope=source.credential_scope,
                credential_capabilities=effective_capabilities,
            )
            source.credential_metadata = normalized_metadata
            source.credential_scope = derived_scope
            source.credential_capabilities = normalized_capabilities

        if credential_type in GitLabAuthService.SUPPORTED_CREDENTIAL_TYPES:
            effective_metadata = (
                source_data.credential_metadata
                if self._field_provided(source_data, "credential_metadata")
                else source.credential_metadata
            )
            effective_capabilities = (
                source_data.credential_capabilities
                if self._field_provided(source_data, "credential_capabilities")
                else source.credential_capabilities
            )
            effective_url = source_data.url if source_data.url is not None else source.url
            normalized_metadata, derived_scope, normalized_capabilities = self._normalize_gitlab_fields(
                credential_type=credential_type,
                source_url=effective_url,
                credential_metadata=effective_metadata,
                credential_scope=source.credential_scope,
                credential_capabilities=effective_capabilities,
            )
            source.credential_metadata = normalized_metadata
            source.credential_scope = derived_scope
            source.credential_capabilities = normalized_capabilities

        if credential_type == "ssh_deploy_key":
            metadata_provided = self._field_provided(source_data, "credential_metadata")
            credential_type_requested = self._field_provided(source_data, "credential_type")
            url_provided = self._field_provided(source_data, "url")

            effective_metadata = source_data.credential_metadata if metadata_provided else source.credential_metadata
            effective_capabilities = (
                source_data.credential_capabilities
                if self._field_provided(source_data, "credential_capabilities")
                else source.credential_capabilities
            )
            effective_url = source_data.url if source_data.url is not None else source.url

            normalized_metadata, derived_scope, normalized_capabilities = self._normalize_ssh_fields(
                credential_type=credential_type,
                source_url=effective_url,
                credential_metadata=effective_metadata,
                credential_scope=source.credential_scope,
                credential_capabilities=effective_capabilities,
                require_known_hosts=metadata_provided or credential_type_requested or url_provided,
            )
            source.credential_metadata = normalized_metadata
            source.credential_scope = derived_scope
            source.credential_capabilities = normalized_capabilities

        if credential_type == "oauth_token":
            effective_metadata = (
                source_data.credential_metadata
                if self._field_provided(source_data, "credential_metadata")
                else source.credential_metadata
            )
            effective_capabilities = (
                source_data.credential_capabilities
                if self._field_provided(source_data, "credential_capabilities")
                else source.credential_capabilities
            )
            effective_scope = (
                source_data.credential_scope
                if self._field_provided(source_data, "credential_scope")
                else source.credential_scope
            )
            effective_url = source_data.url if source_data.url is not None else source.url
            normalized_metadata, normalized_scope, normalized_capabilities = self._normalize_oauth_fields(
                credential_type=credential_type,
                source_url=effective_url,
                credential_metadata=effective_metadata,
                credential_scope=effective_scope,
                credential_capabilities=effective_capabilities,
            )
            source.credential_metadata = normalized_metadata
            source.credential_scope = normalized_scope
            source.credential_capabilities = normalized_capabilities

        if (
            credential_type != "github_app"
            and credential_type not in GitLabAuthService.SUPPORTED_CREDENTIAL_TYPES
            and credential_type != "ssh_deploy_key"
            and credential_type != "oauth_token"
            and self._field_provided(source_data, "credential_scope")
        ):
            source.credential_scope = source_data.credential_scope
        if (
            credential_type != "github_app"
            and credential_type not in GitLabAuthService.SUPPORTED_CREDENTIAL_TYPES
            and credential_type != "ssh_deploy_key"
            and credential_type != "oauth_token"
            and self._field_provided(source_data, "credential_capabilities")
        ):
            source.credential_capabilities = source_data.credential_capabilities
        if (
            credential_type != "github_app"
            and credential_type not in GitLabAuthService.SUPPORTED_CREDENTIAL_TYPES
            and credential_type != "ssh_deploy_key"
            and credential_type != "oauth_token"
            and self._field_provided(source_data, "credential_metadata")
        ):
            source.credential_metadata = source_data.credential_metadata
        if self._field_provided(source_data, "credential_expires_at"):
            source.credential_expires_at = source_data.credential_expires_at
        if self._field_provided(source_data, "credential_last_rotated_at"):
            source.credential_last_rotated_at = source_data.credential_last_rotated_at
        if self._field_provided(source_data, "credential_validation_status"):
            self._validate_credential_validation_status(source_data.credential_validation_status)
            source.credential_validation_status = source_data.credential_validation_status
        if self._field_provided(source_data, "credential_last_validated_at"):
            source.credential_last_validated_at = source_data.credential_last_validated_at
        if self._field_provided(source_data, "credential_validation_error"):
            source.credential_validation_error = source_data.credential_validation_error
        if source_data.auth_token is not None:
            source.auth_token_encrypted = encrypt_value(source_data.auth_token)
        if source_data.auto_sync is not None:
            source.auto_sync = source_data.auto_sync
        if source_data.sync_interval_hours is not None:
            source.sync_interval_hours = source_data.sync_interval_hours
        if source_data.is_active is not None:
            source.is_active = source_data.is_active
        if source_data.description is not None:
            source.description = source_data.description

        self.db.flush()
        if getattr(source_data, "make_default", False):
            self._set_default_module_source(source)
        self.db.refresh(source)
        logger.info(f"Updated module source: {source.name}")
        return self._serialize_source(source)

    def validate_source_credentials(self, source_id: int, *, user_obj: Any | None = None) -> dict[str, Any]:
        """Run bounded provider-specific credential validation for a source."""
        source = self._get_source(source_id)
        credential_type = self._derive_credential_type(source)

        if source.source_type != "git":
            raise BadRequestError(
                "Credential validation is only supported for git sources",
                code="UNSUPPORTED_SOURCE_TYPE",
            )

        if credential_type in GitLabAuthService.SUPPORTED_CREDENTIAL_TYPES:
            return self._validate_gitlab_source_credentials(source, credential_type=credential_type, user_obj=user_obj)

        if credential_type == "ssh_deploy_key":
            return self._validate_ssh_source_credentials(source, credential_type=credential_type, user_obj=user_obj)

        if credential_type == "oauth_token":
            return self._validate_oauth_source_credentials(source, credential_type=credential_type, user_obj=user_obj)

        if credential_type != "github_app":
            raise BadRequestError(
                f"Credential validation for '{credential_type}' is not implemented in this slice",
                code="UNSUPPORTED_CREDENTIAL_TYPE",
            )

        if not source.auth_token_encrypted:
            raise BadRequestError(
                "GitHub App private key is required for validation",
                code="MISSING_GITHUB_APP_PRIVATE_KEY",
            )

        private_key = decrypt_value_or_none(source.auth_token_encrypted)
        if not private_key:
            raise BadRequestError(
                "GitHub App private key could not be decrypted",
                code="INVALID_GITHUB_APP_PRIVATE_KEY",
            )

        try:
            normalized_metadata = GitHubAppAuthService.validate_required_metadata(source.credential_metadata)
            validation = GitHubAppAuthService.validate_repository_access(
                private_key=private_key,
                metadata=normalized_metadata,
            )
        except Exception as e:
            failure = str(e)
            source.credential_validation_status = "invalid"
            source.credential_last_validated_at = datetime.now(UTC)
            source.credential_validation_error = failure[:1000]
            self.db.flush()
            self._create_module_source_audit(
                action="module_source_credential_validation_failed",
                source=source,
                status="failed",
                user_obj=user_obj,
                details={
                    "provider": "github",
                    "credential_type": "github_app",
                    "error": failure[:300],
                },
            )
            raise BadRequestError(
                f"GitHub App credential validation failed: {failure}",
                code="GITHUB_APP_VALIDATION_FAILED",
            ) from e

        source.credential_metadata = normalized_metadata
        source.credential_scope = GitHubAppAuthService.build_scope(normalized_metadata)
        source.credential_capabilities = source.credential_capabilities or ["repo:read"]
        source.credential_validation_status = "valid"
        source.credential_last_validated_at = datetime.now(UTC)
        source.credential_validation_error = None
        source.credential_expires_at = self._parse_datetime_or_none(validation.get("token_expires_at"))
        self.db.flush()

        self._create_module_source_audit(
            action="module_source_credential_validated",
            source=source,
            status="success",
            user_obj=user_obj,
            details={
                "provider": "github",
                "credential_type": "github_app",
                "installation_id": validation.get("installation_id"),
                "owner": validation.get("owner"),
                "repository": validation.get("repository"),
            },
        )

        return {
            "success": True,
            "source_id": source.id,
            "source_name": source.name,
            "credential_type": "github_app",
            "credential_scope": source.credential_scope,
            "credential_validation_status": source.credential_validation_status,
            "credential_last_validated_at": source.credential_last_validated_at,
            "provider": "github",
            "validation": {
                "repository_full_name": validation.get("repository_full_name"),
                "repository_private": validation.get("repository_private"),
                "permissions": validation.get("permissions"),
                "token_expires_at": validation.get("token_expires_at"),
            },
        }

    def _validate_ssh_source_credentials(
        self,
        source: ModuleSource,
        *,
        credential_type: str,
        user_obj: Any | None,
    ) -> dict[str, Any]:
        if not source.auth_token_encrypted:
            raise BadRequestError(
                "SSH deploy key secret is required for validation",
                code="MISSING_SSH_DEPLOY_KEY",
            )

        parsed_target = GitAuthService.parse_ssh_git_target(source.url)
        if not parsed_target:
            raise BadRequestError(
                "SSH credential validation requires an SSH Git URL",
                code="INVALID_SSH_SOURCE_URL",
            )
        ssh_host, ssh_repo_path = parsed_target

        validation_context: dict[str, Any] = {
            "provider": "ssh",
            "host": ssh_host,
            "repo_path": ssh_repo_path,
            "reachability": "unknown",
        }

        temp_dir = tempfile.mkdtemp(prefix="module-source-ssh-validate-")
        safe_source_url = GitAuthService.strip_url_credentials(source.url)
        try:
            auth_ctx = GitAuthService.resolve_for_module_source(source, db=self.db)
            env, cleanup_env = GitAuthService.build_git_environment(auth_ctx)
            try:
                result = subprocess.run(
                    ["git", "ls-remote", "--heads", safe_source_url],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=temp_dir,
                    env=env,
                )
            finally:
                cleanup_env()

            if result.returncode != 0:
                error_kind, guidance = GitAuthService.classify_git_failure(result.stderr)
                sanitized = GitAuthService.sanitize_error_text(result.stderr, secrets=[auth_ctx.secret])
                source.credential_validation_status = "invalid"
                source.credential_last_validated_at = datetime.now(UTC)
                source.credential_validation_error = f"{error_kind}: {guidance}"[:1000]
                self.db.flush()
                self._create_module_source_audit(
                    action="module_source_credential_validation_failed",
                    source=source,
                    status="failed",
                    user_obj=user_obj,
                    details={
                        "provider": "ssh",
                        "credential_type": credential_type,
                        "error_kind": error_kind,
                        "host": ssh_host,
                        "repo_path": ssh_repo_path,
                        "error": sanitized[:300],
                    },
                )
                raise BadRequestError(
                    f"SSH credential validation failed [{error_kind}]: {guidance}",
                    code="SSH_VALIDATION_FAILED",
                )

            source.credential_validation_status = "valid"
            source.credential_last_validated_at = datetime.now(UTC)
            source.credential_validation_error = None
            source.credential_capabilities = source.credential_capabilities or ["repo:read"]
            source.credential_metadata, source.credential_scope, source.credential_capabilities = self._normalize_ssh_fields(
                credential_type=credential_type,
                source_url=source.url,
                credential_metadata=source.credential_metadata,
                credential_scope=source.credential_scope,
                credential_capabilities=source.credential_capabilities,
                require_known_hosts=True,
            )
            self.db.flush()

            validation_context["reachability"] = "reachable"
            validation_context["host_key_trust"] = "trusted"
            validation_context["auth"] = "valid"

            self._create_module_source_audit(
                action="module_source_credential_validated",
                source=source,
                status="success",
                user_obj=user_obj,
                details={
                    "provider": "ssh",
                    "credential_type": credential_type,
                    "host": ssh_host,
                    "repo_path": ssh_repo_path,
                },
            )

            return {
                "success": True,
                "source_id": source.id,
                "source_name": source.name,
                "credential_type": credential_type,
                "credential_scope": source.credential_scope,
                "credential_validation_status": source.credential_validation_status,
                "credential_last_validated_at": source.credential_last_validated_at,
                "provider": "ssh",
                "validation": validation_context,
            }

        except BadRequestError:
            raise
        except Exception as e:
            text = GitAuthService.sanitize_error_text(str(e))
            error_kind, guidance = GitAuthService.classify_git_failure(text)
            source.credential_validation_status = "error"
            source.credential_last_validated_at = datetime.now(UTC)
            source.credential_validation_error = f"{error_kind}: {guidance}"[:1000]
            self.db.flush()
            self._create_module_source_audit(
                action="module_source_credential_validation_failed",
                source=source,
                status="failed",
                user_obj=user_obj,
                details={
                    "provider": "ssh",
                    "credential_type": credential_type,
                    "error_kind": error_kind,
                    "host": ssh_host,
                    "repo_path": ssh_repo_path,
                    "error": text[:300],
                },
            )
            raise BadRequestError(
                f"SSH credential validation failed [{error_kind}]: {guidance}",
                code="SSH_VALIDATION_FAILED",
            ) from e
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _validate_oauth_source_credentials(
        self,
        source: ModuleSource,
        *,
        credential_type: str,
        user_obj: Any | None,
    ) -> dict[str, Any]:
        if not source.auth_token_encrypted:
            raise BadRequestError(
                "OAuth access token secret is required for validation",
                code="MISSING_OAUTH_TOKEN",
            )

        provider = "oauth"
        audit_logged = False
        temp_dir = tempfile.mkdtemp(prefix="module-source-oauth-validate-")
        safe_source_url = GitAuthService.strip_url_credentials(source.url)
        try:
            normalized_metadata, normalized_scope, normalized_capabilities = self._normalize_oauth_fields(
                credential_type=credential_type,
                source_url=source.url,
                credential_metadata=source.credential_metadata,
                credential_scope=source.credential_scope,
                credential_capabilities=source.credential_capabilities,
            )
            provider = str(
                normalized_metadata.get("oauth", {}).get("provider")
                or normalized_metadata.get("provider")
                or provider
            )

            auth_ctx = GitAuthService.resolve_for_module_source(source, db=self.db)
            env, cleanup_env = GitAuthService.build_git_environment(auth_ctx)
            try:
                result = subprocess.run(
                    ["git", "ls-remote", "--heads", safe_source_url],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=temp_dir,
                    env=env,
                )
            finally:
                cleanup_env()

            if result.returncode != 0:
                error_kind, guidance = GitAuthService.classify_git_failure(result.stderr)
                sanitized = GitAuthService.sanitize_error_text(result.stderr, secrets=[auth_ctx.secret])
                source.credential_validation_status = "invalid"
                source.credential_last_validated_at = datetime.now(UTC)
                source.credential_validation_error = f"{error_kind}: {guidance}"[:1000]
                self.db.flush()
                self._create_module_source_audit(
                    action="module_source_credential_validation_failed",
                    source=source,
                    status="failed",
                    user_obj=user_obj,
                    details={
                        "provider": provider,
                        "credential_type": credential_type,
                        "error_kind": error_kind,
                        "error": sanitized[:300],
                    },
                )
                audit_logged = True
                raise BadRequestError(
                    f"OAuth credential validation failed [{error_kind}]: {guidance}",
                    code="OAUTH_VALIDATION_FAILED",
                )

            source.credential_metadata = normalized_metadata
            source.credential_scope = normalized_scope
            source.credential_capabilities = normalized_capabilities
            source.credential_validation_status = "valid"
            source.credential_last_validated_at = datetime.now(UTC)
            source.credential_validation_error = None
            self.db.flush()

            self._create_module_source_audit(
                action="module_source_credential_validated",
                source=source,
                status="success",
                user_obj=user_obj,
                details={
                    "provider": provider,
                    "credential_type": credential_type,
                    "recommendation_tier": "supported_non_default",
                    "recommended_default_credential_types": self._oauth_recommended_defaults(provider),
                },
            )

            return {
                "success": True,
                "source_id": source.id,
                "source_name": source.name,
                "credential_type": credential_type,
                "credential_scope": source.credential_scope,
                "credential_validation_status": source.credential_validation_status,
                "credential_last_validated_at": source.credential_last_validated_at,
                "provider": provider,
                "validation": {
                    "provider": provider,
                    "recommendation_tier": "supported_non_default",
                    "recommended_default_credential_types": self._oauth_recommended_defaults(provider),
                },
            }
        except BadRequestError as e:
            if not audit_logged:
                source.credential_validation_status = "invalid"
                source.credential_last_validated_at = datetime.now(UTC)
                source.credential_validation_error = str(e)[:1000]
                self.db.flush()
                self._create_module_source_audit(
                    action="module_source_credential_validation_failed",
                    source=source,
                    status="failed",
                    user_obj=user_obj,
                    details={
                        "provider": provider,
                        "credential_type": credential_type,
                        "error_kind": "invalid_metadata",
                        "error": str(e)[:300],
                    },
                )
            raise
        except Exception as e:
            text = GitAuthService.sanitize_error_text(str(e))
            source.credential_validation_status = "error"
            source.credential_last_validated_at = datetime.now(UTC)
            source.credential_validation_error = text[:1000]
            self.db.flush()
            self._create_module_source_audit(
                action="module_source_credential_validation_failed",
                source=source,
                status="failed",
                user_obj=user_obj,
                details={
                    "provider": provider,
                    "credential_type": credential_type,
                    "error_kind": "validation_error",
                    "error": text[:300],
                },
            )
            raise BadRequestError(
                f"OAuth credential validation failed: {text}",
                code="OAUTH_VALIDATION_FAILED",
            ) from e
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _validate_gitlab_source_credentials(
        self,
        source: ModuleSource,
        *,
        credential_type: str,
        user_obj: Any | None,
    ) -> dict[str, Any]:
        if not source.auth_token_encrypted:
            raise BadRequestError(
                "GitLab credential secret is required for validation",
                code="MISSING_GITLAB_TOKEN",
            )

        token = decrypt_value_or_none(source.auth_token_encrypted)
        if not token:
            raise BadRequestError(
                "GitLab credential secret could not be decrypted",
                code="INVALID_GITLAB_TOKEN",
            )

        try:
            normalized_metadata = GitLabAuthService.validate_required_metadata(
                credential_type=credential_type,
                metadata=source.credential_metadata,
                source_url=source.url,
            )
            validation = GitLabAuthService.validate_repository_access(
                credential_type=credential_type,
                token=token,
                metadata=normalized_metadata,
                source_url=source.url,
            )
        except (GitLabValidationError, ValueError) as e:
            failure = str(e)
            source.credential_validation_status = "invalid"
            source.credential_last_validated_at = datetime.now(UTC)
            source.credential_validation_error = failure[:1000]
            self.db.flush()
            error_kind = e.error_kind if isinstance(e, GitLabValidationError) else "invalid_metadata"
            self._create_module_source_audit(
                action="module_source_credential_validation_failed",
                source=source,
                status="failed",
                user_obj=user_obj,
                details={
                    "provider": "gitlab",
                    "credential_type": credential_type,
                    "error_kind": error_kind,
                    "error": failure[:300],
                },
            )
            raise BadRequestError(
                f"GitLab credential validation failed [{error_kind}]: {failure}",
                code="GITLAB_VALIDATION_FAILED",
            ) from e
        except Exception as e:
            failure = str(e)
            source.credential_validation_status = "error"
            source.credential_last_validated_at = datetime.now(UTC)
            source.credential_validation_error = failure[:1000]
            self.db.flush()
            self._create_module_source_audit(
                action="module_source_credential_validation_failed",
                source=source,
                status="failed",
                user_obj=user_obj,
                details={
                    "provider": "gitlab",
                    "credential_type": credential_type,
                    "error_kind": "validation_error",
                    "error": failure[:300],
                },
            )
            raise BadRequestError(
                f"GitLab credential validation failed: {failure}",
                code="GITLAB_VALIDATION_FAILED",
            ) from e

        source.credential_metadata = normalized_metadata
        source.credential_scope = GitLabAuthService.build_scope(
            credential_type=credential_type,
            metadata=normalized_metadata,
            source_url=source.url,
        )
        source.credential_capabilities = source.credential_capabilities or ["repo:read"]
        source.credential_validation_status = "valid"
        source.credential_last_validated_at = datetime.now(UTC)
        source.credential_validation_error = None
        self.db.flush()

        self._create_module_source_audit(
            action="module_source_credential_validated",
            source=source,
            status="success",
            user_obj=user_obj,
            details={
                "provider": "gitlab",
                "credential_type": credential_type,
                "project_path": validation.get("project_path"),
                "group_path": validation.get("group_path"),
                "token_class": validation.get("token_class"),
            },
        )

        return {
            "success": True,
            "source_id": source.id,
            "source_name": source.name,
            "credential_type": credential_type,
            "credential_scope": source.credential_scope,
            "credential_validation_status": source.credential_validation_status,
            "credential_last_validated_at": source.credential_last_validated_at,
            "provider": "gitlab",
            "validation": {
                "host": validation.get("host"),
                "project_path": validation.get("project_path"),
                "group_path": validation.get("group_path"),
                "project_id": validation.get("project_id"),
                "project_visibility": validation.get("project_visibility"),
                "default_branch": validation.get("default_branch"),
                "namespace_full_path": validation.get("namespace_full_path"),
                "token_class": validation.get("token_class"),
            },
        }

    def delete_source(self, source_id: int) -> dict[str, Any]:
        """Delete a module source and all its catalog modules (if not in use)."""
        source = self._get_source(source_id)

        if source.source_type == 'builtin':
            raise BadRequestError("Cannot delete built-in module source",
                                  code="BUILTIN_SOURCE_IMMUTABLE")

        # Check if any modules are in use by projects
        in_use_ids = self.db.query(ProjectModule.module_library_id).join(
            ModuleLibrary, ProjectModule.module_library_id == ModuleLibrary.id
        ).filter(
            ModuleLibrary.module_source_id == source_id
        ).distinct().subquery()

        modules_in_use = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.id.in_(self.db.query(in_use_ids))
        ).all()

        if modules_in_use:
            module_names = [m.name for m in modules_in_use[:5]]
            more = len(modules_in_use) - 5 if len(modules_in_use) > 5 else 0
            names_str = ", ".join(module_names) + (f" and {more} more" if more else "")
            raise BadRequestError(
                f"Cannot delete source: {len(modules_in_use)} modules are in use by projects ({names_str})",
                code="SOURCE_IN_USE"
            )

        # Delete catalog modules
        modules_to_delete = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.module_source_id == source_id
        ).all()
        module_count = len(modules_to_delete)
        for module in modules_to_delete:
            self.db.delete(module)

        source_name = source.name
        self.db.delete(source)
        self.db.flush()

        invalidate_cache("module_library:*")
        logger.info(f"Deleted module source: {source_name} ({module_count} catalog modules removed)")

        return {
            "success": True,
            "message": f"Module source '{source_name}' deleted ({module_count} catalog modules removed)"
        }

    # ================================================================
    # Source Modules
    # ================================================================

    def list_source_modules(self, source_id: int) -> dict[str, Any]:
        """List all modules from a specific source."""
        source = self._get_source(source_id)

        modules = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.module_source_id == source_id,
            ModuleLibrary.is_active
        ).all()

        return {
            "source_id": source_id,
            "source_name": source.name,
            "module_count": len(modules),
            "modules": [
                {
                    "id": m.id, "name": m.name, "category": m.category,
                    "provider": m.provider, "description": m.description,
                    "source_path": m.source_path, "source_version": m.source_version,
                    "is_custom": m.is_custom, "update_available": m.update_available,
                    "latest_version": m.latest_version,
                }
                for m in modules
            ]
        }

    # ================================================================
    # Sync
    # ================================================================

    def sync_source(self, source_id: int) -> dict[str, Any]:
        """Sync a module source (parse and import modules)."""
        source = self._get_source(source_id)

        if not source.is_active:
            raise BadRequestError("Source is inactive", code="SOURCE_INACTIVE")

        if source.source_type == 'git':
            from services.module_sync_service import ModuleSyncService
            sync_service = ModuleSyncService(self.db)
            try:
                results = sync_service.sync_git_source(source)
                blueprint_results = self._auto_sync_blueprints_for_git_source(source)
                if blueprint_results is not None:
                    results["blueprint_auto_sync"] = blueprint_results
                # The module-library list route caches for 15 min; without this,
                # a freshly-synced version is missing from version pickers and
                # is_latest badges until the TTL expires. The trailing * glob is
                # required: delete_pattern passes the string to Redis SCAN match=,
                # so a bare prefix matches nothing.
                invalidate_cache("module_library:*")
                return {
                    "success": True,
                    "source_id": source_id,
                    "source_name": source.name,
                    "sync_status": source.sync_status,
                    "results": results
                }
            except Exception as e:
                logger.error(f"Sync failed for source {source_id}: {e}")
                raise ServiceError("git_sync", f"Sync failed: {str(e)}")
        elif source.source_type in ("registry", "terraform_registry", "opentofu_registry", "tfc_private", "public_git"):
            # Imported registry/git modules don't have a bulk-list endpoint to scan;
            # sync means "refresh upstream version metadata for each imported module".
            from services.registry_service import RegistryService
            registry = RegistryService(self.db)
            checked = 0
            updates_available = 0
            errors: list[str] = []
            modules = (
                self.db.query(ModuleLibrary)
                .filter(ModuleLibrary.module_source_id == source.id)
                .all()
            )
            for module in modules:
                try:
                    info = registry.check_for_updates(module.id)
                    checked += 1
                    if info.get("update_available"):
                        updates_available += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("update check failed for module %s: %s", module.id, exc)
                    errors.append(f"{module.name}: {exc}")
            source.sync_status = "success"
            source.sync_error = None if not errors else "; ".join(errors[:3])
            source.last_synced_at = datetime.now(UTC)
            self.db.commit()
            return {
                "success": True,
                "source_id": source_id,
                "source_name": source.name,
                "sync_status": source.sync_status,
                "results": {
                    "modules_checked": checked,
                    "updates_available": updates_available,
                    "errors": errors,
                },
            }
        else:
            raise BadRequestError(f"Sync not supported for source type: {source.source_type}",
                                  code="UNSUPPORTED_SOURCE_TYPE")

    def _auto_sync_blueprints_for_git_source(self, module_source: ModuleSource) -> dict[str, Any] | None:
        if module_source.source_type != "git":
            return None

        blueprint_source, created = self._get_or_create_linked_blueprint_source(module_source)
        results = BlueprintSyncService(self.db).sync_git_source(blueprint_source, sync_related_modules=False)
        self.db.refresh(blueprint_source)
        return {
            "source_id": blueprint_source.id,
            "source_name": blueprint_source.name,
            "created": created,
            "sync_status": blueprint_source.sync_status,
            "results": results,
        }

    def _get_or_create_linked_blueprint_source(self, module_source: ModuleSource) -> tuple[BlueprintSource, bool]:
        source_key = self._source_key(module_source.url, module_source.branch, module_source.git_ref)
        existing = (
            self.db.query(BlueprintSource)
            .filter(BlueprintSource.source_type == "git")
            .order_by(BlueprintSource.id.asc())
            .all()
        )
        for source in existing:
            if self._source_key(source.url, source.branch, source.git_ref) == source_key:
                return source, False

        service = BlueprintCatalogService(self.db)
        created = service.create_source(
            type(
                "BlueprintSourceCreate",
                (),
                {
                    "name": self._linked_blueprint_source_name(module_source.name),
                    "source_type": "git",
                    "url": module_source.url,
                    "branch": module_source.branch,
                    "git_ref": module_source.git_ref,
                    "is_active": module_source.is_active,
                    "description": f"Auto-created from module source '{module_source.name}'",
                },
            )()
        )
        return service._get_source(created["id"]), True

    @staticmethod
    def _source_key(url: str | None, branch: str | None, git_ref: str | None) -> tuple[str, str]:
        normalized_url = GitAuthService.strip_url_credentials((url or "").strip()).rstrip("/").removesuffix(".git")
        normalized_ref = (git_ref or branch or "main").strip() or "main"
        return normalized_url, normalized_ref

    def _linked_blueprint_source_name(self, module_source_name: str) -> str:
        base_name = f"{module_source_name} blueprints"
        candidate = base_name
        suffix = 2
        while True:
            exists = self.db.query(BlueprintSource).filter(BlueprintSource.name == candidate).first()
            if exists is None:
                return candidate
            candidate = f"{base_name} {suffix}"
            suffix += 1
