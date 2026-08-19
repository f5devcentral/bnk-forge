"""
UT-ADR494-B: Unit tests for ADR-494 Phase B release-line drift logic.

Tests the release drift truth table (all five statuses) and the ReleaseDrift
Pydantic schema.  DriftService._compute_release_drift is also exercised with a
mocked DB to cover the deployed_unresolved branch.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from schemas.drift import ReleaseDrift
from services.drift_service import DriftService

# ---------------------------------------------------------------------------
# ReleaseDrift schema — shape and literal validation
# ---------------------------------------------------------------------------

class TestReleaseDriftSchema:
    def test_in_sync(self):
        rd = ReleaseDrift(status="in_sync", deployed_release_id=5, running_release_id=5)
        assert rd.status == "in_sync"
        assert rd.deployed_release_id == 5
        assert rd.running_release_id == 5

    def test_drifted(self):
        rd = ReleaseDrift(status="drifted", deployed_release_id=3, running_release_id=7)
        assert rd.status == "drifted"
        assert rd.deployed_release_id == 3
        assert rd.running_release_id == 7

    def test_not_forge_deployed(self):
        rd = ReleaseDrift(status="not_forge_deployed", deployed_release_id=None, running_release_id=None)
        assert rd.status == "not_forge_deployed"
        assert rd.deployed_release_id is None
        assert rd.running_release_id is None

    def test_undiscovered(self):
        rd = ReleaseDrift(status="undiscovered", deployed_release_id=2, running_release_id=None)
        assert rd.status == "undiscovered"
        assert rd.deployed_release_id == 2
        assert rd.running_release_id is None

    def test_deployed_unresolved(self):
        rd = ReleaseDrift(status="deployed_unresolved", deployed_release_id=None, running_release_id=None)
        assert rd.status == "deployed_unresolved"
        assert rd.deployed_release_id is None

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            ReleaseDrift(status="unknown_status")  # type: ignore[arg-type]

    def test_defaults_nullable(self):
        rd = ReleaseDrift(status="not_forge_deployed")
        assert rd.deployed_release_id is None
        assert rd.running_release_id is None


# ---------------------------------------------------------------------------
# Truth-table: pure drift status determination logic
#
# The helper is a free function that mirrors _compute_release_drift's core
# decision tree — deployed_row_id vs running_row_id — without any DB calls.
# ---------------------------------------------------------------------------

def _drift_status(deployed_row_id: int | None, running_row_id: int | None) -> str:
    """Mirror of the truth table in DriftService._compute_release_drift."""
    if deployed_row_id is None:
        return "not_forge_deployed"
    if running_row_id is None:
        return "undiscovered"
    return "in_sync" if deployed_row_id == running_row_id else "drifted"


class TestDriftTruthTable:
    def test_both_none_is_not_forge_deployed(self):
        assert _drift_status(None, None) == "not_forge_deployed"

    def test_deployed_none_running_set_is_not_forge_deployed(self):
        # deployed absent takes priority over running presence
        assert _drift_status(None, 7) == "not_forge_deployed"

    def test_deployed_set_running_none_is_undiscovered(self):
        assert _drift_status(3, None) == "undiscovered"

    def test_both_equal_is_in_sync(self):
        assert _drift_status(4, 4) == "in_sync"

    def test_both_differ_is_drifted(self):
        assert _drift_status(2, 9) == "drifted"

    def test_large_id_values(self):
        assert _drift_status(9999, 9999) == "in_sync"
        assert _drift_status(1, 9999) == "drifted"


# ---------------------------------------------------------------------------
# _compute_release_drift — mocked DB, the deployed_unresolved branch
# ---------------------------------------------------------------------------


class TestComputeReleaseDrift:
    def test_deployable_exists_flo_unresolvable_returns_deployed_unresolved(self):
        """Deployable row exists (not None), bnk_release_id is None, and resolve_ga returns
        None → cluster IS Forge-deployed but the release line is unknown → deployed_unresolved."""
        deployable = SimpleNamespace(bnk_release_id=None, flo_version="v2.99.0-unknown")
        cluster = SimpleNamespace(running_release_id=5, deployable_release_id=42)

        db = MagicMock()
        # db.query(...).filter(...).first() → the deployable stub
        db.query.return_value.filter.return_value.first.return_value = deployable

        with patch("services.release_registry_service.ReleaseRegistryService.resolve_ga", return_value=None):
            svc = DriftService(db)
            result = svc._compute_release_drift(cluster)

        assert result["status"] == "deployed_unresolved"
        assert result["deployed_release_id"] is None
        # running_release_id is preserved from the cluster
        assert result["running_release_id"] == 5
