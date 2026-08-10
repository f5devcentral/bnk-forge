"""
Pydantic schemas for the Helm domain.

Request schemas remain in routes/helm.py (InstallChartRequest, etc.)
since they are co-located with their validators and well-organized.
This file adds RESPONSE schemas for OpenAPI documentation.
"""

from typing import Any

from pydantic import BaseModel

# =============================================================================
# Release Responses
# =============================================================================

class HelmReleaseInfo(BaseModel):
    """Single Helm release in list response."""
    name: str
    namespace: str
    revision: str | int
    status: str
    chart: str | None = None
    chart_version: str | None = None
    app_version: str | None = None
    updated: str | None = None
    description: str | None = None


class HelmReleaseListResponse(BaseModel):
    """Response for GET /api/k8s/{cluster_id}/helm/releases."""
    success: bool = True
    releases: list[HelmReleaseInfo]
    count: int | None = None


class HelmReleaseDetailResponse(BaseModel):
    """Response for GET /api/k8s/{cluster_id}/helm/releases/{name}."""
    success: bool = True
    release: dict[str, Any]


class HelmOperationResponse(BaseModel):
    """Generic response for install/upgrade/rollback/uninstall operations."""
    success: bool = True
    message: str
    result: dict[str, Any] | None = None
    task_id: str | None = None
    status: str | None = None


# =============================================================================
# Release History / Values / Manifest
# =============================================================================

class HelmRevisionInfo(BaseModel):
    """Single revision in release history."""
    revision: str | int
    status: str
    chart: str | None = None
    app_version: str | None = None
    updated: str | None = None
    description: str | None = None


class HelmReleaseHistoryResponse(BaseModel):
    """Response for GET /api/k8s/{cluster_id}/helm/releases/{name}/history."""
    success: bool = True
    history: list[HelmRevisionInfo]
    count: int | None = None


class HelmReleaseValuesResponse(BaseModel):
    """Response for GET /api/k8s/{cluster_id}/helm/releases/{name}/values."""
    release_name: str
    values: dict[str, Any]


class HelmReleaseManifestResponse(BaseModel):
    """Response for GET /api/k8s/{cluster_id}/helm/releases/{name}/manifest."""
    release_name: str
    manifest: str


# =============================================================================
# Repository Responses
# =============================================================================

class HelmRepositoryInfo(BaseModel):
    """Single Helm repository."""
    name: str
    url: str


class HelmRepositoryListResponse(BaseModel):
    """Response for GET /api/helm/repositories."""
    success: bool = True
    repositories: list[HelmRepositoryInfo]
    count: int | None = None


# =============================================================================
# Chart Responses
# =============================================================================

class HelmChartInfo(BaseModel):
    """Single Helm chart in browse/search results."""
    name: str
    version: str | None = None
    app_version: str | None = None
    description: str | None = None
    repository: str | None = None


class HelmChartSearchResponse(BaseModel):
    """Response for GET /api/helm/charts/search."""
    success: bool = True
    charts: list[HelmChartInfo]
    count: int | None = None


class HelmUploadedChartInfo(BaseModel):
    """Single uploaded chart."""
    id: int
    filename: str
    chart_name: str | None = None
    chart_version: str | None = None
    uploaded_at: str | None = None


class HelmUploadedChartListResponse(BaseModel):
    """Response for GET /api/helm/charts/uploaded."""
    charts: list[HelmUploadedChartInfo]
