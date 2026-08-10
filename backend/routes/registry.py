"""
Module Registry API endpoints.

Browse and import modules from OpenTofu/Terraform Registry, public git URLs,
and Terraform Cloud private registries.
"""

import logging

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError, handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from services.registry_service import RegistryService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/registry", tags=["registry"])


# ============================================================================
# Pydantic Schemas
# ============================================================================

class RegistryModuleImport(BaseModel):
    """Schema for importing a module from a public registry."""
    namespace: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    version: str | None = None
    use_terraform_registry: bool = Field(default=False)


class RegistryGitImport(BaseModel):
    """Schema for importing a module from a public git URL."""
    url: str = Field(..., min_length=1)
    ref: str = Field(..., min_length=1, description="Tag, branch, or commit SHA")
    subpath: str | None = Field(default=None, description="Optional subdirectory inside the repo")
    version_label: str | None = Field(default=None, description="Display version; defaults to ref")
    display_name: str | None = Field(default=None, description="Override the derived module name")


class RegistryTFCImport(BaseModel):
    """Schema for importing a Terraform Cloud private module."""
    hostname: str = Field(..., min_length=1, description="e.g. app.terraform.io")
    namespace: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    provider: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    credential_template_id: int = Field(..., description="ID of CloudCredentialTemplate carrying the TFC token")


def _module_payload(module) -> dict:
    return {
        "id": module.id,
        "name": module.name,
        "category": module.category,
        "provider": module.provider,
        "version": module.version,
        "description": module.description,
        "latest_version": module.latest_version,
        "update_available": module.update_available,
        "local_path": module.local_path,
        "tarball_sha256": module.tarball_sha256,
        "upstream_source_url": module.upstream_source_url,
        "last_smoke_test_status": module.last_smoke_test_status,
    }


# ============================================================================
# API Endpoints
# ============================================================================

@router.get("/search", dependencies=[Depends(require_viewer)])
@handle_route_errors("search registry modules")
def search_registry_modules(
    q: str = Query("", description="Search query"),
    provider: str | None = Query(None, description="Filter by provider (aws, azure, gcp, etc.)"),
    verified: bool | None = Query(None, description="Show only verified modules"),
    limit: int = Query(50, ge=1, le=100, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
):
    """Search modules in OpenTofu/Terraform Registry."""
    registry = RegistryService(db)
    return registry.search_modules(
        query=q, provider=provider, verified=verified, limit=limit, offset=offset
    )


@router.get("/modules/{namespace}/{name}/{provider}", dependencies=[Depends(require_viewer)])
@handle_route_errors("get registry module details")
def get_registry_module(
    namespace: str,
    name: str,
    provider: str,
    use_terraform_registry: bool = Query(False),
    db: Session = Depends(get_db),
):
    registry = RegistryService(db)
    module_details = registry.get_module_details(
        namespace=namespace, name=name, provider=provider, use_terraform_registry=use_terraform_registry
    )
    if not module_details:
        raise NotFoundError("registry_module", f"{namespace}/{name}/{provider}")
    return module_details


@router.get("/modules/{namespace}/{name}/{provider}/versions", dependencies=[Depends(require_viewer)])
@handle_route_errors("get registry module versions")
def get_registry_module_versions(
    namespace: str,
    name: str,
    provider: str,
    use_terraform_registry: bool = Query(False),
    db: Session = Depends(get_db),
):
    registry = RegistryService(db)
    versions = registry.get_module_versions(
        namespace=namespace, name=name, provider=provider, use_terraform_registry=use_terraform_registry
    )
    return {"namespace": namespace, "name": name, "provider": provider, "versions": versions}


@router.post("/import", dependencies=[Depends(require_operator)])
@handle_route_errors("import registry module")
def import_registry_module(
    import_data: RegistryModuleImport,
    db: Session = Depends(get_db),
):
    """Import a public Terraform/OpenTofu Registry module."""
    registry = RegistryService(db)
    module = registry.import_module_from_registry(
        namespace=import_data.namespace,
        name=import_data.name,
        provider=import_data.provider,
        version=import_data.version,
        use_terraform_registry=import_data.use_terraform_registry,
    )
    if not module:
        raise NotFoundError(
            "registry_module", f"{import_data.namespace}/{import_data.name}/{import_data.provider}"
        )
    db.commit()
    return {"success": True, "message": "Module imported", "module": _module_payload(module)}


@router.post("/import-git", dependencies=[Depends(require_operator)])
@handle_route_errors("import public git module")
def import_public_git_module(import_data: RegistryGitImport, db: Session = Depends(get_db)):
    """Import a module from a public git URL pinned to a ref."""
    registry = RegistryService(db)
    module = registry.import_module_from_git(
        url=import_data.url,
        ref=import_data.ref,
        subpath=import_data.subpath,
        version_label=import_data.version_label,
        display_name=import_data.display_name,
    )
    db.commit()
    return {"success": True, "message": "Module imported from git", "module": _module_payload(module)}


@router.post("/import-tfc", dependencies=[Depends(require_operator)])
@handle_route_errors("import TFC private module")
def import_tfc_private_module(import_data: RegistryTFCImport, db: Session = Depends(get_db)):
    """Import a Terraform Cloud private module using a credential template's TFC token."""
    registry = RegistryService(db)
    try:
        module = registry.import_module_from_tfc(
            hostname=import_data.hostname,
            namespace=import_data.namespace,
            name=import_data.name,
            provider=import_data.provider,
            version=import_data.version,
            credential_template_id=import_data.credential_template_id,
        )
    except ValueError as exc:
        raise BadRequestError(str(exc), code="TFC_IMPORT_VALIDATION_ERROR")
    db.commit()
    return {"success": True, "message": "TFC module imported", "module": _module_payload(module)}


@router.get("/modules/{module_id}/check-updates", dependencies=[Depends(require_viewer)])
@handle_route_errors("check module updates")
def check_module_updates(module_id: int, db: Session = Depends(get_db)):
    try:
        registry = RegistryService(db)
        result = registry.check_for_updates(module_id)
        db.commit()
        return result
    except ValueError:
        raise NotFoundError("module", module_id)


@router.post("/modules/{module_id}/upgrade", dependencies=[Depends(require_operator)])
@handle_route_errors("upgrade registry module")
def upgrade_registry_module(
    module_id: int,
    target_version: str | None = Query(None, description="Version to upgrade to (default: latest)"),
    db: Session = Depends(get_db),
):
    try:
        registry = RegistryService(db)
        module = registry.upgrade_module(module_id, target_version)
        db.commit()
        return {
            "success": True,
            "message": f"Module upgraded to {module.source_version}",
            "module": _module_payload(module),
        }
    except ValueError as exc:
        raise BadRequestError(str(exc), code="UPGRADE_VALIDATION_ERROR")
