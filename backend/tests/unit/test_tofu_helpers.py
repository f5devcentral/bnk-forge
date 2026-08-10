from unittest.mock import MagicMock, patch

from tasks._tofu_helpers import _trigger_next_project_module, _update_stack_status_if_needed


def test_update_stack_status_triggers_next_module_when_applied_and_deploying():
    module = MagicMock()
    module.id = 90
    module.stack_instance_id = 18
    module.status = "applied"

    stack = MagicMock()
    stack.id = 18
    stack.status = "deploying"
    stack.current_step = 1
    stack.total_steps = 7

    query_mock = MagicMock()
    query_mock.filter.return_value.first.return_value = stack
    db = MagicMock()
    db.query.return_value = query_mock

    deployment_service = MagicMock()

    with patch("services.stack_deployment_service.StackDeploymentService", return_value=deployment_service), patch(
        "tasks._tofu_helpers._trigger_next_stack_module"
    ) as trigger_next:
        _update_stack_status_if_needed(module, db)

    deployment_service.update_stack_progress.assert_called_once_with(stack)
    trigger_next.assert_called_once_with(stack, module, db)


def test_update_stack_status_does_not_trigger_when_stack_not_deploying():
    """Stack in DEPLOYED (terminal) state with applied module should NOT chain."""
    module = MagicMock()
    module.id = 90
    module.stack_instance_id = 18
    module.status = "applied"

    stack = MagicMock()
    stack.id = 18
    stack.status = "deployed"

    query_mock = MagicMock()
    query_mock.filter.return_value.first.return_value = stack
    db = MagicMock()
    db.query.return_value = query_mock

    deployment_service = MagicMock()

    with patch("services.stack_deployment_service.StackDeploymentService", return_value=deployment_service), patch(
        "tasks._tofu_helpers._trigger_next_stack_module"
    ) as trigger_next:
        _update_stack_status_if_needed(module, db)

    deployment_service.update_stack_progress.assert_called_once_with(stack)
    trigger_next.assert_not_called()


def test_update_stack_status_triggers_next_module_when_failed_stack_module_applied():
    """Regression test for #320: retried module reaching 'applied' in a FAILED stack should resume the chain."""
    module = MagicMock()
    module.id = 91
    module.stack_instance_id = 18
    module.status = "applied"

    stack = MagicMock()
    stack.id = 18
    stack.status = "failed"

    query_mock = MagicMock()
    query_mock.filter.return_value.first.return_value = stack
    db = MagicMock()
    db.query.return_value = query_mock

    deployment_service = MagicMock()

    with patch("services.stack_deployment_service.StackDeploymentService", return_value=deployment_service), patch(
        "tasks._tofu_helpers._trigger_next_stack_module"
    ) as trigger_next:
        _update_stack_status_if_needed(module, db)

    deployment_service.update_stack_progress.assert_called_once_with(stack)
    trigger_next.assert_called_once_with(stack, module, db)


def test_update_stack_status_does_not_trigger_when_failed_stack_module_not_applied():
    """FAILED stack with a module that is NOT applied should NOT chain (no false resume)."""
    module = MagicMock()
    module.id = 92
    module.stack_instance_id = 18
    module.status = "apply_failed"

    stack = MagicMock()
    stack.id = 18
    stack.status = "failed"

    query_mock = MagicMock()
    query_mock.filter.return_value.first.return_value = stack
    db = MagicMock()
    db.query.return_value = query_mock

    deployment_service = MagicMock()

    with patch("services.stack_deployment_service.StackDeploymentService", return_value=deployment_service), patch(
        "tasks._tofu_helpers._trigger_next_stack_module"
    ) as trigger_next:
        _update_stack_status_if_needed(module, db)

    deployment_service.update_stack_progress.assert_called_once_with(stack)
    trigger_next.assert_not_called()


def test_stackless_applied_module_triggers_project_wave():
    # Blueprint-imported module: no stack_instance_id. On apply success it must
    # advance the project-level wave (otherwise the deploy stalls after each layer).
    module = MagicMock()
    module.id = 90
    module.stack_instance_id = None
    module.status = "applied"
    db = MagicMock()

    with patch("tasks._tofu_helpers._trigger_next_project_module") as trigger_project:
        _update_stack_status_if_needed(module, db)

    trigger_project.assert_called_once_with(module, db)


def test_stackless_non_applied_module_does_not_trigger():
    # init/plan/destroy callbacks also reach here; only a successful apply advances.
    module = MagicMock()
    module.id = 90
    module.stack_instance_id = None
    module.status = "destroyed"
    db = MagicMock()

    with patch("tasks._tofu_helpers._trigger_next_project_module") as trigger_project:
        _update_stack_status_if_needed(module, db)

    trigger_project.assert_not_called()


def test_trigger_next_project_module_dispatches_first_wave_with_run_handle():
    completed = MagicMock()
    completed.id = 90
    completed.project_id = 41

    predecessor = MagicMock()
    predecessor.run_handle = "run-abc"
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = predecessor

    svc = MagicMock()
    with patch("services.parallel_execution_service.ParallelExecutionService", return_value=svc):
        _trigger_next_project_module(completed, db)

    svc._dispatch_first_wave.assert_called_once_with(41, run_handle="run-abc")
