"""
Unit tests for BNK consumption aggregation logic.

Covers the pure functions in ``services.bnk.consumption`` that turn fetched
BNK data + pod metrics into the dashboard response shape.
"""

import pytest

from services.bnk.consumption import aggregate_cluster_consumption, aggregate_fleet_summary


def _make_pod(name: str, namespace: str = "f5-bnk", image: str = "f5/spk-tmm:v2.5.0") -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "phase": "Running",
        "containers": [{"name": "tmm", "image": image, "ready": True}],
    }


def _make_metric(name: str, namespace: str, cpu: int, memory: int) -> dict:
    return {"name": name, "namespace": namespace, "cpu_millicores": cpu, "memory_bytes": memory}


class TestAggregateClusterConsumption:
    def test_unreachable_cluster_returns_zeros(self):
        result = aggregate_cluster_consumption(
            cluster_id=1,
            cluster_name="offline",
            node_count=3,
            status="connected",
            bnk_data=None,
            pod_metrics_response=None,
            dpf_summary=None,
            reachable=False,
        )

        assert result["cluster_id"] == 1
        assert result["reachable"] is False
        assert result["bnk_installed"] is False
        assert result["status"] == "offline"
        assert result["control_plane"]["count"] == 0
        assert result["data_plane"]["count"] == 0
        assert result["metrics_available"] is False

    def test_cluster_without_bnk(self):
        result = aggregate_cluster_consumption(
            cluster_id=2,
            cluster_name="plain-k8s",
            node_count=3,
            status="connected",
            bnk_data={"classified_pods": {}},
            pod_metrics_response={"available": True, "metrics": []},
            dpf_summary={"detected": False, "dpu_count": 0},
            reachable=True,
        )

        assert result["reachable"] is True
        assert result["bnk_installed"] is False
        assert result["total"]["count"] == 0

    def test_metrics_unavailable_gracefully_degrades(self):
        bnk_data = {
            "classified_pods": {
                "tmm": [_make_pod("f5-tmm-abc", image="f5/spk-tmm:v2.5.0")],
            },
        }
        result = aggregate_cluster_consumption(
            cluster_id=3,
            cluster_name="no-metrics",
            node_count=3,
            status="connected",
            bnk_data=bnk_data,
            pod_metrics_response={"available": False, "error": "Metrics server not installed"},
            dpf_summary={"detected": False, "dpu_count": 0},
            reachable=True,
        )

        assert result["metrics_available"] is False
        assert result["metrics_error"] == "Metrics server not installed"
        assert result["data_plane"]["count"] == 1
        assert result["data_plane"]["cpu_millicores"] == 0
        assert result["data_plane"]["memory_bytes"] == 0

    def test_sums_resources_by_plane(self):
        bnk_data = {
            "classified_pods": {
                "tmm": [
                    _make_pod("f5-tmm-a", image="f5/spk-tmm:v2.5.0"),
                    _make_pod("f5-tmm-b", image="f5/spk-tmm:v2.5.0"),
                ],
                "controller": [
                    _make_pod("f5ingress-ctrl", image="f5/bnk-controller:v2.5.0"),
                ],
                "flo": [
                    _make_pod("flo-operator", image="f5/ln-operator:v2.5.0"),
                ],
            },
        }
        metrics = {
            "available": True,
            "metrics": [
                _make_metric("f5-tmm-a", "f5-bnk", 1000, 2_000_000_000),
                _make_metric("f5-tmm-b", "f5-bnk", 500, 1_000_000_000),
                _make_metric("f5ingress-ctrl", "f5-bnk", 200, 500_000_000),
                _make_metric("flo-operator", "f5-bnk", 150, 400_000_000),
            ],
        }

        result = aggregate_cluster_consumption(
            cluster_id=4,
            cluster_name="bnk-prod",
            node_count=6,
            status="connected",
            bnk_data=bnk_data,
            pod_metrics_response=metrics,
            dpf_summary={"detected": True, "dpu_count": 2},
            reachable=True,
        )

        assert result["bnk_installed"] is True
        assert result["bnk_version"] == "2.5.0"
        assert result["dpf"]["detected"] is True
        assert result["dpf"]["dpu_count"] == 2
        assert result["data_plane"]["count"] == 2
        assert result["data_plane"]["cpu_millicores"] == 1500
        assert result["data_plane"]["memory_bytes"] == 3_000_000_000
        assert result["control_plane"]["count"] == 2
        assert result["control_plane"]["cpu_millicores"] == 350
        assert result["total"]["count"] == 4
        assert result["total"]["cpu_millicores"] == 1850
        assert result["total"]["memory_bytes"] == 3_900_000_000
        assert result["metrics_available"] is True
        assert result["metrics_error"] is None

    def test_top_pods_sorted_by_cpu(self):
        bnk_data = {
            "classified_pods": {
                "tmm": [
                    _make_pod("f5-tmm-a"),
                    _make_pod("f5-tmm-b"),
                ],
            },
        }
        metrics = {
            "available": True,
            "metrics": [
                _make_metric("f5-tmm-a", "f5-bnk", 500, 1_000_000_000),
                _make_metric("f5-tmm-b", "f5-bnk", 1000, 2_000_000_000),
            ],
        }

        result = aggregate_cluster_consumption(
            cluster_id=5,
            cluster_name="top-prod",
            node_count=3,
            status="connected",
            bnk_data=bnk_data,
            pod_metrics_response=metrics,
            dpf_summary={"detected": False, "dpu_count": 0},
            reachable=True,
        )

        assert len(result["top_pods"]) == 2
        assert result["top_pods"][0]["name"] == "f5-tmm-b"
        assert result["top_pods"][0]["cpu_millicores"] == 1000


class TestAggregateFleetSummary:
    def test_rollup_across_clusters(self):
        clusters = [
            {
                "reachable": True,
                "bnk_installed": True,
                "control_plane": {"count": 2, "cpu_millicores": 200, "memory_bytes": 400_000_000},
                "data_plane": {"count": 3, "cpu_millicores": 1500, "memory_bytes": 3_000_000_000},
                "total": {"count": 5, "cpu_millicores": 1700, "memory_bytes": 3_400_000_000},
                "dpf": {"detected": True, "dpu_count": 2},
            },
            {
                "reachable": False,
                "bnk_installed": False,
                "control_plane": {"count": 0, "cpu_millicores": 0, "memory_bytes": 0},
                "data_plane": {"count": 0, "cpu_millicores": 0, "memory_bytes": 0},
                "total": {"count": 0, "cpu_millicores": 0, "memory_bytes": 0},
                "dpf": {"detected": False, "dpu_count": 0},
            },
            {
                "reachable": True,
                "bnk_installed": False,
                "control_plane": {"count": 0, "cpu_millicores": 0, "memory_bytes": 0},
                "data_plane": {"count": 0, "cpu_millicores": 0, "memory_bytes": 0},
                "total": {"count": 0, "cpu_millicores": 0, "memory_bytes": 0},
                "dpf": {"detected": False, "dpu_count": 0},
            },
        ]

        summary = aggregate_fleet_summary(clusters)

        assert summary["total_clusters"] == 3
        assert summary["reachable_clusters"] == 2
        assert summary["bnk_installed_clusters"] == 1
        assert summary["total_bnk_pods"] == 5
        assert summary["control_plane_pods"] == 2
        assert summary["data_plane_pods"] == 3
        assert summary["total_cpu_millicores"] == 1700
        assert summary["total_memory_bytes"] == 3_400_000_000
        assert summary["dpf_detected_clusters"] == 1
        assert summary["dpu_count"] == 2

    def test_empty_fleet(self):
        summary = aggregate_fleet_summary([])
        assert summary["total_clusters"] == 0
        assert summary["reachable_clusters"] == 0
        assert summary["total_bnk_pods"] == 0
        assert summary["total_cpu_millicores"] == 0
