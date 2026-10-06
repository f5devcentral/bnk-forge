"""ClusterScanner._persist_cluster_metadata stamps sync state only for a reached cluster (#194)."""

from unittest.mock import MagicMock, patch

import pytest

from services.scanner import ClusterScanner


def _scanner(db: MagicMock | None = None):
    db = db or MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    with patch.object(ClusterScanner, "__init__", lambda self, _db: setattr(self, "db", _db)):
        return ClusterScanner(db)


def _cluster():
    cluster = MagicMock()
    cluster.id = 1
    cluster.last_synced_at = None
    cluster.connectivity_status = "in_progress"
    cluster.ssh_tunnel_enabled = False
    return cluster


@pytest.mark.unit
def test_empty_scan_marks_unreachable_without_stamping_sync_time():
    """Every fetcher swallows errors, so an unreachable cluster yields an empty scan.

    That scan must not write a fresh last_synced_at or flip connectivity to
    "connected": it marks the cluster "unreachable" and leaves the sync time.
    """
    cluster = _cluster()
    _scanner()._persist_cluster_metadata(cluster, cluster_info={}, nodes=[])
    assert cluster.last_synced_at is None
    assert cluster.connectivity_status == "unreachable"
    # Non-sync metadata is still written from what is available.
    assert cluster.integration_status == "direct"


@pytest.mark.unit
def test_reached_scan_stamps_sync_time_and_connected():
    cluster = _cluster()
    _scanner()._persist_cluster_metadata(
        cluster, cluster_info={"version": "v1.30.14", "node_count": 3}, nodes=[]
    )
    assert cluster.last_synced_at is not None
    assert cluster.connectivity_status == "connected"
    assert cluster.version == "v1.30.14"
    assert cluster.node_count == 3


@pytest.mark.unit
def test_flush_error_is_logged(caplog):
    db = MagicMock()
    db.flush.side_effect = RuntimeError("db gone")

    _scanner(db)._persist_cluster_metadata(_cluster(), {"version": "v1.30.1"}, [])

    assert "db gone" in caplog.text
