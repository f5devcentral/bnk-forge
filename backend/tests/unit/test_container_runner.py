"""Unit tests for the DockerRunner — argv construction + step execution.

These tests mock subprocess and never require a live docker daemon. They lock
the two behaviors the phase cares about:
  1. docker run argv construction (digest pin, mount, env, limits, security).
  2. run_step result mapping (success / failure / timeout) + authfile cleanup.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from services.execution.container_runner import (
    DockerRunner,
    ResourceLimits,
    StepSpec,
)

DIGEST = "ghcr.io/jgruberf5/roksbnkctl-tools-runner@sha256:" + ("a" * 64)


def _spec(**overrides) -> StepSpec:
    base = dict(
        image_digest=DIGEST,
        args=["roksbnkctl", "apply"],
        workspace_host_path="/var/lib/docker/volumes/bnk-forge_workspace_data/_data/7/42",
        mount_path="/state",
    )
    base.update(overrides)
    return StepSpec(**base)


@pytest.mark.unit
class TestBuildRunArgv:
    def test_argv_uses_digest_pinned_image_as_final_image_token(self):
        runner = DockerRunner(docker_host="tcp://docker-socket-proxy:2375")
        argv = runner.build_run_argv(_spec())
        # args[0] becomes the entrypoint so the argv runs as the literal command
        # (not appended to the image ENTRYPOINT); args[1:] follow the image.
        assert argv[argv.index("--entrypoint") + 1] == "roksbnkctl"
        assert DIGEST in argv
        idx = argv.index(DIGEST)
        assert argv[idx + 1 :] == ["apply"]

    def test_argv_runs_with_rm_and_no_new_privileges(self):
        runner = DockerRunner()
        argv = runner.build_run_argv(_spec())
        assert argv[0] == "docker"
        assert "run" in argv
        assert "--rm" in argv
        assert "no-new-privileges" in argv

    def test_argv_drops_all_capabilities(self):
        # Mirrors the KubernetesRunner security context (capabilities.drop=[ALL]).
        runner = DockerRunner()
        argv = runner.build_run_argv(_spec())
        assert argv[argv.index("--cap-drop") + 1] == "ALL"

    def test_argv_attaches_the_dedicated_artifact_network_by_default(self):
        # Not the daemon default bridge: artifact steps get their own network.
        # (`--network none` is not an option — artifacts need cloud egress.)
        runner = DockerRunner()
        argv = runner.build_run_argv(_spec())
        assert argv[argv.index("--network") + 1] == "bnk-forge-artifacts"

    def test_argv_honors_an_explicit_network(self):
        runner = DockerRunner(network="custom-net")
        argv = runner.build_run_argv(_spec())
        assert argv[argv.index("--network") + 1] == "custom-net"

    def test_empty_network_opts_out_of_the_flag(self):
        # CONTAINER_ARTIFACT_NETWORK="" → daemon default, no --network emitted.
        runner = DockerRunner(network="")
        argv = runner.build_run_argv(_spec())
        assert "--network" not in argv

    def test_argv_binds_host_workspace_to_mount_path_and_sets_workdir(self):
        # Host-path bind fallback (no workspace_volume → WORKSPACE_HOST_BASE layout).
        runner = DockerRunner()
        spec = _spec(
            workspace_host_path="/hostpath/7/42",
            mount_path="/state",
        )
        argv = runner.build_run_argv(spec)
        assert "-v" in argv
        v_idx = argv.index("-v")
        assert argv[v_idx + 1] == "/hostpath/7/42:/state"
        w_idx = argv.index("-w")
        assert argv[w_idx + 1] == "/state"

    def test_argv_mounts_named_volume_subpath_when_set(self):
        # Preferred path: mount the named volume by name + per-component subpath
        # (shares storage with the worker; correct on Docker Desktop). No -v bind.
        runner = DockerRunner()
        spec = _spec(
            workspace_volume="bnk-forge_workspace_data",
            workspace_subpath="7/42",
            mount_path="/state",
        )
        argv = runner.build_run_argv(spec)
        assert "-v" not in argv
        mount_idx = argv.index("--mount")
        assert argv[mount_idx + 1] == (
            "type=volume,source=bnk-forge_workspace_data,target=/state,volume-subpath=7/42"
        )
        assert argv[argv.index("-w") + 1] == "/state"

    def test_argv_includes_resource_limits_when_set(self):
        runner = DockerRunner()
        spec = _spec(limits=ResourceLimits(cpus="1.5", memory="512m", pids=128))
        argv = runner.build_run_argv(spec)
        assert argv[argv.index("--cpus") + 1] == "1.5"
        assert argv[argv.index("--memory") + 1] == "512m"
        assert argv[argv.index("--pids-limit") + 1] == "128"

    def test_argv_omits_limit_flags_when_unset(self):
        runner = DockerRunner()
        argv = runner.build_run_argv(_spec())
        assert "--cpus" not in argv
        assert "--memory" not in argv
        assert "--pids-limit" not in argv

    def test_argv_passes_home_env_and_step_env_as_e_flags(self):
        runner = DockerRunner()
        spec = _spec(
            home_env={"HOME": "/state"},
            env={"IBMCLOUD_API_KEY": "shh"},
        )
        argv = runner.build_run_argv(spec)
        assert "-e" in argv
        joined = " ".join(argv)
        assert "HOME=/state" in joined
        assert "IBMCLOUD_API_KEY=shh" in joined

    def test_argv_adds_config_dir_when_authfile_present(self):
        runner = DockerRunner()
        argv = runner.build_run_argv(_spec(), authfile_dir="/tmp/.bnk_docker_auth_x")
        assert argv[argv.index("--config") + 1] == "/tmp/.bnk_docker_auth_x"

    def test_floating_tag_image_is_rejected(self):
        runner = DockerRunner()
        spec = _spec(image_digest="ghcr.io/jgruberf5/roksbnkctl-tools-runner:1.11.4")
        with pytest.raises(ValueError, match="digest-pinned"):
            runner.build_run_argv(spec)

    def test_empty_args_rejected(self):
        runner = DockerRunner()
        with pytest.raises(ValueError, match="non-empty argv"):
            runner.build_run_argv(_spec(args=[]))

    def test_invalid_env_key_rejected(self):
        runner = DockerRunner()
        with pytest.raises(ValueError, match="environment variable name"):
            runner.build_run_argv(_spec(env={"bad-key": "v"}))

    def test_relative_mount_path_rejected(self):
        runner = DockerRunner()
        with pytest.raises(ValueError, match="absolute path"):
            runner.build_run_argv(_spec(mount_path="state"))


class _FakePopen:
    """subprocess.Popen stand-in: stdout yields the given lines; kill() unblocks a
    blocking stdout so the watchdog-timeout path can be exercised deterministically."""

    def __init__(self, lines, returncode=0, block=False):
        import threading as _t

        self.returncode = returncode
        self.kill_count = 0
        self._killed = _t.Event()
        if block:
            def _gen():
                self._killed.wait(timeout=5)
                return
                yield  # pragma: no cover - makes this a generator
            self.stdout = _gen()
        else:
            self.stdout = iter(lines)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.kill_count += 1
        self.returncode = -9
        self._killed.set()


def _gate(image_user: str = "1000", pull_rc: int = 0, inspect_rc: int = 0):
    """Patch the pre-run pull + image-user inspect that run_step performs.

    run_step pulls and vets the image (non-root gate) via subprocess.run before
    starting the container, so every run_step test has to stand that in.
    """
    def _fake_run(argv, **kwargs):
        if "pull" in argv:
            return MagicMock(returncode=pull_rc, stdout="", stderr="pull failed")
        return MagicMock(returncode=inspect_rc, stdout=image_user + "\n", stderr="")

    return patch("subprocess.run", side_effect=_fake_run)


@pytest.mark.unit
class TestNonRootGate:
    """Mirror of the KubernetesRunner's runAsNonRoot: a root image is refused,
    not remapped. (Docker's --user would override the image's USER and break
    state writes to the workspace, so rejecting is the only faithful analogue.)"""

    @pytest.mark.parametrize("user", ["", "0", "root", "0:0", "root:root", "  ROOT  "])
    def test_root_users_are_detected(self, user):
        assert DockerRunner.is_root_user(user) is True

    @pytest.mark.parametrize("user", ["1000", "nonroot", "1000:1000", "app"])
    def test_non_root_users_pass(self, user):
        assert DockerRunner.is_root_user(user) is False

    def test_run_step_refuses_a_root_image_and_never_starts_it(self):
        runner = DockerRunner()
        with _gate(image_user=""), patch("subprocess.Popen") as popen:
            result = runner.run_step(_spec())
        assert result.success is False
        assert result.exit_code == 126
        assert "runs as root" in result.stdout
        popen.assert_not_called()  # the container must never start

    def test_run_step_runs_a_non_root_image(self):
        runner = DockerRunner()
        with _gate(image_user="1000"), patch(
            "subprocess.Popen", return_value=_FakePopen(["ok\n"], returncode=0)
        ) as popen:
            result = runner.run_step(_spec())
        assert result.success is True
        popen.assert_called_once()

    def test_failed_pull_fails_closed(self):
        runner = DockerRunner()
        with _gate(pull_rc=1), patch("subprocess.Popen") as popen:
            result = runner.run_step(_spec())
        assert result.success is False
        assert "Failed to pull" in result.stdout
        popen.assert_not_called()

    def test_unreadable_image_user_fails_closed(self):
        # If we cannot prove the image is non-root, we do not run it.
        runner = DockerRunner()
        with _gate(inspect_rc=1), patch("subprocess.Popen") as popen:
            result = runner.run_step(_spec())
        assert result.success is False
        assert "Could not read the image's USER" in result.stdout
        popen.assert_not_called()

    def test_pull_is_digest_pinned_and_uses_the_authfile_config_dir(self):
        runner = DockerRunner()
        argv = runner.build_pull_argv(_spec(), authfile_dir="/tmp/auth-x")
        assert argv[:4] == ["docker", "--config", "/tmp/auth-x", "pull"]
        assert argv[-1] == DIGEST


@pytest.mark.unit
class TestRunStep:
    def test_run_step_streams_lines_and_maps_exit_zero(self):
        runner = DockerRunner()
        captured: list[str] = []
        fake = _FakePopen(["line 1\n", "line 2\n"], returncode=0)
        with _gate(), patch("subprocess.Popen", return_value=fake) as mock_popen:
            result = runner.run_step(_spec(), on_output=captured.append)
        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "line 1\nline 2\n"
        # Each line is delivered as its own callback (live), not one buffer at the end.
        assert "line 1" in captured and "line 2" in captured
        _, kwargs = mock_popen.call_args
        assert kwargs["env"]["DOCKER_HOST"] == runner.docker_host
        assert kwargs["stderr"] == subprocess.STDOUT  # merged for ordering

    def test_run_step_failure_maps_nonzero_exit(self):
        runner = DockerRunner()
        fake = _FakePopen(["boom\n"], returncode=2)
        with _gate(), patch("subprocess.Popen", return_value=fake):
            result = runner.run_step(_spec())
        assert result.success is False
        assert result.exit_code == 2
        assert "boom" in result.stdout

    def test_run_step_timeout_kills_and_returns_124(self):
        runner = DockerRunner()
        fake = _FakePopen([], block=True)
        with _gate(), patch("subprocess.Popen", return_value=fake):
            result = runner.run_step(_spec(timeout_seconds=1))
        assert result.success is False
        assert result.timed_out is True
        assert result.exit_code == 124
        assert fake.kill_count >= 1

    def test_run_step_writes_and_cleans_up_transient_authfile(self, tmp_path):
        runner = DockerRunner()
        captured = {}

        def fake_popen(argv, **kwargs):
            # The --config dir must exist with a config.json during the run.
            cfg_idx = argv.index("--config")
            cfg_dir = argv[cfg_idx + 1]
            captured["cfg_dir"] = cfg_dir
            import os

            assert os.path.isfile(os.path.join(cfg_dir, "config.json"))
            return _FakePopen([""], returncode=0)

        authjson = '{"auths": {"ghcr.io": {"auth": "dGVzdA=="}}}'
        with _gate(), patch("subprocess.Popen", side_effect=fake_popen):
            result = runner.run_step(_spec(pull_authfile_json=authjson))

        import os

        assert result.success is True
        # Cleaned up after the run.
        assert not os.path.exists(captured["cfg_dir"])
