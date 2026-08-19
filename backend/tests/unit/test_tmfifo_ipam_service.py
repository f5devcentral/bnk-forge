"""Unit tests for TmfifoPoolAllocator (ADR-424)."""

from unittest.mock import MagicMock

import pytest

from core.errors import ValidationError
from models.dpu import Dpu
from models.kubernetes import BnkClusterConfig
from services.tmfifo_ipam_service import TmfifoPoolAllocator


@pytest.fixture
def mock_db():
    return MagicMock()


def test_tmfifo_allocator_sequential(mock_db):
    # Setup mock cluster config
    cfg = BnkClusterConfig(cluster_id=1, tmfifo_pool_cidr="192.168.100.0/22")
    mock_db.query.return_value.filter.return_value.first.return_value = cfg
    mock_db.query.return_value.filter.return_value.all.return_value = []

    allocator = TmfifoPoolAllocator(mock_db)

    # First allocation
    alloc1 = allocator.allocate_next_subnet(cluster_id=1)
    assert alloc1.host_ip == "192.168.100.1"
    assert alloc1.dpu_ip == "192.168.100.2"
    assert alloc1.subnet_cidr == "192.168.100.0/30"


def test_tmfifo_allocator_skips_used(mock_db):
    cfg = BnkClusterConfig(cluster_id=1, tmfifo_pool_cidr="192.168.100.0/22")
    existing_dpu = Dpu(id=10, kubernetes_cluster_id=1, dpu_tmfifo_ip="192.168.100.2")

    # DB mocks
    mock_db.query.return_value.filter.return_value.first.return_value = cfg
    mock_db.query.return_value.filter.return_value.all.return_value = [existing_dpu]

    allocator = TmfifoPoolAllocator(mock_db)
    alloc = allocator.allocate_next_subnet(cluster_id=1)

    # Should skip 192.168.100.0/30 and pick 192.168.100.4/30
    assert alloc.host_ip == "192.168.100.5"
    assert alloc.dpu_ip == "192.168.100.6"
    assert alloc.subnet_cidr == "192.168.100.4/30"


def test_tmfifo_assign_dpu_idempotent(mock_db):
    dpu = Dpu(
        id=1,
        kubernetes_cluster_id=1,
        host_tmfifo_ip="192.168.100.1",
        dpu_tmfifo_ip="192.168.100.2",
    )

    allocator = TmfifoPoolAllocator(mock_db)
    alloc = allocator.assign_dpu_tmfifo(dpu, cluster_id=1)

    assert alloc.host_ip == "192.168.100.1"
    assert alloc.dpu_ip == "192.168.100.2"


def test_tmfifo_release(mock_db):
    dpu = Dpu(
        id=1,
        kubernetes_cluster_id=1,
        host_tmfifo_ip="192.168.100.1",
        dpu_tmfifo_ip="192.168.100.2",
    )

    allocator = TmfifoPoolAllocator(mock_db)
    allocator.release_dpu_tmfifo(dpu)

    assert dpu.kubernetes_cluster_id is None
    assert dpu.host_tmfifo_ip is None
    assert dpu.dpu_tmfifo_ip is None


def test_tmfifo_invalid_cidr(mock_db):
    cfg = BnkClusterConfig(cluster_id=1, tmfifo_pool_cidr="invalid-cidr")
    mock_db.query.return_value.filter.return_value.first.return_value = cfg

    allocator = TmfifoPoolAllocator(mock_db)
    with pytest.raises(ValidationError, match="Invalid tmfifo pool CIDR"):
        allocator.allocate_next_subnet(cluster_id=1)


def test_tmfifo_pool_too_small_raises_validation_error(mock_db):
    # A /31 (or any prefix >= 30) cannot be subdivided into /30s;
    # subnets(new_prefix=30) raises ValueError — must surface as ValidationError (4xx).
    cfg = BnkClusterConfig(cluster_id=1, tmfifo_pool_cidr="192.168.100.0/31")
    mock_db.query.return_value.filter.return_value.first.return_value = cfg
    mock_db.query.return_value.filter.return_value.all.return_value = []

    allocator = TmfifoPoolAllocator(mock_db)
    with pytest.raises(ValidationError, match="cannot be subdivided into /30s"):
        allocator.allocate_next_subnet(cluster_id=1)
