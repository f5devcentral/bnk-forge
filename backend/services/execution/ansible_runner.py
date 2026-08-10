"""Governed Ansible runner for deployment-pack execution."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GOVERNED_RUNNER_PROFILE = "ansible-default"
_DISALLOWED_DEPLOYMENT_PACK_KEYS = {
    "runtime_image",
    "image",
    "container_image",
    "command_template",
    "hooks",
    "pre_hook",
    "post_hook",
    "pre_apply",
    "post_apply",
}


class AnsibleManifestContractError(ValueError):
    """Raised when the manifest violates governed ansible execution rules."""


@dataclass(frozen=True)
class AnsiblePackConfig:
    """Normalized ansible deployment pack config extracted from pack_manifest."""

    working_directory: str
    playbook: str
    destroy_playbook: str | None
    outputs_file: str | None
    supports_destroy: bool


@dataclass(frozen=True)
class AnsibleCommandResult:
    """Captured subprocess result with secret-safe output fields."""

    returncode: int
    stdout: str
    stderr: str


class AnsibleRunner:
    """Bounded ansible runner enforcing governed contract semantics."""

    def health_check(self) -> bool:
        """Return True when ansible-playbook binary is available."""
        try:
            result = subprocess.run(
                ["ansible-playbook", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    def parse_pack_config(self, pack_manifest: dict[str, Any]) -> AnsiblePackConfig:
        """Parse and enforce bounded ansible manifest contract."""
        if not isinstance(pack_manifest, dict):
            raise AnsibleManifestContractError("pack_manifest must be an object")

        deployment_pack = pack_manifest.get("deployment_pack")
        if not isinstance(deployment_pack, dict):
            raise AnsibleManifestContractError("pack_manifest.deployment_pack must be an object")

        for disallowed_key in _DISALLOWED_DEPLOYMENT_PACK_KEYS:
            if disallowed_key in deployment_pack:
                raise AnsibleManifestContractError(
                    f"deployment_pack.{disallowed_key} is not allowed for governed ansible execution"
                )

        engine = deployment_pack.get("engine")
        if engine != "ansible":
            raise AnsibleManifestContractError("deployment_pack.engine must be 'ansible'")

        runner_profile = deployment_pack.get("runner_profile")
        if runner_profile != GOVERNED_RUNNER_PROFILE:
            raise AnsibleManifestContractError(
                f"deployment_pack.runner_profile must be '{GOVERNED_RUNNER_PROFILE}'"
            )

        working_directory = deployment_pack.get("working_directory")
        if not isinstance(working_directory, str) or not working_directory.strip():
            raise AnsibleManifestContractError("deployment_pack.working_directory must be a non-empty string")

        entrypoints = deployment_pack.get("entrypoints")
        if not isinstance(entrypoints, dict):
            raise AnsibleManifestContractError("deployment_pack.entrypoints must be an object")

        playbook = entrypoints.get("playbook")
        if not isinstance(playbook, str) or not playbook.strip():
            raise AnsibleManifestContractError("deployment_pack.entrypoints.playbook must be a non-empty string")

        destroy_playbook = entrypoints.get("destroy_playbook")
        if destroy_playbook is not None and (not isinstance(destroy_playbook, str) or not destroy_playbook.strip()):
            raise AnsibleManifestContractError(
                "deployment_pack.entrypoints.destroy_playbook must be a non-empty string when provided"
            )

        outputs_file = entrypoints.get("outputs_file")
        if outputs_file is not None and (not isinstance(outputs_file, str) or not outputs_file.strip()):
            raise AnsibleManifestContractError(
                "deployment_pack.entrypoints.outputs_file must be a non-empty string when provided"
            )

        lifecycle = deployment_pack.get("lifecycle")
        if not isinstance(lifecycle, dict):
            raise AnsibleManifestContractError("deployment_pack.lifecycle must be an object")
        supports_destroy = bool(lifecycle.get("supports_destroy", False))
        if supports_destroy and not destroy_playbook:
            raise AnsibleManifestContractError(
                "deployment_pack.lifecycle.supports_destroy=true requires deployment_pack.entrypoints.destroy_playbook"
            )

        return AnsiblePackConfig(
            working_directory=working_directory,
            playbook=playbook,
            destroy_playbook=destroy_playbook,
            outputs_file=outputs_file,
            supports_destroy=supports_destroy,
        )

    def resolve_workspace_subpath(self, workspace_root: str, relative_path: str) -> str:
        """Resolve and constrain a relative path inside workspace_root."""
        root = Path(workspace_root).resolve()
        candidate = (root / relative_path).resolve()
        if os.path.commonpath([str(root), str(candidate)]) != str(root):
            raise AnsibleManifestContractError(f"Path escapes workspace boundary: {relative_path}")
        return str(candidate)

    def run_syntax_check(
        self,
        *,
        work_dir: str,
        playbook_path: str,
        variables: dict[str, Any],
    ) -> AnsibleCommandResult:
        """Run ansible syntax validation as init semantics."""
        return self._run_with_extra_vars(
            work_dir=work_dir,
            variables=variables,
            command_prefix=["ansible-playbook", playbook_path, "--syntax-check"],
        )

    def run_check_mode(
        self,
        *,
        work_dir: str,
        playbook_path: str,
        variables: dict[str, Any],
    ) -> AnsibleCommandResult:
        """Run ansible check-mode as plan semantics."""
        return self._run_with_extra_vars(
            work_dir=work_dir,
            variables=variables,
            command_prefix=["ansible-playbook", playbook_path, "--check"],
        )

    def run_apply(
        self,
        *,
        work_dir: str,
        playbook_path: str,
        variables: dict[str, Any],
    ) -> AnsibleCommandResult:
        """Run ansible apply semantics."""
        return self._run_with_extra_vars(
            work_dir=work_dir,
            variables=variables,
            command_prefix=["ansible-playbook", playbook_path],
        )

    def read_outputs_artifact(
        self,
        *,
        work_dir: str,
        outputs_file: str,
        required: bool,
    ) -> dict[str, Any]:
        """Read structured outputs artifact JSON from manifest-configured path."""
        outputs_path = self.resolve_workspace_subpath(work_dir, outputs_file)
        if not os.path.exists(outputs_path):
            if required:
                raise FileNotFoundError(f"Ansible outputs artifact not found: {outputs_file}")
            return {}

        with open(outputs_path) as file_handle:
            data = json.load(file_handle)

        if not isinstance(data, dict):
            raise ValueError("Ansible outputs artifact must be a JSON object")
        return data

    def _run_with_extra_vars(
        self,
        *,
        work_dir: str,
        variables: dict[str, Any],
        command_prefix: list[str],
    ) -> AnsibleCommandResult:
        extra_vars_file = self._write_extra_vars_file(work_dir, variables)
        command = [*command_prefix, "--extra-vars", f"@{extra_vars_file}"]
        try:
            return self._run(command=command, cwd=work_dir)
        finally:
            self._cleanup_extra_vars_file(extra_vars_file)

    def _write_extra_vars_file(self, work_dir: str, variables: dict[str, Any]) -> str:
        fd, vars_file = tempfile.mkstemp(prefix=".bnk_ansible_vars_", suffix=".json", dir=work_dir)
        file_opened = False
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as file_handle:
                file_opened = True
                json.dump(variables or {}, file_handle)
        except Exception:
            if not file_opened:
                os.close(fd)
            raise
        return vars_file

    def _cleanup_extra_vars_file(self, vars_file: str) -> None:
        try:
            os.remove(vars_file)
        except FileNotFoundError:
            return

    def _run(self, *, command: list[str], cwd: str) -> AnsibleCommandResult:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        return AnsibleCommandResult(
            returncode=result.returncode,
            stdout=_sanitize_process_output(result.stdout),
            stderr=_sanitize_process_output(result.stderr),
        )


def _sanitize_process_output(output: str) -> str:
    """Best-effort secret redaction for ansible subprocess output."""
    if not output:
        return ""

    redacted_lines: list[str] = []
    secret_pattern = re.compile(
        r"(?i)\b(password|token|secret|api[_-]?key|private[_-]?key)\b\s*[:=]\s*([^\s]+)"
    )

    for line in output.splitlines():
        redacted_lines.append(secret_pattern.sub(r"\1=***REDACTED***", line))
    return "\n".join(redacted_lines)
