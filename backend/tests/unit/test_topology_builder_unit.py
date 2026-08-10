"""
Unit tests for topology_builder_service.assemble_topology.

Pure tests — no DB, no K8s cluster, no mocks.
All fixtures use SNAKE_CASE keys as returned by kubernetes-client .to_dict().

Critical invariants tested:
  1. Service → Pod selector-match produces 'selects' edges.
  2. Pod → ReplicaSet → Deployment collapse produces 'owns' edge from Deployment.
  3. Pod owned directly by StatefulSet produces 'owns' edge from StatefulSet.
  4. Pod severity derived from phase + Ready condition.
  5. Workload severity derived from replicas / ready_replicas.
  6. Truncation guard fires at >300 pods and sets info field.
  7. Service with no selector produces no edges.
"""
from services.topology_builder_service import assemble_topology

# ---------------------------------------------------------------------------
# Fixture helpers — snake_case (kubernetes-client to_dict() output)
# ---------------------------------------------------------------------------

def _pod(
    name: str,
    namespace: str = "default",
    uid: str = "",
    phase: str = "Running",
    ready: bool = True,
    labels: dict | None = None,
    owner_references: list | None = None,
) -> dict:
    cond_status = "True" if ready else "False"
    return {
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": uid or name,
            "labels": labels or {},
            "owner_references": owner_references or [],
        },
        "spec": {"containers": [{"name": "app"}]},
        "status": {
            "phase": phase,
            "conditions": [{"type": "Ready", "status": cond_status}],
        },
    }


def _service(
    name: str,
    namespace: str = "default",
    uid: str = "",
    selector: dict | None = None,
) -> dict:
    return {
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace, "uid": uid or name},
        "spec": {"selector": selector},
        "status": {},
    }


def _deployment(
    name: str,
    namespace: str = "default",
    uid: str = "",
    replicas: int = 1,
    ready_replicas: int = 1,
) -> dict:
    return {
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace, "uid": uid or name},
        "spec": {},
        "status": {"replicas": replicas, "ready_replicas": ready_replicas},
    }


def _replicaset(
    name: str,
    namespace: str = "default",
    uid: str = "",
    owner_deployment_uid: str = "",
) -> dict:
    owner_refs = []
    if owner_deployment_uid:
        owner_refs.append({
            "api_version": "apps/v1",
            "kind": "Deployment",
            "name": "dep",
            "uid": owner_deployment_uid,
            "controller": True,
            "block_owner_deletion": True,
        })
    return {
        "kind": "ReplicaSet",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "uid": uid or name,
            "owner_references": owner_refs,
        },
        "spec": {},
        "status": {},
    }


def _statefulset(
    name: str,
    namespace: str = "default",
    uid: str = "",
    replicas: int = 1,
    ready_replicas: int = 1,
) -> dict:
    return {
        "kind": "StatefulSet",
        "metadata": {"name": name, "namespace": namespace, "uid": uid or name},
        "spec": {},
        "status": {"replicas": replicas, "ready_replicas": ready_replicas},
    }


def _daemonset(
    name: str,
    namespace: str = "default",
    uid: str = "",
) -> dict:
    return {
        "kind": "DaemonSet",
        "metadata": {"name": name, "namespace": namespace, "uid": uid or name},
        "spec": {},
        "status": {"replicas": 1, "ready_replicas": 1},
    }


def _owner_ref(kind: str, uid: str, controller: bool = True) -> dict:
    return {
        "api_version": "apps/v1",
        "kind": kind,
        "name": "owner",
        "uid": uid,
        "controller": controller,
        "block_owner_deletion": True,
    }


def _call(**kwargs):
    """Call assemble_topology with defaults for missing lists."""
    defaults = dict(
        pods=[], services=[], deployments=[],
        replicasets=[], statefulsets=[], daemonsets=[],
        cluster_id=1, namespace="default",
    )
    defaults.update(kwargs)
    return assemble_topology(**defaults)


# ---------------------------------------------------------------------------
# Selector-match tests
# ---------------------------------------------------------------------------

class TestSelectorMatch:
    def test_service_selects_matching_pod(self):
        svc = _service("svc1", selector={"app": "web"})
        pod = _pod("pod1", labels={"app": "web"})
        result = _call(services=[svc], pods=[pod])

        selects_edges = [e for e in result.edges if e.kind == "selects"]
        assert len(selects_edges) == 1
        assert "Service/default/svc1" in selects_edges[0].source
        assert "Pod/default/pod1" in selects_edges[0].target

    def test_service_does_not_select_non_matching_pod(self):
        svc = _service("svc1", selector={"app": "web"})
        pod = _pod("pod1", labels={"app": "db"})
        result = _call(services=[svc], pods=[pod])

        selects_edges = [e for e in result.edges if e.kind == "selects"]
        assert len(selects_edges) == 0

    def test_service_with_no_selector_produces_no_edges(self):
        svc = _service("headless", selector=None)
        pod = _pod("pod1", labels={"app": "web"})
        result = _call(services=[svc], pods=[pod])

        selects_edges = [e for e in result.edges if e.kind == "selects"]
        assert len(selects_edges) == 0

    def test_service_with_empty_selector_produces_no_edges(self):
        svc = _service("svc1", selector={})
        pod = _pod("pod1", labels={"app": "web"})
        result = _call(services=[svc], pods=[pod])

        selects_edges = [e for e in result.edges if e.kind == "selects"]
        assert len(selects_edges) == 0

    def test_multi_label_selector_requires_all_match(self):
        svc = _service("svc1", selector={"app": "web", "env": "prod"})
        pod_full = _pod("pod-full", labels={"app": "web", "env": "prod"})
        pod_partial = _pod("pod-partial", labels={"app": "web"})
        result = _call(services=[svc], pods=[pod_full, pod_partial])

        selects_edges = [e for e in result.edges if e.kind == "selects"]
        assert len(selects_edges) == 1
        assert "pod-full" in selects_edges[0].target


# ---------------------------------------------------------------------------
# OwnerReference collapse tests
# ---------------------------------------------------------------------------

class TestOwnerReferenceCollapse:
    def test_pod_owned_by_replicaset_collapses_to_deployment(self):
        dep = _deployment("dep1", uid="dep-uid-1")
        rs = _replicaset("rs1", uid="rs-uid-1", owner_deployment_uid="dep-uid-1")
        pod = _pod(
            "pod1",
            owner_references=[_owner_ref("ReplicaSet", "rs-uid-1")],
        )

        result = _call(pods=[pod], deployments=[dep], replicasets=[rs])

        owns_edges = [e for e in result.edges if e.kind == "owns"]
        assert len(owns_edges) == 1
        assert "Deployment/default/dep1" in owns_edges[0].source
        assert "Pod/default/pod1" in owns_edges[0].target

    def test_pod_owned_directly_by_statefulset(self):
        sts = _statefulset("sts1", uid="sts-uid-1")
        pod = _pod(
            "pod1",
            owner_references=[_owner_ref("StatefulSet", "sts-uid-1")],
        )

        result = _call(pods=[pod], statefulsets=[sts])

        owns_edges = [e for e in result.edges if e.kind == "owns"]
        assert len(owns_edges) == 1
        assert "StatefulSet/default/sts1" in owns_edges[0].source
        assert "Pod/default/pod1" in owns_edges[0].target

    def test_pod_with_no_owner_produces_no_owns_edge(self):
        pod = _pod("orphan")
        result = _call(pods=[pod])

        owns_edges = [e for e in result.edges if e.kind == "owns"]
        assert len(owns_edges) == 0

    def test_pod_owned_directly_by_daemonset(self):
        ds = _daemonset("ds1", uid="ds-uid-1")
        pod = _pod(
            "pod1",
            owner_references=[_owner_ref("DaemonSet", "ds-uid-1")],
        )

        result = _call(pods=[pod], daemonsets=[ds])

        owns_edges = [e for e in result.edges if e.kind == "owns"]
        assert len(owns_edges) == 1
        assert "DaemonSet/default/ds1" in owns_edges[0].source
        assert "Pod/default/pod1" in owns_edges[0].target

    def test_standalone_replicaset_pod_produces_no_owns_edge(self):
        """A pod owned by an RS with no parent Deployment should produce no owns edge."""
        # RS exists but has no deployment parent
        rs = _replicaset("rs1", uid="rs-uid-1", owner_deployment_uid="")
        pod = _pod(
            "pod1",
            owner_references=[_owner_ref("ReplicaSet", "rs-uid-1")],
        )

        result = _call(pods=[pod], replicasets=[rs])

        owns_edges = [e for e in result.edges if e.kind == "owns"]
        assert len(owns_edges) == 0


# ---------------------------------------------------------------------------
# Pod severity tests
# ---------------------------------------------------------------------------

class TestPodSeverity:
    def test_running_ready_pod_is_healthy(self):
        pod = _pod("p1", phase="Running", ready=True)
        result = _call(pods=[pod])
        node = next(n for n in result.nodes if n.kind == "Pod")
        assert node.severity == "healthy"

    def test_running_not_ready_pod_is_degraded(self):
        pod = _pod("p1", phase="Running", ready=False)
        result = _call(pods=[pod])
        node = next(n for n in result.nodes if n.kind == "Pod")
        assert node.severity == "degraded"

    def test_pending_pod_is_degraded(self):
        pod = _pod("p1", phase="Pending")
        result = _call(pods=[pod])
        node = next(n for n in result.nodes if n.kind == "Pod")
        assert node.severity == "degraded"

    def test_failed_pod_is_unhealthy(self):
        pod = _pod("p1", phase="Failed")
        result = _call(pods=[pod])
        node = next(n for n in result.nodes if n.kind == "Pod")
        assert node.severity == "unhealthy"


# ---------------------------------------------------------------------------
# Workload severity tests
# ---------------------------------------------------------------------------

class TestWorkloadSeverity:
    def test_all_replicas_ready_is_healthy(self):
        dep = _deployment("dep1", replicas=3, ready_replicas=3)
        result = _call(deployments=[dep])
        node = next(n for n in result.nodes if n.kind == "Deployment")
        assert node.severity == "healthy"

    def test_partial_ready_is_degraded(self):
        dep = _deployment("dep1", replicas=3, ready_replicas=1)
        result = _call(deployments=[dep])
        node = next(n for n in result.nodes if n.kind == "Deployment")
        assert node.severity == "degraded"

    def test_no_ready_replicas_is_unhealthy(self):
        dep = _deployment("dep1", replicas=3, ready_replicas=0)
        result = _call(deployments=[dep])
        node = next(n for n in result.nodes if n.kind == "Deployment")
        assert node.severity == "unhealthy"

    def test_null_replicas_is_unknown(self):
        dep = _deployment("dep1", replicas=0, ready_replicas=0)
        result = _call(deployments=[dep])
        node = next(n for n in result.nodes if n.kind == "Deployment")
        assert node.severity == "unknown"


# ---------------------------------------------------------------------------
# Truncation guard
# ---------------------------------------------------------------------------

class TestTruncationGuard:
    def test_over_threshold_sets_info_and_truncates(self):
        pods = [_pod(f"pod-{i}") for i in range(305)]
        result = _call(pods=pods)
        assert result.info is not None
        assert "305" in result.info
        pod_nodes = [n for n in result.nodes if n.kind == "Pod"]
        assert len(pod_nodes) == 300

    def test_under_threshold_no_info(self):
        pods = [_pod(f"pod-{i}") for i in range(10)]
        result = _call(pods=pods)
        assert result.info is None


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------

class TestResponseShape:
    def test_cluster_id_and_namespace_in_response(self):
        result = _call(cluster_id=42, namespace="kube-system")
        assert result.cluster_id == 42
        assert result.namespace == "kube-system"

    def test_empty_namespace_returns_empty_graph(self):
        result = _call()
        assert result.nodes == []
        assert result.edges == []
        assert result.info is None

    def test_service_node_is_always_unknown_severity(self):
        svc = _service("svc1", selector={"app": "web"})
        result = _call(services=[svc])
        svc_node = next(n for n in result.nodes if n.kind == "Service")
        assert svc_node.severity == "unknown"
