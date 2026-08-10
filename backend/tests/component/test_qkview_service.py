"""
Component tests for QKView service — CWC REST API integration.

All K8s API calls, kubernetes.stream, and KubernetesService are mocked.
Tests verify business logic: cert discovery, pod lifecycle, request parsing,
cert-manager setup orchestration, and error handling.

Test structure:
  1. Private helpers (pure / near-pure functions)
  2. Public methods: availability & setup
  3. Public methods: CRUD operations
"""

import base64
import io
import json
import tarfile
from unittest.mock import MagicMock, call, patch

import pytest
from kubernetes.client.rest import ApiException

from services.qkview_service import (
    CLIENT_CERT_CONFIG,
    CLUSTER_ISSUER_CANDIDATES,
    CWC_AUTH_TOKEN_SECRET,
    CWC_CERT_SECRET_CANDIDATES,
    CWC_CLIENT_CERT_NAME,
    CWC_CLIENT_CERT_SECRET,
    CWC_DEFAULT_NAMESPACE,
    CWC_LICENSE_CERTS_SECRET,
    CWC_REST_PORT_NAME,
    CWC_SERVER_CERT_NAME,
    CWC_SERVER_CERT_SECRET,
    CWC_SERVICE,
    FALLBACK_CERT_SECRET_CANDIDATES,
    QKViewError,
    _build_curl_command,
    _check_cert_manager_available,
    _check_curl_status,
    _cleanup_all_client_pods,
    _copy_cert_to_cwc_license_secret,
    _create_client_pod,
    _delete_client_pod,
    _detect_cluster_issuer,
    _detect_cwc_namespace,
    _find_cert_secret,
    _find_cwc_pod,
    _find_running_client_pod,
    _get_admin_token,
    _get_cwc_rest_port,
    _get_or_create_client_pod,
    _parse_curl_response,
    _restart_cwc_pod,
    _wait_for_secret,
    cancel_qkview,
    check_cwc_available,
    check_setup_status,
    cleanup_client_pods,
    create_qkview,
    delete_qkview,
    download_qkview,
    get_qkview,
    get_qkview_status,
    list_qkviews,
    setup_qkview_certs,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _api_exception(status=404, reason="Not Found"):
    """Create a Kubernetes ApiException."""
    return ApiException(status=status, reason=reason)


def _mock_service(ports=None, named_port=None):
    """Build a mock K8s Service with port spec."""
    svc = MagicMock()
    if ports is None and named_port is not None:
        port = MagicMock()
        port.name = CWC_REST_PORT_NAME
        port.port = named_port
        ports = [port]
    elif ports is None:
        port = MagicMock()
        port.name = "default"
        port.port = 38081
        ports = [port]
    svc.spec.ports = ports
    return svc


def _mock_secret(data=None, name="test-secret"):
    """Build a mock K8s Secret."""
    secret = MagicMock()
    secret.data = data
    secret.metadata.name = name
    return secret


def _mock_pod(name="test-pod", phase="Running", ready=True):
    """Build a mock K8s Pod.

    `ready=True` (default) also sets container_statuses=[ready] and a Ready=True
    condition, so the pod passes _restart_cwc_pod's readiness gate. Pass
    `ready=False` to construct a Running-but-not-Ready pod.
    """
    pod = MagicMock()
    pod.metadata.name = name
    pod.status.phase = phase
    container_status = MagicMock()
    container_status.ready = ready
    pod.status.container_statuses = [container_status]
    ready_condition = MagicMock()
    ready_condition.type = "Ready"
    ready_condition.status = "True" if ready else "False"
    pod.status.conditions = [ready_condition]
    return pod


def _mock_k8s_service():
    """Build a mock KubernetesService with standard cluster + kubeconfig."""
    k8s_svc = MagicMock()
    k8s_svc.get_cluster.return_value = MagicMock(id=1, name="test-cluster")
    k8s_svc.load_kubeconfig.return_value = MagicMock()  # api_client
    return k8s_svc


def _b64(value: str) -> str:
    """Base64-encode a string (like K8s secret data)."""
    return base64.b64encode(value.encode()).decode()


# ═══════════════════════════════════════════════════════════════════════
# 1. PRIVATE HELPERS
# ═══════════════════════════════════════════════════════════════════════


class TestDetectCwcNamespace:
    """Test _detect_cwc_namespace — auto-detect CWC service namespace."""

    @patch("services.qkview_service.k8s_client")
    def test_finds_service_in_default(self, mock_k8s):
        """Should return 'default' when CWC is only in default namespace."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException

        # Fail for f5-bnk, f5-operator, f5-utils; succeed for default
        def side_effect(name, ns):
            if ns == "default":
                return MagicMock()
            raise _api_exception(404)

        v1.read_namespaced_service.side_effect = side_effect

        result = _detect_cwc_namespace(MagicMock())
        assert result == "default"

    @patch("services.qkview_service.k8s_client")
    def test_finds_service_in_f5_utils(self, mock_k8s):
        """Should return 'f5-utils' when CWC is in f5-utils namespace."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException

        def side_effect(name, ns):
            if ns == "f5-utils":
                return MagicMock()
            raise _api_exception(404)

        v1.read_namespaced_service.side_effect = side_effect

        result = _detect_cwc_namespace(MagicMock())
        assert result == "f5-utils"

    @patch("services.qkview_service.k8s_client")
    def test_returns_first_found(self, mock_k8s):
        """Should return the first namespace where CWC is found."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException

        # CWC exists in both f5-bnk and f5-utils — should return f5-bnk (first scanned)
        def side_effect(name, ns):
            if ns in ("f5-bnk", "f5-utils"):
                return MagicMock()
            raise _api_exception(404)

        v1.read_namespaced_service.side_effect = side_effect

        result = _detect_cwc_namespace(MagicMock())
        assert result == "f5-bnk"

    @patch("services.qkview_service.k8s_client")
    def test_falls_back_to_default_namespace(self, mock_k8s):
        """Should fall back to CWC_DEFAULT_NAMESPACE when not found anywhere."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        v1.read_namespaced_service.side_effect = _api_exception(404)
        # Phase 2 cluster-wide sweep must also return empty for fallback to kick in
        v1.list_service_for_all_namespaces.return_value = MagicMock(items=[])

        result = _detect_cwc_namespace(MagicMock())
        assert result == CWC_DEFAULT_NAMESPACE


class TestGetCwcRestPort:
    """Test _get_cwc_rest_port — auto-discover CWC REST API port."""

    @patch("services.qkview_service.k8s_client")
    def test_returns_named_port(self, mock_k8s):
        """Should return the port named 'cwc-rest'."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        v1.read_namespaced_service.return_value = _mock_service(named_port=38081)

        result = _get_cwc_rest_port(MagicMock(), CWC_DEFAULT_NAMESPACE)

        assert result == 38081
        v1.read_namespaced_service.assert_called_once_with(CWC_SERVICE, CWC_DEFAULT_NAMESPACE)

    @patch("services.qkview_service.k8s_client")
    def test_falls_back_to_first_port(self, mock_k8s):
        """Should fall back to first port when named port not found."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        port = MagicMock()
        port.name = "other-port"
        port.port = 19891

        svc = MagicMock()
        svc.spec.ports = [port]
        v1.read_namespaced_service.return_value = svc

        result = _get_cwc_rest_port(MagicMock(), CWC_DEFAULT_NAMESPACE)
        assert result == 19891

    @patch("services.qkview_service.k8s_client")
    def test_service_not_found_raises(self, mock_k8s):
        """Should raise QKViewError when service not found."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        v1.read_namespaced_service.side_effect = _api_exception(404, "Not Found")

        with pytest.raises(QKViewError, match="not found"):
            _get_cwc_rest_port(MagicMock(), CWC_DEFAULT_NAMESPACE)

    @patch("services.qkview_service.k8s_client")
    def test_no_ports_raises(self, mock_k8s):
        """Should raise QKViewError when service has no ports."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        svc = MagicMock()
        svc.spec.ports = None
        v1.read_namespaced_service.return_value = svc

        with pytest.raises(QKViewError, match="no ports"):
            _get_cwc_rest_port(MagicMock(), CWC_DEFAULT_NAMESPACE)

    @patch("services.qkview_service.k8s_client")
    def test_empty_spec_raises(self, mock_k8s):
        """Should raise QKViewError when service spec is None."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        svc = MagicMock()
        svc.spec = None
        v1.read_namespaced_service.return_value = svc

        with pytest.raises(QKViewError, match="no ports"):
            _get_cwc_rest_port(MagicMock(), CWC_DEFAULT_NAMESPACE)


class TestFindCertSecret:
    """Test _find_cert_secret — try candidate secrets in order."""

    @patch("services.qkview_service.k8s_client")
    def test_returns_first_matching_candidate(self, mock_k8s):
        """Should return the first candidate whose secret has expected keys."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        # First candidate (our cert-manager client cert) exists
        client_secret = _mock_secret(
            data={"tls.crt": "cert", "tls.key": "key", "ca.crt": "ca"}
        )
        v1.read_namespaced_secret.return_value = client_secret

        result = _find_cert_secret(MagicMock(), CWC_DEFAULT_NAMESPACE)

        assert result["name"] == CLIENT_CERT_CONFIG["name"]

    @patch("services.qkview_service.k8s_client")
    def test_skips_missing_secrets(self, mock_k8s):
        """Should skip candidates that don't exist and return a later one."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException

        # First two candidates don't exist, third (cwc-license-certs) does
        def read_secret(name, namespace):
            if name == CWC_LICENSE_CERTS_SECRET:
                return _mock_secret(
                    data={"server-cert": "c", "server-key": "k", "ca-root-cert": "ca"}
                )
            raise _api_exception(404)

        v1.read_namespaced_secret.side_effect = read_secret

        result = _find_cert_secret(MagicMock(), CWC_DEFAULT_NAMESPACE)
        assert result["name"] == CWC_LICENSE_CERTS_SECRET

    @patch("services.qkview_service.k8s_client")
    def test_skips_secret_with_missing_keys(self, mock_k8s):
        """Should skip a secret that exists but lacks expected keys."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException

        call_count = [0]

        def read_secret(name, namespace):
            call_count[0] += 1
            if name == CLIENT_CERT_CONFIG["name"]:
                # Has data but wrong keys
                return _mock_secret(data={"wrong-key": "value"})
            if name == CWC_LICENSE_CERTS_SECRET:
                return _mock_secret(
                    data={"server-cert": "c", "server-key": "k", "ca-root-cert": "ca"}
                )
            raise _api_exception(404)

        v1.read_namespaced_secret.side_effect = read_secret

        result = _find_cert_secret(MagicMock(), CWC_DEFAULT_NAMESPACE)
        assert result["name"] == CWC_LICENSE_CERTS_SECRET

    @patch("services.qkview_service.k8s_client")
    def test_none_found_raises(self, mock_k8s):
        """Should raise QKViewError when no candidate secrets are found."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        v1.read_namespaced_secret.side_effect = _api_exception(404)

        with pytest.raises(QKViewError, match="No CWC mTLS certificates found"):
            _find_cert_secret(MagicMock(), CWC_DEFAULT_NAMESPACE)


class TestGetAdminToken:
    """Test _get_admin_token — read Bearer token from K8s secret."""

    @patch("services.qkview_service.k8s_client")
    def test_returns_decoded_token(self, mock_k8s):
        """Should decode base64 token from secret data."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        token = "my-admin-token"
        v1.read_namespaced_secret.return_value = _mock_secret(
            data={"token": _b64(token)}
        )

        result = _get_admin_token(MagicMock(), CWC_DEFAULT_NAMESPACE)
        assert result == token

    @patch("services.qkview_service.k8s_client")
    def test_returns_none_when_secret_missing(self, mock_k8s):
        """Should return None when auth token secret doesn't exist."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        v1.read_namespaced_secret.side_effect = _api_exception(404)

        result = _get_admin_token(MagicMock(), CWC_DEFAULT_NAMESPACE)
        assert result is None

    @patch("services.qkview_service.k8s_client")
    def test_returns_none_when_no_token_key(self, mock_k8s):
        """Should return None when secret exists but has no 'token' key."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        v1.read_namespaced_secret.return_value = _mock_secret(data={"other": "val"})

        result = _get_admin_token(MagicMock(), CWC_DEFAULT_NAMESPACE)
        assert result is None


class TestBuildCurlCommand:
    """Test _build_curl_command — construct curl with mTLS + Bearer."""

    def test_basic_get(self):
        """Should build a basic GET with cert/key paths."""
        cmd = _build_curl_command(
            CLIENT_CERT_CONFIG,
            "https://host:38081/v1/qkview",
            "GET",
        )
        assert "--cert /certs/tls.crt" in cmd
        assert "--key /certs/tls.key" in cmd
        assert "-X GET" in cmd
        assert "https://host:38081/v1/qkview" in cmd
        assert "HTTP_STATUS" in cmd

    def test_post_with_body(self):
        """Should include -d and Content-Type for POST with body."""
        body = {"namespace": "default"}
        cmd = _build_curl_command(
            CLIENT_CERT_CONFIG,
            "https://host:38081/v1/qkview",
            "POST",
            body=body,
        )
        assert "-X POST" in cmd
        assert "Content-Type: application/json" in cmd
        assert json.dumps(body) in cmd

    def test_bearer_token(self):
        """Should include Authorization header when token provided."""
        cmd = _build_curl_command(
            CLIENT_CERT_CONFIG,
            "https://host:38081/v1/qkview",
            "GET",
            bearer_token="my-token",
        )
        assert "Authorization: Bearer my-token" in cmd

    def test_output_file(self):
        """Should include -o when output_file provided."""
        cmd = _build_curl_command(
            CLIENT_CERT_CONFIG,
            "https://host:38081/v1/qkview/123/download",
            "GET",
            output_file="/tmp/download.bin",
        )
        assert "-o /tmp/download.bin" in cmd

    def test_no_bearer_no_body(self):
        """Should not include auth or body args when not provided."""
        cmd = _build_curl_command(
            CLIENT_CERT_CONFIG,
            "https://host/test",
            "DELETE",
        )
        assert "Authorization" not in cmd
        assert "Content-Type" not in cmd
        assert "-d" not in cmd


class TestCheckCurlStatus:
    """Test _check_curl_status — extract HTTP status from curl output."""

    def test_200_no_error(self):
        """Should not raise for HTTP 200."""
        response = '{"ok": true}\n---HTTP_STATUS:200---'
        _check_curl_status(response, "/v1/qkview")

    def test_400_raises(self):
        """Should raise QKViewError for HTTP 4xx."""
        response = 'unknown field\n---HTTP_STATUS:400---'
        with pytest.raises(QKViewError, match="400") as exc_info:
            _check_curl_status(response, "/v1/qkview")
        assert exc_info.value.status_code == 400

    def test_500_raises(self):
        """Should raise QKViewError for HTTP 5xx."""
        response = 'internal error\n---HTTP_STATUS:500---'
        with pytest.raises(QKViewError, match="500"):
            _check_curl_status(response, "/v1/qkview")

    def test_curl_connection_failure(self):
        """Should raise when curl itself fails (no HTTP status marker)."""
        response = "curl: (7) Failed to connect to host port 38081: Connection refused"
        with pytest.raises(QKViewError, match="connection failed"):
            _check_curl_status(response, "/v1/qkview")

    def test_no_marker_non_curl_raises(self):
        """No HTTP_STATUS marker and no curl: prefix must raise — request did not complete (D-017)."""
        response = "some random output"
        with pytest.raises(QKViewError, match="did not complete"):
            _check_curl_status(response, "/v1/qkview")


class TestParseCurlResponse:
    """Test _parse_curl_response — parse curl output with embedded HTTP status."""

    def test_json_object(self):
        """Should parse JSON object body with 200 status."""
        response = '{"id": "abc123"}\n---HTTP_STATUS:200---'
        result = _parse_curl_response(response, "/v1/qkview")
        assert result == {"id": "abc123"}

    def test_json_array_wrapped(self):
        """Should wrap JSON array in {'items': [...]}."""
        response = '[{"id": "1"}, {"id": "2"}]\n---HTTP_STATUS:200---'
        result = _parse_curl_response(response, "/v1/qkview")
        assert result == {"items": [{"id": "1"}, {"id": "2"}]}

    def test_empty_body(self):
        """Should return empty dict for empty body."""
        response = '\n---HTTP_STATUS:200---'
        result = _parse_curl_response(response, "/v1/qkview")
        assert result == {}

    def test_non_json_body(self):
        """Should return {'raw': body} for non-JSON body."""
        response = 'Hello World\n---HTTP_STATUS:200---'
        result = _parse_curl_response(response, "/v1/qkview")
        assert result == {"raw": "Hello World"}

    def test_4xx_raises(self):
        """Should raise QKViewError for 4xx status."""
        response = '{"error": "not found"}\n---HTTP_STATUS:404---'
        with pytest.raises(QKViewError, match="404"):
            _parse_curl_response(response, "/v1/qkview")

    def test_5xx_raises(self):
        """Should raise QKViewError for 5xx status."""
        response = 'server error\n---HTTP_STATUS:503---'
        with pytest.raises(QKViewError, match="503"):
            _parse_curl_response(response, "/v1/qkview")

    def test_curl_failure_raises(self):
        """Should raise QKViewError for curl connection failure."""
        response = "curl: (7) Failed to connect to host"
        with pytest.raises(QKViewError, match="connection failed"):
            _parse_curl_response(response, "/v1/qkview")

    def test_no_marker_raises_even_with_json_body(self):
        """JSON body without HTTP_STATUS marker must raise — curl did not complete a request (D-017)."""
        response = '{"id": "abc"}'
        with pytest.raises(QKViewError, match="did not complete"):
            _parse_curl_response(response, "/v1/qkview")


class TestFindRunningClientPod:
    """Test _find_running_client_pod — find existing running pod."""

    @patch("services.qkview_service.k8s_client")
    def test_returns_running_pod(self, mock_k8s):
        """Should return name of a running pod with matching cert label."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        pod = _mock_pod("qkview-client-abc", "Running")
        v1.list_namespaced_pod.return_value = MagicMock(items=[pod])

        result = _find_running_client_pod(MagicMock(), "test-cert", CWC_DEFAULT_NAMESPACE)
        assert result == "qkview-client-abc"

    @patch("services.qkview_service.k8s_client")
    def test_skips_non_running(self, mock_k8s):
        """Should skip pods that aren't Running."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        pod = _mock_pod("qkview-client-abc", "Pending")
        v1.list_namespaced_pod.return_value = MagicMock(items=[pod])

        result = _find_running_client_pod(MagicMock(), "test-cert", CWC_DEFAULT_NAMESPACE)
        assert result is None

    @patch("services.qkview_service.k8s_client")
    def test_returns_none_on_empty_list(self, mock_k8s):
        """Should return None when no pods found."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        v1.list_namespaced_pod.return_value = MagicMock(items=[])

        result = _find_running_client_pod(MagicMock(), "test-cert", CWC_DEFAULT_NAMESPACE)
        assert result is None

    @patch("services.qkview_service.k8s_client")
    def test_returns_none_on_exception(self, mock_k8s):
        """Should return None (not raise) on API error."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        v1.list_namespaced_pod.side_effect = Exception("connection error")

        result = _find_running_client_pod(MagicMock(), "test-cert", CWC_DEFAULT_NAMESPACE)
        assert result is None


class TestCreateClientPod:
    """Test _create_client_pod — create ephemeral pod and wait for ready."""

    @patch("services.qkview_service.time")
    @patch("services.qkview_service.uuid")
    @patch("services.qkview_service.k8s_client")
    def test_creates_and_waits_for_running(self, mock_k8s, mock_uuid, mock_time):
        """Should create a pod and wait until it's Running."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        mock_uuid.uuid4.return_value = MagicMock(hex="abcd1234xxxxxx")

        # Pod becomes Running on second check
        mock_time.time.side_effect = [0, 1, 2]  # start, first check, second check
        v1.read_namespaced_pod.side_effect = [
            _mock_pod(phase="Pending"),
            _mock_pod(phase="Running"),
        ]

        result = _create_client_pod(MagicMock(), CLIENT_CERT_CONFIG, CWC_DEFAULT_NAMESPACE)
        assert "bnk-forge-agent-abcd1234" in result
        v1.create_namespaced_pod.assert_called_once()

    @patch("services.qkview_service.time")
    @patch("services.qkview_service.uuid")
    @patch("services.qkview_service.k8s_client")
    def test_failed_pod_raises(self, mock_k8s, mock_uuid, mock_time):
        """Should raise QKViewError when pod enters Failed phase."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        mock_uuid.uuid4.return_value = MagicMock(hex="abcd1234xxxxxx")
        mock_time.time.side_effect = [0, 1]

        v1.read_namespaced_pod.return_value = _mock_pod(phase="Failed")

        with pytest.raises(QKViewError, match="failed to start"):
            _create_client_pod(MagicMock(), CLIENT_CERT_CONFIG, CWC_DEFAULT_NAMESPACE)

    @patch("services.qkview_service._delete_client_pod")
    @patch("services.qkview_service.time")
    @patch("services.qkview_service.uuid")
    @patch("services.qkview_service.k8s_client")
    def test_timeout_cleans_up(self, mock_k8s, mock_uuid, mock_time, mock_delete):
        """Should clean up pod and raise on timeout."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        mock_uuid.uuid4.return_value = MagicMock(hex="abcd1234xxxxxx")
        # Simulate time passing beyond deadline
        mock_time.time.side_effect = [0, 200]

        with pytest.raises(QKViewError, match="did not become ready"):
            _create_client_pod(MagicMock(), CLIENT_CERT_CONFIG, CWC_DEFAULT_NAMESPACE)

        mock_delete.assert_called_once()

    @patch("services.qkview_service.k8s_client")
    @patch("services.qkview_service.uuid")
    def test_create_api_error_raises(self, mock_uuid, mock_k8s):
        """Should raise QKViewError when pod creation fails."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        mock_uuid.uuid4.return_value = MagicMock(hex="abcd1234xxxxxx")
        v1.create_namespaced_pod.side_effect = _api_exception(403, "Forbidden")

        with pytest.raises(QKViewError, match="Failed to create"):
            _create_client_pod(MagicMock(), CLIENT_CERT_CONFIG, CWC_DEFAULT_NAMESPACE)


class TestDeleteClientPod:
    """Test _delete_client_pod — best-effort pod deletion."""

    @patch("services.qkview_service.k8s_client")
    def test_deletes_pod(self, mock_k8s):
        """Should delete the pod without raising."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        _delete_client_pod(MagicMock(), "test-pod", CWC_DEFAULT_NAMESPACE)
        v1.delete_namespaced_pod.assert_called_once()

    @patch("services.qkview_service.k8s_client")
    def test_never_raises(self, mock_k8s):
        """Should not raise even if delete fails."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        v1.delete_namespaced_pod.side_effect = Exception("delete failed")

        # Should not raise
        _delete_client_pod(MagicMock(), "test-pod", CWC_DEFAULT_NAMESPACE)


class TestCleanupAllClientPods:
    """Test _cleanup_all_client_pods — delete all client pods."""

    @patch("services.qkview_service._delete_client_pod")
    @patch("services.qkview_service.k8s_client")
    def test_deletes_all_pods(self, mock_k8s, mock_delete):
        """Should delete each client pod found across both label selectors.

        The service searches both 'bnk-forge-agent' (current) and
        'bnk-forge-qkview-client' (legacy) labels for backward compatibility.
        With 2 pods returned per label, expect 4 total deletes.
        """
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        pod1 = _mock_pod("client-1")
        pod2 = _mock_pod("client-2")
        v1.list_namespaced_pod.return_value = MagicMock(items=[pod1, pod2])

        api_client = MagicMock()
        _cleanup_all_client_pods(api_client)

        # 2 labels × 2 pods each = 4 delete calls
        assert mock_delete.call_count == 4

    @patch("services.qkview_service._delete_client_pod")
    @patch("services.qkview_service.k8s_client")
    def test_handles_list_failure(self, mock_k8s, mock_delete):
        """Should not raise when listing fails."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        v1.list_namespaced_pod.side_effect = Exception("connection error")

        # Should not raise
        _cleanup_all_client_pods(MagicMock())
        mock_delete.assert_not_called()


class TestGetOrCreateClientPod:
    """Test _get_or_create_client_pod — reuse existing or create new."""

    @patch("services.qkview_service._create_client_pod")
    @patch("services.qkview_service._find_running_client_pod")
    def test_reuses_existing(self, mock_find, mock_create):
        """Should reuse existing pod if one is found."""
        mock_find.return_value = "existing-pod"

        result = _get_or_create_client_pod(MagicMock(), CLIENT_CERT_CONFIG, CWC_DEFAULT_NAMESPACE)

        assert result == "existing-pod"
        mock_create.assert_not_called()

    @patch("services.qkview_service._create_client_pod")
    @patch("services.qkview_service._find_running_client_pod")
    def test_creates_when_none_found(self, mock_find, mock_create):
        """Should create new pod when no existing one is found."""
        mock_find.return_value = None
        mock_create.return_value = "new-pod"

        result = _get_or_create_client_pod(MagicMock(), CLIENT_CERT_CONFIG, CWC_DEFAULT_NAMESPACE)

        assert result == "new-pod"
        mock_create.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# cert-manager helpers
# ═══════════════════════════════════════════════════════════════════════


class TestDetectClusterIssuer:
    """Test _detect_cluster_issuer — auto-detect cert-manager ClusterIssuer."""

    @patch("services.qkview_service.k8s_client")
    def test_finds_first_candidate(self, mock_k8s):
        """Should return first candidate when it exists."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        custom_api.get_cluster_custom_object.return_value = {"kind": "ClusterIssuer"}

        result = _detect_cluster_issuer(MagicMock())
        assert result == CLUSTER_ISSUER_CANDIDATES[0]

    @patch("services.qkview_service.k8s_client")
    def test_finds_second_candidate(self, mock_k8s):
        """Should return second candidate when first is missing."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        def side_effect(group, version, plural, name):
            if name == CLUSTER_ISSUER_CANDIDATES[1]:
                return {"kind": "ClusterIssuer"}
            raise _api_exception(404)

        custom_api.get_cluster_custom_object.side_effect = side_effect

        result = _detect_cluster_issuer(MagicMock())
        assert result == CLUSTER_ISSUER_CANDIDATES[1]

    @staticmethod
    def _ready_issuer(name: str) -> dict:
        return {
            "metadata": {"name": name},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }

    @staticmethod
    def _not_ready_issuer(name: str) -> dict:
        return {
            "metadata": {"name": name},
            "status": {"conditions": [{"type": "Ready", "status": "False"}]},
        }

    @patch("services.qkview_service.k8s_client")
    def test_pattern_fallback(self, mock_k8s):
        """Should match a Ready, name-relevant issuer when no known candidate exists."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        # Fast path: all candidates fail
        custom_api.get_cluster_custom_object.side_effect = _api_exception(404)
        # Slow path: list returns a custom Ready issuer matching a name token
        custom_api.list_cluster_custom_object.return_value = {
            "items": [
                self._ready_issuer("custom-ca-cluster-issuer"),
                self._ready_issuer("unrelated-issuer"),
            ]
        }

        result = _detect_cluster_issuer(MagicMock())
        assert result == "custom-ca-cluster-issuer"

    @patch("services.qkview_service.k8s_client")
    def test_returns_none_when_nothing_found(self, mock_k8s):
        """Should return None when no suitable issuer exists."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        # Fast path: all candidates fail
        custom_api.get_cluster_custom_object.side_effect = _api_exception(404)
        # Slow path: only issuer present is not Ready
        custom_api.list_cluster_custom_object.return_value = {
            "items": [self._not_ready_issuer("unrelated-issuer")]
        }

        result = _detect_cluster_issuer(MagicMock())
        assert result is None

    @patch("services.qkview_service.k8s_client")
    def test_finds_f5_cne_internal_ca_over_selfsigned(self, mock_k8s):
        """Live-cluster shape: f5-cne-internal-ca (Ready) + selfsigned-cluster-issuer (Ready).

        Only the BNK-relevant issuer should be selected; the bootstrap
        selfsigned issuer must never be auto-chosen.
        """
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        # Fast path: none of the known candidates exist by this literal name
        custom_api.get_cluster_custom_object.side_effect = _api_exception(404)
        custom_api.list_cluster_custom_object.return_value = {
            "items": [
                self._ready_issuer("f5-cne-internal-ca"),
                self._ready_issuer("selfsigned-cluster-issuer"),
            ]
        }

        result = _detect_cluster_issuer(MagicMock())
        assert result == "f5-cne-internal-ca"

    @patch("services.qkview_service.k8s_client")
    def test_bnk_candidate_still_found_via_fast_path(self, mock_k8s):
        """Regression: bnk-ca-cluster-issuer still resolves via the fast path."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        custom_api.get_cluster_custom_object.return_value = {"kind": "ClusterIssuer"}

        result = _detect_cluster_issuer(MagicMock())
        assert result == "bnk-ca-cluster-issuer"

    @patch("services.qkview_service.k8s_client")
    def test_only_selfsigned_returns_none(self, mock_k8s):
        """A cluster with only a Ready selfsigned bootstrap issuer yields None."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        custom_api.get_cluster_custom_object.side_effect = _api_exception(404)
        custom_api.list_cluster_custom_object.return_value = {
            "items": [self._ready_issuer("selfsigned-cluster-issuer")]
        }

        result = _detect_cluster_issuer(MagicMock())
        assert result is None

    @patch("services.qkview_service.k8s_client")
    def test_unrelated_ready_issuer_not_selected_fails_closed(self, mock_k8s):
        """A single Ready, non-selfsigned issuer whose name contains a bare
        '-ca' token but is NOT actually BNK/CNE/F5-related must NOT be
        auto-selected — fail closed (return None) rather than risk issuing
        CWC mTLS certs from a foreign CA."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        custom_api.get_cluster_custom_object.side_effect = _api_exception(404)
        custom_api.list_cluster_custom_object.return_value = {
            "items": [self._ready_issuer("my-ca")]
        }

        result = _detect_cluster_issuer(MagicMock())
        assert result is None

    @patch("services.qkview_service.k8s_client")
    def test_sole_ready_issuer_with_no_name_token_fails_closed(self, mock_k8s):
        """A single Ready, non-selfsigned issuer with NO BNK/CNE/F5-ish name
        token at all must NOT be auto-selected — fail closed (return None)."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        custom_api.get_cluster_custom_object.side_effect = _api_exception(404)
        custom_api.list_cluster_custom_object.return_value = {
            "items": [self._ready_issuer("prod-issuer-42")]
        }

        result = _detect_cluster_issuer(MagicMock())
        assert result is None

    @patch("services.qkview_service.k8s_client")
    def test_unrelated_ready_issuers_no_bnk_token_fails_closed(self, mock_k8s):
        """A cluster whose only Ready issuers are generic (vault-issuer,
        istio-ca) — neither BNK/CNE/F5-named nor matching the
        '-ca-cluster-issuer' suffix — must return None."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        custom_api.get_cluster_custom_object.side_effect = _api_exception(404)
        custom_api.list_cluster_custom_object.return_value = {
            "items": [
                self._ready_issuer("vault-issuer"),
                self._ready_issuer("istio-ca"),
            ]
        }

        result = _detect_cluster_issuer(MagicMock())
        assert result is None

    @patch("services.qkview_service.k8s_client")
    def test_root_ca_bare_ca_token_no_longer_overmatches(self, mock_k8s):
        """A Ready 'root-ca' issuer contains a bare '-ca' substring but is not
        a real BNK issuer (no bnk/cne/f5 token, no '-ca-cluster-issuer'
        suffix) — must return None, proving the bare '-ca'/'ca-' tokens are
        no longer used for matching."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        custom_api.get_cluster_custom_object.side_effect = _api_exception(404)
        custom_api.list_cluster_custom_object.return_value = {
            "items": [self._ready_issuer("root-ca")]
        }

        result = _detect_cluster_issuer(MagicMock())
        assert result is None

    @patch("services.qkview_service.k8s_client")
    def test_returns_none_when_crd_not_installed(self, mock_k8s):
        """Should return None when cert-manager CRDs are not installed."""
        custom_api = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = custom_api
        mock_k8s.rest.ApiException = ApiException

        # Both fast path and slow path fail with 404 (CRD not installed)
        custom_api.get_cluster_custom_object.side_effect = _api_exception(404)
        custom_api.list_cluster_custom_object.side_effect = _api_exception(404)

        result = _detect_cluster_issuer(MagicMock())
        assert result is None


class TestCheckCertManagerAvailable:
    """Test _check_cert_manager_available — delegates to _detect_cluster_issuer."""

    @patch("services.qkview_service._detect_cluster_issuer")
    def test_returns_true_when_issuer_found(self, mock_detect):
        """Should return True when a ClusterIssuer is detected."""
        mock_detect.return_value = "bnk-ca-cluster-issuer"
        assert _check_cert_manager_available(MagicMock()) is True

    @patch("services.qkview_service._detect_cluster_issuer")
    def test_returns_false_when_no_issuer(self, mock_detect):
        """Should return False when no ClusterIssuer is found."""
        mock_detect.return_value = None
        assert _check_cert_manager_available(MagicMock()) is False


class TestWaitForSecret:
    """Test _wait_for_secret — poll for cert-manager secret issuance."""

    @patch("services.qkview_service.time")
    @patch("services.qkview_service.k8s_client")
    def test_returns_data_when_ready(self, mock_k8s, mock_time):
        """Should return secret data once all keys are present."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        expected_data = {"tls.crt": "cert", "tls.key": "key", "ca.crt": "ca"}
        v1.read_namespaced_secret.return_value = _mock_secret(data=expected_data)
        mock_time.time.side_effect = [0, 1]  # start, first check (within deadline)

        result = _wait_for_secret(MagicMock(), "test-secret", "ns")
        assert result == expected_data

    @patch("services.qkview_service.time")
    @patch("services.qkview_service.k8s_client")
    def test_times_out_raises(self, mock_k8s, mock_time):
        """Should raise QKViewError when secret isn't ready within timeout."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException

        # Secret never has the right keys
        v1.read_namespaced_secret.return_value = _mock_secret(data={"incomplete": "x"})
        mock_time.time.side_effect = [0, 100]  # past deadline

        with pytest.raises(QKViewError, match="Timed out"):
            _wait_for_secret(MagicMock(), "test-secret", "ns", timeout_sec=60)

    @patch("services.qkview_service.time")
    @patch("services.qkview_service.k8s_client")
    def test_retries_on_api_exception(self, mock_k8s, mock_time):
        """Should keep polling when read_namespaced_secret raises."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException

        expected_data = {"tls.crt": "cert", "tls.key": "key", "ca.crt": "ca"}
        v1.read_namespaced_secret.side_effect = [
            _api_exception(404),
            _mock_secret(data=expected_data),
        ]
        mock_time.time.side_effect = [0, 1, 2]

        result = _wait_for_secret(MagicMock(), "test-secret", "ns")
        assert result == expected_data


class TestCopyCertToCwcLicenseSecret:
    """Test _copy_cert_to_cwc_license_secret — patch/create cwc-license-certs."""

    @patch("services.qkview_service.k8s_client")
    def test_patches_existing_secret(self, mock_k8s):
        """Should patch existing secret with cert-manager data."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        cert_data = {"tls.crt": "cert", "tls.key": "key", "ca.crt": "ca"}
        _copy_cert_to_cwc_license_secret(MagicMock(), cert_data)

        v1.patch_namespaced_secret.assert_called_once_with(
            CWC_LICENSE_CERTS_SECRET,
            CWC_DEFAULT_NAMESPACE,
            body={"data": {
                "server-cert": "cert",
                "server-key": "key",
                "ca-root-cert": "ca",
            }},
        )

    @patch("services.qkview_service.k8s_client")
    def test_creates_when_not_found(self, mock_k8s):
        """Should create new secret when existing one not found (404)."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        v1.patch_namespaced_secret.side_effect = _api_exception(404)

        cert_data = {"tls.crt": "cert", "tls.key": "key", "ca.crt": "ca"}
        _copy_cert_to_cwc_license_secret(MagicMock(), cert_data)

        v1.create_namespaced_secret.assert_called_once()

    @patch("services.qkview_service.k8s_client")
    def test_non_404_raises(self, mock_k8s):
        """Should raise QKViewError for non-404 patch failures."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        v1.patch_namespaced_secret.side_effect = _api_exception(403, "Forbidden")

        with pytest.raises(QKViewError, match="Failed to update"):
            _copy_cert_to_cwc_license_secret(
                MagicMock(), {"tls.crt": "c", "tls.key": "k", "ca.crt": "ca"}
            )


class TestFindCwcPod:
    """Test _find_cwc_pod — multi-strategy CWC pod discovery."""

    @patch("services.qkview_service.k8s_client")
    def test_finds_by_first_label(self, mock_k8s):
        """Should find CWC pod via first label candidate (app=cwc)."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        cwc_pod = _mock_pod("f5-spk-cwc-abc", "Running")
        # First label_selector call returns the pod
        v1.list_namespaced_pod.return_value = MagicMock(items=[cwc_pod])

        name, label = _find_cwc_pod(v1, "f5-utils")
        assert name == "f5-spk-cwc-abc"
        assert label == "app=cwc"

    @patch("services.qkview_service.k8s_client")
    def test_finds_by_second_label(self, mock_k8s):
        """Should fall back to second label (app=f5-spk-cwc) when first misses."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        cwc_pod = _mock_pod("f5-spk-cwc-xyz", "Running")

        def side_effect(ns, label_selector=None):
            if label_selector == "app=f5-spk-cwc":
                return MagicMock(items=[cwc_pod])
            return MagicMock(items=[])

        v1.list_namespaced_pod.side_effect = side_effect

        name, label = _find_cwc_pod(v1, "f5-utils")
        assert name == "f5-spk-cwc-xyz"
        assert label == "app=f5-spk-cwc"

    @patch("services.qkview_service.k8s_client")
    def test_falls_back_to_name_prefix(self, mock_k8s):
        """Should find CWC pod by name prefix when no labels match."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        cwc_pod = _mock_pod("f5-spk-cwc-nolabel-123", "Running")
        other_pod = _mock_pod("some-other-pod", "Running")

        def side_effect(ns, label_selector=None):
            if label_selector:
                return MagicMock(items=[])  # No label matches
            return MagicMock(items=[other_pod, cwc_pod])  # Full pod list

        v1.list_namespaced_pod.side_effect = side_effect

        name, label = _find_cwc_pod(v1, "f5-utils")
        assert name == "f5-spk-cwc-nolabel-123"
        assert label is None  # Found by name, not label

    @patch("services.qkview_service.k8s_client")
    def test_returns_none_when_not_found(self, mock_k8s):
        """Should return (None, None) when CWC pod is not found anywhere."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        other_pod = _mock_pod("some-other-pod", "Running")

        def side_effect(ns, label_selector=None):
            if label_selector:
                return MagicMock(items=[])  # No label matches
            return MagicMock(items=[other_pod])  # Only non-CWC pods

        v1.list_namespaced_pod.side_effect = side_effect

        name, label = _find_cwc_pod(v1, "f5-utils")
        assert name is None
        assert label is None

    @patch("services.qkview_service.k8s_client")
    def test_returns_none_when_namespace_empty(self, mock_k8s):
        """Should return (None, None) when namespace has no pods."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        v1.list_namespaced_pod.return_value = MagicMock(items=[])

        name, label = _find_cwc_pod(v1, "f5-utils")
        assert name is None
        assert label is None


class TestRestartCwcPod:
    """Test _restart_cwc_pod — delete CWC pod and wait for replacement."""

    @patch("services.qkview_service.time")
    @patch("services.qkview_service.k8s_client")
    def test_deletes_and_waits_for_new_pod(self, mock_k8s, mock_time):
        """Should delete old pod and wait for new Running pod (label path)."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        old_pod = _mock_pod("f5-spk-cwc-old", "Running")
        new_pod = _mock_pod("f5-spk-cwc-new", "Running")

        # First list: find old pod via label. Second list: new pod is running.
        v1.list_namespaced_pod.side_effect = [
            MagicMock(items=[old_pod]),
            MagicMock(items=[new_pod]),
        ]
        mock_time.time.side_effect = [0, 1]

        result = _restart_cwc_pod(MagicMock())
        assert result == "f5-spk-cwc-old"
        v1.delete_namespaced_pod.assert_called_once()

    @patch("services.qkview_service.time")
    @patch("services.qkview_service.k8s_client")
    def test_restart_via_name_prefix_fallback(self, mock_k8s, mock_time):
        """Should restart CWC pod found by name prefix when labels miss."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1

        old_pod = _mock_pod("f5-spk-cwc-nolabel", "Running")
        new_pod = _mock_pod("f5-spk-cwc-nolabel-new", "Running")

        call_count = [0]

        def side_effect(ns, label_selector=None):
            call_count[0] += 1
            if label_selector:
                # Label lookups always empty
                return MagicMock(items=[])
            # Name prefix scan — first returns old, second returns new
            if call_count[0] <= 3:
                # 3rd call = _find_cwc_pod name prefix scan
                return MagicMock(items=[old_pod])
            # 4th+ call = wait loop name prefix scan
            return MagicMock(items=[new_pod])

        v1.list_namespaced_pod.side_effect = side_effect
        mock_time.time.side_effect = [0, 1]

        result = _restart_cwc_pod(MagicMock())
        assert result == "f5-spk-cwc-nolabel"
        v1.delete_namespaced_pod.assert_called_once()

    @patch("services.qkview_service.k8s_client")
    def test_no_cwc_pod_raises(self, mock_k8s):
        """Should raise when no CWC pod is found by any strategy."""
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        other_pod = _mock_pod("some-other-pod", "Running")

        def side_effect(ns, label_selector=None):
            if label_selector:
                return MagicMock(items=[])
            return MagicMock(items=[other_pod])

        v1.list_namespaced_pod.side_effect = side_effect

        with pytest.raises(QKViewError, match="No CWC pod found"):
            _restart_cwc_pod(MagicMock())


# ═══════════════════════════════════════════════════════════════════════
# 2. PUBLIC METHODS: AVAILABILITY & SETUP
# ═══════════════════════════════════════════════════════════════════════


class TestCheckSetupStatus:
    """Test check_setup_status — cert-manager setup state for a cluster."""

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._secret_exists")
    @patch("services.qkview_service._check_cert_manager_available")
    def test_setup_complete(self, mock_cm, mock_secret, _mock_detect):
        """Should return setup_complete=True when both secrets exist."""
        k8s_svc = _mock_k8s_service()
        mock_cm.return_value = True
        mock_secret.return_value = True  # both server + client

        result = check_setup_status(k8s_svc, 1)

        assert result["setup_complete"] is True
        assert result["cert_manager_available"] is True
        assert result["server_cert_exists"] is True
        assert result["client_cert_exists"] is True

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._secret_exists")
    @patch("services.qkview_service._check_cert_manager_available")
    def test_not_setup_needs_cert_manager(self, mock_cm, mock_secret, _mock_detect):
        """Should report cert-manager missing."""
        k8s_svc = _mock_k8s_service()
        mock_cm.return_value = False
        mock_secret.return_value = False

        result = check_setup_status(k8s_svc, 1)

        assert result["setup_complete"] is False
        assert result["cert_manager_available"] is False
        assert "ClusterIssuer" in result["message"]

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._secret_exists")
    @patch("services.qkview_service._check_cert_manager_available")
    def test_partial_setup(self, mock_cm, mock_secret, _mock_detect):
        """Should report server cert exists but client cert missing."""
        k8s_svc = _mock_k8s_service()
        mock_cm.return_value = True
        mock_secret.side_effect = [True, False]  # server yes, client no

        result = check_setup_status(k8s_svc, 1)

        assert result["setup_complete"] is False
        assert result["server_cert_exists"] is True
        assert result["client_cert_exists"] is False
        assert "client cert is missing" in result["message"]

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._secret_exists")
    @patch("services.qkview_service._check_cert_manager_available")
    def test_needs_setup(self, mock_cm, mock_secret, _mock_detect):
        """Should report setup needed when cert-manager ok but no certs."""
        k8s_svc = _mock_k8s_service()
        mock_cm.return_value = True
        mock_secret.return_value = False

        result = check_setup_status(k8s_svc, 1)

        assert result["setup_complete"] is False
        assert "mTLS setup is required" in result["message"]

    def test_cluster_connection_failure(self):
        """Should return graceful error dict on connection failure."""
        k8s_svc = _mock_k8s_service()
        k8s_svc.get_cluster.side_effect = Exception("cluster not found")

        result = check_setup_status(k8s_svc, 999)

        assert result["setup_complete"] is False
        assert "Failed to connect" in result["message"]


class TestSetupQkviewCerts:
    """Test setup_qkview_certs — one-time cert-manager setup."""

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._cleanup_all_client_pods")
    @patch("services.qkview_service._wait_for_secret")
    @patch("services.qkview_service._restart_cwc_pod")
    @patch("services.qkview_service._copy_cert_to_cwc_license_secret")
    @patch("services.qkview_service._create_cert_manager_certificate")
    @patch("services.qkview_service._cert_exists")
    @patch("services.qkview_service._detect_cluster_issuer")
    def test_full_setup_from_scratch(
        self, mock_detect_issuer, mock_cert_exists, mock_create_cert,
        mock_copy_cert, mock_restart, mock_wait, mock_cleanup, _mock_detect_ns,
    ):
        """Should perform all steps when nothing is set up yet."""
        k8s_svc = _mock_k8s_service()
        mock_detect_issuer.return_value = "bnk-ca-cluster-issuer"
        mock_cert_exists.return_value = False  # neither server nor client cert exists
        mock_wait.return_value = {
            "tls.crt": "cert", "tls.key": "key", "ca.crt": "ca"
        }
        mock_restart.return_value = "cwc-old-pod"

        result = setup_qkview_certs(k8s_svc, 1)

        assert result["success"] is True
        assert len(result["steps"]) == 8
        assert mock_create_cert.call_count == 2  # server + client
        # Both cert creations should use the detected issuer
        for call_args in mock_create_cert.call_args_list:
            assert call_args.kwargs.get("issuer_name") == "bnk-ca-cluster-issuer"
        mock_copy_cert.assert_called_once()
        mock_restart.assert_called_once()
        mock_cleanup.assert_called_once()

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._cleanup_all_client_pods")
    @patch("services.qkview_service._wait_for_secret")
    @patch("services.qkview_service._restart_cwc_pod")
    @patch("services.qkview_service._copy_cert_to_cwc_license_secret")
    @patch("services.qkview_service._create_cert_manager_certificate")
    @patch("services.qkview_service._cert_exists")
    @patch("services.qkview_service._detect_cluster_issuer")
    def test_full_setup_with_arm_issuer(
        self, mock_detect_issuer, mock_cert_exists, mock_create_cert,
        mock_copy_cert, mock_restart, mock_wait, mock_cleanup, _mock_detect_ns,
    ):
        """Should use detected arm-ca-cluster-issuer for cert creation."""
        k8s_svc = _mock_k8s_service()
        mock_detect_issuer.return_value = "arm-ca-cluster-issuer"
        mock_cert_exists.return_value = False
        mock_wait.return_value = {
            "tls.crt": "cert", "tls.key": "key", "ca.crt": "ca"
        }
        mock_restart.return_value = "cwc-old-pod"

        result = setup_qkview_certs(k8s_svc, 1)

        assert result["success"] is True
        # Both certs use the detected arm issuer
        for call_args in mock_create_cert.call_args_list:
            assert call_args.kwargs.get("issuer_name") == "arm-ca-cluster-issuer"

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._cleanup_all_client_pods")
    @patch("services.qkview_service._wait_for_secret")
    @patch("services.qkview_service._restart_cwc_pod")
    @patch("services.qkview_service._copy_cert_to_cwc_license_secret")
    @patch("services.qkview_service._create_cert_manager_certificate")
    @patch("services.qkview_service._cert_exists")
    @patch("services.qkview_service._detect_cluster_issuer")
    def test_skips_existing_certs(
        self, mock_detect_issuer, mock_cert_exists, mock_create_cert,
        mock_copy_cert, mock_restart, mock_wait, mock_cleanup, _mock_detect_ns,
    ):
        """Should skip cert creation when both certs already exist."""
        k8s_svc = _mock_k8s_service()
        mock_detect_issuer.return_value = "bnk-ca-cluster-issuer"
        mock_cert_exists.return_value = True  # both exist
        mock_wait.return_value = {
            "tls.crt": "cert", "tls.key": "key", "ca.crt": "ca"
        }
        mock_restart.return_value = "cwc-old-pod"

        result = setup_qkview_certs(k8s_svc, 1)

        assert result["success"] is True
        mock_create_cert.assert_not_called()
        # Even when certs exist, we still copy/restart/wait
        steps = {s["step"]: s["status"] for s in result["steps"]}
        assert steps["server_cert_create"] == "skipped"
        assert steps["client_cert_create"] == "skipped"

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._detect_cluster_issuer")
    def test_no_cert_manager_raises(self, mock_detect_issuer, _mock_detect_ns):
        """Should raise QKViewError when no ClusterIssuer is found."""
        k8s_svc = _mock_k8s_service()
        mock_detect_issuer.return_value = None

        with pytest.raises(QKViewError, match="No CA ClusterIssuer found"):
            setup_qkview_certs(k8s_svc, 1)

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._wait_for_secret")
    @patch("services.qkview_service._create_cert_manager_certificate")
    @patch("services.qkview_service._cert_exists")
    @patch("services.qkview_service._detect_cluster_issuer")
    def test_server_cert_timeout_raises(
        self, mock_detect_issuer, mock_cert_exists, mock_create_cert, mock_wait, _mock_detect_ns,
    ):
        """Should raise when server cert issuance times out."""
        k8s_svc = _mock_k8s_service()
        mock_detect_issuer.return_value = "bnk-ca-cluster-issuer"
        mock_cert_exists.return_value = False
        mock_wait.side_effect = QKViewError("Timed out waiting for cert")

        with pytest.raises(QKViewError, match="Timed out"):
            setup_qkview_certs(k8s_svc, 1)


class TestCheckCwcAvailable:
    """Test check_cwc_available — verify CWC namespace, service, certs."""

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._find_cert_secret")
    @patch("services.qkview_service.k8s_client")
    def test_all_available(self, mock_k8s, mock_find_cert, _mock_detect):
        """Should return available=True when everything exists."""
        k8s_svc = _mock_k8s_service()
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        mock_find_cert.return_value = CLIENT_CERT_CONFIG

        result = check_cwc_available(k8s_svc, 1)

        assert result["available"] is True

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service.k8s_client")
    def test_namespace_not_found(self, mock_k8s, _mock_detect):
        """Should return available=False when namespace missing."""
        k8s_svc = _mock_k8s_service()
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        v1.read_namespace.side_effect = _api_exception(404)

        result = check_cwc_available(k8s_svc, 1)

        assert result["available"] is False
        assert "Namespace" in result["message"]

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service.k8s_client")
    def test_service_not_found(self, mock_k8s, _mock_detect):
        """Should return available=False when CWC service missing."""
        k8s_svc = _mock_k8s_service()
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        v1.read_namespace.return_value = MagicMock()
        v1.read_namespaced_service.side_effect = _api_exception(404)

        result = check_cwc_available(k8s_svc, 1)

        assert result["available"] is False
        assert "CWC service" in result["message"]

    @patch("services.qkview_service._detect_cwc_namespace", return_value=CWC_DEFAULT_NAMESPACE)
    @patch("services.qkview_service._find_cert_secret")
    @patch("services.qkview_service.k8s_client")
    def test_no_certs_found(self, mock_k8s, mock_find_cert, _mock_detect):
        """Should return available=False when no cert secrets found."""
        k8s_svc = _mock_k8s_service()
        v1 = MagicMock()
        mock_k8s.CoreV1Api.return_value = v1
        mock_k8s.rest.ApiException = ApiException
        mock_find_cert.side_effect = QKViewError("No CWC mTLS certificates found")

        result = check_cwc_available(k8s_svc, 1)

        assert result["available"] is False
        assert "mTLS" in result["message"]

    def test_cluster_connection_failure(self):
        """Should return available=False on connection failure."""
        k8s_svc = _mock_k8s_service()
        k8s_svc.get_cluster.side_effect = Exception("cluster not found")

        result = check_cwc_available(k8s_svc, 999)

        assert result["available"] is False
        assert "Failed to check CWC" in result["message"]


# ═══════════════════════════════════════════════════════════════════════
# 3. PUBLIC METHODS: CRUD OPERATIONS
# ═══════════════════════════════════════════════════════════════════════


class TestListQkviews:
    """Test list_qkviews — list QKView jobs via CWC API."""

    @patch("services.qkview_service._cwc_request")
    def test_returns_list_from_items_key(self, mock_request):
        """Should extract items from dict with 'items' key."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"items": [{"id": "1"}, {"id": "2"}]}

        result = list_qkviews(k8s_svc, 1)

        assert result == [{"id": "1"}, {"id": "2"}]

    @patch("services.qkview_service._cwc_request")
    def test_wraps_single_item_with_id(self, mock_request):
        """Should wrap single item dict in a list."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"id": "abc", "status": "Completed"}

        result = list_qkviews(k8s_svc, 1)

        assert result == [{"id": "abc", "status": "Completed"}]

    @patch("services.qkview_service._cwc_request")
    def test_returns_empty_for_empty_dict(self, mock_request):
        """Should return empty list for empty response."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {}

        result = list_qkviews(k8s_svc, 1)
        assert result == []

    @patch("services.qkview_service._cwc_request")
    def test_wraps_single_item_with_filename(self, mock_request):
        """Should wrap single item dict that has filename in a list."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"filename": "qkview.tar.gz"}

        result = list_qkviews(k8s_svc, 1)
        assert result == [{"filename": "qkview.tar.gz"}]

    @patch("services.qkview_service._cwc_request")
    def test_propagates_error(self, mock_request):
        """Should propagate QKViewError from _cwc_request."""
        k8s_svc = _mock_k8s_service()
        mock_request.side_effect = QKViewError("CWC connection failed")

        with pytest.raises(QKViewError, match="connection failed"):
            list_qkviews(k8s_svc, 1)


class TestCreateQkview:
    """Test create_qkview — create a new QKView diagnostic."""

    @patch("services.qkview_service._cwc_request")
    def test_create_no_options(self, mock_request):
        """Should POST with no body when no options."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"id": "new-qk", "status": "Pending"}

        result = create_qkview(k8s_svc, 1)

        assert result["id"] == "new-qk"
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[1].get("body") is None or call_args[0][3] is None

    @patch("services.qkview_service._cwc_request")
    def test_create_with_options(self, mock_request):
        """Should POST with body when options provided."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"id": "new-qk"}
        options = {"namespace": "default", "filename": "test.tar.gz"}

        result = create_qkview(k8s_svc, 1, options=options)

        assert result["id"] == "new-qk"

    @patch("services.qkview_service._cwc_request")
    def test_create_empty_options_sends_no_body(self, mock_request):
        """Should not send body for empty options dict."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"id": "new-qk"}

        create_qkview(k8s_svc, 1, options={})

        # body should be None for empty dict (falsy)
        call_args = mock_request.call_args
        assert call_args[1].get("body") is None or len(call_args) > 3


class TestGetQkview:
    """Test get_qkview — get details of a specific QKView."""

    @patch("services.qkview_service._cwc_request")
    def test_returns_dict(self, mock_request):
        """Should return QKView details dict."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"id": "abc", "status": "Completed", "filename": "q.tar.gz"}

        result = get_qkview(k8s_svc, 1, "abc")

        assert result["id"] == "abc"
        assert result["status"] == "Completed"

    @patch("services.qkview_service._cwc_request")
    def test_returns_empty_dict_for_non_dict(self, mock_request):
        """Should return empty dict if result is not a dict."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = "unexpected string"

        result = get_qkview(k8s_svc, 1, "abc")
        assert result == {}

    @patch("services.qkview_service._cwc_request")
    def test_not_found_raises(self, mock_request):
        """Should propagate QKViewError for 404."""
        k8s_svc = _mock_k8s_service()
        mock_request.side_effect = QKViewError("404 not found", 404)

        with pytest.raises(QKViewError):
            get_qkview(k8s_svc, 1, "nonexistent")


class TestGetQkviewStatus:
    """Test get_qkview_status — get status of a specific QKView."""

    @patch("services.qkview_service._cwc_request")
    def test_returns_status_dict(self, mock_request):
        """Should return status dict."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"status": "Completed", "progress": 100}

        result = get_qkview_status(k8s_svc, 1, "abc")
        assert result["status"] == "Completed"

    @patch("services.qkview_service._cwc_request")
    def test_wraps_non_dict_in_status(self, mock_request):
        """Should wrap non-dict result in {'status': str(result)}."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = "Completed"

        result = get_qkview_status(k8s_svc, 1, "abc")
        assert result == {"status": "Completed"}


class TestDownloadQkview:
    """Test download_qkview — download QKView tarball as bytes."""

    @patch("services.qkview_service._cwc_request")
    def test_returns_bytes(self, mock_request):
        """Should return binary content."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = b"\x1f\x8b\x08\x00"  # gzip magic bytes

        result = download_qkview(k8s_svc, 1, "abc")
        assert isinstance(result, bytes)
        assert result == b"\x1f\x8b\x08\x00"

    @patch("services.qkview_service._cwc_request")
    def test_non_bytes_raises(self, mock_request):
        """Should raise QKViewError when result is not bytes."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"error": "something unexpected"}

        with pytest.raises(QKViewError, match="Expected binary data"):
            download_qkview(k8s_svc, 1, "abc")

    @patch("services.qkview_service._cwc_request")
    def test_propagates_error(self, mock_request):
        """Should propagate QKViewError from _cwc_request."""
        k8s_svc = _mock_k8s_service()
        mock_request.side_effect = QKViewError("Download failed")

        with pytest.raises(QKViewError, match="Download failed"):
            download_qkview(k8s_svc, 1, "abc")


class TestDeleteQkview:
    """Test delete_qkview — delete a specific QKView."""

    @patch("services.qkview_service._cwc_request")
    def test_returns_result_dict(self, mock_request):
        """Should return result from CWC."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"deleted": True}

        result = delete_qkview(k8s_svc, 1, "abc")
        assert result == {"deleted": True}

    @patch("services.qkview_service._cwc_request")
    def test_returns_deleted_true_for_non_dict(self, mock_request):
        """Should return {'deleted': True} when result is not a dict."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = "OK"

        result = delete_qkview(k8s_svc, 1, "abc")
        assert result == {"deleted": True}

    @patch("services.qkview_service._cwc_request")
    def test_409_in_progress_raises(self, mock_request):
        """Should raise friendly error for 409 (QKView in progress)."""
        k8s_svc = _mock_k8s_service()
        mock_request.side_effect = QKViewError("Conflict", 409)

        with pytest.raises(QKViewError, match="cancel it first") as exc_info:
            delete_qkview(k8s_svc, 1, "abc")
        assert exc_info.value.status_code == 409

    @patch("services.qkview_service._cwc_request")
    def test_other_error_propagated(self, mock_request):
        """Should propagate non-409 errors directly."""
        k8s_svc = _mock_k8s_service()
        mock_request.side_effect = QKViewError("Server error", 500)

        with pytest.raises(QKViewError, match="Server error"):
            delete_qkview(k8s_svc, 1, "abc")


class TestCancelQkview:
    """Test cancel_qkview — cancel a running QKView job."""

    @patch("services.qkview_service._cwc_request")
    def test_returns_result_dict(self, mock_request):
        """Should return result from CWC."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = {"cancelled": True}

        result = cancel_qkview(k8s_svc, 1, "abc")
        assert result == {"cancelled": True}

    @patch("services.qkview_service._cwc_request")
    def test_returns_cancelled_true_for_non_dict(self, mock_request):
        """Should return {'cancelled': True} when result is not a dict."""
        k8s_svc = _mock_k8s_service()
        mock_request.return_value = "OK"

        result = cancel_qkview(k8s_svc, 1, "abc")
        assert result == {"cancelled": True}

    @patch("services.qkview_service._cwc_request")
    def test_propagates_error(self, mock_request):
        """Should propagate QKViewError."""
        k8s_svc = _mock_k8s_service()
        mock_request.side_effect = QKViewError("Cancel failed")

        with pytest.raises(QKViewError, match="Cancel failed"):
            cancel_qkview(k8s_svc, 1, "abc")


class TestCleanupClientPods:
    """Test cleanup_client_pods — manually clean up all client pods."""

    @patch("services.qkview_service._cleanup_all_client_pods")
    def test_returns_cleaned(self, mock_cleanup):
        """Should return cleaned=True."""
        k8s_svc = _mock_k8s_service()

        result = cleanup_client_pods(k8s_svc, 1)
        assert result == {"cleaned": True}
        mock_cleanup.assert_called_once()

    @patch("services.qkview_service._cleanup_all_client_pods")
    def test_calls_cleanup_with_api_client(self, mock_cleanup):
        """Should pass the loaded api_client to _cleanup_all_client_pods."""
        k8s_svc = _mock_k8s_service()
        api_client = MagicMock()
        k8s_svc.load_kubeconfig.return_value = api_client

        cleanup_client_pods(k8s_svc, 1)

        mock_cleanup.assert_called_once_with(api_client)
