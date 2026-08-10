"""
Shared fixtures for golden contract tests.

These fixtures provide the minimal setup needed to call route handlers
through FastAPI TestClient and validate response shapes against Pydantic
schemas. External services (K8s, Redis, Celery, Helm) are mocked.

All fixtures from the root conftest.py are available (client, db, auth headers,
factory fixtures, etc.).
"""

from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from models import KubernetesCluster

# ---------------------------------------------------------------------------
# Cluster fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seed_cluster(db):
    """Create a single K8s cluster for contract tests."""
    cluster = KubernetesCluster(
        name="contract-test-cluster",
        context="contract-test-ctx",
        api_server="https://10.0.0.1:6443",
        cloud_provider="on-prem",
        region="ap-southeast-1",
        default_namespace="default",
        status="active",
        version="1.28",
    )
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


@pytest.fixture()
def seed_clusters(db):
    """Create multiple K8s clusters for list-endpoint contract tests."""
    clusters = []
    for i in range(3):
        cluster = KubernetesCluster(
            name=f"contract-cluster-{i}",
            context=f"contract-ctx-{i}",
            api_server=f"https://10.0.{i}.1:6443",
            cloud_provider="on-prem",
            region="ap-southeast-1",
            default_namespace="default",
            status="active",
            version="1.28",
        )
        db.add(cluster)
        clusters.append(cluster)
    db.commit()
    for c in clusters:
        db.refresh(c)
    return clusters


# ---------------------------------------------------------------------------
# Connectivity mock fixtures
# ---------------------------------------------------------------------------

def _make_connectivity_result(cluster, status="connected"):
    """Build a connectivity probe result dict matching service output."""
    return {
        "cluster_id": cluster.id,
        "cluster_name": cluster.name,
        "api_server": cluster.api_server,
        "status": status,
        "message": f"Cluster is {status}",
        "suggestion": None,
        "icmp": {"reachable": True, "latency_ms": 1.5},
        "tcp": {"open": True, "connect_ms": 2.0, "port": 6443},
        "k8s_api": {"accessible": True, "version": "v1.28.0", "status_code": 200},
        "checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@pytest.fixture()
def mock_connectivity_single(seed_cluster):
    """Mock ConnectivityProbeService.probe_cluster for a single cluster."""
    result = _make_connectivity_result(seed_cluster)
    with patch(
        "services.connectivity_probe_service.ConnectivityProbeService.probe_cluster",
        return_value=result,
    ) as m:
        yield m


@pytest.fixture()
def mock_connectivity_batch(seed_clusters):
    """Mock ConnectivityProbeService.probe_all_clusters for batch check."""
    results = [_make_connectivity_result(c) for c in seed_clusters]
    summary = {
        "total": len(results),
        "connected": len(results),
        "reachable": 0,
        "partial": 0,
        "unreachable": 0,
        "unknown": 0,
    }
    with patch(
        "services.connectivity_probe_service.ConnectivityProbeService.probe_all_clusters",
        return_value={"results": results, "summary": summary},
    ) as m:
        yield m


# ---------------------------------------------------------------------------
# System health mock fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_system_health():
    """Mock SystemService.get_health with a realistic response."""
    health_data = {
        "services": {
            "backend": {"status": "healthy", "response_time_ms": 0.5},
            "database": {"status": "healthy", "response_time_ms": 1.2},
            "redis": {"status": "offline", "error": "Connection refused"},
            "celery": {"status": "degraded", "workers": 0, "active_tasks": 0, "error": "No workers available"},
        },
        "timestamp": datetime.now(UTC).isoformat(),
    }
    with patch(
        "services.system_service.SystemService.get_health",
        return_value=health_data,
    ) as m:
        yield m


# ---------------------------------------------------------------------------
# Fleet health mock fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_fleet_health():
    """Mock the fleet-health route handler's DB queries to return test data."""
    # The fleet-health handler builds data inline from DB queries.
    # We patch the entire handler return by patching the service calls it uses.
    fleet_data = {
        "total_clusters": 2,
        "healthy": 1,
        "warning": 1,
        "critical": 0,
        "offline": 0,
        "unknown": 0,
        "operators": [
            {
                "operator_id": 1,
                "operator_uuid": "op-uuid-001",
                "cluster_id": 1,
                "cluster_name": "prod-cluster",
                "status": "healthy",
                "bnk_severity": "healthy",
                "effective_connectivity_status": "connected",
                "last_seen": "2026-03-27T10:00:00Z",
                "bnk_version": "1.8.0",
                "route_count": 5,
                "tmm_count": 2,
                "gateway_count": 1,
                "uptime": "5d 3h",
                "health_summary": {"healthy": 3, "warning": 0, "critical": 0},
                "kubernetes_version": "1.28.4",
                "node_count": 3,
                "operator_version": "0.9.2",
                "connectivity_mode": "direct",
                "dpf_detected": False,
                "dpf_version": None,
                "dpf_status": None,
                "dpu_count": 0,
                "dpu_cluster_count": 0,
            },
        ],
    }
    return fleet_data


# ---------------------------------------------------------------------------
# K8s connection test mock fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_k8s_test_connection(seed_cluster):
    """Mock KubernetesService.test_connection with a successful result."""
    result = {
        "success": True,
        "message": "Connection successful",
        "cluster_name": seed_cluster.name,
        "version": "v1.28.0",
        "api_server": seed_cluster.api_server,
        "cloud_provider": seed_cluster.cloud_provider,
        "region": seed_cluster.region,
        "status_code": 200,
    }
    with patch(
        "services.kubernetes_service.KubernetesService.test_connection",
        return_value=result,
    ) as m:
        yield m
