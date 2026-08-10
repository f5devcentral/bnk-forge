"""Unit tests for the bnkforge.artifact.json manifest validator (SEAMS spec).

Covers acceptance of valid container_image / helm_chart / manifest artifacts and
rejection of: floating tag (no digest), non-allowlisted registry_host, a step
naming a non-own image, a step invoking sh/bash, an embedded secret, and an
unresolved reference.
"""

from __future__ import annotations

import pytest

from services.module_metadata import (
    InvalidMetadataSchemaError,
    ModuleMetadataValidator,
)

DIGEST = "sha256:" + "a" * 64
ALLOWLIST = ["ghcr.io", "quay.io"]


def _container_image_manifest(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "name": "roksbnkctl-tools-runner",
        "version": "1.11.4",
        "kind": "container_image",
        "container_image": {
            "registry_host": "ghcr.io",
            "repository": "jgruberf5/roksbnkctl-tools-runner",
            "digest": DIGEST,
        },
        "lifecycle": {"supports_apply": True, "supports_destroy": False},
        "steps": {
            "apply": [
                {"name": "deploy", "args": ["roksbnkctl", "apply", "--config", "/in/config.json"]},
            ],
        },
    }
    base.update(overrides)
    return base


def _helm_chart_manifest(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "name": "cne-far",
        "version": "0.3.0",
        "kind": "helm_chart",
        "helm_chart": {
            "registry_host": "ghcr.io",
            "repository": "jgruberf5/charts/cne-far",
            "digest": DIGEST,
        },
    }
    base.update(overrides)
    return base


def _manifest_kind_manifest(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "name": "envoy-dataplane",
        "version": "2.0.0",
        "kind": "manifest",
        "manifest": {
            "registry_host": "quay.io",
            "repository": "f5/envoy-manifests",
            "digest": DIGEST,
        },
    }
    base.update(overrides)
    return base


@pytest.fixture
def validator() -> ModuleMetadataValidator:
    return ModuleMetadataValidator()


# --------------------------------------------------------------------------
# Accepted
# --------------------------------------------------------------------------

def test_validate_container_image_accepted(validator):
    graph = validator.validate_artifact_manifest(
        _container_image_manifest(), registry_host_allowlist=ALLOWLIST
    )
    assert graph["root"] == "roksbnkctl-tools-runner@1.11.4"
    assert graph["edges"] == []


def test_validate_helm_chart_accepted(validator):
    graph = validator.validate_artifact_manifest(
        _helm_chart_manifest(), registry_host_allowlist=ALLOWLIST
    )
    assert graph["root"] == "cne-far@0.3.0"


def test_validate_manifest_kind_accepted(validator):
    validator.validate_artifact_manifest(
        _manifest_kind_manifest(), registry_host_allowlist=ALLOWLIST
    )


def test_execution_engine_defaults_to_kind_engine(validator):
    m = _container_image_manifest()
    validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)
    assert m["execution"]["engine"] == "container"

    h = _helm_chart_manifest()
    validator.validate_artifact_manifest(h, registry_host_allowlist=ALLOWLIST)
    assert h["execution"]["engine"] == "kubernetes"


def test_references_graph_resolved(validator):
    m = _container_image_manifest(references=["cne-far@0.3.0"])
    graph = validator.validate_artifact_manifest(
        m,
        registry_host_allowlist=ALLOWLIST,
        known_artifact_refs={"cne-far@0.3.0"},
    )
    assert "cne-far@0.3.0" in graph["nodes"]
    assert {"from": "roksbnkctl-tools-runner@1.11.4", "to": "cne-far@0.3.0"} in graph["edges"]


# --------------------------------------------------------------------------
# Rejected
# --------------------------------------------------------------------------

def test_reject_floating_tag_no_digest(validator):
    m = _container_image_manifest()
    m["container_image"]["digest"] = "1.11.4"  # floating tag, not sha256
    with pytest.raises(InvalidMetadataSchemaError, match="digest"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_missing_digest(validator):
    m = _container_image_manifest()
    del m["container_image"]["digest"]
    with pytest.raises(InvalidMetadataSchemaError, match="digest is required"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_non_allowlisted_registry_host(validator):
    m = _container_image_manifest()
    m["container_image"]["registry_host"] = "evil.example.com"
    with pytest.raises(InvalidMetadataSchemaError, match="allowlist"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_step_naming_non_own_image(validator):
    m = _container_image_manifest()
    m["steps"]["apply"][0]["image"] = "ghcr.io/other/image@" + DIGEST
    with pytest.raises(InvalidMetadataSchemaError, match="own image"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_step_using_shell(validator):
    m = _container_image_manifest()
    m["steps"]["apply"][0]["args"] = ["sh", "-c", "echo hi"]
    with pytest.raises(InvalidMetadataSchemaError, match="shell"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_step_with_shell_key(validator):
    m = _container_image_manifest()
    m["steps"]["apply"][0]["shell"] = "bash"
    with pytest.raises(InvalidMetadataSchemaError, match="shell"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_embedded_secret(validator):
    m = _container_image_manifest()
    m["container_image"]["password"] = "hunter2"
    with pytest.raises(InvalidMetadataSchemaError, match="secret"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_unresolved_reference(validator):
    m = _container_image_manifest(references=["does-not-exist@9.9.9"])
    with pytest.raises(InvalidMetadataSchemaError, match="does not resolve"):
        validator.validate_artifact_manifest(
            m, registry_host_allowlist=ALLOWLIST, known_artifact_refs={"cne-far@0.3.0"}
        )


def test_reject_invalid_kind(validator):
    m = _container_image_manifest(kind="bogus")
    with pytest.raises(InvalidMetadataSchemaError, match="Invalid kind"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_declarative_kind_with_steps(validator):
    h = _helm_chart_manifest(steps={"apply": [{"args": ["x"]}]})
    with pytest.raises(InvalidMetadataSchemaError, match="must not declare 'steps'"):
        validator.validate_artifact_manifest(h, registry_host_allowlist=ALLOWLIST)


def test_reject_procedural_missing_apply_steps(validator):
    m = _container_image_manifest()
    m["steps"] = {}
    with pytest.raises(InvalidMetadataSchemaError, match="steps.apply"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_supports_destroy_without_destroy_steps(validator):
    m = _container_image_manifest()
    m["lifecycle"]["supports_destroy"] = True
    with pytest.raises(InvalidMetadataSchemaError, match="steps.destroy"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


# --------------------------------------------------------------------------
# Rejected — malformed / hostile input (the manifest is a trust boundary)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("bad_version", [0, 2, "1", None])
def test_reject_unsupported_schema_version(validator, bad_version):
    m = _container_image_manifest(schema_version=bad_version)
    with pytest.raises(InvalidMetadataSchemaError, match="schema_version"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_missing_schema_version(validator):
    m = _container_image_manifest()
    del m["schema_version"]
    with pytest.raises(InvalidMetadataSchemaError, match="schema_version"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


@pytest.mark.parametrize("not_a_dict", [[], "manifest", 1, None])
def test_reject_non_dict_manifest(validator, not_a_dict):
    with pytest.raises(InvalidMetadataSchemaError, match="must be an object"):
        validator.validate_artifact_manifest(not_a_dict, registry_host_allowlist=ALLOWLIST)


def test_reject_deeply_nested_manifest(validator):
    """A nesting bomb is depth-bounded by the secret scanner, not stack-overflowed."""
    m = _container_image_manifest()
    nest: dict = {}
    node = nest
    for _ in range(50):
        node["nested"] = {}
        node = node["nested"]
    m["annotations"] = nest

    with pytest.raises(InvalidMetadataSchemaError, match="too deep"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_reject_self_referencing_artifact(validator):
    m = _container_image_manifest(references=["roksbnkctl-tools-runner@1.11.4"])
    with pytest.raises(InvalidMetadataSchemaError, match="self-reference"):
        validator.validate_artifact_manifest(
            m,
            registry_host_allowlist=ALLOWLIST,
            known_artifact_refs={"roksbnkctl-tools-runner@1.11.4"},
        )


def test_reject_self_referencing_artifact_by_bare_name(validator):
    m = _container_image_manifest(references=[{"ref": "roksbnkctl-tools-runner"}])
    with pytest.raises(InvalidMetadataSchemaError, match="self-reference"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


# --------------------------------------------------------------------------
# Actions block (D-034 — manifest-declared test/scenario actions)
# --------------------------------------------------------------------------

def _actions_block() -> dict:
    return {
        "run-scenario": {
            "title": "Run a functional scenario",
            "description": "Runs one scenario by name against the deployed cluster",
            "rating": "green",
            "steps": [
                {"name": "run", "args": ["roksbnkctl", "scenario", "run", "{{inputs.scenario}}"]},
            ],
            "inputs": [
                {"name": "scenario", "type": "string", "source": "user", "choices": ["tcpl4lb"]},
            ],
        },
    }


def test_validate_actions_block_accepted(validator):
    m = _container_image_manifest(actions=_actions_block())
    validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_validate_actions_minimal_definition_accepted(validator):
    # title + steps only: description/rating/inputs are optional.
    m = _container_image_manifest(actions={
        "e2e-verify": {"title": "Verify e2e", "steps": [{"name": "v", "args": ["roksbnkctl", "e2e"]}]},
    })
    validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_non_dict_block(validator):
    m = _container_image_manifest(actions=["run-scenario"])
    with pytest.raises(InvalidMetadataSchemaError, match="'actions' must be an object"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_invalid_action_name(validator):
    m = _container_image_manifest(actions={"Run Scenario!": _actions_block()["run-scenario"]})
    with pytest.raises(InvalidMetadataSchemaError, match="Invalid action name"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_lifecycle_phase_name_collision(validator):
    m = _container_image_manifest(actions={"apply": _actions_block()["run-scenario"]})
    with pytest.raises(InvalidMetadataSchemaError, match="collides with a lifecycle phase"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_missing_title(validator):
    m = _container_image_manifest(actions={
        "run-scenario": {"steps": [{"name": "run", "args": ["roksbnkctl", "scenario"]}]},
    })
    with pytest.raises(InvalidMetadataSchemaError, match="title"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


@pytest.mark.parametrize("bad_steps", [None, [], "roksbnkctl scenario run"])
def test_actions_reject_missing_or_empty_steps(validator, bad_steps):
    action = {"title": "Run", **({"steps": bad_steps} if bad_steps is not None else {})}
    m = _container_image_manifest(actions={"run-scenario": action})
    with pytest.raises(InvalidMetadataSchemaError, match="steps must be a non-empty list"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_invalid_rating(validator):
    actions = _actions_block()
    actions["run-scenario"]["rating"] = "red"
    m = _container_image_manifest(actions=actions)
    with pytest.raises(InvalidMetadataSchemaError, match="rating"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_shell_token_in_action_step(validator):
    actions = _actions_block()
    actions["run-scenario"]["steps"] = [{"name": "run", "args": ["bash", "-c", "echo hi"]}]
    m = _container_image_manifest(actions=actions)
    with pytest.raises(InvalidMetadataSchemaError, match="shell"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_denylisted_key_in_action_step(validator):
    actions = _actions_block()
    actions["run-scenario"]["steps"][0]["image"] = "ghcr.io/other/image@" + DIGEST
    m = _container_image_manifest(actions=actions)
    with pytest.raises(InvalidMetadataSchemaError, match="own image"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_embedded_secret_in_action_step_env(validator):
    # The manifest-wide secret scanner walks from $ — actions are covered too.
    actions = _actions_block()
    actions["run-scenario"]["steps"][0]["env"] = {"password": "hunter2"}
    m = _container_image_manifest(actions=actions)
    with pytest.raises(InvalidMetadataSchemaError, match="secret"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_invalid_inputs_shape(validator):
    actions = _actions_block()
    actions["run-scenario"]["inputs"] = [{"type": "string"}]  # missing name
    m = _container_image_manifest(actions=actions)
    with pytest.raises(InvalidMetadataSchemaError, match="inputs"):
        validator.validate_artifact_manifest(m, registry_host_allowlist=ALLOWLIST)


def test_actions_reject_on_declarative_kind(validator):
    h = _helm_chart_manifest(actions=_actions_block())
    with pytest.raises(InvalidMetadataSchemaError, match="must not declare 'actions'"):
        validator.validate_artifact_manifest(h, registry_host_allowlist=ALLOWLIST)
