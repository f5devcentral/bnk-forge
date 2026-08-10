"""
BC-C50: Component tests for OpenTofuEngine.

Tests the OpenTofu execution engine adapter: init, plan, apply, destroy,
get_outputs, health_check, and output parsing helpers.
All OpenTofuRuntime, WorkspaceManager, and subprocess calls are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.execution.engine_interface import ModuleContext, OperationResult, PlanResult
from services.execution.opentofu_engine import (
    OpenTofuEngine,
    _parse_apply_summary,
    _parse_plan_summary,
)

# ── Helpers ──────────────────────────────────────────────────────────

def _make_ctx(**overrides):
    defaults = dict(
        module_id=1, project_id=1, path="infra/aws/vpc",
        category="infra", variables={"cidr": "10.0.0.0/16"},
        credentials_env={"AWS_ACCESS_KEY_ID": "fake"},
    )
    defaults.update(overrides)
    return ModuleContext(**defaults)


def _mock_db_factory():
    db = MagicMock()
    factory = MagicMock(return_value=db)
    return factory, db


def _stub_module():
    module = MagicMock()
    module.id = 1
    module.project = MagicMock()
    module.library_module = MagicMock()
    return module


# ── Parse helpers ────────────────────────────────────────────────────

class TestParsePlanSummary:
    def test_parses_standard_plan_output(self):
        output = "Plan: 3 to add, 1 to change, 2 to destroy."
        assert _parse_plan_summary(output) == (3, 1, 2)

    def test_returns_zeros_when_no_match(self):
        assert _parse_plan_summary("Nothing here") == (0, 0, 0)

    def test_parses_zero_changes(self):
        output = "Plan: 0 to add, 0 to change, 0 to destroy."
        assert _parse_plan_summary(output) == (0, 0, 0)


class TestParseApplySummary:
    def test_parses_apply_complete(self):
        output = "Apply complete! Resources: 5 added, 2 changed, 1 destroyed."
        assert _parse_apply_summary(output) == (5, 2, 1)

    def test_returns_zeros_when_no_match(self):
        assert _parse_apply_summary("Error occurred") == (0, 0, 0)


# ── Health check ─────────────────────────────────────────────────────

class TestHealthCheck:
    @patch("subprocess.run")
    def test_health_check_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        engine = OpenTofuEngine(MagicMock())
        assert engine.health_check() is True

    @patch("subprocess.run")
    def test_health_check_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        engine = OpenTofuEngine(MagicMock())
        assert engine.health_check() is False

    @patch("subprocess.run", side_effect=FileNotFoundError("tofu not found"))
    def test_health_check_binary_missing(self, mock_run):
        engine = OpenTofuEngine(MagicMock())
        assert engine.health_check() is False


# ── Init ─────────────────────────────────────────────────────────────

class TestInit:
    def test_init_success(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_init.return_value = (0, "Initializing providers...")

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            result = engine.init(_make_ctx())

        assert result.success is True
        assert "Initializing providers" in result.stdout
        assert result.duration_seconds > 0
        db.close.assert_called_once()

    def test_init_failure_returns_error(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_init.return_value = (1, "Error: provider not found")

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            result = engine.init(_make_ctx())

        assert result.success is False
        assert result.error_message == "OpenTofu init failed"

    def test_init_exception_returns_error(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        with patch.object(engine, "_load_module", side_effect=ValueError("Module 99 not found")):
            result = engine.init(_make_ctx(module_id=99))

        assert result.success is False
        assert "Module 99 not found" in result.error_message

    def test_init_calls_on_output(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_init.return_value = (0, "line1\nline2")

        callback = MagicMock()
        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            engine.init(_make_ctx(), on_output=callback)

        # Should call for workspace + 2 log lines
        assert callback.call_count == 3


# ── Plan ─────────────────────────────────────────────────────────────

class TestPlan:
    def test_plan_no_changes(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_plan_detailed.return_value = (0, "No changes.")

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            result = engine.plan(_make_ctx())

        assert result.has_changes is False
        assert result.adds == 0

    def test_plan_with_changes(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_plan_detailed.return_value = (
            2,
            "Plan: 3 to add, 1 to change, 0 to destroy.",
        )

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            result = engine.plan(_make_ctx())

        assert result.has_changes is True
        assert result.adds == 3
        assert result.changes == 1
        assert result.destroys == 0
        assert result.plan_id is not None

    def test_plan_error(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_plan_detailed.return_value = (1, "Error: invalid config")

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            result = engine.plan(_make_ctx())

        assert result.has_changes is False
        assert "Plan failed" in result.details


# ── Apply ────────────────────────────────────────────────────────────

class TestApply:
    @patch("services.workspace_manager.WorkspaceManager")
    def test_apply_success_with_init(self, mock_wm):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_init.return_value = (0, "Init OK")
        runtime.run_plan.return_value = (0, "Plan OK")
        runtime.run_apply.return_value = (
            0,
            "Apply complete! Resources: 2 added, 0 changed, 0 destroyed.",
            {"vpc_id": "vpc-123"},
        )

        wm = mock_wm.return_value
        wm.is_initialized.return_value = False
        wm.needs_reinit.return_value = (False, None)

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            result = engine.apply(_make_ctx())

        assert result.success is True
        assert result.outputs == {"vpc_id": "vpc-123"}
        assert result.resources_created == 2

    @patch("services.workspace_manager.WorkspaceManager")
    def test_apply_init_failure_stops_execution(self, mock_wm):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_init.return_value = (1, "Init failed")

        wm = mock_wm.return_value
        wm.is_initialized.return_value = False
        wm.needs_reinit.return_value = (False, None)

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            result = engine.apply(_make_ctx())

        assert result.success is False
        assert result.error_message == "OpenTofu init failed"
        # run_plan should not have been called
        runtime.run_plan.assert_not_called()


# ── Destroy ──────────────────────────────────────────────────────────

class TestDestroy:
    @patch("services.workspace_manager.WorkspaceManager")
    def test_destroy_success(self, mock_wm):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.get_destroy_timeout.return_value = 600
        runtime.run_destroy_with_retry.return_value = (
            0,
            "Destroy complete! Resources: 0 added, 0 changed, 3 destroyed.",
        )

        wm = mock_wm.return_value
        wm.is_initialized.return_value = True
        wm.needs_reinit.return_value = (False, None)

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            result = engine.destroy(_make_ctx())

        assert result.success is True
        assert result.resources_destroyed == 3

    @patch("services.workspace_manager.WorkspaceManager")
    def test_destroy_failure_includes_error_analysis(self, mock_wm):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.get_destroy_timeout.return_value = 600
        runtime.run_destroy_with_retry.return_value = (1, "DependencyViolation")
        runtime.parse_destroy_error.return_value = "Resource has dependents"

        wm = mock_wm.return_value
        wm.is_initialized.return_value = True
        wm.needs_reinit.return_value = (False, None)

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            result = engine.destroy(_make_ctx())

        assert result.success is False
        assert result.error_suggestion == "Resource has dependents"


# ── Get Outputs ──────────────────────────────────────────────────────

class TestGetOutputs:
    @patch("services.workspace_manager.WorkspaceManager")
    def test_get_outputs_success(self, mock_wm):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.capture_outputs.return_value = {"vpc_id": "vpc-abc"}
        runtime.normalize_outputs.return_value = {"vpc_id": "vpc-abc"}

        wm = mock_wm.return_value
        wm.get_workspace_path.return_value = "/work/vpc"

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime):
            outputs = engine.get_outputs(_make_ctx())

        assert outputs == {"vpc_id": "vpc-abc"}
        runtime.normalize_outputs.assert_called_once_with({"vpc_id": "vpc-abc"}, module)

    @patch("services.workspace_manager.WorkspaceManager")
    def test_get_outputs_no_workspace_returns_empty(self, mock_wm):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        wm = mock_wm.return_value
        wm.get_workspace_path.return_value = None

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=MagicMock()):
            outputs = engine.get_outputs(_make_ctx())

        assert outputs == {}


# ── Global TF_VAR env merge at entry points ──────────────────────────

class TestEntryPointGlobalTfvarMerge:
    """
    Asserts that _get_global_tfvar_env output is merged into the env dict
    passed to the underlying runtime call for every engine entry point.

    Also verifies that cloud credentials (credentials_env) are preserved
    alongside the injected TF_VAR_jwt_token (regression guard).
    """

    def test_init_merges_global_tfvar_env(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_init.return_value = (0, "Initializing...")

        ctx = _make_ctx(credentials_env={"AWS_ACCESS_KEY_ID": "fake-aws-key"})

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime), \
             patch.object(engine, "_get_global_tfvar_env", return_value={"TF_VAR_jwt_token": "sentinel-jwt"}):
            engine.init(ctx)

        call_args = runtime.run_init.call_args
        env_passed = call_args[0][1]  # positional: (work_dir, env)
        assert env_passed["TF_VAR_jwt_token"] == "sentinel-jwt"
        assert env_passed["AWS_ACCESS_KEY_ID"] == "fake-aws-key"

    def test_plan_merges_global_tfvar_env(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_plan_detailed.return_value = (0, "No changes.")

        ctx = _make_ctx(credentials_env={"AWS_ACCESS_KEY_ID": "fake-aws-key"})

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime), \
             patch.object(engine, "_get_global_tfvar_env", return_value={"TF_VAR_jwt_token": "sentinel-jwt"}):
            engine.plan(ctx)

        call_args = runtime.run_plan_detailed.call_args
        env_passed = call_args[0][1]  # positional: (work_dir, env)
        assert env_passed["TF_VAR_jwt_token"] == "sentinel-jwt"
        assert env_passed["AWS_ACCESS_KEY_ID"] == "fake-aws-key"

    @patch("services.workspace_manager.WorkspaceManager")
    def test_apply_merges_global_tfvar_env(self, mock_wm):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.run_init.return_value = (0, "Init OK")
        runtime.run_plan.return_value = (0, "Plan OK")
        runtime.run_apply.return_value = (
            0,
            "Apply complete! Resources: 1 added, 0 changed, 0 destroyed.",
            {"vpc_id": "vpc-999"},
        )

        wm = mock_wm.return_value
        wm.is_initialized.return_value = False
        wm.needs_reinit.return_value = (False, None)

        ctx = _make_ctx(credentials_env={"AWS_ACCESS_KEY_ID": "fake-aws-key"})

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime), \
             patch.object(engine, "_get_global_tfvar_env", return_value={"TF_VAR_jwt_token": "sentinel-jwt"}):
            engine.apply(ctx)

        # apply builds env once and reuses it for init, plan, and apply calls
        call_args = runtime.run_apply.call_args
        env_passed = call_args[0][1]  # positional: (work_dir, env)
        assert env_passed["TF_VAR_jwt_token"] == "sentinel-jwt"
        assert env_passed["AWS_ACCESS_KEY_ID"] == "fake-aws-key"

    @patch("services.workspace_manager.WorkspaceManager")
    def test_destroy_merges_global_tfvar_env(self, mock_wm):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)

        module = _stub_module()
        runtime = MagicMock()
        runtime.prepare_persistent_workspace.return_value = "/work/vpc"
        runtime.get_destroy_timeout.return_value = 600
        runtime.run_destroy_with_retry.return_value = (
            0,
            "Destroy complete! Resources: 0 added, 0 changed, 2 destroyed.",
        )

        wm = mock_wm.return_value
        wm.is_initialized.return_value = True
        wm.needs_reinit.return_value = (False, None)

        ctx = _make_ctx(credentials_env={"AWS_ACCESS_KEY_ID": "fake-aws-key"})

        with patch.object(engine, "_load_module", return_value=module), \
             patch.object(engine, "_create_engine", return_value=runtime), \
             patch.object(engine, "_get_global_tfvar_env", return_value={"TF_VAR_jwt_token": "sentinel-jwt"}):
            engine.destroy(ctx)

        call_args = runtime.run_destroy_with_retry.call_args
        env_passed = call_args[0][1]  # positional: (work_dir, env, module)
        assert env_passed["TF_VAR_jwt_token"] == "sentinel-jwt"
        assert env_passed["AWS_ACCESS_KEY_ID"] == "fake-aws-key"


# ── _get_global_tfvar_env ────────────────────────────────────────────

class TestGetGlobalTfvarEnv:
    """
    Unit surface for OpenTofuEngine._get_global_tfvar_env.

    The helper must:
    - Return {"TF_VAR_jwt_token": <value>} when an active jwt_token value secret exists.
    - Return {} when no active jwt_token secret is found.
    - Return {} when the decrypted value is empty / whitespace only (R3).
    - Return {} and log (not raise) when decryption throws.
    - Strip leading/trailing whitespace from the decrypted token (R4).
    """

    def _make_project(self, project_id: int = 1):
        project = MagicMock()
        project.id = project_id
        return project

    def _make_secret(self, *, value_encrypted: str = "encrypted-tok", is_active: bool = True, secret_type: str = "value"):
        secret = MagicMock()
        secret.is_active = is_active
        secret.secret_type = secret_type
        secret.value_encrypted = value_encrypted
        return secret

    def test_returns_tf_var_when_active_secret_exists(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)
        project = self._make_project()

        secret = self._make_secret()
        db.query.return_value.filter.return_value.first.return_value = secret

        with patch("services.execution.opentofu_engine.decrypt_value", return_value="real-license-token"):
            result = engine._get_global_tfvar_env(project, db)

        assert result == {"TF_VAR_jwt_token": "real-license-token"}

    def test_returns_empty_when_no_secret(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)
        project = self._make_project()

        db.query.return_value.filter.return_value.first.return_value = None

        result = engine._get_global_tfvar_env(project, db)

        assert result == {}

    def test_returns_empty_when_decrypted_value_is_empty(self):
        # R3: empty value must not produce TF_VAR_jwt_token=""
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)
        project = self._make_project()

        secret = self._make_secret()
        db.query.return_value.filter.return_value.first.return_value = secret

        with patch("services.execution.opentofu_engine.decrypt_value", return_value=""):
            result = engine._get_global_tfvar_env(project, db)

        assert result == {}

    def test_strips_whitespace_from_token(self):
        # R4: trailing newlines from file-upload should be trimmed
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)
        project = self._make_project()

        secret = self._make_secret()
        db.query.return_value.filter.return_value.first.return_value = secret

        with patch("services.execution.opentofu_engine.decrypt_value", return_value="  license\n"):
            result = engine._get_global_tfvar_env(project, db)

        assert result == {"TF_VAR_jwt_token": "license"}

    def test_returns_empty_and_does_not_raise_on_decrypt_error(self):
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)
        project = self._make_project()

        secret = self._make_secret()
        db.query.return_value.filter.return_value.first.return_value = secret

        with patch("services.execution.opentofu_engine.decrypt_value", side_effect=Exception("key error")):
            result = engine._get_global_tfvar_env(project, db)

        assert result == {}

    def test_returns_empty_when_secret_type_is_file(self):
        # File secrets cannot be used as env-var values
        factory, db = _mock_db_factory()
        engine = OpenTofuEngine(factory)
        project = self._make_project()

        secret = self._make_secret(secret_type="file")
        db.query.return_value.filter.return_value.first.return_value = secret

        result = engine._get_global_tfvar_env(project, db)

        assert result == {}
