import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from models import KubernetesCluster

# Using PYTHONPATH=backend, so we can import directly
from services.helm_service import HelmService


def test_compare_revisions_rejects_malicious_arg():
    """
    Test that compare_revisions rejects argument injection.
    Validation happens before kubeconfig is prepared, so no patching needed.
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

    with patch("subprocess.run") as mock_run:
        # Should raise ValueError now (before kubeconfig is even prepared)
        with pytest.raises(ValueError, match="Invalid release_name: cannot start with '-'"):
             service.compare_revisions(1, malicious_release, 1, 2)

        # subprocess.run should NOT be called
        assert not mock_run.called

def test_compare_revisions_valid_input_calls_subprocess():
    """
    Test that valid input proceeds to subprocess (with kubeconfig_for_cluster working).
    Patches the context manager to provide a fake kubeconfig path.
    """
    mock_db = MagicMock()
    service = HelmService(mock_db)

    mock_cluster = MagicMock(spec=KubernetesCluster)
    mock_cluster.id = 1
    mock_cluster.kubeconfig_encrypted = "encrypted_data"
    mock_cluster.ssh_tunnel_enabled = False
    mock_db.query.return_value.filter.return_value.first.return_value = mock_cluster

    from contextlib import contextmanager

    @contextmanager
    def fake_kubeconfig(*args, **kwargs):
        yield "/tmp/mock_kubeconfig"

    with patch("subprocess.run") as mock_run, \
         patch("services.helm_service.kubeconfig_for_cluster", side_effect=fake_kubeconfig):

        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

        service.compare_revisions(1, "valid-release", 1, 2)

        assert mock_run.called
