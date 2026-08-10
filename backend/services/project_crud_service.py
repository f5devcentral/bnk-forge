"""
Project CRUD Service — Business logic for project lifecycle operations.

Extracted from routes/projects.py to separate HTTP handling from domain logic.

Covers:
- Project creation with type inference, credential template resolution, region resolution
- Project listing with eager-loaded relationships
- Project detail serialization
- Project update with duplicate name checking
- Project deletion with workspace lock validation and cleanup
- Project activation (switching active project)
"""

import logging
from typing import Any

from sqlalchemy.orm import joinedload, subqueryload

from core.cache import cache, invalidate_cache
from core.errors import BadRequestError, ConflictError, NotFoundError
from models import CloudCredentialTemplate, Project, ProjectBackendConfig, User
from services.base_service import BaseService
from services.defaults_service import get_default

logger = logging.getLogger(__name__)

# Type inference mappings
TYPE_TO_PROVIDER = {
    "cloud-aws": "aws",
    "cloud-azure": "azure",
    "cloud-gcp": "gcp",
    "cloud-ibm": "ibm",
    "kubernetes": "on-prem",
}

PROVIDER_TO_TYPE = {
    "aws": "cloud-aws",
    "azure": "cloud-azure",
    "gcp": "cloud-gcp",
    "ibm": "cloud-ibm",
    "ssh": "kubernetes",
}


class ProjectCRUDService(BaseService):
    """Service layer for project CRUD operations."""

    # ================================================================
    # Create
    # ================================================================

    def create_project(self, data) -> dict[str, Any]:
        """
        Create a new project with type/provider inference and region resolution.

        Args:
            data: ProjectCreate schema instance

        Returns:
            Dict with success, project_id, name, message
        """
        self._check_name_unique(data.name)

        project_type, cloud_provider = self._resolve_type_and_provider(
            data.project_type, data.cloud_provider, data.credential_template_id
        )
        region = self._resolve_region(
            data.region, cloud_provider, project_type,
            data.credential_template_id, data.name
        )

        # When no credential template is explicitly provided, bind the is_default
        # template (matching provider when possible, global default otherwise).
        # This ensures projects registered without an explicit credential_template_id
        # (e.g. via awsbnkctl forge register) automatically inherit working credentials.
        resolved_template_id = data.credential_template_id
        if not resolved_template_id:
            resolved_template_id = self._resolve_default_template_id(cloud_provider)

        project = Project(
            name=data.name,
            description=data.description,
            project_type=project_type,
            cloud_provider=cloud_provider or None,
            environment=data.environment or "dev",
            backend_type=data.backend_type or "local",
            region=region,
            color=data.color or "#a8337a",
            icon=data.icon or "",
            credential_template_id=resolved_template_id,
        )

        self.db.add(project)
        self._apply_state_config(project, getattr(data, "state_config", None))
        self.db.commit()
        self.db.refresh(project)

        invalidate_cache("projects:*")

        return {
            "success": True,
            "project_id": project.id,
            "name": project.name,
            "message": "Project created successfully",
        }

    # ================================================================
    # Read
    # ================================================================

    def list_projects(self) -> dict[str, Any]:
        """
        List all projects with eager-loaded relationships and caching.

        Returns:
            Dict with projects list and total count
        """
        cache_key = "projects:list"
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        projects = (
            self.db.query(Project)
            .options(
                joinedload(Project.credential_template).load_only(
                    CloudCredentialTemplate.id,
                    CloudCredentialTemplate.name,
                    CloudCredentialTemplate.provider,
                ),
                subqueryload(Project.k8s_clusters),
                joinedload(Project.user).load_only(User.id, User.username),  # MU-001
            )
            .order_by(Project.name)
            .all()
        )

        project_list = [self._serialize_list_item(p) for p in projects]
        result = {"projects": project_list, "total": len(project_list)}

        cache.set(cache_key, result, ttl_seconds=300)
        return result

    def get_project_detail(self, project_id: int) -> dict[str, Any]:
        """
        Get a single project's detail representation.

        Returns:
            Dict with all project fields for the detail response
        """
        project = self._get_project(project_id)
        return self._serialize_detail(project)

    def get_active_project(self) -> dict[str, Any]:
        """Get the currently active project."""
        project = self.db.query(Project).filter(Project.is_active).first()
        if not project:
            return {"active": False, "project": None, "message": "No active project"}
        return {
            "active": True,
            "project": {
                "id": project.id,
                "name": project.name,
                "color": project.color,
                "icon": project.icon,
            },
        }

    # ================================================================
    # Update
    # ================================================================

    def update_project(self, project_id: int, data) -> dict[str, Any]:
        """
        Update a project with duplicate name checking.

        Args:
            data: ProjectUpdate schema instance

        Returns:
            Dict with success, project_id, message
        """
        project = self._get_project(project_id)

        if data.name:
            self._check_name_unique(data.name, exclude_id=project_id)
            if data.name != project.name:
                # Freeze auto-derived encryption passphrase before renaming so
                # existing encrypted state remains readable.
                from services.project_service import _freeze_encryption_passphrase_before_rename
                _freeze_encryption_passphrase_before_rename(self.db, project)
            project.name = data.name
        if data.description is not None:
            project.description = data.description
        if data.color:
            project.color = data.color
        if data.icon:
            project.icon = data.icon
        if data.project_type is not None:
            project.project_type = data.project_type
        if data.cloud_provider is not None:
            project.cloud_provider = data.cloud_provider
        if data.backend_type is not None:
            project.backend_type = data.backend_type
        if data.region is not None:
            project.region = data.region
        if data.state_config is not None:
            self._apply_state_config(project, data.state_config)
        if hasattr(data, "credential_template_id"):
            project.credential_template_id = data.credential_template_id
        if data.enabled is not None:
            project.is_active = data.enabled

        self.db.commit()
        self.db.refresh(project)
        invalidate_cache("projects:*")

        return {
            "success": True,
            "project_id": project.id,
            "message": "Project updated successfully",
        }

    @staticmethod
    def _apply_state_config(project, state_config: dict | None) -> None:
        """Persist project state backend settings into ProjectBackendConfig."""
        if state_config is None:
            return

        backend = project.backend_config
        if backend is None:
            backend = ProjectBackendConfig(project=project)
            project.backend_config = backend

        backend.s3_state_bucket = state_config.get("s3_state_bucket")
        backend.s3_state_key_prefix = state_config.get("s3_state_key_prefix")
        backend.s3_state_region = state_config.get("s3_state_region")
        backend.s3_use_native_locking = state_config.get("s3_use_native_locking", True)
        backend.s3_dynamodb_table = state_config.get("s3_dynamodb_table")
        backend.state_storage_provider = state_config.get("state_storage_provider")
        backend.s3_endpoint = state_config.get("s3_endpoint")

    def update_dependencies(
        self, project_id: int, dependencies: list
    ) -> dict[str, Any]:
        """Update cross-project dependencies."""
        project = self._get_project(project_id)

        for dep in dependencies:
            dep_project = (
                self.db.query(Project).filter(Project.id == dep.project_id).first()
            )
            if not dep_project:
                raise NotFoundError("project", dep.project_id)
            if dep.project_id == project_id:
                raise BadRequestError(
                    "Cannot create circular dependency - project cannot depend on itself"
                )

        project.dependent_projects = [d.model_dump() for d in dependencies]
        self.db.commit()
        self.db.refresh(project)

        return {
            "success": True,
            "message": f"Updated dependencies for project '{project.name}'",
            "dependent_projects": dependencies,
        }

    # ================================================================
    # Delete
    # ================================================================

    def delete_project(self, project_id: int, force: bool = False) -> dict[str, Any]:
        """
        Delete a project with workspace lock validation and cleanup.

        Args:
            project_id: Project ID
            force: Allow deletion of last/only project

        Returns:
            Dict with success and message
        """
        project = self._get_project(project_id)

        self._validate_deletion(project, force)
        self._check_workspace_locks(project_id)
        self._cleanup_workspaces(project_id)

        self.db.delete(project)
        self.db.commit()
        invalidate_cache("projects:*")

        return {
            "success": True,
            "message": f"Project '{project.name}' deleted successfully",
        }

    # ================================================================
    # Activate
    # ================================================================

    def activate_project(self, project_id: int) -> dict[str, Any]:
        """Set a project as active, deactivating all others."""
        project = self._get_project(project_id)
        self.db.query(Project).update({"is_active": False})
        project.is_active = True
        self.db.commit()
        invalidate_cache("projects:*")
        return {
            "success": True,
            "project_id": project.id,
            "name": project.name,
            "message": f"Switched to project '{project.name}'",
        }

    # ================================================================
    # Private Helpers — Type/Provider/Region Resolution
    # ================================================================

    def _resolve_default_template_id(self, cloud_provider: str | None) -> int | None:
        """Return the id of the best-match is_default credential template, or None.

        Safety rule: only bind a default template when its provider matches the
        project's cloud_provider.  Binding a GCP is_default template to an AWS
        project (or vice-versa) would inject wrong-provider credentials and produce
        a confusing 401.  Therefore:
        - If cloud_provider is known: return the is_default template for that
          provider only.  If none exists, return None (no cross-provider fallback).
        - If cloud_provider is unknown/empty: return None — we cannot safely choose
          a provider-specific default without knowing the project's provider.
        Returns None if no matching default exists or if the query fails — project
        creation must not fail when no default template is configured.
        """
        if not self.db:
            return None
        if not cloud_provider:
            # Cannot safely pick a provider-matched default without knowing the provider.
            return None
        try:
            matched = (
                self.db.query(CloudCredentialTemplate)
                .filter(
                    CloudCredentialTemplate.is_default.is_(True),
                    CloudCredentialTemplate.provider == cloud_provider,
                )
                .first()
            )
            if matched:
                logger.info(
                    "Defaulting project credential_template_id to template '%s' (id=%s, provider=%s)",
                    matched.name, matched.id, matched.provider,
                )
                return matched.id
        except Exception as e:
            logger.warning("Could not resolve default credential template: %s", e)
        return None

    def _check_name_unique(
        self, name: str, exclude_id: int | None = None
    ) -> None:
        """Raise ConflictError if a project with this name already exists."""
        query = self.db.query(Project).filter(Project.name == name)
        if exclude_id is not None:
            query = query.filter(Project.id != exclude_id)
        if query.first():
            raise ConflictError(
                "project",
                "A project with this name already exists. Please choose a different name.",
            )

    def _resolve_type_and_provider(
        self,
        project_type: str | None,
        cloud_provider: str | None,
        credential_template_id: int | None,
    ) -> tuple:
        """
        Infer project_type and cloud_provider from each other or from credential template.

        Returns:
            (project_type, cloud_provider) tuple
        """
        cloud_provider = cloud_provider or ""

        # If project_type provided but cloud_provider not, derive it
        if project_type and not cloud_provider:
            cloud_provider = TYPE_TO_PROVIDER.get(project_type, "")

        # If neither provided, try to infer from credential template
        if not project_type and credential_template_id:
            template = (
                self.db.query(CloudCredentialTemplate)
                .filter(CloudCredentialTemplate.id == credential_template_id)
                .first()
            )
            if template:
                project_type = PROVIDER_TO_TYPE.get(template.provider)
                if not cloud_provider:
                    cloud_provider = (
                        template.provider
                        if template.provider != "ssh"
                        else "on-prem"
                    )

        return project_type, cloud_provider

    def _resolve_region(
        self,
        region: str | None,
        cloud_provider: str,
        project_type: str | None,
        credential_template_id: int | None,
        project_name: str,
    ) -> str | None:
        """
        Resolve the deployment region, inheriting from credential template for cloud projects.
        """
        managed_clouds = {"aws", "azure", "gcp", "ibm"}
        if not (
            cloud_provider in managed_clouds
            or (project_type and project_type.startswith("cloud-"))
        ):
            return region

        default_region_key = {
            "aws": "cloud.aws.default_region",
            "azure": "cloud.azure.default_region",
            "gcp": "cloud.gcp.default_region",
            "ibm": "cloud.ibm.default_region",
        }.get(cloud_provider)

        default_region = get_default(self.db, default_region_key) if default_region_key else None
        if not region:
            region = default_region

        if credential_template_id:
            template = (
                self.db.query(CloudCredentialTemplate)
                .filter(CloudCredentialTemplate.id == credential_template_id)
                .first()
            )
            if template:
                if not region or region == default_region:
                    region = template.region or default_region
                    logger.info(
                        f"Project '{project_name}' inheriting region '{region}' "
                        f"from template '{template.name}'"
                    )
                elif region != template.region:
                    logger.warning(
                        f"Project '{project_name}' deploying to {region} "
                        f"but credential template '{template.name}' is configured "
                        f"for {template.region}. Ensure IAM policies allow "
                        f"multi-region operations."
                    )

        return region

    # ================================================================
    # Private Helpers — Deletion
    # ================================================================

    def _validate_deletion(self, project: Project, force: bool) -> None:
        """Validate that a project can be deleted."""
        if not project.is_active:
            return

        total_projects = self.db.query(Project).count()
        if total_projects == 1 and force:
            logger.warning(f"Force deleting last/only project: {project.name}")
        elif total_projects > 1:
            raise BadRequestError(
                "Cannot delete active project. Switch to another project first."
            )
        else:
            raise BadRequestError(
                "This is your only project. Use force=true to delete it anyway.",
                details={"requires_force": True},
            )

    def _check_workspace_locks(self, project_id: int) -> None:
        """Check if any module in the project is held by a live worker.

        Stale locks (heartbeat older than reclaim window) are treated as free
        — the next acquire would auto-reclaim them, so they should not block
        delete.
        """
        from services.module_lock import ModuleLockService

        svc = ModuleLockService(self.db)
        held = [lock for lock in svc.list_held() if lock.get("project_id") == project_id]
        live_held = [lock for lock in held if svc.is_held(lock["module_id"])]
        if live_held:
            locked_modules = [str(lock["module_id"]) for lock in live_held]
            raise ConflictError(
                "project",
                f"Cannot delete: operations in progress on modules: {', '.join(locked_modules)}",
            )

    def _cleanup_workspaces(self, project_id: int) -> None:
        """Clean up persistent workspaces for all modules in the project."""
        from services.workspace_manager import WorkspaceManager

        workspace = WorkspaceManager(self.db)
        workspaces_cleaned = workspace.cleanup_project_workspaces(project_id)
        if workspaces_cleaned > 0:
            logger.info(
                f"Cleaned up {workspaces_cleaned} persistent workspaces for project {project_id}"
            )

    # ================================================================
    # Private Helpers — Serialization
    # ================================================================

    def _serialize_list_item(self, project: Project) -> dict[str, Any]:
        """Serialize a project for the list response."""
        backend = project.backend_config if project.backend_config else project
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "color": project.color,
            "icon": project.icon,
            "is_active": project.is_active,
            "has_deployments": (project.deployed_count or 0) > 0,
            "module_count": project.module_count,
            "deployed_count": project.deployed_count,
            "failed_count": project.failed_count,
            "cluster_count": len(project.k8s_clusters) if project.k8s_clusters else 0,
            "owner": project.owner,
            # MU-001: Ownership
            "user_id": project.user_id,
            "owner_username": project.user.username if project.user else None,
            "team": project.team,
            "visibility": project.visibility,
            "project_type": project.project_type,
            "k8s_context": project.k8s_context,
            "k8s_sync_enabled": project.k8s_sync_enabled,
            "k8s_sync_interval_seconds": project.k8s_sync_interval_seconds,
            "cloud_provider": project.cloud_provider,
            "cloud_sync_enabled": project.cloud_sync_enabled,
            "cloud_sync_interval_seconds": project.cloud_sync_interval_seconds,
            "backend_type": project.backend_type,
            "region": project.region,
            "aws_profile": project.aws_profile,
            "state_storage_provider": getattr(backend, "state_storage_provider", None),
            "s3_state_bucket": getattr(backend, "s3_state_bucket", None),
            "s3_state_key_prefix": getattr(backend, "s3_state_key_prefix", None),
            "s3_state_region": getattr(backend, "s3_state_region", None),
            "s3_use_native_locking": getattr(backend, "s3_use_native_locking", None),
            "s3_endpoint": getattr(backend, "s3_endpoint", None),
            "credential_template_id": project.credential_template_id,
            "credential_template": {
                "id": project.credential_template.id,
                "name": project.credential_template.name,
                "provider": project.credential_template.provider,
            }
            if project.credential_template
            else None,
            # First-class SSH credential
            "ssh_credential_id": project.ssh_credential_id,
            "ssh_credential": {
                "id": project.ssh_credential.id,
                "name": project.ssh_credential.name,
                "host": project.ssh_credential.host,
                "username": project.ssh_credential.username,
            } if project.ssh_credential else None,
            "infra_enabled": project.infra_enabled,
            "infra_host": project.infra_host,
            "infra_ssh_port": project.infra_ssh_port,
            "infra_ssh_username": project.infra_ssh_username,
            "infra_auth_type": project.infra_auth_type,
            "infra_os_type": project.infra_os_type,
            "created_at": project.created_at.isoformat()
            if project.created_at
            else None,
        }

    def _serialize_detail(self, project: Project) -> dict[str, Any]:
        """Serialize a project for the detail response."""
        backend = project.backend_config if project.backend_config else project
        return {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "color": project.color,
            "icon": project.icon,
            "is_active": project.is_active,
            "has_deployments": (project.deployed_count or 0) > 0,
            "module_count": project.module_count or 0,
            "deployed_count": project.deployed_count or 0,
            "failed_count": project.failed_count or 0,
            "owner": project.owner,
            # MU-001: Ownership
            "user_id": project.user_id,
            "owner_username": project.user.username if project.user else None,
            "team": project.team,
            "visibility": project.visibility,
            "project_type": project.project_type,
            "k8s_context": project.k8s_context,
            "k8s_sync_enabled": project.k8s_sync_enabled,
            "k8s_sync_interval_seconds": project.k8s_sync_interval_seconds,
            "cloud_provider": project.cloud_provider,
            "cloud_sync_enabled": project.cloud_sync_enabled,
            "cloud_sync_interval_seconds": project.cloud_sync_interval_seconds,
            "backend_type": project.backend_type,
            "region": project.region,
            "aws_profile": project.aws_profile,
            "state_storage_provider": getattr(backend, "state_storage_provider", None),
            "s3_state_bucket": getattr(backend, "s3_state_bucket", None),
            "s3_state_key_prefix": getattr(backend, "s3_state_key_prefix", None),
            "s3_state_region": getattr(backend, "s3_state_region", None),
            "s3_use_native_locking": getattr(backend, "s3_use_native_locking", None),
            "s3_endpoint": getattr(backend, "s3_endpoint", None),
            "credential_template_id": project.credential_template_id,
            # First-class SSH credential
            "ssh_credential_id": project.ssh_credential_id,
            "infra_enabled": project.infra_enabled,
            "infra_host": project.infra_host,
            "infra_ssh_port": project.infra_ssh_port,
            "infra_ssh_username": project.infra_ssh_username,
            "infra_auth_type": project.infra_auth_type,
            "infra_os_type": project.infra_os_type,
            "created_at": project.created_at.isoformat()
            if project.created_at
            else None,
            "updated_at": project.updated_at.isoformat()
            if project.updated_at
            else None,
        }
