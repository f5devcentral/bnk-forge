"""
BU-026: Unit tests for services.variable_parser module.

Tests VariableParser (HCL parsing, type conversion, default values),
VariableValidator (type checking, required fields), and dependency detection.
All tests use real parser logic against in-memory strings — no mocks, no git.
"""

import pytest

from services.variable_parser import VariableParser, VariableValidator

# ── Type parsing ─────────────────────────────────────────────────────


class TestParseTerraformType:
    def test_simple_string(self):
        assert VariableParser.parse_terraform_type("string") == "string"

    def test_simple_number(self):
        assert VariableParser.parse_terraform_type("number") == "number"

    def test_simple_bool(self):
        assert VariableParser.parse_terraform_type("bool") == "bool"

    def test_simple_any(self):
        assert VariableParser.parse_terraform_type("any") == "any"

    def test_list_type(self):
        assert VariableParser.parse_terraform_type("list(string)") == "list"

    def test_map_type(self):
        assert VariableParser.parse_terraform_type("map(string)") == "map"

    def test_set_type(self):
        assert VariableParser.parse_terraform_type("set(string)") == "set"

    def test_object_type(self):
        assert VariableParser.parse_terraform_type("object({name=string})") == "object"

    def test_tuple_type(self):
        assert VariableParser.parse_terraform_type("tuple([string, number])") == "tuple"

    def test_empty_string_returns_any(self):
        assert VariableParser.parse_terraform_type("") == "any"

    def test_none_returns_any(self):
        assert VariableParser.parse_terraform_type(None) == "any"

    def test_unknown_type_returns_any(self):
        assert VariableParser.parse_terraform_type("foobar") == "any"

    def test_whitespace_stripped(self):
        assert VariableParser.parse_terraform_type("  string  ") == "string"


# ── Default value parsing ────────────────────────────────────────────


class TestParseDefaultValue:
    def test_null_returns_none(self):
        assert VariableParser.parse_default_value("null", "string") is None

    def test_bool_true(self):
        assert VariableParser.parse_default_value("true", "bool") is True

    def test_bool_false(self):
        assert VariableParser.parse_default_value("false", "bool") is False

    def test_number_integer(self):
        assert VariableParser.parse_default_value("42", "number") == 42

    def test_number_float(self):
        assert VariableParser.parse_default_value("3.14", "number") == 3.14

    def test_number_invalid(self):
        assert VariableParser.parse_default_value("not-a-number", "number") is None

    def test_string_quoted(self):
        assert VariableParser.parse_default_value('"hello"', "string") == "hello"

    def test_string_single_quoted(self):
        assert VariableParser.parse_default_value("'world'", "string") == "world"

    def test_string_unquoted(self):
        assert VariableParser.parse_default_value("bare", "string") == "bare"

    def test_list_empty(self):
        assert VariableParser.parse_default_value("[]", "list") == []

    def test_list_with_items(self):
        result = VariableParser.parse_default_value('["a", "b", "c"]', "list")
        assert result == ["a", "b", "c"]

    def test_list_non_bracket_returns_empty(self):
        assert VariableParser.parse_default_value("not-a-list", "list") == []

    def test_map_with_braces(self):
        result = VariableParser.parse_default_value('{"key": "val"}', "map")
        # Returns the raw string for complex map types
        assert isinstance(result, str)
        assert "key" in result

    def test_map_non_brace_returns_empty(self):
        assert VariableParser.parse_default_value("not-a-map", "map") == {}

    def test_empty_string_returns_none(self):
        assert VariableParser.parse_default_value("", "string") is None

    def test_none_returns_none(self):
        assert VariableParser.parse_default_value(None, "string") is None

    def test_whitespace_only_returns_none(self):
        assert VariableParser.parse_default_value("   ", "string") is None


# ── HCL variable parsing ────────────────────────────────────────────


class TestParseVariablesFromHcl:
    def test_simple_variable(self):
        hcl = '''
variable "vpc_cidr" {
  type        = string
  description = "CIDR block for VPC"
  default     = "10.0.0.0/16"
}
'''
        result = VariableParser.parse_variables_from_hcl(hcl)
        assert len(result) == 1
        v = result[0]
        assert v["name"] == "vpc_cidr"
        assert v["type"] == "string"
        assert v["description"] == "CIDR block for VPC"
        assert v["default"] == "10.0.0.0/16"
        assert v["required"] is False

    def test_required_variable(self):
        hcl = '''
variable "region" {
  type        = string
  description = "AWS region"
}
'''
        result = VariableParser.parse_variables_from_hcl(hcl)
        assert len(result) == 1
        assert result[0]["required"] is True
        assert result[0]["default"] is None

    def test_multiple_variables(self):
        hcl = '''
variable "name" {
  type = string
}

variable "count" {
  type    = number
  default = 3
}

variable "enable_dns" {
  type    = bool
  default = true
}
'''
        result = VariableParser.parse_variables_from_hcl(hcl)
        assert len(result) == 3
        names = [v["name"] for v in result]
        assert "name" in names
        assert "count" in names
        assert "enable_dns" in names

    def test_bool_default_parsed(self):
        hcl = '''
variable "enable" {
  type    = bool
  default = true
}
'''
        result = VariableParser.parse_variables_from_hcl(hcl)
        assert result[0]["default"] is True
        assert result[0]["required"] is False

    def test_number_default_parsed(self):
        hcl = '''
variable "replicas" {
  type    = number
  default = 3
}
'''
        result = VariableParser.parse_variables_from_hcl(hcl)
        assert result[0]["default"] == 3

    def test_list_type_detected(self):
        hcl = '''
variable "availability_zones" {
  type    = list(string)
  default = ["us-west-2a", "us-west-2b"]
}
'''
        result = VariableParser.parse_variables_from_hcl(hcl)
        assert result[0]["type"] == "list"
        assert isinstance(result[0]["default"], list)
        assert len(result[0]["default"]) == 2

    def test_sensitive_flag(self):
        hcl = '''
variable "db_password" {
  type      = string
  sensitive = true
}
'''
        result = VariableParser.parse_variables_from_hcl(hcl)
        assert result[0]["sensitive"] is True

    def test_non_sensitive_default(self):
        hcl = '''
variable "name" {
  type = string
}
'''
        result = VariableParser.parse_variables_from_hcl(hcl)
        assert result[0]["sensitive"] is False

    def test_empty_content(self):
        assert VariableParser.parse_variables_from_hcl("") == []

    def test_no_variables_in_content(self):
        hcl = '''
resource "aws_vpc" "main" {
  cidr_block = "10.0.0.0/16"
}
'''
        assert VariableParser.parse_variables_from_hcl(hcl) == []

    def test_variable_with_validation_block_still_parsed(self):
        """Variables with validation blocks are still extracted (name, type, etc.)
        even though the regex can't always capture the validation sub-block itself."""
        hcl = '''
variable "instance_type" {
  type = string
  validation {
    condition     = var.instance_type != ""
    error_message = "Must not be empty."
  }
}
'''
        result = VariableParser.parse_variables_from_hcl(hcl)
        assert len(result) == 1
        assert result[0]["name"] == "instance_type"
        assert result[0]["type"] == "string"
        assert result[0]["required"] is True


# ── Dependency detection ─────────────────────────────────────────────


class TestDetectDependencies:
    def test_vpc_dependency(self):
        variables = [{"name": "vpc_id", "description": "VPC ID"}]
        deps = VariableParser.detect_dependencies_from_variables(variables, "eks")
        assert "vpc" in deps

    def test_subnet_dependency(self):
        variables = [{"name": "subnet_ids", "description": "Subnet IDs"}]
        deps = VariableParser.detect_dependencies_from_variables(variables, "eks")
        assert "subnet" in deps

    def test_eks_dependency(self):
        variables = [{"name": "eks_cluster_name", "description": ""}]
        deps = VariableParser.detect_dependencies_from_variables(variables, "storage")
        assert "eks" in deps

    def test_no_self_dependency(self):
        """A module should not list itself as a dependency."""
        variables = [{"name": "vpc_id", "description": ""}]
        deps = VariableParser.detect_dependencies_from_variables(variables, "vpc")
        assert "vpc" not in deps

    def test_no_false_positive_without_suffix(self):
        """Variable without cross-module suffix shouldn't trigger dependency."""
        variables = [{"name": "vpc_enabled", "description": ""}]
        deps = VariableParser.detect_dependencies_from_variables(variables, "eks")
        # vpc_enabled doesn't have _id, _ids, _arn, etc. suffix after "vpc"
        assert "vpc" not in deps

    def test_multiple_dependencies(self):
        variables = [
            {"name": "vpc_id", "description": ""},
            {"name": "subnet_ids", "description": ""},
            {"name": "eks_cluster_endpoint", "description": ""},
        ]
        deps = VariableParser.detect_dependencies_from_variables(variables, "app")
        assert "vpc" in deps
        assert "subnet" in deps

    def test_empty_variables(self):
        deps = VariableParser.detect_dependencies_from_variables([], "my-module")
        assert deps == []


# ── Variable validation ──────────────────────────────────────────────


class TestVariableValidator:
    def test_required_variable_present(self):
        valid, msg = VariableValidator.validate_variable_value(
            "us-west-2",
            {"name": "region", "type": "string", "required": True},
        )
        assert valid is True

    def test_required_variable_missing(self):
        valid, msg = VariableValidator.validate_variable_value(
            None,
            {"name": "region", "type": "string", "required": True},
        )
        assert valid is False
        assert "required" in msg

    def test_optional_variable_none(self):
        valid, msg = VariableValidator.validate_variable_value(
            None,
            {"name": "tags", "type": "map", "required": False},
        )
        assert valid is True

    def test_string_type_valid(self):
        valid, _ = VariableValidator.validate_variable_value(
            "hello",
            {"name": "x", "type": "string"},
        )
        assert valid is True

    def test_string_type_invalid(self):
        valid, msg = VariableValidator.validate_variable_value(
            123,
            {"name": "x", "type": "string"},
        )
        assert valid is False
        assert "string" in msg

    def test_number_type_valid_int(self):
        valid, _ = VariableValidator.validate_variable_value(
            42,
            {"name": "x", "type": "number"},
        )
        assert valid is True

    def test_number_type_valid_float(self):
        valid, _ = VariableValidator.validate_variable_value(
            3.14,
            {"name": "x", "type": "number"},
        )
        assert valid is True

    def test_number_type_valid_string_number(self):
        """Strings that look like numbers should pass."""
        valid, _ = VariableValidator.validate_variable_value(
            "42",
            {"name": "x", "type": "number"},
        )
        assert valid is True

    def test_number_type_invalid(self):
        valid, msg = VariableValidator.validate_variable_value(
            "not-a-number",
            {"name": "x", "type": "number"},
        )
        assert valid is False

    def test_bool_type_valid(self):
        valid, _ = VariableValidator.validate_variable_value(
            True,
            {"name": "x", "type": "bool"},
        )
        assert valid is True

    def test_bool_type_string_true(self):
        valid, _ = VariableValidator.validate_variable_value(
            "true",
            {"name": "x", "type": "bool"},
        )
        assert valid is True

    def test_bool_type_invalid(self):
        valid, msg = VariableValidator.validate_variable_value(
            "maybe",
            {"name": "x", "type": "bool"},
        )
        assert valid is False

    def test_list_type_valid(self):
        valid, _ = VariableValidator.validate_variable_value(
            ["a", "b"],
            {"name": "x", "type": "list"},
        )
        assert valid is True

    def test_list_type_invalid(self):
        valid, _ = VariableValidator.validate_variable_value(
            "not-a-list",
            {"name": "x", "type": "list"},
        )
        assert valid is False

    def test_map_type_valid(self):
        valid, _ = VariableValidator.validate_variable_value(
            {"key": "val"},
            {"name": "x", "type": "map"},
        )
        assert valid is True

    def test_map_type_invalid(self):
        valid, _ = VariableValidator.validate_variable_value(
            "not-a-map",
            {"name": "x", "type": "map"},
        )
        assert valid is False

    def test_object_type_valid(self):
        valid, _ = VariableValidator.validate_variable_value(
            {"nested": {"key": "val"}},
            {"name": "x", "type": "object"},
        )
        assert valid is True


# ── Batch variable validation ────────────────────────────────────────


class TestValidateVariables:
    def test_all_valid(self):
        schema = [
            {"name": "region", "type": "string", "required": True},
            {"name": "count", "type": "number", "required": False},
        ]
        valid, errors = VariableValidator.validate_variables(
            {"region": "us-west-2", "count": 3},
            schema,
        )
        assert valid is True
        assert errors == []

    def test_missing_required(self):
        schema = [
            {"name": "region", "type": "string", "required": True},
        ]
        valid, errors = VariableValidator.validate_variables({}, schema)
        assert valid is False
        assert any("region" in e for e in errors)

    def test_unknown_variables_ignored(self):
        schema = [
            {"name": "region", "type": "string", "required": True},
        ]
        valid, errors = VariableValidator.validate_variables(
            {"region": "us-west-2", "extra": "ignored"},
            schema,
        )
        assert valid is True

    def test_type_error_reported(self):
        schema = [
            {"name": "count", "type": "number", "required": True},
        ]
        valid, errors = VariableValidator.validate_variables(
            {"count": "not-a-number"},
            schema,
        )
        assert valid is False
        assert len(errors) == 1


# ── User variable validation ────────────────────────────────────────


class TestValidateUserVariables:
    def test_module_sourced_skipped(self):
        """Variables with source='module' should not be required from user."""
        metadata = {
            "required": [
                {"name": "vpc_id", "type": "string", "source": "module"},
                {"name": "region", "type": "string", "source": "user"},
            ],
            "optional": [],
        }
        valid, errors = VariableValidator.validate_user_variables(
            {"region": "us-west-2"},
            metadata,
        )
        assert valid is True
        assert errors == []

    def test_project_sourced_skipped(self):
        """Variables with source='project' are auto-filled, not required."""
        metadata = {
            "required": [
                {"name": "project_name", "type": "string", "source": "project"},
            ],
            "optional": [],
        }
        valid, errors = VariableValidator.validate_user_variables({}, metadata)
        assert valid is True

    def test_user_required_missing(self):
        metadata = {
            "required": [
                {"name": "region", "type": "string", "source": "user"},
            ],
            "optional": [],
        }
        valid, errors = VariableValidator.validate_user_variables({}, metadata)
        assert valid is False
        assert any("region" in e for e in errors)

    def test_type_validation_on_user_vars(self):
        metadata = {
            "required": [],
            "optional": [
                {"name": "count", "type": "number"},
            ],
        }
        valid, errors = VariableValidator.validate_user_variables(
            {"count": "not-a-number"},
            metadata,
        )
        assert valid is False
        assert any("number" in e for e in errors)

    def test_boolean_string_values_accepted_in_user_vars(self):
        # Blueprint ${...} / form toggles deliver booleans as strings; the
        # validator must accept them (regression: install_testing="true" was
        # rejected with "must be a boolean").
        metadata = {
            "required": [],
            "optional": [
                {"name": "install_testing", "type": "boolean"},
                {"name": "install_gateway", "type": "boolean"},
                {"name": "cluster_create", "type": "bool"},
            ],
        }
        valid, errors = VariableValidator.validate_user_variables(
            {"install_testing": "true", "install_gateway": "false", "cluster_create": True},
            metadata,
        )
        assert valid is True, errors

    def test_non_boolean_string_still_rejected(self):
        metadata = {"required": [], "optional": [{"name": "flag", "type": "boolean"}]}
        valid, errors = VariableValidator.validate_user_variables({"flag": "maybe"}, metadata)
        assert valid is False
        assert any("boolean" in e for e in errors)

    def test_list_type_in_user_vars(self):
        metadata = {
            "required": [],
            "optional": [
                {"name": "zones", "type": "list(string)"},
            ],
        }
        valid, errors = VariableValidator.validate_user_variables(
            {"zones": ["us-west-2a"]},
            metadata,
        )
        assert valid is True

    def test_map_object_type_in_user_vars(self):
        metadata = {
            "required": [],
            "optional": [
                {"name": "tags", "type": "map(string)"},
            ],
        }
        valid, errors = VariableValidator.validate_user_variables(
            {"tags": {"env": "prod"}},
            metadata,
        )
        assert valid is True
