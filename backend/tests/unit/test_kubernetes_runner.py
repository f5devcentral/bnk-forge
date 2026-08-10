"""Unit tests for the KubernetesRunner — Job/Secret/NetworkPolicy spec builders
plus run_step orchestration (all k8s clients mocked, no live cluster).

These tests lock the security-critical properties of the K8s substrate:
  1. The step Job pins the image by digest and runs the artifact's own argv.
  2. ALL credentials land in a short-lived Secret (never inlined in the Job),
     and that Secret + the pull Secret carry ownerReferences → the Job (GC).
  3. A dockerconfigjson imagePullSecret is referenced by the pod.
  4. A deny-by-default NetworkPolicy is applied.
  5. Resource limits + activeDeadlineSeconds bound the Job.
  6. Pod logs are surfaced; secret values never appear in the Job/Pod spec.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest

from services.execution.container_runner import ResourceLimits, StepSpec
from services.execution.kubernetes_runner import (
    DENY_ALL_NETPOL_NAME,
    KubernetesRunner,
    RunnerKubeConfig,
)

DIGEST = "ghcr.io/jgruberf5/roksbnkctl-tools-runner@sha256:" + ("a" * 64)


def _runner() -> KubernetesRunner:
    return KubernetesRunner(RunnerKubeConfig(kubeconfig_path="/tmp/kc", namespace="bnk-run"))


def _spec(**overrides) -> StepSpec:
    base = dict(
        image_digest=DIGEST,
        args=["roksbnkctl", "apply"],
        workspace_host_path="/ignored/by/k8s",
        mount_path="/state",
        component_key="proj7-comp42",
        step_name="apply",
    )
    base.update(overrides)
    return StepSpec(**base)


@pytest.mark.unit
class TestBuildJob:
    def test_job_pins_image_by_digest_and_runs_artifact_argv(self):
        job = _runner().build_job(_spec())
        container = job.spec.template.spec.containers[0]
        assert container.image == DIGEST
        # The full argv runs via `command` (overrides the image ENTRYPOINT);
        # `args` is unset so the pod runs exactly this argv.
        assert container.command == ["roksbnkctl", "apply"]
        assert container.args is None

    def test_floating_tag_image_is_rejected(self):
        with pytest.raises(ValueError, match="digest-pinned"):
            _runner().build_job(_spec(image_digest="ghcr.io/x/y:1.11.4"))

    def test_empty_args_rejected(self):
        with pytest.raises(ValueError, match="non-empty argv"):
            _runner().build_job(_spec(args=[]))

    def test_missing_component_key_rejected(self):
        with pytest.raises(ValueError, match="component_key is required"):
            _runner().build_job(_spec(component_key=None))

    def test_job_has_active_deadline_seconds_and_no_retry(self):
        job = _runner().build_job(_spec(timeout_seconds=900))
        assert job.spec.active_deadline_seconds == 900
        assert job.spec.backoff_limit == 0

    def test_job_sets_resource_limits(self):
        job = _runner().build_job(_spec(limits=ResourceLimits(cpus="1.5", memory="512m")))
        limits = job.spec.template.spec.containers[0].resources.limits
        assert limits["cpu"] == "1.5"
        assert limits["memory"] == "512m"

    def test_credentials_are_not_inlined_in_job_spec(self):
        job = _runner().build_job(_spec(env={"IBMCLOUD_API_KEY": "supersecret"}))
        container = job.spec.template.spec.containers[0]
        # Secret values come via env_from (secretRef), NOT inline env values.
        assert container.env_from is not None
        assert container.env_from[0].secret_ref.name.startswith("bnk-creds-")
        rendered = str(job.to_dict() if hasattr(job, "to_dict") else job.__dict__)
        assert "supersecret" not in rendered

    def test_home_env_is_passed_as_plain_env(self):
        job = _runner().build_job(_spec(home_env={"HOME": "/state"}))
        env = job.spec.template.spec.containers[0].env
        assert any(e.name == "HOME" and e.value == "/state" for e in env)

    def test_job_references_pull_secret_when_authfile_present(self):
        job = _runner().build_job(_spec(pull_authfile_json='{"auths":{}}'))
        pull = job.spec.template.spec.image_pull_secrets
        assert pull is not None
        assert pull[0].name.startswith("bnk-pull-")

    def test_job_mounts_per_component_workspace_pvc(self):
        job = _runner().build_job(_spec())
        vol = job.spec.template.spec.volumes[0]
        assert vol.persistent_volume_claim.claim_name == "bnk-ws-proj7-comp42"
        mount = job.spec.template.spec.containers[0].volume_mounts[0]
        assert mount.mount_path == "/state"

    def test_job_drops_privileges(self):
        sc = _runner().build_job(_spec()).spec.template.spec.containers[0].security_context
        assert sc.allow_privilege_escalation is False
        assert sc.run_as_non_root is True
        assert sc.capabilities.drop == ["ALL"]


@pytest.mark.unit
class TestBuildSecrets:
    def test_credentials_secret_carries_owner_reference_to_job(self):
        runner = _runner()
        job = MagicMock()
        job.metadata.name = "bnk-job-1"
        job.metadata.uid = "uid-123"
        owner = runner.owner_reference_for_job(job)
        secret = runner.build_credentials_secret(_spec(env={"AWS_ACCESS_KEY_ID": "x"}), owner)
        assert secret is not None
        assert secret.type == "Opaque"
        assert secret.string_data == {"AWS_ACCESS_KEY_ID": "x"}
        ref = secret.metadata.owner_references[0]
        assert ref.kind == "Job"
        assert ref.uid == "uid-123"
        assert ref.controller is True

    def test_credentials_secret_is_none_without_env(self):
        assert _runner().build_credentials_secret(_spec(), None) is None

    def test_credentials_secret_rejects_bad_env_key(self):
        with pytest.raises(ValueError, match="environment variable name"):
            _runner().build_credentials_secret(_spec(env={"bad-key": "v"}), None)

    def test_pull_secret_is_dockerconfigjson_with_owner_ref(self):
        runner = _runner()
        job = MagicMock()
        job.metadata.name = "bnk-job-1"
        job.metadata.uid = "uid-9"
        owner = runner.owner_reference_for_job(job)
        # pull_authfile_json is a base64-encoded dockerconfigjson; it must land in
        # `data` verbatim (not string_data, which Kubernetes would re-encode).
        authfile_b64 = base64.b64encode(b'{"auths":{}}').decode()
        secret = runner.build_pull_secret(_spec(pull_authfile_json=authfile_b64), owner)
        assert secret is not None
        assert secret.type == "kubernetes.io/dockerconfigjson"
        assert secret.string_data is None
        assert secret.data[".dockerconfigjson"] == authfile_b64
        assert secret.metadata.owner_references[0].uid == "uid-9"

    def test_pull_secret_is_none_without_authfile(self):
        assert _runner().build_pull_secret(_spec(), None) is None


@pytest.mark.unit
class TestBuildNetworkPolicyAndPvc:
    def test_network_policy_is_deny_by_default(self):
        netpol = _runner().build_network_policy()
        assert netpol.metadata.name == DENY_ALL_NETPOL_NAME
        assert sorted(netpol.spec.policy_types) == ["Egress", "Ingress"]
        # Empty pod selector → selects all pods; no rules → deny all.
        assert netpol.spec.ingress == []
        assert netpol.spec.egress == []
        assert netpol.spec.pod_selector.match_labels in (None, {})

    def test_workspace_pvc_named_per_component(self):
        pvc = _runner().build_workspace_pvc(_spec())
        assert pvc.metadata.name == "bnk-ws-proj7-comp42"
        assert pvc.spec.access_modes == ["ReadWriteOnce"]
        # The workspace PVC is NOT owner-bound — it must outlive each step Job.
        assert pvc.metadata.owner_references is None


@pytest.mark.unit
class TestRunStep:
    def _patch_clients(self, batch_v1, core_v1, networking):
        return patch.multiple(
            "services.execution.kubernetes_runner.k8s_client",
            BatchV1Api=MagicMock(return_value=batch_v1),
            CoreV1Api=MagicMock(return_value=core_v1),
            NetworkingV1Api=MagicMock(return_value=networking),
        )

    def test_run_step_success_creates_job_secrets_netpol_and_returns_logs(self):
        runner = _runner()
        runner._api_client = MagicMock()  # bypass kubeconfig loading

        created_job = MagicMock()
        created_job.metadata.name = "bnk-proj7-comp42-apply-1"
        created_job.metadata.uid = "uid-xyz"

        batch_v1 = MagicMock()
        batch_v1.create_namespaced_job.return_value = created_job
        # status: succeeded on first poll
        status = MagicMock(succeeded=1, failed=None)
        batch_v1.read_namespaced_job_status.return_value = MagicMock(status=status)

        core_v1 = MagicMock()
        pod = MagicMock()
        pod.metadata.name = "bnk-proj7-comp42-apply-1-abcde"
        core_v1.list_namespaced_pod.return_value = MagicMock(items=[pod])
        core_v1.read_namespaced_pod_log.return_value = "apply complete"

        networking = MagicMock()

        with self._patch_clients(batch_v1, core_v1, networking):
            result = runner.run_step(_spec(env={"IBMCLOUD_API_KEY": "k"}, pull_authfile_json='{"auths":{}}'))

        assert result.success is True
        assert result.exit_code == 0
        assert "apply complete" in result.stdout
        # NetworkPolicy ensured.
        networking.create_namespaced_network_policy.assert_called_once()
        # PVC ensured + Job created.
        core_v1.create_namespaced_persistent_volume_claim.assert_called_once()
        batch_v1.create_namespaced_job.assert_called_once()
        # Two secrets created (creds + pull), both with owner ref to the job.
        assert core_v1.create_namespaced_secret.call_count == 2
        for call in core_v1.create_namespaced_secret.call_args_list:
            secret = call.args[1]
            assert secret.metadata.owner_references[0].uid == "uid-xyz"

    def test_run_step_failed_job_returns_failure(self):
        runner = _runner()
        runner._api_client = MagicMock()

        created_job = MagicMock()
        created_job.metadata.name = "bnk-job"
        created_job.metadata.uid = "u"

        batch_v1 = MagicMock()
        batch_v1.create_namespaced_job.return_value = created_job
        status = MagicMock(succeeded=None, failed=1, conditions=[])
        batch_v1.read_namespaced_job_status.return_value = MagicMock(status=status)

        core_v1 = MagicMock()
        core_v1.list_namespaced_pod.return_value = MagicMock(items=[])

        with self._patch_clients(batch_v1, core_v1, MagicMock()):
            result = runner.run_step(_spec())

        assert result.success is False
        assert result.exit_code == 1
        assert result.timed_out is False

    def test_run_step_deadline_exceeded_marks_timed_out(self):
        runner = _runner()
        runner._api_client = MagicMock()

        created_job = MagicMock()
        created_job.metadata.name = "bnk-job"
        created_job.metadata.uid = "u"

        cond = MagicMock(type="Failed", reason="DeadlineExceeded")
        batch_v1 = MagicMock()
        batch_v1.create_namespaced_job.return_value = created_job
        status = MagicMock(succeeded=None, failed=1, conditions=[cond])
        batch_v1.read_namespaced_job_status.return_value = MagicMock(status=status)

        core_v1 = MagicMock()
        core_v1.list_namespaced_pod.return_value = MagicMock(items=[])

        with self._patch_clients(batch_v1, core_v1, MagicMock()):
            result = runner.run_step(_spec())

        assert result.success is False
        assert result.timed_out is True
        assert result.exit_code == 124

    def test_run_step_no_credentials_creates_no_secret(self):
        runner = _runner()
        runner._api_client = MagicMock()

        created_job = MagicMock()
        created_job.metadata.name = "bnk-job"
        created_job.metadata.uid = "u"

        batch_v1 = MagicMock()
        batch_v1.create_namespaced_job.return_value = created_job
        batch_v1.read_namespaced_job_status.return_value = MagicMock(
            status=MagicMock(succeeded=1, failed=None)
        )

        core_v1 = MagicMock()
        core_v1.list_namespaced_pod.return_value = MagicMock(items=[])

        with self._patch_clients(batch_v1, core_v1, MagicMock()):
            runner.run_step(_spec())

        core_v1.create_namespaced_secret.assert_not_called()
