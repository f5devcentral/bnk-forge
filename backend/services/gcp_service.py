"""GCP-specific helpers for GKE cluster discovery and kubeconfig generation."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
import yaml

from core.encryption import decrypt_value

logger = logging.getLogger(__name__)

GKE_API_URL = "https://container.googleapis.com/v1"


def _gcp_service_account_info_from_template(template) -> dict[str, Any]:
    """Decrypt and parse GCP service-account JSON from a template."""
    sa_json = decrypt_value(template.gcp_credentials_encrypted) if template.gcp_credentials_encrypted else None
    if not sa_json:
        raise ValueError("GCP credentials are required to discover GKE clusters")
    return json.loads(sa_json)


def _gcp_access_token(template) -> str:
    """Mint a GCP access token from the template's service-account JSON."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.service_account import Credentials
    except ImportError as exc:  # pragma: no cover - dependency may be absent in minimal installs
        raise RuntimeError("google-auth is required for GKE cluster discovery") from exc

    sa_info = _gcp_service_account_info_from_template(template)
    creds = Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(Request())
    return creds.token


def list_gke_clusters_from_template(template) -> list[dict[str, Any]]:
    """List GKE clusters across the template's project.

    If no gcp_project_id is configured, an empty list is returned.
    """
    if not template.gcp_project_id:
        raise ValueError("GCP project_id is required to list GKE clusters")

    token = _gcp_access_token(template)
    url = f"{GKE_API_URL}/projects/{template.gcp_project_id}/locations/-/clusters"
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    if response.status_code in {401, 403}:
        raise RuntimeError("GCP credentials are not authorized to list GKE clusters")
    if not response.ok:
        raise RuntimeError(f"GKE cluster list failed with status {response.status_code}")

    clusters = []
    for item in response.json().get("clusters", []):
        name = item.get("name")
        if not name:
            continue

        location = item.get("location", "")
        # Self-link format: projects/.../locations/.../clusters/...
        full_name = item.get("selfLink") or f"projects/{template.gcp_project_id}/locations/{location}/clusters/{name}"
        clusters.append({
            "name": name,
            "project_id": template.gcp_project_id,
            "location": location,
            "full_name": full_name,
            "endpoint": item.get("endpoint"),
            "master_auth": item.get("masterAuth", {}),
            "version": item.get("currentMasterVersion"),
        })

    return clusters


def generate_gke_kubeconfig(
    cluster_name: str,
    project_id: str,
    location: str,
    server: str,
    certificate_authority_data: str,
) -> str:
    """Generate a portable kubeconfig YAML for a GKE cluster.

    Uses the ``gke-gcloud-auth-plugin`` exec plugin. The binary is required at
    runtime; the kubeconfig itself contains no local file references.
    """
    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": cluster_name,
                "cluster": {
                    "server": server,
                    "certificate-authority-data": certificate_authority_data,
                },
            }
        ],
        "contexts": [
            {
                "name": cluster_name,
                "context": {
                    "cluster": cluster_name,
                    "user": cluster_name,
                },
            }
        ],
        "current-context": cluster_name,
        "users": [
            {
                "name": cluster_name,
                "user": {
                    "exec": {
                        "apiVersion": "client.authentication.k8s.io/v1beta1",
                        "command": "gke-gcloud-auth-plugin",
                        "args": [],
                        "env": [
                            {"name": "CLOUDSDK_CORE_PROJECT", "value": project_id},
                        ],
                        "provideClusterInfo": True,
                    }
                },
            }
        ],
    }
    return yaml.dump(kubeconfig, default_flow_style=False)


def fetch_gke_cluster_credentials(
    cluster_name: str,
    project_id: str,
    location: str,
    template,
) -> dict[str, Any]:
    """Fetch GKE cluster endpoint and CA via Container API.

    Returns a dict with ``server`` and ``certificate_authority_data``.
    """
    token = _gcp_access_token(template)
    url = f"{GKE_API_URL}/projects/{project_id}/locations/{location}/clusters/{cluster_name}"
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"GKE cluster get failed: {response.status_code}")

    cluster = response.json()
    endpoint = cluster.get("endpoint")
    ca_data = cluster.get("masterAuth", {}).get("clusterCaCertificate")
    if not endpoint or not ca_data:
        raise RuntimeError(f"GKE cluster {cluster_name} is missing endpoint or CA certificate")

    return {
        "server": f"https://{endpoint}",
        "certificate_authority_data": ca_data,
    }
