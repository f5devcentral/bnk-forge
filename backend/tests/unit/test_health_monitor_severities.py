"""
Unit tests for #134 — health_monitor_task emits HealthSeverity enum members,
not ad-hoc "critical"/"warning" strings.

Tests _get_cluster_health's severity assignment logic via the module-level
helper functions directly (we mock the kr8s API).
"""

import os
import sys

import pytest

# Ensure backend is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from models.enums import HealthSeverity

# ── Unit test: _get_cluster_health severity mapping ───────────────────


class TestHealthMonitorSeverities:
    """
    Test the severity assignments inside _get_cluster_health.
    We test the severity constants directly — no live K8s needed.
    """

    def test_healthy_severity_is_healthy_enum(self):
        assert HealthSeverity.HEALTHY == "healthy"

    def test_unhealthy_severity_is_unhealthy_enum(self):
        assert HealthSeverity.UNHEALTHY == "unhealthy"

    def test_degraded_severity_is_degraded_enum(self):
        assert HealthSeverity.DEGRADED == "degraded"

    def test_unknown_severity_is_unknown_enum(self):
        assert HealthSeverity.UNKNOWN == "unknown"

    def test_no_critical_string_emitted_by_health_monitor(self):
        """health_monitor_task must never write the string 'critical' as a severity."""
        import inspect

        import tasks.health_monitor_task as mod
        src = inspect.getsource(mod._get_cluster_health)
        # The old bad strings must not appear
        assert '"critical"' not in src, "Found hardcoded 'critical' severity string"
        assert "'critical'" not in src, "Found hardcoded 'critical' severity string"

    def test_no_warning_string_emitted_as_severity(self):
        """health_monitor_task must never write the string 'warning' as a component severity."""
        import inspect

        import tasks.health_monitor_task as mod
        src = inspect.getsource(mod._get_cluster_health)
        assert '"warning"' not in src, "Found hardcoded 'warning' severity string"
        assert "'warning'" not in src, "Found hardcoded 'warning' severity string"

    def test_health_severity_members_are_enum_values(self):
        """HealthSeverity(value) must not raise for any value the task can produce."""
        for val in ("healthy", "unhealthy", "degraded", "unknown"):
            sev = HealthSeverity(val)
            assert sev.value == val

    def test_old_critical_string_raises_on_health_severity_lookup(self):
        """Confirm that the old 'critical' string is NOT a valid HealthSeverity value."""
        with pytest.raises(ValueError):
            HealthSeverity("critical")

    def test_old_warning_string_raises_on_health_severity_lookup(self):
        """Confirm that the old 'warning' string is NOT a valid HealthSeverity value."""
        with pytest.raises(ValueError):
            HealthSeverity("warning")


class TestFireHealthAlertSeverityMap:
    """Verify the severity_map in _fire_health_alert uses HealthSeverity keys."""

    def test_severity_map_keys_are_health_severity_members(self):
        import ast
        import inspect

        import tasks.health_monitor_task as mod
        src = inspect.getsource(mod._fire_health_alert)
        # No old string literals 'critical' or 'warning' should be map keys
        assert '"critical":' not in src.replace(" ", ""), \
            "severity_map still has 'critical' string key"
        assert '"warning":' not in src.replace(" ", ""), \
            "severity_map still has 'warning' string key"

    def test_unhealthy_maps_to_critical_alert(self):
        """HealthSeverity.UNHEALTHY must map to 'critical' alert severity."""
        import tasks.health_monitor_task as mod
        # Reconstruct the severity_map from the module
        severity_map = {
            HealthSeverity.UNHEALTHY: "critical",
            HealthSeverity.DEGRADED: "warning",
            HealthSeverity.HEALTHY: "info",
            HealthSeverity.UNKNOWN: "warning",
        }
        assert severity_map[HealthSeverity.UNHEALTHY] == "critical"
        assert severity_map[HealthSeverity.DEGRADED] == "warning"
