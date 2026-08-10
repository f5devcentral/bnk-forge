"""
Shared diagnostic payload schemas (PLAT-REL-002).

Reusable Pydantic models for operator-facing health and diagnostic APIs.
Any endpoint that reports health/status can include these to provide
structured, actionable explanations beyond a bare status string.

Usage::

    from schemas.diagnostics import DiagnosticItem, DiagnosticSummary

    # Single diagnostic finding
    item = DiagnosticItem(
        severity=HealthSeverity.DEGRADED,
        message="Port 6443 is blocked by a firewall.",
        suggestion="Request firewall rule for TCP 6443.",
        source="connectivity_probe",
        evidence={"tcp_port": 6443, "icmp_reachable": True},
    )

    # Aggregated summary
    summary = DiagnosticSummary.from_items(
        items=[item],
        message="1 of 3 clusters has connectivity issues.",
    )
"""

from datetime import UTC, datetime
from typing import Any, cast

from pydantic import BaseModel, Field

from models.enums import HealthSeverity


class DiagnosticItem(BaseModel):
    """A single diagnostic finding for operator-facing APIs.

    Each item represents one concrete observation about system state.
    Designed to be renderable in UI panels, log entries, and MCP tool outputs.
    """

    severity: HealthSeverity = Field(
        description="How bad is this finding? Uses canonical HealthSeverity."
    )
    message: str = Field(
        description="Human-readable explanation of the finding."
    )
    suggestion: str | None = Field(
        default=None,
        description="Actionable next step for the operator. None if no action needed.",
    )
    source: str | None = Field(
        default=None,
        description="Which subsystem/check produced this (e.g. 'connectivity_probe', 'bnk_health').",
    )
    evidence: dict[str, Any] | None = Field(
        default=None,
        description="Machine-readable context (probe results, counts, pod names, etc.).",
    )
    timestamp: str | None = Field(
        default=None,
        description="ISO 8601 when this was assessed.",
    )

    @classmethod
    def healthy(cls, message: str, source: str | None = None, **evidence: Any) -> "DiagnosticItem":
        """Convenience: create a healthy diagnostic."""
        return cls(
            severity=HealthSeverity.HEALTHY,
            message=message,
            source=source,
            evidence=evidence if evidence else None,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    @classmethod
    def degraded(
        cls, message: str, suggestion: str | None = None,
        source: str | None = None, **evidence: Any,
    ) -> "DiagnosticItem":
        """Convenience: create a degraded diagnostic."""
        return cls(
            severity=HealthSeverity.DEGRADED,
            message=message,
            suggestion=suggestion,
            source=source,
            evidence=evidence if evidence else None,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    @classmethod
    def unhealthy(
        cls, message: str, suggestion: str | None = None,
        source: str | None = None, **evidence: Any,
    ) -> "DiagnosticItem":
        """Convenience: create an unhealthy diagnostic."""
        return cls(
            severity=HealthSeverity.UNHEALTHY,
            message=message,
            suggestion=suggestion,
            source=source,
            evidence=evidence if evidence else None,
            timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )


class DiagnosticSummary(BaseModel):
    """Aggregated diagnostic output for health-like endpoints.

    Provides a top-level severity rollup plus individual findings.
    """

    overall: HealthSeverity = Field(
        description="Worst-case rollup of all diagnostic severities."
    )
    message: str = Field(
        description="One-line summary for operator/AI consumption."
    )
    diagnostics: list[DiagnosticItem] = Field(
        default_factory=list,
        description="Individual diagnostic findings.",
    )
    assessed_at: str = Field(
        description="ISO 8601 when this assessment was made.",
    )

    @classmethod
    def from_items(cls, items: list[DiagnosticItem], message: str) -> "DiagnosticSummary":
        """Create a summary by rolling up severity from a list of items."""
        severities = cast(list[HealthSeverity | str], [item.severity for item in items])
        overall = HealthSeverity.worst(severities) if severities else HealthSeverity.UNKNOWN
        return cls(
            overall=overall,
            message=message,
            diagnostics=items,
            assessed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    @classmethod
    def healthy(cls, message: str) -> "DiagnosticSummary":
        """Convenience: create a fully healthy summary with no diagnostics."""
        return cls(
            overall=HealthSeverity.HEALTHY,
            message=message,
            diagnostics=[],
            assessed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
