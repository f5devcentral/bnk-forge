"""
Tests for Helm values type preservation during variable substitution.

When rendering Helm values.yaml, pure variable references like `${replicas}`
must preserve the variable's native Python type (int, float, bool, None, dict, list).
String interpolation (`prefix-${name}`) must return strings.

This is critical for Helm schema validation — charts like cert-manager require
`replicaCount` to be a number, not a string like "1".
"""

import pytest

from services.execution.k8s_catalog_payload import (
    _apply_pack_input_defaults,
    _render_simple_string,
    _render_string_value,
    _render_template_obj,
)

# ── _render_string_value: Pure Variable References ──────────────────────


class TestPureVariableReferences:
    """Pure ${var} or {{var}} references should preserve native types."""

    # --- Integer preservation ---

    def test_integer_simple_syntax(self):
        result = _render_string_value("${replicas}", {"replicas": 3})
        assert result == 3
        assert isinstance(result, int)

    def test_integer_jinja_syntax(self):
        result = _render_string_value("{{ replicas }}", {"replicas": 3})
        assert result == 3
        assert isinstance(result, int)

    def test_integer_with_whitespace(self):
        result = _render_string_value("  ${replicas}  ", {"replicas": 5})
        assert result == 5
        assert isinstance(result, int)

    def test_integer_zero(self):
        result = _render_string_value("${count}", {"count": 0})
        assert result == 0
        assert isinstance(result, int)

    def test_negative_integer(self):
        result = _render_string_value("${offset}", {"offset": -10})
        assert result == -10
        assert isinstance(result, int)

    # --- Float preservation ---

    def test_float_simple(self):
        result = _render_string_value("${ratio}", {"ratio": 0.5})
        assert result == 0.5
        assert isinstance(result, float)

    def test_float_jinja(self):
        result = _render_string_value("{{ cpu_limit }}", {"cpu_limit": 2.5})
        assert result == 2.5
        assert isinstance(result, float)

    # --- Boolean preservation ---

    def test_boolean_true(self):
        result = _render_string_value("${enabled}", {"enabled": True})
        assert result is True
        assert isinstance(result, bool)

    def test_boolean_false(self):
        result = _render_string_value("${enabled}", {"enabled": False})
        assert result is False
        assert isinstance(result, bool)

    def test_boolean_jinja(self):
        result = _render_string_value("{{ debug }}", {"debug": True})
        assert result is True

    # --- None/null preservation ---

    def test_none_value(self):
        result = _render_string_value("${resources}", {"resources": None})
        assert result is None

    def test_none_jinja(self):
        result = _render_string_value("{{ optional }}", {"optional": None})
        assert result is None

    # --- Dict preservation ---

    def test_dict_simple(self):
        resources = {"limits": {"cpu": "100m", "memory": "128Mi"}}
        result = _render_string_value("${resources}", {"resources": resources})
        assert result == resources
        assert isinstance(result, dict)

    def test_dict_jinja(self):
        config = {"key": "value", "nested": {"a": 1}}
        result = _render_string_value("{{ config }}", {"config": config})
        assert result == config

    def test_dict_is_deep_copied(self):
        """Ensure dict values are deep copied to prevent mutation."""
        original = {"key": "value"}
        result = _render_string_value("${data}", {"data": original})
        result["key"] = "modified"
        assert original["key"] == "value"

    # --- List preservation ---

    def test_list_simple(self):
        items = ["a", "b", "c"]
        result = _render_string_value("${items}", {"items": items})
        assert result == items
        assert isinstance(result, list)

    def test_list_of_dicts(self):
        volumes = [{"name": "data", "size": "10Gi"}, {"name": "logs", "size": "5Gi"}]
        result = _render_string_value("${volumes}", {"volumes": volumes})
        assert result == volumes

    def test_list_is_deep_copied(self):
        original = [1, 2, 3]
        result = _render_string_value("${nums}", {"nums": original})
        result.append(4)
        assert original == [1, 2, 3]

    # --- String preservation (already a string, stays string) ---

    def test_string_value(self):
        result = _render_string_value("${name}", {"name": "my-app"})
        assert result == "my-app"
        assert isinstance(result, str)

    # --- Variable not found ---

    def test_undefined_variable_returns_placeholder(self):
        result = _render_string_value("${undefined}", {})
        assert result == "${undefined}"

    def test_undefined_jinja_returns_placeholder(self):
        result = _render_string_value("{{ undefined }}", {})
        assert result == "{{ undefined }}"


# ── _render_string_value: Interpolated Strings ──────────────────────────


class TestInterpolatedStrings:
    """Mixed text + variables must return strings with substitutions."""

    def test_prefix_with_variable(self):
        result = _render_string_value("prefix-${name}", {"name": "app"})
        assert result == "prefix-app"
        assert isinstance(result, str)

    def test_variable_with_suffix(self):
        result = _render_string_value("${name}-suffix", {"name": "app"})
        assert result == "app-suffix"

    def test_variable_in_middle(self):
        result = _render_string_value("hello-${name}-world", {"name": "test"})
        assert result == "hello-test-world"

    def test_multiple_variables(self):
        result = _render_string_value("${a}-${b}-${c}", {"a": "x", "b": "y", "c": "z"})
        assert result == "x-y-z"

    def test_integer_in_interpolation_becomes_string(self):
        result = _render_string_value("replicas-${count}", {"count": 3})
        assert result == "replicas-3"
        assert isinstance(result, str)

    def test_boolean_in_interpolation_becomes_string(self):
        result = _render_string_value("enabled=${flag}", {"flag": True})
        assert result == "enabled=True"

    def test_jinja_interpolation(self):
        result = _render_string_value("name={{ app }}-svc", {"app": "web"})
        assert result == "name=web-svc"

    def test_mixed_simple_and_jinja(self):
        result = _render_string_value("${a}-{{ b }}", {"a": "x", "b": "y"})
        assert result == "x-y"

    def test_plain_string_no_variables(self):
        result = _render_string_value("just a plain string", {})
        assert result == "just a plain string"


# ── _render_simple_string: Always Returns String ────────────────────────


class TestRenderSimpleString:
    """_render_simple_string always returns a string (for backward compat)."""

    def test_integer_becomes_string(self):
        result = _render_simple_string("${count}", {"count": 5})
        assert result == "5"
        assert isinstance(result, str)

    def test_boolean_becomes_string(self):
        result = _render_simple_string("${flag}", {"flag": True})
        assert result == "True"
        assert isinstance(result, str)

    def test_none_leaves_placeholder(self):
        # None values don't get substituted (match.group(0) returned)
        result = _render_simple_string("${val}", {"val": None})
        assert result == "${val}"

    def test_dict_becomes_string_repr(self):
        result = _render_simple_string("${data}", {"data": {"key": "value"}})
        assert "key" in result and "value" in result
        assert isinstance(result, str)


# ── _render_template_obj: Full Object Tree ──────────────────────────────


class TestRenderTemplateObj:
    """_render_template_obj recursively processes dicts/lists."""

    def test_nested_dict_preserves_types(self):
        template = {
            "replicaCount": "${replicas}",
            "resources": "${resources}",
            "enabled": "${enabled}",
            "name": "${name}",
        }
        variables = {
            "replicas": 3,
            "resources": {"limits": {"cpu": "100m"}},
            "enabled": True,
            "name": "my-app",
        }
        result = _render_template_obj(template, variables)

        assert result["replicaCount"] == 3
        assert isinstance(result["replicaCount"], int)
        assert result["resources"] == {"limits": {"cpu": "100m"}}
        assert isinstance(result["resources"], dict)
        assert result["enabled"] is True
        assert isinstance(result["enabled"], bool)
        assert result["name"] == "my-app"

    def test_deeply_nested_structure(self):
        template = {
            "spec": {
                "replicas": "${replicas}",
                "template": {
                    "containers": [
                        {"name": "${container_name}", "resources": "${resources}"}
                    ]
                },
            }
        }
        variables = {
            "replicas": 2,
            "container_name": "main",
            "resources": {"limits": {"memory": "512Mi"}},
        }
        result = _render_template_obj(template, variables)

        assert result["spec"]["replicas"] == 2
        assert isinstance(result["spec"]["replicas"], int)
        assert result["spec"]["template"]["containers"][0]["name"] == "main"
        assert result["spec"]["template"]["containers"][0]["resources"] == {
            "limits": {"memory": "512Mi"}
        }

    def test_list_of_dicts(self):
        template = [
            {"name": "a", "count": "${count_a}"},
            {"name": "b", "count": "${count_b}"},
        ]
        variables = {"count_a": 1, "count_b": 2}
        result = _render_template_obj(template, variables)

        assert result[0]["count"] == 1
        assert result[1]["count"] == 2

    def test_non_string_values_preserved(self):
        """Non-string values (already int/bool/etc) pass through unchanged."""
        template = {
            "static_int": 42,
            "static_bool": True,
            "static_float": 3.14,
            "static_none": None,
            "static_list": [1, 2, 3],
        }
        result = _render_template_obj(template, {})

        assert result["static_int"] == 42
        assert result["static_bool"] is True
        assert result["static_float"] == 3.14
        assert result["static_none"] is None
        assert result["static_list"] == [1, 2, 3]

    def test_mixed_static_and_variable(self):
        template = {
            "static": 100,
            "dynamic": "${count}",
            "interpolated": "name-${suffix}",
        }
        variables = {"count": 5, "suffix": "prod"}
        result = _render_template_obj(template, variables)

        assert result["static"] == 100
        assert result["dynamic"] == 5
        assert result["interpolated"] == "name-prod"


# ── Real-World Helm Chart Scenarios ─────────────────────────────────────


class TestHelmChartScenarios:
    """Test scenarios based on real Helm chart patterns."""

    def test_cert_manager_values(self):
        """The exact failure case: cert-manager replicaCount and resources."""
        template = {
            "replicaCount": "${controller_replicas}",
            "webhook": {"replicaCount": "${webhook_replicas}"},
            "cainjector": {"replicaCount": "${cainjector_replicas}"},
            "resources": "${resources}",
            "installCRDs": "${install_crds}",
        }
        variables = {
            "controller_replicas": 1,
            "webhook_replicas": 1,
            "cainjector_replicas": 1,
            "resources": None,  # null in YAML
            "install_crds": True,
        }
        result = _render_template_obj(template, variables)

        assert result["replicaCount"] == 1
        assert isinstance(result["replicaCount"], int)
        assert result["webhook"]["replicaCount"] == 1
        assert isinstance(result["webhook"]["replicaCount"], int)
        assert result["cainjector"]["replicaCount"] == 1
        assert result["resources"] is None
        assert result["installCRDs"] is True

    def test_nginx_ingress_values(self):
        """Common nginx-ingress patterns."""
        template = {
            "controller": {
                "replicaCount": "${replicas}",
                "autoscaling": {
                    "enabled": "${autoscaling_enabled}",
                    "minReplicas": "${min_replicas}",
                    "maxReplicas": "${max_replicas}",
                },
                "resources": "${controller_resources}",
            }
        }
        variables = {
            "replicas": 2,
            "autoscaling_enabled": False,
            "min_replicas": 1,
            "max_replicas": 10,
            "controller_resources": {
                "limits": {"cpu": "200m", "memory": "256Mi"},
                "requests": {"cpu": "100m", "memory": "128Mi"},
            },
        }
        result = _render_template_obj(template, variables)

        assert result["controller"]["replicaCount"] == 2
        assert result["controller"]["autoscaling"]["enabled"] is False
        assert result["controller"]["autoscaling"]["minReplicas"] == 1
        assert result["controller"]["autoscaling"]["maxReplicas"] == 10
        assert result["controller"]["resources"]["limits"]["cpu"] == "200m"

    def test_prometheus_values(self):
        """Prometheus chart with retention and storage."""
        template = {
            "server": {
                "retention": "${retention_days}d",
                "persistentVolume": {
                    "enabled": "${pv_enabled}",
                    "size": "${pv_size}",
                },
            },
            "alertmanager": {
                "enabled": "${alertmanager_enabled}",
                "replicaCount": "${alertmanager_replicas}",
            },
        }
        variables = {
            "retention_days": 15,
            "pv_enabled": True,
            "pv_size": "50Gi",
            "alertmanager_enabled": True,
            "alertmanager_replicas": 3,
        }
        result = _render_template_obj(template, variables)

        # Interpolated string (has suffix "d")
        assert result["server"]["retention"] == "15d"
        assert isinstance(result["server"]["retention"], str)

        # Pure references preserve types
        assert result["server"]["persistentVolume"]["enabled"] is True
        assert result["alertmanager"]["replicaCount"] == 3

    def test_values_with_var_dot_prefix(self):
        """Support var.name syntax (Terraform/OpenTofu style)."""
        template = {"count": "${var.replicas}", "name": "${var.app_name}"}
        variables = {"replicas": 5, "app_name": "myapp"}
        result = _render_template_obj(template, variables)

        assert result["count"] == 5
        assert result["name"] == "myapp"


# ── Edge Cases ──────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_string(self):
        result = _render_string_value("", {})
        assert result == ""

    def test_whitespace_only(self):
        result = _render_string_value("   ", {})
        assert result == "   "

    def test_variable_name_with_dots(self):
        result = _render_string_value("${my.var.name}", {"my.var.name": 42})
        assert result == 42

    def test_variable_name_with_underscores(self):
        result = _render_string_value("${my_var_name}", {"my_var_name": 99})
        assert result == 99

    def test_variable_name_with_hyphens(self):
        result = _render_string_value("${my-var-name}", {"my-var-name": 88})
        assert result == 88

    def test_jinja_with_spaces(self):
        result = _render_string_value("{{   count   }}", {"count": 7})
        assert result == 7

    def test_dollar_brace_with_spaces(self):
        result = _render_string_value("${   count   }", {"count": 8})
        assert result == 8

    def test_empty_dict_variable(self):
        result = _render_string_value("${empty}", {"empty": {}})
        assert result == {}

    def test_empty_list_variable(self):
        result = _render_string_value("${empty}", {"empty": []})
        assert result == []

    def test_complex_nested_list_dict(self):
        complex_value = {
            "items": [
                {"name": "a", "ports": [80, 443]},
                {"name": "b", "ports": [8080]},
            ],
            "metadata": {"version": 1},
        }
        result = _render_string_value("${complex}", {"complex": complex_value})
        assert result == complex_value


# ── _apply_pack_input_defaults ──────────────────────────────────────────


class TestApplyPackInputDefaults:
    """Tests for applying pack_manifest.inputs defaults to variables."""

    def test_applies_defaults_for_missing_variables(self):
        pack = {
            "inputs": {
                "optional": [
                    {"name": "replicas", "type": "number", "default": 1},
                    {"name": "enabled", "type": "boolean", "default": True},
                ]
            }
        }
        variables = {"namespace": "default"}
        result = _apply_pack_input_defaults(pack, variables)

        assert result["replicas"] == 1
        assert isinstance(result["replicas"], int)
        assert result["enabled"] is True
        assert result["namespace"] == "default"

    def test_does_not_override_existing_variables(self):
        pack = {
            "inputs": {
                "optional": [
                    {"name": "replicas", "type": "number", "default": 1},
                ]
            }
        }
        variables = {"replicas": 5}
        result = _apply_pack_input_defaults(pack, variables)

        assert result["replicas"] == 5  # Original value preserved

    def test_applies_required_defaults(self):
        pack = {
            "inputs": {
                "required": [
                    {"name": "name", "type": "string", "default": "my-app"},
                ],
                "optional": [],
            }
        }
        variables = {}
        result = _apply_pack_input_defaults(pack, variables)

        assert result["name"] == "my-app"

    def test_preserves_none_defaults(self):
        pack = {
            "inputs": {
                "optional": [
                    {"name": "resources", "type": "map(any)", "default": None},
                ]
            }
        }
        variables = {}
        result = _apply_pack_input_defaults(pack, variables)

        assert result["resources"] is None

    def test_preserves_dict_defaults(self):
        pack = {
            "inputs": {
                "optional": [
                    {
                        "name": "limits",
                        "type": "map(any)",
                        "default": {"cpu": "100m", "memory": "128Mi"},
                    },
                ]
            }
        }
        variables = {}
        result = _apply_pack_input_defaults(pack, variables)

        assert result["limits"] == {"cpu": "100m", "memory": "128Mi"}

    def test_handles_missing_inputs_section(self):
        pack = {"module": {"name": "test"}}
        variables = {"foo": "bar"}
        result = _apply_pack_input_defaults(pack, variables)

        assert result == {"foo": "bar"}

    def test_handles_empty_inputs(self):
        pack = {"inputs": {"required": [], "optional": []}}
        variables = {"foo": "bar"}
        result = _apply_pack_input_defaults(pack, variables)

        assert result == {"foo": "bar"}

    def test_does_not_mutate_original_variables(self):
        pack = {
            "inputs": {
                "optional": [
                    {"name": "replicas", "type": "number", "default": 1},
                ]
            }
        }
        original = {"namespace": "default"}
        result = _apply_pack_input_defaults(pack, original)

        assert "replicas" not in original
        assert result["replicas"] == 1

    def test_cert_manager_example(self):
        """The exact pack_manifest structure that triggered the bug."""
        pack = {
            "inputs": {
                "required": [],
                "optional": [
                    {"name": "namespace", "type": "string", "default": "cert-manager"},
                    {"name": "release_name", "type": "string", "default": "cert-manager"},
                    {"name": "cert_manager_version", "type": "string", "default": "v1.16.1"},
                    {"name": "controller_replicas", "type": "number", "default": 1},
                    {"name": "webhook_replicas", "type": "number", "default": 1},
                    {"name": "cainjector_replicas", "type": "number", "default": 1},
                    {"name": "resources", "type": "map(any)", "default": None},
                ],
            }
        }
        # Only some variables provided by the assembler
        variables = {
            "namespace": "cert-manager",
            "release_name": "cert-manager",
            "cert_manager_version": "v1.16.1",
        }
        result = _apply_pack_input_defaults(pack, variables)

        # Missing numeric defaults should be filled in
        assert result["controller_replicas"] == 1
        assert isinstance(result["controller_replicas"], int)
        assert result["webhook_replicas"] == 1
        assert result["cainjector_replicas"] == 1
        assert result["resources"] is None

        # Provided values should be preserved
        assert result["namespace"] == "cert-manager"

    def test_skips_inputs_without_default_key(self):
        """Inputs without a 'default' key should be skipped, not error."""
        pack = {
            "inputs": {
                "required": [
                    {"name": "cluster_name", "type": "string"},  # No default
                ],
                "optional": [
                    {"name": "replicas", "type": "number", "default": 1},
                ],
            }
        }
        variables = {}
        result = _apply_pack_input_defaults(pack, variables)

        assert "cluster_name" not in result
        assert result["replicas"] == 1
