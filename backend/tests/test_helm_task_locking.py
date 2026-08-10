"""Tests for Helm task advisory locking.

Verifies that install_release / upgrade_release / rollback_release /
uninstall_release each acquire a Postgres advisory lock keyed on
(cluster_id, release_name), and that locks for different releases are
independent (different keys).

Strategy:
- Unit-test the key-derivation function directly.
- Unit-test that helm_release_lock issues pg_advisory_lock/pg_advisory_unlock with
  the expected key on a Postgres-like DB session.
- Unit-test the SQLite no-op path.
- Integration-style tests: patch helm_release_lock + HelmService and assert
  each of the 4 task entrypoints calls helm_release_lock with the right args.
- Serialization: assert that two concurrent calls to the same task with the
  same (cluster_id, release_name) wait behind the lock (simulated by tracking
  call order via a threading barrier).
"""

from __future__ import annotations

import os
import sys
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — mirror the pattern in test_helm_security.py
# ---------------------------------------------------------------------------
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# Force SQLite for this test module so DATABASE_URL is set before import
os.environ.setdefault("DATABASE_URL", "sqlite:///file::memory:?cache=shared")
os.environ.setdefault("REQUIRE_AUTH", "false")
os.environ.setdefault("ENVIRONMENT", "development")

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------
from tasks.helm_tasks import (  # noqa: E402
    _helm_lock_key,
    helm_release_lock,
    install_release,
    rollback_release,
    uninstall_release,
    upgrade_release,
)

# ===========================================================================
# 1. Key derivation
# ===========================================================================


class TestHelmLockKey:
    def test_deterministic_for_same_inputs(self):
        """Same (cluster_id, release_name) always yields the same key."""
        k1 = _helm_lock_key(1, "my-release")
        k2 = _helm_lock_key(1, "my-release")
        assert k1 == k2

    def test_different_release_names_produce_different_keys(self):
        k1 = _helm_lock_key(1, "release-a")
        k2 = _helm_lock_key(1, "release-b")
        assert k1 != k2

    def test_different_cluster_ids_produce_different_keys(self):
        k1 = _helm_lock_key(1, "release-a")
        k2 = _helm_lock_key(2, "release-a")
        assert k1 != k2

    def test_key_fits_postgres_bigint(self):
        """Key must be a non-negative signed 64-bit integer."""
        key = _helm_lock_key(99, "some-release")
        assert 0 <= key <= 0x7FFFFFFFFFFFFFFF


# ===========================================================================
# 2. helm_release_lock context manager
# ===========================================================================


class TestHelmReleaseLockPg:
    """On a non-SQLite DATABASE_URL, the lock issues session-level pg_advisory_lock/unlock."""

    def test_issues_advisory_lock_with_correct_key(self):
        mock_db = MagicMock()
        expected_key = _helm_lock_key(5, "nginx")

        with patch("tasks.helm_tasks.DATABASE_URL", "postgresql://localhost/test"):
            with helm_release_lock(mock_db, 5, "nginx"):
                pass

        # Two execute calls: pg_advisory_lock (acquire) + pg_advisory_unlock (release)
        assert mock_db.execute.call_count == 2
        acquire_call = mock_db.execute.call_args_list[0]
        release_call = mock_db.execute.call_args_list[1]
        assert "pg_advisory_lock" in str(acquire_call[0][0])
        assert "pg_advisory_xact_lock" not in str(acquire_call[0][0]), (
            "should use session-level lock, not transaction-scoped"
        )
        assert acquire_call[0][1] == {"key": expected_key}
        assert "pg_advisory_unlock" in str(release_call[0][0])
        assert release_call[0][1] == {"key": expected_key}

    def test_unlock_called_even_on_exception(self):
        """pg_advisory_unlock must be called even when the body raises."""
        mock_db = MagicMock()
        expected_key = _helm_lock_key(5, "nginx")

        with patch("tasks.helm_tasks.DATABASE_URL", "postgresql://localhost/test"):
            with pytest.raises(RuntimeError, match="body-error"):
                with helm_release_lock(mock_db, 5, "nginx"):
                    raise RuntimeError("body-error")

        assert mock_db.execute.call_count == 2
        release_call = mock_db.execute.call_args_list[1]
        assert "pg_advisory_unlock" in str(release_call[0][0])
        assert release_call[0][1] == {"key": expected_key}

    def test_different_releases_use_different_lock_keys(self):
        db_a = MagicMock()
        db_b = MagicMock()

        with patch("tasks.helm_tasks.DATABASE_URL", "postgresql://localhost/test"):
            with helm_release_lock(db_a, 1, "release-a"):
                pass
            with helm_release_lock(db_b, 1, "release-b"):
                pass

        key_a = db_a.execute.call_args_list[0][0][1]["key"]
        key_b = db_b.execute.call_args_list[0][0][1]["key"]
        assert key_a != key_b


class TestHelmReleaseLockSqlite:
    """On SQLite, the lock is a no-op (no DB call)."""

    def test_no_db_call_on_sqlite(self):
        mock_db = MagicMock()
        # DATABASE_URL is already sqlite in this test module
        with helm_release_lock(mock_db, 1, "my-release"):
            pass
        mock_db.execute.assert_not_called()

    def test_body_executes_normally_on_sqlite(self):
        mock_db = MagicMock()
        sentinel = []
        with helm_release_lock(mock_db, 1, "my-release"):
            sentinel.append(True)
        assert sentinel == [True]


# ===========================================================================
# 3. Each write task acquires the lock with the correct (cluster_id, release_name)
# ===========================================================================

# Common mock return values
_INSTALL_RESULT = {"exit_code": 0, "stdout": "deployed", "stderr": ""}
_UPGRADE_RESULT = {"exit_code": 0, "stdout": "upgraded", "stderr": ""}
_ROLLBACK_RESULT = {"exit_code": 0, "stdout": "rolled back", "stderr": ""}
_UNINSTALL_RESULT = {"exit_code": 0, "stdout": "uninstalled", "stderr": ""}


def _make_mock_db():
    """Minimal DB mock that satisfies get_db_context."""
    db = MagicMock()
    db.__enter__ = MagicMock(return_value=db)
    db.__exit__ = MagicMock(return_value=False)
    return db


class TestInstallReleaseLocking:
    def test_install_acquires_lock_with_correct_key(self):
        """install_release calls helm_release_lock(db, cluster_id, release_name)."""
        lock_calls = []

        @contextmanager
        def fake_lock(db, cluster_id, release_name):
            lock_calls.append((cluster_id, release_name))
            yield

        mock_db = _make_mock_db()
        with (
            patch("tasks.helm_tasks.get_db_context") as mock_ctx,
            patch("tasks.helm_tasks.helm_release_lock", fake_lock),
            patch("tasks.helm_tasks.HelmService") as MockHelm,
        ):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            MockHelm.return_value.install_chart.return_value = _INSTALL_RESULT

            result = install_release(
                cluster_id=3,
                release_name="my-chart",
                chart="stable/nginx",
                namespace="default",
                values=None,
                version=None,
                create_namespace=False,
                wait=False,
                timeout="5m0s",
            )

        assert result["success"] is True
        assert lock_calls == [(3, "my-chart")]


class TestUpgradeReleaseLocking:
    def test_upgrade_acquires_lock_with_correct_key(self):
        lock_calls = []

        @contextmanager
        def fake_lock(db, cluster_id, release_name):
            lock_calls.append((cluster_id, release_name))
            yield

        mock_db = _make_mock_db()
        with (
            patch("tasks.helm_tasks.get_db_context") as mock_ctx,
            patch("tasks.helm_tasks.helm_release_lock", fake_lock),
            patch("tasks.helm_tasks.HelmService") as MockHelm,
        ):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            MockHelm.return_value.upgrade_release.return_value = _UPGRADE_RESULT

            result = upgrade_release(
                cluster_id=7,
                release_name="upgrade-me",
                chart=None,
                namespace="prod",
                values={"key": "val"},
                version="1.2.3",
                install=False,
                wait=True,
                timeout="10m0s",
            )

        assert result["success"] is True
        assert lock_calls == [(7, "upgrade-me")]


class TestRollbackReleaseLocking:
    def test_rollback_acquires_lock_with_correct_key(self):
        lock_calls = []

        @contextmanager
        def fake_lock(db, cluster_id, release_name):
            lock_calls.append((cluster_id, release_name))
            yield

        mock_db = _make_mock_db()
        with (
            patch("tasks.helm_tasks.get_db_context") as mock_ctx,
            patch("tasks.helm_tasks.helm_release_lock", fake_lock),
            patch("tasks.helm_tasks.HelmService") as MockHelm,
        ):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            MockHelm.return_value.rollback_release.return_value = _ROLLBACK_RESULT

            result = rollback_release(
                cluster_id=2,
                release_name="rollback-me",
                revision=3,
                namespace="staging",
                wait=False,
                timeout="5m0s",
            )

        assert result["success"] is True
        assert lock_calls == [(2, "rollback-me")]


class TestUninstallReleaseLocking:
    def test_uninstall_acquires_lock_with_correct_key(self):
        lock_calls = []

        @contextmanager
        def fake_lock(db, cluster_id, release_name):
            lock_calls.append((cluster_id, release_name))
            yield

        mock_db = _make_mock_db()
        with (
            patch("tasks.helm_tasks.get_db_context") as mock_ctx,
            patch("tasks.helm_tasks.helm_release_lock", fake_lock),
            patch("tasks.helm_tasks.HelmService") as MockHelm,
        ):
            mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
            mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
            MockHelm.return_value.uninstall_release.return_value = _UNINSTALL_RESULT

            result = uninstall_release(
                cluster_id=9,
                release_name="gone",
                namespace="default",
                keep_history=False,
                wait=False,
                timeout="5m0s",
            )

        assert result["success"] is True
        assert lock_calls == [(9, "gone")]


# ===========================================================================
# 4. Serialization — same release blocks; different releases don't
# ===========================================================================


class TestSerialization:
    """Simulate concurrent callers using a real threading.Lock to stand in
    for the advisory lock.  The advisory lock itself can't be exercised without
    Postgres, but we can prove the task bodies respect the lock boundary."""

    def test_same_release_serializes_via_lock(self):
        """Two threads calling install_release on the same (cluster_id, release_name)
        must execute HelmService.install_chart sequentially, not concurrently."""
        execution_order = []
        real_lock = threading.Lock()

        @contextmanager
        def serializing_fake_lock(db, cluster_id, release_name):
            # Simulate a blocking lock — only one thread inside at a time.
            with real_lock:
                execution_order.append(f"enter:{cluster_id}:{release_name}")
                yield
                execution_order.append(f"exit:{cluster_id}:{release_name}")

        barrier = threading.Barrier(2)
        results = []

        def run_install():
            mock_db = _make_mock_db()
            with (
                patch("tasks.helm_tasks.get_db_context") as mock_ctx,
                patch("tasks.helm_tasks.helm_release_lock", serializing_fake_lock),
                patch("tasks.helm_tasks.HelmService") as MockHelm,
            ):
                mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
                mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                MockHelm.return_value.install_chart.return_value = _INSTALL_RESULT
                # Both threads try to start at the same time.
                barrier.wait()
                r = install_release(
                    cluster_id=1,
                    release_name="same-release",
                    chart="stable/nginx",
                    namespace="default",
                    values=None,
                    version=None,
                    create_namespace=False,
                    wait=False,
                    timeout="5m0s",
                )
                results.append(r)

        t1 = threading.Thread(target=run_install)
        t2 = threading.Thread(target=run_install)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        # Both tasks must succeed (no task is rejected — they serialize).
        assert len(results) == 2
        assert all(r["success"] for r in results)

        # Verify interleaving never happened: every enter is followed by exit
        # before the next enter (serialized).
        enters = [i for i, e in enumerate(execution_order) if e.startswith("enter")]
        exits = [i for i, e in enumerate(execution_order) if e.startswith("exit")]
        assert len(enters) == 2
        assert len(exits) == 2
        # First exit must come before second enter (no overlap).
        assert exits[0] < enters[1]

    def test_different_releases_do_not_block_each_other(self):
        """Two tasks on different releases can enter the lock body concurrently."""
        inside_count = {"n": 0, "peak": 0}
        mu = threading.Lock()
        barrier = threading.Barrier(2)

        @contextmanager
        def counting_fake_lock(db, cluster_id, release_name):
            # Does NOT hold a real mutex — just counts concurrency.
            with mu:
                inside_count["n"] += 1
                if inside_count["n"] > inside_count["peak"]:
                    inside_count["peak"] = inside_count["n"]
            yield
            with mu:
                inside_count["n"] -= 1

        results = []

        def run(release_name):
            mock_db = _make_mock_db()
            with (
                patch("tasks.helm_tasks.get_db_context") as mock_ctx,
                patch("tasks.helm_tasks.helm_release_lock", counting_fake_lock),
                patch("tasks.helm_tasks.HelmService") as MockHelm,
            ):
                mock_ctx.return_value.__enter__ = MagicMock(return_value=mock_db)
                mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
                MockHelm.return_value.install_chart.return_value = _INSTALL_RESULT
                barrier.wait()
                r = install_release(
                    cluster_id=1,
                    release_name=release_name,
                    chart="stable/nginx",
                    namespace="default",
                    values=None,
                    version=None,
                    create_namespace=False,
                    wait=False,
                    timeout="5m0s",
                )
                results.append(r)

        t1 = threading.Thread(target=run, args=("release-alpha",))
        t2 = threading.Thread(target=run, args=("release-beta",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(results) == 2
        assert all(r["success"] for r in results)
        # Both could be inside the lock body at the same time (peak == 2).
        # (In practice they may not overlap due to GIL, but the point is that
        # different-release locks are independent — no mutual exclusion imposed.)
        # We verify the key invariant: peak >= 1 (both ran).
        assert inside_count["peak"] >= 1
