"""Unit tests for the DockerRunner — argv construction + step execution.

These tests mock subprocess and never require a live docker daemon. They lock
the two behaviors the phase cares about:
  1. docker run argv construction (digest pin, mount, env, limits, security).
  2. run_step result mapping (success / failure / timeout) + authfile cleanup.
"""

import os
import subprocess
import threading
import time
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

# Captured before any test patches time.sleep out from under the poll loop.
_real_sleep = time.sleep

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


class _FakeFollow:
    """Stand-in for the ``docker logs --follow`` child the streamer thread runs.

    Sets ``drained`` once its lines have been consumed, so a test can hold the
    container "running" until the live output has actually been delivered —
    otherwise the state poll could see the container stop before the streamer
    thread is ever scheduled, and the assertions would race.
    """

    def __init__(self, lines=(), timestamped=True):
        self.returncode = 0
        self.kill_count = 0
        self.drained = threading.Event()

        def _gen():
            # --timestamps is always on, so the daemon prefixes every line with
            # an RFC3339 stamp the runner strips before emitting.
            for i, line in enumerate(lines):
                yield f"2026-08-05T10:30:{i:02d}.000000000Z {line}" if timestamped else line
            self.drained.set()

        self.stdout = _gen()

    def kill(self):
        self.kill_count += 1

    def wait(self, timeout=None):
        # Only an *attached* run would wait on this child. Supported so that a
        # regression to the attached form fails on the assertion that names the
        # problem, rather than on a missing attribute here.
        return self.returncode


class _FakeDocker:
    """Stand-in for every ``docker`` call one step makes.

    Detached execution issues a handful of short calls — pull, image inspect,
    the detached run, state polls, logs, rm — instead of a single attached
    ``docker run``, so a test stands in a whole docker rather than one
    subprocess. The state poll reports "running" until the follow has drained.
    """

    def __init__(self, image_user="1000", pull_rc=0, inspect_rc=0, run_rc=0,
                 exit_code=0, logs="", follow=None, follows=None,
                 stays_running=False, on_run=None):
        self.image_user = image_user
        self.pull_rc = pull_rc
        self.inspect_rc = inspect_rc
        self.run_rc = run_rc
        self.exit_code = exit_code
        self.logs = logs
        self.follow = follow if follow is not None else _FakeFollow()
        # A script of successive follow attempts, consumed one per Popen. An
        # exception entry is raised, standing in for a follow that cannot be
        # restarted. While the script has entries left the container reports
        # "running", so the poll can never outrun the streamer thread.
        self.follows = list(follows) if follows else []
        self.follow_dead = threading.Event()
        # `docker ps` output for the pre-step workspace sweep, and an optional
        # side effect for `docker logs` so a failing catch-up read is testable.
        self.ps_result = ""
        self.logs_side_effect = None
        self.stays_running = stays_running
        self.on_run = on_run
        self.calls: list[tuple[list[str], dict]] = []

    def run(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if "ps" in argv:
            return MagicMock(returncode=0, stdout=self.ps_result, stderr="")
        if "kill" in argv:
            return MagicMock(returncode=0, stdout="", stderr="")
        if "logs" in argv and self.logs_side_effect is not None:
            return self.logs_side_effect(argv, **kwargs)
        if "pull" in argv:
            return MagicMock(returncode=self.pull_rc, stdout="", stderr="pull failed")
        if "image" in argv:                       # image inspect → the non-root gate
            return MagicMock(returncode=self.inspect_rc, stdout=self.image_user + "\n", stderr="")
        if "run" in argv:
            if self.on_run:
                self.on_run(argv)
            return MagicMock(returncode=self.run_rc, stdout="deadbeef\n",
                             stderr="" if self.run_rc == 0 else "no such image")
        if "inspect" in argv:                     # the state poll
            if self.follows:                      # the follow script has more to do
                running = True
            else:
                running = self.stays_running or not (
                    self.follow_dead.is_set() or self.follow.drained.is_set()
                )
            return MagicMock(returncode=0,
                             stdout=f"{'true' if running else 'false'} {self.exit_code}\n",
                             stderr="")
        if "logs" in argv:
            return MagicMock(returncode=0, stdout=self.logs, stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")   # kill / rm

    def popen(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        nxt = self.follows.pop(0) if self.follows else self.follow
        if isinstance(nxt, BaseException):
            self.follow_dead.set()
            raise nxt
        return nxt

    def argvs(self, verb: str) -> list[list[str]]:
        """Every recorded argv containing ``verb``, in order."""
        return [a for a, _ in self.calls if verb in a]

    def argv(self, verb: str) -> list[str]:
        """The first recorded argv containing ``verb``."""
        return next(a for a, _ in self.calls if verb in a)

    def kwargs(self, verb: str) -> dict:
        return next(k for a, k in self.calls if verb in a)

    def ran(self, verb: str) -> bool:
        return any(verb in a for a, _ in self.calls)


@contextmanager
def _fake_clock(start=1000.0):
    """Make ``time.sleep`` advance a fake ``time.monotonic``.

    The poll loop's tolerance for an unreachable endpoint is wall-clock, so
    exercising it needs time to pass — but not real time.
    """
    now = {"t": start}

    def _sleep(seconds):
        now["t"] += seconds

    with patch("time.monotonic", lambda: now["t"]), patch("time.sleep", _sleep):
        yield now


@contextmanager
def _docker(**kwargs):
    """Patch out every docker call run_step makes (see :class:`_FakeDocker`).

    ``time.sleep`` becomes a short real sleep rather than a no-op so the poll
    loop cannot starve the streamer thread, while keeping the test in
    milliseconds.
    """
    fake = _FakeDocker(**kwargs)
    with patch("subprocess.run", side_effect=fake.run), \
         patch("subprocess.Popen", side_effect=fake.popen), \
         patch("time.sleep", lambda _seconds: _real_sleep(0.01)):
        yield fake


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
        with _docker(image_user="") as docker:
            result = runner.run_step(_spec())
        assert result.success is False
        assert result.exit_code == 126
        assert "runs as root" in result.stdout
        assert not docker.ran("run")  # the container must never start

    def test_run_step_runs_a_non_root_image(self):
        runner = DockerRunner()
        with _docker(image_user="1000") as docker:
            result = runner.run_step(_spec())
        assert result.success is True
        assert docker.ran("run")

    def test_failed_pull_fails_closed(self):
        runner = DockerRunner()
        with _docker(pull_rc=1) as docker:
            result = runner.run_step(_spec())
        assert result.success is False
        assert "Failed to pull" in result.stdout
        assert not docker.ran("run")

    def test_unreadable_image_user_fails_closed(self):
        # If we cannot prove the image is non-root, we do not run it.
        runner = DockerRunner()
        with _docker(inspect_rc=1) as docker:
            result = runner.run_step(_spec())
        assert result.success is False
        assert "Could not read the image's USER" in result.stdout
        assert not docker.ran("run")

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
        with _docker(follow=_FakeFollow(["line 1\n", "line 2\n"])) as docker:
            result = runner.run_step(_spec(), on_output=captured.append)
        assert result.success is True
        assert result.exit_code == 0
        assert result.stdout == "line 1\nline 2\n"
        # Each line is delivered as its own callback (live), not one buffer at the end.
        assert "line 1" in captured and "line 2" in captured
        assert docker.kwargs("run")["env"]["DOCKER_HOST"] == runner.docker_host
        assert docker.kwargs("logs")["stderr"] == subprocess.STDOUT  # merged for ordering

    def test_run_step_failure_maps_nonzero_exit(self):
        runner = DockerRunner()
        with _docker(follow=_FakeFollow(["boom\n"]), exit_code=2):
            result = runner.run_step(_spec())
        assert result.success is False
        assert result.exit_code == 2
        assert "boom" in result.stdout

    def test_run_step_timeout_kills_and_returns_124(self):
        runner = DockerRunner()
        # The container never stops on its own, so only the step's own deadline
        # can end it — which is the guarantee detached execution restores.
        with _docker(stays_running=True) as docker:
            result = runner.run_step(_spec(timeout_seconds=1))
        assert result.success is False
        assert result.timed_out is True
        assert result.exit_code == 124
        assert docker.ran("kill")

    def test_run_step_writes_and_cleans_up_transient_authfile(self):
        runner = DockerRunner()
        captured = {}

        def check_authfile(argv):
            # The --config dir must exist with a config.json during the run.
            cfg_dir = argv[argv.index("--config") + 1]
            captured["cfg_dir"] = cfg_dir
            assert os.path.isfile(os.path.join(cfg_dir, "config.json"))

        authjson = '{"auths": {"ghcr.io": {"auth": "dGVzdA=="}}}'
        with _docker(on_run=check_authfile):
            result = runner.run_step(_spec(pull_authfile_json=authjson))

        assert result.success is True
        # Cleaned up after the run.
        assert not os.path.exists(captured["cfg_dir"])


@pytest.mark.unit
class TestDetachedExecution:
    """The step must not depend on one long-lived request to the docker endpoint.

    An attached `docker run` parks a single HTTP request on
    /containers/{id}/wait for the entire step, so any idle timeout on the
    DOCKER_HOST path becomes a hard ceiling on step duration — the socket
    proxy's haproxy `timeout client` defaults to 10m, which killed every longer
    step with `unexpected EOF` (exit 125). These lock the detached + polled
    execution that replaced it.
    """

    def test_run_step_starts_the_container_detached(self):
        """The guard that matters: the detached path must be wired into the
        runner that actually executes.

        Asserting on ``build_run_argv`` alone is not enough — it passes just as
        happily when the implementation sits on the ABC and ``DockerRunner``'s
        own ``run_step`` override (the attached form) is what really runs.
        """
        runner = DockerRunner()
        with _docker() as docker:
            runner.run_step(_spec())
        argv = docker.argv("run")
        assert "--detach" in argv
        assert "--name" in argv
        assert "--rm" not in argv

    def test_run_step_removes_the_container_it_started(self):
        """``--rm`` is dropped so the exit code can be read back after the
        container stops, which makes removal the runner's own job."""
        runner = DockerRunner()
        with _docker() as docker:
            runner.run_step(_spec())
        run_argv = docker.argv("run")
        assert "--name" in run_argv, run_argv
        name = run_argv[run_argv.index("--name") + 1]
        assert docker.argv("rm")[-1] == name

    def test_detached_argv_names_the_container_and_drops_rm(self):
        runner = DockerRunner()
        argv = runner.build_run_argv(_spec(), detach=True, container_name="bnkforge-x-1")
        assert "--detach" in argv
        assert "--name" in argv and "bnkforge-x-1" in argv
        # --rm would delete the container before its exit code can be read back.
        assert "--rm" not in argv

    def test_attached_argv_is_unchanged(self):
        """The attached form stays available and identical (back-compat)."""
        argv = DockerRunner().build_run_argv(_spec())
        assert "run" in argv and "--rm" in argv
        assert "--detach" not in argv

    def test_detach_without_a_name_is_rejected(self):
        with pytest.raises(ValueError):
            DockerRunner().build_run_argv(_spec(), detach=True)

    def test_generated_container_name_is_docker_legal(self):
        import re as _re

        name = DockerRunner()._container_name(
            _spec(component_key="p13/m21", step_name="registry replicate")
        )
        assert _re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name), name

    def test_state_poll_reads_liveness_and_exit_code_in_one_call(self):
        argv = DockerRunner().build_state_argv("c1")
        assert argv[1:3] == ["inspect", "--format"]
        assert "{{.State.Running}}" in argv[3] and "{{.State.ExitCode}}" in argv[3]

    def test_logs_argv_can_resume_after_a_dropped_stream(self):
        argv = DockerRunner().build_logs_argv("c1", follow=True, since="1700000000")
        assert argv[1] == "logs"
        assert "--follow" in argv and "--since" in argv

    def test_await_exit_returns_the_container_exit_code(self):
        runner = DockerRunner()
        with patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="false 7\n", stderr="")
            code, timed_out, transport = runner._await_exit("c1", {}, 600, __import__("time").monotonic())
        assert (code, timed_out, transport) == (7, False, None)

    def test_await_exit_tolerates_a_transient_poll_failure(self):
        """One failed poll is a blip — the container keeps running regardless."""
        runner = DockerRunner()
        calls = [
            MagicMock(returncode=1, stdout="", stderr="temporary failure"),
            MagicMock(returncode=0, stdout="false 0\n", stderr=""),
        ]
        with patch("subprocess.run", side_effect=calls), patch("time.sleep"):
            code, timed_out, transport = runner._await_exit("c1", {}, 600, __import__("time").monotonic())
        assert (code, timed_out, transport) == (0, False, None)

    def test_await_exit_reports_a_sustained_endpoint_loss_as_transport(self):
        """A dead endpoint must be named, not surfaced as a bare EOF."""
        runner = DockerRunner()
        dead = MagicMock(returncode=1, stdout="", stderr="cannot connect to the docker daemon")
        with _fake_clock() as now, patch("subprocess.run", return_value=dead):
            code, timed_out, transport = runner._await_exit("c1", {}, None, now["t"])
        assert code == 125 and timed_out is False
        assert "docker daemon" in (transport or "")

    def test_await_exit_waits_out_an_outage_shorter_than_the_grace(self):
        """A proxy restart must not fail a step whose container is running fine.

        The container keeps running whether or not the endpoint is reachable,
        so an outage is only a step failure once it is sustained. Ten failed
        polls used to be the whole budget (~20s) — less than a container
        restart, which made this the same 'infrastructure bounds the step'
        failure the detached model exists to remove.
        """
        runner = DockerRunner()
        dead = MagicMock(returncode=1, stdout="", stderr="connection refused")
        alive = MagicMock(returncode=0, stdout="false 0\n", stderr="")
        # 60s of outage: far more than a poll interval, far less than the grace.
        polls = [dead] * 30 + [alive]
        with _fake_clock() as now, patch("subprocess.run", side_effect=polls):
            code, timed_out, transport = runner._await_exit("c1", {}, None, now["t"])
        assert (code, timed_out, transport) == (0, False, None)

    def test_a_resumed_follow_resumes_from_the_daemon_timestamp(self):
        """A dropped stream must not replay everything since it attached.

        `--since` carries the timestamp the DAEMON put on the last line, not the
        worker's wall clock. Two properties in one: a resume repeats at most the
        final second rather than the whole attach window, and it is immune to
        skew between the worker and a remote docker host — `--since` is
        interpreted daemon-side, and this design assumes a proxied DOCKER_HOST,
        so the worker's clock is the wrong one to feed back.
        """
        runner = DockerRunner()
        stop = threading.Event()
        state = {"last_seen": None, "gave_up": False}
        argvs: list[list[str]] = []
        emitted: list[str] = []

        class _Follow:
            def __init__(self, lines):
                self.stdout = iter(lines)

            def kill(self):
                pass

        def fake_popen(argv, **kwargs):
            argvs.append(argv)
            if len(argvs) >= 2:
                stop.set()          # end the loop once the resume is observed
            return _Follow(
                ["2026-08-05T10:30:00.123456789Z a line\n"] if len(argvs) == 1 else []
            )

        # The worker clock is deliberately nowhere near the daemon's stamp; if
        # the resume used time.time() this would be a 2021 timestamp.
        with patch("subprocess.Popen", side_effect=fake_popen), \
             patch("time.time", return_value=1609459200.0), \
             patch("time.sleep"):
            runner._stream_logs("c1", {}, emitted.append, stop, state)

        assert "--timestamps" in argvs[0]
        assert "--since" not in argvs[0]        # first attach reads from the start
        assert argvs[1][argvs[1].index("--since") + 1] == "2026-08-05T10:30:00.123456789Z"
        # The prefix is consumed, not handed to the caller.
        assert emitted == ["a line"]

    def test_a_line_without_a_timestamp_is_passed_through_intact(self):
        """A daemon that omits the prefix must not lose the line's first token."""
        runner = DockerRunner()
        stop = threading.Event()
        state = {"last_seen": None, "gave_up": False}
        emitted: list[str] = []

        class _Follow:
            def __init__(self):
                self.stdout = iter(["plain output line\n"])

            def kill(self):
                pass

        class _EmptyFollow:
            def __init__(self):
                self.stdout = iter(())

            def kill(self):
                pass

        calls = {"n": 0}

        def fake_popen(argv, **kwargs):
            calls["n"] += 1
            if calls["n"] >= 2:
                stop.set()      # end the loop only after the line is consumed
            return _Follow() if calls["n"] == 1 else _EmptyFollow()

        with patch("subprocess.Popen", side_effect=fake_popen), patch("time.sleep"):
            runner._stream_logs("c1", {}, emitted.append, stop, state)

        assert emitted == ["plain output line"]
        assert state["last_seen"] is None, "no resume point rather than a bogus one"

    def test_a_follow_that_cannot_restart_reports_that_it_gave_up(self):
        """The caller needs to know the follow died, or it truncates silently."""
        runner = DockerRunner()
        state = {"last_seen": None, "gave_up": False}
        with patch("subprocess.Popen", side_effect=OSError("no route to host")):
            runner._stream_logs("c1", {}, lambda _line: None, threading.Event(), state)
        assert state["gave_up"] is True

    def test_run_step_recovers_output_a_dead_follow_missed(self):
        """A follow that dies part-way must not silently truncate the output.

        The step's RESULT is safe either way (it comes from the state poll),
        but the artifact's own stdout is the only failure detail the engine
        surfaces, so losing it is expensive on exactly the runs being debugged.
        The old guard only re-read when the follow produced *nothing*, so a
        follow that delivered one line and then died dropped all the rest.
        """
        runner = DockerRunner()
        with _docker(
            follows=[_FakeFollow(["first\n"]), OSError("stream dropped")],
            logs="second\n",        # what `docker logs --since <last line>` returns
        ) as docker:
            result = runner.run_step(_spec())

        assert result.success is True
        assert result.stdout == "first\nsecond\n"
        # The catch-up read resumes from the last line, not the start of the run.
        assert "--since" in docker.argvs("logs")[-1]

    def test_transport_failure_names_the_container_that_may_still_be_running(self):
        """The message tells the operator the container may still be running,
        so it has to give them the name they need to find and remove it."""
        runner = DockerRunner()
        with _docker() as docker, patch.object(
            DockerRunner, "_await_exit", return_value=(125, False, "connection reset by peer")
        ):
            result = runner.run_step(_spec())

        run_argv = docker.argv("run")
        name = run_argv[run_argv.index("--name") + 1]
        assert result.exit_code == 125
        assert name in result.stderr
        assert "timeout client" in result.stderr     # still points at the likely cause

    def test_detached_run_is_labelled_for_reaping(self):
        """Labels, not the container name, are what make an orphan findable.

        Dropping --rm moved cleanup into a `finally` a SIGKILLed worker never
        reaches, so something has to answer "whose container is this?" after the
        fact — which workspace it holds, and which task owned it.
        """
        runner = DockerRunner()
        argv = runner.build_run_argv(
            _spec(workspace_volume="vol", workspace_subpath="7/bp-1",
                  celery_task_id="celery-abc"),
            detach=True, container_name="bnkforge-x-1",
        )
        labels = [argv[i + 1] for i, a in enumerate(argv) if a == "--label"]
        assert "bnkforge.step=1" in labels
        assert "bnkforge.workspace=7/bp-1" in labels
        assert "bnkforge.task=celery-abc" in labels

    def test_attached_run_is_not_labelled(self):
        """Back-compat: the attached form is untouched."""
        argv = DockerRunner().build_run_argv(_spec())
        assert "--label" not in argv

    def test_a_live_siblings_container_is_never_swept(self):
        """The sweep must not kill another module's running step.

        workspace_subpath is SHARED by design: artifact_workspace_key returns the
        deployment group for state:{scope:deployment}, so every module of a
        blueprint deployment resolves to the same {project}/bp-<release> subpath,
        and parallel_tasks dispatches them in waves onto --concurrency=4 workers.
        module_lock does not serialise them — it is keyed on module.id, so two
        different modules sharing one workspace each hold their own lock. A sweep
        on the workspace label alone force-removes a live sibling, and the victim
        surfaces it as "Lost contact with the docker endpoint".
        """
        runner = DockerRunner()
        with _docker() as docker:
            # A sibling module's container, owned by a different LIVE task.
            docker.ps_result = "sibling123 celery-sibling\n"
            with patch("services.execution_janitor.get_live_task_ids",
                       return_value={"celery-sibling", "celery-mine"}):
                runner.run_step(_spec(workspace_volume="vol", workspace_subpath="7/bp-1",
                                      celery_task_id="celery-mine"))

        assert not [a for a in docker.argvs("rm") if "sibling123" in a], \
            "a live sibling's container must never be force-removed"

    def test_our_own_predecessor_is_swept_even_though_its_task_is_live(self):
        """Celery preserves task_id across retry(), so "owner is live" is true of
        our own orphan. Sparing on liveness alone would reinstate the corruption
        this sweep exists to prevent."""
        runner = DockerRunner()
        with _docker() as docker:
            docker.ps_result = "mine456 celery-mine\n"
            with patch("services.execution_janitor.get_live_task_ids",
                       return_value={"celery-mine"}):
                runner.run_step(_spec(workspace_volume="vol", workspace_subpath="7/bp-1",
                                      celery_task_id="celery-mine"))

        assert [a for a in docker.argvs("rm") if "mine456" in a], \
            "our own predecessor from a retry must still be removed"

    def test_an_unowned_container_is_left_alone(self):
        """No owner label predates the labelling — removing on a guess would kill
        a running deployment, which is worse than the leak it recovers."""
        runner = DockerRunner()
        with _docker() as docker:
            docker.ps_result = "legacy789 \n"
            with patch("services.execution_janitor.get_live_task_ids", return_value=set()):
                runner.run_step(_spec(workspace_volume="vol", workspace_subpath="7/bp-1",
                                      celery_task_id="celery-mine"))

        assert not [a for a in docker.argvs("rm") if "legacy789" in a]

    def test_a_degraded_live_lookup_sweeps_nothing(self):
        """An unavailable lookup must not read as "nothing is running".

        get_live_task_ids() returns an empty set on any failure — import error,
        no redis client, an exception mid-scan — and all three only log. Taken
        literally that spares no sibling and rm --force's every container on a
        shared workspace, which is the exact failure the ownership rule was
        added to prevent, reached by a redis blip instead of a code path. It
        needs no worker outage: this sweep runs on EVERY step.
        """
        runner = DockerRunner()
        with _docker() as docker:
            docker.ps_result = "sibling123 celery-sibling\n"
            with patch("services.execution_janitor.get_live_task_ids", return_value=set()):
                runner.run_step(_spec(workspace_volume="vol", workspace_subpath="7/bp-1",
                                      celery_task_id="celery-mine"))

        assert not [a for a in docker.argvs("rm") if "sibling123" in a], \
            "a degraded live-task lookup must not be read as 'nothing is running'"

    def test_a_step_clears_a_predecessor_still_holding_its_workspace(self):
        """The corruption case, closed where it has to be.

        A worker killed mid-step leaves its container running. The janitor frees
        the task, it is retried, and _container_name mints a fresh uuid — so
        without this the orphan and the retry run CONCURRENTLY against the same
        workspace volume subpath. A periodic reaper cannot close that: the retry
        starts seconds after the worker returns.
        """
        runner = DockerRunner()
        with _docker() as docker:
            # One container is already holding this workspace.
            docker.ps_result = "deadbeefcafe celery-dead\n"
            # {"celery-mine"} rather than set(): our own task is always in a
            # healthy live set (task_prerun records it), so an empty set means
            # the lookup failed, not that nothing is running.
            with patch("services.execution_janitor.get_live_task_ids",
                       return_value={"celery-mine"}):
                runner.run_step(_spec(workspace_volume="vol", workspace_subpath="7/bp-1",
                                      celery_task_id="celery-mine"))

        assert docker.ran("ps"), "the workspace was never swept before the run"
        assert "label=bnkforge.workspace=7/bp-1" in docker.argv("ps")
        removed = [a for a in docker.argvs("rm") if "deadbeefcafe" in a]
        assert removed, "the predecessor must be force-removed before the step starts"
        # ...and before the container is started, not after it is already running.
        calls = [a for a, _ in docker.calls]
        first_rm = next(i for i, a in enumerate(calls) if "rm" in a and "deadbeefcafe" in a)
        first_run = next(i for i, a in enumerate(calls) if "run" in a)
        assert first_rm < first_run, f"swept at {first_rm}, started at {first_run}"

    def test_a_failing_catch_up_read_does_not_destroy_the_result(self):
        """The step's outcome is already known by then — a log read must not lose it."""
        runner = DockerRunner()

        def explode(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, 30)

        with _docker() as docker:
            docker.logs_side_effect = explode
            result = runner.run_step(_spec())

        assert result.success is True, "a log-read timeout must not fail a successful step"
        assert result.exit_code == 0

    def test_step_timeout_is_enforced_by_the_poll_loop(self):
        """The manifest's timeout_seconds is the ONLY thing that ends a step early."""
        runner = DockerRunner()
        alive = MagicMock(returncode=0, stdout="true 0\n", stderr="")
        with patch("subprocess.run", return_value=alive) as run, patch("time.sleep"):
            # started far enough in the past that the deadline has already passed
            code, timed_out, transport = runner._await_exit(
                "c1", {}, 1, __import__("time").monotonic() - 3600
            )
        assert (code, timed_out, transport) == (124, True, None)
        assert any("kill" in str(c) for c in run.call_args_list), "the container must be killed"
