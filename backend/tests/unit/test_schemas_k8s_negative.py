"""
CT-032: Negative schema tests for K8s cluster/resource request schemas.

Tests that wrong payload shapes are rejected with ValidationError.
"""

import pytest
from pydantic import ValidationError

from routes.k8s._shared import (
    AdaptiveModuleRequest,
    AnnotateResourceRequest,
    ClusterCreateRequest,
    ClusterUpdateRequest,
    LabelResourceRequest,
    ResourceCreateRequest,
    ResourcePatchRequest,
    ResourceUpdateRequest,
    ScaleDeploymentRequest,
)
from schemas.k8s import CreateMigrationRequest


class TestClusterCreateRequestNegative:
    def test_valid_create(self):
        req = ClusterCreateRequest(name="dev", kubeconfig="apiVersion: v1\n...")
        assert req.name == "dev"
        assert req.default_namespace == "default"

    def test_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            ClusterCreateRequest(kubeconfig="data")  # type: ignore[call-arg]

    def test_missing_kubeconfig_rejected(self):
        with pytest.raises(ValidationError):
            ClusterCreateRequest(name="dev")  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            ClusterCreateRequest()  # type: ignore[call-arg]

    def test_name_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ClusterCreateRequest(name=123, kubeconfig="data")  # type: ignore[arg-type]

    def test_invalid_aws_region_rejected(self):
        with pytest.raises(ValidationError):
            ClusterCreateRequest(name="dev", kubeconfig="data", cloud_provider="aws", region="xx-fake-1")


class TestClusterUpdateRequestNegative:
    def test_all_optional(self):
        req = ClusterUpdateRequest()
        assert req.name is None

    def test_invalid_region_rejected(self):
        with pytest.raises(ValidationError):
            ClusterUpdateRequest(cloud_provider="aws", region="xx-fake-1")

    def test_ssh_port_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ClusterUpdateRequest(ssh_remote_k8s_port="not-an-int")  # type: ignore[arg-type]


class TestResourceCreateRequestNegative:
    def test_valid_create(self):
        req = ResourceCreateRequest(resource_yaml="apiVersion: v1\nkind: ConfigMap")
        assert req.dry_run is False

    def test_missing_resource_yaml_rejected(self):
        with pytest.raises(ValidationError):
            ResourceCreateRequest()  # type: ignore[call-arg]

    def test_resource_yaml_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ResourceCreateRequest(resource_yaml=123)  # type: ignore[arg-type]


class TestResourceUpdateRequestNegative:
    def test_missing_resource_yaml_rejected(self):
        with pytest.raises(ValidationError):
            ResourceUpdateRequest()  # type: ignore[call-arg]


class TestScaleDeploymentRequestNegative:
    def test_valid_scale(self):
        req = ScaleDeploymentRequest(replicas=3, namespace="default")
        assert req.replicas == 3

    def test_missing_replicas_rejected(self):
        with pytest.raises(ValidationError):
            ScaleDeploymentRequest(namespace="default")  # type: ignore[call-arg]

    def test_missing_namespace_rejected(self):
        with pytest.raises(ValidationError):
            ScaleDeploymentRequest(replicas=3)  # type: ignore[call-arg]

    def test_replicas_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ScaleDeploymentRequest(replicas="three", namespace="default")  # type: ignore[arg-type]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            ScaleDeploymentRequest()  # type: ignore[call-arg]


class TestResourcePatchRequestNegative:
    def test_valid_patch(self):
        req = ResourcePatchRequest(patch_data={"spec": {"replicas": 5}})
        assert req.patch_type == "strategic"

    def test_missing_patch_data_rejected(self):
        with pytest.raises(ValidationError):
            ResourcePatchRequest()  # type: ignore[call-arg]

    def test_patch_data_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ResourcePatchRequest(patch_data="not a dict")  # type: ignore[arg-type]


class TestLabelResourceRequestNegative:
    def test_valid_label(self):
        req = LabelResourceRequest(labels={"app": "nginx"})
        assert req.overwrite is False

    def test_missing_labels_rejected(self):
        with pytest.raises(ValidationError):
            LabelResourceRequest()  # type: ignore[call-arg]

    def test_labels_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            LabelResourceRequest(labels="not a dict")  # type: ignore[arg-type]


class TestAnnotateResourceRequestNegative:
    def test_missing_annotations_rejected(self):
        with pytest.raises(ValidationError):
            AnnotateResourceRequest()  # type: ignore[call-arg]


class TestAdaptiveModuleRequestNegative:
    def test_all_optional(self):
        """All fields optional — empty body is valid."""
        req = AdaptiveModuleRequest()
        assert req.template_slug is None
        assert req.module_paths is None

    def test_module_paths_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            AdaptiveModuleRequest(module_paths="not-a-list")  # type: ignore[arg-type]


class TestCreateMigrationRequestKubectlResources:
    """Validate kubectl_resources format hardening on CreateMigrationRequest."""

    _REQUIRED = {
        "source_descriptor": {"proxy_type": "nginx", "class_name": "nginx"},
        "combined_yaml": "apiVersion: v1",
    }

    def _make(self, kubectl_resources: list) -> CreateMigrationRequest:
        return CreateMigrationRequest(
            **self._REQUIRED,
            teardown_info={"kubectl_resources": kubectl_resources},
        )

    # --- valid entries ---

    def test_valid_kind_name_accepted(self):
        req = self._make(["Deployment/old-nginx"])
        assert req.teardown_info is not None
        assert req.teardown_info["kubectl_resources"] == ["Deployment/old-nginx"]

    def test_valid_kind_name_namespace_accepted(self):
        req = self._make(["Service/old-svc default"])
        assert req.teardown_info is not None

    def test_valid_multiple_entries_accepted(self):
        req = self._make(["Deployment/old-nginx ingress-ns", "ConfigMap/old-cfg default"])
        assert req.teardown_info is not None

    def test_no_kubectl_resources_key_accepted(self):
        """teardown_info without kubectl_resources key is valid."""
        req = CreateMigrationRequest(
            **self._REQUIRED,
            teardown_info={"helm_release": "nginx", "helm_namespace": "ingress"},
        )
        assert req.teardown_info is not None

    def test_teardown_info_none_accepted(self):
        req = CreateMigrationRequest(**self._REQUIRED)
        assert req.teardown_info is None

    # --- rejected entries ---

    def test_leading_dash_flag_rejected(self):
        with pytest.raises(ValidationError, match="kind/name"):
            self._make(["--all"])

    def test_shell_metacharacter_semicolon_rejected(self):
        with pytest.raises(ValidationError, match="kind/name"):
            self._make(["kind/name; rm -rf /"])

    def test_shell_metacharacter_pipe_rejected(self):
        with pytest.raises(ValidationError, match="kind/name"):
            self._make(["kind/name | cat /etc/passwd"])

    def test_embedded_newline_rejected(self):
        with pytest.raises(ValidationError, match="kind/name"):
            self._make(["kind/name\nkill -9 1"])

    def test_empty_string_rejected(self):
        with pytest.raises(ValidationError):
            self._make([""])

    def test_no_slash_rejected(self):
        with pytest.raises(ValidationError, match="kind/name"):
            self._make(["deployment-without-slash"])

    def test_extra_whitespace_tokens_rejected(self):
        with pytest.raises(ValidationError, match="kind/name"):
            self._make(["kind/name ns extra-token"])
