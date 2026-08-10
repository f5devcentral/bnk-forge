"""Unit tests for utils.security helpers."""

import pytest

from utils.security import (
    is_sensitive_input,
    validate_action_inputs,
    validate_cli_arg,
    validate_helm_timeout,
)


class TestIsSensitiveInput:
    def test_explicit_flag_wins(self):
        assert is_sensitive_input({"name": "innocent_field", "sensitive": True}) is True

    def test_falsy_explicit_falls_back_to_heuristic(self):
        # `sensitive: False` does not override a credential-named field.
        assert is_sensitive_input({"name": "ibmcloud_api_key", "sensitive": False}) is True

    def test_credential_names_match(self):
        for name in [
            "ibmcloud_api_key",
            "aws_secret_access_key",
            "aws_access_key",
            "github_token",
            "db_password",
            "ssh_private_key",
            "service_account_secret",
            "passphrase",
        ]:
            assert is_sensitive_input({"name": name}) is True, name

    def test_source_field_match(self):
        assert is_sensitive_input(
            {"name": "creds", "source": "credential_template", "source_field": "ibmcloud_api_key"}
        ) is True

    def test_benign_names_pass_through(self):
        for name in [
            "region",
            "tokens_per_minute",  # "token" not at suffix boundary
            "cluster_name",
            "vpc_cidr",
            "tags",
        ]:
            assert is_sensitive_input({"name": name}) is False, name

    def test_missing_metadata(self):
        assert is_sensitive_input({}) is False
        assert is_sensitive_input({"name": None}) is False


class TestValidateCliArg:
    def test_normal_value_ok(self):
        validate_cli_arg("name", "my-release")

    def test_dash_prefix_rejected(self):
        with pytest.raises(ValueError):
            validate_cli_arg("name", "--evil")


class TestValidateActionInputs:
    _SCENARIO = [{"name": "scenario", "type": "string", "choices": ["tcpl4lb", "udp"]}]
    _FREE = [{"name": "name", "type": "string"}]

    def test_undeclared_key_rejected(self):
        with pytest.raises(ValueError, match="Undeclared action input 'evil'"):
            validate_action_inputs(self._FREE, {"evil": "x"})

    def test_enum_value_out_of_choices_rejected(self):
        with pytest.raises(ValueError, match="scenario"):
            validate_action_inputs(self._SCENARIO, {"scenario": "not-in-choices"})

    def test_enum_value_in_choices_accepted(self):
        assert validate_action_inputs(self._SCENARIO, {"scenario": "tcpl4lb"}) == {"scenario": "tcpl4lb"}

    def test_leading_dash_free_string_rejected(self):
        with pytest.raises(ValueError, match="cannot start with"):
            validate_action_inputs(self._FREE, {"name": "--kubeconfig=/attacker/path"})

    def test_free_string_accepted(self):
        assert validate_action_inputs(self._FREE, {"name": "prod-run"}) == {"name": "prod-run"}

    def test_default_applied_when_omitted(self):
        declared = [{"name": "region", "type": "string", "default": "us-east"}]
        assert validate_action_inputs(declared, None) == {"region": "us-east"}

    def test_omitted_without_default_absent_from_effective(self):
        assert validate_action_inputs(self._FREE, {}) == {}

    def test_choices_bypasses_cli_arg_check(self):
        # A dash-leading value is fine when it is an explicit declared choice.
        declared = [{"name": "flag", "type": "string", "choices": ["--all"]}]
        assert validate_action_inputs(declared, {"flag": "--all"}) == {"flag": "--all"}

    def test_boolean_coerced(self):
        declared = [{"name": "verbose", "type": "boolean"}]
        assert validate_action_inputs(declared, {"verbose": "true"}) == {"verbose": True}

    def test_number_coerced_and_dash_rejected(self):
        declared = [{"name": "count", "type": "integer"}]
        assert validate_action_inputs(declared, {"count": "5"}) == {"count": 5}
        with pytest.raises(ValueError):
            validate_action_inputs(declared, {"count": "-5"})

    def test_non_numeric_for_number_type_rejected(self):
        declared = [{"name": "count", "type": "integer"}]
        with pytest.raises(ValueError, match="Invalid number"):
            validate_action_inputs(declared, {"count": "abc"})


class TestValidateHelmTimeout:
    def test_valid_formats(self):
        for v in ["5m", "300s", "1h30m"]:
            validate_helm_timeout(v)

    def test_none_allowed(self):
        validate_helm_timeout(None)

    def test_garbage_rejected(self):
        with pytest.raises(ValueError):
            validate_helm_timeout("abc")
