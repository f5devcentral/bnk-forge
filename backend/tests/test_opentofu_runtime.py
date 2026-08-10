"""
Tests for services.execution.opentofu_runtime — OpenTofu subprocess operations.

Covers:
  - Workspace preparation (ephemeral and persistent)
  - Subprocess calls (init, plan, apply, destroy)
  - Output capture and JSON parsing
  - Destroy retry with exponential backoff
  - Timeout handling
  - Git clone module source
  - Error handling (subprocess failure, timeout)
"""

import json
import os
import subprocess
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_module(
    project_id=1,
    module_id=10,
    project_name="test-proj",
    cloud_provider="on-prem",
    environment="dev",
    lib_path="modules/test-mod",
    lib_name="test-mod",
    git_source="https://github.com/example/modules.git",
    variables=None,
    variable_overrides=None,
    outputs=None,
    status="not_initialized",
    dependencies=None,
    variables_schema=None,
    inputs_metadata=None,
    project_variables=None,
    backend_type="local",
    region=None,
    credential_template_id=None,
):
    """Create a mock ProjectModule with relationships for OpenTofuRuntime."""
    lib_module = MagicMock()
    lib_module.id = module_id + 1000
    lib_module.name = lib_name
    lib_module.path = lib_path
    lib_module.git_source = git_source
    lib_module.source_path = None
    lib_module.source_version = None
    lib_module.module_source_id = None
    lib_module.last_synced = None
    lib_module.variables_schema = variables_schema
    lib_module.inputs_metadata = inputs_metadata

    project = MagicMock()
    project.id = project_id
    project.name = project_name
    project.cloud_provider = cloud_provider
    project.environment = environment
    project.project_variables = project_variables
    project.backend_type = backend_type
    project.region = region
    project.credential_template_id = credential_template_id
    project.state_encryption_enabled = False

    module = MagicMock()
    module.id = module_id
    module.project_id = project_id
    module.project = project
    module.library_module = lib_module
    module.variables = variables or {}
    module.variable_overrides = variable_overrides
    module.outputs = outputs
    module.status = status
    module.dependencies = dependencies or []
    module.workspace_path = None
    module.path_in_project = f"modules/{lib_name}"

    return module


class TestRunInit:
    """Test OpenTofuRuntime.run_init() subprocess execution."""

    def test_init_success(self):
        """run_init calls 'tofu init' and returns (0, output)."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Initializing provider plugins...\nTerraform has been successfully initialized!"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_init("/tmp/workspace", {"PATH": "/usr/bin"})

        assert exit_code == 0
        assert "successfully initialized" in output
        # Verify correct command was called
        args = mock_run.call_args
        cmd = args[0][0] if args[0] else args[1].get("args", [])
        assert "tofu" in cmd[0]
        assert "init" in cmd

    def test_init_failure(self):
        """run_init returns nonzero exit code on failure."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: Failed to install provider"

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_init("/tmp/workspace", {})

        assert exit_code == 1
        assert "Failed to install provider" in output

    def test_init_timeout(self):
        """run_init handles subprocess timeout gracefully."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        timeout_err = subprocess.TimeoutExpired(cmd=["tofu", "init"], timeout=900)
        timeout_err.stdout = "Partial output..."
        timeout_err.stderr = ""

        with patch("subprocess.run", side_effect=timeout_err), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_init("/tmp/workspace", {})

        assert exit_code == 1
        assert "timed out" in output.lower() or "timeout" in output.lower()

    def test_init_timeout_includes_provider_lock_contention_hint(self):
        """run_init surfaces cache lock contention when init stalls on providers."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        timeout_err = subprocess.TimeoutExpired(cmd=["tofu", "init"], timeout=900)
        timeout_err.stdout = (
            "Initializing provider plugins...\n"
            "- Installing hashicorp/aws v6.41.0...\n"
            "- Waiting for lock on cache directory .terraform/providers\n"
        )
        timeout_err.stderr = ""

        with patch("subprocess.run", side_effect=timeout_err), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_init("/tmp/workspace", {})

        assert exit_code == 1
        assert "provider cache lock contention" in output.lower()
        assert "stale provider lock file" in output.lower()

    def test_init_failure_includes_provider_lock_contention_hint(self):
        """run_init appends lock-contention hint on non-timeout init failure output."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = (
            "Initializing provider plugins...\n"
            "- waiting for LOCK on cache directory .terraform/providers\n"
        )
        mock_result.stderr = "Error: Failed to install provider"

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_init("/tmp/workspace", {})

        assert exit_code == 1
        assert "provider cache lock contention" in output.lower()
        assert "stale provider lock file" in output.lower()


class TestTofuEnvironmentWiring:
    """Ensure all tofu subprocess wrappers inherit plugin cache env."""

    def test_runtime_commands_inherit_tf_plugin_cache_dir(self):
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "error"

        with patch.dict(os.environ, {"TF_PLUGIN_CACHE_DIR": "/tmp/tofu-plugin-cache"}, clear=False), \
             patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            runtime.run_init("/tmp/workspace", {})
            runtime.run_plan("/tmp/workspace", {})
            runtime.run_plan_detailed("/tmp/workspace", {})
            runtime.run_apply("/tmp/workspace", {})  # failure path avoids output capture call
            runtime.run_refresh("/tmp/workspace", {})
            runtime.run_destroy("/tmp/workspace", {})
            runtime.capture_outputs("/tmp/workspace", {})

        for call in mock_run.call_args_list:
            assert call.kwargs["env"]["TF_PLUGIN_CACHE_DIR"] == "/tmp/tofu-plugin-cache"

    def test_reconcile_inherits_tf_plugin_cache_dir(self):
        from services.execution.opentofu_runtime import _reconcile_known_existing_k8s_resources

        state_list_result = MagicMock(
            returncode=0,
            stdout=(
                "kubernetes_namespace_v1.operator\n"
                "kubernetes_namespace_v1.utils\n"
                "kubernetes_namespace_v1.gateway\n"
                "kubernetes_secret_v1.far_secret_operator\n"
                "kubernetes_secret_v1.far_secret_utils\n"
                "kubernetes_secret_v1.far_secret_gateway\n"
            ),
            stderr="",
        )

        with patch.dict(os.environ, {"TF_PLUGIN_CACHE_DIR": "/tmp/tofu-plugin-cache"}, clear=False), \
             patch("subprocess.run", return_value=state_list_result) as mock_run:
            count = _reconcile_known_existing_k8s_resources(
                "/tmp/workspace",
                "k8s/bnk-prerequisites",
                {},
            )

        assert count == 0
        assert mock_run.call_args.kwargs["env"]["TF_PLUGIN_CACHE_DIR"] == "/tmp/tofu-plugin-cache"


class TestPreparePersistentWorkspaceSimplification:
    """Focused behavior checks for deterministic persistent refresh flow."""

    def test_catalog_workspace_refreshes_source_and_clears_module_cache(self, tmp_path):
        from services.execution.opentofu_runtime import OpenTofuRuntime

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.tf").write_text('resource "null_resource" "old" {}', encoding="utf-8")
        terraform_modules = workspace / ".terraform" / "modules"
        terraform_modules.mkdir(parents=True)
        (terraform_modules / "stale").write_text("x", encoding="utf-8")

        module = _make_mock_module(cloud_provider="on-prem")
        module.library_module.module_source_kind = "git_catalog"
        module.library_module.source = None

        with patch("services.workspace_manager.WorkspaceManager.ensure_workspace", return_value=str(workspace)), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"), \
             patch.object(OpenTofuRuntime, "_clone_module_source") as mock_clone, \
             patch("services.execution.opentofu_runtime._apply_known_module_hotfixes"), \
             patch.object(OpenTofuRuntime, "_prepare_project_secrets", return_value={}), \
             patch.object(OpenTofuRuntime, "build_variables", return_value={}), \
             patch.object(OpenTofuRuntime, "write_tfvars"), \
             patch.object(OpenTofuRuntime, "write_backend_config"), \
             patch.object(OpenTofuRuntime, "write_encryption_config"), \
             patch.object(OpenTofuRuntime, "write_provider_config"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            work_dir = runtime.prepare_persistent_workspace(module)

        assert work_dir == str(workspace)
        mock_clone.assert_called_once()
        assert not (workspace / "main.tf").exists()
        assert not terraform_modules.exists()

    def test_non_catalog_workspace_keeps_existing_source_without_refresh(self, tmp_path):
        from services.execution.opentofu_runtime import OpenTofuRuntime

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "main.tf").write_text('resource "null_resource" "keep" {}', encoding="utf-8")

        module = _make_mock_module(cloud_provider="on-prem")
        module.library_module.module_source_kind = "local"
        module.library_module.source = None

        with patch("services.workspace_manager.WorkspaceManager.ensure_workspace", return_value=str(workspace)), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"), \
             patch.object(OpenTofuRuntime, "_clone_module_source") as mock_clone, \
             patch("services.execution.opentofu_runtime._apply_known_module_hotfixes"), \
             patch.object(OpenTofuRuntime, "_prepare_project_secrets", return_value={}), \
             patch.object(OpenTofuRuntime, "build_variables", return_value={}), \
             patch.object(OpenTofuRuntime, "write_tfvars"), \
             patch.object(OpenTofuRuntime, "write_backend_config"), \
             patch.object(OpenTofuRuntime, "write_encryption_config"), \
             patch.object(OpenTofuRuntime, "write_provider_config"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            runtime.prepare_persistent_workspace(module)

        mock_clone.assert_not_called()
        assert (workspace / "main.tf").exists()

class TestRunPlan:
    """Test OpenTofuRuntime.run_plan() subprocess execution."""

    def test_plan_success(self):
        """run_plan calls 'tofu plan -out=plan.out'."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Plan: 3 to add, 0 to change, 0 to destroy."
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_plan("/tmp/workspace", {})

        assert exit_code == 0
        assert "3 to add" in output
        args = mock_run.call_args
        cmd = args[0][0] if args[0] else args[1].get("args", [])
        assert "plan" in cmd
        assert "plan.out" in str(cmd)

    def test_plan_detailed_exit_codes(self):
        """run_plan_detailed returns exit 2 when drift is detected."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 2  # drift detected
        mock_result.stdout = "Plan: 1 to change."
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_plan_detailed("/tmp/workspace", {})

        assert exit_code == 2


class TestRunApply:
    """Test OpenTofuRuntime.run_apply() subprocess execution."""

    def test_apply_success_with_outputs(self):
        """run_apply calls 'tofu apply plan.out' and captures outputs."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        # Mock the apply subprocess
        apply_result = MagicMock()
        apply_result.returncode = 0
        apply_result.stdout = "Apply complete! Resources: 3 added, 0 changed, 0 destroyed."
        apply_result.stderr = ""

        # Mock the output subprocess
        output_result = MagicMock()
        output_result.returncode = 0
        output_result.stdout = json.dumps({
            "vpc_id": {"value": "vpc-123", "type": "string"},
            "subnet_ids": {"value": ["subnet-1", "subnet-2"], "type": ["list", "string"]},
        })
        output_result.stderr = ""

        call_count = [0]
        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0] if args else kwargs.get("args", [])
            if "output" in cmd:
                return output_result
            return apply_result

        with patch("subprocess.run", side_effect=_side_effect), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output, outputs = runtime.run_apply("/tmp/workspace", {})

        assert exit_code == 0
        assert "Apply complete" in output
        assert outputs["vpc_id"] == "vpc-123"
        assert outputs["subnet_ids"] == ["subnet-1", "subnet-2"]

    def test_apply_failure_no_outputs(self):
        """run_apply on failure returns empty outputs dict."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: creating VPC: operation error"

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output, outputs = runtime.run_apply("/tmp/workspace", {})

        assert exit_code == 1
        assert outputs == {}


class TestRunDestroy:
    """Test OpenTofuRuntime.run_destroy() and destroy_with_retry()."""

    def test_destroy_success(self):
        """run_destroy calls 'tofu destroy -auto-approve'."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Destroy complete! Resources: 5 destroyed."
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run, \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_destroy("/tmp/workspace", {})

        assert exit_code == 0
        assert "destroyed" in output.lower()

    def test_destroy_with_retry_success_first_try(self):
        """run_destroy_with_retry returns immediately on success."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Destroy complete!"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_destroy_with_retry(
                "/tmp/workspace", {}, max_retries=3, initial_delay=0.01
            )

        assert exit_code == 0

    def test_destroy_with_retry_retries_on_dependency_violation(self):
        """run_destroy_with_retry retries on AWS DependencyViolation."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stdout = ""
        fail_result.stderr = "Error: DependencyViolation: resource has dependencies"

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stdout = "Destroy complete!"
        success_result.stderr = ""

        call_count = [0]
        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                return fail_result
            return success_result

        with patch("subprocess.run", side_effect=_side_effect), \
             patch("time.sleep"), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_destroy_with_retry(
                "/tmp/workspace", {}, max_retries=5, initial_delay=0.01
            )

        assert exit_code == 0
        assert call_count[0] == 3  # 2 failures + 1 success

    def test_destroy_with_retry_gives_up_after_max(self):
        """run_destroy_with_retry stops after max_retries."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stdout = ""
        fail_result.stderr = "Error: DependencyViolation: still has dependencies"

        with patch("subprocess.run", return_value=fail_result), \
             patch("time.sleep"), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_destroy_with_retry(
                "/tmp/workspace", {}, max_retries=2, initial_delay=0.01
            )

        assert exit_code == 1

    def test_destroy_with_retry_no_retry_on_non_retryable(self):
        """run_destroy_with_retry does NOT retry on non-retryable errors."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stdout = ""
        fail_result.stderr = "Error: Invalid provider configuration"

        call_count = [0]
        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            return fail_result

        with patch("subprocess.run", side_effect=_side_effect), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, output = runtime.run_destroy_with_retry(
                "/tmp/workspace", {}, max_retries=5, initial_delay=0.01
            )

        assert exit_code == 1
        assert call_count[0] == 1  # Only one attempt, no retries

    def test_destroy_with_retry_uses_default_retry_policy_when_not_overridden(self):
        """run_destroy_with_retry uses class defaults when args omitted."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stdout = ""
        fail_result.stderr = "Error: DependencyViolation: still has dependencies"

        call_count = [0]

        def _side_effect(*args, **kwargs):
            call_count[0] += 1
            return fail_result

        with patch("subprocess.run", side_effect=_side_effect), \
             patch("time.sleep"), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            exit_code, _output = runtime.run_destroy_with_retry("/tmp/workspace", {})

        assert exit_code == 1
        # attempts = initial run + max_retries
        assert call_count[0] == OpenTofuRuntime.DESTROY_RETRY_MAX + 1


class TestCaptureOutputs:
    """Test output capture and JSON parsing."""

    def test_capture_outputs_flattens_json(self):
        """capture_outputs extracts value from {name: {value: X, type: T}} format."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        output_json = json.dumps({
            "vpc_id": {"value": "vpc-abc", "type": "string"},
            "count": {"value": 42, "type": "number"},
        })
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = output_json
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            outputs = runtime.capture_outputs("/tmp/workspace", {})

        assert outputs["vpc_id"] == "vpc-abc"
        assert outputs["count"] == 42

    def test_capture_outputs_handles_failure(self):
        """capture_outputs returns empty dict on failure."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error"

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            outputs = runtime.capture_outputs("/tmp/workspace", {})

        assert outputs == {}

    def test_capture_outputs_handles_invalid_json(self):
        """capture_outputs returns empty dict on invalid JSON."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result), \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            outputs = runtime.capture_outputs("/tmp/workspace", {})

        assert outputs == {}

    def test_normalize_outputs_delegates_with_module_context(self):
        """normalize_outputs uses project/module context for durable key normalization."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        module = _make_mock_module(project_id=7, module_id=11)

        with patch("services.execution.opentofu_runtime.normalize_infrastructure_access_outputs") as mock_norm, \
             patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            mock_norm.return_value = MagicMock(outputs={"ok": True})
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            normalized = runtime.normalize_outputs({"a": 1}, module)

        assert normalized == {"ok": True}
        mock_norm.assert_called_once_with({"a": 1}, project_id=7, module_id=11)


class TestDestroyTimeout:
    """Test module-type-specific destroy timeouts."""

    def test_eks_module_gets_longer_timeout(self):
        """EKS modules get a 60-minute destroy timeout."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        module = _make_mock_module(lib_name="aws-eks-cluster")

        with patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            timeout = runtime.get_destroy_timeout(module)

        assert timeout >= 3600  # 60 min

    def test_vpc_module_gets_longer_timeout(self):
        """VPC modules get a 45-minute destroy timeout."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        module = _make_mock_module(lib_name="aws-vpc-network")

        with patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            timeout = runtime.get_destroy_timeout(module)

        assert timeout >= 2700  # 45 min

    def test_generic_module_default_timeout(self):
        """Generic modules get the default 30-minute destroy timeout."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        module = _make_mock_module(lib_name="simple-module")

        with patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            timeout = runtime.get_destroy_timeout(module)

        assert timeout == 1800  # 30 min


class TestParseDestroyError:
    """Test the destroy error parser that adds actionable guidance."""

    def test_eni_cleanup_suggestion(self):
        """parse_destroy_error suggests ENI cleanup for DependencyViolation + network interface."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        module = _make_mock_module()

        with patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            result = runtime.parse_destroy_error(
                "Error: DependencyViolation: network interface eni-123 is currently in use",
                module,
            )

        # Should contain actionable guidance about ENI cleanup
        assert "ENI" in result or "Network Interface" in result

    def test_security_group_guidance(self):
        """parse_destroy_error provides guidance for security group dependencies."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        module = _make_mock_module()

        with patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            result = runtime.parse_destroy_error(
                "Error: DependencyViolation: security group sg-123 is still referenced",
                module,
            )

        assert "Security Group" in result

    def test_generic_error_returns_string(self):
        """parse_destroy_error returns a string even for unrecognized errors."""
        from services.execution.opentofu_runtime import OpenTofuRuntime

        module = _make_mock_module()

        with patch.object(OpenTofuRuntime, "_validate_system_defaults"):
            runtime = OpenTofuRuntime.__new__(OpenTofuRuntime)
            runtime.db = MagicMock()

            result = runtime.parse_destroy_error("Error: something went wrong", module)

        # May be empty for unrecognized errors, but should be a string
        assert isinstance(result, str)


class TestKnownModuleHotfixes:
    """Tests for bounded runtime hotfix rewrites on known module paths."""

    KIND_CREATE_LINE = (
        '      "KUBECONFIG=\'${local.remote_kubeconfig_path}\' '
        'kind create cluster --name \'${var.cluster_name}\' '
        '--image \'${local.kind_node_image}\' '
        '--config \'${local.remote_workspace_dir}/kind-config.yaml\' '
        '--kubeconfig \'${local.remote_kubeconfig_path}\'",\n'
    )

    def test_kind_hotfix_injects_docker_forward_remediation_preflight(self, tmp_path):
        from services.execution.opentofu_runtime import _apply_known_module_hotfixes

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        main_tf = workspace / "main.tf"
        main_tf.write_text(
            'resource "null_resource" "kind" {\n'
            '  provisioner "remote-exec" {\n'
            '    inline = [\n'
            + self.KIND_CREATE_LINE +
            '    ]\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )

        _apply_known_module_hotfixes(str(workspace), "infra/ubuntu/kind")

        updated = main_tf.read_text(encoding="utf-8")
        assert "attempting docker/iptables remediation" in updated
        assert "update-alternatives --set iptables /usr/sbin/iptables-legacy" in updated
        assert "systemctl restart docker" in updated
        assert "DOCKER-FORWARD chain missing after remediation" in updated
        assert "echo \"DOCKER-FORWARD" not in updated

    def test_kind_hotfix_upgrades_old_docker_forward_preflight_to_remediation(self, tmp_path):
        from services.execution.opentofu_runtime import _apply_known_module_hotfixes

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        main_tf = workspace / "main.tf"
        main_tf.write_text(
            'resource "null_resource" "kind" {\n'
            '  provisioner "remote-exec" {\n'
            '    inline = [\n'
            '      "if ! $${SUDO} iptables -S DOCKER-FORWARD >/dev/null 2>&1; then echo \"DOCKER-FORWARD chain missing. Docker host networking is not ready for kind.\"; exit 1; fi",\n'
            + self.KIND_CREATE_LINE +
            '    ]\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )

        _apply_known_module_hotfixes(str(workspace), "infra/ubuntu/kind")

        updated = main_tf.read_text(encoding="utf-8")
        assert "DOCKER-FORWARD chain missing. Docker host networking is not ready for kind." not in updated
        assert "attempting docker/iptables remediation" in updated

    def test_kind_hotfix_replaces_broken_unescaped_quote_preflight_line(self, tmp_path):
        from services.execution.opentofu_runtime import _apply_known_module_hotfixes

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        main_tf = workspace / "main.tf"
        main_tf.write_text(
            'resource "terraform_data" "kind_cluster" {\n'
            '  provisioner "remote-exec" {\n'
            '    inline = [\n'
            '      "if ! $${SUDO} iptables -S DOCKER-FORWARD >/dev/null 2>&1; then echo "DOCKER-FORWARD chain missing. Docker host networking is not ready for kind."; exit 1; fi",\n'
            + self.KIND_CREATE_LINE +
            '    ]\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )

        _apply_known_module_hotfixes(str(workspace), "infra/ubuntu/kind")

        updated = main_tf.read_text(encoding="utf-8")
        assert "attempting docker/iptables remediation" in updated
        assert "echo \"DOCKER-FORWARD" not in updated

    def test_kind_hotfix_uses_tmp_kind_config_transfer_path(self, tmp_path):
        from services.execution.opentofu_runtime import _apply_known_module_hotfixes

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        main_tf = workspace / "main.tf"
        main_tf.write_text(
            'resource "terraform_data" "kind_cluster" {\n'
            '  provisioner "file" {\n'
            '    destination = "${local.remote_workspace_dir}/kind-config.yaml"\n'
            '  }\n'
            '  provisioner "remote-exec" {\n'
            '    inline = [\n'
            '      "KUBECONFIG=\'${local.remote_kubeconfig_path}\' kind create cluster --name \'${var.cluster_name}\' --image \'${local.kind_node_image}\' --config \'${local.remote_workspace_dir}/kind-config.yaml\' --kubeconfig \'${local.remote_kubeconfig_path}\'",\n'
            '    ]\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )

        _apply_known_module_hotfixes(str(workspace), "infra/ubuntu/kind")

        updated = main_tf.read_text(encoding="utf-8")
        assert 'destination = "/tmp/bnk-forge-kind-config-${var.cluster_name}.yaml"' in updated
        assert "--config '/tmp/bnk-forge-kind-config-${var.cluster_name}.yaml'" in updated
        assert "--config '${local.remote_workspace_dir}/kind-config.yaml'" not in updated

    def test_bnk_prerequisites_hotfix_avoids_tar_head_pipefail_141(self, tmp_path):
        from services.execution.opentofu_runtime import _apply_known_module_hotfixes

        workspace = tmp_path / "workspace"
        scripts_dir = workspace / "scripts"
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "download-manifest.sh"
        script.write_text(
            """#!/bin/bash
set -euo pipefail
MANIFEST_TAR="manifest.tgz"
MANIFEST_DIR=$(tar -tf "$MANIFEST_TAR" | head -n1 | cut -d'/' -f1)
""",
            encoding="utf-8",
        )

        _apply_known_module_hotfixes(str(workspace), "k8s/bnk-prerequisites")

        updated = script.read_text(encoding="utf-8")
        assert 'tar -tf "$MANIFEST_TAR" | head -n1 | cut -d\'/\' -f1' not in updated
        assert 'IFS= read -r FIRST_TAR_ENTRY < <(tar -tf "$MANIFEST_TAR")' in updated
        assert 'MANIFEST_DIR="${FIRST_TAR_ENTRY%%/*}"' in updated

    def test_bnk_prerequisites_hotfix_skips_chart_yaml_manifest_pick(self, tmp_path):
        from services.execution.opentofu_runtime import _apply_known_module_hotfixes

        workspace = tmp_path / "workspace"
        scripts_dir = workspace / "scripts"
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "download-manifest.sh"
        script.write_text(
            """#!/bin/bash
set -euo pipefail
MANIFEST_DIR="./work/f5-bigip-k8s-manifest-2.2.0"
# Dynamically find the manifest YAML file
MANIFEST_FILE=$(find "$MANIFEST_DIR" -name "*.yaml" -type f | grep -E "(manifest|bigip)" | head -n1)
if [ -z "$MANIFEST_FILE" ]; then
    # Fallback: try any YAML file in the directory
    MANIFEST_FILE=$(find "$MANIFEST_DIR" -name "*.yaml" -type f | head -n1)
fi
""",
            encoding="utf-8",
        )

        _apply_known_module_hotfixes(str(workspace), "k8s/bnk-prerequisites")

        updated = script.read_text(encoding="utf-8")
        assert 'find "$MANIFEST_DIR" -name "*.yaml" -type f | grep -E "(manifest|bigip)" | head -n1' not in updated
        assert 'for candidate in "$MANIFEST_DIR"/*k8s-manifest*.yaml' in updated
        assert 'Chart.yaml)' in updated

    def test_bnk_prerequisites_hotfix_resolves_service_account_key_path(self, tmp_path):
        """Fix 3: SERVICE_ACCOUNT_KEY_FILE is resolved to absolute before cd."""
        from services.execution.opentofu_runtime import _apply_known_module_hotfixes

        workspace = tmp_path / "workspace"
        scripts_dir = workspace / "scripts"
        scripts_dir.mkdir(parents=True)
        script = scripts_dir / "download-manifest.sh"
        # Include the eval line that Fix 3 targets
        script.write_text(
            '#!/bin/bash\nset -euo pipefail\n'
            'eval "$(jq -r \'@sh "MANIFEST_VERSION=\\(.manifest_version) '
            'WORK_DIR=\\(.work_dir) CHART_NAME=\\(.chart_name) '
            'SERVICE_ACCOUNT_KEY_FILE=\\(.service_account_key_file // empty)"\')"'
            '\nmkdir -p "$WORK_DIR"\ncd "$WORK_DIR"\n',
            encoding="utf-8",
        )

        _apply_known_module_hotfixes(str(workspace), "k8s/bnk-prerequisites")

        updated = script.read_text(encoding="utf-8")
        # Should now resolve SERVICE_ACCOUNT_KEY_FILE to absolute before cd
        assert "Resolve SERVICE_ACCOUNT_KEY_FILE to absolute path" in updated
        assert 'SERVICE_ACCOUNT_KEY_FILE="$(cd "$(dirname "$SERVICE_ACCOUNT_KEY_FILE")"' in updated


class TestKnownExistingResourceReconcile:
    """Tests for bounded pre-apply import reconciliation."""

    def test_bnk_prereq_reconcile_imports_existing_namespaces_and_skips_missing(self):
        from services.execution.opentofu_runtime import _reconcile_known_existing_k8s_resources

        state_list_result = MagicMock(returncode=0, stdout="")

        import_ok = MagicMock(returncode=0, stdout="imported", stderr="")
        import_missing = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Cannot import non-existent remote object",
        )

        with patch("subprocess.run", side_effect=[state_list_result, import_ok, import_ok, import_ok, import_missing, import_missing, import_missing]):
            count = _reconcile_known_existing_k8s_resources("/tmp/work", "k8s/bnk-prerequisites", {"PATH": "/usr/bin"})

        assert count == 3

    def test_bnk_prereq_reconcile_skips_non_target_module(self):
        from services.execution.opentofu_runtime import _reconcile_known_existing_k8s_resources

        with patch("subprocess.run") as mock_run:
            count = _reconcile_known_existing_k8s_resources("/tmp/work", "infra/aws/vpc", {"PATH": "/usr/bin"})

        assert count == 0
        mock_run.assert_not_called()

    def test_bnk_prereq_reconcile_runs_init_when_state_list_requires_it(self):
        from services.execution.opentofu_runtime import _reconcile_known_existing_k8s_resources

        state_needs_init = MagicMock(returncode=1, stdout="", stderr='Error: Backend initialization required, please run "tofu init"')
        init_ok = MagicMock(returncode=0, stdout="init ok", stderr="")
        state_after_init = MagicMock(returncode=0, stdout="", stderr="")
        import_missing = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Cannot import non-existent remote object",
        )

        with patch(
            "subprocess.run",
            side_effect=[
                state_needs_init,
                init_ok,
                state_after_init,
                import_missing,
                import_missing,
                import_missing,
                import_missing,
                import_missing,
                import_missing,
            ],
        ) as mock_run:
            count = _reconcile_known_existing_k8s_resources("/tmp/work", "k8s/bnk-prerequisites", {"PATH": "/usr/bin"})

        assert count == 0
        called_cmds = [call.args[0] for call in mock_run.call_args_list]
        assert ["tofu", "init", "-no-color", "-input=false"] in called_cmds

    def test_bnk_prereq_reconcile_retries_import_after_init_hint(self):
        from services.execution.opentofu_runtime import _reconcile_known_existing_k8s_resources

        state_list_result = MagicMock(returncode=0, stdout="", stderr="")
        import_requires_init = MagicMock(returncode=1, stdout="", stderr='Error: providers are not installed, run "tofu init"')
        init_ok = MagicMock(returncode=0, stdout="init ok", stderr="")
        import_ok = MagicMock(returncode=0, stdout="imported", stderr="")
        import_missing = MagicMock(
            returncode=1,
            stdout="",
            stderr="Error: Cannot import non-existent remote object",
        )

        with patch(
            "subprocess.run",
            side_effect=[
                state_list_result,
                import_requires_init,
                init_ok,
                import_ok,
                import_missing,
                import_missing,
                import_missing,
                import_missing,
                import_missing,
            ],
        ) as mock_run:
            count = _reconcile_known_existing_k8s_resources("/tmp/work", "k8s/bnk-prerequisites", {"PATH": "/usr/bin"})

        assert count == 1
        called_cmds = [call.args[0] for call in mock_run.call_args_list]
        assert ["tofu", "init", "-no-color", "-input=false"] in called_cmds

    def test_bnk_prereq_reconcile_init_fallback_uses_300s_timeout(self):
        from services.execution.opentofu_runtime import _reconcile_known_existing_k8s_resources

        state_needs_init = MagicMock(returncode=1, stdout="", stderr='Error: Backend initialization required, please run "tofu init"')
        init_ok = MagicMock(returncode=0, stdout="init ok", stderr="")
        state_after_init = MagicMock(returncode=0, stdout="", stderr="")
        import_missing = MagicMock(returncode=1, stdout="", stderr="Error: Cannot import non-existent remote object")

        with patch(
            "subprocess.run",
            side_effect=[
                state_needs_init,
                init_ok,
                state_after_init,
                import_missing,
                import_missing,
                import_missing,
                import_missing,
                import_missing,
                import_missing,
            ],
        ) as mock_run:
            _reconcile_known_existing_k8s_resources("/tmp/work", "k8s/bnk-prerequisites", {"PATH": "/usr/bin"})

        init_calls = [
            call for call in mock_run.call_args_list
            if call.args and call.args[0] == ["tofu", "init", "-no-color", "-input=false"]
        ]
        assert init_calls
        assert init_calls[0].kwargs.get("timeout") == 300

    def test_network_setup_reconcile_imports_existing_nads(self):
        from services.execution.opentofu_runtime import _reconcile_known_existing_k8s_resources

        state_list_result = MagicMock(returncode=0, stdout="", stderr="")
        import_ok = MagicMock(returncode=0, stdout="imported", stderr="")

        with (
            patch("services.execution.opentofu_runtime.os.path.exists", side_effect=lambda path: path.endswith("terraform.tfvars.json")),
            patch("services.execution.opentofu_runtime.Path.read_text", return_value='{"namespace":"f5-operator"}'),
            patch("subprocess.run", side_effect=[state_list_result, import_ok, import_ok]) as mock_run,
        ):
            count = _reconcile_known_existing_k8s_resources("/tmp/work", "k8s/network-setup", {"PATH": "/usr/bin"})

        assert count == 2
        called_cmds = [call.args[0] for call in mock_run.call_args_list]
        assert [
            "tofu",
            "import",
            "-no-color",
            "-input=false",
            "kubernetes_manifest.external_nad",
            "apiVersion=k8s.cni.cncf.io/v1,kind=NetworkAttachmentDefinition,namespace=f5-operator,name=external-netdevice",
        ] in called_cmds
        assert [
            "tofu",
            "import",
            "-no-color",
            "-input=false",
            "kubernetes_manifest.internal_nad",
            "apiVersion=k8s.cni.cncf.io/v1,kind=NetworkAttachmentDefinition,namespace=f5-operator,name=internal-netdevice",
        ] in called_cmds

    def test_cert_manager_reconcile_imports_existing_namespace(self):
        from services.execution.opentofu_runtime import _reconcile_known_existing_k8s_resources

        state_list_result = MagicMock(returncode=0, stdout="", stderr="")
        import_ok = MagicMock(returncode=0, stdout="imported", stderr="")

        with (
            patch("services.execution.opentofu_runtime.os.path.exists", side_effect=lambda path: path.endswith("main.tf")),
            patch(
                "services.execution.opentofu_runtime.Path.read_text",
                side_effect=[
                    '# k8s/cert-manager/main.tf\nresource "kubernetes_namespace_v1" "cert_manager" {\n  count = var.create_namespace ? 1 : 0\n}',
                ],
            ),
            patch("subprocess.run", side_effect=[state_list_result, import_ok]) as mock_run,
        ):
            count = _reconcile_known_existing_k8s_resources("/tmp/work", "k8s/cert-manager", {"PATH": "/usr/bin"})

        assert count == 1
        called_cmds = [call.args[0] for call in mock_run.call_args_list]
        assert [
            "tofu",
            "import",
            "-no-color",
            "-input=false",
            "kubernetes_namespace_v1.cert_manager[0]",
            "cert-manager",
        ] in called_cmds

    def test_reconcile_treats_credential_init_failure_as_retryable_for_targeted_module(self):
        from services.execution.opentofu_runtime import _reconcile_known_existing_k8s_resources

        state_needs_init = MagicMock(returncode=1, stdout="", stderr="Error: No valid credential sources found")
        init_credential_failure = MagicMock(returncode=1, stdout="", stderr="Error: No valid credential sources found")
        state_after_init = MagicMock(returncode=0, stdout="", stderr="")
        import_missing = MagicMock(returncode=1, stdout="", stderr="Error: Cannot import non-existent remote object")

        with (
            patch("services.execution.opentofu_runtime.os.path.exists", side_effect=lambda path: path.endswith("terraform.tfvars.json")),
            patch("services.execution.opentofu_runtime.Path.read_text", return_value='{"namespace":"f5-operator"}'),
            patch(
                "subprocess.run",
                side_effect=[state_needs_init, init_credential_failure, state_after_init, import_missing, import_missing],
            ) as mock_run,
        ):
            count = _reconcile_known_existing_k8s_resources("/tmp/work", "k8s/network-setup", {"AWS_PROFILE": "default"})

        assert count == 0
        called_cmds = [call.args[0] for call in mock_run.call_args_list]
        assert ["tofu", "init", "-no-color", "-input=false"] in called_cmds
