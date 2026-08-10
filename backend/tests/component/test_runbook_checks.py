"""
Tests for services.runbook_service — internal check functions and helpers.

Extends coverage beyond existing test_runbook_service.py by testing
_has_condition, _get_condition_message, _RunbookContext caching,
and individual check functions with mocked K8s data.
"""

from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from services.runbook_service import (
    RunbookDefinition,
    RunbookStep,
    RunbookStepResult,
    _check_bnk_namespaces,
    _check_bnk_version,
    _check_flo_operator,
    _check_image_pull_secrets,
    _check_pending_pods,
    _check_recent_events,
    _get_condition_message,
    _has_condition,
    _RunbookContext,
)

# ── _has_condition ───────────────────────────────────────────────────────

class TestHasCondition:
    def test_condition_present_and_true(self):
        resource = {
            "status": {
                "conditions": [
                    {"type": "Programmed", "status": "True"},
                    {"type": "Accepted", "status": "True"},
                ]
            }
        }
        assert _has_condition(resource, "Programmed") is True

    def test_condition_present_but_false(self):
        resource = {
            "status": {
                "conditions": [{"type": "Programmed", "status": "False"}]
            }
        }
        assert _has_condition(resource, "Programmed") is False

    def test_condition_not_present(self):
        resource = {
            "status": {
                "conditions": [{"type": "Ready", "status": "True"}]
            }
        }
        assert _has_condition(resource, "Programmed") is False

    def test_no_conditions(self):
        resource = {"status": {}}
        assert _has_condition(resource, "Programmed") is False

    def test_no_status(self):
        resource = {}
        assert _has_condition(resource, "Programmed") is False

    def test_custom_expected_value(self):
        resource = {
            "status": {
                "conditions": [{"type": "Ready", "status": "False"}]
            }
        }
        assert _has_condition(resource, "Ready", expected="False") is True


# ── _get_condition_message ───────────────────────────────────────────────

class TestGetConditionMessage:
    def test_returns_message(self):
        resource = {
            "status": {
                "conditions": [
                    {"type": "Programmed", "status": "False", "message": "no listeners"},
                ]
            }
        }
        assert _get_condition_message(resource, "Programmed") == "no listeners"

    def test_returns_empty_when_no_message(self):
        resource = {
            "status": {
                "conditions": [{"type": "Programmed", "status": "True"}]
            }
        }
        assert _get_condition_message(resource, "Programmed") == ""

    def test_returns_empty_when_condition_missing(self):
        resource = {"status": {"conditions": []}}
        assert _get_condition_message(resource, "Programmed") == ""


# ── _RunbookContext caching ──────────────────────────────────────────────

class TestRunbookContext:
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_pods_cached(self, mock_discover, mock_classify):
        mock_discover.return_value = (["pod1"], ["pod2"])
        mock_classify.return_value = {"tmm": [], "flo": []}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        _ = ctx.pods
        _ = ctx.pods  # second access
        mock_discover.assert_called_once()

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_classified_triggers_discovery(self, mock_discover, mock_classify):
        mock_discover.return_value = ([], [])
        mock_classify.return_value = {"tmm": ["t1"], "flo": ["f1"]}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        result = ctx.classified
        assert result["tmm"] == ["t1"]
        mock_discover.assert_called_once()


# ── _check_flo_operator ─────────────────────────────────────────────────

class TestCheckFloOperator:
    @patch("services.runbook_service.pod_is_healthy", return_value=True)
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_healthy_flo(self, mock_discover, mock_classify, mock_healthy):
        mock_discover.return_value = ([], [])
        flo_pod = {"name": "flo-1", "namespace": "f5-operator", "phase": "Running", "containers": []}
        mock_classify.return_value = {"flo": [flo_pod], "tmm": []}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_flo_operator(ctx)
        assert passed is True
        assert "healthy" in msg.lower()

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_no_flo_pods_no_other_bnk_pods_is_neutral(self, mock_discover, mock_classify):
        """BNK not installed at all (no FLO, no TMM/controller) — detect_install_shape
        returns 'unknown', which is != 'flo', so an absent FLO isn't reported as a
        failure (mirrors services/bnk/health.py:258)."""
        mock_discover.return_value = ([], [])
        mock_classify.return_value = {"flo": [], "tmm": []}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_flo_operator(ctx)
        assert passed is True

    @patch("services.runbook_service.pod_is_healthy", return_value=True)
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_helm_shape_no_flo_pods_is_neutral(self, mock_discover, mock_classify, mock_healthy):
        """Direct-helm install (TMM/controller present, no FLO) — expected, not a failure."""
        mock_discover.return_value = ([], [])
        tmm_pod = {"name": "f5-tmm-1", "namespace": "bnk-app1", "phase": "Running", "containers": []}
        mock_classify.return_value = {"flo": [], "tmm": [tmm_pod], "controller": []}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_flo_operator(ctx)
        assert passed is True
        assert "helm" in msg.lower()
        assert details["install_shape"] == "helm"

    @patch("services.runbook_service.pod_is_healthy", return_value=False)
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_flo_shape_unhealthy_still_fails(self, mock_discover, mock_classify, mock_healthy):
        """FLO-shape install (FLO pods present but unhealthy) keeps existing failure semantics."""
        mock_discover.return_value = ([], [])
        flo_pod = {"name": "flo-1", "namespace": "f5-operator", "phase": "Pending", "containers": []}
        mock_classify.return_value = {"flo": [flo_pod], "tmm": []}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_flo_operator(ctx)
        assert passed is False
        assert "unhealthy" in msg.lower()


# ── _check_bnk_version ──────────────────────────────────────────────────

class TestCheckBnkVersion:
    @patch("services.runbook_service._safe_fetch_crd", return_value=[])
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_version_from_image_tag(self, mock_discover, mock_classify, mock_crd):
        mock_discover.return_value = ([], [])
        flo_pod = {
            "name": "flo-1", "namespace": "f5-operator", "phase": "Running",
            "containers": [{"image": "repo.f5.com/flo:2.5.0", "name": "flo"}],
        }
        mock_classify.return_value = {"flo": [flo_pod], "tmm": []}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_bnk_version(ctx)
        assert passed is True
        assert "2.5.0" in msg

    @patch("services.runbook_service._safe_fetch_crd", return_value=[])
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_no_bnk_pods_at_all_is_neutral(self, mock_discover, mock_classify, mock_crd):
        """No FLO and no TMM/controller — nothing installed yet, not a hard failure
        (install shape is 'unknown', not 'flo')."""
        mock_discover.return_value = ([], [])
        mock_classify.return_value = {"flo": [], "tmm": []}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_bnk_version(ctx)
        assert passed is True
        assert "No BNK pods" in msg

    @patch("services.runbook_service._safe_fetch_crd", return_value=[])
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_helm_shape_version_from_tmm_image(self, mock_discover, mock_classify, mock_crd):
        """Helm/manual install (no FLO) — version falls back to TMM container image tag."""
        mock_discover.return_value = ([], [])
        tmm_pod = {
            "name": "f5-tmm-1", "namespace": "bnk-app1", "phase": "Running",
            "containers": [{"image": "repo.f5.com/tmm:2.5.0", "name": "tmm"}],
        }
        mock_classify.return_value = {"flo": [], "tmm": [tmm_pod], "controller": []}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_bnk_version(ctx)
        assert passed is True
        assert "2.5.0" in msg
        assert details["version_source"] == "tmm"

    @patch("services.runbook_service._safe_fetch_crd", return_value=[])
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_helm_controller_version_prefers_f5ingress_over_sidecars(
        self, mock_discover, mock_classify, mock_crd
    ):
        """Regression (#391, cluster 54): the f5ingress controller pod has 4 containers,
        with the f5-license-helper sidecar listed first. The reported version must be the
        controller (f5ingress) image tag v14.59.1-0.0.70, NOT the sidecar v0.15.1-0.0.2."""
        mock_discover.return_value = ([], [])
        controller_pod = {
            "name": "f5ingress-f5ingress-abc", "namespace": "f5-cne-core", "phase": "Running",
            "containers": [
                {"image": "repo.f5.com/images/f5-license-helper:v0.15.1-0.0.2", "name": "license-helper"},
                {"image": "repo.f5.com/images/f5ing-tmm-pod-manager:v1.6.1-0.0.4", "name": "pod-manager"},
                {"image": "repo.f5.com/images/f5-fluentbit:v1.5.2", "name": "fluentbit"},
                {"image": "repo.f5.com/images/f5ingress:v14.59.1-0.0.70", "name": "f5ingress"},
            ],
        }
        mock_classify.return_value = {"flo": [], "tmm": [], "controller": [controller_pod]}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_bnk_version(ctx)
        assert passed is True
        assert details["version_source"] == "controller"
        assert details["detected_version"] == "v14.59.1-0.0.70"
        assert "v14.59.1-0.0.70" in msg
        assert "v0.15.1-0.0.2" not in msg


# ── _check_pending_pods ──────────────────────────────────────────────────

class TestCheckPendingPods:
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_no_stuck_pods(self, mock_discover, mock_classify):
        mock_discover.return_value = (
            [{"name": "p1", "namespace": "f5-bnk", "phase": "Running", "containers": []}],
            [],
        )
        mock_classify.return_value = {}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_pending_pods(ctx)
        assert passed is True

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_stuck_pending_pod(self, mock_discover, mock_classify):
        mock_discover.return_value = (
            [{"name": "p-stuck", "namespace": "f5-bnk", "phase": "Pending", "containers": []}],
            [],
        )
        mock_classify.return_value = {}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        passed, msg, details, link = _check_pending_pods(ctx)
        assert passed is False
        assert "stuck" in msg.lower()


# ── Discovered-namespace set (Defect 2 / D-019) ──────────────────────────

class TestDiscoveredNamespaces:
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_pod_in_nonstandard_namespace_is_included(self, mock_discover, mock_classify):
        tenant_pod = {"name": "f5-tmm-1", "namespace": "bnk-app1", "phase": "Running", "containers": []}
        mock_discover.return_value = ([tenant_pod], [])
        mock_classify.return_value = {"tmm": [tenant_pod]}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        ns = ctx.namespaces
        assert "bnk-app1" in ns
        # Union, not replace — the static namespaces are still present.
        assert "f5-bnk" in ns
        assert "f5-operator" in ns

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_cluster_default_namespace_included(self, mock_discover, mock_classify):
        mock_discover.return_value = ([], [])
        mock_classify.return_value = {}
        cluster = MagicMock()
        cluster.default_namespace = "custom-ns"
        cluster.discovered_namespaces = None
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1, cluster=cluster)

        assert "custom-ns" in ctx.namespaces

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_bnk_namespaces_check_inspects_nonstandard_namespace(self, mock_discover, mock_classify):
        """_check_bnk_namespaces reads the namespace a discovered BNK pod actually lives in,
        not just the hardcoded BNK_NAMESPACES list."""
        tenant_pod = {"name": "f5-tmm-1", "namespace": "bnk-app1", "phase": "Running", "containers": []}
        mock_discover.return_value = ([tenant_pod], [])
        mock_classify.return_value = {"tmm": [tenant_pod]}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        with patch("services.runbook_service.k8s_client.CoreV1Api") as mock_core_cls:
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            _check_bnk_namespaces(ctx)

        checked_namespaces = {c.args[0] for c in mock_core.read_namespace.call_args_list}
        assert "bnk-app1" in checked_namespaces
        assert "f5-bnk" in checked_namespaces

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_image_pull_secrets_check_scoped_to_nonstandard_namespace(self, mock_discover, mock_classify):
        """_check_image_pull_secrets looks in a non-standard install namespace too."""
        tenant_pod = {"name": "f5-tmm-1", "namespace": "bnk-app1", "phase": "Running", "containers": []}
        mock_discover.return_value = ([tenant_pod], [])
        mock_classify.return_value = {"tmm": [tenant_pod]}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        secret = MagicMock()
        secret.type = "kubernetes.io/dockerconfigjson"
        secret.metadata.name = "regcred"
        secrets_resp = MagicMock()
        secrets_resp.items = [secret]

        with patch("services.runbook_service.k8s_client.CoreV1Api") as mock_core_cls:
            mock_core = MagicMock()
            mock_core.list_namespaced_secret.return_value = secrets_resp
            mock_core_cls.return_value = mock_core
            passed, msg, details, link = _check_image_pull_secrets(ctx)

        checked_namespaces = {c.args[0] for c in mock_core.list_namespaced_secret.call_args_list}
        assert "bnk-app1" in checked_namespaces
        assert any(entry["namespace"] == "bnk-app1" for entry in details["with_secrets"])

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_image_pull_secrets_ignores_static_hint_namespaces(self, mock_discover, mock_classify):
        """Regression (#391, cluster 54): BNK runs only in the discovered namespace
        (bnk-app1) which HAS a pull secret. The static hint namespaces
        (f5-bnk/f5-operator/f5-utils/default) must NOT be scanned — scanning them
        was what made the live check FAIL with 'No image pull secrets in: f5-bnk,
        f5-operator, f5-utils, default' even though the real BNK namespace was fine."""
        tenant_pod = {"name": "f5-tmm-1", "namespace": "bnk-app1", "phase": "Running", "containers": []}
        mock_discover.return_value = ([tenant_pod], [])
        mock_classify.return_value = {"tmm": [tenant_pod]}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        secret = MagicMock()
        secret.type = "kubernetes.io/dockerconfigjson"
        secret.metadata.name = "regcred"
        secrets_resp = MagicMock()
        secrets_resp.items = [secret]

        with patch("services.runbook_service.k8s_client.CoreV1Api") as mock_core_cls:
            mock_core = MagicMock()
            mock_core.list_namespaced_secret.return_value = secrets_resp
            mock_core_cls.return_value = mock_core
            passed, msg, details, link = _check_image_pull_secrets(ctx)

        checked_namespaces = {c.args[0] for c in mock_core.list_namespaced_secret.call_args_list}
        # Only the discovered namespace is inspected — no static hints.
        assert checked_namespaces == {"bnk-app1"}
        for hint in ("f5-bnk", "f5-operator", "f5-utils", "default"):
            assert hint not in checked_namespaces
        # And the check PASSES (no false failure).
        assert passed is True
        assert details["without_secrets"] == []

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_image_pull_secrets_no_discovered_namespaces_is_neutral(self, mock_discover, mock_classify):
        """Nothing discovered — the check must not iterate the raw static hints; it
        returns a graceful pass instead of a false failure."""
        mock_discover.return_value = ([], [])
        mock_classify.return_value = {}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        with patch("services.runbook_service.k8s_client.CoreV1Api") as mock_core_cls:
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            passed, msg, details, link = _check_image_pull_secrets(ctx)

        assert mock_core.list_namespaced_secret.call_count == 0
        assert passed is True


# ── _check_recent_events (Runbook 3 fail-closed anchor) ──────────────────

class TestCheckRecentEventsFailClosed:
    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_no_discovered_namespaces_fails_closed(self, mock_discover, mock_classify):
        """Runbook 3's analog to _check_pod_events' guard (runbook_service.py:831):
        with nothing discovered, the check must not scan zero namespaces and report
        a vacuous pass — that would make 'BNK Upgrade Failed' report all-green on a
        pod-wiped cluster, the exact failure the runbook exists to diagnose."""
        mock_discover.return_value = ([], [])
        mock_classify.return_value = {}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        with patch("services.runbook_service.k8s_client.CoreV1Api") as mock_core_cls:
            mock_core = MagicMock()
            mock_core_cls.return_value = mock_core
            passed, msg, details, link = _check_recent_events(ctx)

        assert mock_core.list_namespaced_event.call_count == 0
        assert passed is False
        assert "discovered" in msg.lower()

    @patch("services.runbook_service.classify_f5_pods")
    @patch("services.runbook_service.discover_f5_pods")
    def test_discovered_namespace_with_no_events_still_passes(self, mock_discover, mock_classify):
        """Sanity check: the guard only fires when discovery is truly empty — a
        discovered namespace with no recent warning events still passes."""
        tenant_pod = {"name": "f5-tmm-1", "namespace": "bnk-app1", "phase": "Running", "containers": []}
        mock_discover.return_value = ([tenant_pod], [])
        mock_classify.return_value = {"tmm": [tenant_pod]}
        ctx = _RunbookContext(api_client=MagicMock(), cluster_id=1)

        with patch("services.runbook_service.k8s_client.CoreV1Api") as mock_core_cls:
            mock_core = MagicMock()
            empty = MagicMock()
            empty.items = []
            mock_core.list_namespaced_event.return_value = empty
            mock_core_cls.return_value = mock_core
            passed, msg, details, link = _check_recent_events(ctx)

        assert passed is True


# ── Data structures ──────────────────────────────────────────────────────

class TestDataStructures:
    def test_runbook_step_result_fields(self):
        r = RunbookStepResult(step_index=0, name="test", status="pass", message="ok")
        assert r.step_index == 0
        assert r.status == "pass"

    def test_runbook_definition(self):
        rd = RunbookDefinition(
            id="test-rb", name="Test", description="desc", category="test",
            steps=[RunbookStep(name="s1", description="d", check_fn_name="fn",
                               pass_message="ok", fail_message="fail")],
        )
        assert len(rd.steps) == 1
        assert rd.steps[0].check_fn_name == "fn"
