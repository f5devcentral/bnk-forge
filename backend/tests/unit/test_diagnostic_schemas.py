"""
Tests for the shared diagnostic payload schemas (PLAT-REL-002).

Verifies DiagnosticItem and DiagnosticSummary construction,
severity rollup, JSON serialization, and convenience factories.
"""

import json

import pytest

from models.enums import HealthSeverity
from schemas.diagnostics import DiagnosticItem, DiagnosticSummary

# ---------------------------------------------------------------------------
# DiagnosticItem
# ---------------------------------------------------------------------------


class TestDiagnosticItem:
    """Tests for individual diagnostic findings."""

    def test_basic_construction(self):
        item = DiagnosticItem(
            severity=HealthSeverity.DEGRADED,
            message="Port 6443 is blocked.",
            suggestion="Check firewall rules.",
            source="connectivity_probe",
        )
        assert item.severity == "degraded"
        assert item.message == "Port 6443 is blocked."
        assert item.suggestion == "Check firewall rules."
        assert item.source == "connectivity_probe"
        assert item.evidence is None
        assert item.timestamp is None

    def test_with_evidence(self):
        item = DiagnosticItem(
            severity=HealthSeverity.UNHEALTHY,
            message="TMM pods are not running.",
            evidence={"healthy_count": 0, "total_count": 3},
            source="bnk_health",
        )
        assert item.evidence == {"healthy_count": 0, "total_count": 3}

    def test_healthy_factory(self):
        item = DiagnosticItem.healthy(
            "All pods running.",
            source="bnk_health",
            healthy_count=3,
            total_count=3,
        )
        assert item.severity == HealthSeverity.HEALTHY
        assert item.message == "All pods running."
        assert item.source == "bnk_health"
        assert item.evidence == {"healthy_count": 3, "total_count": 3}
        assert item.timestamp is not None
        assert item.suggestion is None

    def test_degraded_factory(self):
        item = DiagnosticItem.degraded(
            "Celery has 0 workers.",
            suggestion="Check Celery container logs.",
            source="system_health",
            workers=0,
        )
        assert item.severity == HealthSeverity.DEGRADED
        assert item.suggestion == "Check Celery container logs."
        assert item.evidence == {"workers": 0}

    def test_unhealthy_factory(self):
        item = DiagnosticItem.unhealthy(
            "Host unreachable.",
            suggestion="Check network connectivity.",
            source="connectivity_probe",
        )
        assert item.severity == HealthSeverity.UNHEALTHY

    def test_json_serialization(self):
        item = DiagnosticItem(
            severity=HealthSeverity.DEGRADED,
            message="Test message.",
        )
        data = json.loads(item.model_dump_json())
        assert data["severity"] == "degraded"
        assert data["message"] == "Test message."
        assert data["suggestion"] is None

    def test_model_dump(self):
        item = DiagnosticItem(
            severity=HealthSeverity.HEALTHY,
            message="OK",
            source="test",
        )
        d = item.model_dump()
        assert d["severity"] == HealthSeverity.HEALTHY
        assert d["source"] == "test"


# ---------------------------------------------------------------------------
# DiagnosticSummary
# ---------------------------------------------------------------------------


class TestDiagnosticSummary:
    """Tests for aggregated diagnostic summaries."""

    def test_from_items_rollup_worst(self):
        items = [
            DiagnosticItem.healthy("All good.", source="a"),
            DiagnosticItem.degraded("Partially broken.", source="b"),
            DiagnosticItem.healthy("Fine.", source="c"),
        ]
        summary = DiagnosticSummary.from_items(items, "2 of 3 healthy.")
        assert summary.overall == HealthSeverity.DEGRADED
        assert summary.message == "2 of 3 healthy."
        assert len(summary.diagnostics) == 3
        assert summary.assessed_at is not None

    def test_from_items_all_healthy(self):
        items = [
            DiagnosticItem.healthy("OK.", source="a"),
            DiagnosticItem.healthy("OK.", source="b"),
        ]
        summary = DiagnosticSummary.from_items(items, "All healthy.")
        assert summary.overall == HealthSeverity.HEALTHY

    def test_from_items_empty(self):
        summary = DiagnosticSummary.from_items([], "No data.")
        assert summary.overall == HealthSeverity.UNKNOWN
        assert summary.diagnostics == []

    def test_from_items_unhealthy_wins(self):
        items = [
            DiagnosticItem.healthy("OK.", source="a"),
            DiagnosticItem.unhealthy("Dead.", source="b"),
            DiagnosticItem.degraded("Slow.", source="c"),
        ]
        summary = DiagnosticSummary.from_items(items, "Problems detected.")
        assert summary.overall == HealthSeverity.UNHEALTHY

    def test_healthy_factory(self):
        summary = DiagnosticSummary.healthy("Everything is fine.")
        assert summary.overall == HealthSeverity.HEALTHY
        assert summary.message == "Everything is fine."
        assert summary.diagnostics == []
        assert summary.assessed_at is not None

    def test_json_serialization(self):
        items = [
            DiagnosticItem.degraded("Issue found.", source="test"),
        ]
        summary = DiagnosticSummary.from_items(items, "1 issue.")
        data = json.loads(summary.model_dump_json())
        assert data["overall"] == "degraded"
        assert data["message"] == "1 issue."
        assert len(data["diagnostics"]) == 1
        assert data["diagnostics"][0]["severity"] == "degraded"
        assert data["diagnostics"][0]["source"] == "test"


# ---------------------------------------------------------------------------
# HealthSeverity enum behavior (co-located for coverage)
# ---------------------------------------------------------------------------


class TestHealthSeverityEnum:
    """Test the canonical HealthSeverity enum from PLAT-REL-001."""

    def test_from_counts_all_healthy(self):
        assert HealthSeverity.from_counts(5, 5) == HealthSeverity.HEALTHY

    def test_from_counts_none_healthy(self):
        assert HealthSeverity.from_counts(0, 5) == HealthSeverity.UNHEALTHY

    def test_from_counts_partial(self):
        assert HealthSeverity.from_counts(3, 5) == HealthSeverity.DEGRADED

    def test_from_counts_zero_total(self):
        assert HealthSeverity.from_counts(0, 0) == HealthSeverity.UNKNOWN

    def test_worst_empty(self):
        assert HealthSeverity.worst([]) == HealthSeverity.UNKNOWN

    def test_worst_mixed(self):
        assert HealthSeverity.worst(["healthy", "degraded"]) == HealthSeverity.DEGRADED

    def test_worst_all_healthy(self):
        assert HealthSeverity.worst(["healthy", "healthy"]) == HealthSeverity.HEALTHY

    def test_worst_unhealthy_wins(self):
        assert HealthSeverity.worst(["healthy", "unhealthy", "degraded"]) == HealthSeverity.UNHEALTHY

    def test_ordering(self):
        order = HealthSeverity.ordering()
        assert order[HealthSeverity.UNHEALTHY] < order[HealthSeverity.DEGRADED]
        assert order[HealthSeverity.DEGRADED] < order[HealthSeverity.UNKNOWN]
        assert order[HealthSeverity.UNKNOWN] < order[HealthSeverity.HEALTHY]

    def test_str_serialization(self):
        """StrEnum members serialize as plain strings."""
        assert str(HealthSeverity.HEALTHY) == "healthy"
        assert json.dumps({"s": HealthSeverity.DEGRADED}) == '{"s": "degraded"}'
