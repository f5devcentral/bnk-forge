"""Unit tests for cloud teardown guarantee (run_cloud_teardown).

Verifies:
- Mid-run failure still triggers destroy_all via run_cloud_teardown
- Failed teardown yields a 'failed' StepResult
- Successful teardown yields 'ok' and clears cloud_project_id from state
- No project_id in state → StepResult is 'skipped'
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from scripts.e2e.client import BnkForgeApiError
from scripts.e2e.cloud_steps import run_cloud_teardown
from scripts.e2e.config import CloudBlueprintConfig, E2EConfig, TimeoutsConfig
from scripts.e2e.steps import Context

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    project_id: int | None = 42,
    extra_state: dict | None = None,
    teardown_always: bool = True,
) -> Context:
    """Create a minimal Context with a fake cloud config."""
    cfg = E2EConfig.model_construct(
        bnk_forge_url="https://forge.test",
        bnk_forge_admin_user="admin",
        bnk_forge_admin_password="pw",
        project_name_prefix="e2e-",
        timeouts=TimeoutsConfig(),
        cloud=CloudBlueprintConfig(
            cloud_provider="aws",
            teardown_always=teardown_always,
        ),
        # Optional DPU fields — empty for cloud-only ctx
        worker_node_ips=[],
        bmc_ips=[],
        worker_ssh_user="",
        worker_ssh_key_path="",
        bmc_password="",
        dpu_os_password="",
        bf_template="lag",
        bfb_image="",
        local_deploy=False,
    )
    client = MagicMock()
    state: dict[str, Any] = {}
    if project_id is not None:
        state["cloud_project_id"] = project_id
    if extra_state:
        state.update(extra_state)
    return Context(cfg=cfg, client=client, state=state)


def _make_exec_list(exec_id: int = 7, status: str = "in_progress") -> list[dict]:
    """Return a parallel-executions list with one record."""
    return [{"id": exec_id, "status": status, "action": "destroy"}]


def _make_exec_status_seq(exec_id: int, *statuses: str) -> list[dict]:
    """Returns a side_effect sequence for get_parallel_execution_status."""
    return [{"id": exec_id, "status": s, "progress_percent": 0.0} for s in statuses]


# ---------------------------------------------------------------------------
# No project_id in state
# ---------------------------------------------------------------------------


class TestTeardownNoProject:
    def test_skipped_when_no_project_id(self):
        ctx = _make_ctx(project_id=None)
        result = run_cloud_teardown(ctx)
        assert result.status == "skipped"
        ctx.client.destroy_all.assert_not_called()


# ---------------------------------------------------------------------------
# Successful teardown
# ---------------------------------------------------------------------------


class TestTeardownSuccess:
    def test_destroys_and_deletes(self):
        ctx = _make_ctx(project_id=42)
        ctx.client.destroy_all.return_value = {"orchestrator_task_id": "handle-xyz"}
        ctx.client.get_parallel_executions.return_value = _make_exec_list(7, "in_progress")
        ctx.client.get_parallel_execution_status.side_effect = _make_exec_status_seq(
            7, "completed",
        )
        ctx.client.delete_project.return_value = {"deleted": True}

        with patch("scripts.e2e.cloud_steps.time") as mock_time:
            mock_time.time.side_effect = [0, 0, 99]
            mock_time.sleep = MagicMock()
            result = run_cloud_teardown(ctx)

        assert result.status == "ok"
        ctx.client.destroy_all.assert_called_once_with(42)
        ctx.client.delete_project.assert_called_once_with(42)
        # cloud_project_id cleared from state after success
        assert ctx.state.get("cloud_project_id") is None

    def test_warn_when_project_delete_fails(self):
        ctx = _make_ctx(project_id=42)
        ctx.client.destroy_all.return_value = {"orchestrator_task_id": "h"}
        ctx.client.get_parallel_executions.return_value = _make_exec_list(7, "in_progress")
        ctx.client.get_parallel_execution_status.side_effect = _make_exec_status_seq(
            7, "completed",
        )
        ctx.client.delete_project.side_effect = BnkForgeApiError(500, "delete failed")

        with patch("scripts.e2e.cloud_steps.time") as mock_time:
            mock_time.time.side_effect = [0, 0, 99]
            mock_time.sleep = MagicMock()
            result = run_cloud_teardown(ctx)

        assert result.status == "warn"


# ---------------------------------------------------------------------------
# Teardown failure paths
# ---------------------------------------------------------------------------


class TestTeardownFailure:
    def test_fails_when_destroy_run_fails(self):
        ctx = _make_ctx(project_id=42)
        ctx.client.destroy_all.return_value = {"orchestrator_task_id": "h"}
        ctx.client.get_parallel_executions.return_value = _make_exec_list(7, "in_progress")
        ctx.client.get_parallel_execution_status.side_effect = _make_exec_status_seq(
            7, "failed",
        )

        with patch("scripts.e2e.cloud_steps.time") as mock_time:
            mock_time.time.side_effect = [0, 0, 99]
            mock_time.sleep = MagicMock()
            result = run_cloud_teardown(ctx)

        assert result.status == "failed"
        assert "failed" in result.summary.lower() or "failed" in (result.error or "")

    def test_fails_when_destroy_all_raises(self):
        ctx = _make_ctx(project_id=42)
        ctx.client.destroy_all.side_effect = BnkForgeApiError(500, "server error")

        result = run_cloud_teardown(ctx)

        assert result.status == "failed"
        # Error message lands in summary (from r.fail()) or error (from StepRecorder)
        assert result.summary or result.error

    def test_fails_when_exec_list_is_empty(self):
        """destroy_all succeeds but parallel-executions list is empty → RuntimeError → fail."""
        ctx = _make_ctx(project_id=42)
        ctx.client.destroy_all.return_value = {"orchestrator_task_id": "h"}
        ctx.client.get_parallel_executions.return_value = []

        result = run_cloud_teardown(ctx)

        assert result.status == "failed"

    def test_fails_on_timeout(self):
        ctx = _make_ctx(project_id=42)
        ctx.client.destroy_all.return_value = {"orchestrator_task_id": "h"}
        ctx.client.get_parallel_executions.return_value = _make_exec_list(7, "in_progress")
        ctx.client.get_parallel_execution_status.return_value = {
            "id": 7, "status": "in_progress",
        }

        with patch("scripts.e2e.cloud_steps.time") as mock_time:
            # First time() call = start, second = already past deadline
            mock_time.time.side_effect = [0, 99]
            mock_time.sleep = MagicMock()
            result = run_cloud_teardown(ctx)

        assert result.status == "failed"


# ---------------------------------------------------------------------------
# Guarantee: teardown runs even after mid-run exception
# ---------------------------------------------------------------------------


class TestTeardownGuaranteeAfterException:
    """Simulate __main__.py's finally-block behaviour inline."""

    def test_teardown_called_after_mid_run_crash(self):
        ctx = _make_ctx(project_id=99)
        ctx.client.destroy_all.return_value = {"orchestrator_task_id": "t"}
        ctx.client.get_parallel_executions.return_value = _make_exec_list(7, "in_progress")
        ctx.client.get_parallel_execution_status.side_effect = _make_exec_status_seq(
            7, "completed",
        )
        ctx.client.delete_project.return_value = {}

        raised = False
        results = []
        try:
            raise RuntimeError("a step blew up")
        except RuntimeError:
            raised = True
        finally:
            # Mirrors the __main__.py finally block logic
            if ctx.state.get("cloud_project_id") is not None:
                with patch("scripts.e2e.cloud_steps.time") as mock_time:
                    mock_time.time.side_effect = [0, 0, 99]
                    mock_time.sleep = MagicMock()
                    teardown = run_cloud_teardown(ctx)
                results.append(teardown)

        assert raised
        assert len(results) == 1
        assert results[0].status == "ok"
        ctx.client.destroy_all.assert_called_once_with(99)

    def test_failed_teardown_surfaces_as_fail(self):
        ctx = _make_ctx(project_id=99)
        ctx.client.destroy_all.side_effect = BnkForgeApiError(500, "boom")

        results = []
        try:
            raise ValueError("step failure")
        except ValueError:
            pass
        finally:
            if ctx.state.get("cloud_project_id") is not None:
                teardown = run_cloud_teardown(ctx)
                results.append(teardown)

        assert results[0].status == "failed"
