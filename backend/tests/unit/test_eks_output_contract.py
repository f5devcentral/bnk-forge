"""
Unit tests for services.eks_service.matches_eks_output_contract.

Guards the pure-function output-contract detection helper that enables
output-contract-driven EKS auto-registration (D-019 "dynamic-by-default").
"""

import pytest

from services.eks_service import matches_eks_output_contract

# ---------------------------------------------------------------------------
# True cases
# ---------------------------------------------------------------------------

class TestMatchesEksOutputContractTrue:
    """Outputs that satisfy the EKS registration contract."""

    def test_returns_true_for_complete_contract(self):
        """All four required keys present with non-empty values → True."""
        outputs = {
            "cluster_name": "my-eks-cluster",
            "cluster_endpoint": "https://ABCDEF.gr7.ap-southeast-2.eks.amazonaws.com",
            "cluster_certificate_authority_data": "LS0tLS1CRUdJTi...",
            "region": "ap-southeast-2",
        }
        assert matches_eks_output_contract(outputs) is True

    def test_returns_true_with_extra_keys(self):
        """Extra keys beyond the contract (e.g. kubeconfig, arn) do not disqualify."""
        outputs = {
            "cluster_name": "my-cluster",
            "cluster_endpoint": "https://endpoint.eks.amazonaws.com",
            "cluster_certificate_authority_data": "base64data",
            "region": "us-east-1",
            "eks_cluster_arn": "arn:aws:eks:us-east-1:123456789012:cluster/my-cluster",
            "kubeconfig": "yaml-data",
            "cloud_az_subnet_mappings": {},
        }
        assert matches_eks_output_contract(outputs) is True

    def test_module_name_is_irrelevant(self):
        """The contract check is purely output-based — the module library name does not matter."""
        # This mirrors the aws_eks_cluster_create module name that caused the bug
        outputs = {
            "cluster_name": "aws-eks-cluster-create-output",
            "cluster_endpoint": "https://endpoint.example.com",
            "cluster_certificate_authority_data": "ca-data",
            "region": "ap-southeast-2",
        }
        assert matches_eks_output_contract(outputs) is True


# ---------------------------------------------------------------------------
# False cases — missing one key at a time
# ---------------------------------------------------------------------------

class TestMatchesEksOutputContractMissingKey:
    """Absence of any single required key → False."""

    @pytest.mark.parametrize("missing_key", [
        "cluster_name",
        "cluster_endpoint",
        "cluster_certificate_authority_data",
        "region",
    ])
    def test_returns_false_when_required_key_missing(self, missing_key):
        base = {
            "cluster_name": "my-cluster",
            "cluster_endpoint": "https://endpoint.eks.amazonaws.com",
            "cluster_certificate_authority_data": "ca-data",
            "region": "us-east-1",
        }
        del base[missing_key]
        assert matches_eks_output_contract(base) is False

    @pytest.mark.parametrize("empty_value", [None, "", 0, False])
    def test_returns_false_when_required_key_is_empty_or_falsy(self, empty_value):
        """An empty / falsy value for any required key is not sufficient."""
        outputs = {
            "cluster_name": "my-cluster",
            "cluster_endpoint": "https://endpoint.eks.amazonaws.com",
            "cluster_certificate_authority_data": "ca-data",
            "region": empty_value,
        }
        assert matches_eks_output_contract(outputs) is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestMatchesEksOutputContractEdgeCases:
    """Defensive handling of None, empty dict, and non-dict inputs."""

    def test_returns_false_for_none(self):
        assert matches_eks_output_contract(None) is False

    def test_returns_false_for_empty_dict(self):
        assert matches_eks_output_contract({}) is False

    def test_returns_false_for_unrelated_outputs(self):
        """A module with unrelated outputs (e.g. a VPC module) → False."""
        outputs = {
            "vpc_id": "vpc-12345",
            "subnet_ids": ["subnet-a", "subnet-b"],
            "security_group_id": "sg-99999",
        }
        assert matches_eks_output_contract(outputs) is False
