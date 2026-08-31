"""Unit tests for GCP service helpers."""

import yaml

from services.gcp_service import generate_gke_kubeconfig


def test_generate_gke_kubeconfig():
    """GKE kubeconfig uses the gke-gcloud-auth-plugin exec plugin."""
    kubeconfig_yaml = generate_gke_kubeconfig(
        cluster_name="gke-prod",
        project_id="my-gcp-project",
        location="us-central1",
        server="https://1.2.3.4",
        certificate_authority_data="LS0tLS1CRUdJTi4u.",
    )

    cfg = yaml.safe_load(kubeconfig_yaml)
    assert cfg["apiVersion"] == "v1"
    assert cfg["kind"] == "Config"
    assert cfg["current-context"] == "gke-prod"

    cluster = cfg["clusters"][0]
    assert cluster["name"] == "gke-prod"
    assert cluster["cluster"]["server"] == "https://1.2.3.4"
    assert cluster["cluster"]["certificate-authority-data"] == "LS0tLS1CRUdJTi4u."

    user = cfg["users"][0]
    assert user["name"] == "gke-prod"
    exec_cfg = user["user"]["exec"]
    assert exec_cfg["command"] == "gke-gcloud-auth-plugin"
    assert exec_cfg["provideClusterInfo"] is True
    env_project = next(
        (e for e in exec_cfg["env"] if e["name"] == "CLOUDSDK_CORE_PROJECT"), None
    )
    assert env_project is not None
    assert env_project["value"] == "my-gcp-project"
