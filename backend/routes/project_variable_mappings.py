"""
Variable Mapping routes for project modules.

Endpoints for:
  - Variable mapping CRUD (per-module)
  - Variable mapping templates (global)
  - Applying templates to modules

Split from monolithic project_modules.py (2,487 lines).
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError
from database import get_db
from models import ProjectModule, User, VariableMapping, VariableMappingTemplate
from routes.auth import require_module_owner, require_operator, require_viewer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/project-modules", tags=["project-variable-mappings"])


# ============================================================================
# Pydantic Request Models
# ============================================================================

class VariableMappingRequest(BaseModel):
    """Schema for creating/updating variable mappings"""
    source_variable_name: str = Field(..., min_length=1, max_length=255)
    target_variable_name: str = Field(..., min_length=1, max_length=255)
    transform_type: str = Field(default="none", description="none, uppercase, lowercase, json_encode, base64")
    is_active: bool = Field(default=True)
    description: str | None = None


class VariableMappingTemplateRequest(BaseModel):
    """Schema for creating/updating variable mapping templates"""
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    mappings: list[dict] = Field(..., description="Array of {source, target, transform} objects")
    is_public: bool = Field(default=True)


# ============================================================================
# Variable Mapping CRUD
# ============================================================================

@router.get("/{module_id}/variable-mappings", dependencies=[Depends(require_viewer)])
def get_variable_mappings(module_id: int, db: Session = Depends(get_db)):
    """
    Get all variable mappings for a project module.

    Returns list of mappings with source/target variable names and transformations.
    """
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    mappings = db.query(VariableMapping).filter(
        VariableMapping.project_module_id == module_id
    ).all()

    return {
        "module_id": module_id,
        "mappings": [
            {
                "id": m.id,
                "source_variable_name": m.source_variable_name,
                "target_variable_name": m.target_variable_name,
                "transform_type": m.transform_type,
                "is_active": m.is_active,
                "description": m.description,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "updated_at": m.updated_at.isoformat() if m.updated_at else None,
            }
            for m in mappings
        ]
    }


@router.post("/{module_id}/variable-mappings")
def create_variable_mapping(
    module_id: int,
    request: VariableMappingRequest,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """
    Create a new variable mapping for a project module.

    Maps an external/source variable name to an internal/target name with optional transformation.
    """
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    # Check for duplicate mapping
    existing = db.query(VariableMapping).filter(
        and_(
            VariableMapping.project_module_id == module_id,
            VariableMapping.source_variable_name == request.source_variable_name
        )
    ).first()

    if existing:
        raise BadRequestError(f"Mapping for '{request.source_variable_name}' already exists")

    mapping = VariableMapping(
        project_module_id=module_id,
        source_variable_name=request.source_variable_name,
        target_variable_name=request.target_variable_name,
        transform_type=request.transform_type,
        is_active=request.is_active,
        description=request.description
    )

    db.add(mapping)
    db.commit()
    db.refresh(mapping)

    logger.info(f"Created variable mapping: {request.source_variable_name} -> {request.target_variable_name}")

    return {
        "id": mapping.id,
        "source_variable_name": mapping.source_variable_name,
        "target_variable_name": mapping.target_variable_name,
        "transform_type": mapping.transform_type,
        "is_active": mapping.is_active,
        "description": mapping.description,
    }


@router.put("/{module_id}/variable-mappings/{mapping_id}")
def update_variable_mapping(
    module_id: int,
    mapping_id: int,
    request: VariableMappingRequest,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Update an existing variable mapping."""
    mapping = db.query(VariableMapping).filter(
        and_(
            VariableMapping.id == mapping_id,
            VariableMapping.project_module_id == module_id
        )
    ).first()

    if not mapping:
        raise NotFoundError("variable mapping", mapping_id)

    mapping.source_variable_name = request.source_variable_name
    mapping.target_variable_name = request.target_variable_name
    mapping.transform_type = request.transform_type
    mapping.is_active = request.is_active
    mapping.description = request.description

    db.commit()
    db.refresh(mapping)

    logger.info(f"Updated variable mapping {mapping_id}")

    return {
        "id": mapping.id,
        "source_variable_name": mapping.source_variable_name,
        "target_variable_name": mapping.target_variable_name,
        "transform_type": mapping.transform_type,
        "is_active": mapping.is_active,
        "description": mapping.description,
    }


@router.delete("/{module_id}/variable-mappings/{mapping_id}")
def delete_variable_mapping(
    module_id: int,
    mapping_id: int,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """Delete a variable mapping."""
    mapping = db.query(VariableMapping).filter(
        and_(
            VariableMapping.id == mapping_id,
            VariableMapping.project_module_id == module_id
        )
    ).first()

    if not mapping:
        raise NotFoundError("variable mapping", mapping_id)

    db.delete(mapping)
    db.commit()

    logger.info(f"Deleted variable mapping {mapping_id}")

    return {"success": True, "message": "Variable mapping deleted"}


# ============================================================================
# Variable Mapping Templates
# ============================================================================

@router.get("/variable-mapping-templates", dependencies=[Depends(require_viewer)])
def get_variable_mapping_templates(db: Session = Depends(get_db)):
    """
    Get all variable mapping templates.

    Templates are reusable mapping configurations that can be applied to multiple modules.
    """
    templates = db.query(VariableMappingTemplate).filter(
        VariableMappingTemplate.is_public
    ).all()

    return {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "mappings": t.mappings,
                "usage_count": t.usage_count,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in templates
        ]
    }


@router.post("/variable-mapping-templates", dependencies=[Depends(require_operator)])
def create_variable_mapping_template(
    request: VariableMappingTemplateRequest,
    db: Session = Depends(get_db)
):
    """
    Create a new variable mapping template.

    Templates can be saved and applied to multiple modules for common mapping patterns.
    """
    # Check for duplicate name
    existing = db.query(VariableMappingTemplate).filter(
        VariableMappingTemplate.name == request.name
    ).first()

    if existing:
        raise BadRequestError(f"Template '{request.name}' already exists")

    template = VariableMappingTemplate(
        name=request.name,
        description=request.description,
        mappings=request.mappings,
        is_public=request.is_public
    )

    db.add(template)
    db.commit()
    db.refresh(template)

    logger.info(f"Created variable mapping template: {request.name}")

    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "mappings": template.mappings,
        "is_public": template.is_public,
    }


@router.put("/variable-mapping-templates/{template_id}", dependencies=[Depends(require_operator)])
def update_variable_mapping_template(
    template_id: int,
    request: VariableMappingTemplateRequest,
    db: Session = Depends(get_db),
):
    """Update an existing variable mapping template."""
    template = db.query(VariableMappingTemplate).filter(
        VariableMappingTemplate.id == template_id
    ).first()

    if not template:
        raise NotFoundError("template", template_id)

    # Check for duplicate name (excluding self)
    existing = db.query(VariableMappingTemplate).filter(
        VariableMappingTemplate.name == request.name,
        VariableMappingTemplate.id != template_id
    ).first()
    if existing:
        raise BadRequestError(f"Template '{request.name}' already exists")

    template.name = request.name
    template.description = request.description
    template.mappings = request.mappings
    template.is_public = request.is_public

    db.commit()
    db.refresh(template)

    logger.info(f"Updated variable mapping template {template_id}: {request.name}")

    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "mappings": template.mappings,
        "is_public": template.is_public,
    }


@router.delete("/variable-mapping-templates/{template_id}", dependencies=[Depends(require_operator)])
def delete_variable_mapping_template(
    template_id: int,
    db: Session = Depends(get_db),
):
    """Delete a variable mapping template."""
    template = db.query(VariableMappingTemplate).filter(
        VariableMappingTemplate.id == template_id
    ).first()

    if not template:
        raise NotFoundError("template", template_id)

    db.delete(template)
    db.commit()

    logger.info(f"Deleted variable mapping template {template_id}: {template.name}")

    return {"success": True, "message": f"Template '{template.name}' deleted"}


@router.post("/{module_id}/variable-mappings/apply-template/{template_id}")
def apply_variable_mapping_template(
    module_id: int,
    template_id: int,
    user: User = Depends(require_module_owner),
    db: Session = Depends(get_db),
):
    """
    Apply a variable mapping template to a project module.

    Creates mappings based on the template configuration.
    """
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)

    template = db.query(VariableMappingTemplate).filter(
        VariableMappingTemplate.id == template_id
    ).first()
    if not template:
        raise NotFoundError("template", template_id)

    # Create mappings from template
    created_count = 0
    skipped_count = 0

    for mapping_data in template.mappings:
        source = mapping_data.get("source")
        target = mapping_data.get("target")
        transform = mapping_data.get("transform", "none")

        # Check if mapping already exists
        existing = db.query(VariableMapping).filter(
            and_(
                VariableMapping.project_module_id == module_id,
                VariableMapping.source_variable_name == source
            )
        ).first()

        if existing:
            skipped_count += 1
            continue

        mapping = VariableMapping(
            project_module_id=module_id,
            source_variable_name=source,
            target_variable_name=target,
            transform_type=transform,
            is_active=True
        )
        db.add(mapping)
        created_count += 1

    db.commit()

    # Update template usage count
    template.usage_count += 1
    db.commit()

    logger.info(f"Applied template '{template.name}' to module {module_id}: {created_count} created, {skipped_count} skipped")

    return {
        "success": True,
        "template_name": template.name,
        "created_count": created_count,
        "skipped_count": skipped_count,
        "message": f"Applied template: {created_count} mappings created, {skipped_count} skipped (duplicates)"
    }
