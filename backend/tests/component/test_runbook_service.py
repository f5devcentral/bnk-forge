"""
Tests for services.runbook_service — runbook listing, lookup, and execution.

BC-012: RunbookService — pure functions tested directly, execute_runbook with
mocked KubernetesService and K8s API clients.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.runbook_service import (
    RUNBOOKS,
    execute_runbook,
    get_runbook,
    list_runbooks,
)

# ---------------------------------------------------------------------------
# list_runbooks (pure)
# ---------------------------------------------------------------------------

class TestListRunbooks:
    """Listing all available runbook definitions."""

    def test_returns_all_runbooks(self):
        result = list_runbooks()
        assert len(result) == len(RUNBOOKS)

    def test_each_has_required_fields(self):
        result = list_runbooks()
        for rb in result:
            assert "id" in rb
            assert "name" in rb
            assert "description" in rb
            assert "category" in rb
            assert "step_count" in rb
            assert "steps" in rb
            assert rb["step_count"] == len(rb["steps"])

    def test_steps_have_name_and_description(self):
        result = list_runbooks()
        for rb in result:
            for step in rb["steps"]:
                assert "name" in step
                assert "description" in step

    def test_known_runbook_ids(self):
        ids = {rb["id"] for rb in list_runbooks()}
        assert "gateway-not-routing" in ids
        assert "pods-not-starting" in ids
        assert "upgrade-failed" in ids

    def test_categories(self):
        categories = {rb["category"] for rb in list_runbooks()}
        assert "networking" in categories
        assert "platform" in categories
        assert "upgrade" in categories


# ---------------------------------------------------------------------------
# get_runbook (pure)
# ---------------------------------------------------------------------------

class TestGetRunbook:
    """Looking up a single runbook definition by ID."""

    def test_get_existing_runbook(self):
        rb = get_runbook("gateway-not-routing")
        assert rb is not None
        assert rb.name == "Gateway Not Routing Traffic"
        assert len(rb.steps) == 5

    def test_get_pods_not_starting(self):
        rb = get_runbook("pods-not-starting")
        assert rb is not None
        assert rb.category == "platform"

    def test_get_upgrade_failed(self):
        rb = get_runbook("upgrade-failed")
        assert rb is not None
        assert rb.category == "upgrade"

    def test_get_nonexistent_returns_none(self):
        assert get_runbook("does-not-exist") is None


# ---------------------------------------------------------------------------
# execute_runbook
# ---------------------------------------------------------------------------

class TestExecuteRunbook:
    """Runbook execution with mocked K8s APIs."""

    def _make_k8s_service(self):
        """Build a mock KubernetesService that returns a mock cluster and api_client."""
        k8s_svc = MagicMock()
        k8s_svc.get_cluster.return_value = MagicMock(id=1, name="test-cluster")
        k8s_svc.load_kubeconfig.return_value = MagicMock()  # api_client
        return k8s_svc

    def test_invalid_runbook_raises(self):
        k8s_svc = self._make_k8s_service()
        with pytest.raises(ValueError, match="not found"):
            execute_runbook(k8s_svc, cluster_id=1, runbook_id="nonexistent")

    @patch("services.runbook_service.CHECK_FUNCTIONS", {})
    def test_missing_check_fn_skips_step(self):
        """When a check function isn't registered, the step is skipped."""
        k8s_svc = self._make_k8s_service()
        result = execute_runbook(k8s_svc, cluster_id=1, runbook_id="gateway-not-routing")
        assert result["runbook_id"] == "gateway-not-routing"
        for step in result["steps"]:
            assert step["status"] == "skip"
            assert "not implemented" in step["message"]

    @patch("services.runbook_service.classify_f5_pods", return_value={})
    @patch("services.runbook_service.discover_f5_pods", return_value=([], []))
    @patch("services.runbook_service._safe_fetch_crd", return_value=[])
    def test_gateway_runbook_no_resources(self, mock_crd, mock_discover, mock_classify):
        """Gateway runbook with no CRDs reports failures for missing resources."""
        k8s_svc = self._make_k8s_service()

        # Mock CoreV1Api to return empty endpoints
        with patch("services.runbook_service.k8s_client.CoreV1Api"):
            result = execute_runbook(k8s_svc, cluster_id=1, runbook_id="gateway-not-routing")

        assert result["runbook_id"] == "gateway-not-routing"
        assert result["status"] in ("fail", "partial")
        assert len(result["steps"]) == 5
        # First step should fail — no gateways found
        assert result["steps"][0]["status"] == "fail"
        assert result["cluster_id"] == 1

    @patch("services.runbook_service.CHECK_FUNCTIONS")
    def test_all_steps_pass(self, mock_fns):
        """When every check returns pass, overall status is 'pass'."""
        # Make every check function return pass
        def _pass_fn(ctx):
            return True, "All good", {}, None

        rb = get_runbook("gateway-not-routing")
        mock_fns.get = lambda name, default=None: _pass_fn

        k8s_svc = self._make_k8s_service()
        result = execute_runbook(k8s_svc, cluster_id=1, runbook_id="gateway-not-routing")
        assert result["status"] == "pass"
        assert result["summary"].startswith("All")
        for step in result["steps"]:
            assert step["status"] == "pass"

    @patch("services.runbook_service.CHECK_FUNCTIONS")
    def test_partial_pass(self, mock_fns):
        """Mix of pass/fail steps yields 'partial' overall status."""
        call_count = {"n": 0}

        def _alternating_fn(ctx):
            call_count["n"] += 1
            if call_count["n"] % 2 == 0:
                return False, "Problem found", {}, None
            return True, "OK", {}, None

        mock_fns.get = lambda name, default=None: _alternating_fn

        k8s_svc = self._make_k8s_service()
        result = execute_runbook(k8s_svc, cluster_id=1, runbook_id="gateway-not-routing")
        assert result["status"] == "partial"
        statuses = [s["status"] for s in result["steps"]]
        assert "pass" in statuses
        assert "fail" in statuses

    @patch("services.runbook_service.CHECK_FUNCTIONS")
    def test_step_exception_counts_as_fail(self, mock_fns):
        """If a check function raises, the step is marked as fail."""
        def _exploding_fn(ctx):
            raise RuntimeError("K8s connection lost")

        mock_fns.get = lambda name, default=None: _exploding_fn

        k8s_svc = self._make_k8s_service()
        result = execute_runbook(k8s_svc, cluster_id=1, runbook_id="pods-not-starting")
        assert result["status"] == "fail"
        for step in result["steps"]:
            assert step["status"] == "fail"
            assert "error" in step["message"].lower()

    def test_result_structure(self):
        """Verify the output dict has the expected top-level keys."""
        k8s_svc = self._make_k8s_service()

        # Patch all check functions to pass quickly
        with patch("services.runbook_service.CHECK_FUNCTIONS") as mock_fns:
            mock_fns.get = lambda name, default=None: (lambda ctx: (True, "ok", {}, None))
            result = execute_runbook(k8s_svc, cluster_id=42, runbook_id="upgrade-failed")

        assert set(result.keys()) == {
            "runbook_id", "runbook_name", "status", "summary", "steps", "cluster_id",
        }
        assert result["cluster_id"] == 42
        assert result["runbook_name"] == "BNK Upgrade Failed"


# ---------------------------------------------------------------------------
# Install-shape awareness (issue #391) — helm/manual installs shouldn't
# false-fail on an absent FLO operator.
# ---------------------------------------------------------------------------

class TestInstallShapeAwareness:
    def _make_k8s_service(self):
        k8s_svc = MagicMock()
        k8s_svc.get_cluster.return_value = MagicMock(id=1, name="test-cluster")
        k8s_svc.load_kubeconfig.return_value = MagicMock()
        return k8s_svc

    def _core_v1_stub(self):
        """A CoreV1Api double with empty-but-successful list/read responses."""
        core = MagicMock()
        core.read_namespace.return_value = MagicMock()
        empty = MagicMock()
        empty.items = []
        core.list_namespaced_secret.return_value = empty
        core.list_namespaced_event.return_value = empty
        core.list_namespaced_persistent_volume_claim.return_value = empty
        core.list_node.return_value = empty
        return core

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_pods_not_starting_helm_shape_no_false_flo_failure(self, mock_discover, mock_classify):
        """Helm install (TMM present, no FLO) — the FLO step must not report a failure."""
        tmm_pod = {"name": "f5-tmm-1", "namespace": "bnk-app1", "phase": "Running", "containers": []}
        mock_discover.return_value = ([tmm_pod], [])
        mock_classify.return_value = {"tmm": [tmm_pod], "flo": [], "controller": []}

        k8s_svc = self._make_k8s_service()
        with patch("services.runbook_service.k8s_client.CoreV1Api", return_value=self._core_v1_stub()):
            result = execute_runbook(k8s_svc, cluster_id=1, runbook_id="pods-not-starting")

        flo_step = next(s for s in result["steps"] if s["name"] == "Check FLO Operator")
        assert flo_step["status"] != "fail"

    @patch("services.runbook_service._safe_fetch_crd", return_value=[])
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_upgrade_failed_helm_shape_no_false_flo_failure(self, mock_discover, mock_classify, mock_crd):
        """Helm install (TMM present, no FLO) — version + FLO-version steps must not
        report a false failure due to FLO's absence."""
        tmm_pod = {
            "name": "f5-tmm-1", "namespace": "bnk-app1", "phase": "Running",
            "containers": [{"image": "repo.f5.com/tmm:2.5.0", "name": "tmm"}],
        }
        mock_discover.return_value = ([tmm_pod], [])
        mock_classify.return_value = {"tmm": [tmm_pod], "flo": [], "controller": []}

        k8s_svc = self._make_k8s_service()
        with patch("services.runbook_service.k8s_client.CoreV1Api", return_value=self._core_v1_stub()):
            result = execute_runbook(k8s_svc, cluster_id=1, runbook_id="upgrade-failed")

        version_step = next(s for s in result["steps"] if s["name"] == "Check BNK Version")
        flo_version_step = next(s for s in result["steps"] if s["name"] == "Check FLO Operator Version")
        assert version_step["status"] != "fail"
        assert flo_version_step["status"] != "fail"
        assert "2.5.0" in version_step["message"]

    @patch("services.runbook_service.pod_is_healthy", return_value=True)
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_flo_shape_behavior_unchanged(self, mock_discover, mock_classify, mock_healthy):
        """FLO-shape install (FLO pods present and healthy) — steps still pass as before."""
        flo_pod = {
            "name": "flo-1", "namespace": "f5-operator", "phase": "Running",
            "containers": [{"image": "repo.f5.com/flo:2.5.0", "name": "flo"}],
        }
        mock_discover.return_value = ([], [])
        mock_classify.return_value = {"flo": [flo_pod], "tmm": [], "controller": []}

        k8s_svc = self._make_k8s_service()
        with patch("services.runbook_service.k8s_client.CoreV1Api", return_value=self._core_v1_stub()), \
                patch("services.runbook_service._safe_fetch_crd", return_value=[]):
            result = execute_runbook(k8s_svc, cluster_id=1, runbook_id="pods-not-starting")

        flo_step = next(s for s in result["steps"] if s["name"] == "Check FLO Operator")
        assert flo_step["status"] == "pass"
        assert "healthy" in flo_step["message"].lower()


# ---------------------------------------------------------------------------
# Fail-closed on empty discovery — Runbook 3 must not report all-green on a
# pod-wiped cluster (mirrors the Runbook 2 pod_events guard, mwiget review).
# ---------------------------------------------------------------------------

class TestUpgradeFailedRunbookFailClosed:
    def _make_k8s_service(self):
        k8s_svc = MagicMock()
        k8s_svc.get_cluster.return_value = MagicMock(id=1, name="test-cluster")
        k8s_svc.load_kubeconfig.return_value = MagicMock()
        return k8s_svc

    def _core_v1_stub(self):
        core = MagicMock()
        core.read_namespace.return_value = MagicMock()
        empty = MagicMock()
        empty.items = []
        core.list_namespaced_secret.return_value = empty
        core.list_namespaced_event.return_value = empty
        core.list_namespaced_persistent_volume_claim.return_value = empty
        core.list_node.return_value = empty
        return core

    @patch("services.runbook_service._safe_fetch_crd", return_value=[])
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_upgrade_failed_no_discovered_pods_does_not_report_all_pass(
        self, mock_discover, mock_classify, mock_crd
    ):
        """A helm upgrade that tore down the old deployment and can't schedule the
        new ReplicaSet leaves zero discoverable BNK pods. Runbook 3 must not report
        'pass' on every step in that state — the recent-events check anchors it
        fail-closed, same as pod_events does for Runbook 2."""
        mock_discover.return_value = ([], [])
        mock_classify.return_value = {}

        k8s_svc = self._make_k8s_service()
        with patch("services.runbook_service.k8s_client.CoreV1Api", return_value=self._core_v1_stub()):
            result = execute_runbook(k8s_svc, cluster_id=1, runbook_id="upgrade-failed")

        assert result["status"] != "pass"
        events_step = next(s for s in result["steps"] if s["name"] == "Check Recent Events")
        assert events_step["status"] == "fail"
