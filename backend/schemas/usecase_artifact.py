"""Pydantic schemas for the D-034 use-case artifact tracer (Phase 0)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class UseCaseCaptureRequest(BaseModel):
    """Capture F5SPKVlan CRs from a cluster into a named, versioned artifact."""

    name: str
    version: str
    matching_bnk_version: str | None = None


class UseCaseArtifactVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    artifact_id: int
    version: str
    matching_bnk_version: str | None
    cr_templates: list[dict[str, Any]]
    param_schema: list[dict[str, Any]]
    source: str
    source_cluster_id: int | None
    content_hash: str
    created_by: str | None
    created_at: datetime


class UseCaseCaptureResponse(BaseModel):
    version: UseCaseArtifactVersionResponse
    already_captured: bool


class UseCaseApplyRequest(BaseModel):
    """Concrete param values to inject when rendering the artifact version."""

    param_values: dict[str, Any]


class UseCaseApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    artifact_version_id: int
    cluster_id: int
    param_values: dict[str, Any]
    applied_by: str | None
    applied_at: datetime


class UseCaseApplyResponse(BaseModel):
    message: str
    results: dict[str, list[dict[str, Any]]]
    application: UseCaseApplicationResponse


class UseCaseDriftRequest(BaseModel):
    """Param values to render the desired-state before diffing against the cluster."""

    param_values: dict[str, Any]


class UseCaseDriftResponse(BaseModel):
    drift_detected: bool
    resource_changes: dict[str, int]
    changed_resources: list[dict[str, Any]]
    summary: str
    check_duration_ms: int
