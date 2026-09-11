"""
Pydantic schemas for the System / API domain.

Covers:
  - Application settings (batch update)
  - AWS authentication method configuration
  - System health, queue metrics, performance
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# =============================================================================
# Settings
# =============================================================================

class SettingsBatchUpdate(BaseModel):
    """Request body for PUT /api/settings — batch update settings."""
    # Flat dict of key → value pairs
    # We accept Dict[str, Any] because setting values can be strings, bools, ints
    settings: dict[str, str] = Field(
        ..., description="Mapping of setting key → value"
    )


class SettingsBatchResponse(BaseModel):
    """Response for batch settings update."""
    success: bool = True
    message: str
    updated: list[str] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


class SettingEntry(BaseModel):
    """Single setting entry in GET /api/settings response."""

    key: str
    value: str | None = None
    value_type: str | None = None
    description: str | None = None
    is_encrypted: bool = False


class SettingsResponse(BaseModel):
    """Response for GET /api/settings."""

    settings: dict[str, list[SettingEntry]] = Field(default_factory=dict)


class VersionResponse(BaseModel):
    """Response for GET /api/system/version."""

    current_version: str
    latest_version: str
    update_available: bool
    commits_behind: int
    error: str | None = None


class UpgradeReadinessResponse(BaseModel):
    """GUI upgrade readiness + deployment-mode hints."""

    host_repo_path_set: bool
    docker_socket_available: bool
    upgrade_ready: bool
    upgrade_in_progress: bool
    deployment_mode: str = "server"
    recommended_command: str | None = None
    recommended_label: str | None = None
    gui_upgrade_supported: bool = True


# =============================================================================
# AWS Auth Method
# =============================================================================

class AWSAuthMethodEnum(StrEnum):
    profile = "profile"
    access_keys = "access_keys"


class AWSCredentials(BaseModel):
    """Nested credentials object within AWSAuthMethod request."""
    profile: str | None = Field(default=None, description="AWS CLI profile name (for profile auth)")
    access_key_id: str | None = Field(default=None, description="AWS access key ID (for access_keys auth)")
    secret_access_key: str | None = Field(
        default=None, description="AWS secret access key (for access_keys auth)"
    )
    session_token: str | None = Field(
        default=None, description="AWS session token for temporary credentials"
    )


class AWSAuthMethodRequest(BaseModel):
    """Request body for POST /api/aws/auth-method."""
    auth_method: AWSAuthMethodEnum = Field(
        ..., description="Authentication method: 'profile' or 'access_keys'"
    )
    credentials: AWSCredentials = Field(
        default_factory=AWSCredentials,
        description="Credentials payload — fields depend on auth_method",
    )
    region: str | None = Field(default=None, description="AWS region to set as default")


class KubeconfigRefreshResult(BaseModel):
    """Sub-object in AWSAuthMethod response."""
    refreshed: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)


class AWSAuthMethodResponse(BaseModel):
    """Response for POST /api/aws/auth-method."""
    success: bool = True
    auth_method: str
    updated_keys: list[str]
    message: str
    kubeconfig_refresh: KubeconfigRefreshResult


# =============================================================================
# System Health
# =============================================================================

class ServiceHealth(BaseModel):
    """Health status of an individual service."""
    status: str
    response_time_ms: float | None = None
    workers: int | None = None
    active_tasks: int | None = None
    error: str | None = None


class SystemHealthResponse(BaseModel):
    """Response for GET /api/system/health."""
    services: dict[str, ServiceHealth]
    timestamp: str
    maintenance_mode: bool = False


# =============================================================================
# Config Import (from config_export.py)
# =============================================================================

# =============================================================================
# System Upgrade State (UP-003)
# =============================================================================

class UpgradeStateResponse(BaseModel):
    """Response for GET /api/system/upgrade/status — persisted upgrade state."""
    status: str = Field(description="in_progress | completed | failed | interrupted | none")
    old_version: str | None = None
    new_version: str | None = None
    started_at: str | None = Field(default=None, description="ISO 8601 timestamp")
    completed_at: str | None = Field(default=None, description="ISO 8601 timestamp, set on completion/failure")
    current_phase: str | None = Field(default=None, description="Current/last phase: start, pull, build, restart, migrate, verify, complete, failed")
    phase_label: str | None = Field(default=None, description="Human-readable phase label")
    pre_upgrade_commit: str | None = Field(default=None, description="Git commit SHA before upgrade (for rollback)")
    log: list[str] = Field(default_factory=list, description="Last N lines of upgrade output")


# =============================================================================
# Config Import (from config_export.py)
# =============================================================================

class ConfigImportRequest(BaseModel):
    """
    Request body for POST /api/clusters/{cluster_id}/bnk/import.

    Accepts a BNK configuration dict to import into a cluster.
    """
    config: dict[str, Any] = Field(
        ..., description="BNK configuration to import (from export YAML/JSON)"
    )


# =============================================================================
# BNK Resource Consumption Dashboard
# =============================================================================

class BnkPlaneConsumption(BaseModel):
    """CPU/memory/pod count for a single BNK plane (control-plane or data-plane)."""

    count: int = Field(..., description="Number of BNK pods in this plane")
    cpu_millicores: int = Field(..., description="Aggregated CPU usage in millicores")
    memory_bytes: int = Field(..., description="Aggregated memory usage in bytes")


class BnkTopPod(BaseModel):
    """A single BNK pod ranked by resource consumption."""

    name: str
    namespace: str
    role: str
    cpu_millicores: int
    memory_bytes: int


class BnkClusterDpfSummary(BaseModel):
    """Lightweight DPF/DPU summary for a single cluster."""

    detected: bool
    dpu_count: int


class BnkNodeCapacity(BaseModel):
    """Node allocatable CPU/memory capacity for a cluster."""

    cpu_millicores: int = Field(default=0, description="Aggregated node allocatable CPU in millicores")
    memory_bytes: int = Field(default=0, description="Aggregated node allocatable memory in bytes")


class BnkClusterConsumption(BaseModel):
    """Per-cluster BNK resource consumption breakdown."""

    cluster_id: int
    cluster_name: str
    reachable: bool
    bnk_installed: bool
    bnk_version: str | None = None
    status: str
    node_count: int | None = None
    control_plane: BnkPlaneConsumption
    data_plane: BnkPlaneConsumption
    total: BnkPlaneConsumption
    node_capacity: BnkNodeCapacity = Field(default_factory=BnkNodeCapacity)
    metrics_available: bool
    metrics_error: str | None = None
    dpf: BnkClusterDpfSummary
    top_pods: list[BnkTopPod] = Field(default_factory=list)


class BnkFleetSummary(BaseModel):
    """Fleet-wide BNK consumption rollup."""

    total_clusters: int
    reachable_clusters: int
    bnk_installed_clusters: int
    total_bnk_pods: int
    control_plane_pods: int
    data_plane_pods: int
    total_cpu_millicores: int
    total_memory_bytes: int
    node_capacity_cpu_millicores: int = 0
    node_capacity_memory_bytes: int = 0
    dpf_detected_clusters: int
    dpu_count: int


class BnkConsumptionResponse(BaseModel):
    """Response for GET /api/system/bnk-consumption."""

    timestamp: str
    fleet_summary: BnkFleetSummary
    clusters: list[BnkClusterConsumption]
