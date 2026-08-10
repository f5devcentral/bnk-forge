from datetime import UTC, datetime

from models.discovery import DiscoveredNode, DiscoveryJob
from models.enums import DiscoveredNodeStatus, DiscoveryJobStatus
from schemas.discovery import DiscoveryJobResponse


def test_discovery_status_vocabularies_match_expected_values():
    assert {status.value for status in DiscoveryJobStatus} == {
        "pending",
        "in_progress",
        "completed",
        "failed",
        "cancelled",
    }
    assert DiscoveryJobStatus.terminal_states() == {
        DiscoveryJobStatus.COMPLETED,
        DiscoveryJobStatus.FAILED,
        DiscoveryJobStatus.CANCELLED,
    }

    assert {status.value for status in DiscoveredNodeStatus} == {
        "pending",
        "connecting",
        "discovering",
        "completed",
        "failed",
    }


def test_discovery_response_is_secret_safe_and_has_credential_flags():
    now = datetime.now(tz=UTC)
    job = DiscoveryJob(
        id=100,
        project_id=44,
        ssh_credential_id=None,
        ssh_username="ubuntu",
        ssh_port=22,
        ssh_auth_type="key",
        ssh_password_encrypted="enc-pass",
        ssh_key_encrypted="enc-key",
        status=DiscoveryJobStatus.PENDING.value,
        total_nodes=1,
        completed_nodes=0,
        failed_nodes=0,
        created_at=now,
    )

    node = DiscoveredNode(
        id=200,
        discovery_job_id=100,
        ip_address="10.0.0.10",
        ssh_username="ubuntu",
        ssh_auth_type="password",
        ssh_password_encrypted="enc-node-pass",
        ssh_key_encrypted=None,
        status=DiscoveredNodeStatus.PENDING.value,
        created_at=now,
        dpu_count=0,
        is_dpu_host=False,
        is_dpu_node=False,
        k8s_installed=False,
        k8s_running=False,
    )
    job.nodes = [node]

    response = DiscoveryJobResponse.from_model(job)
    payload = response.model_dump()

    assert response.has_shared_credentials is True
    assert response.nodes[0].has_ssh_credentials is True

    assert "ssh_username" not in payload
    assert "ssh_port" not in payload
    assert "ssh_auth_type" not in payload

    assert "ssh_password_encrypted" not in payload
    assert "ssh_key_encrypted" not in payload
    assert "discovery_log" not in payload["nodes"][0]
    assert "ssh_password_encrypted" not in payload["nodes"][0]
    assert "ssh_key_encrypted" not in payload["nodes"][0]


def test_discovery_response_can_omit_nodes_for_summary_surfaces():
    now = datetime.now(tz=UTC)
    job = DiscoveryJob(
        id=5,
        project_id=9,
        status=DiscoveryJobStatus.COMPLETED.value,
        total_nodes=3,
        completed_nodes=3,
        failed_nodes=0,
        created_at=now,
    )
    job.nodes = [
        DiscoveredNode(
            id=6,
            discovery_job_id=5,
            ip_address="10.0.0.11",
            status=DiscoveredNodeStatus.COMPLETED.value,
            created_at=now,
            is_dpu_host=False,
            is_dpu_node=False,
            dpu_count=0,
            k8s_installed=False,
            k8s_running=False,
        )
    ]

    response = DiscoveryJobResponse.from_model(job, include_nodes=False)

    assert response.nodes == []
    assert response.total_nodes == 3
