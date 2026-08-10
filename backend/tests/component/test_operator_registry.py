"""
BC-021: Component tests for operator_registry — token creation, operator
registration, heartbeat, disconnect, cleanup, listing.

Uses the real SQLite in-memory DB for persistence.

Note: Token list/revoke tests were removed in D3-CLEANUP (those functions
are no longer exposed). Token creation and validation are still tested as
they're used internally by WebSocket/polling registration.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from models import ConnectedOperator, OperatorRegistrationToken
from models.enums import OperatorStatus
from services.operator_registry import (
    cleanup_stale_operators,
    create_registration_token,
    delete_operator,
    generate_registration_token,
    get_operator,
    list_operators,
    mark_operator_disconnected,
    register_operator,
    update_operator_health,
    update_operator_heartbeat,
    validate_registration_token,
)

# ── Token Generation ─────────────────────────────────────────────────

class TestGenerateToken:
    def test_token_has_prefix(self):
        token = generate_registration_token()
        assert token.startswith("bnk_reg_")

    def test_token_unique(self):
        t1 = generate_registration_token()
        t2 = generate_registration_token()
        assert t1 != t2


# ── Create Registration Token ────────────────────────────────────────

class TestCreateRegistrationToken:
    def test_creates_token(self, db):
        token_record, plaintext = create_registration_token(
            db, name="Test Token", created_by="admin",
        )
        assert token_record.id is not None
        assert token_record.name == "Test Token"
        assert plaintext.startswith("bnk_reg_")
        assert token_record.token_prefix == plaintext[:12]

    def test_max_uses_stored(self, db):
        token_record, _ = create_registration_token(
            db, name="Limited", created_by="admin", max_uses=5,
        )
        assert token_record.max_uses == 5

    def test_labels_stored(self, db):
        token_record, _ = create_registration_token(
            db, name="Labeled", created_by="admin",
            labels={"env": "prod"},
        )
        assert token_record.labels == {"env": "prod"}


# ── Validate Registration Token ──────────────────────────────────────

class TestValidateToken:
    def test_valid_token(self, db):
        _, plaintext = create_registration_token(db, name="Valid", created_by="admin")
        result = validate_registration_token(db, plaintext)
        assert result is not None
        assert result.name == "Valid"

    def test_invalid_token_returns_none(self, db):
        result = validate_registration_token(db, "bnk_reg_invalid")
        assert result is None

    def test_expired_token_returns_none(self, db):
        past = datetime.now(UTC) - timedelta(hours=1)
        _, plaintext = create_registration_token(
            db, name="Expired", created_by="admin", expires_at=past,
        )
        result = validate_registration_token(db, plaintext)
        assert result is None

    def test_exhausted_token_returns_none(self, db):
        token_record, plaintext = create_registration_token(
            db, name="Exhausted", created_by="admin", max_uses=1,
        )
        token_record.use_count = 1
        db.flush()
        result = validate_registration_token(db, plaintext)
        assert result is None


# ── Register Operator ────────────────────────────────────────────────

class TestRegisterOperator:
    def test_new_registration(self, db):
        token_record, _ = create_registration_token(db, name="RegToken", created_by="admin")
        op = register_operator(db, token_record, cluster_name="cluster-1")

        assert op.cluster_name == "cluster-1"
        assert op.is_connected is True
        assert op.status == OperatorStatus.CONNECTED
        assert token_record.use_count == 1

    def test_re_registration_updates_existing(self, db):
        token_record, _ = create_registration_token(db, name="ReReg", created_by="admin")
        op1 = register_operator(db, token_record, cluster_name="cluster-2")

        # Mark disconnected
        op1.is_connected = False
        db.flush()

        # Re-register same cluster
        op2 = register_operator(db, token_record, cluster_name="cluster-2", operator_version="1.2.0")
        assert op2.is_connected is True
        assert op2.operator_version == "1.2.0"
        assert token_record.use_count == 2


# ── Disconnect ───────────────────────────────────────────────────────

class TestMarkDisconnected:
    def test_marks_disconnected(self, db):
        token_record, _ = create_registration_token(db, name="Disc", created_by="admin")
        op = register_operator(db, token_record, cluster_name="cluster-d")

        mark_operator_disconnected(db, op.operator_id, reason="test_reason")
        db.flush()

        refreshed = get_operator(db, op.operator_id)
        assert refreshed.is_connected is False
        assert refreshed.disconnect_reason == "test_reason"
        assert refreshed.status == OperatorStatus.DISCONNECTED


# ── Health & Heartbeat ───────────────────────────────────────────────

class TestHealthAndHeartbeat:
    def test_update_health(self, db):
        token_record, _ = create_registration_token(db, name="Health", created_by="admin")
        op = register_operator(db, token_record, cluster_name="cluster-h")

        update_operator_health(db, op.operator_id, {
            "cluster": {"kubernetes_version": "1.28", "node_count": 3, "nodes_ready": 3},
        })
        db.flush()

        refreshed = get_operator(db, op.operator_id)
        assert refreshed.kubernetes_version == "1.28"
        assert refreshed.node_count == 3

    def test_update_heartbeat(self, db):
        token_record, _ = create_registration_token(db, name="HB", created_by="admin")
        op = register_operator(db, token_record, cluster_name="cluster-hb")

        update_operator_heartbeat(db, op.operator_id, operator_version="1.3.0", uptime_seconds=3600)
        db.flush()

        refreshed = get_operator(db, op.operator_id)
        assert refreshed.operator_version == "1.3.0"
        assert refreshed.uptime_seconds == 3600


# ── Listing & Deletion ───────────────────────────────────────────────

class TestListAndDelete:
    def test_list_all_operators(self, db):
        token_record, _ = create_registration_token(db, name="List", created_by="admin")
        register_operator(db, token_record, cluster_name="a-cluster")
        register_operator(db, token_record, cluster_name="b-cluster")

        ops = list_operators(db)
        assert len(ops) >= 2

    def test_list_connected_only(self, db):
        token_record, _ = create_registration_token(db, name="ConnOnly", created_by="admin")
        op1 = register_operator(db, token_record, cluster_name="connected-c")
        op2 = register_operator(db, token_record, cluster_name="disconnected-c")
        mark_operator_disconnected(db, op2.operator_id)
        db.flush()

        ops = list_operators(db, connected_only=True)
        connected_names = [o.cluster_name for o in ops]
        assert "connected-c" in connected_names
        assert "disconnected-c" not in connected_names

    def test_delete_operator(self, db):
        token_record, _ = create_registration_token(db, name="Del", created_by="admin")
        op = register_operator(db, token_record, cluster_name="del-cluster")

        result = delete_operator(db, op.operator_id)
        assert result is True

        result2 = delete_operator(db, op.operator_id)
        assert result2 is False

    # Token list/revoke tests were removed in D3-CLEANUP — those service
    # functions are no longer exposed via HTTP API.
