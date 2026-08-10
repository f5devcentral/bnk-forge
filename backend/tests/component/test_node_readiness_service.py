"""Tests for services.node_readiness_service.

Validates:
  * Probe Job manifest shape (all-nodes targeting, privileged, hostPath
    mounts for /opt/cni/bin (ro) + /proc (ro), ttlSecondsAfterFinished).
  * Probe log parsing (CNI plugin presence, core_pattern extraction).
  * Per-node result shape, including the bare-"core" and missing-plugin
    failure cases.
  * End-to-end probe() against a mocked Batch/Core API (dispatch → wait →
    collect logs → cleanup).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.node_readiness_service import (
    NodeReadinessService,
    build_node_result,
    build_probe_job_manifest,
    parse_probe_log,
)

# ── build_probe_job_manifest ───────────────────────────────────────────────


class TestBuildProbeJobManifest:
    def _build(self, **overrides):
        defaults = dict(
            job_name="bnk-node-readiness-123",
            namespace="kube-system",
            node_count=3,
            image="busybox:1.36.1",
        )
        defaults.update(overrides)
        return build_probe_job_manifest(**defaults)

    def test_apiversion_and_kind(self):
        m = self._build()
        assert m["apiVersion"] == "batch/v1"
        assert m["kind"] == "Job"

    def test_targets_all_nodes_no_node_selector(self):
        m = self._build(node_count=4)
        spec = m["spec"]
        assert spec["completions"] == 4
        assert spec["parallelism"] == 4
        # No TMM label filter -- no nodeAffinity at all.
        assert "nodeAffinity" not in spec["template"]["spec"]["affinity"]

    def test_ttl_set(self):
        m = self._build()
        assert m["spec"]["ttlSecondsAfterFinished"] > 0

    def test_container_is_privileged(self):
        c = self._build()["spec"]["template"]["spec"]["containers"][0]
        assert c["securityContext"]["privileged"] is True

    def test_mounts_cni_bin_and_proc_readonly(self):
        spec = self._build()["spec"]["template"]["spec"]
        volumes = {v["name"]: v for v in spec["volumes"]}
        assert volumes["host-cni-bin"]["hostPath"]["path"] == "/opt/cni/bin"
        assert volumes["host-proc"]["hostPath"]["path"] == "/proc"

        mounts = {
            m["name"]: m
            for m in spec["containers"][0]["volumeMounts"]
        }
        assert mounts["host-cni-bin"]["readOnly"] is True
        assert mounts["host-proc"]["readOnly"] is True

    def test_pod_antiaffinity_forces_one_pod_per_node(self):
        spec = self._build()["spec"]["template"]["spec"]
        anti = spec["affinity"]["podAntiAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]
        assert anti[0]["topologyKey"] == "kubernetes.io/hostname"

    def test_tolerates_all_taints(self):
        spec = self._build()["spec"]["template"]["spec"]
        assert any(t.get("operator") == "Exists" for t in spec["tolerations"])

    def test_script_checks_all_three_plugins_and_core_pattern(self):
        c = self._build()["spec"]["template"]["spec"]["containers"][0]
        script = c["command"][2]
        assert "macvlan" in script
        assert "host-device" in script
        assert "ipvlan" in script
        assert "core_pattern" in script


# ── parse_probe_log ─────────────────────────────────────────────────────────


class TestParseProbeLog:
    def test_parses_full_output(self):
        log = (
            "NODE=bnkfull-control-plane\n"
            "CNI_PLUGINS= macvlan host-device ipvlan multus\n"
            "CORE_PATTERN=/tmp/core.%e.%p\n"
        )
        parsed = parse_probe_log(log)
        assert parsed["node"] == "bnkfull-control-plane"
        assert parsed["cni_plugins"] == ["macvlan", "host-device", "ipvlan", "multus"]
        assert parsed["core_pattern"] == "/tmp/core.%e.%p"

    def test_missing_plugin_and_bare_core(self):
        log = "NODE=worker-1\nCNI_PLUGINS= host-device ipvlan\nCORE_PATTERN=core\n"
        parsed = parse_probe_log(log)
        assert parsed["cni_plugins"] == ["host-device", "ipvlan"]
        assert parsed["core_pattern"] == "core"

    def test_empty_log(self):
        parsed = parse_probe_log("")
        assert parsed["node"] is None
        assert parsed["cni_plugins"] == []
        assert parsed["core_pattern"] is None


# ── build_node_result ────────────────────────────────────────────────────────


class TestBuildNodeResult:
    def test_all_ready_node(self):
        log = (
            "NODE=bnkfull-control-plane\n"
            "CNI_PLUGINS= macvlan host-device ipvlan\n"
            "CORE_PATTERN=/tmp/core.%e.%p\n"
        )
        result = build_node_result("bnkfull-control-plane", log, "2Gi")
        assert result["cni_plugins"] == {
            "macvlan": True, "host_device": True, "ipvlan": True,
        }
        assert result["cni_ok"] is True
        assert result["core_pattern"] == "/tmp/core.%e.%p"
        assert result["core_pattern_ok"] is True
        assert result["hugepages_2mi"] == "2Gi"
        assert result["hugepages_ok"] is True

    def test_missing_macvlan_and_bare_core_pattern(self):
        log = "NODE=worker-1\nCNI_PLUGINS= host-device ipvlan\nCORE_PATTERN=core\n"
        result = build_node_result("worker-1", log, "0")
        assert result["cni_plugins"]["macvlan"] is False
        assert result["cni_ok"] is False
        assert result["core_pattern"] == "core"
        assert result["core_pattern_ok"] is False
        assert result["hugepages_ok"] is False

    def test_no_log_reports_unknown_not_a_crash(self):
        result = build_node_result("worker-2", None, None)
        assert result["cni_ok"] is False
        assert result["core_pattern"] == "unknown"
        assert result["core_pattern_ok"] is False
        assert result["hugepages_ok"] is False

    def test_path_core_pattern_is_ok(self):
        log = "NODE=n\nCNI_PLUGINS=\nCORE_PATTERN=|/usr/bin/handler %P\n"
        result = build_node_result("n", log, None)
        assert result["core_pattern_ok"] is True


# ── NodeReadinessService.probe (mocked K8s APIs) ────────────────────────────


def _fake_node(name: str, provider_id: str | None, hugepages: str | None = "2Gi"):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(provider_id=provider_id),
        status=SimpleNamespace(capacity={"hugepages-2Mi": hugepages} if hugepages else {}),
    )


def _fake_pod(name: str, node_name: str):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        spec=SimpleNamespace(node_name=node_name),
    )


class TestNodeReadinessServiceProbe:
    def _service(self):
        svc = NodeReadinessService(db=MagicMock())
        svc.get_cluster = MagicMock(return_value=SimpleNamespace(name="c"))
        svc.load_kubeconfig = MagicMock(return_value=MagicMock())
        return svc

    def test_probe_end_to_end_detects_kind_and_readiness(self):
        svc = self._service()

        core_v1 = MagicMock()
        core_v1.list_node.return_value = SimpleNamespace(
            items=[
                _fake_node(
                    "bnkfull-control-plane",
                    "kind://docker/bnkfull/bnkfull-control-plane",
                    hugepages="2Gi",
                )
            ]
        )
        core_v1.list_namespaced_pod.return_value = SimpleNamespace(
            items=[_fake_pod("bnk-node-readiness-123-abcde", "bnkfull-control-plane")]
        )
        core_v1.read_namespaced_pod_log.return_value = (
            "NODE=bnkfull-control-plane\n"
            "CNI_PLUGINS= macvlan host-device ipvlan\n"
            "CORE_PATTERN=/tmp/core.%e.%p\n"
        )

        batch_v1 = MagicMock()
        batch_v1.create_namespaced_job.return_value = SimpleNamespace(
            metadata=SimpleNamespace(name="bnk-node-readiness-123")
        )
        batch_v1.read_namespaced_job_status.return_value = SimpleNamespace(
            status=SimpleNamespace(succeeded=1, failed=0),
            spec=SimpleNamespace(completions=1),
        )

        with patch(
            "services.node_readiness_service.client.CoreV1Api", return_value=core_v1
        ), patch(
            "services.node_readiness_service.client.BatchV1Api", return_value=batch_v1
        ), patch(
            "services.node_readiness_service.time.sleep"
        ):
            result = svc.probe(cluster_id=1)

        assert result["is_kind"] is True
        assert result["is_local"] is True
        assert result["all_ready"] is True
        assert len(result["nodes"]) == 1
        node_result = result["nodes"][0]
        assert node_result["node"] == "bnkfull-control-plane"
        assert node_result["cni_ok"] is True
        assert node_result["core_pattern_ok"] is True
        assert node_result["hugepages_ok"] is True
        batch_v1.delete_namespaced_job.assert_called_once()

    def test_probe_handles_missing_pod_log_gracefully(self):
        svc = self._service()

        core_v1 = MagicMock()
        core_v1.list_node.return_value = SimpleNamespace(
            items=[_fake_node("worker-1", "aws:///us-east-1a/i-1", hugepages=None)]
        )
        # No pods came back -- Job failed/timed out before any pod ran.
        core_v1.list_namespaced_pod.return_value = SimpleNamespace(items=[])

        batch_v1 = MagicMock()
        batch_v1.create_namespaced_job.return_value = SimpleNamespace(
            metadata=SimpleNamespace(name="bnk-node-readiness-123")
        )
        batch_v1.read_namespaced_job_status.return_value = SimpleNamespace(
            status=SimpleNamespace(succeeded=0, failed=1),
            spec=SimpleNamespace(completions=1),
        )

        with patch(
            "services.node_readiness_service.client.CoreV1Api", return_value=core_v1
        ), patch(
            "services.node_readiness_service.client.BatchV1Api", return_value=batch_v1
        ), patch(
            "services.node_readiness_service.time.sleep"
        ):
            result = svc.probe(cluster_id=1)

        assert result["is_kind"] is False
        assert result["all_ready"] is False
        assert result["nodes"][0]["core_pattern"] == "unknown"
