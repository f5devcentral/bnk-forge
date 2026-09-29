"""
Component tests for BenchmarkTargetService cluster-scoped uniqueness.

Verifies:
- (cluster_id, name) is unique
- Same target name on two different clusters is allowed
- Duplicate target name within the same cluster raises ConflictError (409)
- Updating a target to match another target on the same cluster raises ConflictError
- Updating a target to match a target on a different cluster succeeds
- list_targets filters by name and/or cluster_id
- target models include cluster_name
"""

import pytest

from core.errors import ConflictError, NotFoundError
from schemas.benchmarks import BenchmarkTargetCreate, BenchmarkTargetUpdate
from services.benchmark_target_service import BenchmarkTargetService


@pytest.fixture
def target_service(db):
    return BenchmarkTargetService(db)


def test_create_target_same_name_different_clusters_succeeds(
    target_service, make_k8s_cluster
):
    c1 = make_k8s_cluster(name="cluster-1")
    c2 = make_k8s_cluster(name="cluster-2")

    t1 = target_service.create_target(
        BenchmarkTargetCreate(
            name="mcp-shared-route",
            cluster_id=c1.id,
            llm_base_url="http://vllm.default:8000",
            llm_model="llama3",
        )
    )
    t2 = target_service.create_target(
        BenchmarkTargetCreate(
            name="mcp-shared-route",
            cluster_id=c2.id,
            llm_base_url="http://vllm.default:8000",
            llm_model="llama3",
        )
    )

    assert t1.id != t2.id
    assert t1.name == t2.name == "mcp-shared-route"
    assert t1.cluster_id == c1.id
    assert t2.cluster_id == c2.id
    assert t1.cluster_name == "cluster-1"
    assert t2.cluster_name == "cluster-2"


def test_create_target_same_name_same_cluster_raises_conflict(
    target_service, make_k8s_cluster
):
    c1 = make_k8s_cluster(name="cluster-dup")

    target_service.create_target(
        BenchmarkTargetCreate(
            name="mcp-route-dup",
            cluster_id=c1.id,
            llm_base_url="http://vllm:8000",
            llm_model="llama3",
        )
    )

    with pytest.raises(ConflictError) as exc_info:
        target_service.create_target(
            BenchmarkTargetCreate(
                name="mcp-route-dup",
                cluster_id=c1.id,
                llm_base_url="http://vllm:8000",
                llm_model="llama3",
            )
        )
    assert "already exists for cluster" in str(exc_info.value)


def test_update_target_conflict_handling(target_service, make_k8s_cluster):
    c1 = make_k8s_cluster(name="cluster-up-1")
    c2 = make_k8s_cluster(name="cluster-up-2")

    t1_a = target_service.create_target(
        BenchmarkTargetCreate(
            name="route-a",
            cluster_id=c1.id,
            llm_base_url="http://vllm:8000",
            llm_model="llama3",
        )
    )
    target_service.create_target(
        BenchmarkTargetCreate(
            name="route-b",
            cluster_id=c1.id,
            llm_base_url="http://vllm:8000",
            llm_model="llama3",
        )
    )
    t2_c = target_service.create_target(
        BenchmarkTargetCreate(
            name="route-c",
            cluster_id=c2.id,
            llm_base_url="http://vllm:8000",
            llm_model="llama3",
        )
    )

    # Renaming t1_a to 'route-b' (same cluster) must fail with ConflictError
    with pytest.raises(ConflictError):
        target_service.update_target(t1_a.id, BenchmarkTargetUpdate(name="route-b"))

    # Renaming t1_a to 'route-c' (name on cluster 2, but free on cluster 1) must succeed
    updated = target_service.update_target(
        t1_a.id, BenchmarkTargetUpdate(name="route-c")
    )
    assert updated.name == "route-c"
    assert updated.cluster_id == c1.id

    # t2_c on cluster 2 still exists with name 'route-c'
    t2_refresh = target_service.get_target(t2_c.id)
    assert t2_refresh.name == "route-c"
    assert t2_refresh.cluster_id == c2.id


def test_list_targets_filtering(target_service, make_k8s_cluster):
    c1 = make_k8s_cluster(name="cluster-filter-1")
    c2 = make_k8s_cluster(name="cluster-filter-2")

    target_service.create_target(
        BenchmarkTargetCreate(
            name="common-route",
            cluster_id=c1.id,
            llm_base_url="http://vllm:8000",
            llm_model="llama3",
        )
    )
    target_service.create_target(
        BenchmarkTargetCreate(
            name="common-route",
            cluster_id=c2.id,
            llm_base_url="http://vllm:8000",
            llm_model="llama3",
        )
    )
    target_service.create_target(
        BenchmarkTargetCreate(
            name="unique-c1",
            cluster_id=c1.id,
            llm_base_url="http://vllm:8000",
            llm_model="llama3",
        )
    )

    # Filter by name only
    common_targets, total_common = target_service.list_targets(name="common-route")
    assert total_common == 2
    assert len(common_targets) == 2
    assert {t.cluster_id for t in common_targets} == {c1.id, c2.id}

    # Filter by (cluster_id, name)
    c1_common, total_c1 = target_service.list_targets(
        cluster_id=c1.id, name="common-route"
    )
    assert total_c1 == 1
    assert len(c1_common) == 1
    assert c1_common[0].cluster_id == c1.id
    assert c1_common[0].name == "common-route"
    assert c1_common[0].cluster_name == "cluster-filter-1"

    # Filter by non-existent name
    none_targets, total_none = target_service.list_targets(name="does-not-exist")
    assert total_none == 0
    assert len(none_targets) == 0
