"""Sensitive manifest inputs must reach the engine's redactor (issue #408.6).

A step's argv is echoed verbatim to the task log and the module-log WebSocket.
secret_values was built from cloud-credential and pull values only, so an
artifact declaring `args: [..., "--token", "{{inputs.api_token}}"]` leaked that
token in cleartext.

These tests exist because the failure is INVISIBLE by construction: the shipped
roksbnkctl artifact passes its secret via a redacted -e env var, so neither a
live deploy nor any other test would notice if _sensitive_input_values silently
started returning []. Nothing else pins this.
"""

import pytest

from tasks.container_tasks import _sensitive_input_values


@pytest.mark.unit
class TestSensitiveInputValues:
    def test_grouped_inputs_yield_sensitive_values_only(self):
        """The shape the shipped artifacts actually use: required/optional groups."""
        manifest = {
            "inputs": {
                "required": [
                    {"name": "api_token", "type": "string", "source": "user"},
                    {"name": "region", "type": "string", "source": "user"},
                ],
                "optional": [
                    {"name": "debug", "type": "boolean", "source": "user"},
                ],
            }
        }
        variables = {"api_token": "SUPERSECRET", "region": "us-south", "debug": True}

        values = _sensitive_input_values(manifest, variables)

        assert "SUPERSECRET" in values, (
            "a credential-named input was not fed to the redactor — its value is "
            "echoed in cleartext when it appears in a step's argv (#408.6)"
        )
        assert "us-south" not in values, "a non-sensitive input must not be redacted"
        assert len(values) == 1

    def test_explicit_sensitive_flag_is_honoured(self):
        """A flat list, and a name the heuristic would not catch on its own."""
        manifest = {"inputs": [{"name": "widget", "type": "string", "sensitive": True}]}
        assert _sensitive_input_values(manifest, {"widget": "hunter2"}) == ["hunter2"]

    def test_credential_named_input_is_caught_without_the_flag(self):
        """is_sensitive_input's name heuristic covers manifests that omit the flag."""
        manifest = {"inputs": [{"name": "ibmcloud_api_key", "type": "string"}]}
        assert _sensitive_input_values(manifest, {"ibmcloud_api_key": "IBMKEY"}) == ["IBMKEY"]

    def test_unset_and_non_string_values_are_skipped(self):
        """A declared-but-unset secret must not put "" into the redaction list.

        An empty string in secret_values would make the redactor rewrite every
        boundary in the log.
        """
        manifest = {
            "inputs": [
                {"name": "api_token", "type": "string", "sensitive": True},
                {"name": "api_secret", "type": "string", "sensitive": True},
                {"name": "token_count", "type": "number", "sensitive": True},
            ]
        }
        values = _sensitive_input_values(manifest, {"api_token": "", "token_count": 5})
        assert values == []

    @pytest.mark.parametrize("manifest", [{}, {"inputs": None}, {"inputs": "nope"}, {"inputs": []}])
    def test_missing_or_malformed_inputs_are_tolerated(self, manifest):
        """A manifest without inputs must not break the engine build."""
        assert _sensitive_input_values(manifest, {"anything": "x"}) == []


@pytest.mark.unit
class TestActionInputRedaction:
    """Action inputs are declared separately and supplied at invocation time.

    Review finding: `_sensitive_input_values` read only the TOP-LEVEL
    manifest["inputs"], while actions declare their own under
    manifest["actions"][<name>]["inputs"] — and run_action merges the values in
    after the engine is built. So a sensitive action input reached step argv and
    was echoed verbatim into task.logs, the module-log WebSocket and
    OperationResult.stdout.
    """

    MANIFEST = {
        "inputs": {"required": [{"name": "region", "type": "string", "source": "user"}]},
        "actions": {
            "run-e2e": {
                "title": "E2E",
                "inputs": [{"name": "api_token", "type": "string", "sensitive": True}],
                "steps": [{"name": "e", "args": ["e2e", "--token", "{{inputs.api_token}}"]}],
            }
        },
    }

    def test_action_declared_sensitive_input_is_collected(self):
        values = _sensitive_input_values(self.MANIFEST, {"api_token": "ACTION-SECRET"})
        assert "ACTION-SECRET" in values, (
            "an action input marked sensitive never reached the redactor — the "
            "token is echoed in the `$ docker run ...` line"
        )

    def test_non_sensitive_action_inputs_are_not_collected(self):
        assert _sensitive_input_values(self.MANIFEST, {"region": "us-south"}) == []

    def test_top_level_inputs_still_collected_alongside_actions(self):
        m = {
            "inputs": [{"name": "ibmcloud_api_key", "type": "string"}],
            "actions": {"a": {"inputs": [{"name": "api_token", "sensitive": True}]}},
        }
        values = _sensitive_input_values(m, {"ibmcloud_api_key": "K1", "api_token": "K2"})
        assert set(values) == {"K1", "K2"}
