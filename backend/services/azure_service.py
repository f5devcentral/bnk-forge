"""Azure-specific helpers for cluster discovery and kubeconfig generation."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests
import yaml

from core.encryption import decrypt_value
from services.azure_oauth_service import request_azure_oauth_token

logger = logging.getLogger(__name__)

AZURE_MANAGEMENT_URL = "https://management.azure.com"
AKS_AAD_SERVER_APP_ID = "6dae42f8-4368-4678-94ff-3960e28e3630"


def _azure_credentials_from_template(template) -> dict[str, str]:
    """Decrypt and parse Azure service-principal credentials from a template."""
    creds_json = decrypt_value(template.azure_credentials_encrypted) if template.azure_credentials_encrypted else None
    if not creds_json:
        raise ValueError("Azure credentials are required to discover AKS clusters")

    creds = json.loads(creds_json)
    client_id = creds.get("client_id") or creds.get("clientId")
    client_secret = creds.get("client_secret") or creds.get("clientSecret")
    if not client_id or not client_secret:
        raise ValueError("Azure credentials must contain client_id and client_secret")

    return {"client_id": client_id, "client_secret": client_secret}


def _azure_management_token(template) -> str:
    """Exchange Azure service-principal credentials for a management access token."""
    if not template.azure_tenant_id:
        raise ValueError("Azure tenant_id is required to discover AKS clusters")

    creds = _azure_credentials_from_template(template)
    token_data = request_azure_oauth_token(
        tenant_id=template.azure_tenant_id,
        data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "scope": "https://management.azure.com/.default",
        },
        timeout=30,
    )
    return token_data["access_token"]


def list_aks_clusters_from_template(template) -> list[dict[str, Any]]:
    """List AKS clusters across the template's subscription.

    If no subscription_id is configured on the template, an empty list is
    returned — Azure requires an explicit subscription scope.
    """
    if not template.azure_subscription_id:
        raise ValueError("Azure subscription_id is required to list AKS clusters")

    token = _azure_management_token(template)
    url = (
        f"{AZURE_MANAGEMENT_URL}/subscriptions/{template.azure_subscription_id}"
        "/providers/Microsoft.ContainerService/managedClusters"
        "?api-version=2023-10-01"
    )
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    if response.status_code in {401, 403}:
        raise RuntimeError("Azure credentials are not authorized to list AKS clusters")
    if not response.ok:
        raise RuntimeError(f"Azure AKS list failed with status {response.status_code}")

    clusters = []
    for item in response.json().get("value", []):
        props = item.get("properties", {})
        name = item.get("name")
        if not name:
            continue

        clusters.append({
            "name": name,
            "resource_group": _resource_group_from_id(item.get("id", "")),
            "subscription_id": template.azure_subscription_id,
            "tenant_id": template.azure_tenant_id,
            "fqdn": props.get("fqdn"),
            "version": props.get("kubernetesVersion"),
            "location": item.get("location"),
        })

    return clusters


def _resource_group_from_id(resource_id: str) -> str | None:
    """Extract resourceGroup from an Azure resource id path."""
    # /subscriptions/.../resourceGroups/.../providers/...
    parts = resource_id.lower().split("/")
    try:
        idx = parts.index("resourcegroups")
        return parts[idx + 1] if idx + 1 < len(parts) else None
    except ValueError:
        return None


def generate_aks_kubeconfig(
    cluster_name: str,
    server: str,
    ca_data: str,
    token: str,
) -> str:
    """Generate a portable kubeconfig YAML for an AKS cluster.

    Embeds a short-lived AAD bearer token inline. The token must be refreshed
    periodically via the cluster kubeconfig refresh path, but the kubeconfig
    itself is portable and contains no local file references or exec plugins.
    """
    kubeconfig = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": cluster_name,
                "cluster": {
                    "server": f"https://{server}:443",
                    "certificate-authority-data": ca_data,
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
                "user": {"token": token},
            }
        ],
    }
    return yaml.dump(kubeconfig, default_flow_style=False)


def fetch_aks_bearer_token(template) -> str:
    """Fetch an AKS AAD server-scoped bearer token for service-principal auth."""
    if not template.azure_tenant_id:
        raise ValueError("Azure tenant_id is required to fetch an AKS bearer token")

    creds = _azure_credentials_from_template(template)
    token_data = request_azure_oauth_token(
        tenant_id=template.azure_tenant_id,
        data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "scope": f"{AKS_AAD_SERVER_APP_ID}/.default",
        },
        timeout=30,
    )
    return token_data["access_token"]


def fetch_aks_cluster_credentials(
    cluster_name: str,
    resource_group: str,
    subscription_id: str,
    template,
) -> dict[str, Any]:
    """Fetch AKS kubeconfig credential bundle via Azure management API.

    Returns a dict with ``server`` and ``certificate_authority_data``.
    """
    token = _azure_management_token(template)
    url = (
        f"{AZURE_MANAGEMENT_URL}/subscriptions/{subscription_id}"
        f"/resourceGroups/{resource_group}"
        "/providers/Microsoft.ContainerService/managedClusters"
        f"/{cluster_name}"
        "?api-version=2023-10-01"
    )
    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Azure AKS cluster get failed: {response.status_code}")

    props = response.json().get("properties", {})
    server = props.get("fqdn")
    ca_data = props.get("certificateAuthority", {}).get("data")
    if not server or not ca_data:
        raise RuntimeError(f"AKS cluster {cluster_name} is missing fqdn or certificate authority")

    return {
        "server": server,
        "certificate_authority_data": ca_data,
    }
