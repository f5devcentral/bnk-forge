"""
Unit tests for backend/utils/provider_config.py.

Covers:
- generate_aws_eks_providers: exec auth used, no aws_eks_cluster_auth token
- generate_forge_kubeconfig_local (AWS): exec auth in kubeconfig, no token field
- Non-AWS branches unchanged (azure, gcp, ibm, generic)
- generate_helm_kubernetes_block: token and cert-based paths still work
"""

import pytest

from utils.provider_config import (
    generate_aws_eks_providers,
    generate_forge_kubeconfig_local,
    generate_helm_kubernetes_block,
)


class TestGenerateAwsEksProviders:
    """AWS EKS provider generation — must use exec auth, not static token."""

    def test_kubernetes_provider_uses_exec_auth(self):
        """kubernetes provider block must contain exec block with aws eks get-token."""
        provider_blocks, data_sources = generate_aws_eks_providers(
            cluster_name="my-cluster",
            aws_region="us-east-1",
            required_providers=["kubernetes"],
        )
        combined = "\n".join(provider_blocks)
        assert "exec" in combined
        assert "aws" in combined
        assert "eks" in combined
        assert "get-token" in combined
        assert "--cluster-name" in combined
        assert "my-cluster" in combined
        assert "--region" in combined
        assert "us-east-1" in combined

    def test_kubernetes_provider_no_static_token(self):
        """kubernetes provider must NOT reference data.aws_eks_cluster_auth.cluster.token."""
        provider_blocks, data_sources = generate_aws_eks_providers(
            cluster_name="my-cluster",
            aws_region="us-east-1",
            required_providers=["kubernetes"],
        )
        combined = "\n".join(provider_blocks + data_sources)
        assert "aws_eks_cluster_auth" not in combined
        assert "cluster.token" not in combined

    def test_helm_provider_uses_exec_auth(self):
        """helm provider block must contain exec block with aws eks get-token."""
        provider_blocks, data_sources = generate_aws_eks_providers(
            cluster_name="my-cluster",
            aws_region="ap-southeast-2",
            required_providers=["helm"],
        )
        combined = "\n".join(provider_blocks)
        assert "exec" in combined
        assert "get-token" in combined
        assert "my-cluster" in combined
        assert "ap-southeast-2" in combined

    def test_helm_provider_no_static_token(self):
        """helm provider must NOT reference aws_eks_cluster_auth token."""
        provider_blocks, data_sources = generate_aws_eks_providers(
            cluster_name="my-cluster",
            aws_region="us-east-1",
            required_providers=["helm"],
        )
        combined = "\n".join(provider_blocks + data_sources)
        assert "aws_eks_cluster_auth" not in combined
        assert "cluster.token" not in combined

    def test_helm_provider_helm3_syntax(self):
        """helm provider with assignment syntax still uses exec auth."""
        provider_blocks, data_sources = generate_aws_eks_providers(
            cluster_name="my-cluster",
            aws_region="us-east-1",
            required_providers=["helm"],
            helm_use_assignment_syntax=True,
        )
        combined = "\n".join(provider_blocks)
        assert "kubernetes =" in combined
        assert "exec" in combined
        assert "get-token" in combined

    def test_data_sources_contain_only_eks_cluster(self):
        """data_sources must have aws_eks_cluster but NOT aws_eks_cluster_auth."""
        provider_blocks, data_sources = generate_aws_eks_providers(
            cluster_name="my-cluster",
            aws_region="us-east-1",
            required_providers=["kubernetes", "helm"],
        )
        combined_ds = "\n".join(data_sources)
        assert "aws_eks_cluster" in combined_ds
        assert "aws_eks_cluster_auth" not in combined_ds

    def test_both_kubernetes_and_helm_providers(self):
        """Both providers generated with exec auth when both requested."""
        provider_blocks, data_sources = generate_aws_eks_providers(
            cluster_name="test-cluster",
            aws_region="eu-west-1",
            required_providers=["kubernetes", "helm"],
        )
        combined = "\n".join(provider_blocks)
        # Should have two exec blocks (one for kubernetes, one for helm)
        assert combined.count("get-token") >= 2

    def test_no_providers_no_provider_blocks(self):
        """Empty required_providers still emits aws provider and data source."""
        provider_blocks, data_sources = generate_aws_eks_providers(
            cluster_name="my-cluster",
            aws_region="us-east-1",
            required_providers=[],
        )
        # aws provider always emitted; no kubernetes/helm blocks
        aws_provider = "\n".join(provider_blocks)
        assert 'provider "aws"' in aws_provider
        assert 'provider "kubernetes"' not in aws_provider
        assert 'provider "helm"' not in aws_provider


class TestGenerateForgeKubeconfigLocalAws:
    """forge_kubeconfig local for AWS must use exec auth, no token."""

    def test_aws_kubeconfig_uses_exec_auth(self):
        """AWS forge_kubeconfig must contain exec section with aws eks get-token."""
        result = generate_forge_kubeconfig_local(
            cloud_provider="aws",
            variables={"cluster_name": "my-cluster", "region": "us-east-1"},
        )
        assert "exec" in result
        assert "get-token" in result
        assert "my-cluster" in result
        assert "us-east-1" in result

    def test_aws_kubeconfig_no_token_field(self):
        """AWS forge_kubeconfig must NOT embed aws_eks_cluster_auth token."""
        result = generate_forge_kubeconfig_local(
            cloud_provider="aws",
            variables={"cluster_name": "my-cluster", "region": "us-east-1"},
        )
        assert "aws_eks_cluster_auth" not in result
        # The user entry must not contain a bare 'token' field
        # (exec replaces it)
        assert "token = data.aws_eks_cluster_auth" not in result

    def test_aws_kubeconfig_uses_eks_cluster_name_var(self):
        """cluster_name fallback to eks_cluster_name variable."""
        result = generate_forge_kubeconfig_local(
            cloud_provider="aws",
            variables={"eks_cluster_name": "eks-cluster", "region": "eu-west-1"},
        )
        assert "eks-cluster" in result
        assert "eu-west-1" in result

    def test_aws_kubeconfig_region_from_project(self):
        """Region resolved from project when not in variables."""

        class FakeProject:
            region = "ap-northeast-1"

        result = generate_forge_kubeconfig_local(
            cloud_provider="aws",
            variables={"cluster_name": "my-cluster"},
            project=FakeProject(),
        )
        assert "ap-northeast-1" in result

    def test_aws_kubeconfig_references_eks_cluster_endpoint(self):
        """kubeconfig still references data.aws_eks_cluster.cluster.endpoint for server."""
        result = generate_forge_kubeconfig_local(
            cloud_provider="aws",
            variables={"cluster_name": "my-cluster", "region": "us-east-1"},
        )
        assert "aws_eks_cluster.cluster.endpoint" in result

    def test_aws_kubeconfig_fallback_uses_data_source_not_undeclared_var(self):
        """When cluster_name is absent from variables, the exec arg must reference
        the co-located data source (already required by the kubeconfig local for
        endpoint/CA), never an undeclared ${var.cluster_name} — which breaks every
        in-cluster catalog module with 'Reference to undeclared input variable'."""
        result = generate_forge_kubeconfig_local(
            cloud_provider="aws",
            variables={"region": "ap-southeast-2"},
        )
        assert "${var.cluster_name}" not in result
        assert "data.aws_eks_cluster.cluster.name" in result


class TestGenerateForgeKubeconfigLocalNonAws:
    """Non-AWS forge_kubeconfig branches must be unchanged."""

    def test_azure_kubeconfig_unchanged(self):
        """Azure kubeconfig uses kube_config_raw (unchanged)."""
        result = generate_forge_kubeconfig_local(cloud_provider="azure")
        assert "kube_config_raw" in result
        assert "azurerm_kubernetes_cluster" in result

    def test_gcp_kubeconfig_unchanged(self):
        """GCP kubeconfig uses google_client_config access_token (unchanged)."""
        result = generate_forge_kubeconfig_local(cloud_provider="gcp")
        assert "access_token" in result
        assert "google_container_cluster" in result

    def test_ibm_kubeconfig_unchanged(self):
        """IBM kubeconfig uses ibm_container_cluster_config token (unchanged)."""
        result = generate_forge_kubeconfig_local(cloud_provider="ibm")
        assert "ibm_container_cluster_config" in result

    def test_generic_kubeconfig_uses_file(self):
        """Generic/on-prem kubeconfig reads from file path."""
        result = generate_forge_kubeconfig_local(
            cloud_provider="",
            variables={"kubeconfig_path": "/tmp/kube.yaml"},
        )
        assert "file(" in result
        assert "/tmp/kube.yaml" in result


class TestGenerateHelmKubernetesBlock:
    """Existing token/cert paths in generate_helm_kubernetes_block must still work (non-AWS)."""

    def test_token_path_helm2_syntax(self):
        """Token-based auth (GKE/IBM) still works with Helm 2.x syntax."""
        result = generate_helm_kubernetes_block(
            use_assignment_syntax=False,
            host="data.google_container_cluster.cluster.endpoint",
            cluster_ca_certificate="base64decode(...)",
            token="data.google_client_config.default.access_token",
        )
        assert 'provider "helm"' in result
        assert "token" in result
        assert "kubernetes {" in result

    def test_token_path_helm3_syntax(self):
        """Token-based auth still works with Helm 3.x assignment syntax."""
        result = generate_helm_kubernetes_block(
            use_assignment_syntax=True,
            host="data.google_container_cluster.cluster.endpoint",
            cluster_ca_certificate="base64decode(...)",
            token="data.google_client_config.default.access_token",
        )
        assert "kubernetes =" in result
        assert "token" in result

    def test_cert_based_auth(self):
        """Certificate-based auth (AKS) works correctly."""
        result = generate_helm_kubernetes_block(
            use_assignment_syntax=False,
            host="data.azurerm_kubernetes_cluster.cluster.kube_config[0].host",
            cluster_ca_certificate="base64decode(...ca...)",
            client_certificate="base64decode(...cert...)",
            client_key="base64decode(...key...)",
        )
        assert "client_certificate" in result
        assert "client_key" in result

    def test_config_path_auth(self):
        """kubeconfig file path auth works correctly."""
        result = generate_helm_kubernetes_block(
            use_assignment_syntax=False,
            host=None,
            cluster_ca_certificate=None,
            config_path="/app/kubeconfig",
        )
        assert "config_path" in result
        assert "/app/kubeconfig" in result

    def test_missing_auth_raises(self):
        """Missing all auth methods raises ValueError."""
        with pytest.raises(ValueError, match="Must provide"):
            generate_helm_kubernetes_block(
                use_assignment_syntax=False,
                host="host",
                cluster_ca_certificate="ca",
            )


class TestGenerateHelmKubernetesExecBlock:
    """_generate_helm_kubernetes_exec_block: assignment vs block syntax for exec."""

    def test_assignment_syntax_uses_exec_equals(self):
        """Helm 3.x assignment branch must emit 'exec = {' not 'exec {'."""
        from utils.provider_config import _generate_helm_kubernetes_exec_block

        result = _generate_helm_kubernetes_exec_block(
            use_assignment_syntax=True,
            cluster_name="my-cluster",
            aws_region="us-east-1",
        )
        # Outer kubernetes block must use assignment syntax
        assert "kubernetes = {" in result
        # Inner exec block must also use assignment syntax (not bare block)
        assert "exec = {" in result
        assert "exec {" not in result

    def test_block_syntax_uses_exec_block(self):
        """Helm 2.x block branch must emit 'exec {' not 'exec = {'."""
        from utils.provider_config import _generate_helm_kubernetes_exec_block

        result = _generate_helm_kubernetes_exec_block(
            use_assignment_syntax=False,
            cluster_name="my-cluster",
            aws_region="us-east-1",
        )
        assert "kubernetes {" in result
        assert "exec {" in result
        assert "exec = {" not in result

    def test_assignment_syntax_correct_attrs(self):
        """Assignment-syntax exec block includes correct attributes."""
        from utils.provider_config import _generate_helm_kubernetes_exec_block

        result = _generate_helm_kubernetes_exec_block(
            use_assignment_syntax=True,
            cluster_name="test-eks",
            aws_region="ap-southeast-2",
        )
        assert "api_version" in result
        assert "client.authentication.k8s.io/v1beta1" in result
        assert '"aws"' in result
        assert "get-token" in result
        assert "test-eks" in result
        assert "ap-southeast-2" in result
