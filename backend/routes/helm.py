"""
Helm API routes for BNK-Forge.
Provides REST API for Helm chart and release management.
"""

import logging
import os
from typing import Any

import redis as _redis
from celery.result import AsyncResult
from fastapi import APIRouter, Body, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from core.errors import NotFoundError, handle_route_errors
from database import get_db
from models import KubernetesCluster, Project, User
from routes.auth import _check_ownership, require_cluster_owner, require_operator, require_viewer
from schemas.helm import (
    HelmChartSearchResponse,
    HelmOperationResponse,
    HelmReleaseDetailResponse,
    HelmReleaseListResponse,
    HelmRepositoryListResponse,
)
from services.helm_service import HelmService
from tasks import helm_tasks
from utils.security import validate_helm_timeout as _validate_helm_timeout

_SHORT_POLL_TIMEOUT = 10  # seconds to wait inline before falling back to task_id
_TASK_CLUSTER_TTL = 3600  # 1 hour — write ops complete well within this window

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Redis helper — task → cluster_id ownership map
# ---------------------------------------------------------------------------

def _get_redis() -> "_redis.Redis[str]":
    url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return _redis.from_url(url, decode_responses=True)  # type: ignore[return-value]


def _store_task_cluster(task_id: str, cluster_id: int) -> None:
    """Record which cluster a helm write task belongs to (for authz on poll)."""
    try:
        r = _get_redis()
        r.setex(f"helm:task:{task_id}:cluster_id", _TASK_CLUSTER_TTL, str(cluster_id))
    except Exception:
        logger.warning("helm: could not store task→cluster mapping for task %s", task_id)


def _get_task_cluster_id(task_id: str) -> int | None:
    """Return the cluster_id for a helm write task, or None if not found / Redis error."""
    try:
        r = _get_redis()
        val = r.get(f"helm:task:{task_id}:cluster_id")
        if val is None:
            return None
        return int(val)
    except Exception:
        logger.warning("helm: could not retrieve task→cluster mapping for task %s", task_id)
        return None


# Request/Response Models
class InstallChartRequest(BaseModel):
    release_name: str = Field(..., description="Name for the Helm release")
    chart: str = Field(..., description="Chart reference (e.g., 'bitnami/nginx')")
    namespace: str = Field(default="default", description="Target namespace")
    values: dict[str, Any] | None = Field(None, description="Custom values")
    version: str | None = Field(None, description="Chart version")
    create_namespace: bool = Field(True, description="Create namespace if it doesn't exist")
    wait: bool = Field(True, description="Wait for resources to be ready")
    timeout: str = Field("5m", description="Timeout (e.g., '5m', '10m')")

    @field_validator("timeout")
    @classmethod
    def _check_timeout(cls, v: str) -> str:
        _validate_helm_timeout(v)
        return v


class UpgradeReleaseRequest(BaseModel):
    chart: str | None = Field(None, description="Chart reference (optional if already installed)")
    values: dict[str, Any] | None = Field(None, description="Custom values")
    version: str | None = Field(None, description="Chart version")
    install: bool = Field(True, description="Install if release doesn't exist")
    wait: bool = Field(True, description="Wait for resources to be ready")
    timeout: str = Field("5m", description="Timeout")

    @field_validator("timeout")
    @classmethod
    def _check_timeout(cls, v: str) -> str:
        _validate_helm_timeout(v)
        return v


class RollbackReleaseRequest(BaseModel):
    revision: int | None = Field(None, description="Revision to rollback to (default: previous)")
    wait: bool = Field(True, description="Wait for resources to be ready")
    timeout: str = Field("5m", description="Timeout")

    @field_validator("timeout")
    @classmethod
    def _check_timeout(cls, v: str) -> str:
        _validate_helm_timeout(v)
        return v


class AddRepositoryRequest(BaseModel):
    name: str = Field(..., description="Repository name")
    url: str = Field(..., description="Repository URL")
    username: str | None = Field(None, description="Authentication username")
    password: str | None = Field(None, description="Authentication password")


# Release Management Endpoints
@router.get("/api/k8s/{cluster_id}/helm/releases", response_model=HelmReleaseListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list helm releases")
async def list_releases(
    cluster_id: int,
    namespace: str | None = Query(None),
    all_namespaces: bool = Query(False),
):
    cel = helm_tasks.list_releases.apply_async(args=[cluster_id, namespace, all_namespaces])
    try:
        result = cel.get(timeout=_SHORT_POLL_TIMEOUT, propagate=False)
        if cel.failed():
            return {"success": False, "releases": [], "count": 0, "task_id": cel.id, "status": "failed"}
        return result
    except Exception:
        return {"success": False, "releases": [], "count": 0, "task_id": cel.id, "status": "pending"}


@router.get("/api/k8s/{cluster_id}/helm/releases/{release_name}", response_model=HelmReleaseDetailResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("get helm release")
async def get_release(
    cluster_id: int,
    release_name: str,
    namespace: str = Query("default"),
):
    cel = helm_tasks.get_release.apply_async(args=[cluster_id, release_name, namespace])
    try:
        result = cel.get(timeout=_SHORT_POLL_TIMEOUT, propagate=False)
        if cel.failed():
            return {"success": False, "release": {}, "task_id": cel.id, "status": "failed"}
        return result
    except Exception:
        return {"success": False, "release": {}, "task_id": cel.id, "status": "pending"}


@router.post("/api/k8s/{cluster_id}/helm/install", response_model=HelmOperationResponse)
@handle_route_errors("install helm chart")
async def install_chart(
    cluster_id: int,
    request: InstallChartRequest,
    user: User = Depends(require_cluster_owner),
):
    cel = helm_tasks.install_release.apply_async(args=[
        cluster_id,
        request.release_name,
        request.chart,
        request.namespace,
        request.values,
        request.version,
        request.create_namespace,
        request.wait,
        request.timeout,
    ])
    _store_task_cluster(cel.id, cluster_id)
    return {"success": True, "result": {}, "message": f"Chart {request.chart} install queued", "task_id": cel.id, "status": "queued"}


@router.put("/api/k8s/{cluster_id}/helm/releases/{release_name}/upgrade")
@handle_route_errors("upgrade helm release")
async def upgrade_release(
    cluster_id: int,
    release_name: str,
    request: UpgradeReleaseRequest,
    namespace: str = Query("default"),
    user: User = Depends(require_cluster_owner),
):
    cel = helm_tasks.upgrade_release.apply_async(args=[
        cluster_id,
        release_name,
        request.chart,
        namespace,
        request.values,
        request.version,
        request.install,
        request.wait,
        request.timeout,
    ])
    _store_task_cluster(cel.id, cluster_id)
    return {"success": True, "result": {}, "message": f"Release {release_name} upgrade queued", "task_id": cel.id, "status": "queued"}


@router.post("/api/k8s/{cluster_id}/helm/releases/{release_name}/rollback")
@handle_route_errors("rollback helm release")
async def rollback_release(
    cluster_id: int,
    release_name: str,
    request: RollbackReleaseRequest,
    namespace: str = Query("default"),
    user: User = Depends(require_cluster_owner),
):
    cel = helm_tasks.rollback_release.apply_async(args=[
        cluster_id,
        release_name,
        request.revision,
        namespace,
        request.wait,
        request.timeout,
    ])
    _store_task_cluster(cel.id, cluster_id)
    return {"success": True, "result": {}, "message": f"Release {release_name} rollback queued", "task_id": cel.id, "status": "queued"}


@router.delete("/api/k8s/{cluster_id}/helm/releases/{release_name}")
@handle_route_errors("uninstall helm release")
async def uninstall_release(
    cluster_id: int,
    release_name: str,
    namespace: str = Query("default"),
    keep_history: bool = Query(False),
    wait: bool = Query(True),
    timeout: str = Query("5m"),
    user: User = Depends(require_cluster_owner),
):
    cel = helm_tasks.uninstall_release.apply_async(args=[
        cluster_id,
        release_name,
        namespace,
        keep_history,
        wait,
        timeout,
    ])
    _store_task_cluster(cel.id, cluster_id)
    return {"success": True, "result": {}, "message": f"Release {release_name} uninstall queued", "task_id": cel.id, "status": "queued"}


@router.get("/api/k8s/{cluster_id}/helm/releases/{release_name}/history", dependencies=[Depends(require_viewer)])
@handle_route_errors("get helm release history")
async def get_release_history(
    cluster_id: int,
    release_name: str,
    namespace: str = Query("default"),
    max_revisions: int = Query(256),
):
    cel = helm_tasks.release_history.apply_async(args=[cluster_id, release_name, namespace, max_revisions])
    try:
        result = cel.get(timeout=_SHORT_POLL_TIMEOUT, propagate=False)
        if cel.failed():
            return {"success": False, "history": [], "count": 0, "task_id": cel.id, "status": "failed"}
        return result
    except Exception:
        return {"success": False, "history": [], "count": 0, "task_id": cel.id, "status": "pending"}


@router.get("/api/k8s/{cluster_id}/helm/releases/{release_name}/values", dependencies=[Depends(require_viewer)])
@handle_route_errors("get helm release values")
async def get_release_values(
    cluster_id: int,
    release_name: str,
    namespace: str = Query("default"),
    all_values: bool = Query(False),
):
    cel = helm_tasks.release_values.apply_async(args=[cluster_id, release_name, namespace, all_values])
    try:
        result = cel.get(timeout=_SHORT_POLL_TIMEOUT, propagate=False)
        if cel.failed():
            return {"success": False, "values": {}, "task_id": cel.id, "status": "failed"}
        return result
    except Exception:
        return {"success": False, "values": {}, "task_id": cel.id, "status": "pending"}


@router.get("/api/k8s/{cluster_id}/helm/releases/{release_name}/manifest", dependencies=[Depends(require_viewer)])
@handle_route_errors("get helm release manifest")
async def get_release_manifest(
    cluster_id: int,
    release_name: str,
    namespace: str = Query("default"),
    revision: int | None = Query(None),
):
    cel = helm_tasks.release_manifest.apply_async(args=[cluster_id, release_name, namespace, revision])
    try:
        result = cel.get(timeout=_SHORT_POLL_TIMEOUT, propagate=False)
        if cel.failed():
            return {"success": False, "manifest": "", "task_id": cel.id, "status": "failed"}
        return result
    except Exception:
        return {"success": False, "manifest": "", "task_id": cel.id, "status": "pending"}


@router.post("/api/k8s/{cluster_id}/helm/releases/{release_name}/test")
@handle_route_errors("test helm release")
async def test_release(
    cluster_id: int,
    release_name: str,
    namespace: str = Query("default"),
    timeout: str = Query("5m"),
    user: User = Depends(require_cluster_owner),
):
    cel = helm_tasks.test_release.apply_async(args=[cluster_id, release_name, namespace, timeout])
    _store_task_cluster(cel.id, cluster_id)
    return {"success": True, "result": {}, "message": "Queued", "task_id": cel.id, "status": "queued"}


# Repository Management Endpoints
@router.post("/api/helm/repositories", dependencies=[Depends(require_operator)])
@handle_route_errors("add helm repository")
async def add_repository(
    request: AddRepositoryRequest,
    db: Session = Depends(get_db)
):
    """
    Add a Helm chart repository.

    Args:
        request: Repository parameters
        db: Database session

    Returns:
        Result
    """
    helm_service = HelmService(db)
    result = helm_service.add_repository(
        name=request.name,
        url=request.url,
        username=request.username,
        password=request.password
    )
    return result


@router.post("/api/helm/repositories/update", dependencies=[Depends(require_operator)])
@handle_route_errors("update helm repositories")
async def update_repositories(db: Session = Depends(get_db)):
    """
    Update all Helm chart repositories.

    Args:
        db: Database session

    Returns:
        Result
    """
    helm_service = HelmService(db)
    result = helm_service.update_repositories()
    return result


@router.get("/api/helm/repositories", response_model=HelmRepositoryListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list helm repositories")
async def list_repositories(db: Session = Depends(get_db)):
    """
    List all configured Helm chart repositories.

    Args:
        db: Database session

    Returns:
        List of repositories
    """
    helm_service = HelmService(db)
    repositories = helm_service.list_repositories()
    return {
        "success": True,
        "repositories": repositories,
        "count": len(repositories)
    }


@router.delete("/api/helm/repositories/{name}", dependencies=[Depends(require_operator)])
@handle_route_errors("remove helm repository")
async def remove_repository(name: str, db: Session = Depends(get_db)):
    """
    Remove a Helm chart repository.

    Args:
        name: Repository name to remove
        db: Database session

    Returns:
        Success message
    """
    helm_service = HelmService(db)
    result = helm_service.remove_repository(name)
    return result


@router.get("/api/helm/charts/browse", dependencies=[Depends(require_viewer)])
@handle_route_errors("browse helm charts")
async def browse_charts(
    repository: str | None = Query(None, description="Specific repository name (e.g., 'bitnami')"),
    max_results: int = Query(50, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Browse all available charts or charts from a specific repository.

    Args:
        repository: Specific repository name (optional)
        max_results: Maximum number of results
        db: Database session

    Returns:
        List of charts
    """
    helm_service = HelmService(db)
    charts = helm_service.browse_charts(
        repository=repository,
        max_results=max_results
    )
    return {
        "success": True,
        "charts": charts,
        "count": len(charts),
        "repository": repository
    }


@router.get("/api/helm/charts/search", response_model=HelmChartSearchResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("search helm charts")
async def search_charts(
    keyword: str = Query(..., description="Search keyword"),
    repository: str | None = Query(None, description="Specific repository"),
    max_results: int = Query(100, description="Maximum results"),
    db: Session = Depends(get_db)
):
    """
    Search for charts in repositories.

    Args:
        keyword: Search keyword
        repository: Specific repository (optional)
        max_results: Maximum number of results
        db: Database session

    Returns:
        List of charts
    """
    helm_service = HelmService(db)
    charts = helm_service.search_charts(
        keyword=keyword,
        repository=repository,
        max_results=max_results
    )
    return {
        "success": True,
        "charts": charts,
        "count": len(charts)
    }


@router.post("/api/helm/charts/upload", dependencies=[Depends(require_operator)])
@handle_route_errors("upload helm chart")
async def upload_chart(
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    Upload a custom Helm chart (.tgz file).

    Args:
        file: .tgz chart package file
        db: Database session

    Returns:
        Uploaded chart information
    """
    helm_service = HelmService(db)
    result = await helm_service.upload_chart(file)
    db.commit()
    return result


@router.get("/api/helm/charts/uploaded", dependencies=[Depends(require_viewer)])
@handle_route_errors("list uploaded helm charts")
async def list_uploaded_charts(
    source_type: str | None = Query(None, description="Filter by source type ('uploaded' or 'cloned')"),
    db: Session = Depends(get_db)
):
    """
    List uploaded or cloned custom Helm charts.

    Args:
        source_type: Filter by source type (optional)
        db: Database session

    Returns:
        List of charts
    """
    helm_service = HelmService(db)
    charts = helm_service.list_uploaded_charts(source_type=source_type)
    return {
        "success": True,
        "charts": charts,
        "count": len(charts)
    }


@router.delete("/api/helm/charts/uploaded/{chart_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("delete uploaded helm chart")
async def delete_uploaded_chart(chart_id: int, db: Session = Depends(get_db)):
    """
    Delete an uploaded chart.

    Args:
        chart_id: Chart ID to delete
        db: Database session

    Returns:
        Success message
    """
    helm_service = HelmService(db)
    result = helm_service.delete_uploaded_chart(chart_id)
    db.commit()
    return result


@router.get("/api/helm/charts/uploaded/{chart_id}/values", dependencies=[Depends(require_viewer)])
@handle_route_errors("get helm chart values")
async def get_chart_values(chart_id: int, db: Session = Depends(get_db)):
    """
    Get the values.yaml content from an uploaded chart.

    Args:
        chart_id: Chart ID
        db: Database session

    Returns:
        Values YAML content
    """
    helm_service = HelmService(db)
    result = helm_service.get_chart_values(chart_id)
    return result


@router.put("/api/helm/charts/uploaded/{chart_id}/values", dependencies=[Depends(require_operator)])
@handle_route_errors("update helm chart values")
async def update_chart_values(
    chart_id: int,
    values: str = Body(..., description="New values.yaml content"),
    db: Session = Depends(get_db)
):
    """
    Update the values.yaml in an uploaded chart.

    Args:
        chart_id: Chart ID
        values: New values.yaml content
        db: Database session

    Returns:
        Success message
    """
    helm_service = HelmService(db)
    result = helm_service.update_chart_values(chart_id, values)
    return result


@router.post("/api/helm/charts/clone", dependencies=[Depends(require_operator)])
@handle_route_errors("clone helm chart")
async def clone_chart(
    chart: str = Query(..., description="Chart reference (e.g., 'bitnami/nginx')"),
    version: str | None = Query(None, description="Chart version"),
    cloned_by: str | None = Query(None, description="Username of cloner"),
    db: Session = Depends(get_db)
):
    """
    Clone a chart from a repository.

    Args:
        chart: Chart reference (e.g., 'bitnami/nginx')
        version: Specific version to clone (optional)
        cloned_by: Username of cloner
        db: Database session

    Returns:
        Cloned chart information
    """
    helm_service = HelmService(db)
    result = await helm_service.clone_chart(chart, version, cloned_by)
    db.commit()
    return result


@router.get("/api/k8s/{cluster_id}/helm/releases/{release_name}/compare", dependencies=[Depends(require_viewer)])
@handle_route_errors("compare helm revisions")
async def compare_revisions(
    cluster_id: int,
    release_name: str,
    revision1: int = Query(..., description="First revision number"),
    revision2: int = Query(..., description="Second revision number"),
    namespace: str = Query("default", description="Kubernetes namespace"),
):
    cel = helm_tasks.release_compare.apply_async(args=[cluster_id, release_name, revision1, revision2, namespace])
    try:
        result = cel.get(timeout=_SHORT_POLL_TIMEOUT, propagate=False)
        if cel.failed():
            return {"success": False, "task_id": cel.id, "status": "failed"}
        return result
    except Exception:
        return {"success": False, "task_id": cel.id, "status": "pending"}


@router.get("/api/k8s/helm/tasks/{task_id}")
@handle_route_errors("get helm task status")
async def get_helm_task_status(
    task_id: str,
    user: User = Depends(require_viewer),
    db: Session = Depends(get_db),
):
    """Poll the result of a helm write Celery task by its task ID.

    Authz: the task must have been registered with a cluster_id, and the caller
    must be able to view that cluster (same project membership check as other
    cluster-scoped routes). Returns 404 for unknown task IDs to avoid leaking
    whether a task exists in another tenant's cluster.
    """
    cluster_id = _get_task_cluster_id(task_id)
    if cluster_id is None:
        # Task not found in our registry — either expired TTL, wrong ID, or
        # belongs to a read task that was never registered. Return 404 to avoid
        # information leakage about cross-tenant tasks.
        raise NotFoundError("helm task", task_id)

    # Verify the cluster exists and the caller belongs to its project.
    cluster = db.query(KubernetesCluster).filter(KubernetesCluster.id == cluster_id).first()
    if not cluster:
        raise NotFoundError("cluster", cluster_id)

    project = db.query(Project).filter(Project.id == cluster.project_id).first()
    if not project:
        raise NotFoundError("project", cluster.project_id)
    _check_ownership(user, project)

    result = AsyncResult(task_id)
    if result.ready():
        if result.failed():
            return {"task_id": task_id, "status": "failed", "error": str(result.result)}
        return {"task_id": task_id, "status": "ready", "result": result.result}
    return {"task_id": task_id, "status": "pending"}
