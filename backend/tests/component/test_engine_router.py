"""
Tests for services.execution.engine_router — engine selection, circuit breaker,
and health caching.

Covers EngineRouter.get_engine_type, get_engine, circuit breaker functions,
health check caching, and engine status summary.
"""

import time
from unittest.mock import MagicMock, mock_open, patch

import pytest
import yaml

from services.execution.engine_router import (
    CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    EngineRouter,
    EngineUnavailableError,
    _circuit_breaker_state,
    _health_cache,
    _inject_aks_bearer_token,
    _inject_gke_bearer_token,
    check_engine_health_cached,
    get_circuit_breaker_state,
    get_engine_status_summary,
    invalidate_health_cache,
    is_circuit_tripped,
    record_engine_failure,
    record_engine_success,
)

# Stored kubeconfig with exec-based auth — what GKE/AKS clusters ship.
_EXEC_KUBECONFIG = """
apiVersion: v1
kind: Config
clusters:
  - name: target
    cluster:
      server: https://10.0.0.1
users:
  - name: target-user
    user:
      exec:
        command: some-auth-plugin
        args: ["get-token"]
contexts:
  - name: target
    context:
      cluster: target
      user: target-user
current-context: target
"""

_AKS_EXEC_KUBECONFIG = """
apiVersion: v1
kind: Config
clusters:
  - name: aks-target
    cluster:
      certificate-authority-data: ZmFrZS1jYQ==
      server: https://aks-target
users:
  - name: aks-user
    user:
      exec:
        command: kubelogin
        args: ["get-token"]
contexts:
  - name: aks-target
    context:
      cluster: aks-target
      user: aks-user
current-context: aks-target
"""


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Clear module-level circuit breaker and health cache between tests."""
    _circuit_breaker_state.clear()
    _health_cache.clear()
    yield
    _circuit_breaker_state.clear()
    _health_cache.clear()


# ── Circuit breaker ──────────────────────────────────────────────────────

class TestCircuitBreaker:
    def test_initial_state(self):
        state = get_circuit_breaker_state("test-engine")
        assert state["failures"] == 0
        assert state["tripped_at"] is None

    def test_not_tripped_initially(self):
        assert is_circuit_tripped("test-engine") is False

    def test_tripped_after_threshold_failures(self):
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            record_engine_failure("test-engine")
        assert is_circuit_tripped("test-engine") is True

    def test_success_resets_breaker(self):
        for _ in range(CIRCUIT_BREAKER_FAILURE_THRESHOLD):
            record_engine_failure("test-engine")
        assert is_circuit_tripped("test-engine") is True
        record_engine_success("test-engine")
        assert is_circuit_tripped("test-engine") is False
        assert get_circuit_breaker_state("test-engine")["failures"] == 0

    def test_single_failure_does_not_trip(self):
        record_engine_failure("test-engine")
        assert is_circuit_tripped("test-engine") is False


# ── Health check cache ───────────────────────────────────────────────────

class TestHealthCheckCache:
    def test_caches_healthy_result(self):
        engine = MagicMock()
        engine.health_check.return_value = True
        result = check_engine_health_cached("test-eng", engine)
        assert result is True
        # Second call should use cache (health_check not called again)
        engine.health_check.return_value = False
        result2 = check_engine_health_cached("test-eng", engine)
        assert result2 is True  # cached
        engine.health_check.assert_called_once()

    def test_caches_unhealthy_result(self):
        engine = MagicMock()
        engine.health_check.return_value = False
        result = check_engine_health_cached("bad-eng", engine)
        assert result is False

    def test_exception_returns_false(self):
        engine = MagicMock()
        engine.health_check.side_effect = RuntimeError("boom")
        result = check_engine_health_cached("err-eng", engine)
        assert result is False

    def test_invalidate_specific_engine(self):
        _health_cache["eng-a"] = {"healthy": True, "checked_at": time.monotonic()}
        _health_cache["eng-b"] = {"healthy": False, "checked_at": time.monotonic()}
        invalidate_health_cache("eng-a")
        assert "eng-a" not in _health_cache
        assert "eng-b" in _health_cache

    def test_invalidate_all(self):
        _health_cache["eng-a"] = {"healthy": True, "checked_at": time.monotonic()}
        invalidate_health_cache(None)
        assert len(_health_cache) == 0


# ── Engine status summary ────────────────────────────────────────────────

class TestEngineStatusSummary:
    def test_empty_when_no_engines(self):
        assert get_engine_status_summary() == {}

    def test_reports_failure_count(self):
        record_engine_failure("my-engine")
        summary = get_engine_status_summary()
        assert "my-engine" in summary
        assert summary["my-engine"]["failures"] == 1
        assert summary["my-engine"]["tripped"] is False


# ── EngineRouter.get_engine_type ─────────────────────────────────────────

class TestEngineRouterGetEngineType:
    def test_opentofu_default(self):
        router = EngineRouter()
        assert router.get_engine_type("infra/aws/vpc") == "opentofu"

    def test_kubernetes_when_k8s_execution_engine_and_kubeconfig(self):
        router = EngineRouter(kubeconfig_path="/tmp/kube")
        assert router.get_engine_type("k8s/bnk-gateway", execution_engine="kubernetes-direct") == "kubernetes"

    @patch("services.execution.engine_router.ServiceRegistry")
    def test_operator_preferred_over_kubernetes(self, mock_registry_cls):
        svc = mock_registry_cls.get.return_value
        svc.operator_connections.is_connected.return_value = True
        router = EngineRouter(kubeconfig_path="/tmp/kube", operator_id="op-1")
        assert router.get_engine_type("k8s/bnk-gateway", execution_engine="kubernetes-direct") == "operator"


class TestEngineRouterCompatibleEngines:
    @patch("services.execution.engine_router.ServiceRegistry")
    def test_get_compatible_engines_for_k8s_execution_engine(self, mock_registry_cls):
        mock_registry_cls.get.return_value.operator_connections.is_connected.return_value = True
        router = EngineRouter(kubeconfig_path="/tmp/kube", operator_id="op-1")

        engines = router._get_compatible_engines("k8s/mod", execution_engine="kubernetes-direct")
        engine_names = [name for name, _engine in engines]

        assert "operator" in engine_names
        assert "kubernetes" in engine_names


class TestEngineRouterGetEngine:
    @patch("services.execution.engine_router.check_engine_health_cached", return_value=True)
    @patch("services.execution.engine_router.ServiceRegistry")
    def test_get_engine_for_k8s_execution_engine_prefers_operator(
        self,
        mock_registry_cls,
        _mock_health,
    ):
        mock_registry_cls.get.return_value.operator_connections.is_connected.return_value = True
        router = EngineRouter(kubeconfig_path="/tmp/kube", operator_id="op-1")

        engine = router.get_engine("k8s/mod", execution_engine="kubernetes-direct")

        assert engine.__class__.__name__ == "OperatorEngine"


# ── GKE bearer token injection (#218) ────────────────────────────────────

class TestInjectGkeBearerToken:
    def _cluster(self):
        cluster = MagicMock()
        cluster.name = "gke-cluster"
        cluster.cloud_provider = "gke"
        return cluster

    @patch("services.credentials_service.get_gcp_service_account_info")
    def test_rewrites_user_to_minted_token(self, mock_sa_info):
        mock_sa_info.return_value = {"type": "service_account", "project_id": "p"}
        project = MagicMock()

        fake_creds = MagicMock()
        fake_creds.token = "ya29.fake-gcp-token"

        with patch(
            "google.oauth2.service_account.Credentials.from_service_account_info",
            return_value=fake_creds,
        ):
            result = _inject_gke_bearer_token(
                _EXEC_KUBECONFIG, self._cluster(), project, db=MagicMock(),
            )

        parsed = yaml.safe_load(result)
        for user in parsed["users"]:
            assert user["user"] == {"token": "ya29.fake-gcp-token"}
            assert "exec" not in user["user"]
        fake_creds.refresh.assert_called_once()

    @patch("services.credentials_service.get_gcp_service_account_info", return_value=None)
    def test_graceful_fallback_when_no_credentials(self, _mock_sa_info):
        result = _inject_gke_bearer_token(
            _EXEC_KUBECONFIG, self._cluster(), MagicMock(), db=MagicMock(),
        )
        # Unmodified — exec auth preserved.
        assert result == _EXEC_KUBECONFIG

    @patch("services.credentials_service.get_gcp_service_account_info")
    def test_graceful_fallback_when_mint_raises(self, mock_sa_info):
        mock_sa_info.return_value = {"type": "service_account"}
        with patch(
            "google.oauth2.service_account.Credentials.from_service_account_info",
            side_effect=ValueError("bad key"),
        ):
            result = _inject_gke_bearer_token(
                _EXEC_KUBECONFIG, self._cluster(), MagicMock(), db=MagicMock(),
            )
        assert result == _EXEC_KUBECONFIG


# ── AKS bearer token injection (#218) ────────────────────────────────────

class TestInjectAksBearerToken:
    def _cluster(self):
        cluster = MagicMock()
        cluster.name = "aks-cluster"
        cluster.cloud_provider = "aks"
        return cluster

    def _project_with_template(self, template):
        project = MagicMock()
        project.credential_template_id = 5
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = template
        return project, db

    def _azure_template(self):
        template = MagicMock()
        template.provider = "azure"
        template.azure_tenant_id = "tenant-123"
        template.azure_credentials_encrypted = "enc"
        return template

    @patch("services.execution.engine_router.request_azure_oauth_token")
    @patch("services.execution.engine_router.decrypt_value")
    def test_rewrites_user_to_minted_token(self, mock_decrypt, mock_token_request):
        mock_decrypt.return_value = (
            '{"client_id": "cid", "client_secret": "csecret"}'
        )
        mock_token_request.return_value = {"access_token": "aad-fake-token"}

        project, db = self._project_with_template(self._azure_template())
        result = _inject_aks_bearer_token(_EXEC_KUBECONFIG, self._cluster(), project, db)

        parsed = yaml.safe_load(result)
        for user in parsed["users"]:
            assert user["user"] == {"token": "aad-fake-token"}
        # Token endpoint + scope are the AKS AAD server app default scope.
        kwargs = mock_token_request.call_args.kwargs
        assert kwargs["tenant_id"] == "tenant-123"
        assert kwargs["data"]["scope"].endswith("/.default")

    def test_graceful_fallback_when_no_template(self):
        project = MagicMock()
        project.credential_template_id = None
        result = _inject_aks_bearer_token(
            _EXEC_KUBECONFIG, self._cluster(), project, db=MagicMock(),
        )
        assert result == _EXEC_KUBECONFIG

    @patch("services.execution.engine_router.request_azure_oauth_token")
    @patch("services.execution.engine_router.decrypt_value")
    def test_graceful_fallback_when_post_raises(self, mock_decrypt, mock_token_request):
        mock_decrypt.return_value = '{"client_id": "cid", "client_secret": "csecret"}'
        mock_token_request.side_effect = RuntimeError("network down")

        project, db = self._project_with_template(self._azure_template())
        result = _inject_aks_bearer_token(_EXEC_KUBECONFIG, self._cluster(), project, db)
        assert result == _EXEC_KUBECONFIG


class TestResolveKubeconfigAks:
    @patch("services.execution.engine_router._inject_aks_bearer_token")
    @patch("services.execution.engine_router.decrypt_value")
    @patch("services.execution.engine_router.os.makedirs")
    @patch("builtins.open", new_callable=mock_open)
    def test_normalized_kubelogin_kubeconfig_reaches_aks_injection_path(
        self,
        _mock_open,
        _mock_makedirs,
        mock_decrypt,
        mock_inject_aks,
    ):
        # Stored kubeconfig uses kubelogin exec auth (AKS default).
        mock_decrypt.return_value = _AKS_EXEC_KUBECONFIG
        mock_inject_aks.return_value = "rewritten-kubeconfig"

        cluster = MagicMock()
        cluster.id = 11
        cluster.name = "aks-cluster"
        cluster.cloud_provider = "aks"
        cluster.kubeconfig_encrypted = "encrypted-kubeconfig"
        cluster.ssh_tunnel_enabled = False

        project = MagicMock()
        project.id = 99

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = cluster

        path = EngineRouter.resolve_kubeconfig(project, db)

        assert path == "/tmp/bnk-forge-kubeconfigs/project-99.yaml"
        mock_inject_aks.assert_called_once()
        _mock_open().write.assert_called_once_with("rewritten-kubeconfig")
