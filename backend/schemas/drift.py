"""
Pydantic schemas for the Drift Detection domain.

Covers:
  - Drift settings (request / response)
  - Drift check response
  - Drift summary response
  - Trigger drift check request
  - Cluster drift status response (module drift + release-line drift)
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

# =============================================================================
# Drift Settings Schemas
# =============================================================================

class DriftSettingsRequest(BaseModel):
    """Request model for drift settings."""
    enabled: bool = False
    schedule_type: str = "cron"
    schedule_value: str = "0 2 * * *"
    check_all_modules: bool = True
    module_ids: list[int] | None = None
    notify_on_drift: bool = True
    notification_channels: list[str] | None = None
    notification_config: dict | None = None
    ignore_insignificant_changes: bool = False
    ignore_patterns: list[str] | None = None


class DriftSettingsResponse(BaseModel):
    """Response model for drift settings."""
    id: int
    project_id: int
    enabled: bool
    schedule_type: str
    schedule_value: str
    check_all_modules: bool
    module_ids: list[int] | None
    notify_on_drift: bool
    notification_channels: list[str] | None
    notification_config: dict | None
    ignore_insignificant_changes: bool
    ignore_patterns: list[str] | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# =============================================================================
# Drift Check Schemas
# =============================================================================

class DriftCheckResponse(BaseModel):
    """Response model for drift check."""
    id: int
    project_id: int
    module_id: int | None
    module_name: str | None
    schedule_enabled: bool
    schedule_cron: str | None
    last_check_at: datetime | None
    next_check_at: datetime | None
    drift_detected: bool
    drift_summary: str | None
    drift_details: dict | None
    task_id: int | None
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# =============================================================================
# Drift Summary / Trigger Schemas
# =============================================================================

class DriftSummaryResponse(BaseModel):
    """Response model for drift summary."""
    total_checks: int
    drift_detected_count: int
    no_drift_count: int
    failed_count: int
    last_check_at: datetime | None
    projects_with_drift: int
    modules_with_drift: int


class TriggerDriftCheckRequest(BaseModel):
    """Request model for triggering drift check."""
    module_ids: list[int] | None = None


# =============================================================================
# Cluster Drift Status (module drift + release-line drift)
# =============================================================================

class ReleaseDrift(BaseModel):
    """
    Deployed-vs-running release-line drift signal (ADR-494 Phase B).

    Granularity is VERSION LINE (e.g. BNK 2.3 vs BNK 2.4), not exact build
    (e.g. 2.3.0 vs 2.3.1).  Discovery resolves a FLO chart version to a whole
    release-line registry row; exact point-release comparison is deferred until
    discovery emits build-level information.

    Status meanings:
      in_sync             — deployed and running resolve to the same release line
      drifted             — deployed and running resolve to different release lines
      not_forge_deployed  — cluster has no Forge-tracked deployable release
      undiscovered        — cluster has not been scanned / FLO version undetectable
      deployed_unresolved — cluster is Forge-deployed but the deployed release's FLO version
                            could not be resolved to a known release line
    """
    status: Literal["in_sync", "drifted", "not_forge_deployed", "undiscovered", "deployed_unresolved"]
    deployed_release_id: int | None = None
    running_release_id: int | None = None


class ModuleDriftStatus(BaseModel):
    """Per-module drift status entry within a cluster drift status response."""
    module_id: int
    module_name: str | None = None
    module_path: str | None = None
    engine_type: str | None = None
    status: str
    drift_detected: bool
    drift_summary: str | None = None
    drift_details: dict[str, Any] | None = None
    last_check_at: str | None = None
    check_id: int | None = None


class ClusterDriftStatusResponse(BaseModel):
    """Response for GET /api/clusters/{cluster_id}/drift/status."""
    cluster_id: int
    project_id: int | None = None
    drift_enabled: bool
    total_modules: int
    modules_with_drift: int
    modules_ok: int
    modules_unchecked: int
    overall_status: str
    module_statuses: list[ModuleDriftStatus]
    release_drift: ReleaseDrift
