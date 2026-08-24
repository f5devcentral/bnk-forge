"""#195: OpenTofu runtime streams subprocess output incrementally.

Before this fix ``run_init``/``run_plan``/``run_apply``/``run_destroy`` used a
blocking ``subprocess.run(capture_output=True)`` that returned the entire log
only when the process exited, so a caller had nothing to persist until the very
end. These tests prove the streaming path delivers each line *while the process
is still running*, and that the ``run_*`` methods route through it only when an
``on_output`` callback is supplied (the classic blocking capture is preserved
otherwise, which the existing test_opentofu_runtime.py suite locks).
"""

import os
import subprocess
import sys

import pytest

import services.execution.opentofu_runtime as otr
from services.execution.opentofu_runtime import OpenTofuRuntime, _stream_subprocess


@pytest.mark.component
class TestStreamSubprocess:
    def test_delivers_each_line_before_process_finishes(self, tmp_path):
        """A handshake proves lines arrive live, not buffered until exit.

        The child prints ``line-1`` then blocks on a sentinel that the test's
        ``on_output`` writes only when it *receives* ``line-1``; only then does
        the child print ``line-2``. The blocking loop runs FAR longer than the
        watchdog (``timeout`` below), so a buffered implementation — the #195 bug,
        where ``on_output`` fires only after the process exits — cannot
        self-release: the sentinel never appears, the child deadlocks, and the
        watchdog kills it → ``_stream_subprocess`` raises ``TimeoutExpired`` and
        this test ERRORS. Genuine streaming releases the child within
        milliseconds, so the call returns well under the watchdog. Both the raise
        AND the timing bound below make the distinction non-vacuous — a fully
        buffered impl fails, proven by mutation.
        """
        import time

        go = tmp_path / "go"
        # ~300s of blocking: >> the 8s watchdog, so a buffered impl MUST deadlock
        # rather than self-release before the watchdog fires.
        script = (
            "import sys, time, pathlib\n"
            "print('line-1', flush=True)\n"
            "sentinel = pathlib.Path(sys.argv[1])\n"
            "for _ in range(6000):\n"
            "    if sentinel.exists():\n"
            "        break\n"
            "    time.sleep(0.05)\n"
            "print('line-2', flush=True)\n"
        )

        received: list[str] = []

        def on_output(line: str) -> None:
            received.append(line)
            if line == "line-1":
                go.write_text("go")  # unblock the child only after we SEE line-1

        started = time.monotonic()
        code, output = _stream_subprocess(
            [sys.executable, "-c", script, str(go)],
            cwd=str(tmp_path),
            env=dict(os.environ),
            timeout=8,
            on_output=on_output,
        )
        elapsed = time.monotonic() - started

        assert code == 0
        # line-2 was printed *only* because on_output saw line-1 and released it.
        assert received == ["line-1", "line-2"]
        assert "line-1" in output and "line-2" in output
        # Live streaming releases the child in ms; a buffered impl would deadlock
        # and blow the 8s watchdog. The timing bound makes that explicit.
        assert elapsed < 5, f"took {elapsed:.1f}s — output looks buffered, not streamed"

    def test_returns_combined_output_and_exit_code(self, tmp_path):
        script = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
        seen: list[str] = []
        code, output = _stream_subprocess(
            [sys.executable, "-c", script],
            cwd=str(tmp_path),
            env=dict(os.environ),
            timeout=20,
            on_output=seen.append,
        )
        assert code == 3
        # stderr is merged into stdout so both surface in the log and the sink.
        assert "out" in output and "err" in output
        assert "out" in seen and "err" in seen

    def test_timeout_kills_and_raises_with_partial_output(self, tmp_path):
        script = (
            "import sys, time\n"
            "print('before-hang', flush=True)\n"
            "time.sleep(30)\n"
        )
        seen: list[str] = []
        with pytest.raises(subprocess.TimeoutExpired) as exc:
            _stream_subprocess(
                [sys.executable, "-c", script],
                cwd=str(tmp_path),
                env=dict(os.environ),
                timeout=1,
                on_output=seen.append,
            )
        # The partial output produced before the hang is carried on the
        # exception, matching subprocess.run(...).stdout semantics the run_*
        # timeout branches rely on.
        assert "before-hang" in (exc.value.output or "")
        assert seen == ["before-hang"]


@pytest.mark.component
class TestRunMethodsRouteThroughStreaming:
    @staticmethod
    def _runtime(monkeypatch):
        # OpenTofuRuntime only needs a DB session for workspace ops, not for the
        # subprocess-running methods under test here. Stub the system-defaults
        # gate so construction doesn't need a real DB.
        monkeypatch.setattr(
            otr, "check_required_configured",
            lambda _db: {"all_configured": True, "missing": []},
        )
        return OpenTofuRuntime(db=None)

    def _patch_stream(self, monkeypatch):
        calls: dict = {}

        def fake_stream(cmd, *, cwd, env, timeout, on_output):
            calls["cmd"] = cmd
            calls["on_output"] = on_output
            on_output("streamed-1")
            on_output("streamed-2")
            return 0, "streamed-1\nstreamed-2\n"

        monkeypatch.setattr(otr, "_stream_subprocess", fake_stream)
        return calls

    def test_run_plan_streams_when_on_output_given(self, monkeypatch):
        calls = self._patch_stream(monkeypatch)
        runtime = self._runtime(monkeypatch)
        seen: list[str] = []

        code, output = runtime.run_plan("/tmp/w", {}, on_output=seen.append)

        assert code == 0
        assert seen == ["streamed-1", "streamed-2"]
        assert "streamed-1" in output and "streamed-2" in output
        assert "plan" in calls["cmd"]

    def test_run_init_streams_when_on_output_given(self, monkeypatch):
        calls = self._patch_stream(monkeypatch)
        runtime = self._runtime(monkeypatch)
        seen: list[str] = []

        code, output = runtime.run_init("/tmp/w", {}, on_output=seen.append)

        assert code == 0
        assert seen == ["streamed-1", "streamed-2"]
        assert "init" in calls["cmd"]

    def test_run_apply_streams_when_on_output_given(self, monkeypatch):
        self._patch_stream(monkeypatch)
        runtime = self._runtime(monkeypatch)
        # apply captures outputs on success; stub that out.
        monkeypatch.setattr(runtime, "_capture_outputs", lambda *a, **k: {})
        seen: list[str] = []

        code, output, _outputs = runtime.run_apply("/tmp/w", {}, on_output=seen.append)

        assert code == 0
        assert seen == ["streamed-1", "streamed-2"]

    def test_blocking_path_used_when_no_on_output(self, monkeypatch):
        """Without on_output the classic subprocess.run capture is used, NOT the
        streaming helper — preserving legacy behaviour and the existing suite."""
        def _boom(*a, **k):
            raise AssertionError("_stream_subprocess must not run without on_output")

        monkeypatch.setattr(otr, "_stream_subprocess", _boom)

        completed = subprocess.CompletedProcess(
            args=["tofu", "plan"], returncode=0, stdout="blocking-out", stderr="",
        )
        monkeypatch.setattr(otr.subprocess, "run", lambda *a, **k: completed)

        runtime = self._runtime(monkeypatch)
        code, output = runtime.run_plan("/tmp/w", {})

        assert code == 0
        assert output == "blocking-out"
