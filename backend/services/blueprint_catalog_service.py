"""Service layer for blueprint source and immutable release catalog persistence."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from core.errors import BadRequestError, ConflictError, NotFoundError
from models import BlueprintRelease, BlueprintSource
from models.blueprint_catalog import _MUTABLE_BLUEPRINT_RELEASE_LIFECYCLE_FIELDS
from models.enums import BlueprintReleaseState, BlueprintValidationState, ModuleSyncStatus
from services.blueprint_default_source_service import is_default_blueprint_source, set_default_blueprint_source
from services.blueprint_manifest_service import BlueprintManifestValidationError, validate_blueprint_manifest
from utils.catalog_versioning import canonical_json_sha256

logger = logging.getLogger(__name__)

ALLOWED_BLUEPRINT_SOURCE_TYPES = {"git", "builtin"}
ALLOWED_BLUEPRINT_RELEASE_STATES = set(BlueprintReleaseState)
ALLOWED_BLUEPRINT_VALIDATION_STATES = set(BlueprintValidationState)


def _normalize_enum_value(value: str | None, *, field_name: str, allowed: set[str], default: str) -> str:
    if value is None:
        return default
    if value not in allowed:
        raise BadRequestError(
            f"Unsupported {field_name} '{value}'",
            code=f"INVALID_{field_name.upper()}",
        )
    return value


class BlueprintCatalogService:
    """Service layer for blueprint-source and release persistence operations."""

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # source CRUD/list/detail
    # ------------------------------------------------------------------

    def list_sources(self, source_type: str | None = None, is_active: bool | None = None) -> list[dict[str, Any]]:
        if source_type is not None and source_type not in ALLOWED_BLUEPRINT_SOURCE_TYPES:
            raise BadRequestError(
                f"Unsupported source_type '{source_type}'",
                code="INVALID_SOURCE_TYPE",
            )

        query = self.db.query(BlueprintSource)
        if source_type is not None:
            query = query.filter(BlueprintSource.source_type == source_type)
        if is_active is not None:
            query = query.filter(BlueprintSource.is_active == is_active)
        sources = query.order_by(BlueprintSource.created_at.desc()).all()
        return [self._serialize_source(source) for source in sources]

    def get_source(self, source_id: int) -> dict[str, Any]:
        return self._serialize_source(self._get_source(source_id))

    def create_source(self, source_data: Any) -> dict[str, Any]:
        existing = self.db.query(BlueprintSource).filter(BlueprintSource.name == source_data.name).first()
        if existing:
            raise ConflictError("blueprint_source", f"Blueprint source '{source_data.name}' already exists")

        if source_data.source_type not in ALLOWED_BLUEPRINT_SOURCE_TYPES:
            raise BadRequestError(
                "source_type must be 'git' or 'builtin'",
                code="INVALID_SOURCE_TYPE",
            )

        source = BlueprintSource(
            name=source_data.name,
            source_type=source_data.source_type,
            url=source_data.url,
            branch=source_data.branch,
            git_ref=source_data.git_ref,
            sync_status=ModuleSyncStatus.PENDING,
            is_active=source_data.is_active,
            description=source_data.description,
        )

        self.db.add(source)
        self.db.flush()
        if getattr(source_data, "make_default", False):
            set_default_blueprint_source(source, self.db)
        self.db.refresh(source)
        logger.info("Created blueprint source: %s", source.name)
        return self._serialize_source(source)

    def update_source(self, source_id: int, source_data: Any) -> dict[str, Any]:
        source = self._get_source(source_id)

        if source.source_type == "builtin":
            raise BadRequestError("Cannot modify built-in blueprint source", code="BUILTIN_SOURCE_IMMUTABLE")

        if source_data.name is not None:
            existing = self.db.query(BlueprintSource).filter(
                BlueprintSource.name == source_data.name,
                BlueprintSource.id != source_id,
            ).first()
            if existing:
                raise ConflictError("blueprint_source", f"Blueprint source '{source_data.name}' already exists")
            source.name = source_data.name

        if source_data.url is not None:
            source.url = source_data.url
        if source_data.branch is not None:
            source.branch = source_data.branch
        if source_data.git_ref is not None:
            source.git_ref = source_data.git_ref
        if source_data.is_active is not None:
            source.is_active = source_data.is_active
        if source_data.description is not None:
            source.description = source_data.description

        self.db.flush()
        if getattr(source_data, "make_default", False):
            set_default_blueprint_source(source, self.db)
        self.db.refresh(source)
        return self._serialize_source(source)

    def delete_source(self, source_id: int) -> dict[str, Any]:
        source = self._get_source(source_id)

        if source.source_type == "builtin":
            raise BadRequestError("Cannot delete built-in blueprint source", code="BUILTIN_SOURCE_IMMUTABLE")

        if is_default_blueprint_source(source, self.db):
            raise BadRequestError(
                "Cannot delete the configured default blueprint source. Clear blueprint_library.git_url first if you want to remove it.",
                code="DEFAULT_BLUEPRINT_SOURCE_IMMUTABLE",
            )

        source_name = source.name
        self.db.delete(source)
        self.db.flush()
        return {
            "success": True,
            "message": f"Blueprint source '{source_name}' deleted",
        }

    # ------------------------------------------------------------------
    # release CRUD/list/detail + promotion-state update
    # ------------------------------------------------------------------

    def list_releases(
        self,
        source_id: int | None = None,
        blueprint_id: str | None = None,
        release_state: str | None = None,
        validation_state: str | None = None,
        is_active: bool | None = None,
    ) -> list[dict[str, Any]]:
        normalized_release_state = _normalize_enum_value(
            release_state,
            field_name="release_state",
            allowed={state.value for state in ALLOWED_BLUEPRINT_RELEASE_STATES},
            default=BlueprintReleaseState.IMPORTED,
        ) if release_state is not None else None
        normalized_validation_state = _normalize_enum_value(
            validation_state,
            field_name="validation_state",
            allowed={state.value for state in ALLOWED_BLUEPRINT_VALIDATION_STATES},
            default=BlueprintValidationState.UNKNOWN,
        ) if validation_state is not None else None

        query = self.db.query(BlueprintRelease)
        if source_id is not None:
            query = query.filter(BlueprintRelease.blueprint_source_id == source_id)
        if blueprint_id is not None:
            query = query.filter(BlueprintRelease.blueprint_id == blueprint_id)
        if normalized_release_state is not None:
            query = query.filter(BlueprintRelease.release_state == normalized_release_state)
        if normalized_validation_state is not None:
            query = query.filter(BlueprintRelease.validation_state == normalized_validation_state)
        if is_active is not None:
            query = query.filter(BlueprintRelease.is_active == is_active)

        releases = query.order_by(BlueprintRelease.imported_at.desc()).all()
        return [self._serialize_release(release) for release in releases]

    def get_release(self, release_id: int) -> dict[str, Any]:
        return self._serialize_release(self._get_release(release_id))

    def create_release(self, release_data: Any) -> dict[str, Any]:
        source = self._get_source(release_data.blueprint_source_id)

        provided_release_state = _normalize_enum_value(
            release_data.release_state,
            field_name="release_state",
            allowed={state.value for state in ALLOWED_BLUEPRINT_RELEASE_STATES},
            default=BlueprintReleaseState.IMPORTED,
        )

        try:
            manifest = validate_blueprint_manifest(release_data.manifest)
            validation_state = BlueprintValidationState.VALID.value
            validation_errors = None
        except BlueprintManifestValidationError as exc:
            validation_state = BlueprintValidationState.INVALID.value
            validation_errors = [
                {
                    "code": issue.code,
                    "path": issue.path,
                    "message": issue.message,
                }
                for issue in exc.issues
            ]

        if validation_state != BlueprintValidationState.VALID.value and provided_release_state in {
            BlueprintReleaseState.IMPORTED.value,
            BlueprintReleaseState.APPROVED.value,
            BlueprintReleaseState.DEPRECATED.value,
            BlueprintReleaseState.INACTIVE.value,
        }:
            raise BadRequestError(
                "Cannot create non-discovered release when manifest validation is invalid",
                code="BLUEPRINT_RELEASE_VALIDATION_FAILED",
                details={"validation_errors": validation_errors or []},
            )

        if provided_release_state == BlueprintReleaseState.DISCOVERED.value and release_data.is_active:
            raise BadRequestError(
                "Discovered releases are not deployable catalog entries and must be inactive",
                code="BLUEPRINT_DISCOVERED_RELEASE_MUST_BE_INACTIVE",
            )

        manifest_dict = release_data.manifest
        if validation_state == BlueprintValidationState.VALID.value:
            manifest_dict = manifest.model_dump(mode="json")

        blueprint_identity = manifest_dict.get("blueprint") if isinstance(manifest_dict, dict) else None
        if not isinstance(blueprint_identity, dict):
            raise BadRequestError(
                "Blueprint manifest must include a blueprint identity object",
                code="BLUEPRINT_MANIFEST_IDENTITY_REQUIRED",
            )

        blueprint_id = blueprint_identity.get("id")
        blueprint_version = blueprint_identity.get("version")
        blueprint_name = blueprint_identity.get("name")
        schema_version = manifest_dict.get("schema_version") if isinstance(manifest_dict, dict) else None

        if not blueprint_id or not blueprint_version or not blueprint_name or not isinstance(schema_version, int):
            raise BadRequestError(
                "Blueprint manifest must include blueprint.id, blueprint.version, blueprint.name, and schema_version",
                code="BLUEPRINT_MANIFEST_IDENTITY_REQUIRED",
            )

        content_sha256 = canonical_json_sha256(manifest_dict)

        imported_at: Any = None
        if provided_release_state != BlueprintReleaseState.DISCOVERED.value:
            imported_at = self._now_utc()

        release = BlueprintRelease(
            blueprint_source_id=source.id,
            blueprint_id=str(blueprint_id),
            blueprint_version=str(blueprint_version),
            blueprint_name=str(blueprint_name),
            blueprint_description=blueprint_identity.get("description"),
            schema_version=schema_version,
            source_path=release_data.source_path,
            source_ref=release_data.source_ref,
            content_sha256=content_sha256,
            manifest=manifest_dict,
            validation_state=validation_state,
            validation_errors=validation_errors,
            release_state=provided_release_state,
            state_reason=release_data.state_reason,
            is_active=release_data.is_active,
            imported_at=imported_at,
        )

        self.db.add(release)
        self.db.flush()

        source.release_count = self.db.query(BlueprintRelease).filter(
            BlueprintRelease.blueprint_source_id == source.id,
            BlueprintRelease.is_active,
        ).count()

        self.db.refresh(release)
        self.db.refresh(source)
        logger.info(
            "Created blueprint release: source=%s blueprint=%s version=%s state=%s",
            source.name,
            release.blueprint_id,
            release.blueprint_version,
            release.release_state,
        )
        return self._serialize_release(release)

    def update_release_state(self, release_id: int, state_data: Any) -> dict[str, Any]:
        release = self._get_release(release_id)
        next_release_state = _normalize_enum_value(
            state_data.release_state,
            field_name="release_state",
            allowed={state.value for state in ALLOWED_BLUEPRINT_RELEASE_STATES},
            default=BlueprintReleaseState.IMPORTED,
        )

        if (
            release.release_state != BlueprintReleaseState.DISCOVERED.value
            and next_release_state == BlueprintReleaseState.DISCOVERED.value
        ):
            raise BadRequestError(
                "Release state cannot transition back to discovered after import",
                code="BLUEPRINT_RELEASE_INVALID_STATE_TRANSITION",
            )

        if (
            next_release_state == BlueprintReleaseState.APPROVED.value
            and release.validation_state != BlueprintValidationState.VALID.value
        ):
            raise BadRequestError(
                "Only validation_state=valid releases can be promoted to approved",
                code="BLUEPRINT_RELEASE_PROMOTION_BLOCKED",
            )

        if next_release_state != BlueprintReleaseState.DISCOVERED.value and release.imported_at is None:
            if release.validation_state != BlueprintValidationState.VALID.value:
                raise BadRequestError(
                    "Only validation_state=valid discovered releases can be imported",
                    code="BLUEPRINT_RELEASE_IMPORT_BLOCKED",
                )
            release.imported_at = self._now_utc()

        next_is_active = state_data.is_active
        if next_release_state == BlueprintReleaseState.DISCOVERED.value:
            if next_is_active is True:
                raise BadRequestError(
                    "Discovered releases are not deployable catalog entries and must remain inactive",
                    code="BLUEPRINT_DISCOVERED_RELEASE_MUST_BE_INACTIVE",
                )
            release.is_active = False
        elif next_is_active is not None:
            release.is_active = next_is_active

        release.release_state = next_release_state
        release.state_reason = state_data.state_reason

        source = release.source
        source.release_count = self.db.query(BlueprintRelease).filter(
            BlueprintRelease.blueprint_source_id == release.blueprint_source_id,
            BlueprintRelease.is_active,
        ).count()

        self.db.flush()
        self.db.refresh(release)
        self.db.refresh(source)
        return self._serialize_release(release)

    def unimport_release(self, release_id: int) -> dict[str, Any]:
        """Reverse an Import: take an imported/approved release back to discovered.

        Blocked when any project's modules were created from this release —
        ProjectModule.path_in_project is prefixed with
        ``imported-blueprint/{release_id}/`` by ImportedBlueprintService when
        a project is built from a release. Returns the offending project
        names in the conflict so the operator knows what to clean up first.
        """
        release = self._get_release(release_id)

        if release.release_state == BlueprintReleaseState.DISCOVERED.value:
            raise BadRequestError(
                "Release is already in 'discovered' state and is not deployable",
                code="BLUEPRINT_RELEASE_NOT_IMPORTED",
            )

        from models import Project, ProjectModule

        path_prefix = f"imported-blueprint/{release.id}/"
        in_use = (
            self.db.query(Project.id, Project.name)
            .join(ProjectModule, ProjectModule.project_id == Project.id)
            .filter(ProjectModule.path_in_project.like(f"{path_prefix}%"))
            .distinct()
            .all()
        )
        if in_use:
            project_names = [row.name for row in in_use]
            raise ConflictError(
                "blueprint_release",
                "Cannot remove blueprint release: it is in use by "
                f"{len(project_names)} project(s): {', '.join(project_names)}. "
                "Delete those projects (or their imported modules) first.",
            )

        release.release_state = BlueprintReleaseState.DISCOVERED.value
        release.is_active = False
        release.imported_at = None
        release.state_reason = "Removed from deployable catalog"

        source = release.source
        if source is not None:
            source.release_count = self.db.query(BlueprintRelease).filter(
                BlueprintRelease.blueprint_source_id == source.id,
                BlueprintRelease.is_active,
            ).count()

        self.db.flush()
        self.db.refresh(release)
        if source is not None:
            self.db.refresh(source)

        return self._serialize_release(release)

    def set_release_visibility(self, release_id: int, is_visible: bool) -> dict[str, Any]:
        """Toggle visibility of an imported/approved release on the user-facing catalog.

        ON  (is_visible=True)  => ensure release_state=imported, is_active=True
        OFF (is_visible=False) => set is_active=False (release_state unchanged)

        Blocked for releases in 'discovered' state — they are not yet imported.
        """
        release = self._get_release(release_id)

        if release.release_state == BlueprintReleaseState.DISCOVERED.value:
            raise BadRequestError(
                "Cannot change visibility of a discovered release — import it first",
                code="BLUEPRINT_RELEASE_NOT_IMPORTED",
            )

        if is_visible:
            # Ensure release_state is imported and is_active=True.
            # Preserve approved state if already there (don't downgrade).
            release.is_active = True
            if release.release_state not in {
                BlueprintReleaseState.IMPORTED.value,
                BlueprintReleaseState.APPROVED.value,
            }:
                release.release_state = BlueprintReleaseState.IMPORTED.value
        else:
            release.is_active = False

        source = release.source
        if source is not None:
            source.release_count = self.db.query(BlueprintRelease).filter(
                BlueprintRelease.blueprint_source_id == release.blueprint_source_id,
                BlueprintRelease.is_active,
            ).count()

        self.db.flush()
        self.db.refresh(release)
        if source is not None:
            self.db.refresh(source)
        return self._serialize_release(release)

    def delete_release(self, release_id: int) -> dict[str, Any]:
        release = self._get_release(release_id)
        source = release.source
        release_name = f"{release.blueprint_name} {release.blueprint_version}"

        self.db.delete(release)
        self.db.flush()

        if source is not None:
            source.release_count = self.db.query(BlueprintRelease).filter(
                BlueprintRelease.blueprint_source_id == source.id,
                BlueprintRelease.is_active,
            ).count()
            self.db.flush()

        return {
            "success": True,
            "message": f"Blueprint release '{release_name}' deleted",
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _get_source(self, source_id: int) -> BlueprintSource:
        source = self.db.query(BlueprintSource).filter(BlueprintSource.id == source_id).first()
        if source is None:
            raise NotFoundError("blueprint_source", source_id)
        return source

    def _get_release(self, release_id: int) -> BlueprintRelease:
        release = self.db.query(BlueprintRelease).filter(BlueprintRelease.id == release_id).first()
        if release is None:
            raise NotFoundError("blueprint_release", release_id)
        return release

    def _serialize_source(self, source: BlueprintSource) -> dict[str, Any]:
        return {
            "id": source.id,
            "name": source.name,
            "source_type": source.source_type,
            "url": source.url,
            "branch": source.branch,
            "git_ref": source.git_ref,
            "sync_status": source.sync_status,
            "sync_error": source.sync_error,
            "last_synced_at": source.last_synced_at,
            "release_count": source.release_count,
            "is_active": source.is_active,
            "is_default": is_default_blueprint_source(source, self.db),
            "description": source.description,
            "created_at": source.created_at,
            "updated_at": source.updated_at,
        }

    def _count_projects_using_release(self, release_id: int) -> int:
        """Count distinct projects with at least one module created from
        this release. Tied to ImportedBlueprintService's
        ``imported-blueprint/{release_id}/`` path_in_project prefix.
        """
        from models import Project, ProjectModule

        prefix = f"imported-blueprint/{release_id}/"
        return (
            self.db.query(Project.id)
            .join(ProjectModule, ProjectModule.project_id == Project.id)
            .filter(ProjectModule.path_in_project.like(f"{prefix}%"))
            .distinct()
            .count()
        )

    def _serialize_release(self, release: BlueprintRelease) -> dict[str, Any]:
        manifest = release.manifest or {}
        return {
            "id": release.id,
            "blueprint_source_id": release.blueprint_source_id,
            "source_name": release.source.name if release.source else None,
            "source_type": release.source.source_type if release.source else None,
            "blueprint_id": release.blueprint_id,
            "blueprint_version": release.blueprint_version,
            "blueprint_name": release.blueprint_name,
            "blueprint_description": release.blueprint_description,
            "category": manifest.get("category"),
            "cloud_provider": manifest.get("cloud_provider"),
            "bnk_version": manifest.get("bnk_version"),
            "tags": manifest.get("tags") or [],
            "schema_version": release.schema_version,
            "source_path": release.source_path,
            "source_ref": release.source_ref,
            "content_sha256": release.content_sha256,
            "validation_state": release.validation_state,
            "validation_errors": release.validation_errors,
            "release_state": release.release_state,
            "state_reason": release.state_reason,
            "is_active": release.is_active,
            "imported_at": release.imported_at,
            "created_at": release.created_at,
            "updated_at": release.updated_at,
            "deployed_project_count": self._count_projects_using_release(release.id),
            "is_featured": bool(manifest.get("is_featured", False)),
            "mutable_lifecycle_fields": sorted(_MUTABLE_BLUEPRINT_RELEASE_LIFECYCLE_FIELDS),
        }

    def _now_utc(self) -> Any:
        from datetime import UTC, datetime

        return datetime.now(UTC)
