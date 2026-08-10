"""
BC-C54: Component tests for InputWiringService.

Tests module input source categorization, pending input detection,
readiness checks, and input value collection.
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from models.enums import ModuleStatus
from services.input_wiring_service import InputWiringService

# ── Helpers ──────────────────────────────────────────────────────────

def _make_library_module(inputs_metadata=None):
    lib = MagicMock()
    lib.inputs_metadata = inputs_metadata
    return lib


def _make_project_module(project_id=1, library_module=None, status="applied", outputs=None):
    pm = MagicMock()
    pm.project_id = project_id
    pm.library_module = library_module
    pm.status = status if isinstance(status, str) else status.value
    pm.outputs = outputs or {}
    return pm


def _make_source_module(status=ModuleStatus.APPLIED, outputs=None):
    """Create a mock source ProjectModule for dependency resolution."""
    pm = MagicMock()
    pm.status = status
    pm.outputs = outputs or {}
    return pm


# ── Get Module Input Sources ─────────────────────────────────────────

class TestGetModuleInputSources:
    def test_empty_metadata_returns_empty(self):
        svc = InputWiringService(MagicMock())
        result = svc.get_module_input_sources(None)
        assert result == {"user": [], "module": [], "auto": []}

    def test_categorizes_user_inputs(self):
        lib = _make_library_module(inputs_metadata={
            "required": [{"name": "vpc_cidr", "source": "user"}],
            "optional": [],
        })
        svc = InputWiringService(MagicMock())
        result = svc.get_module_input_sources(lib)
        assert len(result["user"]) == 1
        assert result["user"][0]["name"] == "vpc_cidr"

    def test_categorizes_module_inputs(self):
        lib = _make_library_module(inputs_metadata={
            "required": [
                {"name": "vpc_id", "source": "module", "from_module": "infra/aws/vpc", "from_output": "vpc_id"},
            ],
            "optional": [],
        })
        svc = InputWiringService(MagicMock())
        result = svc.get_module_input_sources(lib)
        assert len(result["module"]) == 1
        assert result["module"][0]["from_module"] == "infra/aws/vpc"

    def test_defaults_unknown_source_to_user(self):
        lib = _make_library_module(inputs_metadata={
            "required": [{"name": "x", "source": "unknown_source"}],
            "optional": [],
        })
        svc = InputWiringService(MagicMock())
        result = svc.get_module_input_sources(lib)
        assert len(result["user"]) == 1

    def test_combines_required_and_optional(self):
        lib = _make_library_module(inputs_metadata={
            "required": [{"name": "a", "source": "user"}],
            "optional": [{"name": "b", "source": "auto"}],
        })
        svc = InputWiringService(MagicMock())
        result = svc.get_module_input_sources(lib)
        assert len(result["user"]) == 1
        assert len(result["auto"]) == 1


# ── Get Pending Inputs ───────────────────────────────────────────────

class TestGetPendingInputs:
    def test_no_library_module_returns_empty(self):
        pm = _make_project_module(library_module=None)
        svc = InputWiringService(MagicMock())
        result = svc.get_pending_inputs(pm)
        assert result == []

    def test_ready_when_source_applied_with_output(self):
        lib = _make_library_module(inputs_metadata={
            "required": [
                {"name": "vpc_id", "source": "module", "from_module": "infra/aws/vpc", "from_output": "vpc_id"},
            ],
            "optional": [],
        })
        pm = _make_project_module(library_module=lib)

        source_pm = _make_source_module(
            status=ModuleStatus.APPLIED,
            outputs={"vpc_id": "vpc-123"},
        )

        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.first.return_value = source_pm
        svc = InputWiringService(db)

        result = svc.get_pending_inputs(pm)
        assert len(result) == 1
        assert result[0]["status"] == "ready"
        assert result[0]["value"] == "vpc-123"

    def test_pending_when_source_not_applied(self):
        lib = _make_library_module(inputs_metadata={
            "required": [
                {"name": "vpc_id", "source": "module", "from_module": "infra/aws/vpc", "from_output": "vpc_id"},
            ],
            "optional": [],
        })
        pm = _make_project_module(library_module=lib)

        source_pm = _make_source_module(status=ModuleStatus.INITIALIZED, outputs={})

        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.first.return_value = source_pm
        svc = InputWiringService(db)

        result = svc.get_pending_inputs(pm)
        assert result[0]["status"] == "pending"

    def test_missing_when_source_not_in_project(self):
        lib = _make_library_module(inputs_metadata={
            "required": [
                {"name": "vpc_id", "source": "module", "from_module": "infra/aws/vpc", "from_output": "vpc_id"},
            ],
            "optional": [],
        })
        pm = _make_project_module(library_module=lib)

        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.first.return_value = None
        svc = InputWiringService(db)

        result = svc.get_pending_inputs(pm)
        assert result[0]["status"] == "missing"

    def test_output_missing_when_key_not_in_outputs(self):
        lib = _make_library_module(inputs_metadata={
            "required": [
                {"name": "vpc_id", "source": "module", "from_module": "infra/aws/vpc", "from_output": "vpc_id"},
            ],
            "optional": [],
        })
        pm = _make_project_module(library_module=lib)

        source_pm = _make_source_module(
            status=ModuleStatus.APPLIED,
            outputs={"other_key": "value"},
        )

        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.first.return_value = source_pm
        svc = InputWiringService(db)

        result = svc.get_pending_inputs(pm)
        assert result[0]["status"] == "output_missing"


# ── Are All Inputs Ready ─────────────────────────────────────────────

class TestAreAllInputsReady:
    def test_all_ready(self):
        svc = InputWiringService(MagicMock())
        with patch.object(svc, "get_pending_inputs", return_value=[
            {"name": "vpc_id", "status": "ready", "value": "vpc-1"},
        ]):
            pm = _make_project_module()
            ready, not_ready = svc.are_all_inputs_ready(pm)
            assert ready is True
            assert not_ready == []

    def test_not_all_ready(self):
        svc = InputWiringService(MagicMock())
        with patch.object(svc, "get_pending_inputs", return_value=[
            {"name": "vpc_id", "status": "ready", "value": "vpc-1"},
            {"name": "subnet_id", "status": "pending", "value": None},
        ]):
            pm = _make_project_module()
            ready, not_ready = svc.are_all_inputs_ready(pm)
            assert ready is False
            assert len(not_ready) == 1


# ── Get Input Values ─────────────────────────────────────────────────

class TestGetInputValues:
    def test_returns_only_ready_values(self):
        svc = InputWiringService(MagicMock())
        with patch.object(svc, "get_pending_inputs", return_value=[
            {"name": "vpc_id", "status": "ready", "value": "vpc-1"},
            {"name": "subnet_id", "status": "pending", "value": None},
        ]):
            pm = _make_project_module()
            values = svc.get_input_values_for_module(pm)
            assert values == {"vpc_id": "vpc-1"}
