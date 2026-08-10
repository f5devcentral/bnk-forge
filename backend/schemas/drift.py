"""
Pydantic schemas for the Drift Detection domain.

Covers:
  - Drift settings (request / response)
  - Drift check response
  - Drift summary response
  - Trigger drift check request
"""

from datetime import datetime

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
