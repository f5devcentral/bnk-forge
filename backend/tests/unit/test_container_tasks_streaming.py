"""Unit tests for the container-engine streaming log sink (_streaming_sink).

Phase 1: incremental task.logs flush (throttled). Phase 2: per-line WebSocket
publish via publish_module_log. Both are best-effort and must never raise.
"""

from unittest.mock import MagicMock, patch

import pytest

from tasks.container_tasks import _streaming_sink


@pytest.mark.unit
class TestStreamingSink:
    def test_publishes_each_line_over_websocket(self):
        task = MagicMock()
        task.module_id = 95
        db = MagicMock()
        lines: list[str] = []

        with patch("services.websocket_service.publish_module_log") as pub:
            sink = _streaming_sink(task, db, "HEADER", lines)
            sink("creating cluster")
            sink("still creating")

        assert pub.call_count == 2
        # module_id forwarded; the line is stamped (already redacted upstream).
        _, kw = pub.call_args
        assert kw["module_id"] == 95
        assert "still creating" in kw["line"]

    def test_throttled_flush_persists_task_logs(self):
        task = MagicMock()
        task.module_id = 95
        db = MagicMock()
        lines: list[str] = []

        # interval=0 → every line flushes, so task.logs + commit happen.
        with patch("services.websocket_service.publish_module_log"):
            sink = _streaming_sink(task, db, "HEADER", lines, interval=0)
            sink("line one")

        assert task.logs is not None and "line one" in task.logs
        db.commit.assert_called()

    def test_ws_publish_failure_does_not_break_step(self):
        task = MagicMock()
        task.module_id = 95
        db = MagicMock()
        lines: list[str] = []

        with patch("services.websocket_service.publish_module_log", side_effect=RuntimeError("redis down")):
            sink = _streaming_sink(task, db, "HEADER", lines)
            sink("a line")  # must not raise

        assert lines and "a line" in lines[0]

    def test_no_module_id_skips_publish(self):
        task = MagicMock()
        task.module_id = None
        db = MagicMock()
        lines: list[str] = []

        with patch("services.websocket_service.publish_module_log") as pub:
            sink = _streaming_sink(task, db, "HEADER", lines)
            sink("a line")

        pub.assert_not_called()


@pytest.mark.unit
class TestStageDetailMarkerParser:
    """The "[N/M] <text>" step-marker parser that mirrors the latest phase into
    ``module.stage_detail`` — matching, dedupe, time-throttle + final flush, and
    250-char truncation.
    """

    @staticmethod
    def _stage_writes(session_local):
        """Return the ordered list of values passed to the stage_detail UPDATE."""
        update = session_local.return_value.query.return_value.filter.return_value.update
        return [next(iter(call.args[0].values())) for call in update.call_args_list]

    def test_valid_marker_sets_stage_detail(self):
        task = MagicMock()
        task.module_id = 7
        db = MagicMock()
        lines: list[str] = []

        with patch("services.websocket_service.publish_module_log"), \
                patch("database.SessionLocal") as sl:
            sink = _streaming_sink(task, db, "H", lines, interval=0)
            sink("[3/6] cluster-up")

        assert self._stage_writes(sl) == ["[3/6] cluster-up"]

    def test_non_matching_line_leaves_stage_detail_untouched(self):
        task = MagicMock()
        task.module_id = 7
        db = MagicMock()
        lines: list[str] = []

        with patch("services.websocket_service.publish_module_log"), \
                patch("database.SessionLocal") as sl:
            sink = _streaming_sink(task, db, "H", lines, interval=0)
            sink("just some build output")
            sink("STEP 5/8: RUN make")  # no leading [N/M]

        assert self._stage_writes(sl) == []
        sl.return_value.commit.assert_not_called()

    def test_consecutive_identical_markers_dedupe(self):
        task = MagicMock()
        task.module_id = 7
        db = MagicMock()
        lines: list[str] = []

        with patch("services.websocket_service.publish_module_log"), \
                patch("database.SessionLocal") as sl:
            sink = _streaming_sink(task, db, "H", lines, interval=0)
            sink("[2/6] pulling")
            sink("[2/6] pulling")

        assert self._stage_writes(sl) == ["[2/6] pulling"]

    def test_throttle_writes_only_first_within_window_then_final_flush(self):
        task = MagicMock()
        task.module_id = 7
        db = MagicMock()
        lines: list[str] = []

        # interval=100s; two distinct markers arrive 0.1s apart → the first is
        # written, the second is throttled. flush_stage() persists the throttled
        # final phase so the UI still ends on the true last phase.
        with patch("services.websocket_service.publish_module_log"), \
                patch("time.monotonic", side_effect=[1000.0, 1000.1]), \
                patch("database.SessionLocal") as sl:
            sink = _streaming_sink(task, db, "H", lines, interval=100)
            sink("[1/6] first")
            sink("[2/6] second")  # within window → not written yet
            assert self._stage_writes(sl) == ["[1/6] first"]
            sink.flush_stage()

        assert self._stage_writes(sl) == ["[1/6] first", "[2/6] second"]

    def test_flush_stage_is_noop_when_latest_already_written(self):
        task = MagicMock()
        task.module_id = 7
        db = MagicMock()
        lines: list[str] = []

        with patch("services.websocket_service.publish_module_log"), \
                patch("database.SessionLocal") as sl:
            sink = _streaming_sink(task, db, "H", lines, interval=0)
            sink("[6/6] done")  # interval=0 → written immediately
            sink.flush_stage()  # nothing new to flush

        assert self._stage_writes(sl) == ["[6/6] done"]

    def test_marker_truncated_to_250_chars(self):
        task = MagicMock()
        task.module_id = 7
        db = MagicMock()
        lines: list[str] = []
        long_marker = "[1/2] " + "x" * 400

        with patch("services.websocket_service.publish_module_log"), \
                patch("database.SessionLocal") as sl:
            sink = _streaming_sink(task, db, "H", lines, interval=0)
            sink(long_marker)

        written = self._stage_writes(sl)
        assert len(written) == 1
        assert written[0] == long_marker[:250]
        assert len(written[0]) == 250
