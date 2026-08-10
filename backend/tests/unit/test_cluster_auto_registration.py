"""
Unit tests for cluster auto-registration output contract matching.
"""

import pytest

from services.cluster_auto_registration_service import (
    REQUIRED_OUTPUT_KEYS,
    matches_cluster_output_contract,
)


class TestClusterOutputContract:
    """Test output contract matching for auto-registration."""

    def test_matches_with_all_required_keys(self):
        """Outputs with all required keys match contract."""
        outputs = {
            "cluster_name": "bnk-dev",
            "remote_kubeconfig_path": "/tmp/kubeconfig",
            "remote_host": "10.176.11.143",
        }
        assert matches_cluster_output_contract(outputs) is True

    def test_matches_with_extra_keys(self):
        """Outputs with extra keys still match contract."""
        outputs = {
            "cluster_name": "bnk-dev",
            "remote_kubeconfig_path": "/tmp/kubeconfig",
            "remote_host": "10.176.11.143",
            "kubeconfig_path": "./local/path",  # extra
            "other_output": "value",  # extra
        }
        assert matches_cluster_output_contract(outputs) is True

    def test_does_not_match_missing_cluster_name(self):
        """Missing cluster_name doesn't match."""
        outputs = {
            "remote_kubeconfig_path": "/tmp/kubeconfig",
            "remote_host": "10.176.11.143",
        }
        assert matches_cluster_output_contract(outputs) is False

    def test_does_not_match_missing_remote_kubeconfig_path(self):
        """Missing remote_kubeconfig_path doesn't match."""
        outputs = {
            "cluster_name": "bnk-dev",
            "remote_host": "10.176.11.143",
        }
        assert matches_cluster_output_contract(outputs) is False

    def test_does_not_match_missing_remote_host(self):
        """Missing remote_host doesn't match."""
        outputs = {
            "cluster_name": "bnk-dev",
            "remote_kubeconfig_path": "/tmp/kubeconfig",
        }
        assert matches_cluster_output_contract(outputs) is False

    def test_does_not_match_empty_outputs(self):
        """Empty outputs don't match."""
        assert matches_cluster_output_contract({}) is False

    def test_does_not_match_none_outputs(self):
        """None outputs don't match."""
        assert matches_cluster_output_contract(None) is False

    def test_does_not_match_unrelated_outputs(self):
        """Unrelated module outputs don't match."""
        outputs = {
            "vpc_id": "vpc-123",
            "subnet_ids": ["subnet-a", "subnet-b"],
            "security_group_id": "sg-123",
        }
        assert matches_cluster_output_contract(outputs) is False

    def test_required_keys_constant(self):
        """Verify required keys constant is correct."""
        assert REQUIRED_OUTPUT_KEYS == {"cluster_name", "remote_kubeconfig_path", "remote_host"}
