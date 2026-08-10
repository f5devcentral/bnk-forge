"""
Pydantic schemas for the Stacks domain.

Covers:
  - Stack template responses (detail and list)
  - Stack preview
  - Stack instance create / response
  - Stack deployment status
  - Custom stack operations (save-as-template, duplicate, import, publish)
"""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from utils.validators import validate_cidr_fields

# =============================================================================
# Stack Template Schemas
# =============================================================================

class StackTemplateResponse(BaseModel):
    """Schema for stack template response"""
    id: int
    name: str
    slug: str
    description: str | None
    category: str | None
    cloud_provider: str | None
    icon: str | None
    color: str | None
    estimated_time: str | None
    estimated_cost: str | None
    difficulty: str | None
    modules: list["StackTemplateModuleResponse"] | None
    variable_templates: dict | None
    prerequisites: list | None
    tags: list | None
    maturity: str | None = None
    outcomes: list | None = None
    platform_defaults: dict | None = None
    bnk_version: str | None = None
    is_active: bool
    is_featured: bool
    version: str | None = "1.0.0"
    created_by: str | None = "system"
    is_public: bool | None = False
    forked_from: int | None = None
    source_kind: str | None = None
    blueprint_release_id: int | None = None
    blueprint_source_id: int | None = None
    release_state: str | None = None
    validation_state: str | None = None
    source_path: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StackTemplateListResponse(BaseModel):
    """Simplified schema for stack template list"""
    id: int
    name: str
    slug: str
    description: str | None
    category: str | None
    cloud_provider: str | None
    icon: str | None
    color: str | None
    estimated_time: str | None
    estimated_cost: str | None
    difficulty: str | None
    tags: list | None
    maturity: str | None = None
    platform_defaults: dict | None = None
    bnk_version: str | None = None
    is_active: bool
    is_featured: bool
    version: str | None = "1.0.0"
    created_by: str | None = "system"
    is_public: bool | None = False
    forked_from: int | None = None
    source_kind: str | None = None
    blueprint_release_id: int | None = None
    blueprint_source_id: int | None = None
    release_state: str | None = None
    validation_state: str | None = None
    source_path: str | None = None

    class Config:
        from_attributes = True


# =============================================================================
# Stack Preview Schema
# =============================================================================

class StackPreviewResponse(BaseModel):
    """Preview of what will be deployed"""
    modules: list["StackTemplateModuleResponse"]
    prerequisites: list | None
    estimated_cost: str | None
    estimated_time: str | None
    total_modules: int


class ImportedBlueprintProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    project_type: str | None = None
    cloud_provider: str | None = None
    environment: str | None = "production"
    region: str | None = None
    credential_template_id: int | None = None
    backend_type: str | None = "local"
    color: str | None = "#2563eb"
    icon: str | None = ""
    variables: dict[str, object] = Field(default_factory=dict)


class ImportedBlueprintProjectCreateResponse(BaseModel):
    success: bool = True
    project_id: int
    project_name: str
    blueprint_release_id: int
    module_count: int
    created_module_ids: list[int]
    message: str


class ImportedBlueprintAddToProjectRequest(BaseModel):
    """Request body for adding an imported blueprint release to an existing project."""

    variables: dict[str, object] = Field(default_factory=dict)


# =============================================================================
# Stack Instance Schemas
# =============================================================================

class StackInstanceCreate(BaseModel):
    """Schema for creating a stack instance"""
    template_id: int
    name: str = Field(..., min_length=1, max_length=255)
    variables: dict | None = Field(default=None)

    @model_validator(mode="after")
    def _validate_cidr_variables(self) -> Self:
        validate_cidr_fields(self.variables)
        return self


class StackInstanceResponse(BaseModel):
    """Schema for stack instance response"""
    id: int
    project_id: int
    template_id: int | None
    blueprint_release_id: int | None = None
    template_slug: str | None = None
    name: str
    status: str
    variables: dict | None
    deployed_modules: list[int]
    current_step: int
    total_steps: int | None
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StackStatusResponse(BaseModel):
    """Stack deployment status"""
    current_step: int
    total_steps: int
    status: str
    modules: list[dict]


class StackInstanceModuleResponse(BaseModel):
    """Stack instance module entry with additive capability metadata."""

    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    path: str
    status: str
    deployment_order: int
    error: str | None = None

    engine_type: str | None = None
    lifecycle_capabilities: dict[str, bool] | None = None


class StackInstanceDetailResponse(StackInstanceResponse):
    """Detailed stack instance schema with module capability metadata."""

    modules: list[StackInstanceModuleResponse]


# =============================================================================
# Custom Stack Operation Schemas
# =============================================================================

class SaveAsTemplateRequest(BaseModel):
    """Schema for saving a project as a stack template"""
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    category: str = "custom"
    version: str = "1.0.0"
    is_public: bool = False
    tags: list[str] | None = Field(default=None)
    icon: str | None = None
    color: str | None = None


class DuplicateTemplateRequest(BaseModel):
    """Schema for duplicating a stack template"""
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    version: str = "1.0.0"


class ImportTemplateRequest(BaseModel):
    """Schema for importing a stack template from JSON"""
    template_json: dict


class PublishTemplateRequest(BaseModel):
    """Schema for publishing/unpublishing a template"""
    is_public: bool


class StackTemplateModuleResponse(BaseModel):
    """Template module entry with additive capability metadata."""

    model_config = ConfigDict(extra="allow")

    path: str | None = None
    name: str | None = None
    required: bool | None = None
    description: str | None = None
    variables: dict | None = None

    engine_type: str | None = None
    lifecycle_capabilities: dict[str, bool] | None = None
