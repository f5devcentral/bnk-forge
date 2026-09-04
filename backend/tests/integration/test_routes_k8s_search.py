"""
Integration tests for Global K8s and Infrastructure search — GET /api/k8s/search.
"""
from unittest.mock import MagicMock, patch

import pytest

from models import KubernetesCluster, Project
from routes.k8s.search import IngressSearchResult


class TestGlobalSearch:
    """GET /api/k8s/search"""

    def test_search_clusters_and_projects_from_db(
        self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster
    ):
        """Search matches clusters and projects by name and cloud provider."""
        cluster = make_k8s_cluster(
            project=sample_project,
            name="eks-prod-us-east-1",
            cloud_provider="aws",
            region="us-east-1",
        )

        with patch("routes.k8s.search._scan_cluster_for_query", return_value=[]):
            response = client.get(
                "/api/k8s/search?q=eks-prod",
                headers=viewer_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["query"] == "eks-prod"
            assert len(data["clusters"]) >= 1
            assert data["clusters"][0]["name"] == "eks-prod-us-east-1"
            assert data["clusters"][0]["cloud_provider"] == "aws"

    def test_search_ingresses_and_fqdns(
        self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster
    ):
        """Search matches Ingress hosts across clusters."""
        cluster = make_k8s_cluster(
            project=sample_project,
            name="eks-prod-us-east-1",
            cloud_provider="aws",
            region="us-east-1",
        )

        mock_ingress_result = [
            IngressSearchResult(
                kind="Ingress",
                name="api-gateway-ingress",
                namespace="core-api",
                matched_host="api.example.com",
                all_hosts=["api.example.com"],
                cluster_id=cluster.id,
                cluster_name=cluster.name,
                cloud_provider="aws",
                region="us-east-1",
                target_service="api-gateway:8080",
                status="active",
            )
        ]

        with patch("routes.k8s.search._scan_cluster_for_query", return_value=mock_ingress_result):
            response = client.get(
                "/api/k8s/search?q=api.example.com",
                headers=viewer_headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["query"] == "api.example.com"
            assert len(data["ingresses"]) == 1
            assert data["ingresses"][0]["matched_host"] == "api.example.com"
            assert data["ingresses"][0]["target_service"] == "api-gateway:8080"
            assert data["ingresses"][0]["cluster_name"] == "eks-prod-us-east-1"
