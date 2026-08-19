"""Unit tests for the ContainerEngine — step resolution, templating, when-gates,
outputs capture, redaction, and substrate selection.

These use a fake runner that records the StepSpecs it is asked to run, so no
docker daemon or cluster is required.
"""

from __future__ import annotations

import json
import os

import pytest

from services.execution.container_engine import (
    ContainerEngine,
    select_runner,
)
from services.execution.container_runner import ContainerRunner, DockerRunner, StepResult, StepSpec
from services.execution.engine_interface import ModuleContext

DIGEST = "sha256:" + "a" * 64
IMAGE_REF = f"ghcr.io/jgruberf5/roksbnkctl-tools-runner@{DIGEST}"


class FakeRunner(ContainerRunner):
    """Records every StepSpec and returns a scripted result per step."""

    def __init__(self, results: list[StepResult] | None = None):
        self.specs: list[StepSpec] = []
        self._results = results or []

    def run_step(self, spec: StepSpec, on_output=None) -> StepResult:
        self.specs.append(spec)
        if on_output:
            on_output(f"ran {spec.step_name}")
        if self._results:
            return self._results.pop(0)
        return StepResult(success=True, exit_code=0, stdout="ok")


def _manifest(**overrides) -> dict:
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
        "steps": {
            "apply": [{"name": "deploy", "args": ["roksbnkctl", "apply", "--config", "/state/c.json"]}],
            "destroy": [{"name": "teardown", "args": ["roksbnkctl", "destroy"]}],
        },
    }
    base.update(overrides)
    return base


def _ctx(manifest: dict, *, variables=None) -> ModuleContext:
    return ModuleContext(
        module_id=42,
        project_id=7,
        path="artifacts/tools-runner",
        category="container",
        variables=variables or {},
        pack_manifest=manifest,
    )


def _engine(runner, tmp_path, **kw) -> ContainerEngine:
    return ContainerEngine(
        runner,
        workspace_host_path="/var/lib/docker/volumes/bnk_ws/_data/7/42",
        workspace_local_path=str(tmp_path),
        **kw,
    )


@pytest.mark.unit
class TestStepResolution:
    def test_apply_runs_declared_apply_steps_with_digest_pinned_image(self, tmp_path):
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)
        result = engine.apply(_ctx(_manifest()))
        assert result.success
        assert len(runner.specs) == 1
        assert runner.specs[0].image_digest == IMAGE_REF
        assert runner.specs[0].args == ["roksbnkctl", "apply", "--config", "/state/c.json"]

    def test_destroy_runs_declared_destroy_steps(self, tmp_path):
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)
        result = engine.destroy(_ctx(_manifest()))
        assert result.success
        assert runner.specs[0].args == ["roksbnkctl", "destroy"]

    def test_destroy_with_no_destroy_steps_is_noop_success(self, tmp_path):
        runner = FakeRunner()
        manifest = _manifest(steps={"apply": [{"name": "a", "args": ["x"]}]})
        engine = _engine(runner, tmp_path)
        result = engine.destroy(_ctx(manifest))
        assert result.success
        assert runner.specs == []

    def test_apply_without_apply_steps_fails(self, tmp_path):
        runner = FakeRunner()
        manifest = _manifest(steps={"destroy": [{"name": "d", "args": ["x"]}]})
        engine = _engine(runner, tmp_path)
        result = engine.apply(_ctx(manifest))
        assert not result.success
        assert "apply" in (result.error_message or "")

    def test_execution_steps_location_takes_precedence(self, tmp_path):
        runner = FakeRunner()
        manifest = _manifest()
        manifest["execution"] = {"steps": {"apply": [{"name": "exec", "args": ["from-exec"]}]}}
        engine = _engine(runner, tmp_path)
        engine.apply(_ctx(manifest))
        assert runner.specs[0].args == ["from-exec"]

    def test_steps_run_in_order(self, tmp_path):
        runner = FakeRunner()
        manifest = _manifest(steps={"apply": [
            {"name": "one", "args": ["a"]},
            {"name": "two", "args": ["b"]},
        ]})
        engine = _engine(runner, tmp_path)
        engine.apply(_ctx(manifest))
        assert [s.step_name for s in runner.specs] == ["one", "two"]

    def test_step_failure_halts_remaining_steps(self, tmp_path):
        runner = FakeRunner(results=[StepResult(success=False, exit_code=2, stderr="boom")])
        manifest = _manifest(steps={"apply": [
            {"name": "one", "args": ["a"]},
            {"name": "two", "args": ["b"]},
        ]})
        engine = _engine(runner, tmp_path)
        result = engine.apply(_ctx(manifest))
        assert not result.success
        assert len(runner.specs) == 1  # second step never ran


@pytest.mark.unit
class TestWhenGatesAndTemplating:
    def test_when_false_skips_step(self, tmp_path):
        runner = FakeRunner()
        manifest = _manifest(steps={"apply": [
            {"name": "skipped", "args": ["a"], "when": False},
            {"name": "run", "args": ["b"]},
        ]})
        engine = _engine(runner, tmp_path)
        engine.apply(_ctx(manifest))
        assert [s.step_name for s in runner.specs] == ["run"]

    def test_when_template_truthy_runs_step(self, tmp_path):
        runner = FakeRunner()
        manifest = _manifest(steps={"apply": [
            {"name": "gated", "args": ["a"], "when": "{{inputs.enabled}}"},
        ]})
        engine = _engine(runner, tmp_path)
        engine.apply(_ctx(manifest, variables={"enabled": "true"}))
        assert [s.step_name for s in runner.specs] == ["gated"]

    def test_when_template_falsy_skips_step(self, tmp_path):
        runner = FakeRunner()
        manifest = _manifest(steps={"apply": [
            {"name": "gated", "args": ["a"], "when": "{{inputs.enabled}}"},
        ]})
        engine = _engine(runner, tmp_path)
        engine.apply(_ctx(manifest, variables={"enabled": "false"}))
        assert runner.specs == []

    def test_inputs_templated_into_argv(self, tmp_path):
        runner = FakeRunner()
        manifest = _manifest(steps={"apply": [
            {"name": "deploy", "args": ["roksbnkctl", "--name", "{{inputs.cluster_name}}"]},
        ]})
        engine = _engine(runner, tmp_path)
        engine.apply(_ctx(manifest, variables={"cluster_name": "prod-1"}))
        assert runner.specs[0].args == ["roksbnkctl", "--name", "prod-1"]

    def test_inputs_templated_into_env(self, tmp_path):
        runner = FakeRunner()
        manifest = _manifest(steps={"apply": [
            {"name": "deploy", "args": ["x"], "env": {"REGION": "{{inputs.region}}"}},
        ]})
        engine = _engine(runner, tmp_path)
        engine.apply(_ctx(manifest, variables={"region": "us-east-1"}))
        assert runner.specs[0].env["REGION"] == "us-east-1"


@pytest.mark.unit
class TestOutputsAndRedaction:
    def test_apply_captures_and_normalizes_outputs_file(self, tmp_path):
        outputs = {"endpoint": {"value": "https://x"}, "ready": True}
        with open(os.path.join(tmp_path, "outputs.json"), "w") as f:
            json.dump(outputs, f)
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)
        result = engine.apply(_ctx(_manifest()))
        assert result.outputs == {"endpoint": "https://x", "ready": True}

    def test_missing_outputs_file_yields_empty(self, tmp_path):
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)
        result = engine.apply(_ctx(_manifest()))
        assert result.outputs == {}

    def test_secret_values_redacted_from_logs(self, tmp_path):
        captured: list[str] = []

        class EchoingRunner(ContainerRunner):
            def run_step(self, spec: StepSpec, on_output=None) -> StepResult:
                # Mirror DockerRunner: stream the artifact's stdout through the sink.
                if on_output:
                    on_output("token=SUPERSECRET")
                return StepResult(success=True, exit_code=0, stdout="token=SUPERSECRET")

        engine = _engine(EchoingRunner(), tmp_path, secret_values=["SUPERSECRET"])
        result = engine.apply(_ctx(_manifest()), on_output=captured.append)
        # The runner streamed the secret via on_output → engine sink must redact it
        # in both the forwarded callback and the engine's collected stdout.
        assert "SUPERSECRET" not in "\n".join(captured)
        assert "SUPERSECRET" not in result.stdout
        assert "***" in result.stdout


@pytest.mark.unit
class TestSubstrateSelection:
    def test_explicit_docker_backend(self):
        runner = select_runner(backend="docker", deploy_model=None)
        assert isinstance(runner, DockerRunner)

    def test_helm_deploy_model_infers_kubernetes(self):
        sentinel = object()
        runner = select_runner(
            backend=None,
            deploy_model="helm",
            runner_factory_kubernetes=lambda: sentinel,
        )
        assert runner is sentinel

    def test_compose_deploy_model_infers_docker(self):
        runner = select_runner(backend=None, deploy_model="compose")
        assert isinstance(runner, DockerRunner)

    def test_default_is_docker(self):
        runner = select_runner(backend=None, deploy_model=None)
        assert isinstance(runner, DockerRunner)

    def test_kubernetes_backend_requires_factory(self):
        with pytest.raises(ValueError):
            select_runner(backend="kubernetes", deploy_model=None)

    def test_unknown_backend_rejected(self):
        with pytest.raises(ValueError):
            select_runner(backend="podman", deploy_model=None)


@pytest.mark.unit
class TestRunOnceAndRetry:
    """run_once idempotency + retry/backoff for re-applyable long-running steps."""

    def test_run_once_step_skipped_on_reapply(self, tmp_path):
        # apply step-set mirrors roksbnkctl: a run_once `init` (aborts if the
        # workspace already exists) followed by the repeatable `cluster-up`.
        manifest = _manifest(steps={
            "apply": [
                {"name": "init", "run_once": True, "args": ["roksbnkctl", "init", "-w", "forge"]},
                {"name": "cluster-up", "args": ["roksbnkctl", "cluster", "up", "--auto"]},
            ],
        })
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)

        first = engine.apply(_ctx(manifest))
        assert first.success
        assert [s.step_name for s in runner.specs] == ["init", "cluster-up"]
        # marker persisted in the workspace
        assert os.path.isfile(os.path.join(str(tmp_path), ".bnkforge_step_init.done"))

        # Re-apply with the same persistent workspace: init is skipped.
        second = engine.apply(_ctx(manifest))
        assert second.success
        assert [s.step_name for s in runner.specs] == ["init", "cluster-up", "cluster-up"]

    def test_run_once_marker_not_written_on_failure(self, tmp_path):
        manifest = _manifest(steps={
            "apply": [{"name": "init", "run_once": True, "args": ["roksbnkctl", "init"]}],
        })
        runner = FakeRunner(results=[StepResult(success=False, exit_code=1, stderr="boom")])
        engine = _engine(runner, tmp_path)

        result = engine.apply(_ctx(manifest))
        assert not result.success
        assert not os.path.isfile(os.path.join(str(tmp_path), ".bnkforge_step_init.done"))

    def test_retry_succeeds_after_transient_failures(self, tmp_path):
        manifest = _manifest(steps={
            "apply": [{
                "name": "cluster-up",
                "args": ["roksbnkctl", "cluster", "up", "--auto"],
                "retry": {"max_attempts": 3, "backoff_seconds": 0},
            }],
        })
        runner = FakeRunner(results=[
            StepResult(success=False, exit_code=1, stderr="not ready"),
            StepResult(success=False, exit_code=1, stderr="not ready"),
            StepResult(success=True, exit_code=0, stdout="ok"),
        ])
        engine = _engine(runner, tmp_path)

        result = engine.apply(_ctx(manifest))
        assert result.success
        assert len(runner.specs) == 3  # retried twice, succeeded on the third

    def test_retry_exhausted_reports_failure(self, tmp_path):
        manifest = _manifest(steps={
            "apply": [{
                "name": "cluster-up",
                "args": ["roksbnkctl", "cluster", "up"],
                "retry": {"max_attempts": 2, "backoff_seconds": 0},
            }],
        })
        runner = FakeRunner(results=[
            StepResult(success=False, exit_code=1, stderr="not ready"),
            StepResult(success=False, exit_code=1, stderr="not ready"),
        ])
        engine = _engine(runner, tmp_path)

        result = engine.apply(_ctx(manifest))
        assert not result.success
        assert len(runner.specs) == 2
        assert "2 attempts" in (result.error_message or "")

    def test_hard_timeout_is_not_retried(self, tmp_path):
        manifest = _manifest(steps={
            "apply": [{
                "name": "cluster-up",
                "args": ["roksbnkctl", "cluster", "up"],
                "retry": {"max_attempts": 3, "backoff_seconds": 0},
            }],
        })
        runner = FakeRunner(results=[StepResult(success=False, exit_code=124, timed_out=True)])
        engine = _engine(runner, tmp_path)

        result = engine.apply(_ctx(manifest))
        assert not result.success
        assert len(runner.specs) == 1  # a step that exceeded its own timeout is not retried


@pytest.mark.unit
class TestOutputsFileResolution:
    def test_apply_reads_manifest_declared_outputs_file(self, tmp_path):
        # Artifact writes outputs to a non-default path declared in state.outputs_file.
        import os as _os
        outdir = tmp_path / ".roksbnkctl" / "forge"
        _os.makedirs(outdir, exist_ok=True)
        (outdir / "cluster-outputs.json").write_text('{"cluster_id": "d8q-123", "vpc_id": "r006-x"}')

        manifest = _manifest(
            state={"mount_path": "/work", "outputs_file": ".roksbnkctl/forge/cluster-outputs.json"},
            steps={"apply": [{"name": "cluster-up", "args": ["roksbnkctl", "cluster", "up"]}]},
        )
        engine = _engine(FakeRunner(), tmp_path)
        result = engine.apply(_ctx(manifest))
        assert result.success
        # Without the fix this is {} (default outputs.json is read instead).
        assert result.outputs == {"cluster_id": "d8q-123", "vpc_id": "r006-x"}

    def test_default_outputs_file_still_works(self, tmp_path):
        (tmp_path / "outputs.json").write_text('{"k": "v"}')
        manifest = _manifest(steps={"apply": [{"name": "deploy", "args": ["roksbnkctl", "apply"]}]})
        engine = _engine(FakeRunner(), tmp_path)
        result = engine.apply(_ctx(manifest))
        assert result.outputs == {"k": "v"}


@pytest.mark.unit
class TestRunAction:
    """run_action (D-034) — named action step-sets reuse the lifecycle machinery."""

    @staticmethod
    def _manifest_with_actions(**action_overrides) -> dict:
        action = {
            "title": "Run a functional scenario",
            "steps": [
                {"name": "run", "args": ["roksbnkctl", "scenario", "run", "{{inputs.scenario}}"]},
            ],
        }
        action.update(action_overrides)
        return _manifest(actions={"run-scenario": action})

    def test_run_action_executes_declared_steps(self, tmp_path):
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)
        result = engine.run_action(
            _ctx(self._manifest_with_actions(), variables={"scenario": "tcpl4lb"}), "run-scenario"
        )
        assert result.success
        assert runner.specs[0].image_digest == IMAGE_REF
        assert runner.specs[0].args == ["roksbnkctl", "scenario", "run", "tcpl4lb"]

    def test_run_action_invocation_inputs_override_ctx_variables(self, tmp_path):
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)
        ctx = _ctx(self._manifest_with_actions(), variables={"scenario": "from-module"})
        result = engine.run_action(ctx, "run-scenario", action_inputs={"scenario": "from-invocation"})
        assert result.success
        assert runner.specs[0].args[-1] == "from-invocation"
        # The invocation overlay must not leak back into the caller's ctx.
        assert ctx.variables == {"scenario": "from-module"}

    def test_run_action_honors_when_gate(self, tmp_path):
        runner = FakeRunner()
        manifest = self._manifest_with_actions(steps=[
            {"name": "gated", "args": ["a"], "when": "{{inputs.enabled}}"},
            {"name": "always", "args": ["b"]},
        ])
        engine = _engine(runner, tmp_path)
        engine.run_action(_ctx(manifest), "run-scenario", action_inputs={"enabled": "false"})
        assert [s.step_name for s in runner.specs] == ["always"]

    def test_run_action_unknown_action_fails_with_clear_error(self, tmp_path):
        engine = _engine(FakeRunner(), tmp_path)
        result = engine.run_action(_ctx(self._manifest_with_actions()), "no-such-action")
        assert not result.success
        assert "no-such-action" in (result.error_message or "")
        assert "run-scenario" in (result.error_message or "")  # declared actions named

    def test_run_action_no_actions_block_fails(self, tmp_path):
        engine = _engine(FakeRunner(), tmp_path)
        result = engine.run_action(_ctx(_manifest()), "run-scenario")
        assert not result.success
        assert "none" in (result.error_message or "")

    def test_run_action_does_not_capture_outputs(self, tmp_path):
        (tmp_path / "outputs.json").write_text('{"cluster_id": "d8q-123"}')
        engine = _engine(FakeRunner(), tmp_path)
        result = engine.run_action(
            _ctx(self._manifest_with_actions()), "run-scenario", action_inputs={"scenario": "x"}
        )
        assert result.success
        assert result.outputs == {}

    def test_run_action_step_failure_reports_failure(self, tmp_path):
        runner = FakeRunner(results=[StepResult(success=False, exit_code=3, stderr="scenario failed")])
        engine = _engine(runner, tmp_path)
        result = engine.run_action(
            _ctx(self._manifest_with_actions()), "run-scenario", action_inputs={"scenario": "x"}
        )
        assert not result.success
        assert "exit 3" in (result.error_message or "")


@pytest.mark.unit
class TestOutputsFileContainment:
    """state.outputs_file must not escape the workspace.

    Issue #408.2: the manifest value reached os.path.join with only a .strip(),
    so an absolute path or a ../ climb read a file outside the workspace, and its
    contents were normalized into module.outputs and shown to the user —
    disclosure of worker files such as /app/keys and /app/secrets. The manifest
    validator never checked the field.
    """

    @pytest.mark.parametrize("escape", [
        "/app/keys/id_rsa",
        "../../app/secrets/creds.json",
        "..",
        "../outputs.json",
        "/etc/passwd",
        "subdir/../../../etc/passwd",
    ])
    def test_escaping_paths_fall_back_to_the_default(self, tmp_path, escape):
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)
        ctx = _ctx(_manifest(state={"outputs_file": escape}))

        resolved = engine._resolve_outputs_filename(ctx)

        assert resolved == engine.outputs_filename, (
            f"state.outputs_file={escape!r} was accepted — it reads a file outside "
            "the workspace and surfaces it in module.outputs (#408.2)"
        )

    @pytest.mark.parametrize("legit", [
        "outputs.json",
        ".roksbnkctl/forge/cluster-outputs.json",
        "./nested/out.json",
        "a/b/c/outputs.json",
    ])
    def test_workspace_relative_paths_are_still_honoured(self, tmp_path, legit):
        """Contrast: the shipped artifacts declare nested relative paths."""
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)
        ctx = _ctx(_manifest(state={"outputs_file": legit}))

        assert engine._resolve_outputs_filename(ctx) == legit

    def test_absent_or_blank_still_uses_the_default(self, tmp_path):
        runner = FakeRunner()
        engine = _engine(runner, tmp_path)
        assert engine._resolve_outputs_filename(_ctx(_manifest())) == engine.outputs_filename
        assert engine._resolve_outputs_filename(
            _ctx(_manifest(state={"outputs_file": "   "}))
        ) == engine.outputs_filename
