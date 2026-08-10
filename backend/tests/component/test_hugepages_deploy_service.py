"""Tests for services.hugepages_deploy_service.

Validates:
  * Job manifest shape (completions/parallelism match node count, affinity,
    tolerations, hostPath volumes, privileged container, correct sysctl write).
  * Node enumeration picks up both TMM label schemes and de-dupes.
  * Request-level size → page count wiring is correct.
  * Error path when no TMM nodes are found.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError
from services.hugepages_deploy_service import (
    HugePagesDeployService,
    build_job_manifest,
)

# ── build_job_manifest ─────────────────────────────────────────────────────


class TestBuildJobManifest:
    def _build(self, **overrides):
        defaults = dict(
            job_name="bnk-hugepages-small-123",
            namespace="kube-system",
            node_count=3,
            page_count=1536,
            image="busybox:1.36.1",
        )
        defaults.update(overrides)
        return build_job_manifest(**defaults)

    def test_apiversion_and_kind(self):
        m = self._build()
        assert m["apiVersion"] == "batch/v1"
        assert m["kind"] == "Job"

    def test_parallelism_matches_node_count(self):
        m = self._build(node_count=5)
        assert m["spec"]["completions"] == 5
        assert m["spec"]["parallelism"] == 5

    def test_ttl_and_deadline_bound_runtime(self):
        m = self._build()
        assert m["spec"]["ttlSecondsAfterFinished"] > 0
        assert m["spec"]["activeDeadlineSeconds"] > 0

    def test_container_is_privileged_for_sysctl_write(self):
        c = self._build()["spec"]["template"]["spec"]["containers"][0]
        assert c["securityContext"]["privileged"] is True

    def test_mounts_proc_and_sysctl_d_hostpaths(self):
        spec = self._build()["spec"]["template"]["spec"]
        volumes = {v["name"]: v for v in spec["volumes"]}
        assert volumes["host-proc"]["hostPath"]["path"] == "/proc"
        assert volumes["host-etc-sysctl"]["hostPath"]["path"] == "/etc/sysctl.d"

    def test_script_writes_runtime_and_persistent_hugepages(self):
        c = self._build(page_count=1536)["spec"]["template"]["spec"]["containers"][0]
        script = c["command"][2]
        # Runtime write
        assert "1536 > /host-proc/sys/vm/nr_hugepages" in script
        # Persistent sysctl.d write
        assert "vm.nr_hugepages=1536" in script
        assert "90-f5-bnk-hugepages.conf" in script

    def test_node_affinity_targets_both_tmm_label_schemes(self):
        spec = self._build()["spec"]["template"]["spec"]
        terms = spec["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"]
        # Two terms (OR semantics at term level) -- app=f5-tmm and node-role label.
        assert len(terms) == 2
        flat_keys = [
            expr["key"]
            for term in terms
            for expr in term["matchExpressions"]
        ]
        assert "app" in flat_keys
        assert "node-role.kubernetes.io/f5-tmm" in flat_keys

    def test_pod_antiaffinity_forces_one_pod_per_node(self):
        spec = self._build()["spec"]["template"]["spec"]
        anti = spec["affinity"]["podAntiAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]
        assert anti[0]["topologyKey"] == "kubernetes.io/hostname"

    def test_tolerates_all_taints(self):
        spec = self._build()["spec"]["template"]["spec"]
        # TMM nodes are commonly tainted; the Job has to be able to land there.
        assert any(t.get("operator") == "Exists" for t in spec["tolerations"])

    def test_image_is_configurable(self):
        m = self._build(image="mirror.internal/busybox:1.36.1")
        c = m["spec"]["template"]["spec"]["containers"][0]
        assert c["image"] == "mirror.internal/busybox:1.36.1"


# ── HugePagesDeployService ─────────────────────────────────────────────────


def _fake_node(name: str, labels: dict[str, str]):
    meta = SimpleNamespace(name=name, labels=labels)
    return SimpleNamespace(metadata=meta)


class TestListTmmNodes:
    def test_deduplicates_when_node_has_both_label_schemes(self):
        # Same node name returned by both label queries -- must not count twice.
        hybrid = _fake_node(
            "tmm-1",
            {"app": "f5-tmm", "node-role.kubernetes.io/f5-tmm": ""},
        )
        list_resp = SimpleNamespace(items=[hybrid])

        core_v1 = MagicMock()
        core_v1.list_node.return_value = list_resp

        with patch(
            "services.hugepages_deploy_service.client.CoreV1Api",
            return_value=core_v1,
        ):
            nodes = HugePagesDeployService._list_tmm_nodes(MagicMock())

        assert [n["name"] for n in nodes] == ["tmm-1"]
        # Two label selectors tried, even though both returned the same node.
        assert core_v1.list_node.call_count == 2

    def test_merges_nodes_from_both_label_schemes(self):
        app_labelled = _fake_node("tmm-a", {"app": "f5-tmm"})
        role_labelled = _fake_node("tmm-b", {"node-role.kubernetes.io/f5-tmm": ""})

        core_v1 = MagicMock()
        core_v1.list_node.side_effect = [
            SimpleNamespace(items=[app_labelled]),
            SimpleNamespace(items=[role_labelled]),
        ]

        with patch(
            "services.hugepages_deploy_service.client.CoreV1Api",
            return_value=core_v1,
        ):
            nodes = HugePagesDeployService._list_tmm_nodes(MagicMock())

        assert sorted(n["name"] for n in nodes) == ["tmm-a", "tmm-b"]


class TestDeploy:
    def _service(self):
        svc = HugePagesDeployService(db=MagicMock())
        svc.get_cluster = MagicMock(return_value=SimpleNamespace(name="c"))
        svc.load_kubeconfig = MagicMock(return_value=MagicMock())
        return svc

    def test_raises_bad_request_when_no_tmm_nodes(self):
        svc = self._service()
        with patch.object(
            HugePagesDeployService, "_list_tmm_nodes", return_value=[]
        ):
            with pytest.raises(BadRequestError, match="No TMM nodes"):
                svc.deploy(cluster_id=1, size="small")

    def test_dispatches_job_with_correct_page_count_for_size(self):
        svc = self._service()

        captured_body: dict = {}

        def fake_create(namespace, body):
            captured_body.update(body)
            return SimpleNamespace(metadata=SimpleNamespace(name=body["metadata"]["name"]))

        batch_v1 = MagicMock()
        batch_v1.create_namespaced_job.side_effect = fake_create

        with patch.object(
            HugePagesDeployService,
            "_list_tmm_nodes",
            return_value=[{"name": "tmm-1", "labels": {}}, {"name": "tmm-2", "labels": {}}],
        ), patch(
            "services.hugepages_deploy_service.client.BatchV1Api",
            return_value=batch_v1,
        ):
            result = svc.deploy(cluster_id=1, size="medium")

        assert result["success"] is True
        assert result["page_count"] == 3072  # medium
        assert result["target_node_count"] == 2
        assert result["memory_gib_per_node"] == 6.0
        assert captured_body["spec"]["completions"] == 2
        assert captured_body["spec"]["parallelism"] == 2
        # Confirm the script writes the size-derived count.
        script = captured_body["spec"]["template"]["spec"]["containers"][0]["command"][2]
        assert "3072 > /host-proc/sys/vm/nr_hugepages" in script

    def test_request_image_override_beats_env_default(self):
        svc = self._service()
        batch_v1 = MagicMock()
        batch_v1.create_namespaced_job.return_value = SimpleNamespace(
            metadata=SimpleNamespace(name="j")
        )

        with patch.object(
            HugePagesDeployService,
            "_list_tmm_nodes",
            return_value=[{"name": "tmm-1", "labels": {}}],
        ), patch(
            "services.hugepages_deploy_service.client.BatchV1Api",
            return_value=batch_v1,
        ):
            result = svc.deploy(
                cluster_id=1, size="small", image="mirror.internal/busybox:1.36.1"
            )

        assert result["image"] == "mirror.internal/busybox:1.36.1"
