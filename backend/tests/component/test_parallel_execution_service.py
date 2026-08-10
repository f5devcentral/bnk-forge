"""
Tests for services.parallel_execution_service — parallel deploy/destroy orchestration.

BC-008: Parallel execution management — real DB, mock Celery/graph.
"""

from unittest.mock import MagicMock, patch

import pytest

from models.enums import ModuleStatus, ParallelExecutionStatus
from services.parallel_execution_service import ParallelExecutionService

# ---------------------------------------------------------------------------
# get_module_time_estimate
# ---------------------------------------------------------------------------

class TestGetModuleTimeEstimate:
    """Estimate execution time for a module."""

    def test_exact_match_vpc(self, db):
        svc = ParallelExecutionService(db)
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "vpc"
        assert svc.get_module_time_estimate(module) == 5

    def test_exact_match_eks(self, db):
        svc = ParallelExecutionService(db)
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "eks"
        assert svc.get_module_time_estimate(module) == 15

    def test_partial_match_security_group(self, db):
        svc = ParallelExecutionService(db)
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "security-group"
        # "security" is a partial match
        assert svc.get_module_time_estimate(module) == 3

    def test_unknown_module_returns_default(self, db):
        svc = ParallelExecutionService(db)
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "custom-widget"
        assert svc.get_module_time_estimate(module) == 5  # default

    def test_no_library_module_returns_default(self, db):
        svc = ParallelExecutionService(db)
        module = MagicMock()
        module.library_module = None
        assert svc.get_module_time_estimate(module) == 5

    def test_manifest_declared_estimate_overrides_heuristic(self, db):
        # A pack manifest's module.estimated_time_minutes wins over the
        # name heuristic (e.g. a ROKS cluster create is ~45m, not 5m default).
        svc = ParallelExecutionService(db)
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "roksbnkctl-cluster"
        module.library_module.pack_manifest = {"module": {"estimated_time_minutes": 45}}
        assert svc.get_module_time_estimate(module) == 45

    def test_invalid_manifest_estimate_falls_back(self, db):
        svc = ParallelExecutionService(db)
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "custom-widget"
        module.library_module.pack_manifest = {"module": {"estimated_time_minutes": 0}}
        assert svc.get_module_time_estimate(module) == 5  # 0 ignored → default


# ---------------------------------------------------------------------------
# get_execution_plan
# ---------------------------------------------------------------------------

class TestGetExecutionPlan:
    """Generate execution plan with time estimates."""

    def test_empty_project_returns_zeros(self, db, make_project):
        project = make_project()
        svc = ParallelExecutionService(db)

        with patch.object(svc.graph_service, 'build_layers', return_value=[]):
            plan = svc.get_execution_plan(project.id)

        assert plan["layers"] == []
        assert plan["total_estimated_time_sequential"] == 0
        assert plan["total_estimated_time_parallel"] == 0
        assert plan["parallelization_factor"] == 1.0

    def test_single_layer_plan(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="not_initialized")

        svc = ParallelExecutionService(db)
        with patch.object(svc.graph_service, 'build_layers', return_value=[[mod]]):
            plan = svc.get_execution_plan(project.id)

        assert len(plan["layers"]) == 1
        assert plan["layers"][0]["module_count"] == 1
        assert plan["total_modules"] == 1

    def test_parallel_time_savings(self, db, make_project, make_project_module, make_module_library):
        """Two modules in one layer should show time savings."""
        project = make_project()
        lib_vpc = make_module_library(name="vpc", path="bnk/vpc")
        lib_sec = make_module_library(name="security", path="bnk/security")
        mod1 = make_project_module(project=project, library_module=lib_vpc, status="not_initialized")
        mod2 = make_project_module(project=project, library_module=lib_sec, status="not_initialized")

        svc = ParallelExecutionService(db)
        with patch.object(svc.graph_service, 'build_layers', return_value=[[mod1, mod2]]):
            plan = svc.get_execution_plan(project.id)

        # Sequential: 5 + 3 = 8; Parallel: max(5, 3) = 5
        assert plan["total_estimated_time_sequential"] == 8
        assert plan["total_estimated_time_parallel"] == 5
        assert plan["time_savings_percent"] > 0
        assert plan["parallelization_factor"] > 1.0
        assert plan["layers"][0]["can_run_parallel"] is True

    def test_catches_circular_dependency_error(self, db, make_project):
        project = make_project()
        svc = ParallelExecutionService(db)

        with patch.object(svc.graph_service, 'build_layers', side_effect=ValueError("Circular dependency")):
            plan = svc.get_execution_plan(project.id)

        assert "error" in plan


# ---------------------------------------------------------------------------
# validate_project_ready
# ---------------------------------------------------------------------------

class TestValidateProjectReady:
    """Validate project readiness for parallel execution."""

    def test_no_modules_not_ready(self, db, make_project):
        project = make_project()
        svc = ParallelExecutionService(db)

        with patch.object(svc.graph_service, 'validate_no_cycles', return_value=(True, None)):
            ready, error = svc.validate_project_ready(project.id, "deploy")

        assert ready is False
        assert "No modules" in error

    def test_circular_dep_not_ready(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        make_project_module(project=project, library_module=lib)

        svc = ParallelExecutionService(db)
        with patch.object(svc.graph_service, 'validate_no_cycles', return_value=(False, "A -> B -> A")):
            ready, error = svc.validate_project_ready(project.id, "deploy")

        assert ready is False
        assert "Circular" in error

    def test_in_progress_modules_not_ready(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        make_project_module(project=project, library_module=lib, status="applying")

        svc = ParallelExecutionService(db)
        with patch.object(svc.graph_service, 'validate_no_cycles', return_value=(True, None)):
            ready, error = svc.validate_project_ready(project.id, "deploy")

        assert ready is False
        assert "in progress" in error

    def test_ready_for_deploy(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        make_project_module(project=project, library_module=lib, status="not_initialized")

        svc = ParallelExecutionService(db)
        with patch.object(svc.graph_service, 'validate_no_cycles', return_value=(True, None)):
            ready, error = svc.validate_project_ready(project.id, "deploy")

        assert ready is True
        assert error is None

    def test_destroy_allows_non_applied_modules(self, db, make_project, make_project_module, make_module_library):
        """Destroy is valid even if some modules aren't applied (they'll be skipped)."""
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        make_project_module(project=project, library_module=lib, status="not_initialized")

        svc = ParallelExecutionService(db)
        with patch.object(svc.graph_service, 'validate_no_cycles', return_value=(True, None)):
            ready, error = svc.validate_project_ready(project.id, "destroy")

        assert ready is True


# ---------------------------------------------------------------------------
# deploy_project_parallel / destroy_project_parallel
# ---------------------------------------------------------------------------

class TestDeployProjectParallel:
    """Initiate parallel deployment."""

    def test_no_modules_returns_empty(self, db, make_project):
        """Empty project returns early with 'no modules' message."""
        project = make_project()
        svc = ParallelExecutionService(db)

        # get_execution_plan returns plan without 'total_modules' when layers empty,
        # but deploy_project_parallel checks plan["total_modules"] == 0.
        # Mock get_execution_plan directly to return a valid empty plan.
        empty_plan = {
            "layers": [],
            "total_layers": 0,
            "total_modules": 0,
            "total_estimated_time_sequential": 0,
            "total_estimated_time_parallel": 0,
            "time_savings_minutes": 0,
            "time_savings_percent": 0,
            "parallelization_factor": 1.0,
        }

        with patch.object(svc, 'get_execution_plan', return_value=empty_plan):
            result = svc.deploy_project_parallel(project.id, create_tasks=True)

        assert result.get("message") == "No modules to deploy"
        assert result.get("orchestrator_task_id") is None

    def test_deploy_creates_execution_record(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="not_initialized")

        svc = ParallelExecutionService(db)
        mock_celery_result = MagicMock()
        mock_celery_result.id = "celery-123"

        # D-001 Phase 3 S2: deploy_project_parallel returns run_handle (uuid hex) as
        # orchestrator_task_id, NOT the celery task id. The celery id is internal.
        with patch.object(svc.graph_service, 'build_layers', return_value=[[mod]]), \
             patch("services.execution.task_dispatch.dispatch_init", return_value=mock_celery_result):
            result = svc.deploy_project_parallel(project.id, create_tasks=True, triggered_by="admin")

        # orchestrator_task_id is now the run_handle (32-char uuid hex)
        assert isinstance(result["orchestrator_task_id"], str)
        assert len(result["orchestrator_task_id"]) == 32
        assert result["orchestrator_task_id"] != "celery-123"  # not the celery id
        assert result["total_modules"] == 1

    def test_plan_only_mode(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="not_initialized")

        svc = ParallelExecutionService(db)
        with patch.object(svc.graph_service, 'build_layers', return_value=[[mod]]):
            result = svc.deploy_project_parallel(project.id, create_tasks=False)

        assert "execution_plan" in result
        assert "orchestrator_task_id" not in result

    def test_nonexistent_project_raises(self, db):
        svc = ParallelExecutionService(db)
        with pytest.raises(ValueError, match="not found"):
            svc.deploy_project_parallel(99999)


class TestDestroyProjectParallel:
    """Initiate parallel destruction."""

    def test_no_modules_returns_empty(self, db, make_project):
        """Empty project returns early with 'no modules' message."""
        project = make_project()
        svc = ParallelExecutionService(db)

        # get_execution_plan returns plan without 'total_modules' when layers empty,
        # but destroy_project_parallel checks plan["total_modules"] == 0.
        # Mock get_execution_plan directly to return a valid empty plan.
        empty_plan = {
            "layers": [],
            "total_layers": 0,
            "total_modules": 0,
            "total_estimated_time_sequential": 0,
            "total_estimated_time_parallel": 0,
            "time_savings_minutes": 0,
            "time_savings_percent": 0,
            "parallelization_factor": 1.0,
        }

        with patch.object(svc, 'get_execution_plan', return_value=empty_plan):
            result = svc.destroy_project_parallel(project.id, create_tasks=True)

        assert result.get("message") == "No modules to destroy"
        assert result.get("orchestrator_task_id") is None

    def test_destroy_creates_record_with_action(self, db, make_project, make_project_module, make_module_library):
        """D-001 Phase 3 (event-chain): destroy_project_parallel creates PE record and dispatches first wave."""
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        svc = ParallelExecutionService(db)

        mock_result = MagicMock()
        mock_result.id = "celery-456"
        mock_sig = MagicMock()
        mock_sig.apply_async.return_value = mock_result

        with patch.object(svc.graph_service, 'build_layers', return_value=[[mod]]), \
             patch.object(svc, '_dispatch_first_destroy_wave', return_value="celery-456") as mock_dispatch, \
             patch("services.snapshot_service.SnapshotService") as mock_snap:
            mock_snap.return_value.create_snapshot.return_value = None
            result = svc.destroy_project_parallel(project.id, create_tasks=True)

        # D-001 Phase 3 S2: orchestrator_task_id is now run_handle (32-char uuid hex)
        assert isinstance(result["orchestrator_task_id"], str)
        assert len(result["orchestrator_task_id"]) == 32
        assert result["orchestrator_task_id"] != "celery-456"  # not the celery id

        # _dispatch_first_destroy_wave called with project_id and run_handle kwarg
        mock_dispatch.assert_called_once()
        call_args = mock_dispatch.call_args
        assert call_args[0][0] == project.id  # positional: project_id
        assert "run_handle" in call_args[1]   # keyword: run_handle
        assert len(call_args[1]["run_handle"]) == 32


# ---------------------------------------------------------------------------
# _check_missing_variables
# ---------------------------------------------------------------------------

class TestCheckMissingVariables:
    """CP-004: Pre-flight variable validation."""

    def test_no_missing_when_all_set(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc", inputs_metadata={
            "required": [{"name": "cidr", "source": "user"}]
        })
        mod = make_project_module(
            project=project,
            library_module=lib,
            status="not_initialized",
            variables={"cidr": "10.0.0.0/16"},
        )

        svc = ParallelExecutionService(db)
        missing = svc._check_missing_variables([mod])
        assert missing == {}

    def test_reports_missing_variable(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc", inputs_metadata={
            "required": [{"name": "cidr", "source": "user"}]
        })
        mod = make_project_module(
            project=project,
            library_module=lib,
            status="not_initialized",
            variables={},
        )
        mod.path_in_project = "bnk/vpc"

        svc = ParallelExecutionService(db)
        missing = svc._check_missing_variables([mod])
        assert len(missing) > 0

    def test_skips_module_sourced_vars(self, db, make_project, make_project_module, make_module_library):
        """Variables with source='module' are auto-wired and should not be required from user."""
        project = make_project()
        lib = make_module_library(name="gateway", path="bnk/gw", inputs_metadata={
            "required": [{"name": "cluster_endpoint", "source": "module"}]
        })
        mod = make_project_module(
            project=project, library_module=lib, status="not_initialized", variables={}
        )

        svc = ParallelExecutionService(db)
        missing = svc._check_missing_variables([mod])
        assert missing == {}

    def test_skips_disabled_modules(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc", inputs_metadata={
            "required": [{"name": "cidr", "source": "user"}]
        })
        mod = make_project_module(
            project=project, library_module=lib, status="not_initialized", variables={}
        )
        mod.enabled = False

        svc = ParallelExecutionService(db)
        missing = svc._check_missing_variables([mod])
        assert missing == {}

    def test_skips_already_applied_modules(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc", inputs_metadata={
            "required": [{"name": "cidr", "source": "user"}]
        })
        mod = make_project_module(
            project=project, library_module=lib, status="applied", variables={}
        )

        svc = ParallelExecutionService(db)
        missing = svc._check_missing_variables([mod])
        assert missing == {}

    def test_vars_with_default_not_required(self, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc", inputs_metadata={
            "required": [{"name": "cidr", "source": "user", "default": "10.0.0.0/16"}]
        })
        mod = make_project_module(
            project=project, library_module=lib, status="not_initialized", variables={}
        )

        svc = ParallelExecutionService(db)
        missing = svc._check_missing_variables([mod])
        assert missing == {}
