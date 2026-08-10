"""Backup and restore API schemas."""
from pydantic import BaseModel, Field, field_validator


class BackupCreateRequest(BaseModel):
    """Request to create a backup archive."""

    passphrase: str = Field(..., min_length=12, description="Passphrase to protect the backup archive (min 12 chars)")

    @field_validator("passphrase")
    @classmethod
    def passphrase_strength(cls, v: str) -> str:
        if len(v) < 12:
            raise ValueError("Passphrase must be at least 12 characters for security")
        return v


class BackupStatusResponse(BaseModel):
    """Status of backup/restore operations."""

    in_progress: bool
    operation: str | None = None  # "backup" | "restore" | None
    started_at: str | None = None
    message: str | None = None


class RestoreResponse(BaseModel):
    """Response after restore completes."""

    status: str  # "success" | "error"
    table_counts: dict[str, int]  # e.g. {"projects": 2, "users": 3, "kubernetes_clusters": 1, ...}
    migrations_applied: list[str]
    warnings: list[str]
    restart_triggered: bool  # always True on success


class MaintenanceStatusResponse(BaseModel):
    """Maintenance mode status."""

    maintenance_mode: bool
    message: str | None = None
    started_at: str | None = None
