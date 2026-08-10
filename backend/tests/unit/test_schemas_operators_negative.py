"""
CT-034: Negative schema tests for Operator request schemas.

Tests that SendCommandRequest, LinkClusterRequest, SetConnectivityModeRequest,
FanOutCommandRequest, and FleetCompareRequest reject wrong payload shapes
with ValidationError.

Note: CreateTokenRequest and GenerateInstallCommandRequest tests were removed
in D3-CLEANUP (kubeconfig-first fleet architecture made token endpoints obsolete).
"""

import pytest
from pydantic import ValidationError

from routes.operators import (
    FanOutCommandRequest,
    FleetCompareRequest,
    LinkClusterRequest,
    SendCommandRequest,
    SetConnectivityModeRequest,
)


class TestSendCommandRequestNegative:
    def test_valid_command(self):
        req = SendCommandRequest(action="get_health")
        assert req.action == "get_health"
        assert req.payload is None
        assert req.timeout == 300.0

    def test_missing_action_rejected(self):
        with pytest.raises(ValidationError):
            SendCommandRequest()  # type: ignore[call-arg]

    def test_action_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            SendCommandRequest(action=123)  # type: ignore[arg-type]

    def test_with_payload_valid(self):
        req = SendCommandRequest(action="apply_manifests", payload={"yaml": "..."})
        assert req.payload["yaml"] == "..."

    def test_payload_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            SendCommandRequest(action="test", payload="not-a-dict")  # type: ignore[arg-type]

    def test_timeout_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            SendCommandRequest(action="test", timeout="slow")  # type: ignore[arg-type]


class TestLinkClusterRequestNegative:
    def test_valid_link(self):
        req = LinkClusterRequest(cluster_id=5)
        assert req.cluster_id == 5

    def test_missing_cluster_id_rejected(self):
        with pytest.raises(ValidationError):
            LinkClusterRequest()  # type: ignore[call-arg]

    def test_cluster_id_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            LinkClusterRequest(cluster_id="five")  # type: ignore[arg-type]


class TestSetConnectivityModeRequestNegative:
    def test_valid_mode(self):
        req = SetConnectivityModeRequest(mode="direct_ws")
        assert req.mode == "direct_ws"
        assert req.config is None

    def test_missing_mode_rejected(self):
        with pytest.raises(ValidationError):
            SetConnectivityModeRequest()  # type: ignore[call-arg]

    def test_mode_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            SetConnectivityModeRequest(mode=123)  # type: ignore[arg-type]

    def test_with_config_valid(self):
        req = SetConnectivityModeRequest(
            mode="reverse_ssh", config={"host": "bastion.example.com", "port": 22}
        )
        assert req.config["host"] == "bastion.example.com"

    def test_config_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            SetConnectivityModeRequest(mode="direct_ws", config="not-a-dict")  # type: ignore[arg-type]


class TestFanOutCommandRequestNegative:
    def test_valid_fanout(self):
        req = FanOutCommandRequest(action="get_health")
        assert req.action == "get_health"
        assert req.operator_ids is None
        assert req.labels is None

    def test_missing_action_rejected(self):
        with pytest.raises(ValidationError):
            FanOutCommandRequest()  # type: ignore[call-arg]

    def test_action_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            FanOutCommandRequest(action=123)  # type: ignore[arg-type]

    def test_with_filters_valid(self):
        req = FanOutCommandRequest(
            action="scan_cluster",
            operator_ids=["op-1", "op-2"],
            labels={"env": "prod"},
        )
        assert len(req.operator_ids) == 2

    def test_operator_ids_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            FanOutCommandRequest(action="test", operator_ids="not-a-list")  # type: ignore[arg-type]


class TestFleetCompareRequestNegative:
    def test_valid_compare(self):
        req = FleetCompareRequest(operator_a_id=1, operator_b_id=2)
        assert req.operator_a_id == 1

    def test_missing_operator_a_rejected(self):
        with pytest.raises(ValidationError):
            FleetCompareRequest(operator_b_id=2)  # type: ignore[call-arg]

    def test_missing_operator_b_rejected(self):
        with pytest.raises(ValidationError):
            FleetCompareRequest(operator_a_id=1)  # type: ignore[call-arg]

    def test_operator_id_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            FleetCompareRequest(operator_a_id="one", operator_b_id=2)  # type: ignore[arg-type]
