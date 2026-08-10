"""
Helm service for BNK-Forge.
Provides Helm chart and release management capabilities.

Repository management is in helm_repository_service.py (HelmRepositoryMixin).
Chart store (upload/clone) is in helm_chart_store_service.py (HelmChartStoreMixin).
"""

import json
import logging
import os
import subprocess
import tempfile
from typing import Any

from sqlalchemy.orm import Session

from core.errors import ReleaseNotFoundError
from models import KubernetesCluster
from services.cluster_utils import get_cluster as get_cluster_util
from services.cluster_utils import kubeconfig_for_cluster, prepare_kubeconfig
from services.helm_chart_store_service import HelmChartStoreMixin
from services.helm_repository_service import HelmRepositoryMixin
from services.reachability import with_breaker
from utils.security import validate_cli_arg, validate_helm_timeout

logger = logging.getLogger(__name__)

# Default helm --timeout (wait duration) and the subprocess wall-clock timeout.
# The subprocess timeout MUST exceed the helm --timeout, otherwise a genuine
# helm wait that runs to its full duration gets SIGKILLed at exactly the helm
# timeout boundary (exit_code=124) even when the release ultimately converges.
# Keep the relationship derived, not two independent magic numbers.
DEFAULT_HELM_TIMEOUT = "5m"
# Grace added on top of the parsed helm --timeout for the subprocess wall clock.
SUBPROCESS_TIMEOUT_GRACE_SEC = 60
# Fallback subprocess timeout for helm commands that take no --timeout flag
# (list/status/get/...): no helm-side wait, so a fixed ceiling is appropriate.
DEFAULT_SUBPROCESS_TIMEOUT_SEC = 300

# helm prints this on stderr when a named release does not exist. Matching it is
# how we distinguish a genuine not-found from a transient backend failure.
_HELM_NOT_FOUND_MARKERS = ("release: not found", "not found")


def _parse_helm_timeout_seconds(timeout: str) -> int:
    """Parse a helm duration string (e.g. ``5m``, ``300s``, ``1h30m``) to seconds.

    Mirrors the units helm accepts. Returns a best-effort total; on any parse
    failure falls back to interpreting nothing and the caller's default applies.
    """
    units = {"h": 3600, "m": 60, "s": 1}
    total = 0
    num = ""
    for ch in timeout:
        if ch.isdigit():
            num += ch
        elif ch in units and num:
            total += int(num) * units[ch]
            num = ""
        else:
            num = ""
    return total


def _subprocess_timeout_for(helm_timeout: str | None) -> int:
    """Derive the subprocess wall-clock timeout from the helm --timeout.

    Always strictly greater than the helm --timeout so a full-duration helm wait
    is never killed before helm itself gives up. Falls back to a fixed ceiling
    for commands with no helm-side wait.
    """
    if not helm_timeout:
        return DEFAULT_SUBPROCESS_TIMEOUT_SEC
    parsed = _parse_helm_timeout_seconds(helm_timeout)
    if parsed <= 0:
        return DEFAULT_SUBPROCESS_TIMEOUT_SEC
    return parsed + SUBPROCESS_TIMEOUT_GRACE_SEC


class HelmService(HelmRepositoryMixin, HelmChartStoreMixin):
    """
    Helm chart and release management service.
    Uses Helm CLI for all operations with proper kubeconfig management.

    Repository management provided by HelmRepositoryMixin.
    Chart store (upload/clone) provided by HelmChartStoreMixin.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_cluster(self, cluster_id: int) -> KubernetesCluster:
        """Get cluster configuration from database."""
        return get_cluster_util(self.db, cluster_id)

    def _validate_arg(self, name: str, value: str | None):
        """
        Validate that an argument does not look like a CLI flag.

        Delegates to the centralized ``utils.security.validate_cli_arg``.
        Kept as an instance method for backward compatibility with existing
        call sites within this class.
        """
        validate_cli_arg(name, value)

    @staticmethod
    def _extract_helm_timeout(command: list[str]) -> str | None:
        """Return the helm ``--timeout`` value embedded in *command*, if any.

        Commands that pass ``--wait --timeout 5m`` need a subprocess wall-clock
        timeout strictly larger than that helm timeout; commands without a
        ``--timeout`` (list/status/get) have no helm-side wait.
        """
        try:
            idx = command.index('--timeout')
        except ValueError:
            return None
        if idx + 1 < len(command):
            return command[idx + 1]
        return None

    def _prepare_kubeconfig(self, cluster: KubernetesCluster) -> Any:
        """
        Prepare kubeconfig file for Helm operations.
        Delegates to shared cluster_utils.prepare_kubeconfig for DRY compliance.
        """
        return prepare_kubeconfig(cluster, self.db)

    def _run_helm_command(
        self,
        cluster: KubernetesCluster,
        command: list[str],
        namespace: str | None = None,
        context: str | None = None
    ) -> dict[str, Any]:
        """
        Run a Helm command with proper kubeconfig setup.

        Args:
            cluster: Kubernetes cluster configuration
            command: Helm command arguments (e.g., ['list', '--output', 'json'])
            namespace: Optional namespace (default: all namespaces)
            context: Optional kube context name. When set, ``--kube-context``
                is appended so Helm targets that context without depending on
                the kubeconfig's ``current-context``. When None, behavior is
                unchanged (no ``--kube-context`` flag). Note: Helm uses
                ``--kube-context``; ``--context`` (kubectl's flag) is rejected.

        Returns:
            Dict with exit_code, stdout, stderr
        """
        # Reject a context that could be parsed as a CLI flag (e.g. leading '-')
        # before it reaches the helm argv. None/empty pass through unchanged.
        self._validate_arg("context", context)

        # Prepare kubeconfig
        kubeconfig_file = self._prepare_kubeconfig(cluster)
        kubeconfig_path = kubeconfig_file.name

        try:
            # Build Helm command
            helm_cmd = ['helm'] + command
            helm_cmd.extend(['--kubeconfig', kubeconfig_path])

            if context:
                helm_cmd.extend(['--kube-context', context])

            if namespace:
                helm_cmd.extend(['--namespace', namespace])

            logger.info(f"Running Helm command: {' '.join(helm_cmd)}")

            # Derive the subprocess wall-clock timeout from the helm --timeout
            # embedded in the command (if any), so a full-duration helm --wait is
            # never SIGKILLed before helm itself gives up (the exit_code=124 bug).
            helm_timeout = self._extract_helm_timeout(command)
            subprocess_timeout = _subprocess_timeout_for(helm_timeout)

            # Run command
            result = subprocess.run(
                helm_cmd,
                capture_output=True,
                text=True,
                timeout=subprocess_timeout
            )

            return {
                'exit_code': result.returncode,
                'stdout': result.stdout,
                'stderr': result.stderr
            }

        except subprocess.TimeoutExpired as e:
            logger.error(f"Helm command timed out after {e.timeout}s")
            return {
                'exit_code': 124,
                'stdout': '',
                'stderr': f'Command timed out after {e.timeout}s'
            }
        except Exception as e:
            logger.error(f"Error running Helm command: {e}")
            return {
                'exit_code': 1,
                'stdout': '',
                'stderr': str(e)
            }
        finally:
            # Clean up temporary kubeconfig
            try:
                if os.path.exists(kubeconfig_path):
                    os.unlink(kubeconfig_path)
            except Exception as e:
                logger.warning(f"Failed to delete temporary kubeconfig: {e}")

    @with_breaker("cluster", target_id_arg="cluster_id")
    def list_releases(
        self,
        cluster_id: int,
        namespace: str | None = None,
        all_namespaces: bool = False,
        show_all_status: bool = True
    ) -> list[dict[str, Any]]:
        """
        List Helm releases in a cluster.

        Args:
            cluster_id: Kubernetes cluster ID
            namespace: Specific namespace (default: all)
            all_namespaces: List releases across all namespaces
            show_all_status: Include failed/pending releases (default: True)

        Returns:
            List of release dictionaries
        """
        self._validate_arg("namespace", namespace)

        cluster = self.get_cluster(cluster_id)

        command = ['list', '--output', 'json']

        if all_namespaces:
            command.append('--all-namespaces')

        # CRITICAL: Show ALL releases including failed/pending-install/pending-upgrade
        # This is essential for a Helm management portal to show stuck releases
        if show_all_status:
            command.append('--all')

        result = self._run_helm_command(cluster, command, namespace)

        if result['exit_code'] != 0:
            raise RuntimeError(f"Failed to list releases: {result['stderr']}")

        try:
            releases = json.loads(result['stdout']) if result['stdout'].strip() else []
            return releases
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Helm output: {e}")
            return []

    def get_release(
        self,
        cluster_id: int,
        release_name: str,
        namespace: str | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        """
        Get detailed information about a specific release.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Name of the Helm release
            namespace: Namespace where release is installed
            context: Optional kube context to target (see _run_helm_command)

        Returns:
            Release details dictionary
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("namespace", namespace)

        cluster = self.get_cluster(cluster_id)

        command = ['status', release_name, '--output', 'json']

        result = self._run_helm_command(cluster, command, namespace, context)

        if result['exit_code'] != 0:
            stderr = result['stderr'] or ''
            # Distinguish a genuine "release does not exist" from a transient
            # backend failure (API-server unreachable, auth expiry, helm timeout
            # exit_code=124). Callers probing existence must NOT treat the latter
            # as "absent" — doing so triggers a spurious reinstall of a shared
            # control plane. Only helm's not-found marker maps to absence.
            if (
                result['exit_code'] != 124
                and any(marker in stderr.lower() for marker in _HELM_NOT_FOUND_MARKERS)
            ):
                raise ReleaseNotFoundError(release_name)
            raise RuntimeError(f"Failed to get release: {stderr}")

        try:
            return json.loads(result['stdout'])
        except json.JSONDecodeError as e:
            raise RuntimeError(f"Failed to parse release info: {e}")

    def install_chart(
        self,
        cluster_id: int,
        release_name: str,
        chart: str,
        namespace: str | None = None,
        values: dict[str, Any] | None = None,
        version: str | None = None,
        create_namespace: bool = True,
        wait: bool = True,
        timeout: str = DEFAULT_HELM_TIMEOUT,
        context: str | None = None
    ) -> dict[str, Any]:
        """
        Install a Helm chart.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Name for the release
            chart: Chart reference (e.g., 'bitnami/nginx', 'my-repo/my-chart')
            namespace: Target namespace
            values: Values to override (optional)
            version: Chart version (optional)
            create_namespace: Create namespace if it doesn't exist
            wait: Wait for resources to be ready
            timeout: Timeout for wait (e.g., '5m', '10m')
            context: Optional kubectl context to target (default: kubeconfig's
                current-context).

        Returns:
            Installation result
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("chart", chart)
        self._validate_arg("version", version)
        self._validate_arg("namespace", namespace)
        validate_helm_timeout(timeout)

        cluster = self.get_cluster(cluster_id)

        # `upgrade --install` is idempotent: installs if missing, upgrades if
        # present.  Avoids "release name already in use" failures on Forge's
        # Redeploy flow when a prior partial install left a release behind.
        command = ['upgrade', '--install', release_name, chart, '--output', 'json']

        if version:
            command.extend(['--version', version])

        if create_namespace:
            command.append('--create-namespace')

        if wait:
            command.extend(['--wait', '--timeout', timeout])

        # Handle custom values
        if values:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                import yaml
                yaml.dump(values, f)
                values_file = f.name

            try:
                command.extend(['--values', values_file])
                result = self._run_helm_command(cluster, command, namespace, context)
            finally:
                os.unlink(values_file)
        else:
            result = self._run_helm_command(cluster, command, namespace, context)

        if result['exit_code'] != 0:
            error_msg = result['stderr'] or result['stdout'] or 'Unknown error'
            logger.error(f"Helm install failed with exit code {result['exit_code']}: {error_msg}")

            # Check for user errors (400-level) vs system errors (500-level)
            if 'cannot re-use a name that is still in use' in error_msg:
                raise ValueError(f"Release name already in use: {error_msg}")
            elif 'not found' in error_msg.lower():
                raise ValueError(f"Chart not found: {error_msg}")
            else:
                raise RuntimeError(f"Failed to install chart: {error_msg}")

        try:
            return json.loads(result['stdout'])
        except json.JSONDecodeError:
            # Some Helm versions don't output JSON on install
            return {
                'success': True,
                'message': result['stdout'],
                'release': release_name,
                'namespace': namespace
            }

    def upgrade_release(
        self,
        cluster_id: int,
        release_name: str,
        chart: str | None = None,
        namespace: str | None = None,
        values: dict[str, Any] | None = None,
        version: str | None = None,
        install: bool = True,
        wait: bool = True,
        timeout: str = "5m"
    ) -> dict[str, Any]:
        """
        Upgrade a Helm release.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Name of the release
            chart: Chart reference (optional if already installed)
            namespace: Namespace of the release
            values: Values to override (optional)
            version: Chart version (optional)
            install: Install if release doesn't exist
            wait: Wait for resources to be ready
            timeout: Timeout for wait

        Returns:
            Upgrade result
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("chart", chart)
        self._validate_arg("version", version)
        self._validate_arg("namespace", namespace)
        validate_helm_timeout(timeout)

        cluster = self.get_cluster(cluster_id)

        command = ['upgrade', release_name]

        if chart:
            command.append(chart)

        command.extend(['--output', 'json'])

        if version:
            command.extend(['--version', version])

        if install:
            command.append('--install')

        if wait:
            command.extend(['--wait', '--timeout', timeout])

        # Handle custom values
        if values:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                import yaml
                yaml.dump(values, f)
                values_file = f.name

            try:
                command.extend(['--values', values_file])
                result = self._run_helm_command(cluster, command, namespace)
            finally:
                os.unlink(values_file)
        else:
            result = self._run_helm_command(cluster, command, namespace)

        if result['exit_code'] != 0:
            raise RuntimeError(f"Failed to upgrade release: {result['stderr']}")

        try:
            return json.loads(result['stdout'])
        except json.JSONDecodeError:
            return {
                'success': True,
                'message': result['stdout'],
                'release': release_name,
                'namespace': namespace
            }

    def rollback_release(
        self,
        cluster_id: int,
        release_name: str,
        revision: int | None = None,
        namespace: str | None = None,
        wait: bool = True,
        timeout: str = "5m"
    ) -> dict[str, Any]:
        """
        Rollback a Helm release to a previous revision.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Name of the release
            revision: Revision number to rollback to (default: previous)
            namespace: Namespace of the release
            wait: Wait for resources to be ready
            timeout: Timeout for wait

        Returns:
            Rollback result
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("namespace", namespace)
        validate_helm_timeout(timeout)

        cluster = self.get_cluster(cluster_id)

        command = ['rollback', release_name]

        if revision:
            command.append(str(revision))

        if wait:
            command.extend(['--wait', '--timeout', timeout])

        result = self._run_helm_command(cluster, command, namespace)

        if result['exit_code'] != 0:
            raise RuntimeError(f"Failed to rollback release: {result['stderr']}")

        return {
            'success': True,
            'message': result['stdout'],
            'release': release_name,
            'namespace': namespace,
            'revision': revision
        }

    def uninstall_release(
        self,
        cluster_id: int,
        release_name: str,
        namespace: str | None = None,
        keep_history: bool = False,
        wait: bool = True,
        timeout: str = "5m",
        context: str | None = None
    ) -> dict[str, Any]:
        """
        Uninstall a Helm release.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Name of the release
            namespace: Namespace of the release
            keep_history: Keep release history
            wait: Wait for resources to be deleted
            timeout: Timeout for wait
            context: Optional kubectl context to target (default: kubeconfig's
                current-context).

        Returns:
            Uninstall result
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("namespace", namespace)
        validate_helm_timeout(timeout)

        cluster = self.get_cluster(cluster_id)

        command = ['uninstall', release_name]

        if keep_history:
            command.append('--keep-history')

        if wait:
            command.extend(['--wait', '--timeout', timeout])

        result = self._run_helm_command(cluster, command, namespace, context)

        if result['exit_code'] != 0:
            raise RuntimeError(f"Failed to uninstall release: {result['stderr']}")

        return {
            'success': True,
            'message': result['stdout'],
            'release': release_name,
            'namespace': namespace
        }

    def get_history(
        self,
        cluster_id: int,
        release_name: str,
        namespace: str | None = None,
        max_revisions: int = 256
    ) -> list[dict[str, Any]]:
        """
        Get revision history for a release.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Name of the release
            namespace: Namespace of the release
            max_revisions: Maximum number of revisions to return

        Returns:
            List of revision dictionaries
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("namespace", namespace)

        cluster = self.get_cluster(cluster_id)

        command = ['history', release_name, '--output', 'json', '--max', str(max_revisions)]

        result = self._run_helm_command(cluster, command, namespace)

        if result['exit_code'] != 0:
            raise RuntimeError(f"Failed to get release history: {result['stderr']}")

        try:
            return json.loads(result['stdout']) if result['stdout'].strip() else []
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Helm history: {e}")
            return []

    def get_values(
        self,
        cluster_id: int,
        release_name: str,
        namespace: str | None = None,
        all_values: bool = False
    ) -> dict[str, Any]:
        """
        Get values for a release.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Name of the release
            namespace: Namespace of the release
            all_values: Get all values (computed + user-provided)

        Returns:
            Values dictionary
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("namespace", namespace)

        cluster = self.get_cluster(cluster_id)

        command = ['get', 'values', release_name, '--output', 'json']

        if all_values:
            command.append('--all')

        result = self._run_helm_command(cluster, command, namespace)

        if result['exit_code'] != 0:
            raise RuntimeError(f"Failed to get release values: {result['stderr']}")

        try:
            return json.loads(result['stdout']) if result['stdout'].strip() else {}
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse release values: {e}")
            return {}

    def get_manifest(
        self,
        cluster_id: int,
        release_name: str,
        namespace: str | None = None,
        revision: int | None = None
    ) -> str:
        """
        Get the Kubernetes manifest for a release.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Name of the release
            namespace: Namespace of the release
            revision: Specific revision (default: current)

        Returns:
            YAML manifest as string
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("namespace", namespace)

        cluster = self.get_cluster(cluster_id)

        command = ['get', 'manifest', release_name]

        if revision:
            command.extend(['--revision', str(revision)])

        result = self._run_helm_command(cluster, command, namespace)

        if result['exit_code'] != 0:
            raise RuntimeError(f"Failed to get release manifest: {result['stderr']}")

        return result['stdout']

    def test_release(
        self,
        cluster_id: int,
        release_name: str,
        namespace: str | None = None,
        timeout: str = "5m"
    ) -> dict[str, Any]:
        """
        Run tests for a release.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Name of the release
            namespace: Namespace of the release
            timeout: Timeout for tests

        Returns:
            Test result
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("namespace", namespace)
        validate_helm_timeout(timeout)

        cluster = self.get_cluster(cluster_id)

        command = ['test', release_name, '--timeout', timeout]

        result = self._run_helm_command(cluster, command, namespace)

        return {
            'success': result['exit_code'] == 0,
            'exit_code': result['exit_code'],
            'output': result['stdout'],
            'error': result['stderr']
        }

    # ============================================================
    # Revision Comparison
    # ============================================================

    def compare_revisions(
        self,
        cluster_id: int,
        release_name: str,
        revision1: int,
        revision2: int,
        namespace: str = "default"
    ) -> dict[str, Any]:
        """
        Compare two revisions of a release.

        Args:
            cluster_id: Kubernetes cluster ID
            release_name: Release name
            revision1: First revision number
            revision2: Second revision number
            namespace: Kubernetes namespace

        Returns:
            Comparison data with values and manifests for both revisions
        """
        self._validate_arg("release_name", release_name)
        self._validate_arg("namespace", namespace)

        cluster = self.get_cluster(cluster_id)

        with kubeconfig_for_cluster(cluster, self.db) as kubeconfig_path:
            # Get values for both revisions
            values1 = self._get_revision_values(
                release_name, revision1, namespace, kubeconfig_path
            )
            values2 = self._get_revision_values(
                release_name, revision2, namespace, kubeconfig_path
            )

            # Get manifests for both revisions
            manifest1 = self._get_revision_manifest(
                release_name, revision1, namespace, kubeconfig_path
            )
            manifest2 = self._get_revision_manifest(
                release_name, revision2, namespace, kubeconfig_path
            )

            return {
                "success": True,
                "revision1": {
                    "number": revision1,
                    "values": values1,
                    "manifest": manifest1
                },
                "revision2": {
                    "number": revision2,
                    "values": values2,
                    "manifest": manifest2
                }
            }

    def _get_revision_values(
        self,
        release_name: str,
        revision: int,
        namespace: str,
        kubeconfig_path: str
    ) -> dict[str, Any]:
        """Get values for a specific revision."""
        result = subprocess.run(
            [
                'helm', 'get', 'values', release_name,
                '--revision', str(revision),
                '--namespace', namespace,
                '--output', 'json',
                '--kubeconfig', kubeconfig_path
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.warning(f"Failed to get values for revision {revision}: {result.stderr}")
            return {}

        try:
            return json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            return {}

    def _get_revision_manifest(
        self,
        release_name: str,
        revision: int,
        namespace: str,
        kubeconfig_path: str
    ) -> str:
        """Get manifest for a specific revision."""
        result = subprocess.run(
            [
                'helm', 'get', 'manifest', release_name,
                '--revision', str(revision),
                '--namespace', namespace,
                '--kubeconfig', kubeconfig_path
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.warning(f"Failed to get manifest for revision {revision}: {result.stderr}")
            return ""

        return result.stdout
