"""
Golden contract tests — K8s resource/scan/type domain.

Validates contract shape for Tier 1 K8s resource endpoints plus resource-types
and scan endpoints after response-model hardening.
"""

from unittest.mock import patch

from schemas.k8s import (
    ClusterEventsResponse,
    ClusterScanEnvelope,
    NodeTopResponse,
    PodLogsResponse,
    PodRestartResponse,
    PodTopResponse,
    ResourceDescribeEnvelope,
    ResourceListEnvelope,
    ResourceTypeCatalogResponse,
)


class TestK8sResourceTier1Contracts:
    """Contract tests for 7 Tier-1 K8s resource endpoints."""

    @patch("routes.k8s.resources.KubernetesService")
    def test_resource_list_contract(
        self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster
    ):
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = mock_k8s_svc_cls.return_value
        mock_svc.get_resources.return_value = [
            {
                "name": "nginx",
                "namespace": "default",
                "kind": "Pod",
                "apiVersion": "v1",
                "metadata": {"name": "nginx", "namespace": "default"},
            }
        ]

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/resources/pod?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        parsed = ResourceListEnvelope.model_validate(response.json())
        assert parsed.cluster_id == cluster.id
        assert parsed.resource_type == "pod"
        assert parsed.count == 1

    @patch("routes.k8s.resources.KubernetesService")
    def test_resource_describe_contract(
        self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster
    ):
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = mock_k8s_svc_cls.return_value
        mock_svc.describe_resource.return_value = {
            "name": "nginx",
            "namespace": "default",
            "kind": "Deployment",
            "api_version": "apps/v1",
            "metadata": {"name": "nginx", "namespace": "default"},
            "spec": {"replicas": 2},
            "status": {"readyReplicas": 2},
            "conditions": [{"type": "Available", "status": "True"}],
            "events": [{"type": "Normal", "reason": "ScalingReplicaSet"}],
            "relationships": {"owned": [{"kind": "ReplicaSet", "name": "nginx-rs"}]},
        }

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/resources/deployment/nginx/describe?namespace=default",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        parsed = ResourceDescribeEnvelope.model_validate(response.json())
        assert parsed.cluster_id == cluster.id
        assert parsed.resource_type == "deployment"
        assert parsed.resource_name == "nginx"

    @patch("routes.k8s.resources.KubernetesService")
    def test_pod_logs_contract(
        self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster
    ):
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = mock_k8s_svc_cls.return_value
        mock_svc.get_pod_logs.return_value = "line1\nline2"

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/pods/test-pod/logs?namespace=default&container=app",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        parsed = PodLogsResponse.model_validate(response.json())
        assert parsed.cluster_id == cluster.id
        assert parsed.pod_name == "test-pod"
        assert parsed.container == "app"

    @patch("routes.k8s.resources.KubernetesService")
    def test_cluster_events_contract(
        self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster
    ):
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = mock_k8s_svc_cls.return_value
        mock_svc.get_events.return_value = [
            {"type": "Warning", "reason": "BackOff", "message": "Back-off restarting"}
        ]

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/events?namespace=default&event_type=Warning",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        parsed = ClusterEventsResponse.model_validate(response.json())
        assert parsed.cluster_id == cluster.id
        assert parsed.count == 1
        assert parsed.event_type == "Warning"

    @patch("routes.k8s.resources.KubernetesService")
    def test_pod_top_contract(
        self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster
    ):
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = mock_k8s_svc_cls.return_value
        mock_svc.get_pod_metrics.return_value = {
            "available": True,
            "metrics": [{"name": "pod-a", "namespace": "default", "cpu_millicores": 10}],
        }

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/top/pods?namespace=default&sort_by=cpu",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        parsed = PodTopResponse.model_validate(response.json())
        assert parsed.cluster_id == cluster.id
        assert parsed.available is True
        assert parsed.sort_by == "cpu"

    @patch("routes.k8s.resources.KubernetesService")
    def test_node_top_contract(
        self, mock_k8s_svc_cls, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster
    ):
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = mock_k8s_svc_cls.return_value
        mock_svc.get_node_metrics.return_value = {
            "available": True,
            "metrics": [{"name": "node-a", "cpu_millicores": 500}],
        }

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/top/nodes?sort_by=memory",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        parsed = NodeTopResponse.model_validate(response.json())
        assert parsed.cluster_id == cluster.id
        assert parsed.available is True
        assert parsed.sort_by == "memory"

    @patch("routes.k8s.resources.KubernetesService")
    def test_pod_restart_contract(
        self, mock_k8s_svc_cls, client, admin_headers, sample_user, sample_project, make_k8s_cluster
    ):
        cluster = make_k8s_cluster(project=sample_project)
        mock_svc = mock_k8s_svc_cls.return_value
        mock_svc.restart_pod.return_value = {
            "success": True,
            "message": "Pod deleted for restart",
        }

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/pods/test-pod/restart?namespace=default",
            headers=admin_headers,
        )
        assert response.status_code == 200
        parsed = PodRestartResponse.model_validate(response.json())
        assert parsed.cluster_id == cluster.id
        assert parsed.success is True


class TestResourceTypesAndScanContracts:
    """Contract tests for CT-B17 (resource-types) and CT-B16 (scan)."""

    def test_resource_types_contract(self, client, viewer_headers, all_test_users):
        response = client.get("/api/k8s/resource-types", headers=viewer_headers)
        assert response.status_code == 200

        parsed = ResourceTypeCatalogResponse.model_validate(response.json())
        assert parsed.count > 0
        assert len(parsed.resource_types) == parsed.count
        assert all(rt.key for rt in parsed.resource_types)
        assert all(rt.category for rt in parsed.resource_types)

    @patch("services.cluster_scanner.ClusterScanner.scan")
    def test_cluster_scan_contract(
        self,
        mock_scan,
        client,
        admin_headers,
        sample_user,
        sample_project,
        make_k8s_cluster,
    ):
        cluster = make_k8s_cluster(project=sample_project, name="scan-cluster")
        mock_scan.return_value = {
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "cluster_info": {"distribution": "on-prem", "node_count": 2},
            "prerequisites": {"cert_manager": {"status": "detected"}},
            "bnk_install": {"status": "not_installed", "health": None},
            "recommendations": [{"id": "rec-1", "severity": "required"}],
            "scan_metadata": {
                "scanned_at": "2026-03-27T12:00:00+00:00",
                "duration_ms": 123,
                "api_calls": 22,
            },
            "platform_context": {
                "detected_platform_profile": "generic_onprem",
                "detected_platform_provider": "baremetal",
                "platform_capabilities": {
                    "gateway_api": True,
                },
                "platform_constraints": {
                    "cluster_lifecycle_external": None,
                },
            },
        }

        response = client.post(
            f"/api/k8s/clusters/{cluster.id}/scan",
            headers=admin_headers,
        )
        assert response.status_code == 200
        parsed = ClusterScanEnvelope.model_validate(response.json())
        assert parsed.cluster_id == cluster.id
        assert parsed.cluster_name == cluster.name
        assert isinstance(parsed.recommendations, list)
        assert isinstance(parsed.platform_context, dict)
        assert parsed.platform_context["detected_platform_profile"] == "generic_onprem"
        assert parsed.platform_context["detected_platform_provider"] == "baremetal"
