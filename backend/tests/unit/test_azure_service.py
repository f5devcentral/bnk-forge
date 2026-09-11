"""Unit tests for Azure service helpers."""

import yaml

from services.azure_service import _resource_group_from_id, generate_aks_kubeconfig


def test_generate_aks_kubeconfig():
    """AKS kubeconfig embeds a bearer token and contains no local file refs."""
    kubeconfig_yaml = generate_aks_kubeconfig(
        cluster_name="aks-prod",
        server="aks-prod.hcp.eastus.azmk8s.io",
        ca_data="LS0tLS1CRUdJTi4u.",
        token="test-aad-token",
    )

    cfg = yaml.safe_load(kubeconfig_yaml)
    assert cfg["apiVersion"] == "v1"
    assert cfg["kind"] == "Config"
    assert cfg["current-context"] == "aks-prod"

    cluster = cfg["clusters"][0]
    assert cluster["name"] == "aks-prod"
    assert cluster["cluster"]["server"] == "https://aks-prod.hcp.eastus.azmk8s.io:443"
    assert cluster["cluster"]["certificate-authority-data"] == "LS0tLS1CRUdJTi4u."

    user = cfg["users"][0]
    assert user["name"] == "aks-prod"
    assert user["user"]["token"] == "test-aad-token"


def test_resource_group_from_id():
    assert _resource_group_from_id(
        "/subscriptions/sub-123/resourceGroups/rg-prod/providers/Microsoft.ContainerService/managedClusters/aks-prod"
    ) == "rg-prod"
    assert _resource_group_from_id("/subscriptions/sub-123/") is None
