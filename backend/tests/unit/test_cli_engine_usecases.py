"""
Unit tests for Phase 2 use-case runner additions.

Coverage:
  - resolve_usecase_commands: all 4 presets, individual demo, individual scenario,
    individual amber scenario, unknown name → ValueError
  - cli_tasks._build_cli_context gating: use-cases action never gets cluster_yaml;
    cluster module path does get cluster_yaml
  - Seeder: CLI_BNKCTL_MODULES contains the new bnk-demo-usecases module with
    required schema keys and correct dependencies_metadata
"""

import pytest

from services.execution.cli_engine import _DEMO_NAMES, _SCENARIO_NAMES, resolve_usecase_commands

# ── resolve_usecase_commands ──────────────────────────────────────────────────

def test_resolve_all_green_returns_demo_all():
    """all-green preset maps to [("demo","--all")]."""
    result = resolve_usecase_commands("all-green")
    assert result == [("demo", "--all")]


def test_resolve_all_demos_returns_four_demos():
    """all-demos preset returns all 4 demos in the specified order."""
    result = resolve_usecase_commands("all-demos")
    assert result == [
        ("demo", "http2"),
        ("demo", "diameter"),
        ("demo", "ingress-migration"),
        ("demo", "bigip-cis"),
    ]


def test_resolve_all_returns_demo_all_plus_three_amber():
    """all preset returns demo --all followed by 3 amber scenarios."""
    result = resolve_usecase_commands("all")
    assert result == [
        ("demo", "--all"),
        ("scenarios", "egress-snat"),
        ("scenarios", "ai-token-counting"),
        ("scenarios", "ai-semantic-cache"),
    ]
    assert len(result) == 4


def test_resolve_all_demos_contains_exactly_four_entries():
    """all-demos must have exactly 4 entries matching the authoritative demo list."""
    result = resolve_usecase_commands("all-demos")
    assert len(result) == 4
    assert {target for _, target in result} == _DEMO_NAMES


def test_resolve_individual_demo_name_uses_demo_verb():
    """A known demo name routes to ("demo", name)."""
    result = resolve_usecase_commands("http2")
    assert result == [("demo", "http2")]


def test_resolve_individual_demo_name_diameter():
    result = resolve_usecase_commands("diameter")
    assert result == [("demo", "diameter")]


def test_resolve_individual_demo_name_ingress_migration():
    result = resolve_usecase_commands("ingress-migration")
    assert result == [("demo", "ingress-migration")]


def test_resolve_individual_demo_name_bigip_cis():
    result = resolve_usecase_commands("bigip-cis")
    assert result == [("demo", "bigip-cis")]


def test_resolve_individual_green_scenario_uses_scenarios_verb():
    """A known green scenario name routes to ("scenarios", name)."""
    result = resolve_usecase_commands("http-routing-e2e")
    assert result == [("scenarios", "http-routing-e2e")]


def test_resolve_individual_amber_scenario_uses_scenarios_verb():
    """An amber scenario name also routes to ("scenarios", name)."""
    result = resolve_usecase_commands("egress-snat")
    assert result == [("scenarios", "egress-snat")]


def test_resolve_another_amber_scenario():
    result = resolve_usecase_commands("ai-token-counting")
    assert result == [("scenarios", "ai-token-counting")]


def test_resolve_unknown_name_raises_value_error():
    """An unknown name must raise ValueError with a helpful message."""
    with pytest.raises(ValueError, match="Unknown use-case selector"):
        resolve_usecase_commands("not-a-real-usecase")


def test_resolve_none_literal_returns_empty_list():
    """'none' is the skip selector — returns an empty list, does not raise."""
    assert resolve_usecase_commands("none") == []


def test_resolve_empty_string_returns_empty_list():
    """Empty string is treated as 'none' (skip) — returns [], does not raise."""
    assert resolve_usecase_commands("") == []


def test_resolve_none_value_returns_empty_list():
    """Python None is treated as 'none' (skip) — returns [], does not raise."""
    assert resolve_usecase_commands(None) == []


def test_resolve_unknown_name_still_raises_value_error():
    """An unknown name (that is not 'none'/empty) must raise ValueError."""
    with pytest.raises(ValueError, match="Unknown use-case selector"):
        resolve_usecase_commands("not-a-real-usecase")


def test_resolve_all_scenarios_have_scenarios_verb():
    """Every scenario name individually routes to ("scenarios", name)."""
    for name in _SCENARIO_NAMES:
        result = resolve_usecase_commands(name)
        assert len(result) == 1
        verb, target = result[0]
        assert verb == "scenarios", f"Expected 'scenarios' verb for {name!r}, got {verb!r}"
        assert target == name


def test_resolve_all_demos_have_demo_verb():
    """Every demo name individually routes to ("demo", name)."""
    for name in _DEMO_NAMES:
        result = resolve_usecase_commands(name)
        assert len(result) == 1
        verb, target = result[0]
        assert verb == "demo", f"Expected 'demo' verb for {name!r}, got {verb!r}"
        assert target == name


# ── cli_tasks gating ──────────────────────────────────────────────────────────
# We test the gating predicate logic directly — the predicate that gates whether
# cluster_yaml gets set is: module_path.startswith("cli-bnkctl/") AND
# "cluster_yaml" not in variables AND variables.get("bnkctl_action") != "demo-usecases"
# This is pure boolean logic; no ORM needed.

def _should_render_cluster_yaml(module_path: str, variables: dict) -> bool:
    """Mirror the gating predicate from _build_cli_context."""
    return (
        module_path.startswith("cli-bnkctl/")
        and "cluster_yaml" not in variables
        and variables.get("bnkctl_action") != "demo-usecases"
    )


def test_cluster_module_should_render_cluster_yaml():
    """A standard cli-bnkctl cluster module without existing cluster_yaml triggers rendering."""
    assert _should_render_cluster_yaml(
        "cli-bnkctl/awsbnkctl/bnk-demo",
        {"cluster_name": "test"},
    ) is True


def test_usecases_action_should_not_render_cluster_yaml():
    """When bnkctl_action == demo-usecases, cluster_yaml must not be rendered."""
    assert _should_render_cluster_yaml(
        "cli-bnkctl/awsbnkctl/bnk-demo-usecases",
        {"bnkctl_action": "demo-usecases", "cluster_name": "test"},
    ) is False


def test_prerendered_cluster_yaml_should_not_re_render():
    """If cluster_yaml is already in variables, the predicate returns False (no overwrite)."""
    assert _should_render_cluster_yaml(
        "cli-bnkctl/awsbnkctl/bnk-demo",
        {"cluster_yaml": "apiVersion: ...", "cluster_name": "test"},
    ) is False


def test_non_cli_module_should_not_render():
    """A non-cli-bnkctl module path must not trigger cluster_yaml rendering."""
    assert _should_render_cluster_yaml(
        "k8s/some-module",
        {"cluster_name": "test"},
    ) is False


def test_usecases_with_cluster_action_should_render():
    """If bnkctl_action is 'cluster' (or absent), the predicate is True."""
    assert _should_render_cluster_yaml(
        "cli-bnkctl/awsbnkctl/bnk-demo",
        {"bnkctl_action": "cluster"},
    ) is True

    assert _should_render_cluster_yaml(
        "cli-bnkctl/awsbnkctl/bnk-demo",
        {},
    ) is True


# ── "none" skip short-circuit ─────────────────────────────────────────────────
# Verify that plan/apply return success without requiring cluster.yaml or DEMO_MODE
# when usecases="none".

def _make_ctx(usecases: str = "none"):
    """Build a minimal ModuleContext-like object for skip tests."""
    from unittest.mock import MagicMock
    ctx = MagicMock()
    ctx.variables = {"usecases": usecases, "bnkctl_action": "demo-usecases"}
    ctx.project_id = 1
    ctx.module_id = 1
    ctx.credentials_env = {}
    return ctx


def test_plan_usecases_skip_when_none(tmp_path):
    """_plan_usecases with usecases='none' returns has_changes=False without touching disk."""
    from services.execution.cli_engine import BnkctlEngine

    engine = BnkctlEngine()
    engine._WORKSPACE_ROOT = str(tmp_path)

    ctx = _make_ctx("none")
    result = engine._plan_usecases(ctx, on_output=None)

    assert result.has_changes is False
    assert "skipped" in result.details.lower()
    # No cluster.yaml was written or required
    assert not (tmp_path / "cluster.yaml").exists()


def test_apply_usecases_skip_when_none(tmp_path):
    """_apply_usecases with usecases='none' returns success without touching disk."""
    from services.execution.cli_engine import BnkctlEngine

    engine = BnkctlEngine()
    engine._WORKSPACE_ROOT = str(tmp_path)

    ctx = _make_ctx("none")
    result = engine._apply_usecases(ctx, on_output=None)

    assert result.success is True
    assert "skipped" in (result.stdout or "").lower()


# ── Seeder presence ───────────────────────────────────────────────────────────

def test_seeder_contains_usecases_module():
    """CLI_BNKCTL_MODULES must include the new bnk-demo-usecases module."""
    from services.cli_bnkctl_module_seeder import CLI_BNKCTL_MODULES

    paths = [m["path"] for m in CLI_BNKCTL_MODULES]
    assert "cli-bnkctl/awsbnkctl/bnk-demo-usecases" in paths


def test_seeder_usecases_module_has_required_keys():
    """The bnk-demo-usecases module dict must have the keys the seed loop writes."""
    from services.cli_bnkctl_module_seeder import CLI_BNKCTL_MODULES

    module = next(
        m for m in CLI_BNKCTL_MODULES
        if m["path"] == "cli-bnkctl/awsbnkctl/bnk-demo-usecases"
    )

    assert module["name"] == "AWS BNK Demo Use-Cases (CLI Run)"
    assert module["execution_engine"] == "cli-bnkctl"
    assert module["deploy_model"] == "cli-exec"
    assert module["module_source_kind"] == "builtin"
    assert module["is_official"] is True
    assert module["is_active"] is True


def test_seeder_usecases_module_has_correct_dependencies():
    """bnk-demo-usecases must declare bnk-demo as a required dependency."""
    from services.cli_bnkctl_module_seeder import CLI_BNKCTL_MODULES

    module = next(
        m for m in CLI_BNKCTL_MODULES
        if m["path"] == "cli-bnkctl/awsbnkctl/bnk-demo-usecases"
    )

    deps = module.get("dependencies_metadata", {})
    required = deps.get("required", [])
    assert any(
        isinstance(dep, dict) and dep.get("module") == "cli-bnkctl/awsbnkctl/bnk-demo"
        for dep in required
    )


def test_seeder_usecases_module_variables_schema():
    """variables_schema must include usecases (default none); bnkctl_action/bnkctl_tool
    are internal and must NOT be in the schema (they are supplied as blueprint inputs)."""
    from services.cli_bnkctl_module_seeder import CLI_BNKCTL_MODULES

    module = next(
        m for m in CLI_BNKCTL_MODULES
        if m["path"] == "cli-bnkctl/awsbnkctl/bnk-demo-usecases"
    )

    schema = {entry["name"]: entry for entry in module["variables_schema"]}

    assert "usecases" in schema
    assert schema["usecases"]["default"] == "none"
    assert schema["usecases"]["required"] is False

    # bnkctl_action and bnkctl_tool are internal — supplied as literal blueprint inputs,
    # must NOT surface as user-facing deploy-dialog inputs.
    assert "bnkctl_action" not in schema, "bnkctl_action must not be in variables_schema"
    assert "bnkctl_tool" not in schema, "bnkctl_tool must not be in variables_schema"


def test_seeder_module_count_is_two():
    """There should be exactly two modules in the seeder list."""
    from services.cli_bnkctl_module_seeder import CLI_BNKCTL_MODULES

    assert len(CLI_BNKCTL_MODULES) == 2
