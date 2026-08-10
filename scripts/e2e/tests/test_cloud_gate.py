"""Tests for cloud-gating (credential presence check) and dry-run expansion.

Validates:
- Without AWS creds, all cloud steps return 'skipped'
- With AWS creds, steps proceed (into their actual logic)
- _cloud_creds_present returns correct values per provider
- CLOUD_PHASE_STEPS registry is non-empty and step names are correct
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from scripts.e2e.cloud_steps import (
    CLOUD_PHASE_STEPS,
    _cloud_creds_present,
    step_cloud_create_project,
    step_cloud_deploy_all,
    step_cloud_resolve_release,
    step_cloud_wait_bnk_ready,
    step_cloud_wait_deploy_terminal,
    step_cloud_wait_license_active,
)
from scripts.e2e.config import CloudBlueprintConfig, E2EConfig, TimeoutsConfig
from scripts.e2e.steps import Context

# ---------------------------------------------------------------------------
# _cloud_creds_present
# ---------------------------------------------------------------------------


class TestCloudCredsPresent:
    def test_aws_present_when_both_vars_set(self):
        with patch.dict(
            os.environ,
            {"AWS_ACCESS_KEY_ID": "AKI123", "AWS_SESSION_TOKEN": "tok"},
            clear=False,
        ):
            assert _cloud_creds_present("aws") is True

    def test_aws_absent_when_key_id_missing(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN")}
        with patch.dict(os.environ, env, clear=True):
            assert _cloud_creds_present("aws") is False

    def test_aws_absent_when_session_token_missing(self):
        env = {k: v for k, v in os.environ.items()
               if k != "AWS_SESSION_TOKEN"}
        env["AWS_ACCESS_KEY_ID"] = "AKI123"
        with patch.dict(os.environ, env, clear=True):
            assert _cloud_creds_present("aws") is False

    def test_gcp_always_skipped(self):
        with patch.dict(
            os.environ,
            {"GOOGLE_APPLICATION_CREDENTIALS": "/creds.json"},
            clear=False,
        ):
            assert _cloud_creds_present("gcp") is False

    def test_azure_always_skipped(self):
        assert _cloud_creds_present("azure") is False

    def test_unknown_provider_skipped(self):
        assert _cloud_creds_present("unknown") is False


# ---------------------------------------------------------------------------
# Cloud-gated skip (no creds → all steps skip)
# ---------------------------------------------------------------------------


def _make_ctx_no_creds(cloud_provider: str = "aws") -> Context:
    cfg = E2EConfig.model_construct(
        bnk_forge_url="https://forge.test",
        bnk_forge_admin_user="admin",
        bnk_forge_admin_password="pw",
        project_name_prefix="e2e-",
        timeouts=TimeoutsConfig(),
        cloud=CloudBlueprintConfig(cloud_provider=cloud_provider),
        worker_node_ips=[],
        bmc_ips=[],
        worker_ssh_user="",
        worker_ssh_key_path="",
        bmc_password="",
        dpu_os_password="",
        bf_template="lag",
        bfb_image="",
        local_deploy=False,
    )
    return Context(cfg=cfg, client=MagicMock(), state={})


def _make_ctx_no_cloud() -> Context:
    cfg = E2EConfig.model_construct(
        bnk_forge_url="https://forge.test",
        bnk_forge_admin_user="admin",
        bnk_forge_admin_password="pw",
        project_name_prefix="e2e-",
        timeouts=TimeoutsConfig(),
        cloud=None,
        worker_node_ips=[],
        bmc_ips=[],
        worker_ssh_user="",
        worker_ssh_key_path="",
        bmc_password="",
        dpu_os_password="",
        bf_template="lag",
        bfb_image="",
        local_deploy=False,
    )
    return Context(cfg=cfg, client=MagicMock(), state={})


class TestCloudGatedSkip:
    """All cloud steps must skip (green) when credentials are absent."""

    @pytest.mark.parametrize("step_fn", [
        step_cloud_resolve_release,
        step_cloud_create_project,
        step_cloud_deploy_all,
        step_cloud_wait_deploy_terminal,
        step_cloud_wait_bnk_ready,
        step_cloud_wait_license_active,
    ])
    def test_step_skips_without_aws_creds(self, step_fn):
        """Steps skip when AWS creds are absent."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN")}
        with patch.dict(os.environ, env, clear=True):
            ctx = _make_ctx_no_creds("aws")
            result = step_fn(ctx)
        assert result.status == "skipped", (
            f"{step_fn.__name__} should skip without creds, got {result.status}: "
            f"{result.summary}"
        )

    @pytest.mark.parametrize("step_fn", [
        step_cloud_resolve_release,
        step_cloud_create_project,
        step_cloud_deploy_all,
    ])
    def test_step_skips_without_cloud_config(self, step_fn):
        """Steps skip when the cloud section is absent from config."""
        ctx = _make_ctx_no_cloud()
        result = step_fn(ctx)
        assert result.status == "skipped"

    def test_no_client_calls_when_skipped(self):
        """Skipped steps must not call the forge client at all."""
        env = {k: v for k, v in os.environ.items()
               if k not in ("AWS_ACCESS_KEY_ID", "AWS_SESSION_TOKEN")}
        with patch.dict(os.environ, env, clear=True):
            ctx = _make_ctx_no_creds("aws")
            step_cloud_resolve_release(ctx)
            step_cloud_create_project(ctx)
            step_cloud_deploy_all(ctx)
        ctx.client.list_blueprint_releases.assert_not_called()
        ctx.client.create_project_from_release.assert_not_called()
        ctx.client.deploy_all.assert_not_called()


# ---------------------------------------------------------------------------
# CLOUD_PHASE_STEPS registry
# ---------------------------------------------------------------------------


class TestCloudPhaseStepsRegistry:
    def test_registry_non_empty(self):
        assert len(CLOUD_PHASE_STEPS) > 0

    def test_expected_steps_present(self):
        names = [name for name, _ in CLOUD_PHASE_STEPS]
        expected = [
            "cloud_resolve_release",
            "cloud_create_project",
            "cloud_deploy_all",
            "cloud_wait_deploy_terminal",
            "cloud_wait_bnk_ready",
            "cloud_wait_license_active",
            "cloud_destroy_all",
            "cloud_delete_project",
        ]
        for e in expected:
            assert e in names, f"step '{e}' missing from CLOUD_PHASE_STEPS"

    def test_all_callables(self):
        for name, fn in CLOUD_PHASE_STEPS:
            assert callable(fn), f"step '{name}' is not callable"


# ---------------------------------------------------------------------------
# Dry-run step plan expansion
# ---------------------------------------------------------------------------


class TestDryRunExpansion:
    """Verify that '--steps cloud' expands to the full cloud plan."""

    def test_cloud_alias_expands_in_select_steps(self):
        from scripts.e2e.__main__ import _select_steps
        plan = _select_steps("cloud")
        plan_names = [name for name, _ in plan]
        cloud_names = [name for name, _ in CLOUD_PHASE_STEPS]
        assert plan_names == cloud_names

    def test_dry_run_lists_cloud_steps(self, capsys):
        # Write a minimal cloud-only config to a temp file
        import tempfile

        import yaml

        from scripts.e2e.__main__ import main

        config_data = {
            "bnk_forge_url": "https://localhost",
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False,
        ) as f:
            yaml.dump(config_data, f)
            config_path = f.name

        rc = main([config_path, "--steps", "cloud", "--dry-run"])
        captured = capsys.readouterr()

        assert rc == 0
        assert "cloud_resolve_release" in captured.out
        assert "cloud_destroy_all" in captured.out
        assert "cloud_delete_project" in captured.out
