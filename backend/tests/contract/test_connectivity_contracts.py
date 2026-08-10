"""
Golden contract tests — Connectivity domain.

Validates that connectivity endpoints return responses matching their
declared Pydantic response_model schemas.

Endpoints tested:
- GET /api/k8s/clusters/{id}/connectivity → ClusterConnectivityResponse
- GET /api/k8s/clusters/connectivity      → BatchConnectivityResponse
"""

from models.enums import ConnectivityStatus
from schemas.k8s import BatchConnectivityResponse, ClusterConnectivityResponse

# ------------------------------------------------------------------ #
# GET /api/k8s/clusters/{id}/connectivity → ClusterConnectivityResponse
# ------------------------------------------------------------------ #


class TestClusterConnectivityContract:
    """GET /api/k8s/clusters/{id}/connectivity returns shape matching ClusterConnectivityResponse."""

    def test_connectivity_response_shape(
        self,
        client,
        admin_headers,
        sample_user,
        seed_cluster,
        mock_connectivity_single,
    ):
        """L1+L2: Response parses through ClusterConnectivityResponse, required fields present."""
        response = client.get(
            f"/api/k8s/clusters/{seed_cluster.id}/connectivity",
            headers=admin_headers,
        )
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable through Pydantic schema
        parsed = ClusterConnectivityResponse.model_validate(data)

        # L2: Required fields present
        assert isinstance(parsed.cluster_id, int)
        assert parsed.cluster_id == seed_cluster.id
        assert isinstance(parsed.cluster_name, str)
        assert isinstance(parsed.message, str)
        assert isinstance(parsed.checked_at, str)

        # Nested probe results
        assert isinstance(parsed.icmp.reachable, bool)
        assert isinstance(parsed.tcp.open, bool)
        assert isinstance(parsed.k8s_api.accessible, bool)

    def test_connectivity_canonical_vocabulary(
        self,
        client,
        admin_headers,
        sample_user,
        seed_cluster,
        mock_connectivity_single,
    ):
        """L3: Status uses canonical ConnectivityStatus values."""
        response = client.get(
            f"/api/k8s/clusters/{seed_cluster.id}/connectivity",
            headers=admin_headers,
        )
        data = response.json()
        parsed = ClusterConnectivityResponse.model_validate(data)

        valid_statuses = {s.value for s in ConnectivityStatus}
        assert parsed.status in valid_statuses, (
            f"Unexpected connectivity status: {parsed.status}"
        )


# ------------------------------------------------------------------ #
# GET /api/k8s/clusters/connectivity → BatchConnectivityResponse
# ------------------------------------------------------------------ #


class TestBatchConnectivityContract:
    """GET /api/k8s/clusters/connectivity returns shape matching BatchConnectivityResponse."""

    def test_batch_connectivity_response_shape(
        self,
        client,
        admin_headers,
        sample_user,
        seed_clusters,
        mock_connectivity_batch,
    ):
        """L1+L2: Response parses through BatchConnectivityResponse, required fields present."""
        response = client.get(
            "/api/k8s/clusters/connectivity", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable
        parsed = BatchConnectivityResponse.model_validate(data)

        # L2: Required fields
        assert isinstance(parsed.results, list)
        assert len(parsed.results) == 3
        assert parsed.summary is not None
        assert isinstance(parsed.summary.total, int)
        assert parsed.summary.total == 3

        # Summary counts
        assert isinstance(parsed.summary.connected, int)
        assert isinstance(parsed.summary.unreachable, int)
        assert isinstance(parsed.summary.unknown, int)

        # Each result is a valid ClusterConnectivityResponse
        for result in parsed.results:
            assert isinstance(result.cluster_id, int)
            assert isinstance(result.cluster_name, str)
            assert isinstance(result.message, str)
