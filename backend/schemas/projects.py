"""
Pydantic schemas for the Projects domain.

Covers:
  - Project CRUD (create, update, list, detail)
  - Variable defaults and project variables
  - Dependencies
  - Execution plans and parallel executions
  - Credential template summary (nested in project responses)
"""

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from utils.naming import (
    is_aws_safe_name,
    is_gcp_safe_name,
    is_ibm_safe_name,
    slugify_aws_name,
    slugify_gcp_name,
    slugify_ibm_name,
)
from utils.validators import (
    validate_aws_region,
    validate_azure_region,
    validate_cidr_fields,
    validate_gcp_region,
    validate_ibm_region,
)

# =============================================================================
# Shared / Nested Models
# =============================================================================

class CredentialTemplateSummary(BaseModel):
    """Minimal credential template info embedded in project responses."""
    id: int
    name: str
    provider: str
    has_ibmcloud_api_key: bool | None = None
    ibmcloud_resource_group: str | None = None

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Project Create / Update  (moved from schemas/models.py)
# =============================================================================

class ProjectCreate(BaseModel):
    """Schema for creating a new project."""
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, description="Project description")
    project_type: str | None = Field(
        default=None,
        description="Project type: cloud-aws, cloud-azure, cloud-gcp, cloud-ibm, kubernetes",
    )
    cloud_provider: str | None = Field(
        default=None,
        description="Cloud provider: aws, azure, gcp, ibm, on-prem, none",
    )
    environment: str | None = Field(default="dev", description="Environment (dev, staging, prod)")
    region: str | None = Field(default=None, description="AWS/cloud region or on-prem location label")
    backend_type: str | None = Field(default="local", description="Terraform backend type")
    state_config: dict[str, Any] | None = Field(default=None, description="State backend configuration")
    color: str | None = Field(default="#a8337a", description="UI color theme")
    icon: str | None = Field(default="", description="UI icon")
    credential_template_id: int | None = Field(
        default=None, description="Credential template ID to use for this project"
    )
    ssh_credential_id: int | None = Field(
        default=None, description="First-class SSH credential ID for on-prem/bastion access"
    )
    target_platform_profile: str | None = Field(
        default=None,
        description="Declared target platform profile",
    )
    platform_provider: str | None = Field(
        default=None,
        description="Optional declared platform provider",
    )
    management_boundary: str | None = Field(
        default=None,
        description="Optional declared management boundary",
    )

    @model_validator(mode="after")
    def _validate_region(self) -> Self:
        is_aws = self.cloud_provider == "aws" or (self.project_type and self.project_type.startswith("cloud-aws"))
        is_ibm = self.cloud_provider == "ibm" or (self.project_type and self.project_type.startswith("cloud-ibm"))
        is_gcp = self.cloud_provider == "gcp" or (self.project_type and self.project_type.startswith("cloud-gcp"))
        is_azure = self.cloud_provider == "azure" or (self.project_type and self.project_type.startswith("cloud-azure"))
        if is_aws:
            validate_aws_region(self.region, field_name="region")
        if is_ibm:
            validate_ibm_region(self.region, field_name="region")
        if is_gcp:
            validate_gcp_region(self.region, field_name="region")
        if is_azure:
            validate_azure_region(self.region, field_name="region")
        if is_aws and self.name and not is_aws_safe_name(self.name):
            suggested = slugify_aws_name(self.name)
            raise ValueError(
                f"Project name contains characters that break AWS-derived resource names. Use letters, numbers, '+', '=', ',', '.', '@', '-', '_' only, or rename to something like '{suggested}'."
            )
        if is_ibm and self.name and not is_ibm_safe_name(self.name):
            suggested = slugify_ibm_name(self.name)
            raise ValueError(
                f"Project name contains characters that break IBM Cloud resource names. "
                f"Use lowercase letters, digits, and '-' only (max 35 chars, must start with a letter or digit), "
                f"or rename to something like '{suggested}'."
            )
        if is_gcp and self.name and not is_gcp_safe_name(self.name):
            suggested = slugify_gcp_name(self.name)
            raise ValueError(
                f"Project name contains characters that break GCP-derived resource names. "
                f"Use lowercase letters, digits, and '-' only (max 40 chars, must start with a letter and not end with '-'), "
                f"or rename to something like '{suggested}'."
            )
        return self


class ProjectUpdate(BaseModel):
    """Schema for updating a project — all fields optional."""
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    project_type: str | None = None
    cloud_provider: str | None = None
    environment: str | None = None
    region: str | None = None
    backend_type: str | None = None
    state_config: dict[str, Any] | None = None
    enabled: bool | None = None
    credential_template_id: int | None = None
    ssh_credential_id: int | None = None
    color: str | None = None
    icon: str | None = None
    target_platform_profile: str | None = None
    platform_provider: str | None = None
    management_boundary: str | None = None

    @model_validator(mode="after")
    def _validate_region(self) -> Self:
        is_aws = self.cloud_provider == "aws" or (self.project_type and self.project_type.startswith("cloud-aws"))
        is_ibm = self.cloud_provider == "ibm" or (self.project_type and self.project_type.startswith("cloud-ibm"))
        is_gcp = self.cloud_provider == "gcp" or (self.project_type and self.project_type.startswith("cloud-gcp"))
        is_azure = self.cloud_provider == "azure" or (self.project_type and self.project_type.startswith("cloud-azure"))
        if is_aws:
            validate_aws_region(self.region, field_name="region")
        if is_ibm:
            validate_ibm_region(self.region, field_name="region")
        if is_gcp:
            validate_gcp_region(self.region, field_name="region")
        if is_azure:
            validate_azure_region(self.region, field_name="region")
        if is_aws and self.name and not is_aws_safe_name(self.name):
            suggested = slugify_aws_name(self.name)
            raise ValueError(
                f"Project name contains characters that break AWS-derived resource names. Use letters, numbers, '+', '=', ',', '.', '@', '-', '_' only, or rename to something like '{suggested}'."
            )
        if is_ibm and self.name and not is_ibm_safe_name(self.name):
            suggested = slugify_ibm_name(self.name)
            raise ValueError(
                f"Project name contains characters that break IBM Cloud resource names. "
                f"Use lowercase letters, digits, and '-' only (max 35 chars, must start with a letter or digit), "
                f"or rename to something like '{suggested}'."
            )
        if is_gcp and self.name and not is_gcp_safe_name(self.name):
            suggested = slugify_gcp_name(self.name)
            raise ValueError(
                f"Project name contains characters that break GCP-derived resource names. "
                f"Use lowercase letters, digits, and '-' only (max 40 chars, must start with a letter and not end with '-'), "
                f"or rename to something like '{suggested}'."
            )
        return self


# =============================================================================
# Project Responses
# =============================================================================

class ProjectListItem(BaseModel):
    """Single project in a list response — includes summary counts."""
    id: int
    name: str
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    is_active: bool = False
    has_deployments: bool = False
    module_count: int | None = 0
    deployed_count: int | None = 0
    failed_count: int | None = 0
    # Single pollable field for teardown completion: "clean" | "in_progress" | "failed".
    # Defaults to "unknown", not "clean": callers are told to treat anything other
    # than "clean" as unfinished, so a serializer that forgets this field must fail
    # safe rather than report a teardown complete.
    module_state: str = "unknown"
    cluster_count: int = 0
    owner: str | None = None
    team: str | None = None
    visibility: str | None = "private"
    # Project type
    project_type: str | None = None
    target_platform_profile: str | None = None
    platform_provider: str | None = None
    management_boundary: str | None = None
    # K8s settings
    k8s_context: str | None = None
    k8s_sync_enabled: bool | None = False
    k8s_sync_interval_seconds: int | None = 300
    # Cloud provider
    cloud_provider: str | None = None
    cloud_sync_enabled: bool | None = False
    cloud_sync_interval_seconds: int | None = 600
    # Backend settings
    backend_type: str | None = "local"
    region: str | None = None
    aws_profile: str | None = None
    # Credential template
    credential_template_id: int | None = None
    credential_template: CredentialTemplateSummary | None = None
    # First-class SSH credential (for K8s / on-prem projects)
    ssh_credential_id: int | None = None
    ssh_credential: dict | None = None
    # On-prem infra
    infra_enabled: bool | None = False
    infra_host: str | None = None
    infra_ssh_port: int | None = 22
    infra_ssh_username: str | None = None
    infra_auth_type: str | None = "password"
    infra_os_type: str | None = None
    created_at: str | None = None  # ISO string from .isoformat()
    # MU-001: Ownership — must be present for "My Projects" filter to work
    user_id: int | None = None
    owner_username: str | None = None


class ProjectListResponse(BaseModel):
    """Response for GET /api/projects."""
    projects: list[ProjectListItem]
    total: int


class ProjectDetailResponse(BaseModel):
    """Response for GET /api/projects/{project_id}."""
    id: int
    name: str
    description: str | None = None
    color: str | None = None
    icon: str | None = None
    is_active: bool = False
    has_deployments: bool = False
    module_count: int = 0
    deployed_count: int = 0
    failed_count: int = 0
    # Single pollable field for teardown completion: "clean" | "in_progress" | "failed".
    # Defaults to "unknown", not "clean": callers are told to treat anything other
    # than "clean" as unfinished, so a serializer that forgets this field must fail
    # safe rather than report a teardown complete.
    module_state: str = "unknown"
    owner: str | None = None
    team: str | None = None
    visibility: str | None = "private"
    # Project type
    project_type: str | None = None
    target_platform_profile: str | None = None
    platform_provider: str | None = None
    management_boundary: str | None = None
    # K8s settings
    k8s_context: str | None = None
    k8s_sync_enabled: bool | None = False
    k8s_sync_interval_seconds: int | None = 300
    # Cloud provider
    cloud_provider: str | None = None
    cloud_sync_enabled: bool | None = False
    cloud_sync_interval_seconds: int | None = 600
    # Backend settings
    backend_type: str | None = "local"
    region: str | None = None
    aws_profile: str | None = None
    # Credential template
    credential_template_id: int | None = None
    # First-class SSH credential (for K8s / on-prem projects)
    ssh_credential_id: int | None = None
    ssh_credential: dict | None = None
    # Infra
    infra_enabled: bool | None = False
    infra_host: str | None = None
    infra_ssh_port: int | None = 22
    infra_ssh_username: str | None = None
    infra_auth_type: str | None = "password"
    infra_os_type: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # MU-001: Ownership — must be present for project detail ownership checks
    user_id: int | None = None
    owner_username: str | None = None


class ActiveProjectResponse(BaseModel):
    """Response for GET /api/projects/active."""
    active: bool
    project: dict[str, Any] | None = None
    message: str | None = None


# =============================================================================
# Generic success / mutation responses
# =============================================================================

class SuccessResponse(BaseModel):
    """Generic success response for mutating operations."""
    success: bool = True
    message: str
    project_id: int | None = None


class ProjectMutationResponse(BaseModel):
    """Response for project create/update/delete."""
    success: bool = True
    project_id: int | None = None
    name: str | None = None
    message: str


class TransferOwnershipRequest(BaseModel):
    """MU-009: Request body for transferring project ownership."""
    new_owner_id: int = Field(..., gt=0, description="User ID of the new owner")


class TransferOwnershipResponse(BaseModel):
    """MU-009: Response for ownership transfer."""
    success: bool = True
    project_id: int
    project_name: str
    previous_owner: str | None = None
    new_owner: str
    message: str


# =============================================================================
# Dependencies
# =============================================================================

class ProjectDependencyItem(BaseModel):
    """A single cross-project dependency."""
    project_id: int = Field(..., gt=0, description="ID of the project to depend on")
    outputs: list[str] = Field(
        default_factory=list,
        description="List of output variable names to consume from the dependency",
    )


class ProjectDependenciesUpdate(BaseModel):
    """Request body for PUT /api/projects/{id}/dependencies."""
    dependencies: list[ProjectDependencyItem]


class ProjectDependenciesResponse(BaseModel):
    """Response for dependency update."""
    success: bool = True
    message: str
    dependent_projects: list[ProjectDependencyItem]


# =============================================================================
# Variable Defaults
# =============================================================================

class VariableDefaultsUpdate(BaseModel):
    """
    Request body for PUT /api/projects/{id}/variable-defaults.

    Accepts a flat dict of variable_name → default_value.
    """
    defaults: dict[str, Any] = Field(
        ..., description="Mapping of variable names to their default values"
    )

    @model_validator(mode="after")
    def _validate_cidr(self) -> Self:
        validate_cidr_fields(self.defaults)
        return self


class VariableDefaultsResponse(BaseModel):
    """Response for GET /api/projects/{id}/variable-defaults."""
    project_id: int
    project_name: str
    common_defaults: dict[str, Any]
    custom_defaults: dict[str, Any]
    effective_defaults: dict[str, Any]


# =============================================================================
# Project Variables
# =============================================================================

class ProjectVariablesUpdate(BaseModel):
    """
    Request body for PUT /api/projects/{id}/variables.

    Accepts variable_name → value pairs to inject into all module deployments.
    """
    variables: dict[str, Any] = Field(
        ..., description="Mapping of variable names to their values"
    )


class ProjectVariablesResponse(BaseModel):
    """Response for GET /api/projects/{id}/variables."""
    project_id: int
    project_name: str
    variables: dict[str, Any]
    discovered: dict[str, Any] = Field(default_factory=dict)
    configurable: dict[str, Any] = Field(default_factory=dict)
    modules: dict[str, Any] = Field(default_factory=dict)
    effective: dict[str, Any] = Field(default_factory=dict)


class ProjectVariablesMutationResponse(BaseModel):
    """Response for PUT /api/projects/{id}/variables."""
    success: bool = True
    message: str
    variables_count: int
    variables: dict[str, Any]


# =============================================================================
# Execution Plan / Parallel Execution
# =============================================================================

class DeployAllRequest(BaseModel):
    """Request body for POST /api/projects/{id}/deploy-all."""
    parallel: bool = Field(default=False, description="Deprecated flag. Execution is now sequential by default.")


class DestroyAllRequest(BaseModel):
    """Request body for POST /api/projects/{id}/destroy-all."""
    parallel: bool = Field(default=False, description="Deprecated flag. Execution is now sequential by default.")
    force_destroy: bool = Field(
        default=False,
        description=(
            "When True, skip in-cluster modules (mark them destroyed immediately) and destroy "
            "only cloud-infra / non-in-cluster modules. Use when the cluster is already gone "
            "and in-cluster modules cannot be reached. Non-in-cluster failures remain fail-closed."
        ),
    )


# =============================================================================
# Module Actions (D-034 — manifest-declared test/scenario actions)
# =============================================================================

class ModuleActionInputDef(BaseModel):
    """One input declared by a manifest action."""
    name: str
    type: str
    source: str | None = None
    default: Any = None
    description: str | None = None
    choices: list[Any] | None = None


class ModuleActionInfo(BaseModel):
    """One action declared in a container module's artifact manifest."""
    name: str
    title: str
    description: str | None = None
    rating: str = "green"
    inputs: list[ModuleActionInputDef] = Field(default_factory=list)
    # Shown after the action completes (e.g. "left cluster objects; run Clean").
    cleanup_note: str | None = None


class ModuleActionsListResponse(BaseModel):
    """Response for GET /api/project-modules/{id}/actions."""
    module_id: int
    actions: list[ModuleActionInfo]
    total: int


class ModuleActionSubmitResponse(BaseModel):
    """Response for POST /api/project-modules/{id}/actions/{action_name}."""
    success: bool
    message: str
    action: str
    task_id: int
    celery_task_id: str | None = None
    status: str


class DeploymentOutputResponse(BaseModel):
    """Response for GET /api/project-modules/{id}/deployments/{deployment_id}/output.

    The captured stdout/stderr of a deployment run. Without this the only place a
    failed module's step output existed was the UI's log viewer, so a headless or
    CI-driven deploy had no way to find out why it failed (issue #526).
    """
    module_id: int
    deployment_id: int
    action: str
    status: str
    exit_code: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    stdout: str
    stderr: str
    truncated: bool = False


class ModuleReportFile(BaseModel):
    """One file within a report run (D-034 PR-2.5)."""
    path: str
    kind: str
    size: int


class ModuleReportRun(BaseModel):
    """One report run — a ``<stamp>/`` directory — and its files."""
    stamp: str
    files: list[ModuleReportFile] = Field(default_factory=list)


class ModuleReportsListResponse(BaseModel):
    """Response for GET /api/project-modules/{id}/reports."""
    module_id: int
    runs: list[ModuleReportRun] = Field(default_factory=list)


class ModuleReportContentResponse(BaseModel):
    """Response for GET /api/project-modules/{id}/reports/content."""
    path: str
    kind: str
    size: int
    content: str
