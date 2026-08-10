import os
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if backend_path not in sys.path:
    sys.path.append(backend_path)

from models import KubernetesCluster
from services.helm_service import HelmService


def _mock_kubeconfig_ctx():
    """Helper: create a mock kubeconfig_for_cluster context manager."""
    @contextmanager
    def fake_kubeconfig(*args, **kwargs):
        yield "/tmp/mock_kubeconfig"
    return fake_kubeconfig


def _mock_prepare_kubeconfig():
    """Helper: create a mock prepare_kubeconfig that returns a file-like object."""
    mock_file = MagicMock()
    mock_file.name = "/tmp/mock_kubeconfig"
    return mock_file


def test_install_chart_rejects_malicious_arg():
    """
    Test that install_chart rejects argument injection (after fix).
    Validation happens before kubeconfig is prepared.
    """
    mock_db = MagicMock()
    service = HelmService(mock_db)

    # Mock cluster
    mock_cluster = MagicMock(spec=KubernetesCluster)
    mock_cluster.id = 1
    mock_cluster.name = "test-cluster"
    mock_cluster.kubeconfig_encrypted = "encrypted_data"

    mock_db.query.return_value.filter.return_value.first.return_value = mock_cluster

    # Malicious input
    malicious_release = "-f"
    chart = "nginx"

    with patch("subprocess.run") as mock_run:
        # Should raise ValueError
        with pytest.raises(ValueError, match="Invalid release_name: cannot start with '-'"):
            service.install_chart(1, malicious_release, chart)

        # subprocess.run should NOT be called
        assert not mock_run.called

def test_install_chart_rejects_malicious_chart():
    """
    Test that install_chart rejects argument injection via chart name
    """
    mock_db = MagicMock()
    service = HelmService(mock_db)

    # Mock cluster setup
    mock_cluster = MagicMock(spec=KubernetesCluster)
    mock_cluster.id = 1
    mock_cluster.kubeconfig_encrypted = "encrypted_data"
    mock_db.query.return_value.filter.return_value.first.return_value = mock_cluster

    release_name = "my-release"
    malicious_chart = "--dry-run"

    with patch("subprocess.run") as mock_run:
        with pytest.raises(ValueError, match="Invalid chart: cannot start with '-'"):
            service.install_chart(1, release_name, malicious_chart)

        assert not mock_run.called

def test_install_chart_valid_input():
    """
    Test that install_chart works with valid input.
    Patches _prepare_kubeconfig to avoid real cluster access.
    """
    mock_db = MagicMock()
    service = HelmService(mock_db)

    # Mock cluster setup
    mock_cluster = MagicMock(spec=KubernetesCluster)
    mock_cluster.id = 1
    mock_cluster.kubeconfig_encrypted = "encrypted_data"
    mock_cluster.ssh_tunnel_enabled = False
    mock_db.query.return_value.filter.return_value.first.return_value = mock_cluster

    release_name = "valid-release"
    chart = "nginx"

    with patch("subprocess.run") as mock_run, \
         patch.object(HelmService, "_prepare_kubeconfig", return_value=_mock_prepare_kubeconfig()), \
         patch("os.unlink"):

        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

        service.install_chart(1, release_name, chart)

        assert mock_run.called

def test_compare_revisions_blocks_injection():
    """
    Test that compare_revisions BLOCKS argument injection.
    We expect a ValueError.
    """
    mock_db = MagicMock()
    service = HelmService(mock_db)

    # Mock cluster
    mock_cluster = MagicMock(spec=KubernetesCluster)
    mock_cluster.id = 1
    mock_cluster.name = "test-cluster"
    mock_cluster.kubeconfig_encrypted = "encrypted_data"

    mock_db.query.return_value.filter.return_value.first.return_value = mock_cluster

    # Malicious input
    malicious_release = "--help"

    with patch("subprocess.run") as mock_run:
        # Should raise ValueError (before kubeconfig is even prepared)
        with pytest.raises(ValueError, match="Invalid release_name: cannot start with '-'"):
            service.compare_revisions(1, malicious_release, 1, 2)

        # Verify subprocess was NOT called
        assert not mock_run.called

def test_compare_revisions_blocks_namespace_injection():
    """
    Test that compare_revisions BLOCKS injection via namespace.
    """
    mock_db = MagicMock()
    service = HelmService(mock_db)

    # Mock cluster
    mock_cluster = MagicMock(spec=KubernetesCluster)
    mock_cluster.id = 1
    mock_cluster.name = "test-cluster"
    mock_cluster.kubeconfig_encrypted = "encrypted_data"

    mock_db.query.return_value.filter.return_value.first.return_value = mock_cluster

    release_name = "valid-release"
    malicious_namespace = "--all-namespaces"

    with patch("subprocess.run") as mock_run:
        # Should raise ValueError (before kubeconfig is even prepared)
        with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
            service.compare_revisions(1, release_name, 1, 2, namespace=malicious_namespace)

        assert not mock_run.called
