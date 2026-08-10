"""
Pydantic schemas for BNK-Forge v2

ProjectCreate and ProjectUpdate are canonical in schemas/projects.py.
Re-exported here for backward compatibility.
"""
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

# Re-export from canonical location (schemas/projects.py)
from schemas.projects import ProjectCreate, ProjectUpdate  # noqa: F401
from utils.validators import validate_cidr_fields


class ModuleCreate(BaseModel):
    """Schema for adding a module to a project"""
    module_library_id: int = Field(..., gt=0)
    path_in_project: str = Field(..., min_length=1, max_length=1000)
    variables: dict[str, Any] | None = Field(default=None)
    deployment_order: int = Field(default=0, ge=0, le=9999)

    @model_validator(mode="after")
    def _validate_cidr_variables(self) -> Self:
        validate_cidr_fields(self.variables)
        return self


class ModuleUpdate(BaseModel):
    """Schema for updating a project module"""
    variables: dict[str, Any] | None = None
    deployment_order: int | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _validate_cidr_variables(self) -> Self:
        validate_cidr_fields(self.variables)
        return self
