"""
Tests for services.execution.config_writer — TF config file generation.

Covers write_tfvars, write_backend_config, write_encryption_config,
and write_provider_config with mocked filesystem and project models.
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from services.execution.config_writer import (
    _inject_forge_kubeconfig_locals,
    write_backend_config,
    write_encryption_config,
    write_provider_config,
    write_tfvars,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _make_module(
    project_name="test-proj",
    project_id=1,
    module_id=10,
    backend_type="local",
    cloud_provider="aws",
    region="us-west-2",
    encryption_enabled=False,
    encryption_provider="pbkdf2",
    backend_config=None,
    encryption_config=None,
    lib_module=None,
):
    """Build a mock ProjectModule with nested project."""
    project = MagicMock()
    project.id = project_id
    project.name = project_name
    project.backend_type = backend_type
    project.cloud_provider = cloud_provider
    project.region = region
    project.backend_config = backend_config
    project.encryption_config = encryption_config

    # Encryption fields on project (fallback when encryption_config is None)
    project.state_encryption_enabled = encryption_enabled
    project.encryption_provider = encryption_provider
    project.encryption_passphrase_encrypted = None
    project.encryption_kms_key_id = None
    project.encryption_kms_region = None

    module = MagicMock()
    module.id = module_id
    module.project = project
    module.project_id = project_id
    module.path_in_project = "infra/vpc"
    module.library_module = lib_module
    return module


# ── write_tfvars ─────────────────────────────────────────────────────────

class TestWriteTfvars:
    def test_writes_json_file(self, tmp_path):
        variables = {"cidr": "10.0.0.0/16", "name": "my-vpc"}
        result = write_tfvars(str(tmp_path), variables)
        assert result.endswith("terraform.tfvars.json")
        with open(result) as f:
            data = json.load(f)
        assert data == variables

    def test_handles_empty_variables(self, tmp_path):
        result = write_tfvars(str(tmp_path), {})
        with open(result) as f:
            data = json.load(f)
        assert data == {}

    def test_handles_nested_types(self, tmp_path):
        variables = {
            "tags": {"env": "dev", "team": "infra"},
            "count": 3,
            "enabled": True,
            "subnets": ["10.0.1.0/24", "10.0.2.0/24"],
        }
        result = write_tfvars(str(tmp_path), variables)
        with open(result) as f:
            data = json.load(f)
        assert data["tags"]["env"] == "dev"
        assert data["count"] == 3
        assert data["enabled"] is True

    def test_returns_correct_path(self, tmp_path):
        result = write_tfvars(str(tmp_path), {"x": 1})
        assert result == os.path.join(str(tmp_path), "terraform.tfvars.json")

    def test_excludes_env_injected_creds_and_none_values(self, tmp_path):
        """
        Credential keys (aws_access_key_id, aws_secret_access_key, aws_session_token)
        and None-valued keys must NOT appear in terraform.tfvars.json so that
        TF_VAR_* env vars set by get_cloud_credentials_env are not overridden.
        aws_region and other non-credential vars must be preserved.
        """
        variables = {
            "aws_access_key_id": None,
            "aws_secret_access_key": None,
            "aws_session_token": "",
            "aws_region": "ap-southeast-2",
            "eks_cluster_name": "x",
            "some_none": None,
        }
        result = write_tfvars(str(tmp_path), variables)
        with open(result) as f:
            data = json.load(f)

        # Credential keys must be absent (None values + env-injected)
        assert "aws_access_key_id" not in data
        assert "aws_secret_access_key" not in data
        assert "aws_session_token" not in data
        # None-valued non-cred key must also be absent
        assert "some_none" not in data
        # Legitimate vars must be preserved
        assert data["aws_region"] == "ap-southeast-2"
        assert data["eks_cluster_name"] == "x"


# ── write_backend_config ─────────────────────────────────────────────────

class TestWriteBackendConfig:
    @patch("os.makedirs")
    def test_local_backend(self, mock_makedirs, tmp_path):
        module = _make_module(backend_type="local")
        write_backend_config(str(tmp_path), module)
        backend_file = os.path.join(str(tmp_path), "backend_override.tf")
        assert os.path.exists(backend_file)
        content = open(backend_file).read()
        assert 'backend "local"' in content
        assert "terraform.tfstate" in content

    def test_s3_backend_native_locking(self, tmp_path):
        bk = MagicMock()
        bk.s3_state_bucket = "my-bucket"
        bk.s3_state_region = "eu-west-1"
        bk.s3_state_key_prefix = "state/"
        bk.s3_use_native_locking = True
        module = _make_module(backend_type="s3", backend_config=bk)
        write_backend_config(str(tmp_path), module)
        content = open(os.path.join(str(tmp_path), "backend_override.tf")).read()
        assert 'backend "s3"' in content
        assert "my-bucket" in content
        assert "eu-west-1" in content
        assert "use_lockfile" in content
        assert "dynamodb_table" not in content

    def test_s3_backend_dynamodb_locking(self, tmp_path):
        bk = MagicMock()
        bk.s3_state_bucket = "my-bucket"
        bk.s3_state_region = "us-east-1"
        bk.s3_state_key_prefix = ""
        bk.s3_use_native_locking = False
        bk.s3_dynamodb_table = "my-lock-table"
        module = _make_module(backend_type="s3", backend_config=bk)
        write_backend_config(str(tmp_path), module)
        content = open(os.path.join(str(tmp_path), "backend_override.tf")).read()
        assert "dynamodb_table" in content
        assert "my-lock-table" in content

    @patch("services.execution.config_writer.get_default", return_value="ap-south-1")
    def test_s3_backend_region_from_defaults(self, mock_get_default, tmp_path):
        bk = MagicMock()
        bk.s3_state_bucket = "bucket"
        bk.s3_state_region = None
        bk.s3_state_key_prefix = ""
        bk.s3_use_native_locking = True
        module = _make_module(backend_type="s3", region=None, backend_config=bk)
        mock_db = MagicMock()
        write_backend_config(str(tmp_path), module, db=mock_db)
        content = open(os.path.join(str(tmp_path), "backend_override.tf")).read()
        assert "ap-south-1" in content


# ── write_encryption_config ──────────────────────────────────────────────

class TestWriteEncryptionConfig:
    def test_disabled_encryption_no_file(self, tmp_path):
        module = _make_module(encryption_enabled=False)
        write_encryption_config(str(tmp_path), module)
        assert not os.path.exists(os.path.join(str(tmp_path), "encryption.tf"))

    @patch("services.execution.config_writer.decrypt_value", return_value="my-secure-passphrase")
    def test_pbkdf2_encryption(self, mock_decrypt, tmp_path):
        enc = MagicMock()
        enc.state_encryption_enabled = True
        enc.encryption_provider = "pbkdf2"
        enc.encryption_passphrase_encrypted = "encrypted-blob"
        module = _make_module(encryption_enabled=True, encryption_config=enc)
        write_encryption_config(str(tmp_path), module)
        content = open(os.path.join(str(tmp_path), "encryption.tf")).read()
        assert 'key_provider "pbkdf2" "main"' in content
        assert "my-secure-passphrase" in content
        assert "aes_gcm" in content

    def test_aws_kms_missing_key_raises(self, tmp_path):
        enc = MagicMock()
        enc.state_encryption_enabled = True
        enc.encryption_provider = "aws_kms"
        enc.encryption_kms_key_id = None
        module = _make_module(encryption_enabled=True, encryption_config=enc)
        with pytest.raises(ValueError, match="AWS KMS"):
            write_encryption_config(str(tmp_path), module)

    def test_unknown_provider_raises(self, tmp_path):
        enc = MagicMock()
        enc.state_encryption_enabled = True
        enc.encryption_provider = "invalid_provider"
        module = _make_module(encryption_enabled=True, encryption_config=enc)
        with pytest.raises(ValueError, match="Unknown encryption provider"):
            write_encryption_config(str(tmp_path), module)


# ── write_provider_config ────────────────────────────────────────────────

class TestWriteProviderConfig:
    def test_no_lib_module_returns_early(self, tmp_path):
        module = _make_module(lib_module=None)
        module.library_module = None
        write_provider_config(str(tmp_path), module)
        assert not os.path.exists(os.path.join(str(tmp_path), "bnk_forge_providers.tf"))

    def test_no_required_providers_returns_early(self, tmp_path):
        lib = MagicMock()
        lib.inputs_metadata = {"providers": {"required": []}}
        module = _make_module(lib_module=lib)
        write_provider_config(str(tmp_path), module)
        assert not os.path.exists(os.path.join(str(tmp_path), "bnk_forge_providers.tf"))

    def test_kubectl_module_injects_forge_kubeconfig_locals_despite_no_providers(self, tmp_path):
        """Regression: kubectl-only modules (local+null providers, no kubernetes/helm/aws)
        that reference local.forge_kubeconfig must get bnk_forge_locals.tf injected even
        though required_providers is empty and the function would otherwise return early.

        Affected modules: multus, tmm-nads, cert-issuer, cneinstall, etc.
        """
        # Simulate a kubectl-based module workspace: versions.tf declares only local+null,
        # and main.tf uses try(local.forge_kubeconfig, var.forge_kubeconfig_content).
        (tmp_path / "versions.tf").write_text(
            'terraform {\n'
            '  required_providers {\n'
            '    local = { source = "hashicorp/local", version = "~> 2.0" }\n'
            '    null  = { source = "hashicorp/null",  version = "~> 3.0" }\n'
            '  }\n'
            '}\n'
        )
        (tmp_path / "main.tf").write_text(
            'resource "local_file" "kubeconfig" {\n'
            '  content  = try(local.forge_kubeconfig, var.forge_kubeconfig_content)\n'
            '  filename = "/tmp/kubeconfig"\n'
            '}\n'
        )

        lib = MagicMock()
        lib.inputs_metadata = {"providers": {"required": []}}
        project = MagicMock()
        project.id = 1
        project.name = "eks-test"
        project.cloud_provider = "aws"
        project.region = "us-east-1"
        project.k8s_clusters = []
        module = _make_module(cloud_provider="aws", lib_module=lib)
        module.project = project

        with patch("services.execution.config_writer._resolve_project_kubeconfig_path", return_value=None):
            write_provider_config(str(tmp_path), module, variables={"cluster_name": "my-eks"})

        locals_file = tmp_path / "bnk_forge_locals.tf"
        assert locals_file.exists(), (
            "bnk_forge_locals.tf must be written for kubectl-based modules that reference "
            "local.forge_kubeconfig, even when required_providers is empty"
        )
        content = locals_file.read_text()
        assert "forge_kubeconfig" in content

    def test_kubectl_module_without_forge_kubeconfig_ref_does_not_inject_locals(self, tmp_path):
        """Negative case: a module with no required providers AND no forge_kubeconfig reference
        (e.g. a cluster-create module) must NOT get bnk_forge_locals.tf written."""
        (tmp_path / "versions.tf").write_text(
            'terraform {\n'
            '  required_providers {\n'
            '    local = { source = "hashicorp/local", version = "~> 2.0" }\n'
            '  }\n'
            '}\n'
        )
        (tmp_path / "main.tf").write_text(
            'resource "local_file" "something" {\n'
            '  content  = "hello"\n'
            '  filename = "/tmp/out"\n'
            '}\n'
        )

        lib = MagicMock()
        lib.inputs_metadata = {"providers": {"required": []}}
        module = _make_module(cloud_provider="aws", lib_module=lib)

        write_provider_config(str(tmp_path), module, variables={})

        assert not (tmp_path / "bnk_forge_locals.tf").exists(), (
            "bnk_forge_locals.tf must NOT be written when no .tf references forge_kubeconfig"
        )

    def test_kubernetes_module_with_forge_kubeconfig_ref_no_duplicate_aws_eks_cluster(self, tmp_path):
        """Regression: modules requiring kubernetes provider AND referencing local.forge_kubeconfig
        (e.g. eks-cluster-install-bnk-prereqs) must NOT produce duplicate data "aws_eks_cluster"
        blocks across .tf files.

        Prior bug: _inject_forge_kubeconfig_locals ran unconditionally BEFORE the full
        provider-generation path, emitting data "aws_eks_cluster" into bnk_forge_locals.tf.
        Then the full path wrote bnk_forge_providers.tf which ALSO declared data "aws_eks_cluster".
        Result: `tofu init` failed with "Duplicate data aws_eks_cluster configuration".

        Fix: _inject now runs AFTER bnk_forge_providers.tf is written (path 3), so its
        missing-sources check sees the already-declared data source and skips it.
        """
        # Simulate eks-cluster-install-bnk-prereqs workspace:
        # versions.tf requires the kubernetes provider (triggers full-gen path)
        (tmp_path / "versions.tf").write_text(
            'terraform {\n'
            '  required_providers {\n'
            '    kubernetes = {\n'
            '      source  = "hashicorp/kubernetes"\n'
            '      version = "~> 2.0"\n'
            '    }\n'
            '  }\n'
            '}\n'
        )
        # main.tf references local.forge_kubeconfig (triggers _inject usage gate)
        (tmp_path / "main.tf").write_text(
            'resource "kubernetes_manifest" "prereqs" {\n'
            '  manifest = yamldecode(try(local.forge_kubeconfig, var.forge_kubeconfig_content))\n'
            '}\n'
        )

        lib = MagicMock()
        lib.inputs_metadata = {"providers": {"required": []}}  # detected from versions.tf
        project = MagicMock()
        project.id = 1
        project.name = "eks-bnk-prereqs"
        project.cloud_provider = "aws"
        project.region = "us-east-1"
        project.k8s_clusters = []
        project.k8s_context = None
        module = _make_module(cloud_provider="aws", lib_module=lib)
        module.project = project

        with patch("services.execution.config_writer._resolve_project_kubeconfig_path", return_value=None):
            write_provider_config(
                str(tmp_path),
                module,
                variables={"cluster_name": "bnk-prereqs-eks"},
            )

        # Count total occurrences of 'data "aws_eks_cluster" "cluster"' across ALL .tf files
        total_eks_data_source_declarations = 0
        for tf_file in tmp_path.iterdir():
            if tf_file.suffix == ".tf":
                text = tf_file.read_text()
                total_eks_data_source_declarations += text.count('data "aws_eks_cluster" "cluster"')

        assert total_eks_data_source_declarations == 1, (
            f"data \"aws_eks_cluster\" must be declared exactly once across all .tf files "
            f"(found {total_eks_data_source_declarations}); duplicate causes `tofu init` failure"
        )

        # forge_kubeconfig local must be defined (either in bnk_forge_providers.tf or
        # bnk_forge_locals.tf — _inject's definition guard handles both cases)
        forge_kubeconfig_defined = False
        for tf_file in tmp_path.iterdir():
            if tf_file.suffix == ".tf":
                if "forge_kubeconfig" in tf_file.read_text():
                    forge_kubeconfig_defined = True
                    break
        assert forge_kubeconfig_defined, "forge_kubeconfig must be defined in at least one .tf file"

    def test_writes_ibm_provider_config_for_roks(self, tmp_path):
        lib = MagicMock()
        lib.inputs_metadata = {"providers": {"required": ["kubernetes", "helm"]}}
        module = _make_module(cloud_provider="ibm", region="us-south", lib_module=lib)

        write_provider_config(
            str(tmp_path),
            module,
            variables={"cluster_name": "roks-prod", "ibm_region": "us-south"},
        )

        provider_file = os.path.join(str(tmp_path), "bnk_forge_providers.tf")
        assert os.path.exists(provider_file)
        content = open(provider_file).read()
        assert 'provider "ibm"' in content
        assert 'data "ibm_container_cluster_config" "cluster"' in content
        assert 'cluster_name_id = "roks-prod"' in content
        assert 'provider "kubernetes"' in content
        assert 'provider "helm"' in content

    def test_ibm_provider_config_includes_resource_group(self, tmp_path):
        lib = MagicMock()
        lib.inputs_metadata = {"providers": {"required": ["kubernetes"]}}
        module = _make_module(cloud_provider="ibm", region="eu-de", lib_module=lib)

        write_provider_config(
            str(tmp_path),
            module,
            variables={
                "cluster_name": "roks-eu",
                "ibm_region": "eu-de",
                "ibm_resource_group_id": "rg-123",
            },
        )

        content = open(os.path.join(str(tmp_path), "bnk_forge_providers.tf")).read()
        assert 'resource_group_id = "rg-123"' in content


# ── _inject_forge_kubeconfig_locals ─────────────────────────────────────────

def _make_project(cloud_provider="aws", name="test-cluster", region="us-east-1"):
    p = MagicMock()
    p.cloud_provider = cloud_provider
    p.name = name
    p.region = region
    p.id = 1
    # _resolve_project_kubeconfig_path accesses these
    p.cluster = None
    return p


class TestInjectForgeKubeconfigLocals:
    """FIX A: usage-gate + AWS data source co-emission."""

    def test_no_reference_does_not_write_locals(self, tmp_path):
        """Module with NO forge_kubeconfig reference -> no bnk_forge_locals.tf written."""
        # Write a .tf file that does NOT reference local.forge_kubeconfig
        (tmp_path / "main.tf").write_text('resource "aws_vpc" "main" {}\n')
        project = _make_project(cloud_provider="aws")

        _inject_forge_kubeconfig_locals(str(tmp_path), {"cluster_name": "my-eks"}, project)

        assert not (tmp_path / "bnk_forge_locals.tf").exists(), (
            "locals file must not be written when no .tf references forge_kubeconfig"
        )

    def test_empty_workspace_does_not_write_locals(self, tmp_path):
        """Empty workspace (no .tf files) -> no bnk_forge_locals.tf written."""
        project = _make_project(cloud_provider="aws")
        _inject_forge_kubeconfig_locals(str(tmp_path), {}, project)
        assert not (tmp_path / "bnk_forge_locals.tf").exists()

    def test_aws_consumer_writes_locals_and_data_sources(self, tmp_path):
        """Module referencing local.forge_kubeconfig (AWS) -> locals + data sources emitted."""
        (tmp_path / "main.tf").write_text(
            'resource "helm_release" "bnk" { values = [local.forge_kubeconfig] }\n'
        )
        project = _make_project(cloud_provider="aws")

        _inject_forge_kubeconfig_locals(
            str(tmp_path), {"cluster_name": "my-eks"}, project
        )

        assert (tmp_path / "bnk_forge_locals.tf").exists(), "locals file must be written"
        content = (tmp_path / "bnk_forge_locals.tf").read_text()
        assert 'data "aws_eks_cluster" "cluster"' in content, "EKS data source must be emitted"
        assert 'data "aws_eks_cluster_auth" "cluster"' in content, "EKS auth data source must be emitted"
        assert "forge_kubeconfig" in content, "locals block must define forge_kubeconfig"
        assert "my-eks" in content, "cluster_name must appear in data source"

    def test_aws_consumer_skips_data_sources_when_already_declared(self, tmp_path):
        """If EKS data sources already exist in workspace, they are not duplicated."""
        (tmp_path / "providers.tf").write_text(
            'data "aws_eks_cluster" "cluster" { name = "my-eks" }\n'
            'data "aws_eks_cluster_auth" "cluster" { name = "my-eks" }\n'
        )
        (tmp_path / "main.tf").write_text(
            "locals { x = local.forge_kubeconfig }\n"
        )
        project = _make_project(cloud_provider="aws")

        _inject_forge_kubeconfig_locals(str(tmp_path), {"cluster_name": "my-eks"}, project)

        content = (tmp_path / "bnk_forge_locals.tf").read_text()
        # data sources should NOT be duplicated
        assert content.count('data "aws_eks_cluster" "cluster"') == 0, (
            "EKS data source must not be emitted when already declared"
        )

    def test_already_defined_skips_injection(self, tmp_path):
        """forge_kubeconfig already defined in workspace -> no bnk_forge_locals.tf written."""
        (tmp_path / "bnk_forge_providers.tf").write_text(
            "locals { forge_kubeconfig = yamlencode({}) }\n"
        )
        project = _make_project(cloud_provider="aws")

        _inject_forge_kubeconfig_locals(str(tmp_path), {}, project)

        assert not (tmp_path / "bnk_forge_locals.tf").exists(), (
            "must not write locals when forge_kubeconfig is already defined"
        )

    def test_non_aws_consumer_writes_locals_no_eks_data_sources(self, tmp_path):
        """Non-AWS (e.g. gcp) consumer module gets locals but no AWS data sources."""
        (tmp_path / "main.tf").write_text(
            'resource "helm_release" "x" { values = [local.forge_kubeconfig] }\n'
        )
        project = _make_project(cloud_provider="gcp")

        _inject_forge_kubeconfig_locals(str(tmp_path), {"cluster_name": "my-gke"}, project)

        content = (tmp_path / "bnk_forge_locals.tf").read_text()
        assert 'data "aws_eks_cluster"' not in content
        assert "forge_kubeconfig" in content

    @patch("utils.provider_config.generate_forge_kubeconfig_local", return_value="locals {}\n")
    def test_aws_cluster_name_is_json_escaped_in_data_sources(self, _mock_local, tmp_path):
        project = MagicMock()
        project.cloud_provider = "aws"
        project.name = "proj"
        cluster_name = 'prod"cluster\n${path.module}\nname'
        project.project_variables = {"variable_defaults": {"cluster_name": cluster_name}}

        module_tf = tmp_path / "module.tf"
        module_tf.write_text("locals { kube = try(local.forge_kubeconfig, var.kubeconfig_path) }\n")

        _inject_forge_kubeconfig_locals(str(tmp_path), variables={}, project=project)

        locals_tf = (tmp_path / "bnk_forge_locals.tf").read_text()
        escaped = json.dumps(cluster_name)
        assert locals_tf.count(f"name = {escaped}") == 2
