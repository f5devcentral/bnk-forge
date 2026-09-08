"""Unit tests for BnkClusterService (ADR-424)."""

from unittest.mock import MagicMock

import pytest

from core.errors import NotFoundError
from models.bare_metal import BareMetalHost
from models.dpu import Dpu
from models.kubernetes import BnkClusterConfig, KubernetesCluster
from services.bnk_cluster_service import BnkClusterService


@pytest.fixture
def mock_db():
    return MagicMock()


def test_get_or_create_config_new(mock_db):
    cluster = KubernetesCluster(id=1, name="cluster-1")

    # DB mocks
    def query_side_effect(model):
        m = MagicMock()
        if model == KubernetesCluster:
            m.get.return_value = cluster
        elif model == BnkClusterConfig:
            m.filter.return_value.first.return_value = None
        return m

    mock_db.query.side_effect = query_side_effect

    service = BnkClusterService(mock_db)
    cfg = service.get_or_create_config(cluster_id=1, tmfifo_pool_cidr="192.168.100.0/22")

    assert cfg.cluster_id == 1
    assert cfg.tmfifo_pool_cidr == "192.168.100.0/22"
    assert cfg.join_transport == "rshim"


def test_assign_members_success(mock_db):
    cluster = KubernetesCluster(id=1, name="cluster-1")
    cp_host = BareMetalHost(id=101, hostname="host-cp")
    worker_host = BareMetalHost(id=102, hostname="host-worker")
    dpu1 = Dpu(id=201, name="dpu-1")

    def query_side_effect(model):
        m = MagicMock()
        if model == KubernetesCluster:
            m.get.return_value = cluster
        elif model == BnkClusterConfig:
            m.filter.return_value.first.return_value = None
        elif model == BareMetalHost:
            m.get.side_effect = lambda hid: cp_host if hid == 101 else worker_host
            m.filter.return_value.all.return_value = [cp_host, worker_host]
            m.filter.return_value.with_for_update.return_value.all.return_value = [cp_host, worker_host]
        elif model == Dpu:
            m.filter.return_value.all.return_value = [dpu1]
            m.filter.return_value.with_for_update.return_value.all.return_value = [dpu1]
        return m

    mock_db.query.side_effect = query_side_effect

    service = BnkClusterService(mock_db)
    res = service.assign_members(
        cluster_id=1,
        control_plane_host_id=101,
        host_ids=[101, 102],
        dpu_ids=[201],
        tmfifo_pool_cidr="192.168.100.0/22",
    )

    assert res["cluster_id"] == 1
    assert res["control_plane_host_id"] == 101
    assert cp_host.is_control_plane is True
    assert cp_host.kubernetes_cluster_id == 1
    assert worker_host.is_control_plane is False
    assert worker_host.kubernetes_cluster_id == 1
    assert len(res["assigned_dpus"]) == 1
    assert res["assigned_dpus"][0]["dpu_id"] == 201


def test_assign_members_missing_cp_host(mock_db):
    cluster = KubernetesCluster(id=1, name="cluster-1")

    def query_side_effect(model):
        m = MagicMock()
        if model == KubernetesCluster:
            m.get.return_value = cluster
        elif model == BareMetalHost:
            m.get.return_value = None
        return m

    mock_db.query.side_effect = query_side_effect

    service = BnkClusterService(mock_db)
    with pytest.raises(NotFoundError):
        service.assign_members(
            cluster_id=1,
            control_plane_host_id=999,
            host_ids=[999],
            dpu_ids=[],
        )
