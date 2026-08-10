"""
Golden contract tests — Cluster Management domain.

Validates that cluster endpoints return responses matching their declared
Pydantic response_model schemas.

Endpoints tested:
- GET  /api/k8s/clusters                    → ClusterListResponse
- GET  /api/k8s/clusters/{id}               → ClusterDetailResponse
- POST /api/k8s/clusters/{id}/test          → ClusterConnectionTestResponse
"""

from schemas.k8s import (
    ClusterConnectionTestResponse,
    ClusterDetailResponse,
    ClusterListResponse,
)

# ------------------------------------------------------------------ #
# GET /api/k8s/clusters → ClusterListResponse
# ------------------------------------------------------------------ #


class TestClusterListContract:
    """GET /api/k8s/clusters returns shape matching ClusterListResponse."""

    def test_cluster_list_response_shape(
        self, client, admin_headers, sample_user, seed_clusters
    ):
        """L1+L2: Response parses through ClusterListResponse, required fields present."""
        response = client.get("/api/k8s/clusters", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable through Pydantic schema
        parsed = ClusterListResponse.model_validate(data)

        # L2: Required fields
        assert isinstance(parsed.clusters, list)
        assert len(parsed.clusters) == 3
        assert parsed.count == 3

        # Each cluster has required fields
        for cluster in parsed.clusters:
            assert isinstance(cluster.id, int)
            assert isinstance(cluster.name, str)
            assert cluster.name != ""
            assert cluster.detected_platform_profile in {
                "generic_onprem",
                "eks",
                "aks",
                "gke",
                "ocp",
                "unknown",
            }
            assert isinstance(cluster.platform_capabilities.model_dump(), dict)
            assert isinstance(cluster.platform_constraints.model_dump(), dict)

    def test_cluster_list_empty(self, client, admin_headers, sample_user):
        """ClusterListResponse works with zero clusters."""
        response = client.get("/api/k8s/clusters", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        parsed = ClusterListResponse.model_validate(data)
        assert parsed.clusters == []
        assert parsed.count == 0


# ------------------------------------------------------------------ #
# GET /api/k8s/clusters/{id} → ClusterDetailResponse
# ------------------------------------------------------------------ #


class TestClusterDetailContract:
    """GET /api/k8s/clusters/{id} returns shape matching ClusterDetailResponse."""

    def test_cluster_detail_response_shape(
        self, client, admin_headers, sample_user, seed_cluster
    ):
        """L1+L2: Response parses through ClusterDetailResponse, required fields present."""
        response = client.get(
            f"/api/k8s/clusters/{seed_cluster.id}", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable
        parsed = ClusterDetailResponse.model_validate(data)

        # L2: Required fields
        assert parsed.id == seed_cluster.id
        assert isinstance(parsed.name, str)
        assert parsed.name == "contract-test-cluster"
        assert isinstance(parsed.status, str)
        assert parsed.detected_platform_profile in {
            "generic_onprem",
            "eks",
            "aks",
            "gke",
            "ocp",
            "unknown",
        }

    def test_cluster_detail_vocabulary(
        self, client, admin_headers, sample_user, seed_cluster
    ):
        """L3: Status field uses valid vocabulary."""
        response = client.get(
            f"/api/k8s/clusters/{seed_cluster.id}", headers=admin_headers
        )
        data = response.json()
        parsed = ClusterDetailResponse.model_validate(data)

        valid_statuses = {"active", "inactive", "error", "pending"}
        assert parsed.status in valid_statuses, f"Unexpected status: {parsed.status}"


# ------------------------------------------------------------------ #
# POST /api/k8s/clusters/{id}/test → ClusterConnectionTestResponse
# ------------------------------------------------------------------ #


class TestClusterConnectionTestContract:
    """POST /api/k8s/clusters/{id}/test returns shape matching ClusterConnectionTestResponse."""

    def test_connection_test_response_shape(
        self, client, admin_headers, sample_user, sample_project, db, mock_k8s_test_connection
    ):
        """L1+L2: Response parses through schema, required fields present.

        Note: require_cluster_owner needs the cluster to belong to a project.
        Admin users bypass ownership check but the project must still exist.
        """
        from models import KubernetesCluster

        cluster = KubernetesCluster(
            name="owned-test-cluster",
            context="owned-ctx",
            api_server="https://10.0.0.1:6443",
            cloud_provider="on-prem",
            region="ap-southeast-1",
            status="active",
            version="1.28",
            project_id=sample_project.id,
        )
        db.add(cluster)
        db.commit()
        db.refresh(cluster)

        # Re-patch with this cluster's info
        from unittest.mock import patch

        mock_result = {
            "success": True,
            "message": "Connection successful",
            "cluster_name": cluster.name,
            "version": "v1.28.0",
            "api_server": cluster.api_server,
            "cloud_provider": cluster.cloud_provider,
            "region": cluster.region,
            "status_code": 200,
        }
        with patch(
            "services.kubernetes_service.KubernetesService.test_connection",
            return_value=mock_result,
        ):
            response = client.post(
                f"/api/k8s/clusters/{cluster.id}/test", headers=admin_headers
            )
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable
        parsed = ClusterConnectionTestResponse.model_validate(data)

        # L2: Required fields
        assert isinstance(parsed.success, bool)
        assert isinstance(parsed.message, str)
        assert parsed.success is True
        assert parsed.cluster_name == "owned-test-cluster"
