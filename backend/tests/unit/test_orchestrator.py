"""Unit tests for BareMetalDeploymentService — mocked DB."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models.bare_metal import BareMetalDeployment, BareMetalHost, DeploymentStep
from models.enums import BareMetalDeploymentStatus, DeploymentStepStatus
from services.bare_metal.orchestrator import BareMetalDeploymentService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_host(**kwargs):
    host = MagicMock(spec=BareMetalHost)
    host.id = kwargs.get("id", 1)
    host.project_id = kwargs.get("project_id", 1)
    host.topology = kwargs.get("topology", "regular")
    host.version_profile = kwargs.get("version_profile", None)
    host.bmc_ip = kwargs.get("bmc_ip", None)
    host.deploy_dpu_pci_address = kwargs.get("deploy_dpu_pci_address", None)
    return host


def _make_mock_release(**kwargs):
    """A minimal mock BnkDeployableRelease for deployment creation tests."""
    release = MagicMock()
    release.id = kwargs.get("id", 1)
    release.name = kwargs.get("name", "bnk-2.2")
    release.display_name = kwargs.get("display_name", "BNK 2.2 (GA)")
    release.is_active = kwargs.get("is_active", True)
    release.is_default = kwargs.get("is_default", True)
    release.source_type = "manual"
    release.bnk_release_id = None
    release.bnk_manifest_version = "2.2.1-3.2226.0-0.0.511"
    release.bnk_cr_kind = "CNEInstance"
    release.flo_version = "v2.9.27-0.3.4"
    release.k8s_version = "1.30.4"
    release.doca_version = "2.9.1"
    release.containerd_version = "1.7.20"
    release.runc_version = "1.1.13"
    release.calico_version = "3.28.1"
    release.cert_manager_version = "v1.15.3"
    release.gateway_api_version = "1.1.0"
    release.multus_version = "4.1.0"
    release.sriov_version = "1.4.0"
    release.storage_class_type = "local-path"
    release.storage_provisioner = "rancher.io/local-path"
    release.feature_flags = {}
    release.full_manifest = None
    return release


def _make_mock_deployment(**kwargs):
    d = MagicMock(spec=BareMetalDeployment)
    d.id = kwargs.get("id", 1)
    d.host_id = kwargs.get("host_id", 1)
    d.project_id = kwargs.get("project_id", 1)
    d.topology = kwargs.get("topology", "regular")
    d.status = kwargs.get("status", BareMetalDeploymentStatus.PENDING)
    d.celery_task_id = kwargs.get("celery_task_id", None)
    d.resume_from_step = kwargs.get("resume_from_step", None)
    d.started_at = kwargs.get("started_at", None)
    d.completed_at = kwargs.get("completed_at", None)
    d.error_message = None
    d.error_phase = None
    d.error_step_index = None
    d.steps = kwargs.get("steps", [])
    return d


def _make_mock_step(
    index: int = 0,
    name: str = "test_step",
    phase: str = "phase_1_dpu",
    status: DeploymentStepStatus = DeploymentStepStatus.PENDING,
) -> MagicMock:
    s = MagicMock(spec=DeploymentStep)
    s.step_index = index
    s.name = name
    s.phase = phase
    s.status = status
    s.connectivity_risk = False
    s.connectivity_mechanism = None
    s.started_at = None
    s.completed_at = None
    s.duration_seconds = None
    s.error_message = None
    s.output_log = None
    s.exit_code = None
    s.already_done = False
    return s


@pytest.fixture
def mock_db():
    return MagicMock()


# ---------------------------------------------------------------------------
# TestCreateDeployment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateDeployment:
    def test_create_deployment_regular(self, mock_db):
        host = _make_mock_host(topology="regular")
        mock_db.query.return_value.filter.return_value.first.side_effect = [host, None, _make_mock_release()]
        mock_db.add = MagicMock()
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.create_deployment(1, 1)

        # Should have called db.add for deployment + 19 steps = 20 calls
        assert mock_db.add.call_count == 20

    def test_create_deployment_no_host(self, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="not found"):
            svc.create_deployment(999, 1)

    def test_create_deployment_no_topology(self, mock_db):
        host = _make_mock_host(topology=None)
        mock_db.query.return_value.filter.return_value.first.return_value = host

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="topology"):
            svc.create_deployment(1, 1)

    def test_create_deployment_bf3(self, mock_db):
        host = _make_mock_host(topology="bf3")
        mock_db.query.return_value.filter.return_value.first.side_effect = [host, None, _make_mock_release()]
        mock_db.add = MagicMock()
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.create_deployment(1, 1)

        # bf3 has 21 steps + 1 deployment = 22
        assert mock_db.add.call_count == 22

    def test_create_deployment_bf3_ipmi(self, mock_db):
        host = _make_mock_host(topology="bf3_ipmi")
        mock_db.query.return_value.filter.return_value.first.side_effect = [host, None, _make_mock_release()]
        mock_db.add = MagicMock()
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.create_deployment(1, 1)

        # bf3_ipmi has 23 steps + 1 deployment = 24
        assert mock_db.add.call_count == 24

    def test_create_deployment_bmc(self, mock_db):
        host = _make_mock_host(topology="bmc")
        mock_db.query.return_value.filter.return_value.first.side_effect = [host, None, _make_mock_release()]
        mock_db.add = MagicMock()
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.create_deployment(1, 1)

        # bmc has 23 steps + 1 deployment = 24
        assert mock_db.add.call_count == 24

    def test_create_deployment_dual_dpu_obmc(self, mock_db):
        host = _make_mock_host(
            topology="dual_dpu_obmc",
            bmc_ip="10.0.0.50",
            deploy_dpu_pci_address="0002:03:00.0",
        )
        mock_db.query.return_value.filter.return_value.first.side_effect = [host, None, _make_mock_release()]
        mock_db.add = MagicMock()
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.create_deployment(1, 1)

        # dual_dpu_obmc has 20 steps + 1 deployment = 21
        assert mock_db.add.call_count == 21

    def test_create_deployment_dual_dpu_obmc_requires_bmc_ip(self, mock_db):
        host = _make_mock_host(
            topology="dual_dpu_obmc",
            bmc_ip=None,
            deploy_dpu_pci_address="0002:03:00.0",
        )
        mock_db.query.return_value.filter.return_value.first.return_value = host

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="bmc_ip"):
            svc.create_deployment(1, 1)

    def test_create_deployment_dual_dpu_obmc_requires_deploy_dpu_pci_address(self, mock_db):
        host = _make_mock_host(
            topology="dual_dpu_obmc",
            bmc_ip="10.0.0.50",
            deploy_dpu_pci_address=None,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = host

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="deploy_dpu_pci_address"):
            svc.create_deployment(1, 1)

    def test_create_deployment_snapshots_deployable_release(self, mock_db):
        """Deployable release snapshot is captured and FK stamped on create."""
        release = _make_mock_release(id=7, name="bnk-2.3.1")
        host = _make_mock_host(topology="regular")
        mock_db.query.return_value.filter.return_value.first.side_effect = [host, None, release]
        mock_db.add = MagicMock()
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.create_deployment(1, 1)
        assert mock_db.add.called
        # host.version_profile_id should be set to release.id
        assert host.version_profile_id == 7

    def test_create_deployment_with_triggered_by(self, mock_db):
        host = _make_mock_host(topology="regular")
        mock_db.query.return_value.filter.return_value.first.side_effect = [host, None, _make_mock_release()]
        mock_db.add = MagicMock()
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.create_deployment(1, 1, triggered_by="admin")
        assert mock_db.add.called

    def test_create_deployment_no_default_release_raises(self, mock_db):
        """No default BnkDeployableRelease → BadRequestError."""
        host = _make_mock_host(topology="regular")
        # 3rd query (default release) returns None → should raise
        mock_db.query.return_value.filter.return_value.first.side_effect = [host, None, None]

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="no default BNK release"):
            svc.create_deployment(1, 1)

    def test_create_deployment_inactive_release_raises(self, mock_db):
        """Explicit inactive BnkDeployableRelease → BadRequestError."""
        host = _make_mock_host(topology="regular")
        inactive = _make_mock_release(is_active=False, name="bnk-2.1")
        # 3rd query (explicit release) returns inactive release
        mock_db.query.return_value.filter.return_value.first.side_effect = [host, None, inactive]

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="not active"):
            svc.create_deployment(1, 1, deployable_release_id=99)


# ---------------------------------------------------------------------------
# TestGetDeployment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDeployment:
    def test_get_existing_deployment(self, mock_db):
        deployment = _make_mock_deployment(id=42)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment

        svc = BareMetalDeploymentService(mock_db)
        result = svc.get_deployment(42)

        assert result.id == 42

    def test_get_missing_deployment_raises(self, mock_db):
        mock_db.query.return_value.filter.return_value.first.return_value = None

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="not found"):
            svc.get_deployment(999)


# ---------------------------------------------------------------------------
# TestExecuteDeployment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteDeployment:
    def test_execute_all_steps_success(self, mock_db):
        steps = [_make_mock_step(i, f"step_{i}") for i in range(3)]
        deployment = _make_mock_deployment(steps=steps)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.commit = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        result = svc.execute_deployment(1)

        assert result["status"] == "completed"
        assert result["completed"] == 3
        assert result["skipped"] == 0
        assert deployment.status == BareMetalDeploymentStatus.COMPLETED

    def test_execute_sets_in_progress(self, mock_db):
        """Deployment transitions to IN_PROGRESS immediately on execute."""
        steps = [_make_mock_step(0)]
        deployment = _make_mock_deployment(steps=steps)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.commit = MagicMock()

        status_seen = []
        original_commit = mock_db.commit

        def track_commit():
            status_seen.append(deployment.status)

        mock_db.commit.side_effect = track_commit

        svc = BareMetalDeploymentService(mock_db)
        svc.execute_deployment(1)

        assert BareMetalDeploymentStatus.IN_PROGRESS in status_seen

    def test_execute_with_resume(self, mock_db):
        steps = [_make_mock_step(i, f"step_{i}") for i in range(5)]
        deployment = _make_mock_deployment(steps=steps, resume_from_step=3)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.commit = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        result = svc.execute_deployment(1)

        # Should only execute steps 3, 4
        assert result["completed"] == 2

    def test_execute_step_failure(self, mock_db):
        steps = [_make_mock_step(0, "good_step"), _make_mock_step(1, "bad_step")]
        deployment = _make_mock_deployment(steps=steps)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.commit = MagicMock()

        svc = BareMetalDeploymentService(mock_db)

        # Make step 1 fail
        original_execute = svc._execute_step
        call_count = [0]

        def failing_execute(dep, step):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("Installation failed")
            return original_execute(dep, step)

        svc._execute_step = failing_execute

        with pytest.raises(RuntimeError, match="Installation failed"):
            svc.execute_deployment(1)

        assert deployment.status == BareMetalDeploymentStatus.FAILED
        assert deployment.error_step_index == 1

    def test_execute_skipped_step(self, mock_db):
        """Steps returning skipped=True increment skipped count."""
        steps = [_make_mock_step(0, "probe_step")]
        deployment = _make_mock_deployment(steps=steps)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.commit = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc._execute_step = lambda dep, step: {"skipped": True}

        result = svc.execute_deployment(1)

        assert result["skipped"] == 1
        assert result["completed"] == 0
        assert result["status"] == "completed"

    def test_execute_sets_started_at(self, mock_db):
        steps = [_make_mock_step(0)]
        deployment = _make_mock_deployment(steps=steps)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.commit = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.execute_deployment(1)

        assert deployment.started_at is not None

    def test_execute_sets_completed_at(self, mock_db):
        steps = [_make_mock_step(0)]
        deployment = _make_mock_deployment(steps=steps)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.commit = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.execute_deployment(1)

        assert deployment.completed_at is not None


# ---------------------------------------------------------------------------
# TestResumeDeployment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResumeDeployment:
    def test_resume_from_failed(self, mock_db):
        failed_step = _make_mock_step(3, "failed_step", status=DeploymentStepStatus.FAILED)
        deployment = _make_mock_deployment(
            status=BareMetalDeploymentStatus.FAILED,
            steps=[_make_mock_step(i) for i in range(3)] + [failed_step],
        )
        deployment.error_step_index = 3
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        result = svc.resume_deployment(1)

        assert result.resume_from_step == 3
        assert result.status == BareMetalDeploymentStatus.PENDING

    def test_resume_from_specific_step(self, mock_db):
        deployment = _make_mock_deployment(
            status=BareMetalDeploymentStatus.FAILED,
            steps=[_make_mock_step(i) for i in range(5)],
        )
        deployment.error_step_index = 3
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        result = svc.resume_deployment(1, from_step=1)

        assert result.resume_from_step == 1

    def test_resume_wrong_status(self, mock_db):
        deployment = _make_mock_deployment(status=BareMetalDeploymentStatus.IN_PROGRESS)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="Cannot resume"):
            svc.resume_deployment(1)

    def test_resume_paused_deployment(self, mock_db):
        deployment = _make_mock_deployment(
            status=BareMetalDeploymentStatus.PAUSED,
            steps=[],
        )
        deployment.error_step_index = None
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        result = svc.resume_deployment(1)

        assert result.status == BareMetalDeploymentStatus.PENDING
        assert result.resume_from_step == 0

    def test_resume_clears_error_state(self, mock_db):
        deployment = _make_mock_deployment(status=BareMetalDeploymentStatus.FAILED, steps=[])
        deployment.error_step_index = 2
        deployment.error_message = "Something failed"
        deployment.error_phase = "phase_1_dpu"
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.resume_deployment(1)

        assert deployment.error_message is None
        assert deployment.error_phase is None
        assert deployment.error_step_index is None

    def test_resume_resets_failed_steps(self, mock_db):
        """Failed steps at or after resume point should be reset to PENDING."""
        failed_step = _make_mock_step(2, status=DeploymentStepStatus.FAILED)
        ok_step = _make_mock_step(0, status=DeploymentStepStatus.COMPLETED)
        deployment = _make_mock_deployment(
            status=BareMetalDeploymentStatus.FAILED,
            steps=[ok_step, _make_mock_step(1, status=DeploymentStepStatus.COMPLETED), failed_step],
        )
        deployment.error_step_index = 2
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.resume_deployment(1, from_step=2)

        assert failed_step.status == DeploymentStepStatus.PENDING
        # Steps before resume point should not be reset
        assert ok_step.status == DeploymentStepStatus.COMPLETED


# ---------------------------------------------------------------------------
# TestCancelDeployment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelDeployment:
    def test_cancel_running(self, mock_db):
        deployment = _make_mock_deployment(status=BareMetalDeploymentStatus.IN_PROGRESS)
        deployment.started_at = datetime.now(UTC)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        result = svc.cancel_deployment(1)

        assert result.status == BareMetalDeploymentStatus.CANCELLED

    def test_cancel_pending(self, mock_db):
        deployment = _make_mock_deployment(status=BareMetalDeploymentStatus.PENDING)
        deployment.started_at = None
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        result = svc.cancel_deployment(1)

        assert result.status == BareMetalDeploymentStatus.CANCELLED

    def test_cancel_terminal_raises(self, mock_db):
        deployment = _make_mock_deployment(status=BareMetalDeploymentStatus.COMPLETED)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="Cannot cancel"):
            svc.cancel_deployment(1)

    def test_cancel_already_failed_raises(self, mock_db):
        deployment = _make_mock_deployment(status=BareMetalDeploymentStatus.FAILED)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment

        svc = BareMetalDeploymentService(mock_db)
        with pytest.raises(Exception, match="Cannot cancel"):
            svc.cancel_deployment(1)

    def test_cancel_sets_completed_at(self, mock_db):
        deployment = _make_mock_deployment(status=BareMetalDeploymentStatus.IN_PROGRESS)
        deployment.started_at = None
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.cancel_deployment(1)

        assert deployment.completed_at is not None

    @patch("services.bare_metal.orchestrator.celery_app", create=True)
    def test_cancel_revokes_celery(self, mock_celery, mock_db):
        deployment = _make_mock_deployment(
            status=BareMetalDeploymentStatus.IN_PROGRESS,
            celery_task_id="task-abc-123",
        )
        deployment.started_at = datetime.now(UTC)
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        svc.cancel_deployment(1)

        assert deployment.status == BareMetalDeploymentStatus.CANCELLED

    def test_cancel_no_celery_task_ok(self, mock_db):
        """Cancellation with no Celery task ID should not raise."""
        deployment = _make_mock_deployment(
            status=BareMetalDeploymentStatus.PENDING,
            celery_task_id=None,
        )
        deployment.started_at = None
        mock_db.query.return_value.filter.return_value.first.return_value = deployment
        mock_db.flush = MagicMock()

        svc = BareMetalDeploymentService(mock_db)
        result = svc.cancel_deployment(1)

        assert result.status == BareMetalDeploymentStatus.CANCELLED
