"""
Component tests for tasks.cluster_scan_task.

Covers:
- scan_cluster_async happy path (ClusterScanner.scan is called)
- scan_cluster_async failure path (exception swallowed, task returns normally)
- enqueue_cluster_scan calls .delay with the right cluster_id
- enqueue_cluster_scan swallows broker exceptions
"""

from unittest.mock import MagicMock, patch

import pytest

_TASK_MOD = "tasks.cluster_scan_task"


class TestScanClusterAsync:
    def test_happy_path_calls_scanner(self):
        """scan_cluster_async acquires a DB session and calls ClusterScanner.scan."""
        mock_scanner = MagicMock()
        mock_db = MagicMock()

        with patch(f"{_TASK_MOD}.get_db_context") as mock_ctx, \
             patch(f"{_TASK_MOD}.ClusterScanner") as mock_scanner_cls:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            mock_scanner_cls.return_value = mock_scanner

            from tasks.cluster_scan_task import scan_cluster_async
            scan_cluster_async(cluster_id=42)

        mock_scanner_cls.assert_called_once_with(mock_db)
        mock_scanner.scan.assert_called_once_with(42)

    def test_scanner_exception_is_swallowed(self):
        """scan_cluster_async logs a warning and returns normally when scanner raises."""
        mock_db = MagicMock()

        with patch(f"{_TASK_MOD}.get_db_context") as mock_ctx, \
             patch(f"{_TASK_MOD}.ClusterScanner") as mock_scanner_cls, \
             patch(f"{_TASK_MOD}.logger") as mock_logger:
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            mock_scanner_cls.return_value.scan.side_effect = RuntimeError("kubeconfig broken")

            from tasks.cluster_scan_task import scan_cluster_async
            # Must not raise
            result = scan_cluster_async(cluster_id=99)

        assert result is None
        mock_logger.warning.assert_called_once()
        assert "99" in str(mock_logger.warning.call_args)

    def test_db_context_exception_is_swallowed(self):
        """scan_cluster_async swallows DB context errors too."""
        with patch(f"{_TASK_MOD}.get_db_context") as mock_ctx, \
             patch(f"{_TASK_MOD}.logger") as mock_logger:
            mock_ctx.return_value.__enter__ = MagicMock(side_effect=Exception("db conn failed"))
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)

            from tasks.cluster_scan_task import scan_cluster_async
            result = scan_cluster_async(cluster_id=7)

        assert result is None
        mock_logger.warning.assert_called_once()


class TestEnqueueClusterScan:
    def test_calls_delay_with_cluster_id(self):
        """enqueue_cluster_scan calls scan_cluster_async.delay(cluster_id)."""
        with patch(f"{_TASK_MOD}.scan_cluster_async") as mock_task:
            from tasks.cluster_scan_task import enqueue_cluster_scan
            enqueue_cluster_scan(42)

        mock_task.delay.assert_called_once_with(42)

    def test_broker_exception_is_swallowed(self):
        """enqueue_cluster_scan swallows broker exceptions so the caller's API succeeds."""
        with patch(f"{_TASK_MOD}.scan_cluster_async") as mock_task, \
             patch(f"{_TASK_MOD}.logger") as mock_logger:
            mock_task.delay.side_effect = ConnectionError("broker down")

            from tasks.cluster_scan_task import enqueue_cluster_scan
            # Must not raise
            result = enqueue_cluster_scan(55)

        assert result is None
        mock_logger.warning.assert_called_once()
        assert "55" in str(mock_logger.warning.call_args)
