"""
Subprocess mock objects for testing CLI tool invocations.

Provides mocks for:
- OpenTofu (tofu init, plan, apply, destroy)
- Helm (install, upgrade, rollback, uninstall)
- kubectl (apply, delete, get)

These mocks replace subprocess.run / subprocess.Popen calls that invoke
external CLI tools, allowing service tests to run without those tools installed.
"""

import json
from unittest.mock import MagicMock


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    """Create a mock subprocess.CompletedProcess."""
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ---------------------------------------------------------------------------
# OpenTofu Mocks
# ---------------------------------------------------------------------------

def mock_tofu_subprocess(
    *,
    command: str = "init",
    success: bool = True,
    plan_changes: int = 3,
    apply_added: int = 3,
    apply_changed: int = 0,
    apply_destroyed: int = 0,
    outputs: dict | None = None,
) -> MagicMock:
    """
    Mock subprocess result for tofu CLI commands.

    Args:
        command: Which tofu subcommand to mock (init, plan, apply, destroy, output)
        success: Whether the command succeeds
        plan_changes: Number of changes in plan output
        apply_added/changed/destroyed: Resource counts for apply output
        outputs: Dict of output values for tofu output command
    """
    if not success:
        return _make_completed_process(
            returncode=1,
            stderr=f"Error: tofu {command} failed\nSome detailed error message",
        )

    if command == "init":
        return _make_completed_process(
            stdout="Initializing the backend...\n\nOpenTofu has been successfully initialized!",
        )

    if command == "plan":
        return _make_completed_process(
            stdout=(
                f"OpenTofu will perform the following actions:\n\n"
                f"Plan: {plan_changes} to add, 0 to change, 0 to destroy.\n"
            ),
        )

    if command == "apply":
        return _make_completed_process(
            stdout=(
                f"Apply complete! Resources: {apply_added} added, "
                f"{apply_changed} changed, {apply_destroyed} destroyed.\n"
            ),
        )

    if command == "destroy":
        return _make_completed_process(
            stdout=(
                f"Destroy complete! Resources: {apply_destroyed or 3} destroyed.\n"
            ),
        )

    if command == "output":
        output_data = outputs or {"vpc_id": {"value": "vpc-12345", "type": "string"}}
        return _make_completed_process(
            stdout=json.dumps(output_data),
        )

    # Generic success for unknown commands
    return _make_completed_process(stdout=f"tofu {command} completed successfully")


# ---------------------------------------------------------------------------
# Helm Mocks
# ---------------------------------------------------------------------------

def mock_helm_subprocess(
    *,
    command: str = "install",
    success: bool = True,
    release_name: str = "my-release",
    namespace: str = "default",
    releases: list | None = None,
) -> MagicMock:
    """
    Mock subprocess result for helm CLI commands.

    Args:
        command: Which helm subcommand to mock (install, upgrade, rollback, list, etc.)
        success: Whether the command succeeds
        release_name: Release name for install/upgrade output
        namespace: Namespace for output
        releases: List of release dicts for helm list output
    """
    if not success:
        return _make_completed_process(
            returncode=1,
            stderr=f"Error: helm {command} failed: release not found",
        )

    if command == "install":
        return _make_completed_process(
            stdout=(
                f'NAME: {release_name}\n'
                f'NAMESPACE: {namespace}\n'
                f'STATUS: deployed\n'
                f'REVISION: 1\n'
            ),
        )

    if command == "upgrade":
        return _make_completed_process(
            stdout=(
                f'Release "{release_name}" has been upgraded.\n'
                f'NAMESPACE: {namespace}\n'
                f'STATUS: deployed\n'
                f'REVISION: 2\n'
            ),
        )

    if command == "list":
        data = releases or [
            {
                "name": release_name,
                "namespace": namespace,
                "revision": "1",
                "status": "deployed",
                "chart": "nginx-1.0.0",
                "app_version": "1.25.0",
            }
        ]
        return _make_completed_process(stdout=json.dumps(data))

    if command == "rollback":
        return _make_completed_process(
            stdout='Rollback was a success! Happy Helming!\n',
        )

    if command == "uninstall":
        return _make_completed_process(
            stdout=f'release "{release_name}" uninstalled\n',
        )

    return _make_completed_process(stdout=f"helm {command} completed")


# ---------------------------------------------------------------------------
# kubectl Mocks
# ---------------------------------------------------------------------------

def mock_kubectl_subprocess(
    *,
    command: str = "apply",
    success: bool = True,
    resources: list | None = None,
) -> MagicMock:
    """
    Mock subprocess result for kubectl CLI commands.

    Args:
        command: Which kubectl subcommand to mock (apply, delete, get, etc.)
        success: Whether the command succeeds
        resources: List of resource dicts for get output
    """
    if not success:
        return _make_completed_process(
            returncode=1,
            stderr=f"error: kubectl {command} failed: resource not found",
        )

    if command == "apply":
        return _make_completed_process(
            stdout="service/my-service created\ndeployment.apps/my-app created\n",
        )

    if command == "delete":
        return _make_completed_process(
            stdout='service "my-service" deleted\n',
        )

    if command == "get":
        data = resources or {
            "kind": "List",
            "items": [
                {
                    "kind": "Pod",
                    "metadata": {"name": "test-pod", "namespace": "default"},
                    "status": {"phase": "Running"},
                }
            ],
        }
        return _make_completed_process(stdout=json.dumps(data))

    return _make_completed_process(stdout=f"kubectl {command} completed")
