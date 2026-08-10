"""
Tests for services.execution.opentofu_runtime — OpenTofu subprocess management.

Covers run_init, run_plan, run_apply, run_destroy, _capture_outputs,
parse_destroy_error, get_destroy_timeout, and cleanup_workspace.
"""

import json
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from services.execution.opentofu_runtime import OpenTofuRuntime
from services.git_auth_service import GitAuthContext, GitAuthService

# ── Helpers ──────────────────────────────────────────────────────────────

@patch("services.execution.opentofu_runtime.check_required_configured")
def _make_runtime(mock_check, configured=True):
    """Build an OpenTofuRuntime with mocked system defaults check."""
    mock_check.return_value = {"all_configured": configured, "missing": []}
    db = MagicMock()
    return OpenTofuRuntime(db), db


def _mock_subprocess_result(returncode=0, stdout="OK", stderr=""):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ── run_init ─────────────────────────────────────────────────────────────

class TestRunInit:
    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = _mock_subprocess_result(0, "Initialized successfully")
        runtime, _ = _make_runtime()
        code, output = runtime.run_init("/tmp/work", {})
        assert code == 0
        assert "Initialized" in output

    @patch("subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = _mock_subprocess_result(1, "", "Error downloading provider")
        runtime, _ = _make_runtime()
        code, output = runtime.run_init("/tmp/work", {})
        assert code == 1
        assert "Error" in output

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        exc = subprocess.TimeoutExpired(["tofu", "init"], 900)
        exc.stdout = "partial output"
        exc.stderr = ""
        mock_run.side_effect = exc
        runtime, _ = _make_runtime()
        code, output = runtime.run_init("/tmp/work", {}, timeout=900)
        assert code == 1
        assert "timed out" in output


# ── run_plan ─────────────────────────────────────────────────────────────

class TestRunPlan:
    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = _mock_subprocess_result(0, "Plan: 2 to add, 0 to change, 0 to destroy.")
        runtime, _ = _make_runtime()
        code, output = runtime.run_plan("/tmp/work", {})
        assert code == 0
        assert "Plan:" in output

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        exc = subprocess.TimeoutExpired(["tofu", "plan"], 1800)
        exc.stdout = ""
        exc.stderr = ""
        mock_run.side_effect = exc
        runtime, _ = _make_runtime()
        code, output = runtime.run_plan("/tmp/work", {})
        assert code == 1
        assert "timed out" in output


# ── run_apply ────────────────────────────────────────────────────────────

class TestRunApply:
    @patch("subprocess.run")
    def test_success_with_outputs(self, mock_run):
        apply_result = _mock_subprocess_result(0, "Apply complete! Resources: 3 added, 0 changed, 0 destroyed.")
        output_result = _mock_subprocess_result(0, json.dumps({"vpc_id": {"value": "vpc-123"}}))
        mock_run.side_effect = [apply_result, output_result]
        runtime, _ = _make_runtime()
        code, output, outputs = runtime.run_apply("/tmp/work", {})
        assert code == 0
        assert outputs["vpc_id"] == "vpc-123"

    @patch("services.execution.opentofu_runtime.normalize_infrastructure_access_outputs")
    @patch("subprocess.run")
    def test_success_with_module_normalizes_outputs(self, mock_run, mock_normalize):
        apply_result = _mock_subprocess_result(0, "Apply complete!")
        output_result = _mock_subprocess_result(0, json.dumps({"raw": {"value": "data"}}))
        mock_run.side_effect = [apply_result, output_result]
        mock_normalize.return_value = MagicMock(outputs={"normalized": True})

        runtime, _ = _make_runtime()
        module = MagicMock()
        module.project_id = 22
        module.id = 33

        code, _output, outputs = runtime.run_apply("/tmp/work", {}, module=module)
        assert code == 0
        assert outputs == {"normalized": True}
        mock_normalize.assert_called_once_with({"raw": "data"}, project_id=22, module_id=33)

    @patch("subprocess.run")
    def test_failure_no_outputs(self, mock_run):
        mock_run.return_value = _mock_subprocess_result(1, "", "Error: creating VPC")
        runtime, _ = _make_runtime()
        code, output, outputs = runtime.run_apply("/tmp/work", {})
        assert code == 1
        assert outputs == {}

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        exc = subprocess.TimeoutExpired(["tofu", "apply"], 5400)
        exc.stdout = "partial"
        exc.stderr = ""
        mock_run.side_effect = exc
        runtime, _ = _make_runtime()
        code, output, outputs = runtime.run_apply("/tmp/work", {})
        assert code == 1
        assert outputs == {}
        assert "timed out" in output


# ── run_destroy ──────────────────────────────────────────────────────────

class TestRunDestroy:
    @patch("subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = _mock_subprocess_result(0, "Destroy complete! Resources: 5 destroyed.")
        runtime, _ = _make_runtime()
        code, output = runtime.run_destroy("/tmp/work", {})
        assert code == 0
        assert "Destroy complete" in output

    @patch("subprocess.run")
    def test_timeout(self, mock_run):
        exc = subprocess.TimeoutExpired(["tofu", "destroy"], 1800)
        exc.stdout = ""
        exc.stderr = ""
        mock_run.side_effect = exc
        runtime, _ = _make_runtime()
        code, output = runtime.run_destroy("/tmp/work", {})
        assert code == 1
        assert "timed out" in output


# ── _capture_outputs ─────────────────────────────────────────────────────

class TestCaptureOutputs:
    @patch("subprocess.run")
    def test_captures_json_outputs(self, mock_run):
        mock_run.return_value = _mock_subprocess_result(
            0,
            json.dumps({
                "vpc_id": {"value": "vpc-abc"},
                "subnet_ids": {"value": ["sub-1", "sub-2"]},
            }),
        )
        runtime, _ = _make_runtime()
        outputs = runtime.capture_outputs("/tmp/work", {})
        assert outputs["vpc_id"] == "vpc-abc"
        assert outputs["subnet_ids"] == ["sub-1", "sub-2"]

    @patch("subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = _mock_subprocess_result(1, "", "No state")
        runtime, _ = _make_runtime()
        outputs = runtime.capture_outputs("/tmp/work", {})
        assert outputs == {}

    @patch("subprocess.run")
    def test_returns_empty_on_invalid_json(self, mock_run):
        mock_run.return_value = _mock_subprocess_result(0, "not-json")
        runtime, _ = _make_runtime()
        outputs = runtime.capture_outputs("/tmp/work", {})
        assert outputs == {}


# ── parse_destroy_error ──────────────────────────────────────────────────

class TestParseDestroyError:
    def test_eni_dependency(self):
        runtime, _ = _make_runtime()
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "other"
        output = "DependencyViolation: network interface eni-123"
        result = runtime.parse_destroy_error(output, module)
        assert "ENI" in result

    def test_no_matching_pattern_returns_empty(self):
        runtime, _ = _make_runtime()
        module = MagicMock()
        module.library_module = None
        result = runtime.parse_destroy_error("Random error", module)
        assert result == ""


# ── get_destroy_timeout ──────────────────────────────────────────────────

class TestGetDestroyTimeout:
    def test_vpc_timeout(self):
        runtime, _ = _make_runtime()
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "aws-vpc"
        assert runtime.get_destroy_timeout(module) == 45 * 60

    def test_eks_timeout(self):
        runtime, _ = _make_runtime()
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "aws-eks"
        assert runtime.get_destroy_timeout(module) == 60 * 60

    def test_default_timeout(self):
        runtime, _ = _make_runtime()
        module = MagicMock()
        module.library_module = MagicMock()
        module.library_module.name = "some-random-module"
        assert runtime.get_destroy_timeout(module) == 30 * 60

    def test_no_library_module(self):
        runtime, _ = _make_runtime()
        module = MagicMock()
        module.library_module = None
        assert runtime.get_destroy_timeout(module) == 30 * 60


class TestCloneModuleSourceAuthTransport:
    @patch("services.execution.opentofu_runtime.subprocess.run")
    def test_clone_module_source_uses_sanitized_url_and_env_auth(self, mock_run):
        runtime, _ = _make_runtime()

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("services.execution.opentofu_runtime.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.execution.opentofu_runtime.GitAuthService.build_git_environment") as mock_env, \
                patch("services.execution.opentofu_runtime.os.listdir", return_value=[]), \
                patch("services.execution.opentofu_runtime.os.path.exists", return_value=True):
            mock_resolve.return_value = GitAuthContext(
                transport="https_token",
                credential_type="legacy_pat",
                secret="runtime-token",
                username="git",
            )
            mock_env.return_value = ({"GIT_ASKPASS": "/tmp/askpass", "GIT_TERMINAL_PROMPT": "0"}, lambda: None)

            module_source = MagicMock()
            module_source.url = "https://repo-token@github.com/org/repo.git"
            module_source.branch = "main"
            module_source.git_ref = None

            runtime._clone_module_source(
                "git::https://repo-token@github.com/org/repo.git//modules/vpc?ref=main",
                "/tmp/work",
                module_source,
            )

        called_cmd = mock_run.call_args.args[0]
        called_kwargs = mock_run.call_args.kwargs
        assert called_cmd[6] == "https://github.com/org/repo.git"
        assert "runtime-token" not in called_cmd[6]
        assert called_kwargs["env"]["GIT_ASKPASS"] == "/tmp/askpass"
        assert mock_resolve.call_args.kwargs["db"] is not None

    @patch("services.execution.opentofu_runtime.subprocess.run")
    def test_clone_module_source_sanitizes_secret_in_error_message(self, mock_run):
        runtime, _ = _make_runtime()
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=128,
            cmd=["git", "clone"],
            stderr="Authentication failed for https://top-secret-token@github.com/org/repo.git",
        )

        with patch("services.execution.opentofu_runtime.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.execution.opentofu_runtime.GitAuthService.build_git_environment") as mock_env:
            mock_resolve.return_value = GitAuthContext(
                transport="https_token",
                credential_type="legacy_pat",
                secret="top-secret-token",
                username="git",
            )
            mock_env.return_value = ({"GIT_TERMINAL_PROMPT": "0"}, lambda: None)

            with pytest.raises(RuntimeError) as exc_info:
                runtime._clone_module_source(
                    "git::https://github.com/org/repo.git//modules/vpc?ref=main",
                    "/tmp/work",
                    MagicMock(),
                )

        message = str(exc_info.value)
        assert "top-secret-token" not in message
        assert "***" in message
        assert "[auth_failure]" in message

    @patch("services.execution.opentofu_runtime.subprocess.run")
    def test_clone_module_source_ssh_uses_git_ssh_wrapper(self, mock_run):
        runtime, _ = _make_runtime()

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("services.execution.opentofu_runtime.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.execution.opentofu_runtime.GitAuthService.build_git_environment") as mock_env, \
                patch("services.execution.opentofu_runtime.os.listdir", return_value=[]), \
                patch("services.execution.opentofu_runtime.os.path.exists", return_value=True):
            mock_resolve.return_value = GitAuthContext(
                transport="ssh_key",
                credential_type="ssh_deploy_key",
                secret="PRIVATE KEY",
            )
            mock_env.return_value = ({"GIT_SSH": "/tmp/git_ssh.sh", "GIT_TERMINAL_PROMPT": "0"}, lambda: None)

            runtime._clone_module_source(
                "git::ssh://git@github.com/org/repo.git//modules/vpc?ref=main",
                "/tmp/work",
                MagicMock(),
            )

        called_kwargs = mock_run.call_args.kwargs
        assert "GIT_SSH" in called_kwargs["env"]
        assert "GIT_SSH_COMMAND" not in called_kwargs["env"]

    def test_clone_module_source_ssh_requires_known_hosts_via_shared_auth_path(self):
        runtime, _ = _make_runtime()

        with patch("services.execution.opentofu_runtime.GitAuthService.resolve_for_module_source") as mock_resolve, \
                patch("services.execution.opentofu_runtime.GitAuthService.build_git_environment", side_effect=RuntimeError("known_hosts required")):
            mock_resolve.return_value = GitAuthContext(
                transport="ssh_key",
                credential_type="ssh_deploy_key",
                secret="PRIVATE KEY",
                ssh_known_hosts=None,
            )

            with pytest.raises(RuntimeError, match="known_hosts required"):
                runtime._clone_module_source(
                    "git::ssh://git@git.internal.example.com/org/repo.git//modules/vpc?ref=main",
                    "/tmp/work",
                    MagicMock(),
                )


# ── _strip_global_tfvar_secrets ──────────────────────────────────────────

def _make_lib_module(declared_var_names: list[str]):
    """Build a fake lib_module whose variables_schema declares the given names."""
    lib_module = MagicMock()
    lib_module.variables_schema = [{"name": n} for n in declared_var_names]
    return lib_module


class TestStripGlobalTfvarSecrets:
    """
    Unit surface for OpenTofuRuntime._strip_global_tfvar_secrets.

    The helper must:
    - Remove jwt_token when the module does NOT declare it.
    - Preserve jwt_token when the module DOES declare it (e.g. bnk/flo).
    - Leave all other keys untouched in both cases.
    - Return input unchanged when jwt_token is absent.
    - Handle lib_modules with no variables_schema (None / empty).
    """

    def test_strips_jwt_token_when_undeclared(self):
        runtime, _ = _make_runtime()
        lib_module = _make_lib_module(["other_var", "region"])
        variables = {"jwt_token": "secret", "region": "us-east-1", "cne_pull_secret": "x"}

        result = runtime._strip_global_tfvar_secrets(variables, lib_module)

        assert "jwt_token" not in result
        assert result["region"] == "us-east-1"
        assert result["cne_pull_secret"] == "x"

    def test_preserves_jwt_token_when_declared(self):
        # bnk/flo declares jwt_token — canonical production reference (architect-r1.md §8)
        runtime, _ = _make_runtime()
        lib_module = _make_lib_module(["jwt_token", "manifest_version"])
        variables = {"jwt_token": "license-token", "manifest_version": "3.2.1"}

        result = runtime._strip_global_tfvar_secrets(variables, lib_module)

        assert result["jwt_token"] == "license-token"
        assert result["manifest_version"] == "3.2.1"

    def test_no_op_when_jwt_token_absent(self):
        runtime, _ = _make_runtime()
        lib_module = _make_lib_module(["region"])
        variables = {"region": "eu-west-1", "cne_pull_secret": "blob"}

        result = runtime._strip_global_tfvar_secrets(variables, lib_module)

        assert result == variables

    def test_strips_jwt_token_when_schema_is_none(self):
        runtime, _ = _make_runtime()
        lib_module = MagicMock()
        lib_module.variables_schema = None
        variables = {"jwt_token": "tok", "region": "us-east-1"}

        result = runtime._strip_global_tfvar_secrets(variables, lib_module)

        assert "jwt_token" not in result
        assert result["region"] == "us-east-1"

    def test_strips_jwt_token_when_schema_is_empty(self):
        runtime, _ = _make_runtime()
        lib_module = _make_lib_module([])
        variables = {"jwt_token": "tok", "vpc_id": "vpc-abc"}

        result = runtime._strip_global_tfvar_secrets(variables, lib_module)

        assert "jwt_token" not in result
        assert result["vpc_id"] == "vpc-abc"

    def test_does_not_mutate_input_dict(self):
        runtime, _ = _make_runtime()
        lib_module = _make_lib_module([])
        variables = {"jwt_token": "tok", "region": "ap-southeast-2"}
        original = dict(variables)

        runtime._strip_global_tfvar_secrets(variables, lib_module)

        assert variables == original


class TestCloneModuleSourceRef:
    """#321 — _clone_module_source must honor ModuleSource.git_ref/branch when the
    git_source carries no explicit ?ref= (engine=kubernetes modules otherwise clone
    an empty 'main')."""

    def _run_clone(self, git_source, module_source, tmp_path):
        runtime, _ = _make_runtime()
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _mock_subprocess_result(0, "Cloning...")

        with patch("services.execution.opentofu_runtime.subprocess.run", side_effect=_fake_run), \
             patch.object(GitAuthService, "resolve_for_module_source") as m_rms, \
             patch.object(GitAuthService, "resolve_for_module_library_token_setting") as m_rml, \
             patch.object(GitAuthService, "strip_url_credentials", side_effect=lambda u: u), \
             patch.object(GitAuthService, "build_git_environment", return_value=({}, lambda: None)):
            m_rms.return_value = MagicMock()
            m_rml.return_value = MagicMock()
            runtime._clone_module_source(git_source, str(tmp_path), module_source)
        # ["git","clone","--depth=1","--branch", ref, "--", source, dest]
        return captured["cmd"][4]

    def test_falls_back_to_git_ref_when_no_explicit_ref(self, tmp_path):
        src = MagicMock()
        src.git_ref = "release/2.3"
        src.branch = None
        ref = self._run_clone("git::https://github.com/org/repo.git", src, tmp_path)
        assert ref == "release/2.3"

    def test_falls_back_to_branch_when_no_git_ref(self, tmp_path):
        src = MagicMock()
        src.git_ref = None
        src.branch = "release/2.3"
        ref = self._run_clone("git::https://github.com/org/repo.git", src, tmp_path)
        assert ref == "release/2.3"

    def test_explicit_ref_in_source_wins_over_module_source(self, tmp_path):
        src = MagicMock()
        src.git_ref = "release/2.3"
        src.branch = None
        ref = self._run_clone("git::https://github.com/org/repo.git?ref=feature-x", src, tmp_path)
        assert ref == "feature-x"

    def test_defaults_to_main_when_nothing_set(self, tmp_path):
        src = MagicMock()
        src.git_ref = None
        src.branch = None
        ref = self._run_clone("git::https://github.com/org/repo.git", src, tmp_path)
        assert ref == "main"

    def test_defaults_to_main_when_module_source_none(self, tmp_path):
        ref = self._run_clone("git::https://github.com/org/repo.git", None, tmp_path)
        assert ref == "main"
