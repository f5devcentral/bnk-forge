import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from models import KubernetesCluster

# Using PYTHONPATH=backend, so we can import directly
from services.helm_service import HelmService


def test_namespace_injection_blocked_in_list_releases():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.list_releases(1, namespace="-malicious")

def test_namespace_injection_blocked_in_get_release():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.get_release(1, "release", namespace="-malicious")

def test_namespace_injection_blocked_in_install_chart():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.install_chart(1, "release", "chart", namespace="-malicious")

def test_namespace_injection_blocked_in_upgrade_release():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.upgrade_release(1, "release", namespace="-malicious")

def test_namespace_injection_blocked_in_rollback_release():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.rollback_release(1, "release", namespace="-malicious")

def test_namespace_injection_blocked_in_uninstall_release():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.uninstall_release(1, "release", namespace="-malicious")

def test_namespace_injection_blocked_in_get_history():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.get_history(1, "release", namespace="-malicious")

def test_namespace_injection_blocked_in_get_values():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.get_values(1, "release", namespace="-malicious")

def test_namespace_injection_blocked_in_get_manifest():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.get_manifest(1, "release", namespace="-malicious")

def test_namespace_injection_blocked_in_test_release():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.test_release(1, "release", namespace="-malicious")

def test_namespace_injection_blocked_in_compare_revisions():
    mock_db = MagicMock()
    service = HelmService(mock_db)

    with pytest.raises(ValueError, match="Invalid namespace: cannot start with '-'"):
        service.compare_revisions(1, "release", 1, 2, namespace="-malicious")
