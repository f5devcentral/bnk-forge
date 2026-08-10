"""Integration tests for BNK recovery routes."""

from unittest.mock import MagicMock, patch


class TestRecoveryStatusEndpoint:
    """Tests for GET /api/k8s/clusters/{id}/recovery/status."""

    @patch("routes.k8s.recovery._check_vlans_failed")
    @patch("routes.k8s.recovery._check_cwc_cert_stale")
    @patch("routes.k8s.recovery.KubernetesService")
    def test_status_returns_healthy(
        self,
        mock_k8s_svc,
        mock_cert_check,
        mock_vlan_check,
        client,
        operator_headers,
        all_test_users,
    ):
        mock_k8s_svc.return_value.get_cluster.return_value = MagicMock()
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_cert_check.return_value = (False, "cwc-license-certs matches cert-manager — OK", "ok")
        mock_vlan_check.return_value = (False, "All 2 VLANs programmed — OK")

        resp = client.get("/api/k8s/clusters/1/recovery/status", headers=operator_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["cwc_cert_stale"] is False
        assert data["cwc_cert_status"] == "ok"
        assert data["vlans_failed"] is False
        assert data["platform_healthy"] is True
        assert "OK" in data["cwc_cert_detail"]
        assert "OK" in data["vlans_detail"]

    @patch("routes.k8s.recovery._check_vlans_failed")
    @patch("routes.k8s.recovery._check_cwc_cert_stale")
    @patch("routes.k8s.recovery.KubernetesService")
    def test_status_returns_issues(
        self,
        mock_k8s_svc,
        mock_cert_check,
        mock_vlan_check,
        client,
        operator_headers,
        all_test_users,
    ):
        mock_k8s_svc.return_value.get_cluster.return_value = MagicMock()
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_cert_check.return_value = (True, "cwc-license-certs has stale certs", "stale")
        mock_vlan_check.return_value = (True, "sf-external: Failure in sending CR config")

        resp = client.get("/api/k8s/clusters/1/recovery/status", headers=operator_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["cwc_cert_stale"] is True
        assert data["vlans_failed"] is True
        assert data["platform_healthy"] is False

    def test_status_requires_auth(self, client):
        resp = client.get("/api/k8s/clusters/1/recovery/status")
        assert resp.status_code == 401


class TestCWCCertResyncEndpoint:
    """Tests for POST /api/k8s/clusters/{id}/recovery/cwc-certs."""

    @patch("routes.k8s.recovery._detect_cwc_namespace", return_value="f5-utils")
    @patch("routes.k8s.recovery._cleanup_all_client_pods")
    @patch("routes.k8s.recovery._restart_cwc_pod")
    @patch("routes.k8s.recovery._copy_cert_to_cwc_license_secret")
    @patch("routes.k8s.recovery._wait_for_secret")
    @patch("routes.k8s.recovery.KubernetesService")
    def test_resync_success(
        self,
        mock_k8s_svc,
        mock_wait,
        mock_copy,
        mock_restart,
        mock_cleanup,
        _mock_detect,
        client,
        operator_headers,
        all_test_users,
    ):
        mock_k8s_svc.return_value.get_cluster.return_value = MagicMock()
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_wait.return_value = {
            "tls.crt": "cert-data",
            "tls.key": "key-data",
            "ca.crt": "ca-data",
        }
        mock_restart.return_value = "f5-spk-cwc-old-pod"

        resp = client.post("/api/k8s/clusters/1/recovery/cwc-certs", headers=operator_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert "re-synced" in data["message"].lower()
        assert len(data["steps"]) == 4
        # All steps should be 'ok'
        assert all(s["status"] == "ok" for s in data["steps"])

        mock_copy.assert_called_once()
        mock_restart.assert_called_once()
        mock_cleanup.assert_called_once()

    @patch("routes.k8s.recovery._detect_cwc_namespace", return_value="f5-utils")
    @patch("routes.k8s.recovery._wait_for_secret")
    @patch("routes.k8s.recovery.KubernetesService")
    def test_resync_fails_when_cert_missing(
        self,
        mock_k8s_svc,
        mock_wait,
        _mock_detect,
        client,
        operator_headers,
        all_test_users,
    ):
        from services.qkview_service import QKViewError

        mock_k8s_svc.return_value.get_cluster.return_value = MagicMock()
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_wait.side_effect = QKViewError("Timed out waiting for secret")

        resp = client.post("/api/k8s/clusters/1/recovery/cwc-certs", headers=operator_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is False
        assert "not found" in data["message"].lower() or "timed out" in data["message"].lower()

    def test_resync_requires_operator_auth(self, client, viewer_headers, all_test_users):
        resp = client.post("/api/k8s/clusters/1/recovery/cwc-certs", headers=viewer_headers)
        assert resp.status_code == 403

    @patch("routes.k8s.recovery._get_install_shape", return_value="helm")
    @patch("routes.k8s.recovery._detect_cwc_namespace", return_value="f5-cne-core")
    @patch("routes.k8s.recovery._wait_for_secret")
    @patch("routes.k8s.recovery.KubernetesService")
    def test_resync_tolerant_when_helm_install_and_secret_absent(
        self,
        mock_k8s_svc,
        mock_wait,
        _mock_detect,
        _mock_shape,
        client,
        operator_headers,
        all_test_users,
    ):
        """Direct-helm installs have no Forge-managed cert-manager secret — this
        should read as informational, not a failure."""
        from services.qkview_service import QKViewError

        mock_k8s_svc.return_value.get_cluster.return_value = MagicMock()
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_wait.side_effect = QKViewError("Timed out waiting for secret")

        resp = client.post("/api/k8s/clusters/1/recovery/cwc-certs", headers=operator_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert "managed outside Forge" in data["message"]
        assert data["steps"][0]["status"] == "skipped"


class TestPlatformRestartEndpoint:
    """Tests for POST /api/k8s/clusters/{id}/recovery/platform-restart."""

    @patch("routes.k8s.recovery._detect_bnk_tenant_namespace", return_value="f5-bnk")
    @patch("routes.k8s.recovery.k8s_client")
    @patch("routes.k8s.recovery.KubernetesService")
    def test_restart_controller_only(
        self,
        mock_k8s_svc,
        mock_k8s_client,
        _mock_detect_ns,
        client,
        operator_headers,
        all_test_users,
    ):
        mock_k8s_svc.return_value.get_cluster.return_value = MagicMock()
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()

        # Mock a controller pod
        ctrl_pod = MagicMock()
        ctrl_pod.metadata.name = "f5-cne-controller-abc123"
        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.return_value.items = [ctrl_pod]
        mock_k8s_client.CoreV1Api.return_value = mock_core_v1

        resp = client.post(
            "/api/k8s/clusters/1/recovery/platform-restart",
            headers=operator_headers,
            json={"restart_controller": True, "restart_flo": False, "restart_tmm": False},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert len(data["restarted"]) == 1
        assert data["restarted"][0]["component"] == "CNE Controller"
        assert data["restarted"][0]["status"] == "restarted"
        assert "f5-cne-controller-abc123" in data["restarted"][0]["deleted_pods"]

    @patch("routes.k8s.recovery._detect_bnk_tenant_namespace", return_value="f5-bnk")
    @patch("routes.k8s.recovery._find_and_restart_pods")
    @patch("routes.k8s.recovery.k8s_client")
    @patch("routes.k8s.recovery.KubernetesService")
    def test_restart_all_components(
        self,
        mock_k8s_svc,
        mock_k8s_client,
        mock_find_restart,
        _mock_detect_ns,
        client,
        operator_headers,
        all_test_users,
    ):
        mock_k8s_svc.return_value.get_cluster.return_value = MagicMock()
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()

        # Controller pod
        ctrl_pod = MagicMock()
        ctrl_pod.metadata.name = "f5-cne-controller-abc"
        # TMM pod
        tmm_pod = MagicMock()
        tmm_pod.metadata.name = "f5-tmm-xyz"

        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.return_value.items = [ctrl_pod, tmm_pod]
        mock_k8s_client.CoreV1Api.return_value = mock_core_v1

        mock_find_restart.return_value = {
            "component": "FLO Operator",
            "status": "restarted",
            "deleted_pods": ["flo-abc"],
            "message": "Deleted 1 FLO pod(s)",
        }

        resp = client.post(
            "/api/k8s/clusters/1/recovery/platform-restart",
            headers=operator_headers,
            json={"restart_controller": True, "restart_flo": True, "restart_tmm": True},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert len(data["restarted"]) == 3

    def test_restart_requires_operator_auth(self, client, viewer_headers, all_test_users):
        resp = client.post(
            "/api/k8s/clusters/1/recovery/platform-restart",
            headers=viewer_headers,
            json={"restart_controller": True, "restart_flo": False, "restart_tmm": False},
        )
        assert resp.status_code == 403

    def test_restart_requires_auth(self, client):
        resp = client.post(
            "/api/k8s/clusters/1/recovery/platform-restart",
            json={"restart_controller": True, "restart_flo": False, "restart_tmm": False},
        )
        assert resp.status_code == 401

    @patch("routes.k8s.recovery._detect_bnk_tenant_namespace", return_value="bnk-app1")
    @patch("routes.k8s.recovery.k8s_client")
    @patch("routes.k8s.recovery.KubernetesService")
    def test_restart_controller_matches_f5ingress_pod(
        self,
        mock_k8s_svc,
        mock_k8s_client,
        _mock_detect_ns,
        client,
        operator_headers,
        all_test_users,
    ):
        """Direct-helm installs name the controller pod 'f5ingress-f5ingress-*'."""
        mock_k8s_svc.return_value.get_cluster.return_value = MagicMock()
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()

        ctrl_pod = MagicMock()
        ctrl_pod.metadata.name = "f5ingress-f5ingress-abc123"
        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.return_value.items = [ctrl_pod]
        mock_k8s_client.CoreV1Api.return_value = mock_core_v1

        resp = client.post(
            "/api/k8s/clusters/1/recovery/platform-restart",
            headers=operator_headers,
            json={"restart_controller": True, "restart_flo": False, "restart_tmm": False},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["restarted"][0]["status"] == "restarted"
        assert "f5ingress-f5ingress-abc123" in data["restarted"][0]["deleted_pods"]


class TestBnkTenantNamespaceFallback:
    """Unit tests for _detect_bnk_tenant_namespace's cluster.default_namespace fallback."""

    def test_falls_back_to_cluster_default_namespace(self):
        from routes.k8s.recovery import _detect_bnk_tenant_namespace

        api_client = MagicMock()
        core_v1 = MagicMock()
        core_v1.list_namespaced_pod.return_value.items = []
        core_v1.list_pod_for_all_namespaces.return_value.items = []

        with patch("routes.k8s.recovery.k8s_client.CoreV1Api", return_value=core_v1):
            cluster = MagicMock(default_namespace="bnk-app1")
            ns = _detect_bnk_tenant_namespace(api_client, cluster)

        assert ns == "bnk-app1"

    def test_falls_back_to_hardcoded_default_when_no_cluster(self):
        from routes.k8s.recovery import _detect_bnk_tenant_namespace

        api_client = MagicMock()
        core_v1 = MagicMock()
        core_v1.list_namespaced_pod.return_value.items = []
        core_v1.list_pod_for_all_namespaces.return_value.items = []

        with patch("routes.k8s.recovery.k8s_client.CoreV1Api", return_value=core_v1):
            ns = _detect_bnk_tenant_namespace(api_client, cluster=None)

        assert ns == "f5-bnk"


class TestCwcCertStaleTolerant:
    """CWC cert staleness check should be tolerant of direct-helm installs."""

    def test_not_applicable_when_helm_install_and_secret_absent(self):
        from kubernetes.client.rest import ApiException

        from routes.k8s.recovery import _check_cwc_cert_stale

        api_client = MagicMock()
        core_v1 = MagicMock()
        core_v1.read_namespaced_secret.side_effect = ApiException(status=404)

        with (
            patch("routes.k8s.recovery.k8s_client.CoreV1Api", return_value=core_v1),
            patch("routes.k8s.recovery._detect_cwc_namespace", return_value="f5-cne-core"),
            patch("routes.k8s.recovery._get_install_shape", return_value="helm"),
        ):
            is_stale, detail, status = _check_cwc_cert_stale(api_client)

        assert is_stale is False
        assert status == "not_applicable"
        assert "managed outside Forge" in detail

    def test_unknown_when_secret_absent_and_not_helm(self):
        from kubernetes.client.rest import ApiException

        from routes.k8s.recovery import _check_cwc_cert_stale

        api_client = MagicMock()
        core_v1 = MagicMock()
        core_v1.read_namespaced_secret.side_effect = ApiException(status=404)

        with (
            patch("routes.k8s.recovery.k8s_client.CoreV1Api", return_value=core_v1),
            patch("routes.k8s.recovery._detect_cwc_namespace", return_value="f5-utils"),
            patch("routes.k8s.recovery._get_install_shape", return_value="unknown"),
        ):
            is_stale, detail, status = _check_cwc_cert_stale(api_client)

        assert is_stale is False
        assert status == "unknown"
        assert "setup may not have run yet" in detail


class TestFloNotPresentMessaging:
    """FLO restart should read as informational (not a fault) when FLO is absent."""

    @patch("routes.k8s.recovery._detect_bnk_tenant_namespace", return_value="bnk-app1")
    @patch("routes.k8s.recovery._find_and_restart_pods")
    @patch("routes.k8s.recovery.k8s_client")
    @patch("routes.k8s.recovery.KubernetesService")
    def test_flo_not_present_message_is_informational(
        self,
        mock_k8s_svc,
        mock_k8s_client,
        mock_find_restart,
        _mock_detect_ns,
        client,
        operator_headers,
        all_test_users,
    ):
        mock_k8s_svc.return_value.get_cluster.return_value = MagicMock()
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()

        # Label-based lookup finds nothing
        mock_find_restart.return_value = {
            "component": "FLO Operator",
            "status": "not_found",
            "message": "No FLO Operator pods found (label: app.kubernetes.io/name=f5-lifecycle-operator)",
        }
        # Name-prefix fallback also finds nothing
        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.return_value.items = []
        mock_k8s_client.CoreV1Api.return_value = mock_core_v1

        resp = client.post(
            "/api/k8s/clusters/1/recovery/platform-restart",
            headers=operator_headers,
            json={"restart_controller": False, "restart_flo": True, "restart_tmm": False},
        )
        assert resp.status_code == 200
        data = resp.json()

        flo_result = data["restarted"][0]
        assert flo_result["status"] == "not_found"
        assert "expected on a direct-helm install" in flo_result["message"]
