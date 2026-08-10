"""
Tests for services.helm_service — Helm release lifecycle management.

Covers:
  - Install chart (success, duplicate release, missing chart)
  - Upgrade release (success, failure)
  - Rollback release (success, failure)
  - Uninstall release (success, failure)
  - List releases (JSON parse, empty list)
  - Get release details
  - Get history and values
  - Input validation (CLI injection prevention, timeout validation)
  - Subprocess timeout handling
  - Kubeconfig cleanup
"""

import json
import subprocess
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_helm_service(db):
    """Create a HelmService with a test DB session."""
    from services.helm_service import HelmService
    return HelmService(db)


def _make_mock_cluster(cluster_id=1, name="test-cluster"):
    """Create a mock KubernetesCluster."""
    cluster = MagicMock()
    cluster.id = cluster_id
    cluster.name = name
    cluster.kubeconfig_encrypted = "encrypted_kubeconfig_data"
    cluster.cluster_type = "generic"
    cluster.status = "active"
    return cluster


def _mock_run_helm_success(stdout="", stderr=""):
    """Return a mock _run_helm_command result dict for success."""
    return {"exit_code": 0, "stdout": stdout, "stderr": stderr}


def _mock_run_helm_failure(stderr="Error occurred"):
    """Return a mock _run_helm_command result dict for failure."""
    return {"exit_code": 1, "stdout": "", "stderr": stderr}


class TestInputValidation:
    """Test CLI injection prevention and timeout validation."""

    def test_validate_arg_rejects_flag_injection(self, db):
        """_validate_arg raises ValueError for values starting with '-'."""
        svc = _make_helm_service(db)

        with pytest.raises(ValueError):
            svc._validate_arg("release_name", "--set=evil=true")

    def test_validate_arg_accepts_valid_values(self, db):
        """_validate_arg accepts normal string values."""
        svc = _make_helm_service(db)

        # Should not raise
        svc._validate_arg("release_name", "my-release")
        svc._validate_arg("namespace", "kube-system")
        svc._validate_arg("chart", "bitnami/nginx")

    def test_validate_arg_accepts_none(self, db):
        """_validate_arg silently accepts None values."""
        svc = _make_helm_service(db)
        svc._validate_arg("namespace", None)  # should not raise


class TestInstallChart:
    """Test the install_chart method."""

    def test_install_success(self, db, make_k8s_cluster):
        """install_chart returns parsed result on success."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        install_json = json.dumps({
            "name": "nginx",
            "info": {"status": "deployed"},
            "namespace": "default",
            "version": 1,
        })

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(stdout=install_json)):
            result = svc.install_chart(
                cluster_id=cluster.id,
                release_name="nginx",
                chart="bitnami/nginx",
                namespace="default",
            )

        assert result["name"] == "nginx"
        assert result["info"]["status"] == "deployed"

    def test_install_duplicate_release_raises_value_error(self, db, make_k8s_cluster):
        """install_chart raises ValueError when release name is already in use."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_failure(
                 stderr="cannot re-use a name that is still in use"
             )):
            with pytest.raises(ValueError, match="re-use"):
                svc.install_chart(
                    cluster_id=cluster.id,
                    release_name="existing",
                    chart="bitnami/nginx",
                )

    def test_install_chart_not_found_raises_value_error(self, db, make_k8s_cluster):
        """install_chart raises ValueError for chart not found."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_failure(
                 stderr="Error: chart not found: fake/chart"
             )):
            with pytest.raises(ValueError, match="not found"):
                svc.install_chart(
                    cluster_id=cluster.id,
                    release_name="test",
                    chart="fake/chart",
                )

    def test_install_generic_error_raises_runtime_error(self, db, make_k8s_cluster):
        """install_chart raises RuntimeError for unexpected failures."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_failure(
                 stderr="Error: completely unexpected failure"
             )):
            with pytest.raises(RuntimeError):
                svc.install_chart(
                    cluster_id=cluster.id,
                    release_name="test",
                    chart="bitnami/nginx",
                )

    def test_install_with_values(self, db, make_k8s_cluster):
        """install_chart serializes values to a temp file."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        install_json = json.dumps({"name": "nginx", "namespace": "default"})

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(stdout=install_json)):
            result = svc.install_chart(
                cluster_id=cluster.id,
                release_name="nginx",
                chart="bitnami/nginx",
                values={"replicaCount": 3, "image": {"tag": "latest"}},
            )

        assert result is not None


class TestUpgradeRelease:
    """Test the upgrade_release method."""

    def test_upgrade_success(self, db, make_k8s_cluster):
        """upgrade_release returns parsed result on success."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        upgrade_json = json.dumps({
            "name": "nginx",
            "info": {"status": "deployed"},
            "version": 2,
        })

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(stdout=upgrade_json)):
            result = svc.upgrade_release(
                cluster_id=cluster.id,
                release_name="nginx",
                chart="bitnami/nginx",
            )

        assert result is not None

    def test_upgrade_failure_raises(self, db, make_k8s_cluster):
        """upgrade_release raises RuntimeError on failure."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_failure(
                 stderr="Error: upgrade failed"
             )):
            with pytest.raises(RuntimeError):
                svc.upgrade_release(
                    cluster_id=cluster.id,
                    release_name="nginx",
                    chart="bitnami/nginx",
                )


class TestRollbackRelease:
    """Test the rollback_release method."""

    def test_rollback_success(self, db, make_k8s_cluster):
        """rollback_release returns success dict."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(
                 stdout="Rollback was a success"
             )):
            result = svc.rollback_release(
                cluster_id=cluster.id,
                release_name="nginx",
                revision=1,
            )

        assert result["success"] is True

    def test_rollback_failure_raises(self, db, make_k8s_cluster):
        """rollback_release raises RuntimeError on failure."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_failure(
                 stderr="Error: rollback failed"
             )):
            with pytest.raises(RuntimeError):
                svc.rollback_release(
                    cluster_id=cluster.id,
                    release_name="nginx",
                    revision=1,
                )


class TestUninstallRelease:
    """Test the uninstall_release method."""

    def test_uninstall_success(self, db, make_k8s_cluster):
        """uninstall_release returns success dict."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(
                 stdout="release \"nginx\" uninstalled"
             )):
            result = svc.uninstall_release(
                cluster_id=cluster.id,
                release_name="nginx",
            )

        assert result["success"] is True

    def test_uninstall_failure_raises(self, db, make_k8s_cluster):
        """uninstall_release raises RuntimeError on failure."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_failure(
                 stderr="Error: uninstall failed"
             )):
            with pytest.raises(RuntimeError):
                svc.uninstall_release(
                    cluster_id=cluster.id,
                    release_name="nginx",
                )


class TestListReleases:
    """Test listing Helm releases."""

    def test_list_releases_parses_json(self, db, make_k8s_cluster):
        """list_releases parses JSON output from helm list."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        releases_json = json.dumps([
            {"name": "nginx", "namespace": "default", "revision": "1", "status": "deployed"},
            {"name": "redis", "namespace": "cache", "revision": "3", "status": "deployed"},
        ])

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(stdout=releases_json)):
            result = svc.list_releases(cluster_id=cluster.id)

        assert len(result) == 2
        assert result[0]["name"] == "nginx"
        assert result[1]["name"] == "redis"

    def test_list_releases_handles_empty(self, db, make_k8s_cluster):
        """list_releases returns empty list when no releases."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(stdout="[]")):
            result = svc.list_releases(cluster_id=cluster.id)

        assert result == []

    def test_list_releases_handles_invalid_json(self, db, make_k8s_cluster):
        """list_releases returns empty list on JSON parse failure."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(stdout="not json")):
            result = svc.list_releases(cluster_id=cluster.id)

        assert result == []


class TestGetRelease:
    """Test getting a single release."""

    def test_get_release_success(self, db, make_k8s_cluster):
        """get_release returns parsed release details."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        release_json = json.dumps({
            "name": "nginx",
            "info": {"status": "deployed", "first_deployed": "2026-01-01"},
            "namespace": "default",
        })

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(stdout=release_json)):
            result = svc.get_release(
                cluster_id=cluster.id,
                release_name="nginx",
            )

        assert result["name"] == "nginx"


class TestGetReleaseErrorClassification:
    """H3: get_release must distinguish a genuine not-found from transient errors.

    Transient failures (API-server unreachable, auth expiry, helm timeout
    exit_code=124) must NOT be reported as not-found — a probe treating them as
    "release absent" would trigger a spurious reinstall of a shared control plane.
    """

    def test_not_found_raises_release_not_found_error(self, db, make_k8s_cluster):
        from core.errors import ReleaseNotFoundError
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()
        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value={
                 "exit_code": 1, "stdout": "", "stderr": "Error: release: not found",
             }):
            with pytest.raises(ReleaseNotFoundError):
                svc.get_release(cluster_id=cluster.id, release_name="missing")

    def test_transient_error_does_not_report_absent(self, db, make_k8s_cluster):
        """API-server-unreachable style error → RuntimeError, NOT ReleaseNotFoundError."""
        from core.errors import ReleaseNotFoundError
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()
        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value={
                 "exit_code": 1, "stdout": "",
                 "stderr": "Error: Kubernetes cluster unreachable: connection refused",
             }):
            with pytest.raises(RuntimeError) as exc:
                svc.get_release(cluster_id=cluster.id, release_name="eg")
            assert not isinstance(exc.value, ReleaseNotFoundError)

    def test_helm_timeout_exit_124_is_not_not_found(self, db, make_k8s_cluster):
        """exit_code=124 (subprocess timeout) must never map to not-found."""
        from core.errors import ReleaseNotFoundError
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()
        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value={
                 "exit_code": 124, "stdout": "",
                 "stderr": "Command timed out after 360s",
             }):
            with pytest.raises(RuntimeError) as exc:
                svc.get_release(cluster_id=cluster.id, release_name="eg")
            assert not isinstance(exc.value, ReleaseNotFoundError)


class TestContextThreading:
    """M6: cluster.context must be validated and emitted as --kube-context."""

    def test_emits_kube_context_flag(self, db, make_k8s_cluster):
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()
        captured = {}

        def _capture(*args, **kwargs):
            captured["cmd"] = args[0]
            return subprocess.CompletedProcess(args[0], 0, stdout="{}", stderr="")

        with patch.object(svc, "_prepare_kubeconfig") as mock_kc, \
             patch("subprocess.run", side_effect=_capture), \
             patch("os.unlink"):
            mock_kc.return_value.name = "/tmp/kubeconfig-test"
            svc._run_helm_command(cluster, ["status", "eg"], context="prod-ctx")

        cmd = captured["cmd"]
        assert "--kube-context" in cmd
        assert cmd[cmd.index("--kube-context") + 1] == "prod-ctx"

    def test_leading_dash_context_rejected(self, db, make_k8s_cluster):
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()
        with patch.object(svc, "_prepare_kubeconfig"):
            with pytest.raises(ValueError, match="context"):
                svc._run_helm_command(cluster, ["status", "eg"], context="--kubeconfig=/evil")

    def test_none_context_omits_flag(self, db, make_k8s_cluster):
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()
        captured = {}

        def _capture(*args, **kwargs):
            captured["cmd"] = args[0]
            return subprocess.CompletedProcess(args[0], 0, stdout="{}", stderr="")

        with patch.object(svc, "_prepare_kubeconfig") as mock_kc, \
             patch("subprocess.run", side_effect=_capture), \
             patch("os.unlink"):
            mock_kc.return_value.name = "/tmp/kubeconfig-test"
            svc._run_helm_command(cluster, ["status", "eg"], context=None)

        assert "--kube-context" not in captured["cmd"]


class TestSubprocessTimeoutDerivation:
    """M7: subprocess wall-clock timeout must strictly exceed helm --timeout."""

    def test_subprocess_timeout_greater_than_helm_timeout(self):
        from services.helm_service import _parse_helm_timeout_seconds, _subprocess_timeout_for
        helm_seconds = _parse_helm_timeout_seconds("5m")
        assert _subprocess_timeout_for("5m") > helm_seconds

    def test_timeout_parser_handles_compound_units(self):
        from services.helm_service import _parse_helm_timeout_seconds
        assert _parse_helm_timeout_seconds("5m") == 300
        assert _parse_helm_timeout_seconds("300s") == 300
        assert _parse_helm_timeout_seconds("1h30m") == 5400

    def test_no_helm_timeout_uses_default_ceiling(self):
        from services.helm_service import DEFAULT_SUBPROCESS_TIMEOUT_SEC, _subprocess_timeout_for
        assert _subprocess_timeout_for(None) == DEFAULT_SUBPROCESS_TIMEOUT_SEC

    def test_run_helm_command_uses_derived_timeout_for_wait(self, db, make_k8s_cluster):
        """A `--wait --timeout 5m` install must get a subprocess timeout > 300s."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()
        captured = {}

        def _capture(*args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            return subprocess.CompletedProcess(args[0], 0, stdout="{}", stderr="")

        with patch.object(svc, "_prepare_kubeconfig") as mock_kc, \
             patch("subprocess.run", side_effect=_capture), \
             patch("os.unlink"):
            mock_kc.return_value.name = "/tmp/kubeconfig-test"
            svc._run_helm_command(
                cluster,
                ["upgrade", "--install", "eg", "chart", "--wait", "--timeout", "5m"],
            )

        assert captured["timeout"] > 300


class TestGetHistory:
    """Test getting release history."""

    def test_get_history_success(self, db, make_k8s_cluster):
        """get_history returns parsed revision list."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        history_json = json.dumps([
            {"revision": 1, "status": "superseded"},
            {"revision": 2, "status": "deployed"},
        ])

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(stdout=history_json)):
            result = svc.get_history(
                cluster_id=cluster.id,
                release_name="nginx",
            )

        assert len(result) == 2
        assert result[0]["revision"] == 1


class TestGetValues:
    """Test getting release values."""

    def test_get_values_success(self, db, make_k8s_cluster):
        """get_values returns parsed values dict."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        values_json = json.dumps({"replicaCount": 3, "image": {"tag": "latest"}})

        with patch.object(svc, "get_cluster", return_value=cluster), \
             patch.object(svc, "_run_helm_command", return_value=_mock_run_helm_success(stdout=values_json)):
            result = svc.get_values(
                cluster_id=cluster.id,
                release_name="nginx",
            )

        assert result["replicaCount"] == 3


class TestRunHelmCommand:
    """Test the _run_helm_command subprocess wrapper."""

    def test_timeout_returns_exit_124(self, db, make_k8s_cluster):
        """_run_helm_command returns exit 124 on subprocess timeout."""
        svc = _make_helm_service(db)
        cluster = make_k8s_cluster()

        timeout_err = subprocess.TimeoutExpired(cmd=["helm"], timeout=300)

        with patch.object(svc, "_prepare_kubeconfig") as mock_kc, \
             patch("subprocess.run", side_effect=timeout_err), \
             patch("os.unlink"):  # Don't actually delete files
            mock_kc.return_value = MagicMock(name="/tmp/kubeconfig-test")
            mock_kc.return_value.name = "/tmp/kubeconfig-test"

            result = svc._run_helm_command(cluster, ["list"])

        assert result["exit_code"] == 124
        assert "timed out" in result["stderr"].lower()
