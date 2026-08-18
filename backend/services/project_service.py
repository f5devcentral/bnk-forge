"""
Project Service Functions (v2)

Handles project-related operations for BNK-Forge v2.

v2 Architecture Changes:
- No git repository management (removed)
- No Terragrunt scanning (removed)
- No HCL generation (removed)
- Variables stored in database, not in files
- Direct OpenTofu execution with runtime injection

IMP-002: ProjectService class encapsulates all project business logic.
Standalone functions (detect_module_dependencies, update_project_counts) remain
at module level for backward compatibility.
"""

import logging

from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload, subqueryload

# Core imports
from core.cache import cache, invalidate_cache
from core.errors import BadRequestError, ConflictError, NotFoundError
from core.platform_context import (
    MANAGEMENT_BOUNDARY_VALUES,
    PLATFORM_PROFILE_VALUES,
    PLATFORM_PROVIDER_VALUES,
    PlatformProfile,
)

# Model imports
from models import CloudCredentialTemplate, Project, ProjectBackendConfig, ProjectModule, User

# Service imports
from services.base_service import BaseService
from services.defaults_service import get_default
from services.platform_context_service import PlatformContextService

# Configure logging
logger = logging.getLogger(__name__)


# ============================================================
# Encryption passphrase safety
# ============================================================

def _freeze_encryption_passphrase_before_rename(db: Session, project: "Project") -> None:
    """Persist the auto-derived encryption passphrase before a project rename.

    The default PBKDF2 passphrase is derived from ``project.name`` which is
    mutable.  If a project is renamed while modules have encrypted state, the
    derived passphrase changes and existing state becomes unreadable.

    This function snapshots the *current* derived passphrase into the durable
    ``encryption_passphrase_encrypted`` column so that future operations
    (including post-rename ones) use the frozen value instead of re-deriving.

    If the project already has an explicit passphrase stored, this is a no-op.
    """
    enc = project.encryption_config if project.encryption_config else project
    if not enc.state_encryption_enabled:
        return
    if (enc.encryption_provider or "pbkdf2") != "pbkdf2":
        return
    if enc.encryption_passphrase_encrypted:
        return  # already persisted — safe

    import hashlib

    from core.encryption import encrypt_value

    passphrase = hashlib.sha256(
        f"bnk-forge-{project.id}-{project.name}".encode()
    ).hexdigest()[:32]
    enc.encryption_passphrase_encrypted = encrypt_value(passphrase)
    db.flush()  # flush within the same transaction; caller will commit
    logger.info(
        "Froze encryption passphrase for project %s before rename "
        "(was derived from name '%s')",
        project.id,
        project.name,
    )


# ============================================================
# ProjectService (IMP-002)
# ============================================================

def summarize_module_state(deployed_count: int | None, failed_count: int | None) -> str:
    """One field a client can poll for teardown completion.

    "clean" / "in_progress" / "failed".

    deployed_count alone cannot distinguish a finished teardown from a wholly
    failed one: a destroy_failed module reports deployed_count=0, failed_count=1,
    so it leaves deployed_count entirely and looks identical to success. A
    teardown script that polled deployed_count == 0 therefore proceeded to
    DELETE the project while a ROKS cluster was still live (issue #125).

    Callers should treat anything other than "clean" as "not finished".
    """
    if (failed_count or 0) > 0:
        return "failed"
    if (deployed_count or 0) > 0:
        return "in_progress"
    return "clean"


class ProjectService(BaseService):
    """Encapsulates all project business logic.

    Extracted from routes/projects.py so that route handlers become
    thin controllers (2-5 lines each).
    """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _project_to_list_dict(project) -> dict:
        """Convert a Project ORM object to the list-item dict shape."""
        backend = project.backend_config if project.backend_config else project
        normalized_target_platform_profile = PlatformContextService.normalize_project_value(
            getattr(project, "target_platform_profile", None),
            PLATFORM_PROFILE_VALUES,
        )
        target_platform_profile = ProjectService._derive_target_platform_profile_for_read(
            project,
            normalized_target_platform_profile,
        )
        platform_provider = PlatformContextService.normalize_project_value(
            getattr(project, "platform_provider", None),
            PLATFORM_PROVIDER_VALUES,
        )
        management_boundary = PlatformContextService.normalize_project_value(
            getattr(project, "management_boundary", None),
            MANAGEMENT_BOUNDARY_VALUES,
        )

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
            "module_state": summarize_module_state(project.deployed_count, project.failed_count),
            "cluster_count": len(project.k8s_clusters) if project.k8s_clusters else 0,
            "owner": project.owner,
            "team": project.team,
            "visibility": project.visibility,
            # MU-001: Ownership
            "user_id": project.user_id,
            "owner_username": project.user.username if project.user else None,
            # Project type
            "project_type": project.project_type,
            "target_platform_profile": target_platform_profile,
            "platform_provider": platform_provider,
            "management_boundary": management_boundary,
            # K8s settings
            "k8s_context": project.k8s_context,
            "k8s_sync_enabled": project.k8s_sync_enabled,
            "k8s_sync_interval_seconds": project.k8s_sync_interval_seconds,
            # Cloud provider settings
            "cloud_provider": project.cloud_provider,
            "cloud_sync_enabled": project.cloud_sync_enabled,
            "cloud_sync_interval_seconds": project.cloud_sync_interval_seconds,
            # Terraform backend settings
            "backend_type": project.backend_type,
            "region": project.region,
            "aws_profile": project.aws_profile,
            "state_storage_provider": getattr(backend, "state_storage_provider", None),
            "s3_state_bucket": getattr(backend, "s3_state_bucket", None),
            "s3_state_key_prefix": getattr(backend, "s3_state_key_prefix", None),
            "s3_state_region": getattr(backend, "s3_state_region", None),
            "s3_use_native_locking": getattr(backend, "s3_use_native_locking", None),
            "s3_endpoint": getattr(backend, "s3_endpoint", None),
            # Credential template
            "credential_template_id": project.credential_template_id,
            "credential_template": {
                "id": project.credential_template.id,
                "name": project.credential_template.name,
                "provider": project.credential_template.provider,
                "has_ibmcloud_api_key": bool(project.credential_template.ibmcloud_api_key_encrypted),
                "ibmcloud_resource_group": project.credential_template.ibmcloud_resource_group,
            } if project.credential_template else None,
            # First-class SSH credential
            "ssh_credential_id": project.ssh_credential_id,
            "ssh_credential": {
                "id": project.ssh_credential.id,
                "name": project.ssh_credential.name,
                "host": project.ssh_credential.host,
                "username": project.ssh_credential.username,
            } if project.ssh_credential else None,
            # On-Prem Infrastructure Host settings
            "infra_enabled": project.infra_enabled,
            "infra_host": project.infra_host,
            "infra_ssh_port": project.infra_ssh_port,
            "infra_ssh_username": project.infra_ssh_username,
            "infra_auth_type": project.infra_auth_type,
            "infra_os_type": project.infra_os_type,
            "created_at": project.created_at.isoformat() if project.created_at else None,
        }

    @staticmethod
    def _field_was_supplied(payload, field_name: str) -> bool:
        """Return True when a field was explicitly provided by the caller."""
        fields_set = getattr(payload, "model_fields_set", None)
        if isinstance(fields_set, set):
            return field_name in fields_set
        return hasattr(payload, field_name)

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

    @staticmethod
    def _derive_target_platform_profile_for_read(project, normalized_profile: str | None) -> str | None:
        """Return truthful target-platform profile for legacy project rows.

        Legacy projects created before platform-context defaults can have
        ``target_platform_profile`` unset even when project shape is clearly
        kubernetes/on-prem. For read serialization only, conservatively expose
        the generic on-prem baseline instead of surfacing unknown/null.
        """
        if normalized_profile:
            return normalized_profile

        project_type = (getattr(project, "project_type", None) or "").strip().lower()
        cloud_provider = (getattr(project, "cloud_provider", None) or "").strip().lower()
        if (
            project_type == "kubernetes"
            or cloud_provider in {"on-prem", "onprem", "baremetal", "vmware", "vsphere"}
        ):
            return PlatformProfile.GENERIC_ONPREM.value

        return None

    @staticmethod
    def _project_to_detail_dict(project) -> dict:
        """Convert a Project ORM object to the detail dict shape."""
        backend = project.backend_config if project.backend_config else project
        normalized_target_platform_profile = PlatformContextService.normalize_project_value(
            getattr(project, "target_platform_profile", None),
            PLATFORM_PROFILE_VALUES,
        )
        target_platform_profile = ProjectService._derive_target_platform_profile_for_read(
            project,
            normalized_target_platform_profile,
        )
        platform_provider = PlatformContextService.normalize_project_value(
            getattr(project, "platform_provider", None),
            PLATFORM_PROVIDER_VALUES,
        )
        management_boundary = PlatformContextService.normalize_project_value(
            getattr(project, "management_boundary", None),
            MANAGEMENT_BOUNDARY_VALUES,
        )

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
            "module_state": summarize_module_state(project.deployed_count, project.failed_count),
            "owner": project.owner,
            "team": project.team,
            "visibility": project.visibility,
            # MU-001: Ownership
            "user_id": project.user_id,
            "owner_username": project.user.username if project.user else None,
            # Project type
            "project_type": project.project_type,
            "target_platform_profile": target_platform_profile,
            "platform_provider": platform_provider,
            "management_boundary": management_boundary,
            # K8s settings
            "k8s_context": project.k8s_context,
            "k8s_sync_enabled": project.k8s_sync_enabled,
            "k8s_sync_interval_seconds": project.k8s_sync_interval_seconds,
            # Cloud provider settings
            "cloud_provider": project.cloud_provider,
            "cloud_sync_enabled": project.cloud_sync_enabled,
            "cloud_sync_interval_seconds": project.cloud_sync_interval_seconds,
            # Terraform backend settings
            "backend_type": project.backend_type,
            "region": project.region,
            "aws_profile": project.aws_profile,
            "state_storage_provider": getattr(backend, "state_storage_provider", None),
            "s3_state_bucket": getattr(backend, "s3_state_bucket", None),
            "s3_state_key_prefix": getattr(backend, "s3_state_key_prefix", None),
            "s3_state_region": getattr(backend, "s3_state_region", None),
            "s3_use_native_locking": getattr(backend, "s3_use_native_locking", None),
            "s3_endpoint": getattr(backend, "s3_endpoint", None),
            # Credential template
            "credential_template_id": project.credential_template_id,
            # First-class SSH credential
            "ssh_credential_id": project.ssh_credential_id,
            "ssh_credential": {
                "id": project.ssh_credential.id,
                "name": project.ssh_credential.name,
                "host": project.ssh_credential.host,
                "username": project.ssh_credential.username,
            } if project.ssh_credential else None,
            # Infrastructure SSH settings
            "infra_enabled": project.infra_enabled,
            "infra_host": project.infra_host,
            "infra_ssh_port": project.infra_ssh_port,
            "infra_ssh_username": project.infra_ssh_username,
            "infra_auth_type": project.infra_auth_type,
            "infra_os_type": project.infra_os_type,
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        }

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def list_projects(self, skip_cache: bool = False) -> dict:
        """List all projects with summary counts.

        Returns the same dict shape as ProjectListResponse.
        """
        cache_key = "projects:list"

        if not skip_cache:
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result

        # BE-004: Fix N+1 query - eagerly load k8s_clusters, credential_template, and user
        projects = self.db.query(Project).options(
            joinedload(Project.credential_template).load_only(
                CloudCredentialTemplate.id,
                CloudCredentialTemplate.name,
                CloudCredentialTemplate.provider,
                CloudCredentialTemplate.ibmcloud_api_key_encrypted,
                CloudCredentialTemplate.ibmcloud_resource_group,
            ),
            subqueryload(Project.k8s_clusters),
            joinedload(Project.user).load_only(User.id, User.username),  # MU-001
        ).order_by(Project.name).all()

        project_list = [self._project_to_list_dict(p) for p in projects]

        result = {
            "projects": project_list,
            "total": len(project_list),
        }

        # Cache with 5 minute TTL
        cache.set(cache_key, result, ttl_seconds=300)
        return result

    def create_project(self, project_data, user_id: int | None = None) -> dict:
        """Create a new project.

        Handles type/provider inference, region resolution, and credential
        template logic.  Returns ProjectMutationResponse-shaped dict.

        Args:
            project_data: ProjectCreate schema with project fields.
            user_id: ID of the user creating this project (MU-001 ownership).
        """
        # Check for duplicate name
        existing = self.db.query(Project).filter(Project.name == project_data.name).first()
        if existing:
            raise ConflictError(
                "project",
                "A project with this name already exists. Please choose a different name.",
            )

        # Resolve project_type and cloud_provider
        project_type = project_data.project_type
        cloud_provider = project_data.cloud_provider or ""

        # If project_type provided but cloud_provider not, derive it
        if project_type and not cloud_provider:
            type_to_provider = {
                "cloud-aws": "aws",
                "cloud-azure": "azure",
                "cloud-gcp": "gcp",
                "cloud-ibm": "ibm",
                "kubernetes": "on-prem",
            }
            cloud_provider = type_to_provider.get(project_type, "")

        # If neither provided, try to infer from credential template
        if not project_type and project_data.credential_template_id:
            template = self.db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.id == project_data.credential_template_id
            ).first()
            if template:
                provider_to_type = {
                    "aws": "cloud-aws",
                    "azure": "cloud-azure",
                    "gcp": "cloud-gcp",
                    "ibm": "cloud-ibm",
                    "ssh": "kubernetes",
                }
                project_type = provider_to_type.get(template.provider)
                if not cloud_provider:
                    cloud_provider = template.provider if template.provider != "ssh" else "on-prem"

        # Bind a default credential template when none was supplied so projects
        # are not created with no credentials (see FEAT-0326 / ERR-0026). Only a
        # provider-matched is_default template is bound — never cross-provider.
        credential_template_id = project_data.credential_template_id
        if not credential_template_id:
            credential_template_id = self._resolve_default_template_id(cloud_provider)

        # Determine region - only default to AWS region for cloud projects
        region = project_data.region  # None if not provided - fine for non-cloud

        # PLATFORM-CONTEXT-004 baseline normalization:
        # make current kubeconfig-first/manual-friendly project behavior explicit.
        target_platform_profile = getattr(project_data, "target_platform_profile", None)
        if not target_platform_profile:
            if project_type == "kubernetes" or cloud_provider in {
                "on-prem", "onprem", "baremetal", "vmware", "vsphere",
            }:
                target_platform_profile = PlatformProfile.GENERIC_ONPREM.value
            elif cloud_provider:
                # Auto-derive managed platform profile from cloud_provider
                profile = PlatformContextService._MANAGED_CLOUD_PROFILE_MAP.get(
                    cloud_provider.strip().lower()
                )
                if profile:
                    target_platform_profile = profile

        managed_clouds = {"aws", "azure", "gcp", "ibm"}
        if cloud_provider in managed_clouds or (project_type and project_type.startswith("cloud-")):
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
                template = self.db.query(CloudCredentialTemplate).filter(
                    CloudCredentialTemplate.id == credential_template_id
                ).first()

                if template:
                    # If no region provided or region matches default, inherit from template
                    if not project_data.region or project_data.region == default_region:
                        region = template.region or default_region
                        logger.info(
                            f"Project '{project_data.name}' inheriting region '{region}' "
                            f"from template '{template.name}'"
                        )
                    elif project_data.region != template.region:
                        logger.warning(
                            f"Project '{project_data.name}' deploying to {project_data.region} "
                            f"but credential template '{template.name}' is configured for "
                            f"{template.region}. Ensure IAM policies allow multi-region operations."
                        )

        # Create project with v2 simplified fields (no git)
        project = Project(
            name=project_data.name,
            description=project_data.description,
            project_type=project_type,
            cloud_provider=cloud_provider or None,
            environment=project_data.environment or "dev",
            backend_type=project_data.backend_type or "local",
            region=region,
            color=project_data.color or "#a8337a",
            icon=project_data.icon or "",
            credential_template_id=credential_template_id,
            ssh_credential_id=getattr(project_data, "ssh_credential_id", None),
            target_platform_profile=target_platform_profile,
            platform_provider=getattr(project_data, "platform_provider", None),
            management_boundary=getattr(project_data, "management_boundary", None),
            user_id=user_id,  # MU-001: Set project owner
        )

        self.db.add(project)
        self._apply_state_config(project, getattr(project_data, "state_config", None))
        self.db.commit()
        self.db.refresh(project)

        # Invalidate projects cache after creation
        invalidate_cache("projects:*")

        return {
            "success": True,
            "project_id": project.id,
            "name": project.name,
            "message": "Project created successfully",
        }

    def _resolve_default_template_id(self, cloud_provider: str | None) -> int | None:
        """Return the id of the best-match is_default credential template, or None.

        Safety rule: only bind a default template when its provider matches the
        project's cloud_provider. Binding a wrong-provider default would inject
        credentials that produce a confusing 401, so an unknown/empty provider
        yields None (no cross-provider fallback). Returns None on any failure —
        project creation must not fail when no default template is configured.
        """
        if not cloud_provider:
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

    def get_project_detail(self, project_id: int) -> dict:
        """Get a single project's detail dict.

        Uses BaseService._get_project() for the lookup.
        """
        project = self._get_project(project_id)
        return self._project_to_detail_dict(project)

    def update_project(self, project_id: int, project_data) -> dict:
        """Update a project's fields.

        Validates duplicate names and applies only the provided fields.
        Returns ProjectMutationResponse-shaped dict.
        """
        project = self._get_project(project_id)

        # Update basic fields if provided
        if project_data.name:
            # Check for duplicate name (skip if unchanged)
            if project_data.name != project.name:
                existing = self.db.query(Project).filter(
                    Project.name == project_data.name,
                    Project.id != project_id,
                ).first()
                if existing:
                    raise ConflictError(
                        "project",
                        "A project with this name already exists. Please choose a different name.",
                    )
                # Freeze auto-derived encryption passphrase before renaming so
                # existing encrypted state remains readable.  The passphrase was
                # previously derived from project.name which is about to change.
                _freeze_encryption_passphrase_before_rename(self.db, project)
            project.name = project_data.name
        if project_data.description is not None:
            project.description = project_data.description
        if project_data.color:
            project.color = project_data.color
        if project_data.icon:
            project.icon = project_data.icon

        # Update project type if provided
        if project_data.project_type is not None:
            project.project_type = project_data.project_type
        if project_data.cloud_provider is not None:
            project.cloud_provider = project_data.cloud_provider
        # Same always-true hasattr() as the credential fields below: a partial
        # update was nulling the project's platform routing on every request.
        if self._field_was_supplied(project_data, "target_platform_profile"):
            project.target_platform_profile = project_data.target_platform_profile
        if self._field_was_supplied(project_data, "platform_provider"):
            project.platform_provider = project_data.platform_provider
        if self._field_was_supplied(project_data, "management_boundary"):
            project.management_boundary = project_data.management_boundary

        # PLATFORM-CONTEXT-004 baseline normalization on update:
        # if caller did not explicitly provide target_platform_profile and the
        # resulting shape is kubernetes/on-prem, normalize implicit baseline.
        explicit_target_platform_profile = self._field_was_supplied(
            project_data,
            "target_platform_profile",
        )
        if not explicit_target_platform_profile and not project.target_platform_profile:
            if (
                project.project_type == "kubernetes"
                or project.cloud_provider in {"on-prem", "onprem", "baremetal", "vmware", "vsphere"}
            ):
                project.target_platform_profile = PlatformProfile.GENERIC_ONPREM.value
            elif project.cloud_provider:
                profile = PlatformContextService._MANAGED_CLOUD_PROFILE_MAP.get(
                    project.cloud_provider.strip().lower()
                )
                if profile:
                    project.target_platform_profile = profile

        # Update backend settings if provided
        if project_data.backend_type is not None:
            project.backend_type = project_data.backend_type
        if project_data.region is not None:
            project.region = project_data.region
        if project_data.state_config is not None:
            self._apply_state_config(project, project_data.state_config)

        # Only assign when the CALLER sent the field. hasattr() is always True
        # for a declared Pydantic field, so the previous checks fired on every
        # request and wrote the field's default of None — a partial update that
        # never mentioned the credential template silently detached it.
        #
        # The damage is not cosmetic: get_cloud_credentials_env() derives
        # IBMCLOUD_API_KEY (and the AWS/Azure/GCP equivalents) from the project's
        # template, so once it is cleared every subsequent module runs with no
        # cloud credentials at all. Because the project row is touched as modules
        # complete, the first module in a blueprint succeeds and every later one
        # fails with "no IBM Cloud API key", which reads like a module bug rather
        # than a project one.
        #
        # _field_was_supplied() is the existing expression of "if provided" —
        # already used below for target_platform_profile — so this uses it rather
        # than reading model_fields_set inline a second time.
        if self._field_was_supplied(project_data, "credential_template_id"):
            project.credential_template_id = project_data.credential_template_id

        if self._field_was_supplied(project_data, "ssh_credential_id"):
            project.ssh_credential_id = project_data.ssh_credential_id

        # Update enabled status if provided
        if project_data.enabled is not None:
            project.is_active = project_data.enabled

        self.db.commit()
        self.db.refresh(project)

        # Invalidate projects cache after update
        invalidate_cache("projects:*")

        return {
            "success": True,
            "project_id": project.id,
            "message": "Project updated successfully",
        }

    def delete_project(self, project_id: int, force: bool = False) -> dict:
        """Delete a project, including workspace cleanup.

        Checks active status, workspace locks, and cleans up persistent
        workspaces before cascade-deleting the project.
        Returns SuccessResponse-shaped dict.
        """
        project = self._get_project(project_id)

        # Refuse to delete a project whose modules may still own cloud resources.
        #
        # Forge holds the ONLY record of what a module built, so deleting the
        # project orphans those resources with no retry path — the cluster keeps
        # billing and nothing in Forge points at it. Reported on 3.1.6: a
        # destroy-all returned non-zero, DELETE succeeded 22 seconds later, and a
        # live ROKS cluster plus its VPC, three subnets and three public gateways
        # had to be removed by hand (issue #125).
        #
        # NO_INFRA_STATUSES is the existing definition of "this module has
        # nothing left to destroy"; anything else — applied, applying,
        # destroying, apply_failed, destroy_failed — may still own something.
        #
        # This runs FIRST, ahead of the active-project and live-lock gates, and
        # that ordering is load-bearing. The only-project gate below refuses with
        # "use force=true to delete it anyway" — advice about project count, not
        # about infrastructure. A user who follows it passes the one flag that
        # also disarms this gate, which is precisely how #125 happened. Checking
        # infra first means force=true is only ever suggested to someone whose
        # modules are already destroyed.
        #
        # force=true remains available: deliberately abandoning resources is a
        # legitimate operation. It just must not be the default, because a
        # project that still owns resources is recoverable and a deleted one is
        # not.
        from tasks.parallel_tasks import NO_INFRA_STATUSES

        undestroyed = [
            m for m in self.db.query(ProjectModule)
            .filter(ProjectModule.project_id == project_id).all()
            if (m.status or "") not in NO_INFRA_STATUSES
        ]
        if undestroyed and not force:
            raise ConflictError(
                "project",
                f"Cannot delete: {len(undestroyed)} module(s) are not destroyed and may "
                f"still own cloud resources. Destroy them first, or pass force=true to "
                f"delete the project and abandon those resources.",
                details={
                    "requires_force": True,
                    "undestroyed_modules": [
                        {
                            "id": m.id,
                            "name": (m.library_module.name if m.library_module else m.path_in_project),
                            "status": m.status,
                        }
                        for m in undestroyed
                    ],
                },
            )

        # Check if this is the only project
        total_projects = self.db.query(Project).count()

        # Don't allow deleting active project UNLESS it's the only one and force=True
        if project.is_active:
            if total_projects == 1 and force:
                logger.warning(f"Force deleting last/only project: {project.name}")
            elif total_projects > 1:
                raise BadRequestError("Cannot delete active project. Switch to another project first.")
            else:
                # Only project and no force flag
                raise BadRequestError(
                    "This is your only project. Use force=true to delete it anyway.",
                    details={"requires_force": True},
                )

        # Check if any module is locked by a LIVE worker before deletion.
        # Stale locks (heartbeat older than the reclaim window) are treated as
        # free — they will auto-clear on the next acquire, so they don't need
        # the legacy force=true Redis-release dance. force=true still bypasses
        # the live-holder gate as an admin escape hatch; the worker that loses
        # its row to ON DELETE CASCADE will fail its next under-lock UPDATE
        # via the fence-token guard.
        from services.module_lock import ModuleLockService
        svc = ModuleLockService(self.db)
        held = [lock for lock in svc.list_held() if lock.get("project_id") == project_id]
        live_held = [lock for lock in held if svc.is_held(lock["module_id"])]
        if live_held and not force:
            locked_modules = [str(lock["module_id"]) for lock in live_held]
            raise ConflictError(
                "project",
                f"Cannot delete: operations in progress on modules: {', '.join(locked_modules)}. "
                f"Use force=true to override the live-holder gate.",
            )

        # Clean up persistent workspaces for all modules in the project (WORK-006)
        from services.workspace_manager import WorkspaceManager
        workspace = WorkspaceManager(self.db)
        workspaces_cleaned = workspace.cleanup_project_workspaces(project_id)
        if workspaces_cleaned > 0:
            logger.info(f"Cleaned up {workspaces_cleaned} persistent workspaces for project {project_id}")

        # Delete project from database (cascade will delete modules and related resources)
        project_name = project.name
        self.db.delete(project)
        self.db.commit()

        # Invalidate projects cache after deletion
        invalidate_cache("projects:*")

        return {
            "success": True,
            "message": f"Project '{project_name}' deleted successfully",
        }

    def set_active_project(self, project_id: int) -> dict:
        """Set a project as active, deactivating all others.

        Returns ProjectMutationResponse-shaped dict.
        """
        project = self._get_project(project_id)

        # Deactivate only the currently active project(s) - not a full table UPDATE
        self.db.query(Project).filter(
            Project.is_active,
            Project.id != project_id,
        ).update({"is_active": False})

        # Activate selected project
        project.is_active = True

        self.db.commit()

        # Invalidate project cache so UI reflects new active state immediately
        invalidate_cache("projects:*")

        return {
            "success": True,
            "project_id": project.id,
            "name": project.name,
            "message": f"Switched to project '{project.name}'",
        }

    def transfer_ownership(self, project_id: int, new_owner_id: int) -> dict:
        """MU-009: Transfer project ownership to another user.

        Returns TransferOwnershipResponse-shaped dict.
        Records an explicit audit entry with before/after ownership details.
        """
        from services.audit_service import create_audit_log

        project = self._get_project(project_id)

        # Validate new owner exists and is active
        new_owner = self.db.query(User).filter(User.id == new_owner_id).first()
        if not new_owner:
            raise NotFoundError("user", new_owner_id)
        if not new_owner.is_active:
            raise BadRequestError(f"Cannot transfer to inactive user '{new_owner.username}'")

        # Get previous owner info
        previous_owner_id = project.user_id
        previous_owner_name = None
        if project.user_id:
            prev_user = self.db.query(User).filter(User.id == project.user_id).first()
            if prev_user:
                previous_owner_name = prev_user.username

        # Transfer
        project.user_id = new_owner_id
        self.db.commit()

        # Invalidate project cache
        invalidate_cache("projects:*")

        logger.info(
            "Transferred project %d ('%s') from %s to %s",
            project.id, project.name, previous_owner_name or "unowned", new_owner.username,
        )

        # MU-011: Explicit audit trail for ownership change
        create_audit_log(
            self.db,
            action="transfer_ownership",
            resource_type="project",
            resource_id=str(project.id),
            resource_name=project.name,
            user=previous_owner_name or "system",
            user_id=previous_owner_id,
            details={
                "previous_owner_id": previous_owner_id,
                "previous_owner": previous_owner_name,
                "new_owner_id": new_owner_id,
                "new_owner": new_owner.username,
            },
        )

        return {
            "success": True,
            "project_id": project.id,
            "project_name": project.name,
            "previous_owner": previous_owner_name,
            "new_owner": new_owner.username,
            "message": f"Transferred '{project.name}' to {new_owner.username}",
        }

    def update_dependencies(self, project_id: int, dependencies: list) -> dict:
        """Update cross-project dependencies for a project.

        Validates that dependent projects exist and checks for circular
        dependencies.  Returns ProjectDependenciesResponse-shaped dict.
        """
        project = self._get_project(project_id)

        # Validate that dependent projects exist
        for dep in dependencies:
            dep_project = self.db.query(Project).filter(Project.id == dep.project_id).first()
            if not dep_project:
                raise NotFoundError("project", dep.project_id)

            # Check for circular dependencies
            if dep.project_id == project_id:
                raise BadRequestError("Cannot create circular dependency - project cannot depend on itself")

        # Update dependencies (serialize Pydantic models to dicts for JSON column)
        project.dependent_projects = [d.model_dump() for d in dependencies]
        self.db.commit()
        self.db.refresh(project)

        return {
            "success": True,
            "message": f"Updated dependencies for project '{project.name}'",
            "dependent_projects": dependencies,
        }


# ============================================================
# Module Dependency Detection
# ============================================================

# Common module dependency patterns — provider-neutral.
# Used as fallback when module.json metadata is not available.
# Preferred path: module.json metadata (see detect_module_dependencies below).
MODULE_DEPENDENCY_RULES = {
    # Network infrastructure (base layer - no dependencies)
    'vpc': [],
    'vnet': [],       # Azure
    'network': [],
    'networking': [],

    # Compute depends on networking
    'eks': ['vpc', 'network'],      # AWS
    'aks': ['vnet', 'network'],     # Azure
    'gke': ['network'],             # GCP
    'kubernetes': ['vpc', 'vnet', 'network'],
    'k8s': ['vpc', 'vnet', 'network'],

    # Database depends on networking
    'database': ['vpc', 'vnet', 'network'],
    'rds': ['vpc', 'network'],
    'aurora': ['vpc', 'network'],

    # Storage — some types are global (S3, GCS), others need networking
    'storage': ['vpc', 'vnet', 'network'],

    # Application services depend on compute
    'bnk': ['eks', 'aks', 'gke', 'kubernetes', 'k8s'],
    'f5': ['eks', 'aks', 'gke', 'kubernetes', 'k8s'],
    'big-ip': ['eks', 'aks', 'gke', 'kubernetes', 'k8s'],
    'ingress': ['eks', 'aks', 'gke', 'kubernetes', 'k8s'],
    'load-balancer': ['vpc', 'vnet', 'network'],

    # Security dependencies
    'security': ['vpc', 'vnet', 'network'],
    'firewall': ['vpc', 'vnet', 'network'],

    # Monitoring depends on resources being monitored
    'monitoring': ['vpc', 'vnet', 'eks', 'aks', 'gke', 'kubernetes'],
    'prometheus': ['eks', 'aks', 'gke', 'kubernetes'],
}


def detect_module_dependencies(library_module, existing_modules: list) -> list:
    """
    Detect dependencies for a module using module.json metadata.
    Falls back to hardcoded rules if metadata is not available.

    Args:
        library_module: ModuleLibrary object with dependencies_metadata
        existing_modules: List of existing ProjectModule objects in the project

    Returns:
        list: List of module IDs that this module depends on
    """
    dependencies = []

    # Try to use module.json metadata first (preferred in v2)
    if library_module and hasattr(library_module, 'dependencies_metadata') and library_module.dependencies_metadata:
        required_deps = library_module.dependencies_metadata.get("required", [])

        # Build lookup: module_path -> ProjectModule.id (with flexible matching)
        existing_by_path = {}
        for pm in existing_modules:
            if pm.library_module:
                existing_by_path[pm.library_module.path] = pm.id

        for dep in required_deps:
            dep_module_path = dep.get("module")  # e.g., "infra/aws/vpc"
            matched_id = None

            # Try exact match first
            if dep_module_path in existing_by_path:
                matched_id = existing_by_path[dep_module_path]
            else:
                # Try flexible matching (handle "infra/aws/vpc" vs "aws/vpc")
                normalized_dep_path = dep_module_path.replace("infra/", "")
                for existing_path, pm_id in existing_by_path.items():
                    normalized_existing_path = existing_path.replace("infra/", "")
                    if normalized_dep_path == normalized_existing_path:
                        matched_id = pm_id
                        logger.info(f"Matched dependency by normalized path: {dep_module_path} -> {existing_path}")
                        break

            if matched_id:
                dependencies.append(matched_id)
                logger.info(f"Resolved dependency from module.json: {library_module.name} depends on {dep_module_path} (ProjectModule {matched_id})")
            else:
                logger.warning(f"Required dependency {dep_module_path} not found in project for module {library_module.name}")

        return dependencies

    # FALLBACK: Use hardcoded rules if no metadata available
    module_name = library_module.name if library_module else "unknown"
    module_name_lower = module_name.lower().replace('-', '_').replace(' ', '_')

    # Find matching rule for this module type
    potential_deps = []
    for key, deps in MODULE_DEPENDENCY_RULES.items():
        if key in module_name_lower or module_name_lower in key:
            potential_deps = deps
            break

    if not potential_deps:
        logger.info(f"No dependency rules found for module: {module_name}")
        return []

    # Find existing modules that match the dependency types
    for dep_type in potential_deps:
        for existing_module in existing_modules:
            existing_name = (existing_module.library_module.name if existing_module.library_module
                           else existing_module.path_in_project.split('/')[-1]).lower()
            existing_name = existing_name.replace('-', '_').replace(' ', '_')

            # Check if this existing module matches the dependency type
            if dep_type in existing_name or existing_name in dep_type:
                if existing_module.id not in dependencies:
                    dependencies.append(existing_module.id)
                    logger.info(f"  Detected dependency (fallback): {module_name} depends on {existing_module.library_module.name if existing_module.library_module else existing_module.path_in_project}")

    return dependencies


# ============================================================
# Project Statistics
# ============================================================

def update_project_counts(db: Session, project_id: int):
    """
    Update denormalized counts for a project based on current module statuses.
    Call this whenever module status changes to keep project stats in sync.

    This maintains project-level statistics that reflect actual module states:
    - module_count: Total modules in project
    - deployed_count: Modules with status "deployed" or "applied"
    - failed_count: Modules with any failed status

    NOTE: is_active is user-controlled ("selected project"), not computed here.
    Use deployed_count > 0 (has_deployments in API) for deployment status.

    Args:
        db: Database session
        project_id: Project ID to update
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return

    # Optimized: Get all counts in a single query
    counts = db.query(
        func.count(ProjectModule.id),
        func.count(case((ProjectModule.status.in_(["applied"]), 1))),
        func.count(case((ProjectModule.status.in_(["failed", "apply_failed", "destroy_failed", "init_failed", "plan_failed"]), 1)))
    ).filter(
        ProjectModule.project_id == project_id
    ).first()

    project.module_count = counts[0] or 0
    project.deployed_count = counts[1] or 0
    project.failed_count = counts[2] or 0

    # NOTE: is_active is user-controlled ("selected project"), NOT computed from deployment state.
    # Use deployed_count > 0 (exposed as has_deployments in API) for deployment status.

    # Invalidate cache since project stats changed
    invalidate_cache("projects:list")
    invalidate_cache(f"projects:{project_id}")


def recompute_all_project_counts(db: Session) -> dict:
    """Idempotent sweep — recompute denormalized counts for every project.

    Run periodically by APScheduler to repair drift when a per-write update
    was missed (worker crash mid-deploy, partial transaction, manual DB edit).
    """
    project_ids = [pid for (pid,) in db.query(Project.id).all()]
    repaired = 0
    for project_id in project_ids:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            continue
        before = (project.module_count, project.deployed_count, project.failed_count)
        update_project_counts(db, project_id)
        after = (project.module_count, project.deployed_count, project.failed_count)
        if before != after:
            repaired += 1
    db.commit()
    return {"projects_scanned": len(project_ids), "projects_repaired": repaired}

    logger.info(f"Updated project {project_id} counts: modules={project.module_count}, deployed={project.deployed_count}, failed={project.failed_count}")
