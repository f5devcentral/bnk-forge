"""Unit tests for durable infrastructure access output normalization."""

from services.infrastructure_access_service import (
    INFRA_ACCESS_MESSAGE_FIELD,
    INFRA_ACCESS_STATUS_FIELD,
    INFRA_ACCESS_STATUS_READY,
    INFRA_ACCESS_STATUS_RECOVERY_REQUIRED,
    INFRA_JUMPHOST_COMMAND_FIELD,
    INFRA_PRIVATE_KEY_AVAILABLE_FIELD,
    INFRA_PRIVATE_KEY_PATH_FIELD,
    get_durable_infra_private_key_path,
    normalize_infrastructure_access_outputs,
)

PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAuTestKeyMaterial
-----END RSA PRIVATE KEY-----
"""


def test_normalize_outputs_writes_durable_key_and_rewrites_command(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.infrastructure_access_service.get_durable_infra_private_key_path",
        lambda project_id, module_id: str(tmp_path / f"{project_id}-{module_id}.pem"),
    )

    result = normalize_infrastructure_access_outputs(
        {
            "infrastructure_private_key": PRIVATE_KEY,
            INFRA_PRIVATE_KEY_PATH_FIELD: "~/.ssh/legacy.pem",
            INFRA_JUMPHOST_COMMAND_FIELD: "ssh -i ~/.ssh/legacy.pem ubuntu@1.2.3.4",
        },
        project_id=10,
        module_id=20,
    )

    durable_path = tmp_path / "10-20.pem"
    assert durable_path.exists()
    assert durable_path.read_text().startswith("-----BEGIN RSA PRIVATE KEY-----")
    assert result.outputs[INFRA_PRIVATE_KEY_PATH_FIELD] == str(durable_path)
    assert str(durable_path) in result.outputs[INFRA_JUMPHOST_COMMAND_FIELD]
    assert result.outputs[INFRA_ACCESS_STATUS_FIELD] == INFRA_ACCESS_STATUS_READY
    assert result.outputs[INFRA_PRIVATE_KEY_AVAILABLE_FIELD] is True
    assert "infrastructure_private_key" not in result.outputs


def test_normalize_outputs_degrades_truthfully_when_key_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "services.infrastructure_access_service.get_durable_infra_private_key_path",
        lambda project_id, module_id: str(tmp_path / "missing.pem"),
    )

    result = normalize_infrastructure_access_outputs(
        {
            INFRA_PRIVATE_KEY_PATH_FIELD: "~/.ssh/vanished.pem",
            INFRA_JUMPHOST_COMMAND_FIELD: "ssh -i ~/.ssh/vanished.pem ubuntu@1.2.3.4",
        },
        project_id=1,
        module_id=2,
    )

    assert result.outputs[INFRA_PRIVATE_KEY_PATH_FIELD] is None
    assert result.outputs[INFRA_JUMPHOST_COMMAND_FIELD] is None
    assert result.outputs[INFRA_PRIVATE_KEY_AVAILABLE_FIELD] is False
    assert result.outputs[INFRA_ACCESS_STATUS_FIELD] == INFRA_ACCESS_STATUS_RECOVERY_REQUIRED
    assert "unavailable" in result.outputs[INFRA_ACCESS_MESSAGE_FIELD].lower()


def test_normalize_outputs_repairs_from_existing_legacy_path(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy.pem"
    legacy.write_text(PRIVATE_KEY)
    durable = tmp_path / "durable.pem"

    monkeypatch.setattr(
        "services.infrastructure_access_service.get_durable_infra_private_key_path",
        lambda project_id, module_id: str(durable),
    )

    result = normalize_infrastructure_access_outputs(
        {
            INFRA_PRIVATE_KEY_PATH_FIELD: str(legacy),
            INFRA_JUMPHOST_COMMAND_FIELD: f"ssh -i {legacy} ubuntu@1.2.3.4",
        },
        project_id=1,
        module_id=2,
    )

    assert durable.exists()
    assert result.outputs[INFRA_PRIVATE_KEY_PATH_FIELD] == str(durable)
    assert result.outputs[INFRA_ACCESS_STATUS_FIELD] == INFRA_ACCESS_STATUS_READY


def test_durable_path_is_deterministic():
    p1 = get_durable_infra_private_key_path(9, 99)
    p2 = get_durable_infra_private_key_path(9, 99)
    assert p1 == p2
    assert p1.endswith("/app/state/9/99/infrastructure/infrastructure-access.pem")
