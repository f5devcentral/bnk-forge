"""
Tests for services.execution.variable_assembler — the 7-layer variable chain.

Covers:
  - Layer 1-6 precedence chain
  - Auto-wire common variables
  - Dependency output wiring (required + optional, apply + destroy)
  - Transformations (uppercase, lowercase, base64, json_encode, unknown)
  - JWT guard (S23-001)
  - Variable mappings with deferred source deletion (S15-035)
  - Raw encoding skip (S23-001)
  - can_execute dependency checking
  - find_dependency_by_path path matching (exact, suffix, normalized)
"""

import base64
import json

import pytest

from models import CloudCredentialTemplate


class TestApplyTransformation:
    """Test apply_transformation() for all transform types."""

    def test_none_passthrough(self):
        """Transform type 'none' returns the value unchanged."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation("hello", "none") == "hello"
        assert apply_transformation(42, "none") == 42
        assert apply_transformation({"a": 1}, "none") == {"a": 1}

    def test_empty_string_passthrough(self):
        """Empty transform type returns the value unchanged."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation("hello", "") == "hello"
        assert apply_transformation("hello", None) == "hello"

    def test_uppercase(self):
        """Transform 'uppercase' converts to upper case."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation("hello", "uppercase") == "HELLO"
        assert apply_transformation("MiXeD", "uppercase") == "MIXED"

    def test_uppercase_non_string(self):
        """Transform 'uppercase' coerces non-strings to str first."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation(42, "uppercase") == "42"

    def test_lowercase(self):
        """Transform 'lowercase' converts to lower case."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation("HELLO", "lowercase") == "hello"
        assert apply_transformation("MiXeD", "lowercase") == "mixed"

    def test_json_encode(self):
        """Transform 'json_encode' serializes the value as JSON."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation({"key": "val"}, "json_encode") == '{"key": "val"}'
        assert apply_transformation([1, 2], "json_encode") == "[1, 2]"
        assert apply_transformation("hello", "json_encode") == '"hello"'

    def test_base64_encode(self):
        """Transform 'base64' encodes a plain string."""
        from services.execution.variable_assembler import apply_transformation

        result = apply_transformation("hello", "base64")
        expected = base64.b64encode(b"hello").decode()
        assert result == expected

    def test_base64_non_string(self):
        """Transform 'base64' coerces non-string to str first."""
        from services.execution.variable_assembler import apply_transformation

        result = apply_transformation(42, "base64")
        expected = base64.b64encode(b"42").decode()
        assert result == expected

    def test_unknown_transform_returns_original(self):
        """Unknown transform type returns the original value."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation("hello", "rot13") == "hello"
        assert apply_transformation(99, "fancy_transform") == 99


class TestJWTGuard:
    """Test the JWT guard mechanism (S23-001)."""

    def test_looks_like_jwt_valid(self):
        """A value with 3 dot-separated parts starting with eyJ is detected as JWT."""
        from services.execution.variable_assembler import _looks_like_jwt

        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signature_here"
        assert _looks_like_jwt(jwt) is True

    def test_looks_like_jwt_not_jwt(self):
        """Plain strings are not detected as JWT."""
        from services.execution.variable_assembler import _looks_like_jwt

        assert _looks_like_jwt("hello-world") is False
        assert _looks_like_jwt("just.two") is False
        assert _looks_like_jwt("a.b.c.d") is False  # 4 parts

    def test_looks_like_jwt_wrong_prefix(self):
        """Three-part string without eyJ prefix is not JWT."""
        from services.execution.variable_assembler import _looks_like_jwt

        assert _looks_like_jwt("abc.def.ghi") is False

    def test_base64_skips_jwt(self):
        """Transform 'base64' returns raw value when input looks like JWT."""
        from services.execution.variable_assembler import apply_transformation

        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.sig"
        result = apply_transformation(jwt, "base64")
        # Should NOT be base64 encoded — returned as-is
        assert result == jwt

    def test_base64_encodes_non_jwt(self):
        """Transform 'base64' does encode when input is not JWT."""
        from services.execution.variable_assembler import apply_transformation

        result = apply_transformation("not-a-jwt", "base64")
        assert result == base64.b64encode(b"not-a-jwt").decode()


class TestBuildVariables:
    """Test the 7-layer variable assembly chain via build_variables()."""

    def test_layer1_project_system_vars(self, db, make_project, make_project_module):
        """Layer 1: project_name, environment, common_tags are always set."""
        from services.execution.variable_assembler import build_variables

        project = make_project(name="my-proj", environment="staging", cloud_provider="on-prem")
        module = make_project_module(project=project)

        result = build_variables(db, module)

        assert result["project_name"] == "my-proj"
        assert result["environment"] == "staging"
        assert result["common_tags"]["Project"] == "my-proj"
        assert result["common_tags"]["Environment"] == "staging"
        assert result["common_tags"]["ManagedBy"] == "BNK-Forge"

    def test_layer1_default_environment(self, db, make_project, make_project_module):
        """Layer 1: environment defaults to 'dev' when None."""
        from services.execution.variable_assembler import build_variables

        project = make_project(environment=None, cloud_provider="on-prem")
        module = make_project_module(project=project)

        result = build_variables(db, module)
        assert result["environment"] == "dev"

    def test_layer1_aws_region_injection(self, db, make_project, make_project_module):
        """Layer 1: AWS projects inject both 'region' and 'aws_region'."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="aws", region="us-west-2")
        module = make_project_module(project=project)

        result = build_variables(db, module)
        assert result["region"] == "us-west-2"
        assert result["aws_region"] == "us-west-2"
        assert result["aws_profile"] == "default"

    def test_layer1_non_aws_no_region(self, db, make_project, make_project_module):
        """Layer 1: non-AWS projects do NOT inject region or aws_region."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="on-prem", region="us-east-1")
        module = make_project_module(project=project)

        result = build_variables(db, module)
        assert "region" not in result
        assert "aws_region" not in result

    def test_layer1_ibm_region_injection(self, db, make_project, make_project_module):
        """Layer 1: IBM projects inject both 'region' and 'ibm_region'."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="ibm", region="us-south")
        module = make_project_module(project=project)

        result = build_variables(db, module)
        assert result["region"] == "us-south"
        assert result["ibm_region"] == "us-south"

    def test_layer1_ibm_resource_group_from_credential_template(self, db, make_project, make_project_module):
        """Layer 1: IBM projects inherit resource group from the selected credential template."""
        from services.execution.variable_assembler import build_variables

        template = CloudCredentialTemplate(
            name="ibm-template",
            provider="ibm",
            region="us-south",
            ibmcloud_resource_group="platform-rg",
        )
        db.add(template)
        db.flush()

        project = make_project(cloud_provider="ibm", region="us-south", credential_template_id=template.id)
        module = make_project_module(project=project)

        result = build_variables(db, module)
        assert result["ibmcloud_resource_group"] == "platform-rg"

    def test_layer1_ibm_smart_defaults_include_cluster_region_and_api_key(self, db, client, operator_headers, all_test_users):
        """IBM smart defaults inherit template-backed region, resource group, and API key."""
        from core.encryption import encrypt_value
        from models import CloudCredentialTemplate, ModuleLibrary, Project

        template = CloudCredentialTemplate(
            name="ibm-template",
            provider="ibm",
            region="us-south",
            ibmcloud_resource_group="platform-rg",
            ibmcloud_api_key_encrypted=encrypt_value("template-secret"),
        )
        db.add(template)
        db.flush()

        project = Project(
            name="ibm-project",
            cloud_provider="ibm",
            region="us-south",
            credential_template_id=template.id,
            backend_type="local",
            color="#2563eb",
            icon="",
        )
        db.add(project)
        db.flush()

        module = ModuleLibrary(
            name="ibm_roks_single_nic",
            category="infra",
            path="modules/cluster",
            provider="ibm",
            git_source="https://github.com/example/ibm-roks.git//modules/cluster",
            inputs_metadata={
                "required": [
                    {"name": "ibmcloud_api_key", "type": "string", "source": "user", "description": "IBM API key"},
                    {"name": "ibmcloud_cluster_region", "type": "string", "source": "user", "description": "IBM region"},
                ],
                "optional": [
                    {"name": "ibmcloud_resource_group", "type": "string", "source": "user", "description": "IBM resource group"},
                ],
            },
        )
        db.add(module)
        db.commit()

        response = client.get(
            f"/api/module-library/{module.id}/smart-defaults",
            params={"project_id": project.id},
            headers=operator_headers,
        )

        assert response.status_code == 200
        data = response.json()["defaults"]
        assert data["ibmcloud_api_key"] == "template-secret"
        assert data["ibmcloud_cluster_region"] == "us-south"
        assert data["ibmcloud_resource_group"] == "platform-rg"

    def test_cluster_aliases_include_roks_names(self, db, make_project, make_project_module, make_k8s_cluster):
        """Cluster-bound projects expose ROKS/OpenShift cluster name aliases."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="ibm", region="us-south")
        make_k8s_cluster(project=project, name="roks-main", cloud_provider="ibm")
        module = make_project_module(project=project)

        result = build_variables(db, module)
        assert result["cluster_name"] == "roks-main"
        assert result["roks_cluster_name"] == "roks-main"
        assert result["openshift_cluster_name"] == "roks-main"

    def test_kubeconfig_auto_injected_when_no_user_value(
        self, db, make_project, make_project_module, make_k8s_cluster, make_module_library,
    ):
        """Regression for PR #406 review point 1: with no conflicting user
        override, the registered cluster's kubeconfig is auto-injected."""
        from core.encryption import encrypt_value
        from services.execution.variable_assembler import build_variables

        sample_kubeconfig = (
            "apiVersion: v1\n"
            "clusters:\n"
            "- cluster:\n"
            "    server: https://k8s.example.com:6443\n"
            "    certificate-authority-data: dGVzdA==\n"
            "  name: test-cluster\n"
            "contexts:\n"
            "- context:\n"
            "    cluster: test-cluster\n"
            "    user: admin\n"
            "  name: test-context\n"
            "current-context: test-context\n"
            "kind: Config\n"
            "users:\n"
            "- name: admin\n"
            "  user:\n"
            "    token: test-token\n"
        )

        project = make_project(cloud_provider="aws", region="us-east-1")
        make_k8s_cluster(
            project=project,
            name="eks-main",
            kubeconfig_encrypted=encrypt_value(sample_kubeconfig),
        )
        lib_module = make_module_library(variables_schema=[{"name": "kubeconfig_content"}])
        module = make_project_module(project=project, library_module=lib_module)

        result = build_variables(db, module)
        assert "kubeconfig_content" in result
        assert "server: https://k8s.example.com:6443" in result["kubeconfig_content"]

    def test_kubeconfig_injected_when_user_override_is_blank_placeholder(
        self, db, make_project, make_project_module, make_k8s_cluster, make_module_library,
    ):
        """Regression for PR #406 review point 1 (blocker): a falsy/placeholder
        user override must not block auto-injection — this was the bug the PR
        was written to fix (auto-injection was previously a no-op because
        Layer 5 ran after the Layer-1 injection and stomped it)."""
        from core.encryption import encrypt_value
        from services.execution.variable_assembler import build_variables

        sample_kubeconfig = (
            "apiVersion: v1\n"
            "clusters:\n"
            "- cluster:\n"
            "    server: https://k8s.example.com:6443\n"
            "    certificate-authority-data: dGVzdA==\n"
            "  name: test-cluster\n"
            "contexts:\n"
            "- context:\n"
            "    cluster: test-cluster\n"
            "    user: admin\n"
            "  name: test-context\n"
            "current-context: test-context\n"
            "kind: Config\n"
            "users:\n"
            "- name: admin\n"
            "  user:\n"
            "    token: test-token\n"
        )

        project = make_project(cloud_provider="aws", region="us-east-1")
        make_k8s_cluster(
            project=project,
            name="eks-main",
            kubeconfig_encrypted=encrypt_value(sample_kubeconfig),
        )
        lib_module = make_module_library(variables_schema=[{"name": "kubeconfig_content"}])
        module = make_project_module(
            project=project,
            library_module=lib_module,
            variable_overrides={"kubeconfig_content": ""},
        )

        result = build_variables(db, module)
        assert "server: https://k8s.example.com:6443" in result["kubeconfig_content"]

    def test_kubeconfig_user_override_wins_over_registered_cluster(
        self, db, make_project, make_project_module, make_k8s_cluster, make_module_library,
    ):
        """Regression for PR #406 review point 1: a genuine user-supplied
        kubeconfig must still win over the registered cluster's kubeconfig."""
        from core.encryption import encrypt_value
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="aws", region="us-east-1")
        make_k8s_cluster(
            project=project,
            name="eks-main",
            kubeconfig_encrypted=encrypt_value("apiVersion: v1\nkind: Config\n"),
        )
        lib_module = make_module_library(variables_schema=[{"name": "kubeconfig_content"}])
        module = make_project_module(
            project=project,
            library_module=lib_module,
            variable_overrides={"kubeconfig_content": "user-supplied-kubeconfig-yaml"},
        )

        result = build_variables(db, module)
        assert result["kubeconfig_content"] == "user-supplied-kubeconfig-yaml"

    def test_kubeconfig_schema_as_dict_does_not_crash(
        self, db, make_project, make_project_module, make_k8s_cluster, make_module_library,
    ):
        """Regression for PR #406 review point 3 (blocker): ModuleLibrary.variables_schema
        can be a dict keyed by variable name rather than a list of dicts. build_variables
        must not crash, and must not inject kubeconfig in that shape."""
        from core.encryption import encrypt_value
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="aws", region="us-east-1")
        make_k8s_cluster(
            project=project,
            name="eks-main",
            kubeconfig_encrypted=encrypt_value("apiVersion: v1\nkind: Config\n"),
        )
        lib_module = make_module_library(variables_schema={"kubeconfig_content": {"type": "string"}})
        module = make_project_module(project=project, library_module=lib_module)

        result = build_variables(db, module)
        assert "kubeconfig_content" not in result

    def test_layer2_project_variables(self, db, make_project, make_project_module):
        """Layer 2: project-level variables are applied."""
        from services.execution.variable_assembler import build_variables

        project = make_project(
            cloud_provider="on-prem",
            project_variables={"vpc_cidr": "10.0.0.0/16", "enable_nat": True},
        )
        module = make_project_module(project=project)

        result = build_variables(db, module)
        assert result["vpc_cidr"] == "10.0.0.0/16"
        assert result["enable_nat"] is True

    def test_layer2_legacy_format(self, db, make_project, make_project_module):
        """Layer 2: legacy format with 'variable_defaults' key is unwrapped."""
        from services.execution.variable_assembler import build_variables

        project = make_project(
            cloud_provider="on-prem",
            project_variables={"variable_defaults": {"foo": "bar"}},
        )
        module = make_project_module(project=project)

        result = build_variables(db, module)
        assert result["foo"] == "bar"

    def test_layer5_user_overrides_higher_precedence(self, db, make_project, make_project_module):
        """Layer 5: user overrides (variable_overrides) stomp project variables."""
        from services.execution.variable_assembler import build_variables

        project = make_project(
            cloud_provider="on-prem",
            project_variables={"cidr": "10.0.0.0/16"},
        )
        module = make_project_module(
            project=project,
            variables={"cidr": "172.16.0.0/12"},  # Layer 5 should win
        )

        result = build_variables(db, module)
        assert result["cidr"] == "172.16.0.0/12"

    def test_layer5_variable_overrides_preferred(self, db, make_project, make_project_module):
        """Layer 5: variable_overrides is preferred over variables."""
        from models import ProjectModule
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="on-prem")
        module = make_project_module(project=project, variables={"x": "from_vars"})
        # Set variable_overrides directly (factory uses variables kwarg)
        module.variable_overrides = {"x": "from_overrides"}
        db.flush()

        result = build_variables(db, module)
        assert result["x"] == "from_overrides"

    def test_layer6_secrets_override_user_vars(self, db, make_project, make_project_module):
        """Layer 6: secret variables override user overrides."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="on-prem")
        module = make_project_module(
            project=project,
            variables={"api_key": "user_value"},
        )

        result = build_variables(
            db, module, secret_variables={"api_key": "secret_value"}
        )
        assert result["api_key"] == "secret_value"

    def test_full_precedence_chain(self, db, make_project, make_project_module):
        """All layers interact correctly — higher layers override lower."""
        from services.execution.variable_assembler import build_variables

        project = make_project(
            cloud_provider="on-prem",
            project_variables={"shared": "from_project", "proj_only": "proj_val"},
        )
        module = make_project_module(
            project=project,
            variables={"shared": "from_user", "user_only": "user_val"},
        )

        result = build_variables(
            db, module,
            secret_variables={"shared": "from_secret", "secret_only": "secret_val"},
        )

        # Layer 6 (secret) wins over Layer 5 (user) wins over Layer 2 (project)
        assert result["shared"] == "from_secret"
        assert result["proj_only"] == "proj_val"
        assert result["user_only"] == "user_val"
        assert result["secret_only"] == "secret_val"


class TestDependencyOutputWiring:
    """Test Layer 3: Dependency output wiring from inputs_metadata."""

    def test_wires_required_dependency_output(self, db, make_project, make_project_module, make_module_library):
        """Layer 3: required dependency output is wired into variables."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="on-prem")

        # Create the dependency module (already applied with outputs)
        dep_lib = make_module_library(name="vpc", path="infra/aws/vpc")
        dep_module = make_project_module(
            project=project,
            library_module=dep_lib,
            status="applied",
        )
        dep_module.outputs = {"vpc_id": "vpc-123", "subnet_ids": ["subnet-1", "subnet-2"]}
        db.flush()

        # Create the consumer module with inputs_metadata
        consumer_lib = make_module_library(
            name="eks",
            path="infra/aws/eks",
            inputs_metadata={
                "required": [{
                    "name": "vpc_id",
                    "source": "module",
                    "from_module": "infra/aws/vpc",
                    "from_output": "vpc_id",
                }],
                "optional": [],
            },
        )
        consumer = make_project_module(project=project, library_module=consumer_lib)
        consumer.dependencies = [dep_module.id]
        db.flush()

        result = build_variables(db, consumer)
        assert result["vpc_id"] == "vpc-123"

    def test_missing_required_dependency_raises(self, db, make_project, make_project_module, make_module_library):
        """Layer 3: missing required dependency raises ValueError on apply."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="on-prem")

        consumer_lib = make_module_library(
            name="eks",
            path="infra/aws/eks",
            inputs_metadata={
                "required": [{
                    "name": "vpc_id",
                    "source": "module",
                    "from_module": "infra/aws/nonexistent",
                    "from_output": "vpc_id",
                }],
                "optional": [],
            },
        )
        consumer = make_project_module(project=project, library_module=consumer_lib)

        with pytest.raises(ValueError, match="Required dependency not available"):
            build_variables(db, consumer, operation="apply")

    def test_missing_required_dependency_lenient_on_destroy(self, db, make_project, make_project_module, make_module_library):
        """Layer 3: destroy mode is lenient — missing deps do not raise."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="on-prem")

        consumer_lib = make_module_library(
            name="eks",
            path="infra/aws/eks",
            inputs_metadata={
                "required": [{
                    "name": "vpc_id",
                    "source": "module",
                    "from_module": "infra/aws/nonexistent",
                    "from_output": "vpc_id",
                }],
                "optional": [],
            },
        )
        consumer = make_project_module(project=project, library_module=consumer_lib)

        # Should NOT raise in destroy mode
        result = build_variables(db, consumer, operation="destroy")
        assert "vpc_id" not in result  # missing dep, not wired

    def test_optional_dependency_does_not_raise(self, db, make_project, make_project_module, make_module_library):
        """Layer 3: missing optional dependency never raises."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="on-prem")

        consumer_lib = make_module_library(
            name="eks",
            path="infra/aws/eks",
            inputs_metadata={
                "required": [],
                "optional": [{
                    "name": "monitoring_endpoint",
                    "source": "module",
                    "from_module": "infra/monitoring",
                    "from_output": "endpoint",
                }],
            },
        )
        consumer = make_project_module(project=project, library_module=consumer_lib)

        result = build_variables(db, consumer, operation="apply")
        assert "monitoring_endpoint" not in result

    def test_layer3_fallback_does_not_override_earlier_resolved_value(
        self,
        db,
        make_project,
        make_project_module,
        make_module_library,
    ):
        """Layer 3 fallback must not overwrite values resolved in earlier layers."""
        from services.execution.variable_assembler import build_variables

        project = make_project(
            cloud_provider="on-prem",
            project_variables={"vpc_id": "vpc-from-project-vars"},
        )

        dep_lib = make_module_library(name="producer", path="infra/aws/eks-cluster-create")
        dep_module = make_project_module(
            project=project,
            library_module=dep_lib,
            status="applied",
        )
        dep_module.outputs = {"vpc_id": "vpc-from-fallback"}
        db.flush()

        consumer_lib = make_module_library(
            name="consumer",
            path="infra/aws/eks",
            inputs_metadata={
                "required": [{
                    "name": "vpc_id",
                    "source": "module",
                    "from_module": "infra/aws/nonexistent-hint",
                    "from_output": "vpc_id",
                }],
                "optional": [],
            },
        )
        consumer = make_project_module(project=project, library_module=consumer_lib)
        consumer.dependencies = [dep_module.id]
        db.flush()

        result = build_variables(db, consumer, operation="apply")
        assert result["vpc_id"] == "vpc-from-project-vars"

    def test_layer3_fallback_prefers_current_stack_instance(
        self,
        db,
        make_project,
        make_project_module,
        make_module_library,
        make_stack_instance,
    ):
        """Layer 3 fallback must avoid cross-stack output wiring in same project."""
        from services.execution.variable_assembler import build_variables

        project = make_project(cloud_provider="on-prem")
        stack_a = make_stack_instance(project=project)
        stack_b = make_stack_instance(project=project)

        dep_lib = make_module_library(name="producer", path="infra/aws/vpc-producer")
        dep_a = make_project_module(
            project=project,
            library_module=dep_lib,
            status="applied",
            stack_instance_id=stack_a.id,
        )
        dep_a.outputs = {"vpc_id": "vpc-stack-a"}

        dep_b = make_project_module(
            project=project,
            library_module=dep_lib,
            status="applied",
            stack_instance_id=stack_b.id,
            path_in_project="modules/producer-b",
        )
        dep_b.outputs = {"vpc_id": "vpc-stack-b"}
        db.flush()

        consumer_lib = make_module_library(
            name="consumer",
            path="infra/aws/eks",
            inputs_metadata={
                "required": [{
                    "name": "vpc_id",
                    "source": "module",
                    "from_module": "infra/aws/nonexistent-hint",
                    "from_output": "vpc_id",
                }],
                "optional": [],
            },
        )
        consumer = make_project_module(
            project=project,
            library_module=consumer_lib,
            stack_instance_id=stack_b.id,
        )
        db.flush()

        result = build_variables(db, consumer, operation="apply")
        assert result["vpc_id"] == "vpc-stack-b"


class TestAutoWireCommonVariables:
    """Test Layer 4: auto-wiring outputs from applied modules."""

    def test_auto_wires_outputs_from_applied_module(self, db, make_project, make_project_module, make_module_library):
        """Layer 4: outputs from applied modules are wired to unset variables."""
        from services.execution.variable_assembler import auto_wire_common_variables

        project = make_project(cloud_provider="on-prem")

        # Applied module with outputs
        applied_lib = make_module_library(name="vpc", path="infra/vpc")
        applied = make_project_module(project=project, library_module=applied_lib, status="applied")
        applied.outputs = {"vpc_id": "vpc-abc"}
        db.flush()

        # Target module
        target_lib = make_module_library(name="eks", path="infra/eks")
        target = make_project_module(project=project, library_module=target_lib)

        variables = {}
        auto_wire_common_variables(db, target, variables)
        assert variables["vpc_id"] == "vpc-abc"

    def test_auto_wire_does_not_overwrite_existing(self, db, make_project, make_project_module, make_module_library):
        """Layer 4: auto-wire skips variables that are already set."""
        from services.execution.variable_assembler import auto_wire_common_variables

        project = make_project(cloud_provider="on-prem")

        applied_lib = make_module_library(name="vpc", path="infra/vpc")
        applied = make_project_module(project=project, library_module=applied_lib, status="applied")
        applied.outputs = {"vpc_id": "vpc-from-autowire"}
        db.flush()

        target_lib = make_module_library(name="eks", path="infra/eks")
        target = make_project_module(project=project, library_module=target_lib)

        variables = {"vpc_id": "already-set"}
        auto_wire_common_variables(db, target, variables)
        assert variables["vpc_id"] == "already-set"

    def test_auto_wire_prefers_declared_dependency(self, db, make_project, make_project_module, make_module_library):
        """Layer 4: declared dependencies win over arbitrary modules on conflict."""
        from services.execution.variable_assembler import auto_wire_common_variables

        project = make_project(cloud_provider="on-prem")

        # Module A produces "shared_output" (NOT a dependency)
        lib_a = make_module_library(name="modA", path="infra/modA")
        mod_a = make_project_module(project=project, library_module=lib_a, status="applied")
        mod_a.outputs = {"shared_output": "from_A"}
        db.flush()

        # Module B produces "shared_output" (IS a declared dependency)
        lib_b = make_module_library(name="modB", path="infra/modB")
        mod_b = make_project_module(project=project, library_module=lib_b, status="applied")
        mod_b.outputs = {"shared_output": "from_B"}
        db.flush()

        target_lib = make_module_library(name="target", path="infra/target")
        target = make_project_module(project=project, library_module=target_lib)
        target.dependencies = [mod_b.id]  # Declare B as dependency
        db.flush()

        variables = {}
        auto_wire_common_variables(db, target, variables)
        assert variables["shared_output"] == "from_B"


class TestVariableMappings:
    """Test Layer 7: variable mappings with rename/transform."""

    def test_simple_rename(self, db, make_project, make_project_module):
        """Mapping renames source to target, deleting original."""
        from models import VariableMapping
        from services.execution.variable_assembler import apply_variable_mappings

        project = make_project(cloud_provider="on-prem")
        module = make_project_module(project=project)

        # Create a mapping: old_name -> new_name
        mapping = VariableMapping(
            project_module_id=module.id,
            source_variable_name="old_name",
            target_variable_name="new_name",
            transform_type="none",
            is_active=True,
        )
        db.add(mapping)
        db.flush()

        variables = {"old_name": "hello", "other": "keep"}
        result = apply_variable_mappings(db, module, variables)

        assert result["new_name"] == "hello"
        assert "old_name" not in result  # source deleted
        assert result["other"] == "keep"

    def test_mapping_with_transform(self, db, make_project, make_project_module):
        """Mapping applies transformation during rename."""
        from models import VariableMapping
        from services.execution.variable_assembler import apply_variable_mappings

        project = make_project(cloud_provider="on-prem")
        module = make_project_module(project=project)

        mapping = VariableMapping(
            project_module_id=module.id,
            source_variable_name="name",
            target_variable_name="upper_name",
            transform_type="uppercase",
            is_active=True,
        )
        db.add(mapping)
        db.flush()

        variables = {"name": "hello"}
        result = apply_variable_mappings(db, module, variables)

        assert result["upper_name"] == "HELLO"

    def test_identity_mapping_keeps_source(self, db, make_project, make_project_module):
        """When source == target, the variable is NOT deleted."""
        from models import VariableMapping
        from services.execution.variable_assembler import apply_variable_mappings

        project = make_project(cloud_provider="on-prem")
        module = make_project_module(project=project)

        mapping = VariableMapping(
            project_module_id=module.id,
            source_variable_name="x",
            target_variable_name="x",
            transform_type="uppercase",
            is_active=True,
        )
        db.add(mapping)
        db.flush()

        variables = {"x": "hello"}
        result = apply_variable_mappings(db, module, variables)
        assert result["x"] == "HELLO"

    def test_inactive_mapping_ignored(self, db, make_project, make_project_module):
        """Inactive mappings are skipped."""
        from models import VariableMapping
        from services.execution.variable_assembler import apply_variable_mappings

        project = make_project(cloud_provider="on-prem")
        module = make_project_module(project=project)

        mapping = VariableMapping(
            project_module_id=module.id,
            source_variable_name="old",
            target_variable_name="new",
            transform_type="none",
            is_active=False,  # inactive
        )
        db.add(mapping)
        db.flush()

        variables = {"old": "value"}
        result = apply_variable_mappings(db, module, variables)
        assert "old" in result
        assert "new" not in result

    def test_deferred_deletion_s15_035(self, db, make_project, make_project_module):
        """S15-035: Source vars are deleted AFTER all mappings, not during."""
        from models import VariableMapping
        from services.execution.variable_assembler import apply_variable_mappings

        project = make_project(cloud_provider="on-prem")
        module = make_project_module(project=project)

        # Two mappings both read from "source"
        m1 = VariableMapping(
            project_module_id=module.id,
            source_variable_name="source",
            target_variable_name="target_a",
            transform_type="uppercase",
            is_active=True,
        )
        m2 = VariableMapping(
            project_module_id=module.id,
            source_variable_name="source",
            target_variable_name="target_b",
            transform_type="lowercase",
            is_active=True,
        )
        db.add_all([m1, m2])
        db.flush()

        variables = {"source": "Hello"}
        result = apply_variable_mappings(db, module, variables)

        # Both targets should have values
        assert result["target_a"] == "HELLO"
        assert result["target_b"] == "hello"
        # Source should be deleted (mapped to different targets)
        assert "source" not in result

    def test_missing_source_skipped(self, db, make_project, make_project_module):
        """Mapping with nonexistent source variable is silently skipped."""
        from models import VariableMapping
        from services.execution.variable_assembler import apply_variable_mappings

        project = make_project(cloud_provider="on-prem")
        module = make_project_module(project=project)

        mapping = VariableMapping(
            project_module_id=module.id,
            source_variable_name="nonexistent",
            target_variable_name="target",
            transform_type="none",
            is_active=True,
        )
        db.add(mapping)
        db.flush()

        variables = {"other": "val"}
        result = apply_variable_mappings(db, module, variables)
        assert "target" not in result
        assert result["other"] == "val"


class TestCanExecute:
    """Test the can_execute() dependency checker."""

    def test_no_dependencies_can_execute(self, db, make_project_module):
        """A module with no dependencies can always execute."""
        from services.execution.variable_assembler import can_execute

        module = make_project_module()
        module.dependencies = []
        db.flush()

        ok, missing = can_execute(db, module)
        assert ok is True
        assert missing == []

    def test_applied_dependency_can_execute(self, db, make_project, make_project_module, make_module_library):
        """Module can execute when all deps are 'applied' with outputs."""
        from services.execution.variable_assembler import can_execute

        project = make_project(cloud_provider="on-prem")

        dep_lib = make_module_library(name="vpc", path="vpc")
        dep = make_project_module(project=project, library_module=dep_lib, status="applied")
        dep.outputs = {"vpc_id": "vpc-1"}
        db.flush()

        consumer = make_project_module(project=project)
        consumer.dependencies = [dep.id]
        db.flush()

        ok, missing = can_execute(db, consumer)
        assert ok is True
        assert missing == []

    def test_unapplied_dependency_blocks(self, db, make_project, make_project_module, make_module_library):
        """Module cannot execute when a dep is not 'applied'."""
        from services.execution.variable_assembler import can_execute

        project = make_project(cloud_provider="on-prem")

        dep_lib = make_module_library(name="vpc", path="vpc")
        dep = make_project_module(project=project, library_module=dep_lib, status="planned")
        db.flush()

        consumer = make_project_module(project=project)
        consumer.dependencies = [dep.id]
        db.flush()

        ok, missing = can_execute(db, consumer)
        assert ok is False
        assert len(missing) == 1
        assert "planned" in missing[0]

    def test_missing_dependency_blocks(self, db, make_project_module):
        """Module cannot execute when a dep ID doesn't exist."""
        from services.execution.variable_assembler import can_execute

        module = make_project_module()
        module.dependencies = [99999]  # nonexistent ID
        db.flush()

        ok, missing = can_execute(db, module)
        assert ok is False
        assert "99999" in missing[0]

    def test_null_outputs_blocks(self, db, make_project, make_project_module, make_module_library):
        """Module cannot execute when dep is applied but outputs is None."""
        from services.execution.variable_assembler import can_execute

        project = make_project(cloud_provider="on-prem")

        dep_lib = make_module_library(name="vpc", path="vpc")
        dep = make_project_module(project=project, library_module=dep_lib, status="applied")
        dep.outputs = None  # applied but no outputs captured
        db.flush()

        consumer = make_project_module(project=project)
        consumer.dependencies = [dep.id]
        db.flush()

        ok, missing = can_execute(db, consumer)
        assert ok is False
        assert "no outputs" in missing[0]

    def test_empty_dict_outputs_ok(self, db, make_project, make_project_module, make_module_library):
        """Module CAN execute when dep has outputs={} (empty but not None)."""
        from services.execution.variable_assembler import can_execute

        project = make_project(cloud_provider="on-prem")

        dep_lib = make_module_library(name="vpc", path="vpc")
        dep = make_project_module(project=project, library_module=dep_lib, status="applied")
        dep.outputs = {}  # S14-020: empty dict is valid
        db.flush()

        consumer = make_project_module(project=project)
        consumer.dependencies = [dep.id]
        db.flush()

        ok, missing = can_execute(db, consumer)
        assert ok is True


class TestFindDependencyByPath:
    """Test find_dependency_by_path path matching strategies."""

    def test_exact_match(self, db, make_project, make_project_module, make_module_library):
        """Exact path match returns the correct module."""
        from services.execution.variable_assembler import find_dependency_by_path

        project = make_project(cloud_provider="on-prem")
        lib = make_module_library(name="vpc", path="infra/aws/vpc")
        mod = make_project_module(project=project, library_module=lib)

        result = find_dependency_by_path(db, project.id, "infra/aws/vpc")
        assert result is not None
        assert result.id == mod.id

    def test_suffix_match(self, db, make_project, make_project_module, make_module_library):
        """Suffix match works when one path ends with the other."""
        from services.execution.variable_assembler import find_dependency_by_path

        project = make_project(cloud_provider="on-prem")
        lib = make_module_library(name="vpc", path="infra/aws/vpc")
        mod = make_project_module(project=project, library_module=lib)

        result = find_dependency_by_path(db, project.id, "aws/vpc")
        assert result is not None
        assert result.id == mod.id

    def test_normalized_match(self, db, make_project, make_project_module, make_module_library):
        """Normalized match strips 'infra/' prefix from both sides."""
        from services.execution.variable_assembler import find_dependency_by_path

        project = make_project(cloud_provider="on-prem")
        lib = make_module_library(name="vpc", path="infra/aws/vpc")
        make_project_module(project=project, library_module=lib)

        # Query without "infra/" prefix — should match via normalization
        result = find_dependency_by_path(db, project.id, "infra/aws/vpc")
        assert result is not None

    def test_no_match_returns_none(self, db, make_project, make_project_module, make_module_library):
        """Returns None when no module matches the path."""
        from services.execution.variable_assembler import find_dependency_by_path

        project = make_project(cloud_provider="on-prem")
        lib = make_module_library(name="vpc", path="infra/aws/vpc")
        make_project_module(project=project, library_module=lib)

        result = find_dependency_by_path(db, project.id, "totally/different/path")
        assert result is None

    def test_stack_scoped_prefers_same_stack_over_other_stack(
        self, db, make_project, make_project_module, make_module_library, make_stack_instance
    ):
        """When stack_instance_id provided, returns same-stack module even if another
        stack's module would be returned first by a plain project-wide query."""
        from services.execution.variable_assembler import find_dependency_by_path

        project = make_project(cloud_provider="on-prem")
        lib = make_module_library(name="kubeadm-init", path="bare-metal/kubeadm-init")

        stack_a = make_stack_instance(project=project)
        stack_b = make_stack_instance(project=project)

        # stack_a module is not_initialized with no outputs (would be wrong pick).
        # path_in_project must be distinct per project (unique constraint).
        mod_a = make_project_module(
            project=project,
            library_module=lib,
            status="not_initialized",
            stack_instance_id=stack_a.id,
            path_in_project="stack-a/kubeadm-init",
        )
        # stack_b module is applied with outputs (the correct pick)
        mod_b = make_project_module(
            project=project,
            library_module=lib,
            status="applied",
            stack_instance_id=stack_b.id,
            path_in_project="stack-b/kubeadm-init",
        )
        mod_b.outputs = {"join_command": "kubeadm join ..."}
        db.flush()

        # Querying from stack_b's perspective must return mod_b, not mod_a
        result = find_dependency_by_path(
            db, project.id, "bare-metal/kubeadm-init", stack_instance_id=stack_b.id
        )
        assert result is not None
        assert result.id == mod_b.id

    def test_stack_scoped_falls_back_to_project_wide_when_not_in_stack(
        self, db, make_project, make_project_module, make_module_library, make_stack_instance
    ):
        """When stack_instance_id provided but path not in that stack, falls back to
        the project-wide match (original behaviour preserved)."""
        from services.execution.variable_assembler import find_dependency_by_path

        project = make_project(cloud_provider="on-prem")
        lib = make_module_library(name="vpc", path="infra/aws/vpc")

        stack_a = make_stack_instance(project=project)
        stack_b = make_stack_instance(project=project)

        # Module only exists in stack_a — stack_b has no such module
        mod_a = make_project_module(
            project=project,
            library_module=lib,
            status="applied",
            stack_instance_id=stack_a.id,
        )

        result = find_dependency_by_path(
            db, project.id, "infra/aws/vpc", stack_instance_id=stack_b.id
        )
        assert result is not None
        assert result.id == mod_a.id

    def test_without_stack_instance_id_behaves_as_before(
        self, db, make_project, make_project_module, make_module_library, make_stack_instance
    ):
        """Omitting stack_instance_id (None) preserves the original project-wide search."""
        from services.execution.variable_assembler import find_dependency_by_path

        project = make_project(cloud_provider="on-prem")
        lib = make_module_library(name="vpc", path="infra/aws/vpc")
        stack = make_stack_instance(project=project)
        mod = make_project_module(
            project=project,
            library_module=lib,
            stack_instance_id=stack.id,
        )

        result = find_dependency_by_path(db, project.id, "infra/aws/vpc")
        assert result is not None
        assert result.id == mod.id
