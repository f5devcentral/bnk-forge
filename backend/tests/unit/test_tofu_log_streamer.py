"""Unit tests for TofuLogStreamer — the incremental task.logs flusher (#195).

The streamer turns the runtime's per-line ``on_output`` callback into throttled
writes of ``task.logs`` so ``logs_full_size`` grows during a run. These tests
lock: base+buffer composition, growth across lines, interval throttling, the
force-flush of the first line, and that a failed commit never propagates.
"""

from unittest.mock import MagicMock

import pytest

from tasks._tofu_helpers import TofuLogStreamer


@pytest.mark.unit
class TestTofuLogStreamer:
    def test_first_line_flushes_and_prepends_base(self):
        task = MagicMock()
        db = MagicMock()
        streamer = TofuLogStreamer(task, db, interval=0)

        sink = streamer.begin("HEADER\n")
        sink("first line")

        assert task.logs == "HEADER\nfirst line\n"
        db.commit.assert_called()

    def test_logs_grow_across_lines(self):
        task = MagicMock()
        db = MagicMock()
        streamer = TofuLogStreamer(task, db, interval=0)  # every line flushes

        sink = streamer.begin("BASE\n")
        sizes = []
        for i in range(1, 6):
            sink(f"line {i}")
            sizes.append(len(task.logs))

        # Strictly increasing — the whole point of #195: not 0 until the end.
        assert sizes == sorted(sizes)
        assert len(set(sizes)) == len(sizes)
        assert db.commit.call_count == 5
        assert task.logs.startswith("BASE\n")
        assert "line 5" in task.logs

    def test_interval_throttles_intermediate_lines(self, monkeypatch):
        task = MagicMock()
        db = MagicMock()
        # begin() sets last=0.0 so the first line always flushes; a large
        # interval then suppresses the second line arriving within the window.
        monkeypatch.setattr("time.monotonic", MagicMock(side_effect=[1000.0, 1000.1]))
        streamer = TofuLogStreamer(task, db, interval=100)

        sink = streamer.begin("")
        sink("first")   # 1000.0 - 0.0 >= 100 -> flush
        sink("second")  # 1000.1 - 1000.0 < 100 -> throttled

        assert db.commit.call_count == 1
        # Persisted content still reflects only the flushed line; the throttled
        # line is folded into task.logs by the task's final all_logs write.
        assert task.logs == "first\n"

    def test_begin_resets_base_and_buffer(self):
        task = MagicMock()
        db = MagicMock()
        streamer = TofuLogStreamer(task, db, interval=0)

        streamer.begin("STEP-A\n")("a-line")
        assert task.logs == "STEP-A\na-line\n"

        # A new step starts from a fresh base; the previous step's buffer is gone.
        streamer.begin("STEP-A\na-line\nSTEP-B\n")("b-line")
        assert task.logs == "STEP-A\na-line\nSTEP-B\nb-line\n"

    def test_commit_failure_is_swallowed_and_rolls_back(self):
        task = MagicMock()
        db = MagicMock()
        db.commit.side_effect = RuntimeError("db gone")
        streamer = TofuLogStreamer(task, db, interval=0)

        sink = streamer.begin("H\n")
        sink("a line")  # must not raise

        db.rollback.assert_called_once()
