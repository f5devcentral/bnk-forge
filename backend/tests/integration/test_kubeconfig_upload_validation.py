"""
Integration tests for kubeconfig portability validation on cluster create/update routes.

Verifies that:
  - POST /api/projects/{pid}/k8s/clusters with an unportable kubeconfig → 422
    with error.code == "KUBECONFIG_UNPORTABLE" and details.field set correctly.
  - PATCH /api/k8s/clusters/{id} with an unportable kubeconfig → same.
  - Portable kubeconfigs pass through without error.
  - The error envelope matches the canonical AppError shape.
"""
import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent.parent / "unit" / "fixtures" / "kubeconfigs"


def _b64(yaml_text: str) -> str:
    return base64.b64encode(yaml_text.encode()).decode()


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class TestClusterCreateValidation:
    """POST /api/projects/{pid}/k8s/clusters — kubeconfig portability gate."""

    def test_path_ca_returns_422(self, client, admin_headers, sample_user, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "ocp-cluster",
                "kubeconfig": _b64(_load_fixture("path_ca.yaml")),
            },
            headers=admin_headers,
        )
        assert response.status_code == 422
        body = response.json()
        err = body["error"]
        assert err["code"] == "KUBECONFIG_UNPORTABLE"
        assert err["details"]["field"] == "certificate-authority"
        assert "user_message" in err["details"]
        assert "/etc/pki" in err["details"]["user_message"]

    def test_path_client_cert_returns_422(self, client, admin_headers, sample_user, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "cert-cluster",
                "kubeconfig": _b64(_load_fixture("path_client_cert.yaml")),
            },
            headers=admin_headers,
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "KUBECONFIG_UNPORTABLE"
        assert body["error"]["details"]["field"] == "client-certificate"

    def test_path_client_key_returns_422(self, client, admin_headers, sample_user, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "key-cluster",
                "kubeconfig": _b64(_load_fixture("path_client_key.yaml")),
            },
            headers=admin_headers,
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "KUBECONFIG_UNPORTABLE"
        assert body["error"]["details"]["field"] == "client-key"

    def test_path_token_file_returns_422(self, client, admin_headers, sample_user, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "token-cluster",
                "kubeconfig": _b64(_load_fixture("path_token_file.yaml")),
            },
            headers=admin_headers,
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "KUBECONFIG_UNPORTABLE"
        assert body["error"]["details"]["field"] == "tokenFile"

    def test_exec_unknown_returns_422(self, client, admin_headers, sample_user, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "oidc-cluster",
                "kubeconfig": _b64(_load_fixture("exec_unknown.yaml")),
            },
            headers=admin_headers,
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "KUBECONFIG_UNPORTABLE"
        assert body["error"]["details"]["field"] == "exec.command"
        assert "unknown-auth-plugin" in body["error"]["details"]["user_message"]

    def test_exec_aks_kubelogin_succeeds(self, client, admin_headers, sample_user, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "aks-cluster",
                "kubeconfig": _b64(_load_fixture("exec_aks.yaml")),
                "cloud_provider": "aks",
                "region": "westeurope",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200

    def test_multi_cluster_partial_returns_422(self, client, admin_headers, sample_user, sample_project):
        """Even if current-context cluster is portable, other clusters must be too."""
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "multi-cluster",
                "kubeconfig": _b64(_load_fixture("multi_cluster_partial.yaml")),
            },
            headers=admin_headers,
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "KUBECONFIG_UNPORTABLE"

    def test_flat_eks_succeeds(self, client, admin_headers, sample_user, sample_project, db):
        """A fully portable EKS kubeconfig should be accepted."""
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "eks-cluster",
                "kubeconfig": _b64(_load_fixture("flat_eks.yaml")),
                "cloud_provider": "aws",
                "region": "us-east-1",
            },
            headers=admin_headers,
        )
        # 200 means it passed validation and was persisted
        assert response.status_code == 200

    def test_mixed_data_and_path_succeeds(self, client, admin_headers, sample_user, sample_project, db):
        """When both *-data and path are present, data wins → accepted."""
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "mixed-cluster",
                "kubeconfig": _b64(_load_fixture("mixed_data_and_path.yaml")),
            },
            headers=admin_headers,
        )
        assert response.status_code == 200

    def test_error_envelope_shape(self, client, admin_headers, sample_user, sample_project):
        """The 422 envelope must match the canonical AppError shape."""
        response = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "shape-test",
                "kubeconfig": _b64(_load_fixture("path_ca.yaml")),
            },
            headers=admin_headers,
        )
        assert response.status_code == 422
        body = response.json()
        # Canonical shape: {"error": {"code": ..., "message": ..., "details": {...}}}
        assert "error" in body
        err = body["error"]
        assert "code" in err
        assert "message" in err
        assert "details" in err
        assert isinstance(err["details"], dict)


class TestClusterUpdateValidation:
    """PUT /api/k8s/clusters/{id} — kubeconfig portability gate on update."""

    def _make_cluster(self, client, admin_headers, sample_project):
        """Helper to create a cluster with a valid kubeconfig first."""
        resp = client.post(
            f"/api/projects/{sample_project.id}/k8s/clusters",
            json={
                "name": "update-test-cluster",
                "kubeconfig": _b64(_load_fixture("flat_eks.yaml")),
                "cloud_provider": "aws",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        return resp.json()["id"]

    def test_update_with_path_ca_returns_422(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        cluster_id = self._make_cluster(client, admin_headers, sample_project)
        response = client.put(
            f"/api/k8s/clusters/{cluster_id}",
            json={"kubeconfig": _b64(_load_fixture("path_ca.yaml"))},
            headers=admin_headers,
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "KUBECONFIG_UNPORTABLE"
        assert body["error"]["details"]["field"] == "certificate-authority"

    def test_update_with_flat_kubeconfig_succeeds(
        self, client, admin_headers, sample_user, sample_project, db
    ):
        cluster_id = self._make_cluster(client, admin_headers, sample_project)
        response = client.put(
            f"/api/k8s/clusters/{cluster_id}",
            json={"kubeconfig": _b64(_load_fixture("flat_gke.yaml"))},
            headers=admin_headers,
        )
        assert response.status_code == 200
