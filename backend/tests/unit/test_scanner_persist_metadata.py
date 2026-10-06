"""Unit tests for ClusterScanner._persist_cluster_metadata connectivity write-back."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.scanner import ClusterScanner


def _scanner(db: MagicMock) -> ClusterScanner:
    scanner = ClusterScanner.__new__(ClusterScanner)
    scanner.db = db
    return scanner


def _cluster() -> SimpleNamespace:
    return SimpleNamespace(id=1, version=None, node_count=None, zones=None, connectivity_status="connected")


@pytest.mark.parametrize(("version", "expected"), [("v1.30.1", "connected"), (None, "unreachable")])
def test_connectivity_status_follows_version_probe(version, expected):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    cluster = _cluster()

    _scanner(db)._persist_cluster_metadata(cluster, {"version": version}, [])

    assert cluster.connectivity_status == expected


def test_flush_error_is_logged(caplog):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.flush.side_effect = RuntimeError("db gone")

    _scanner(db)._persist_cluster_metadata(_cluster(), {"version": "v1.30.1"}, [])

    assert "db gone" in caplog.text
