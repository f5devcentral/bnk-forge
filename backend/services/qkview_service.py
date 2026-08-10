"""
QKView / CWC Service — CWC REST API integration for BNK clusters.

Architecture (see DECISIONS.md D3):
  The CWC REST API requires mTLS + Bearer token auth and is only reachable
  from inside the cluster network.

  Solution: deploy a persistent "bnk-forge-agent" Pod that we control.
  - We create a lightweight Pod (alpine/curl) in f5-utils namespace
  - Mount the mTLS client cert + admin token from K8s secrets
  - Exec `curl` commands from inside our pod → CWC REST API
  - Pod is persistent (sleep infinity) and shared by QKView + licensing
  - Created on first use, reused for all subsequent CWC operations

  First-time setup (see DECISIONS.md D1):
  - The CWC REST API uses legacy certs (TLSGenSelfSignedtRootCA) by default
  - BNK-Forge replaces these with cert-manager-managed certs from bnk-ca
  - This is a one-time operation per cluster that requires a CWC pod restart
  - After setup, both server and client certs are managed by cert-manager

  F5 docs:
    https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/spk-ihealth.html
    https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/overviews/spk-admin-access-api.html
"""

import base64
import io
import json
import logging
import shlex
import tarfile
import tempfile
import time
import uuid
from typing import Any

from kubernetes import client as k8s_client
from kubernetes.stream import stream as k8s_stream

from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)

# CWC service coordinates
CWC_DEFAULT_NAMESPACE = "f5-utils"  # Legacy default; auto-detected at runtime
CWC_SERVICE = "f5-spk-cwc"
CWC_REST_PORT_NAME = "cwc-rest"
CLIENT_POD_IMAGE = "alpine/curl:latest"
CLIENT_POD_PREFIX = "bnk-forge-agent"
CLIENT_POD_LABEL = "bnk-forge-agent"  # shared pod for QKView, licensing, CWC ops
CLIENT_POD_TIMEOUT_SEC = 120  # Max time to wait for pod ready

# cert-manager setup — names for the resources we create
CLUSTER_ISSUER_CANDIDATES = (
    "bnk-ca-cluster-issuer",
    "arm-ca-cluster-issuer",
    "f5-cne-internal-ca",
)
# Slow-path discovery: name tokens that mark an issuer as BNK/CNE/F5-relevant.
# Deliberately does NOT include bare "-ca"/"ca-" — those over-match unrelated
# bootstrap CAs (e.g. "root-ca"). The suffix below covers the legacy
# "<name>-ca-cluster-issuer" naming convention precisely.
CLUSTER_ISSUER_NAME_TOKENS = ("bnk", "cne", "f5")
CLUSTER_ISSUER_NAME_SUFFIX = "-ca-cluster-issuer"
# Bootstrap/self-signed issuers to never auto-select.
CLUSTER_ISSUER_EXCLUDE_TOKENS = ("selfsigned", "self-signed")
CWC_SERVER_CERT_NAME = "bnk-forge-cwc-rest-api"
CWC_SERVER_CERT_SECRET = "bnk-forge-cwc-rest-api-tls"
CWC_CLIENT_CERT_NAME = "bnk-forge-cwc-client"
CWC_CLIENT_CERT_SECRET = "bnk-forge-cwc-client-tls"
CWC_LICENSE_CERTS_SECRET = "cwc-license-certs"
CWC_AUTH_TOKEN_SECRET = "cwc-auth-token"

# Cert secret used for mTLS after setup — our client cert from cert-manager
CLIENT_CERT_CONFIG = {
    "name": CWC_CLIENT_CERT_SECRET,
    "ca": "ca.crt",
    "cert": "tls.crt",
    "key": "tls.key",
}

# Fallback cert secrets if setup hasn't been done yet
FALLBACK_CERT_SECRET_CANDIDATES = [
    {
        "name": "tls-spkcwc-grpc-svr-secret",
        "ca": "ca.crt",
        "cert": "tls.crt",
        "key": "tls.key",
    },
    {
        "name": "cwc-license-certs",
        "ca": "ca-root-cert",
        "cert": "server-cert",
        "key": "server-key",
    },
]

# Ordered list: prefer our cert-manager client cert, fall back to legacy secrets
CWC_CERT_SECRET_CANDIDATES = [CLIENT_CERT_CONFIG] + FALLBACK_CERT_SECRET_CANDIDATES

# cert-manager Certificate CR defaults
CERT_DURATION = "8760h"  # 1 year
CERT_RENEW_BEFORE = "720h"  # 30 days before expiry

# CPCL state secrets — must be deleted before a license renewal to reset
# the CWC licensing state machine.  Names sourced from the manual cleanup
# script used in production (ns-f5-cnf-ciot-cgnat cleanup.sh).
CPCL_STATE_SECRETS: tuple[str, ...] = (
    "activationmessage",
    "activationstatus",
    "activationtimestamp",
    "configreport",
    "configreportsignedackresponse",
    "context",
    "csr",
    "customerprovidedid",
    "digitalassetid",
    "digitalassetname",
    "digitalassetversion",
    "entitlements",
    "initialregistrationstatus",
    "licensekey",
    "licensestatus",
    "modeofoperation",
    "previousreportname",
    "previousreportverifieddate",
    "productname",
    "statehistory",
    "statesofdecay",
    "switchlicensestatus",
    "telemetrystatus",
)


class QKViewError(Exception):
    """Error communicating with the CWC QKView API."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Helpers — CWC namespace detection
# ---------------------------------------------------------------------------


# Known BNK namespaces to scan — imported from shared pod discovery.
# Importing the tuple avoids duplicating the list of namespaces.
from services.bnk_pod_discovery import BNK_NAMESPACES as _BNK_NAMESPACES


def _detect_cwc_namespace(api_client: k8s_client.ApiClient) -> str:
    """
    Auto-detect which namespace contains the CWC service (f5-spk-cwc).

    Phase 1: Check known BNK namespaces first (fast path).
    Phase 2: If not found, list ALL services cluster-wide to catch
             CWC deployed in a non-standard namespace.

    Falls back to CWC_DEFAULT_NAMESPACE ("f5-utils") if no service is found
    anywhere, so existing clusters keep working even if the service is down.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)

    # Phase 1: known namespaces (fast)
    for ns in _BNK_NAMESPACES:
        try:
            core_v1.read_namespaced_service(CWC_SERVICE, ns)
            logger.debug(f"CWC service found in namespace '{ns}'")
            return ns
        except k8s_client.rest.ApiException:
            continue

    # Phase 2: cluster-wide sweep
    try:
        all_services = core_v1.list_service_for_all_namespaces(
            field_selector=f"metadata.name={CWC_SERVICE}",
            _request_timeout=10,
        )
        if all_services.items:
            ns = all_services.items[0].metadata.namespace
            logger.info(f"CWC service found in non-standard namespace '{ns}' via cluster-wide sweep")
            return ns
    except Exception as e:
        logger.debug(f"Cluster-wide CWC service sweep failed (non-fatal): {e}")

    logger.warning(
        f"CWC service '{CWC_SERVICE}' not found in any namespace. "
        f"Falling back to '{CWC_DEFAULT_NAMESPACE}'."
    )
    return CWC_DEFAULT_NAMESPACE


def _is_issuer_ready(item: dict) -> bool:
    """True if a ClusterIssuer object's status.conditions reports Ready=True."""
    conditions = item.get("status", {}).get("conditions", []) or []
    return any(
        c.get("type") == "Ready" and c.get("status") == "True" for c in conditions
    )


def _detect_cluster_issuer(api_client: k8s_client.ApiClient) -> str | None:
    """
    Auto-detect which ClusterIssuer to use for cert-manager certificates.

    Strategy:
      1. Check known candidates in order (see CLUSTER_ISSUER_CANDIDATES)
      2. If none found, list all ClusterIssuers and pick a Ready, non-selfsigned
         issuer whose name is specifically BNK/CNE/F5-related (contains one of
         CLUSTER_ISSUER_NAME_TOKENS or ends with CLUSTER_ISSUER_NAME_SUFFIX).
      3. Fail closed: return None if no such issuer is found. We deliberately
         do NOT fall back to "any Ready issuer" — issuing CWC mTLS certs from
         an unrelated CA (e.g. vault-issuer, istio-ca) would silently break
         mTLS since BNK would reject certs signed by a foreign CA.
    """
    custom_api = k8s_client.CustomObjectsApi(api_client)

    # Fast path: check known candidates by name
    for candidate in CLUSTER_ISSUER_CANDIDATES:
        try:
            custom_api.get_cluster_custom_object(
                "cert-manager.io", "v1", "clusterissuers", candidate
            )
            logger.debug(f"ClusterIssuer found: {candidate}")
            return candidate
        except k8s_client.rest.ApiException:
            continue

    # Slow path: list all ClusterIssuers and pick a Ready, non-bootstrap one
    try:
        issuers = custom_api.list_cluster_custom_object(
            "cert-manager.io", "v1", "clusterissuers"
        )
        items = issuers.get("items", [])

        named_matches: list[str] = []
        for item in items:
            name = item.get("metadata", {}).get("name", "")
            if not name:
                continue
            lowered = name.lower()
            if any(tok in lowered for tok in CLUSTER_ISSUER_EXCLUDE_TOKENS):
                continue
            # Belt-and-suspenders: also exclude explicit selfSigned issuer spec.
            if "selfSigned" in (item.get("spec", {}) or {}):
                continue
            if not _is_issuer_ready(item):
                continue
            if any(tok in lowered for tok in CLUSTER_ISSUER_NAME_TOKENS) or lowered.endswith(
                CLUSTER_ISSUER_NAME_SUFFIX
            ):
                named_matches.append(name)

        if named_matches:
            chosen = sorted(named_matches)[0]
            logger.info(
                f"ClusterIssuer discovered by BNK/CNE/F5 name match (Ready): {chosen}"
            )
            return chosen
    except k8s_client.rest.ApiException:
        logger.debug("cert-manager ClusterIssuer CRD not available on this cluster")

    logger.warning("No suitable CA ClusterIssuer found on cluster")
    return None


# ---------------------------------------------------------------------------
# Helpers — service discovery
# ---------------------------------------------------------------------------


def _get_cwc_rest_port(api_client: k8s_client.ApiClient, cwc_namespace: str) -> int:
    """Auto-discover the CWC REST API port from the K8s Service spec."""
    core_v1 = k8s_client.CoreV1Api(api_client)
    try:
        svc = core_v1.read_namespaced_service(CWC_SERVICE, cwc_namespace)
    except k8s_client.rest.ApiException as e:
        raise QKViewError(
            f"CWC service '{CWC_SERVICE}' not found in '{cwc_namespace}': {e.reason}",
            e.status,
        )
    if not svc.spec or not svc.spec.ports:
        raise QKViewError("CWC service has no ports defined")

    for port in svc.spec.ports:
        if port.name == CWC_REST_PORT_NAME:
            return port.port

    # Fallback: first port
    return svc.spec.ports[0].port


def _find_cert_secret(api_client: k8s_client.ApiClient, cwc_namespace: str) -> dict:
    """
    Find the mTLS cert secret to use. Tries candidates in order.
    Returns the matching candidate dict with the secret name + key mappings.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)
    for candidate in CWC_CERT_SECRET_CANDIDATES:
        try:
            secret = core_v1.read_namespaced_secret(
                candidate["name"], cwc_namespace
            )
            if (
                secret.data
                and candidate["cert"] in secret.data
                and candidate["key"] in secret.data
            ):
                logger.info(f"Using cert secret: {candidate['name']}")
                return candidate
        except k8s_client.rest.ApiException:
            continue
    raise QKViewError(
        "No CWC mTLS certificates found. Tried: "
        + ", ".join(c["name"] for c in CWC_CERT_SECRET_CANDIDATES)
    )


# ---------------------------------------------------------------------------
# Reusable client pod lifecycle
# ---------------------------------------------------------------------------


def _find_running_client_pod(
    api_client: k8s_client.ApiClient,
    cert_secret_name: str,
    cwc_namespace: str,
) -> str | None:
    """
    Find an existing running bnk-forge-agent pod that mounts the correct cert secret.
    Returns pod name or None.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)
    try:
        # Try new label first, then legacy label for backward compat
        for label in [CLIENT_POD_LABEL, "bnk-forge-qkview-client"]:
            pods = core_v1.list_namespaced_pod(
                cwc_namespace,
                label_selector=f"app={label},cert-secret={cert_secret_name}",
            )
            for pod in pods.items:
                if pod.status and pod.status.phase == "Running":
                    logger.debug(f"Reusing existing client pod: {pod.metadata.name}")
                    return pod.metadata.name
    except Exception as e:
        logger.warning(f"Failed to list client pods: {e}")
    return None


def _get_or_create_client_pod(
    api_client: k8s_client.ApiClient,
    cert_secret: dict,
    cwc_namespace: str,
) -> str:
    """
    Get a running client pod (with correct cert) or create a new one.
    Reuses existing pods to avoid the overhead of creating one per request.
    """
    existing = _find_running_client_pod(api_client, cert_secret["name"], cwc_namespace)
    if existing:
        return existing
    return _create_client_pod(api_client, cert_secret, cwc_namespace)


def _create_client_pod(
    api_client: k8s_client.ApiClient,
    cert_secret: dict,
    cwc_namespace: str,
) -> str:
    """
    Create a persistent agent Pod in the CWC namespace that mounts the CWC mTLS certs.

    This pod is shared by QKView, licensing, and any future CWC operations.
    Uses `sleep infinity` so it stays alive indefinitely (managed by BNK-Forge).
    Returns the pod name.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)
    pod_name = f"{CLIENT_POD_PREFIX}-{uuid.uuid4().hex[:8]}"

    # Build volume + volume mount for the cert secret
    volume = k8s_client.V1Volume(
        name="cwc-certs",
        secret=k8s_client.V1SecretVolumeSource(
            secret_name=cert_secret["name"],
            optional=False,
        ),
    )
    volume_mount = k8s_client.V1VolumeMount(
        name="cwc-certs",
        mount_path="/certs",
        read_only=True,
    )

    pod = k8s_client.V1Pod(
        metadata=k8s_client.V1ObjectMeta(
            name=pod_name,
            namespace=cwc_namespace,
            labels={
                "app": CLIENT_POD_LABEL,
                "managed-by": "bnk-forge",
                "cert-secret": cert_secret["name"],
            },
        ),
        spec=k8s_client.V1PodSpec(
            restart_policy="Never",
            # Keep the pod alive indefinitely — shared by QKView, licensing, CWC ops
            containers=[
                k8s_client.V1Container(
                    name="client",
                    image=CLIENT_POD_IMAGE,
                    command=["sleep", "infinity"],
                    volume_mounts=[volume_mount],
                    resources=k8s_client.V1ResourceRequirements(
                        requests={"cpu": "10m", "memory": "16Mi"},
                        limits={"cpu": "100m", "memory": "64Mi"},
                    ),
                )
            ],
            volumes=[volume],
            automount_service_account_token=False,
        ),
    )

    try:
        core_v1.create_namespaced_pod(cwc_namespace, pod)
        logger.info(f"Created qkview client pod: {pod_name}")
    except k8s_client.rest.ApiException as e:
        raise QKViewError(
            f"Failed to create qkview client pod: {e.reason}", e.status
        )

    # Wait for pod to be ready
    deadline = time.time() + CLIENT_POD_TIMEOUT_SEC
    while time.time() < deadline:
        try:
            p = core_v1.read_namespaced_pod(pod_name, cwc_namespace)
            if p.status and p.status.phase == "Running":
                logger.info(f"Qkview client pod ready: {pod_name}")
                return pod_name
            if p.status and p.status.phase in ("Failed", "Unknown"):
                raise QKViewError(
                    f"Qkview client pod failed to start: {p.status.phase}"
                )
        except k8s_client.rest.ApiException:
            pass
        time.sleep(1)

    # Timed out — clean up
    _delete_client_pod(api_client, pod_name)
    raise QKViewError(
        f"Qkview client pod did not become ready within "
        f"{CLIENT_POD_TIMEOUT_SEC}s"
    )


def _delete_client_pod(
    api_client: k8s_client.ApiClient, pod_name: str, cwc_namespace: str = CWC_DEFAULT_NAMESPACE,
):
    """Delete a client pod. Best-effort, never raises."""
    core_v1 = k8s_client.CoreV1Api(api_client)
    try:
        core_v1.delete_namespaced_pod(
            pod_name,
            cwc_namespace,
            grace_period_seconds=0,
            propagation_policy="Background",
        )
        logger.info(f"Deleted qkview client pod: {pod_name}")
    except Exception as e:
        logger.warning(f"Failed to delete qkview client pod {pod_name}: {e}")


def _cleanup_all_client_pods(api_client: k8s_client.ApiClient, cwc_namespace: str = CWC_DEFAULT_NAMESPACE):
    """Delete ALL bnk-forge agent pods (both new and legacy labels)."""
    core_v1 = k8s_client.CoreV1Api(api_client)
    for label in [CLIENT_POD_LABEL, "bnk-forge-qkview-client"]:
        try:
            pods = core_v1.list_namespaced_pod(
                cwc_namespace,
                label_selector=f"app={label}",
            )
            for pod in pods.items:
                _delete_client_pod(api_client, pod.metadata.name, cwc_namespace)
        except Exception as e:
            logger.warning(f"Client pod cleanup (label={label}) failed: {e}")


# ---------------------------------------------------------------------------
# Core: exec curl in the client pod
# ---------------------------------------------------------------------------


def _get_admin_token(api_client: k8s_client.ApiClient, cwc_namespace: str) -> str | None:
    """Read the CWC admin Bearer token from the cwc-auth-token secret."""
    core_v1 = k8s_client.CoreV1Api(api_client)
    try:
        secret = core_v1.read_namespaced_secret(
            CWC_AUTH_TOKEN_SECRET, cwc_namespace
        )
        if secret.data and "token" in secret.data:
            return base64.b64decode(secret.data["token"]).decode("utf-8").strip()
    except k8s_client.rest.ApiException:
        logger.debug(f"Admin token secret '{CWC_AUTH_TOKEN_SECRET}' not found")
    return None


def _build_curl_command(
    cert_secret: dict,
    cwc_url: str,
    method: str,
    body: dict | None = None,
    raw_body: str | None = None,
    output_file: str | None = None,
    bearer_token: str | None = None,
) -> str:
    """
    Build a shell command string for curl with mTLS certs + Bearer token.

    CWC requires both mTLS client certificate AND Bearer token (per F5
    Admin Access docs). The token comes from the cwc-auth-token secret.

    Args:
        body: JSON body (sent as application/json)
        raw_body: Raw string body (sent as-is, for /reactivate and /receipt
                  which expect raw JWT/manifest data per F5 docs)
    """
    cert_file = f"/certs/{cert_secret['cert']}"
    key_file = f"/certs/{cert_secret['key']}"

    parts = [
        "curl", "-s", "-S", "-k",
        "--cert", cert_file,
        "--key", key_file,
        "-X", method.upper(),
        "-w", r"\n---HTTP_STATUS:%{http_code}---",
    ]

    if bearer_token:
        parts.extend(["-H", f"Authorization: Bearer {bearer_token}"])

    if raw_body is not None:
        # /reactivate and /receipt expect raw data, not JSON.
        # Content-Type: application/json is still required per the manual
        # licensing script that works in production.
        parts.extend(["-H", "Content-Type: application/json"])
        parts.extend(["-d", raw_body])
    elif body is not None:
        parts.extend(["-H", "Content-Type: application/json"])
        parts.extend(["-d", json.dumps(body)])

    if output_file:
        parts.extend(["-o", output_file])

    parts.append(cwc_url)

    # shlex.join properly quotes each argument for sh -c
    return shlex.join(parts)


def _exec_in_pod(
    api_client: k8s_client.ApiClient,
    pod_name: str,
    command: list[str],
    cwc_namespace: str = CWC_DEFAULT_NAMESPACE,
) -> str:
    """Exec a command in the client pod and return stdout."""
    core_v1 = k8s_client.CoreV1Api(api_client)
    return k8s_stream(
        core_v1.connect_get_namespaced_pod_exec,
        pod_name,
        cwc_namespace,
        container="client",
        command=command,
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=True,
    )


def _copy_file_from_pod(
    api_client: k8s_client.ApiClient,
    pod_name: str,
    remote_path: str,
    local_path: str,
    cwc_namespace: str = CWC_DEFAULT_NAMESPACE,
) -> None:
    """
    Copy a file from a pod to the local filesystem.

    Uses the same approach as `kubectl cp`: exec `tar cf -` in the pod
    and stream the tar archive back, then extract locally. This handles
    large binary files reliably (unlike cat through websocket).

    The k8s websocket multiplexes stdout/stderr on separate channels.
    We read raw bytes from channel 1 (stdout) to avoid unicode issues.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)

    # Stream the tar output (don't preload — read in chunks)
    resp = k8s_stream(
        core_v1.connect_get_namespaced_pod_exec,
        pod_name,
        cwc_namespace,
        container="client",
        command=["tar", "cf", "-", "-C", "/", remote_path.lstrip("/")],
        stderr=True,
        stdin=False,
        stdout=True,
        tty=False,
        _preload_content=False,
        binary=True,  # Return raw bytes, not decoded strings
    )

    # Read all chunks from the websocket stream into a buffer
    tar_buffer = io.BytesIO()
    while resp.is_open():
        resp.update(timeout=300)
        if resp.peek_stdout():
            chunk = resp.read_stdout()
            if chunk:
                tar_buffer.write(chunk)
        if resp.peek_stderr():
            err = resp.read_stderr()
            if err:
                logger.warning(f"tar stderr: {err}")
    resp.close()

    tar_buffer.seek(0)
    tar_size = tar_buffer.getbuffer().nbytes
    logger.info(f"Received tar stream: {tar_size} bytes")

    if tar_size == 0:
        raise QKViewError("Received empty tar stream from pod")

    # Extract the single file from the tar archive
    with tarfile.open(fileobj=tar_buffer) as tar:
        members = tar.getmembers()
        if not members:
            raise QKViewError("tar archive from pod was empty")
        member = members[0]
        extracted = tar.extractfile(member)
        if extracted is None:
            raise QKViewError(f"Could not extract {member.name} from tar")
        with open(local_path, "wb") as f:
            while True:
                chunk = extracted.read(65536)
                if not chunk:
                    break
                f.write(chunk)

    logger.info(f"Copied {remote_path} from pod to {local_path}")


def _cwc_request(
    api_client: k8s_client.ApiClient,
    method: str,
    path: str,
    body: dict | None = None,
    raw_body: str | None = None,
    stream: bool = False,
) -> dict | bytes:
    """
    Make a request to the CWC REST API by:
      1. Auto-detecting the CWC namespace
      2. Getting/creating a reusable client pod with mTLS certs
      3. Exec'ing curl inside it
      4. Pod stays alive for reuse (NOT deleted per-request)

    Args:
        body: JSON body (sent as application/json)
        raw_body: Raw string body (sent as-is, for /reactivate and /receipt)
        stream: If True, return raw bytes (for binary downloads)
    """
    cwc_ns = _detect_cwc_namespace(api_client)
    cert_secret = _find_cert_secret(api_client, cwc_ns)
    cwc_port = _get_cwc_rest_port(api_client, cwc_ns)
    cwc_url = f"https://{CWC_SERVICE}.{cwc_ns}:{cwc_port}{path}"
    bearer_token = _get_admin_token(api_client, cwc_ns)

    pod_name = _get_or_create_client_pod(api_client, cert_secret, cwc_ns)

    try:
        if stream:
            # For downloads: curl saves to file in the pod, then we copy
            # it to a local temp file using tar-stream (like kubectl cp).
            # This is reliable for large files (40MB+ tarballs) unlike
            # piping binary through the k8s exec websocket directly.
            dl_cmd = _build_curl_command(
                cert_secret, cwc_url, method, body,
                raw_body=raw_body,
                output_file="/tmp/download.bin",
                bearer_token=bearer_token,
            )
            exec_resp = _exec_in_pod(api_client, pod_name, ["sh", "-c", dl_cmd], cwc_ns)
            logger.info(f"CWC {method} {path} (download) status line: {exec_resp[-60:]}")
            _check_curl_status(exec_resp, path)

            # Verify file was written
            size_resp = _exec_in_pod(
                api_client, pod_name,
                ["sh", "-c", "stat -c %s /tmp/download.bin 2>/dev/null || echo 0"],
                cwc_ns,
            )
            file_size = int(size_resp.strip() or "0")
            if file_size == 0:
                raise QKViewError("Download produced an empty file")
            logger.info(f"Download file size in pod: {file_size} bytes")

            # Copy from pod to local temp file via tar stream
            local_tmp = tempfile.mktemp(suffix=".tar.gz", dir="/tmp")
            try:
                _copy_file_from_pod(
                    api_client, pod_name, "/tmp/download.bin", local_tmp, cwc_ns
                )
                with open(local_tmp, "rb") as f:
                    return f.read()
            finally:
                try:
                    import os
                    os.unlink(local_tmp)
                except OSError:
                    pass
        else:
            cmd = _build_curl_command(
                cert_secret, cwc_url, method, body,
                raw_body=raw_body,
                bearer_token=bearer_token,
            )
            exec_resp = _exec_in_pod(api_client, pod_name, ["sh", "-c", cmd], cwc_ns)
            logger.info(f"CWC {method} {path} raw response: {exec_resp[:500]}")
            return _parse_curl_response(exec_resp, path)

    except QKViewError:
        raise
    except Exception as e:
        logger.error(f"CWC request failed: {e}", exc_info=True)
        raise QKViewError(f"Failed to communicate with CWC: {str(e)}")


def _check_curl_status(response: str, path: str):
    """Extract HTTP status from curl -w output and raise if error."""
    marker = "---HTTP_STATUS:"
    if marker in response:
        status_part = response.split(marker)[-1].replace("---", "").strip()
        try:
            status_code = int(status_part)
            if status_code >= 400:
                body = response.split(marker)[0].strip()
                raise QKViewError(
                    f"CWC API error ({status_code}): {body[:500]}", status_code
                )
        except ValueError:
            pass
    elif response.strip().startswith("curl:"):
        raise QKViewError(
            f"CWC connection failed: {response.strip()[:300]}"
        )
    else:
        # Absent marker with no curl error prefix means the HTTP exchange never
        # completed — websocket dropout, pod exec killed mid-stream, CWC
        # returned zero bytes.  This is not the same as a 200 OK.
        raise QKViewError(
            f"CWC {path}: request did not complete — no HTTP status returned "
            "from in-cluster curl. This is not the same as a 200 OK.",
            status_code=None,
        )


def _parse_curl_response(response: str, path: str) -> dict:
    """Parse curl response with embedded HTTP status."""
    marker = "---HTTP_STATUS:"

    if marker in response:
        parts = response.split(marker)
        body = parts[0].strip()
        status_str = parts[-1].replace("---", "").strip()
        status_code = 200
        try:
            status_code = int(status_str)
        except ValueError:
            pass

        if status_code >= 400:
            raise QKViewError(
                f"CWC API error ({status_code}): {body[:500]}", status_code
            )

        if not body:
            return {}

        try:
            parsed = json.loads(body)
            if isinstance(parsed, list):
                return {"items": parsed}
            return parsed
        except json.JSONDecodeError:
            return {"raw": body[:1000]}

    if response.strip().startswith("curl:"):
        raise QKViewError(
            f"CWC connection failed: {response.strip()[:300]}"
        )

    # Absent marker with no curl error prefix means the HTTP exchange never
    # completed — websocket dropout, pod exec killed mid-stream, CWC returned
    # zero bytes.  This is not the same as a 200 OK.
    raise QKViewError(
        f"CWC {path}: request did not complete — no HTTP status returned "
        "from in-cluster curl. This is not the same as a 200 OK.",
        status_code=None,
    )


# ---------------------------------------------------------------------------
# cert-manager setup — one-time per cluster (see DECISIONS.md D1)
# ---------------------------------------------------------------------------


def _check_cert_manager_available(api_client: k8s_client.ApiClient) -> bool:
    """Check if cert-manager CRDs are installed and a suitable ClusterIssuer exists."""
    return _detect_cluster_issuer(api_client) is not None


def _cert_exists(
    api_client: k8s_client.ApiClient, name: str, namespace: str
) -> bool:
    """Check if a cert-manager Certificate CR exists."""
    custom_api = k8s_client.CustomObjectsApi(api_client)
    try:
        custom_api.get_namespaced_custom_object(
            "cert-manager.io", "v1", namespace, "certificates", name
        )
        return True
    except k8s_client.rest.ApiException:
        return False


def _secret_exists(
    api_client: k8s_client.ApiClient, name: str, namespace: str
) -> bool:
    """Check if a K8s Secret exists and has data."""
    core_v1 = k8s_client.CoreV1Api(api_client)
    try:
        secret = core_v1.read_namespaced_secret(name, namespace)
        return bool(secret.data)
    except k8s_client.rest.ApiException:
        return False


def _create_cert_manager_certificate(
    api_client: k8s_client.ApiClient,
    name: str,
    namespace: str,
    secret_name: str,
    dns_names: list[str],
    usages: list[str],
    common_name: str | None = None,
    issuer_name: str = "bnk-ca-cluster-issuer",
) -> None:
    """Create a cert-manager Certificate CR."""
    custom_api = k8s_client.CustomObjectsApi(api_client)

    cert_body = {
        "apiVersion": "cert-manager.io/v1",
        "kind": "Certificate",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {
                "managed-by": "bnk-forge",
                "bnk-forge/purpose": "qkview-cwc",
            },
        },
        "spec": {
            "secretName": secret_name,
            "issuerRef": {
                "name": issuer_name,
                "kind": "ClusterIssuer",
                "group": "cert-manager.io",
            },
            "duration": CERT_DURATION,
            "renewBefore": CERT_RENEW_BEFORE,
            "usages": usages,
            "dnsNames": dns_names,
            "privateKey": {
                "algorithm": "RSA",
                "size": 2048,
            },
        },
    }
    if common_name:
        cert_body["spec"]["commonName"] = common_name

    custom_api.create_namespaced_custom_object(
        "cert-manager.io", "v1", namespace, "certificates", cert_body
    )
    logger.info(f"Created cert-manager Certificate: {namespace}/{name}")


def _wait_for_secret(
    api_client: k8s_client.ApiClient,
    secret_name: str,
    namespace: str,
    timeout_sec: int = 60,
) -> dict:
    """
    Wait for cert-manager to issue a certificate and populate the secret.
    Returns the secret data dict (base64-encoded values).
    """
    core_v1 = k8s_client.CoreV1Api(api_client)
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            secret = core_v1.read_namespaced_secret(secret_name, namespace)
            if (
                secret.data
                and "tls.crt" in secret.data
                and "tls.key" in secret.data
                and "ca.crt" in secret.data
            ):
                logger.info(f"Secret '{secret_name}' is ready")
                return secret.data
        except k8s_client.rest.ApiException:
            pass
        time.sleep(2)
    raise QKViewError(
        f"Timed out waiting for cert-manager to issue '{secret_name}' "
        f"({timeout_sec}s). Check cert-manager logs."
    )


def _copy_cert_to_cwc_license_secret(
    api_client: k8s_client.ApiClient,
    cert_data: dict,
    cwc_namespace: str = CWC_DEFAULT_NAMESPACE,
) -> None:
    """
    Copy cert-manager cert data into cwc-license-certs with the key names
    the CWC binary expects: server-cert, server-key, ca-root-cert.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)

    new_data = {
        "server-cert": cert_data["tls.crt"],
        "server-key": cert_data["tls.key"],
        "ca-root-cert": cert_data["ca.crt"],
    }

    try:
        # Try to patch the existing secret
        core_v1.patch_namespaced_secret(
            CWC_LICENSE_CERTS_SECRET,
            cwc_namespace,
            body={"data": new_data},
        )
        logger.info(f"Patched '{CWC_LICENSE_CERTS_SECRET}' with cert-manager certs")
    except k8s_client.rest.ApiException as e:
        if e.status == 404:
            # Secret doesn't exist yet — create it
            secret = k8s_client.V1Secret(
                metadata=k8s_client.V1ObjectMeta(
                    name=CWC_LICENSE_CERTS_SECRET,
                    namespace=cwc_namespace,
                    labels={
                        "managed-by": "bnk-forge",
                        "bnk-forge/purpose": "qkview-cwc",
                    },
                ),
                data=new_data,
                type="Opaque",
            )
            core_v1.create_namespaced_secret(cwc_namespace, secret)
            logger.info(f"Created '{CWC_LICENSE_CERTS_SECRET}' with cert-manager certs")
        else:
            raise QKViewError(
                f"Failed to update '{CWC_LICENSE_CERTS_SECRET}': {e.reason}",
                e.status,
            )


# CWC pod label candidates — different BNK versions use different labels
_CWC_POD_LABEL_CANDIDATES = ("app=cwc", "app=f5-spk-cwc")
# Fallback: match pod name prefix if no label works
_CWC_POD_NAME_PREFIX = "f5-spk-cwc"


def _find_cwc_pod(
    core_v1: k8s_client.CoreV1Api, cwc_namespace: str
) -> tuple[str | None, str | None]:
    """
    Find the CWC pod by trying label selectors, then falling back to pod
    name prefix matching.

    Returns (pod_name, label_selector_used) or (None, None) if not found.
    The label_selector is needed to watch for the replacement pod.
    """
    # Strategy 1: try known label selectors
    for label_sel in _CWC_POD_LABEL_CANDIDATES:
        pods = core_v1.list_namespaced_pod(cwc_namespace, label_selector=label_sel)
        if pods.items:
            pod_name = pods.items[0].metadata.name
            logger.debug(f"CWC pod found via label '{label_sel}': {pod_name}")
            return pod_name, label_sel

    # Strategy 2: fall back to pod name prefix
    all_pods = core_v1.list_namespaced_pod(cwc_namespace)
    for p in (all_pods.items or []):
        name = p.metadata.name or ""
        if name.startswith(_CWC_POD_NAME_PREFIX):
            logger.info(f"CWC pod found via name prefix '{_CWC_POD_NAME_PREFIX}': {name}")
            return name, None

    return None, None


def _restart_cwc_pod(api_client: k8s_client.ApiClient, cwc_namespace: str = CWC_DEFAULT_NAMESPACE) -> str:
    """
    Restart the CWC pod by deleting it. The Deployment controller will
    recreate it, and the new pod will read the updated cwc-license-certs
    secret via the K8s API.
    Returns the name of the deleted pod.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)

    pod_name, label_sel = _find_cwc_pod(core_v1, cwc_namespace)
    if not pod_name:
        raise QKViewError(
            f"No CWC pod found to restart. Tried labels: "
            f"{', '.join(_CWC_POD_LABEL_CANDIDATES)} and name prefix "
            f"'{_CWC_POD_NAME_PREFIX}' in namespace '{cwc_namespace}'."
        )

    core_v1.delete_namespaced_pod(
        pod_name,
        cwc_namespace,
        grace_period_seconds=10,
    )
    logger.info(f"Deleted CWC pod for restart: {pod_name}")

    # Wait for a new pod to come up Running
    deadline = time.time() + 120
    while time.time() < deadline:
        if label_sel:
            new_pods = core_v1.list_namespaced_pod(
                cwc_namespace, label_selector=label_sel,
            )
        else:
            # Found by name prefix — scan all pods again
            all_pods = core_v1.list_namespaced_pod(cwc_namespace)
            new_pods_items = [
                p for p in (all_pods.items or [])
                if (p.metadata.name or "").startswith(_CWC_POD_NAME_PREFIX)
            ]
            # Create a simple namespace to match the label_selector path
            new_pods = type("Pods", (), {"items": new_pods_items})()

        for p in new_pods.items:
            if (
                p.metadata.name == pod_name
                or not p.status
                or p.status.phase != "Running"
            ):
                continue
            # Phase=Running is not enough — the CWC HTTPS listener on port 38081
            # binds only after the readiness probe passes. Wait for every
            # container to report `ready=True` AND for the pod-level Ready
            # condition to be True. Without this gate, the first POST
            # /reactivate after force-renew hits the port too early and gets
            # `curl: (7) Failed to connect`.
            container_statuses = p.status.container_statuses or []
            all_containers_ready = bool(container_statuses) and all(
                cs.ready for cs in container_statuses
            )
            pod_ready_condition = next(
                (c for c in (p.status.conditions or []) if c.type == "Ready"),
                None,
            )
            pod_ready = pod_ready_condition is not None and pod_ready_condition.status == "True"
            if all_containers_ready and pod_ready:
                logger.info(f"New CWC pod is Ready: {p.metadata.name}")
                return pod_name
        time.sleep(3)

    logger.warning("CWC pod restart: new pod did not become Ready in 120s")
    return pod_name


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_setup_status(
    k8s_service: KubernetesService, cluster_id: int
) -> dict[str, Any]:
    """
    Check whether QKView mTLS setup has been completed for this cluster.

    Per F5 CloudDocs, QKView requires a running CWC instance plus client/server
    certificates for mutual TLS. In BNK Forge, those certs are managed through
    cert-manager and stored in the CWC namespace.

    Returns:
      {
        "setup_complete": bool,
        "cert_manager_available": bool,
        "server_cert_exists": bool,
        "client_cert_exists": bool,
        "message": str,
      }
    """
    try:
        cluster = k8s_service.get_cluster(cluster_id)
        api_client = k8s_service.load_kubeconfig(cluster)
    except Exception as e:
        return {
            "setup_complete": False,
            "cert_manager_available": False,
            "server_cert_exists": False,
            "client_cert_exists": False,
            "message": f"Failed to connect to cluster: {e}",
        }

    cwc_ns = _detect_cwc_namespace(api_client)
    cert_manager_ok = _check_cert_manager_available(api_client)
    server_cert_ok = _secret_exists(
        api_client, CWC_SERVER_CERT_SECRET, cwc_ns
    )
    client_cert_ok = _secret_exists(
        api_client, CWC_CLIENT_CERT_SECRET, cwc_ns
    )
    setup_complete = server_cert_ok and client_cert_ok

    if setup_complete:
        message = "CWC API mTLS setup is complete"
    elif not cert_manager_ok:
        message = (
            "No CA ClusterIssuer found. Any Ready cert-manager ClusterIssuer whose "
            "name references BNK/CNE/CA (or the well-known candidates: "
            f"{', '.join(CLUSTER_ISSUER_CANDIDATES)}) is accepted. "
            "cert-manager must be installed and configured."
        )
    elif server_cert_ok and not client_cert_ok:
        message = "Server cert exists but client cert is missing — setup incomplete"
    else:
        message = (
            "CWC API mTLS setup is required. This will replace the CWC REST API "
            "certificates and restart the CWC pod (one-time operation)."
        )

    return {
        "setup_complete": setup_complete,
        "cert_manager_available": cert_manager_ok,
        "server_cert_exists": server_cert_ok,
        "client_cert_exists": client_cert_ok,
        "message": message,
    }


def setup_cwc_api_certs(
    k8s_service: KubernetesService, cluster_id: int
) -> dict[str, Any]:
    """
    One-time QKView mTLS setup for CWC REST API access.

    Steps:
      1. Verify cert-manager + ClusterIssuer are available
      2. Create server Certificate CR → waits for issuance
      3. Copy issued cert into cwc-license-certs (CWC expected format)
      4. Restart CWC pod so it picks up the new certs
      5. Create client Certificate CR → waits for issuance
      6. Clean up any stale client pods (they have old certs mounted)

    This aligns BNK Forge with the documented QKView flow where CWC serves the
    QKView REST API over mTLS and clients authenticate with client cert, key,
    and CA material.

    Returns a status dict with details of each step.
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    cwc_ns = _detect_cwc_namespace(api_client)

    steps: list[dict] = []

    # Step 1: Verify cert-manager + detect ClusterIssuer
    issuer_name = _detect_cluster_issuer(api_client)
    if not issuer_name:
        raise QKViewError(
            "No CA ClusterIssuer found. Any Ready cert-manager ClusterIssuer whose "
            "name references BNK/CNE/CA (or the well-known candidates: "
            f"{', '.join(CLUSTER_ISSUER_CANDIDATES)}) is accepted. "
            "Install cert-manager and create a CA ClusterIssuer first."
        )
    logger.info(f"Using ClusterIssuer: {issuer_name}")
    steps.append({"step": "cert_manager_check", "status": "ok", "detail": f"issuer: {issuer_name}"})

    # Step 2: Create server cert (or skip if already exists)
    if _cert_exists(api_client, CWC_SERVER_CERT_NAME, cwc_ns):
        logger.info(f"Server Certificate '{CWC_SERVER_CERT_NAME}' already exists, skipping")
        steps.append({"step": "server_cert_create", "status": "skipped"})
    else:
        _create_cert_manager_certificate(
            api_client,
            name=CWC_SERVER_CERT_NAME,
            namespace=cwc_ns,
            secret_name=CWC_SERVER_CERT_SECRET,
            dns_names=[
                f"{CWC_SERVICE}",
                f"{CWC_SERVICE}.{cwc_ns}",
                f"{CWC_SERVICE}.{cwc_ns}.svc",
                f"{CWC_SERVICE}.{cwc_ns}.svc.cluster.local",
            ],
            usages=["server auth", "client auth", "digital signature", "key encipherment"],
            common_name=f"{CWC_SERVICE}.{cwc_ns}",
            issuer_name=issuer_name,
        )
        steps.append({"step": "server_cert_create", "status": "ok"})

    # Step 3: Wait for server cert secret + copy to cwc-license-certs
    cert_data = _wait_for_secret(api_client, CWC_SERVER_CERT_SECRET, cwc_ns)
    steps.append({"step": "server_cert_ready", "status": "ok"})

    _copy_cert_to_cwc_license_secret(api_client, cert_data, cwc_ns)
    steps.append({"step": "cwc_license_certs_updated", "status": "ok"})

    # Step 4: Restart CWC pod
    deleted_pod = _restart_cwc_pod(api_client, cwc_ns)
    steps.append({
        "step": "cwc_pod_restart",
        "status": "ok",
        "detail": f"Deleted pod: {deleted_pod}",
    })

    # Step 5: Create client cert (or skip if already exists)
    if _cert_exists(api_client, CWC_CLIENT_CERT_NAME, cwc_ns):
        logger.info(f"Client Certificate '{CWC_CLIENT_CERT_NAME}' already exists, skipping")
        steps.append({"step": "client_cert_create", "status": "skipped"})
    else:
        _create_cert_manager_certificate(
            api_client,
            name=CWC_CLIENT_CERT_NAME,
            namespace=cwc_ns,
            secret_name=CWC_CLIENT_CERT_SECRET,
            dns_names=[
                "bnk-forge-client",
                f"bnk-forge-client.{cwc_ns}",
            ],
            usages=["client auth", "digital signature", "key encipherment"],
            common_name="bnk-forge-client",
            issuer_name=issuer_name,
        )
        steps.append({"step": "client_cert_create", "status": "ok"})

    # Wait for client cert secret
    _wait_for_secret(api_client, CWC_CLIENT_CERT_SECRET, cwc_ns)
    steps.append({"step": "client_cert_ready", "status": "ok"})

    # Step 6: Clean up stale client pods (they mount old cert secrets)
    _cleanup_all_client_pods(api_client, cwc_ns)
    steps.append({"step": "stale_pods_cleanup", "status": "ok"})

    return {
        "success": True,
        "message": "CWC API mTLS setup complete. CWC REST API now uses cert-manager certs.",
        "steps": steps,
    }


# Backward-compatible alias while routes/tests migrate naming.
setup_qkview_certs = setup_cwc_api_certs


def check_cwc_available(
    k8s_service: KubernetesService, cluster_id: int
) -> dict[str, Any]:
    """
    Check if the CWC service is available and the mTLS certs are accessible.

    QKView and licensing both depend on the same CWC REST API path, so this is
    the shared health check for those workflows.
    """
    try:
        cluster = k8s_service.get_cluster(cluster_id)
        api_client = k8s_service.load_kubeconfig(cluster)
        core_v1 = k8s_client.CoreV1Api(api_client)

        # Auto-detect which namespace CWC lives in
        cwc_ns = _detect_cwc_namespace(api_client)

        try:
            core_v1.read_namespace(cwc_ns)
        except k8s_client.rest.ApiException:
            return {
                "available": False,
                "message": f"Namespace '{cwc_ns}' not found",
            }

        try:
            core_v1.read_namespaced_service(CWC_SERVICE, cwc_ns)
        except k8s_client.rest.ApiException:
            return {
                "available": False,
                "message": f"CWC service '{CWC_SERVICE}' not found in '{cwc_ns}'",
            }

        try:
            _find_cert_secret(api_client, cwc_ns)
        except QKViewError as e:
            return {"available": False, "message": str(e)}

        return {"available": True, "message": f"CWC is available in namespace '{cwc_ns}'"}

    except Exception as e:
        return {
            "available": False,
            "message": f"Failed to check CWC: {str(e)}",
        }


def list_qkviews(
    k8s_service: KubernetesService, cluster_id: int
) -> list[dict]:
    """List all QKView jobs on the cluster."""
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    result = _cwc_request(api_client, "GET", "/v1/qkview")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        if "items" in result:
            return result["items"]
        # Single item or empty
        return [result] if result.get("id") or result.get("filename") else []
    return []


def create_qkview(
    k8s_service: KubernetesService,
    cluster_id: int,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new QKView diagnostic tarball."""
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    # Only send a body if there are actual options
    body = options if options else None
    result = _cwc_request(api_client, "POST", "/v1/qkview", body=body)
    return result if isinstance(result, dict) else {"id": str(result)}


def get_qkview(
    k8s_service: KubernetesService, cluster_id: int, qkview_id: str
) -> dict:
    """Get details of a specific QKView by ID."""
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    result = _cwc_request(api_client, "GET", f"/v1/qkview/{qkview_id}")
    return result if isinstance(result, dict) else {}


def get_qkview_status(
    k8s_service: KubernetesService, cluster_id: int, qkview_id: str
) -> dict:
    """Get the status of a specific QKView."""
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    result = _cwc_request(api_client, "GET", f"/v1/qkview/{qkview_id}/status")
    return result if isinstance(result, dict) else {"status": str(result)}


def download_qkview(
    k8s_service: KubernetesService, cluster_id: int, qkview_id: str
) -> bytes:
    """Download the QKView tarball as bytes."""
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    result = _cwc_request(
        api_client, "GET", f"/v1/qkview/{qkview_id}/download", stream=True
    )
    if isinstance(result, bytes):
        return result
    raise QKViewError("Expected binary data from download endpoint")


def delete_qkview(
    k8s_service: KubernetesService, cluster_id: int, qkview_id: str
) -> dict:
    """Delete a specific QKView by ID."""
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    try:
        result = _cwc_request(
            api_client, "DELETE", f"/v1/qkview/{qkview_id}"
        )
        return result if isinstance(result, dict) else {"deleted": True}
    except QKViewError as e:
        if e.status_code == 409:
            raise QKViewError(
                "QKView is still in progress — cancel it first", 409
            )
        raise


def cancel_qkview(
    k8s_service: KubernetesService, cluster_id: int, qkview_id: str
) -> dict:
    """Cancel a running QKView job."""
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    result = _cwc_request(
        api_client, "POST", f"/v1/qkview/{qkview_id}/cancel"
    )
    return result if isinstance(result, dict) else {"cancelled": True}


def cleanup_client_pods(
    k8s_service: KubernetesService, cluster_id: int
) -> dict:
    """Manually clean up all qkview client pods. Exposed for admin use."""
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    _cleanup_all_client_pods(api_client)
    return {"cleaned": True}


# ---------------------------------------------------------------------------
# Licensing — CWC license endpoints via the same _cwc_request infrastructure
# ---------------------------------------------------------------------------
# These endpoints use the same CWC REST API (mTLS + Bearer) as QKView.
# See: https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/
#      installing-bnk-dpu-using-f5-lifecycle-operator/installing/
#      spk-cluster-license.html


def _normalize_cwc_status(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize the CWC /status response into a stable shape for the frontend.

    The CWC returns varying top-level keys depending on license state and
    CWC version:
      - ``InitialRegistrationStatus`` — first-time activation context
      - ``SwitchLicenseStatus`` — license switch context
      - ``LicenseStatus`` — sometimes a direct top-level field
      - ``TelemetryStatus`` — telemetry reporting state

    Each of these sub-objects may contain ``LicenseDetails``,
    ``LicenseStatus``, and ``ClusterDetails`` in slightly different layouts.

    This function extracts the best available license information and returns
    a consistent shape:

    .. code-block:: json

        {
            "license_state": "Active" | "Device Registration Failed" | ...,
            "entitlement_type": "eval" | "subscription" | ...,
            "digital_asset_id": "...",
            "license_expiry_date": "...",
            "license_expiry_days": 90,
            "cluster_name": "My Cluster",
            "error": "..." | null,
            "suggested_action": "..." | null,
            "telemetry_state": "...",
            "telemetry_message": "...",
            "raw_cwc_response": { ... }
        }
    """
    # Try multiple CWC response shapes in priority order.
    # SwitchLicenseStatus takes precedence if present (means a switch was
    # attempted and may have its own state).  Otherwise use
    # InitialRegistrationStatus, then fall back to direct top-level fields.
    reg_status: dict[str, Any] = {}
    for key in ("Status", "SwitchLicenseStatus", "InitialRegistrationStatus"):
        candidate = raw.get(key)
        if isinstance(candidate, dict):
            # Pick the first one that has a non-empty LicenseStatus
            ls = candidate.get("LicenseStatus", {})
            if ls and ls.get("State"):
                reg_status = candidate
                break
            # If we haven't found one with a State yet, keep this as fallback
            if not reg_status:
                reg_status = candidate

    license_details = reg_status.get("LicenseDetails", {}) or {}
    license_status_obj = reg_status.get("LicenseStatus", {}) or {}
    cluster_details = reg_status.get("ClusterDetails", {}) or {}

    # Also check for a direct top-level LicenseStatus (some CWC versions)
    if not license_status_obj:
        license_status_obj = raw.get("LicenseStatus", {}) or {}

    # Telemetry is always top-level
    telemetry = raw.get("TelemetryStatus", {}) or {}

    # Extract fields
    license_state = license_status_obj.get("State", "")
    error = license_status_obj.get("Error") or license_status_obj.get("ErrString") or None
    suggested_action = (
        license_status_obj.get("SuggestedAction")
        or license_status_obj.get("OperatorAction")
        or None
    )

    entitlement_type = license_details.get("EntitlementType", "")
    digital_asset_id = license_details.get("DigitalAssetID", "")
    expiry_date = license_details.get("LicenseExpiryDate", "")
    _raw_expiry = license_details.get("LicenseExpiryInDays")
    try:
        expiry_days = int(_raw_expiry) if _raw_expiry is not None else None
    except (ValueError, TypeError):
        expiry_days = None

    # Telemetry may be flat or nested under NextReport
    next_report = telemetry.get("NextReport", {}) or {}
    telemetry_state = telemetry.get("State", "") or next_report.get("State", "")
    telemetry_message = telemetry.get("Message", "") or next_report.get("Message", "")

    # Determine a normalized state value the frontend can rely on
    normalized: dict[str, Any] = {
        "license_state": license_state or "Unknown",
        "entitlement_type": entitlement_type or None,
        "digital_asset_id": digital_asset_id or None,
        "license_expiry_date": expiry_date or None,
        "license_expiry_days": expiry_days,
        "cluster_name": cluster_details.get("Name") or None,
        "error": error,
        "suggested_action": suggested_action,
        "telemetry_state": telemetry_state or None,
        "telemetry_message": telemetry_message or None,
        "raw_cwc_response": raw,
    }
    return normalized


def _extract_switch_error(status_result: dict[str, Any]) -> tuple[str | None, str | None]:
    """
    Pull the *switching*-context error out of a raw CWC /status response.

    The primary normalizer (`_normalize_cwc_status`) prefers the long-lived
    `Status` block, which keeps reporting the *current* license state even
    while a switch is failing. CWC reports the switch-specific outcome under
    `SwitchingStatus.LicenseStatus.{State,Error}` — useful only when
    diagnosing a failed switch_license / force_renew attempt.

    Returns (state, error) — either may be None if absent.
    """
    for key in ("SwitchingStatus", "SwitchLicenseStatus", "InitialRegistrationStatus"):
        section = status_result.get(key)
        if not isinstance(section, dict):
            continue
        ls = section.get("LicenseStatus") or {}
        state = ls.get("State")
        err = ls.get("Error") or ls.get("ErrString")
        if state or err:
            return state, err
    return None, None


def _format_switch_failure_message(
    pre_state: str | None,
    post_state: str | None,
    status_result: dict[str, Any],
) -> str:
    """
    Build a human-actionable error message for a failed switch_license attempt.

    Layers:
      1. CWC's own error text from SwitchingStatus.LicenseStatus.Error (if any)
      2. Remediation hint for the well-known eval→eval rejection
      3. Generic fallback when CWC told us nothing
    """
    switch_state, switch_error = _extract_switch_error(status_result)
    if switch_error:
        base = f"CWC rejected the license switch: {switch_error}"
        if switch_state and switch_state.lower() != "device registration failed":
            base = f"CWC rejected the license switch (state={switch_state!r}): {switch_error}"
        if "eval to eval" in switch_error.lower():
            base += (
                " — eval→eval is not allowed by CWC. Use a paid JWT, or use "
                "Force Renew to reset CPCL state first (deletes current eval "
                "and restarts CWC; ~2 min downtime)."
            )
        return base
    return (
        f"CWC accepted the request but license state did not change "
        f"(pre={pre_state!r}, post={post_state!r}). "
        "JWT may be invalid or for the wrong cluster."
    )


def get_license_status(
    k8s_service: KubernetesService, cluster_id: int
) -> dict[str, Any]:
    """
    Get CWC license and telemetry status for a cluster.

    CWC endpoint: GET /status
    Returns normalized license state, entitlement type, expiry, telemetry
    status, etc.  The raw CWC response is included as ``raw_cwc_response``
    for debugging.
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    result = _cwc_request(api_client, "GET", "/status")
    normalized = _normalize_cwc_status(result if isinstance(result, dict) else {})
    return {"success": True, **normalized}


def get_license_report(
    k8s_service: KubernetesService, cluster_id: int
) -> dict[str, Any]:
    """
    Get CWC telemetry report for a cluster.

    CWC endpoint: GET /report
    Only available when CWC telemetry state is "Config Report Ready to Download".
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    result = _cwc_request(api_client, "GET", "/report")
    return {"success": True, **result}


def activate_license(
    k8s_service: KubernetesService, cluster_id: int, jwt_data: str
) -> dict[str, Any]:
    """
    Reactivate/switch CWC license on a cluster.

    CWC endpoint: POST /reactivate
    Per F5 docs, /reactivate expects raw JWT data (NOT JSON-wrapped).

    Before activation, validates that cpcl-key-cm has real JWKS data
    (not placeholder values from the FLO Helm chart).
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    # Ensure JWKS is valid before attempting activation — without valid
    # JWKS, CWC will accept the JWT via /reactivate but then fail
    # verification with "illegal base64 data at input byte 0".
    jwks_status = None
    jwks_validation: dict[str, Any] | None = None
    try:
        jwks_status = ensure_valid_jwks(api_client)
    except QKViewError as e:
        logger.warning(f"JWKS validation failed during activate (non-fatal): {e}")
        jwks_validation = {"status": "failed", "error": str(e)}

    # Capture pre-state so we can detect whether activation actually took effect.
    pre: dict[str, Any] = {}
    try:
        pre_raw = _cwc_request(api_client, "GET", "/status")
        pre = _normalize_cwc_status(pre_raw if isinstance(pre_raw, dict) else {})
    except QKViewError as e:
        logger.warning(f"Pre-activate status check failed (non-fatal): {e}")

    activate_result = _cwc_request(api_client, "POST", "/reactivate", raw_body=jwt_data)
    result = activate_result if isinstance(activate_result, dict) else {}

    # Capture post-state to derive success from observable outcome.
    post: dict[str, Any] = {}
    try:
        post_raw = _cwc_request(api_client, "GET", "/status")
        post = _normalize_cwc_status(post_raw if isinstance(post_raw, dict) else {})
    except QKViewError as e:
        logger.warning(f"Post-activate status check failed (non-fatal): {e}")

    _allow_list = {"active", "licensed", "verification complete"}
    post_state_lower = (post.get("license_state") or "").strip().lower()
    pre_state_lower = (pre.get("license_state") or "").strip().lower()
    asset_changed = bool(
        post.get("digital_asset_id") and post["digital_asset_id"] != pre.get("digital_asset_id")
    )
    expiry_changed = bool(
        post.get("license_expiry_date") and post["license_expiry_date"] != pre.get("license_expiry_date")
    )

    accepted = (
        asset_changed
        or expiry_changed
        or (post_state_lower in _allow_list and post_state_lower != pre_state_lower)
    )

    # Benign re-activate: pre was already healthy and state did not change — idempotent success.
    if not accepted and pre_state_lower in _allow_list and post_state_lower == pre_state_lower:
        accepted = True
        reactivate_message: str | None = "license already active (no state change observed)"
    else:
        reactivate_message = None

    response: dict[str, Any]
    if accepted:
        response = {
            "success": True,
            **result,
            "license_state_pre": pre.get("license_state"),
            "license_state_post": post.get("license_state"),
            "digital_asset_id_post": post.get("digital_asset_id"),
        }
        if reactivate_message:
            response["message"] = reactivate_message
    else:
        response = {
            "success": False,
            "error_message": (
                f"CWC accepted the request but license state did not change "
                f"(pre={pre.get('license_state')!r}, post={post.get('license_state')!r}). "
                "JWT may be invalid or for the wrong cluster."
            ),
            "license_state_pre": pre.get("license_state"),
            "license_state_post": post.get("license_state"),
            "cwc_response": result,
        }

    if jwks_status:
        response["jwks_validation"] = jwks_status
    elif jwks_validation:
        response["jwks_validation"] = jwks_validation
    return response


def post_license_receipt(
    k8s_service: KubernetesService, cluster_id: int, manifest_data: str
) -> dict[str, Any]:
    """
    Send signed manifest (receipt) back to CWC.

    CWC endpoint: POST /receipt
    Per F5 docs, /receipt expects raw manifest data without curly
    brackets or quotes.
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    raw_result = _cwc_request(api_client, "POST", "/receipt", raw_body=manifest_data)
    result = raw_result if isinstance(raw_result, dict) else {}

    # A completed receipt POST should return a non-trivial body.  Empty or
    # raw-only response means CWC silently rejected the manifest.
    is_empty = not result or result == {"raw": ""}
    if is_empty:
        return {
            "success": False,
            "error_message": (
                "CWC returned empty response to POST /receipt. "
                "The manifest may be malformed or CWC rejected it silently."
            ),
            "cwc_response": result,
        }
    return {"success": True, **result}


# ---------------------------------------------------------------------------
# License ConfigMap helpers — JWKS validation + JWT patching
# ---------------------------------------------------------------------------

# F5's static public JWKS for BNK license JWT verification.
# Source: https://product.apis.f5.com/ee/v1/keys/jwks
# These are the same for ALL BNK deployments — they are F5's signing keys,
# not cluster-specific.  Hardcoded here as a fallback when the cluster
# cannot reach the F5 API or when the FLO Helm chart rendered placeholders.
F5_JWKS_URL = "https://product.apis.f5.com/ee/v1/keys/jwks"
F5_STATIC_JWKS: dict[str, Any] = {
    "keys": [
        {
            "kty": "RSA",
            "kid": "v1",
            "use": "sig",
            "alg": "RS512",
            "n": "wgqDv-fuebdh_gV3wN8voRGcHGDo4YekYT78U2x-gAgxWDFFP4uIpQk9d_Hszevyr78xgFBD7RnR4FeWu7R62L1DnEEbrQYEjNI1quLtcfI5wvfdMLeyv5ZXe4-Uu21lwhvRtJCyzNRW797NPaVsMAzrcWwslHZdrljgbLaf3UX90ITyqd9MFgb7jNmRLjdzZLDqQjyYJTq2AOaAoagX0pqKkBWGlgNbiFAuV2RMUhis9Ei5NSfiD5I5ntsvp_Q_XxcJrYrJOs9uaXkeTMXa58JUeX7Tt6zLA6Ju1wxClYMyNWj4u5dG8u2yhCrucJ2_IvTs4A1BSOfAWW5TwS8BBYuY9H_OXgoqyYybeOU5YOHmszaDXfMs7FFZkjAFIfr95bprH-HW1cwREQ_eqkYxi9XmA3KCNQGLhZ_i9gRTu8MlGwYzckeJG_NOyPIa7WRw7zFe7RHcchqaCSjpWC4u-GSZ92uf-EUw2Db7OX4J8VcD_3cvCfCEzfsrSaTkLZvlkYLC-3x4eM7B2GiM7SA8l4u0DK0nrScLsOR5njd2IUUB38K39Jq-tlD5PoqLT6u0AE4IJtWN-S6uFhqzdUvExBYK19ZhTbeMmOEkMALlEPhsNUnOSXGnocxlsRYldwfCoNtpVYxlXrSnbHkuPlK8Q27yi1wO9r3RwO_OxQnUvXk",
            "e": "AQAB",
            "x5c": [
                "MIIFpzCCBI+gAwIBAgIQcC/1AFczGqUAmtLfiN4SvzANBgkqhkiG9w0BAQsFADCBpzELMAkGA1UEBhMCVVMxEzARBgNVBAgMCldhc2hpbmd0b24xGjAYBgNVBAoMEUY1IE5ldHdvcmtzLCBJbmMuMR4wHAYDVQQLDBVDZXJ0aWZpY2F0ZSBBdXRob3JpdHkxNTAzBgNVBAMMLEY1IFBSRCBJc3N1aW5nIENlcnRpZmljYXRlIEF1dGhvcml0eSBURUVNIFYxMRAwDgYDVQQHDAdTZWF0dGxlMB4XDTIyMDEyNTE2MzYwOVoXDTI3MDEyNDE3MzYwOVowgYExCzAJBgNVBAYTAlVTMRMwEQYDVQQIDApXYXNoaW5ndG9uMRAwDgYDVQQHDAdTZWF0dGxlMRowGAYDVQQKDBFGNSBOZXR3b3JrcywgSW5jLjENMAsGA1UECwwEVEVFTTEgMB4GA1UEAwwXRjUgUFJEIFRFRU0gSldUIEF1dGggdjEwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDCCoO/5+55t2H+BXfA3y+hEZwcYOjhh6RhPvxTbH6ACDFYMUU/i4ilCT138ezN6/KvvzGAUEPtGdHgV5a7tHrYvUOcQRutBgSM0jWq4u1x8jnC990wt7K/lld7j5S7bWXCG9G0kLLM1Fbv3s09pWwwDOtxbCyUdl2uWOBstp/dRf3QhPKp30wWBvuM2ZEuN3NksOpCPJglOrYA5oChqBfSmoqQFYaWA1uIUC5XZExSGKz0SLk1J+IPkjme2y+n9D9fFwmtisk6z25peR5MxdrnwlR5ftO3rMsDom7XDEKVgzI1aPi7l0by7bKEKu5wnb8i9OzgDUFI58BZblPBLwEFi5j0f85eCirJjJt45Tlg4eazNoNd8yzsUVmSMAUh+v3lumsf4dbVzBERD96qRjGL1eYDcoI1AYuFn+L2BFO7wyUbBjNyR4kb807I8hrtZHDvMV7tEdxyGpoJKOlYLi74ZJn3a5/4RTDYNvs5fgnxVwP/dy8J8ITN+ytJpOQtm+WRgsL7fHh4zsHYaIztIDyXi7QMrSetJwuw5HmeN3YhRQHfwrf0mr62UPk+iotPq7QATggm1Y35Lq4WGrN1S8TEFgrX1mFNt4yY4SQwAuUQ+Gw1Sc5JcaehzGWxFiV3B8Kg22lVjGVetKdseS4+UrxDbvKLXA72vdHA787FCdS9eQIDAQABo4HyMIHvMAkGA1UdEwQCMAAwHwYDVR0jBBgwFoAUg6RSCVfoA+ncTgkMv61aIxsPLegwHQYDVR0OBBYEFCKHAv7lwN4DEyUCOp0XHcWZ8QySMA4GA1UdDwEB/wQEAwIFoDAdBgNVHSUEFjAUBggrBgEFBQcDAQYIKwYBBQUHAwIwcwYDVR0fBGwwajBooGagZIZiaHR0cDovL2NybC10ZWVtLXByZC1vcmUtZjUuczMudXMtd2VzdC0yLmFtYXpvbmF3cy5jb20vY3JsLzJlNmVhYWI3LTFmNDQtNGRmYS05MTY3LWQ2MjZjOTAzY2M2Zi5jcmwwDQYJKoZIhvcNAQELBQADggEBAGwFGNJVigk6rrJRy5SVUYFR1LPEE/LpdpaFfZjbiviG0LIzu3TA6sbyj6+KFbdQX+tvK46JvDmdTDow23gvqdujPMBcwIGVstg1PYYae8rq29iu3mmC5Y/bWJSYD34hxkUE8k3z3r7aCuUkle3jOwYMgQqVqT/CmdbOVlgBGW+qL2+jD0kBCarJUH6Ckb6X5rFQB/X6bisIxuA6ubYsIsDiPYlt87xScHlIjH2hVuDU/QAXpxL7SvKCsLU8GDCpQqHjJusaD48o2/zDGalqVpjZ9f+McC7fj/DAHidzTvJ44GTxQ+5yeSput9rcpkTwHmJ2TAqDAWZ9HXC0X/1pJ8o=",
                "MIIE/jCCAuagAwIBAgIBAjANBgkqhkiG9w0BAQsFADB1MQswCQYDVQQGEwJVUzELMAkGA1UECBMCV0ExGTAXBgNVBAoTEEY1IE5ldHdvcmtzLCBJbmMxPjA8BgNVBAMTNUY1IFBSRCBJbnRlcm1lZGlhdGUgQ2VydGlmaWNhdGUgQXV0aG9yaXR5IEY1QVBJLVBNIFYyMB4XDTE5MDEwNDE3NTIyNVoXDTI5MDEwMTE3NTIyNVowgacxCzAJBgNVBAYTAlVTMRMwEQYDVQQIDApXYXNoaW5ndG9uMRowGAYDVQQKDBFGNSBOZXR3b3JrcywgSW5jLjEeMBwGA1UECwwVQ2VydGlmaWNhdGUgQXV0aG9yaXR5MTUwMwYDVQQDDCxGNSBQUkQgSXNzdWluZyBDZXJ0aWZpY2F0ZSBBdXRob3JpdHkgVEVFTSBWMTEQMA4GA1UEBwwHU2VhdHRsZTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBALXsiCog/VlPmHOcPNC0Q5hAm5fsY7IkdBqaDJ/TO+XRsyMQbZBFgbG8MueW2d6jVVYKoJeT+5q8HltzF31yUjWMRAznJGyawN3Rvb0+8ZeHeIJRkUQHjXvo7Mx3tUwhkcPVxYZB0XGjA1jZakminP7x/uOKhZgzafDT165hGpFbtTjBcTmtLHKIWiX27toNbDr4J9DMtpeo0MVJYgaaZVsV0BjL+6R3pYiWhJlomJVtrlFMBL6o9LgzlBPq7i5fiAjkkuJC3f0am/HP9PzbvPjmpL70/Z8el6w48UpTuYbG3gYLILVoRByTyGn8QzeKHcqsMvXkwGIwPTyhcK+gQ/sCAwEAAaNmMGQwHQYDVR0OBBYEFIOkUglX6APp3E4JDL+tWiMbDy3oMB8GA1UdIwQYMBaAFFqYykyPkWydzv5wl+VsJZ88cOeMMBIGA1UdEwEB/wQIMAYBAf8CAQEwDgYDVR0PAQH/BAQDAgGGMA0GCSqGSIb3DQEBCwUAA4ICAQASUz5XADaWb4gIjcdOCrqfCvq95CaIjM4TLn4pMaGL9MaRW498CzsVNqjaJKe5t3De6xONMheocnvPMV1V3JZ6olaDcseDPTo3V/Gu+PVAv0/qOpmbJmipM5yHcY6bM6Ek+EQOAjHjs4bV+IrDyorUIHtHfSzFhKSmIiBafyuCsbuG3z9B7ty7Rm9jjayLSLRmEbpN+IkqKQGNCWlVHlEwQ6NY+kIhXAfHQf6JpKLsw36ukkvpQGGb46sQsXI2Bh94yM7xxQs0baXyHSCck6h9Az2FUQxRjqDQCANjLiMe2qclWCuPS4o/i792mWC/AQMXj+ofH3vLaPjSm5eETeBpcSOMrczY9V75Tn+WN7h/fpIHyiToZ3IAHoncDuaXUCTvJ6uMZPLjb8hEX9aqYwvPwYI9IADcqdx3SGd69JLmk7o+P7pcX0rlAttMQC8WOJfjUlex9jjSLn+6ArPa94+Agckh09r5M8X1GOMpQatXr/QZ76qaBEaq6QuKJBT7j4vvMDbv9AtU87+v478Joiv2Br74V3ZtCrneE8OT1AjeaZ6++8bxNIuvvG1qYq48sZv4RIMw+uodLoyNUOUqgTx/N2I6Rrw9ytxQV3v0smZTLczWx3dgACZnc5ngMLiaxRVtPcLSxogTEjpCKOjk4wk8f10h99Hy+Vl3eA2laE2lFA==",
                "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",
                "MIIGETCCA/mgAwIBAgIBGDANBgkqhkiG9w0BAQsFADCBujEZMBcGA1UEChMQRjUgTmV0d29ya3MsIEluYzEaMBgGA1UECxMRSW50ZXJuYWwgVXNlIE9ubHkxHTAbBgkqhkiG9w0BCQEWDnJvb3RAbG9jYWxob3N0MRAwDgYDVQQHEwdTZWF0dGxlMQswCQYDVQQIEwJXQTELMAkGA1UEBhMCVVMxNjA0BgNVBAMTLUY1IFBSRCBSb290IENlcnRpZmljYXRlIEF1dGhvcml0eSBGNUFQSS1QUiBWMjAeFw0yMjA4MTcxNjAxMjBaFw0zMjA4MTQxNjAxMjBaMHUxCzAJBgNVBAYTAlVTMQswCQYDVQQIDAJXQTEZMBcGA1UECgwQRjUgTmV0d29ya3MsIEluYzE+MDwGA1UEAww1RjUgUFJEIEludGVybWVkaWF0ZSBDZXJ0aWZpY2F0ZSBBdXRob3JpdHkgRjVBUEktUE0gVjIwggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQC87P9OSZd3BG6JpelO6fEuDxT5NvtNIQANSGwL3889yHRPduKx1JdZBl1BIUp6ZmEUSwt2Fp/TUDukgiawN3L7FA9w4xLOGFoiO0qLEPANygEbA4MdfMNfh8+ceNOXkIO1H3KHRixC8q+edaQHjCWju3Vrz9zYLGaDbSTD71U/volviFzr/fVQBPJqRoW51z8xO7SlM0U6bBr4LeXQtzJUrPgzrApCDQJ5081LEGdrWAZuHi2YVCUgwDgxWSJRPHCGpvXBjbsjz6ZncO0Be9NWgU0Eljg95hj2qyujMK+BUNO0MTAM3FlyyVI2QhqWdPgZUaZzkGlY8snML62ypdjt0jKTJmXRZLY//ZSve4j6EE0Lk7uAfi7O902mSPuQmUnvEX9psXQruq6M4wiChCEER04YTXNShPxfjmQk6Xa8a/NP94aQGQqGqpY5bqMNNjfJEU0Va8lh61FdUltB9sCX+xq0o+iXd92eJqXeZsXTdJld2cFW6N0hI7SlpLcHdMJNbuLDUk3JbcgflUU/gSXARYImQVvSG7s0sdvCw66zrgGZOtkI0oSRmlPofJmxkJXBl4Eo4xwe6mwtsQLZ0XeRePKbs2gcQhHAWJqzRinsC3TneaIb7rW+0c2grPn+gcd8jUI3zbc62Y8wz4YuLkWGrZMttvvGWYoXPkDaGba5ywIDAQABo2YwZDAdBgNVHQ4EFgQUWpjKTI+RbJ3O/nCX5Wwlnzxw54wwHwYDVR0jBBgwFoAUNyeBj+lAqBvokj8drl3gs60xD5QwEgYDVR0TAQH/BAgwBgEB/wIBATAOBgNVHQ8BAf8EBAMCAYYwDQYJKoZIhvcNAQELBQADggIBAFzxxIcmKXmwBeiT73s8NdYjwsfhXn0LYrD5DgYYs5/SmPZ/M2CsMkVOGXgYQmqXxQl9CZWAn4vEnpGW9LMi2buPFHgEfSLxw2ESNnxu5x+GForjVebtexeZl83dEkNBfKshL+XuV/ZRYilRpDYA9hionMBwVZxmNH+oMT1I7GIxGZlTD6Qko0K+KKmqua2GebIO+6L56Bht/791BrARXDjOINgyzJAYYahpE3C5XQWj8QoZjXP+iA4LyfBJO2MXcpOKnGJRNDIFMJnOKvcbJenPOy3jH2fOA9Dz7FxZKffxV/wss/W03xrASrQSWnrZl1vBkRVuJTohCodJBQ2TyzjzoUhx0biwQDJgOpSGs5bvu0CnVX99CpCshHS6cU66ry6tFIcyXcZi+EJ37E0+27WDWGf8HuRBa+DfYDGJw7rKIjaMaCo+nijyBomvUMLp+pLFmPQhS7IdjjSxtg90G6i+vA13t79b7NmaTFSogdJA+wNtVuMm2/dZGk85NOQYSepArKc88BfOXjmfRsO5ANnFbDXZwz59WksfXjwItXGRsdOasG1ZvnxbGTB+PdZ5sqIpVFFG8ioSvhmM3kj/4nUJ7H5N4KjgXY9krnTiMHYXtbm2ckXDahAq/9s8CGG6/VsdBkbO3007A/qJtXQMxeyfVdY2Chjs2X2VqdkWYd18",
            ],
        }
    ]
}

# ConfigMap names created by the FLO Helm chart
CPCL_KEY_CM = "cpcl-key-cm"        # JWKS public key for JWT verification
CPCL_CONFIG_CM = "cpcl-config-cm"  # JWT + license config

# Minimum length of a valid RSA modulus (base64url-encoded).
# A real 4096-bit RSA modulus is ~680 chars.  Placeholder "..." is 3 chars.
_MIN_RSA_MODULUS_LEN = 100


def _read_configmap(
    api_client: k8s_client.ApiClient,
    name: str,
    namespace: str,
) -> dict[str, str] | None:
    """Read a ConfigMap's data dict. Returns None if not found."""
    core_v1 = k8s_client.CoreV1Api(api_client)
    try:
        cm = core_v1.read_namespaced_config_map(name, namespace)
        return dict(cm.data) if cm.data else {}
    except k8s_client.rest.ApiException as e:
        if e.status == 404:
            return None
        raise


def _patch_configmap(
    api_client: k8s_client.ApiClient,
    name: str,
    namespace: str,
    data: dict[str, str],
) -> None:
    """Patch a ConfigMap's data. Creates the ConfigMap if it doesn't exist."""
    core_v1 = k8s_client.CoreV1Api(api_client)
    try:
        core_v1.patch_namespaced_config_map(
            name, namespace, body={"data": data},
        )
        logger.info(f"Patched ConfigMap '{namespace}/{name}'")
    except k8s_client.rest.ApiException as e:
        if e.status == 404:
            cm = k8s_client.V1ConfigMap(
                metadata=k8s_client.V1ObjectMeta(
                    name=name,
                    namespace=namespace,
                    labels={"managed-by": "bnk-forge"},
                ),
                data=data,
            )
            core_v1.create_namespaced_config_map(namespace, cm)
            logger.info(f"Created ConfigMap '{namespace}/{name}'")
        else:
            raise QKViewError(
                f"Failed to patch ConfigMap '{name}': {e.reason}", e.status,
            )


def _validate_jwt_shape(jwt_data: str) -> str | None:
    """
    Basic structural pre-check for a JWT before sending to CWC.

    Returns None if the shape is valid, or an error message string if invalid.
    Does NOT validate the signature — that's CWC's job with the JWKS.
    """
    parts = jwt_data.strip().split(".")
    if len(parts) != 3:
        return f"JWT does not parse — expected three base64url-encoded parts separated by '.', got {len(parts)}"

    if not parts[2]:
        return "JWT signature (part 3) is empty"

    for i, (part, label) in enumerate(zip(parts[:2], ("header", "payload"))):
        # base64url: pad to multiple of 4
        padded = part + "=" * (-len(part) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded)
            parsed = json.loads(decoded)
        except Exception:
            return f"JWT {label} (part {i + 1}) does not decode as base64url JSON"

        if label == "header":
            if not isinstance(parsed, dict) or "alg" not in parsed or "typ" not in parsed:
                return "JWT header is missing required 'alg' or 'typ' fields"

    return None


def _jwks_has_valid_modulus(jwks_data: str) -> bool:
    """
    Check if JWKS JSON data contains a valid RSA modulus.

    Returns False if the 'n' field is a placeholder ("...") or too short
    to be a real RSA key.
    """
    try:
        jwks = json.loads(jwks_data)
        keys = jwks.get("keys", [jwks] if "n" in jwks else [])
        for key in keys:
            n_value = key.get("n", "")
            if len(n_value) >= _MIN_RSA_MODULUS_LEN:
                return True
        return False
    except (json.JSONDecodeError, TypeError):
        return False


def _fetch_f5_jwks() -> dict[str, Any]:
    """
    Fetch the official F5 JWKS from the public API endpoint.

    Returns the parsed JWKS dict. Raises QKViewError on failure.
    """
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(F5_JWKS_URL, headers={"User-Agent": "BNK-Forge"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            parsed: dict[str, Any] = json.loads(data)
            return parsed
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        raise QKViewError(
            f"Failed to fetch F5 JWKS from {F5_JWKS_URL}: {e}"
        )


def _detect_cwc_operator_namespace(api_client: k8s_client.ApiClient) -> str:
    """
    Detect the namespace where CPCL ConfigMaps live.

    The FLO Helm chart creates cpcl-key-cm and cpcl-config-cm in the
    operator namespace (typically f5-operator), NOT the CWC namespace
    (f5-utils).  Search both.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)
    # Check common operator namespaces first
    for ns in ("f5-operator", "f5-utils"):
        try:
            core_v1.read_namespaced_config_map(CPCL_KEY_CM, ns)
            return ns
        except k8s_client.rest.ApiException:
            continue

    # Broader search across all BNK namespaces
    for ns in _BNK_NAMESPACES:
        try:
            core_v1.read_namespaced_config_map(CPCL_KEY_CM, ns)
            return ns
        except k8s_client.rest.ApiException:
            continue

    raise QKViewError(
        f"ConfigMap '{CPCL_KEY_CM}' not found in any BNK namespace. "
        "Is FLO installed?"
    )


def ensure_valid_jwks(
    api_client: k8s_client.ApiClient,
    operator_ns: str | None = None,
) -> dict[str, Any]:
    """
    Ensure the cpcl-key-cm ConfigMap contains valid JWKS data.

    The FLO Helm chart creates this ConfigMap with placeholder values
    ("n": "...", "x5c": ["...","...","...","..."]) when license.modulus
    and license.x5c are not set in the Helm values.  CWC reads this
    ConfigMap to verify JWT signatures — if the JWKS is invalid, ALL
    licensing operations fail with "illegal base64 data at input byte 0".

    This function:
      1. Reads cpcl-key-cm
      2. Checks if the RSA modulus is a valid length (not placeholder)
      3. If invalid, fetches the real JWKS from F5's public API
      4. Patches the ConfigMap with the real JWKS

    Idempotent — safe to call before every licensing operation.
    """
    if not operator_ns:
        operator_ns = _detect_cwc_operator_namespace(api_client)

    cm_data = _read_configmap(api_client, CPCL_KEY_CM, operator_ns)
    if cm_data is None:
        return {
            "status": "not_found",
            "message": f"ConfigMap '{CPCL_KEY_CM}' not found in '{operator_ns}'",
        }

    jwt_key_data = cm_data.get("jwt.key", "")
    if _jwks_has_valid_modulus(jwt_key_data):
        logger.debug(f"JWKS in '{CPCL_KEY_CM}' is valid — no patch needed")
        return {"status": "valid", "namespace": operator_ns}

    # JWKS is invalid (placeholder or missing) — fetch real keys from F5
    logger.warning(
        f"JWKS in '{CPCL_KEY_CM}' has placeholder data — fetching real keys from F5"
    )
    try:
        real_jwks = _fetch_f5_jwks()
    except QKViewError:
        logger.warning("Cannot fetch JWKS from F5 API — using hardcoded fallback")
        real_jwks = F5_STATIC_JWKS

    # Patch the ConfigMap with the real JWKS
    new_data = {"jwt.key": json.dumps(real_jwks, indent=2)}
    _patch_configmap(api_client, CPCL_KEY_CM, operator_ns, new_data)

    return {
        "status": "patched",
        "namespace": operator_ns,
        "message": "Replaced placeholder JWKS with real F5 public keys",
    }


def _patch_cpcl_config_jwt(
    api_client: k8s_client.ApiClient,
    jwt_data: str,
    operator_ns: str | None = None,
) -> dict[str, Any]:
    """
    Ensure the cpcl-config-cm ConfigMap contains the correct JWT.

    The FLO Helm chart creates this ConfigMap from the license.jwt Helm
    value.  During a license switch/renewal, the new JWT must be written
    here so that if CWC restarts, it reads the updated JWT.

    Args:
        jwt_data: Raw JWT string (starts with eyJ...)
        operator_ns: Namespace where ConfigMap lives (auto-detected if None)
    """
    if not operator_ns:
        operator_ns = _detect_cwc_operator_namespace(api_client)

    cm_data = _read_configmap(api_client, CPCL_CONFIG_CM, operator_ns)
    if cm_data is None:
        logger.warning(
            f"ConfigMap '{CPCL_CONFIG_CM}' not found in '{operator_ns}' — "
            "skipping JWT patch (CWC may not use ConfigMap-based config)"
        )
        return {"status": "not_found", "namespace": operator_ns}

    # Check if JWT is already current
    current_jwt = cm_data.get("jwt", "").strip()
    if current_jwt == jwt_data.strip():
        return {"status": "already_current", "namespace": operator_ns}

    # Patch with new JWT
    _patch_configmap(
        api_client, CPCL_CONFIG_CM, operator_ns,
        {**cm_data, "jwt": jwt_data.strip()},
    )
    return {
        "status": "patched",
        "namespace": operator_ns,
        "message": "Updated JWT in cpcl-config-cm",
    }


# ---------------------------------------------------------------------------
# License renewal — three tiers
# ---------------------------------------------------------------------------


def _delete_cpcl_state_secrets(
    api_client: k8s_client.ApiClient,
    cwc_namespace: str,
) -> dict[str, Any]:
    """
    Delete all CPCL state secrets from the CWC namespace.

    This resets the CWC licensing state machine so a new JWT can be
    applied via /reactivate.  Missing secrets (404) are silently
    ignored — it's normal for some secrets not to exist depending on
    how far the previous license cycle progressed.

    Returns a summary dict with deleted/skipped counts.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)
    deleted = []
    skipped = []

    for secret_name in CPCL_STATE_SECRETS:
        try:
            core_v1.delete_namespaced_secret(secret_name, cwc_namespace)
            deleted.append(secret_name)
            logger.debug(f"Deleted CPCL secret: {secret_name}")
        except k8s_client.rest.ApiException as e:
            if e.status == 404:
                skipped.append(secret_name)
            else:
                # Log but don't fail — best-effort cleanup
                logger.warning(
                    f"Failed to delete CPCL secret '{secret_name}': "
                    f"{e.status} {e.reason}"
                )
                skipped.append(secret_name)

    logger.info(
        f"CPCL cleanup: {len(deleted)} deleted, {len(skipped)} skipped "
        f"in namespace '{cwc_namespace}'"
    )
    return {"deleted": deleted, "skipped": skipped}


def switch_license(
    k8s_service: KubernetesService, cluster_id: int, jwt_data: str
) -> dict[str, Any]:
    """
    Clean license switch using CWC REST APIs — no pod restart, no
    secret deletion.

    This is the preferred renewal path for eval→paid, paid→paid, or
    JWT resubmission.  It uses the CWC /reactivate API which is
    designed for exactly this purpose.

    Steps:
      1. Detect namespaces (CWC + operator)
      2. Ensure cpcl-key-cm has valid JWKS (fix placeholder if needed)
      3. JWT shape pre-check (fail fast on garbage before touching cluster)
      4. GET /status — verify CWC is reachable and capture pre-state
      5. POST /reactivate — send new JWT to CWC
      6. GET /status — verify activation succeeded (gate success on delta)
      7. Patch cpcl-config-cm with validated JWT (only on confirmed success)

    Per F5 docs, switching license is allowed:
      - eval → paid
      - paid → paid
    But NOT eval → eval.

    No CWC downtime.  Telemetry history is preserved.
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    steps: list[dict[str, Any]] = []

    # Step 1: Detect namespaces
    cwc_ns = _detect_cwc_namespace(api_client)
    steps.append({"step": "detect_namespace", "status": "ok", "namespace": cwc_ns})

    try:
        operator_ns = _detect_cwc_operator_namespace(api_client)
    except QKViewError:
        operator_ns = cwc_ns  # Fallback: same namespace
    steps.append({"step": "detect_operator_ns", "status": "ok", "namespace": operator_ns})

    # Step 2: Ensure JWKS is valid
    try:
        jwks_result = ensure_valid_jwks(api_client, operator_ns)
        steps.append({
            "step": "ensure_jwks",
            "status": "ok",
            "detail": jwks_result.get("status"),
        })
    except QKViewError as e:
        # Non-fatal — JWKS might already be correct or CWC might not
        # use the ConfigMap (some versions read from secret instead)
        logger.warning(f"JWKS validation failed (non-fatal): {e}")
        steps.append({"step": "ensure_jwks", "status": "warning", "error": str(e)})

    # Step 3: JWT shape pre-check — fail fast before touching the cluster
    jwt_shape_error = _validate_jwt_shape(jwt_data)
    if jwt_shape_error:
        steps.append({"step": "jwt_shape_check", "status": "error", "error": jwt_shape_error})
        return {
            "success": False,
            "error_message": jwt_shape_error,
            "steps": steps,
        }
    steps.append({"step": "jwt_shape_check", "status": "ok"})

    # Step 4: Check current license status (verify CWC is reachable, capture pre-state)
    normalized_pre: dict[str, Any] = {}
    try:
        pre_status_raw = _cwc_request(api_client, "GET", "/status")
        pre_status: dict[str, Any] = pre_status_raw if isinstance(pre_status_raw, dict) else {}
        normalized_pre = _normalize_cwc_status(pre_status)
        steps.append({
            "step": "pre_check_status",
            "status": "ok",
            "license_state": normalized_pre.get("license_state", "unknown"),
        })
    except QKViewError as e:
        steps.append({"step": "pre_check_status", "status": "error", "error": str(e)})
        return {
            "success": False,
            "error_message": (
                f"CWC is not reachable — cannot perform license switch: {e}. "
                "Use force=true for nuclear renewal (restarts CWC pod)."
            ),
            "steps": steps,
        }

    # Step 5: Send new JWT via /reactivate
    activate_result: dict[str, Any] = {}
    try:
        raw = _cwc_request(
            api_client, "POST", "/reactivate", raw_body=jwt_data,
        )
        activate_result = raw if isinstance(raw, dict) else {}
        steps.append({"step": "reactivate", "status": "ok"})
    except QKViewError as e:
        steps.append({"step": "reactivate", "status": "error", "error": str(e)})
        return {
            "success": False,
            "error_message": (
                f"CWC /reactivate failed: {e}. "
                "The JWT may be invalid, or CWC may need a force renewal."
            ),
            "steps": steps,
        }

    # Step 6: Verify new license status — gate success on observable state change.
    # CWC's SwitchingStatus often reports "Device Registration In Progress" for
    # a couple of seconds after /reactivate before settling to Failed+Error or
    # Active. Poll briefly so we read the final outcome, not the transient.
    normalized_post: dict[str, Any] = {}
    status_result: dict[str, Any] = {}
    try:
        for attempt in range(4):
            raw = _cwc_request(api_client, "GET", "/status")
            status_result = raw if isinstance(raw, dict) else {}
            normalized_post = _normalize_cwc_status(status_result)
            switch_state, switch_error = _extract_switch_error(status_result)
            still_processing = (
                isinstance(switch_state, str)
                and switch_state.lower() == "device registration in progress"
                and not switch_error
            )
            if not still_processing:
                break
            if attempt < 3:
                time.sleep(2)
        steps.append({
            "step": "verify_status",
            "status": "ok",
            "license_state": normalized_post.get("license_state", "unknown"),
            "switch_state": switch_state,
            "polls": attempt + 1,
        })
    except QKViewError as e:
        logger.warning(f"Post-switch status check failed: {e}")
        steps.append({"step": "verify_status", "status": "warning", "error": str(e)})

    accepted = (
        (normalized_post.get("digital_asset_id") and normalized_post["digital_asset_id"] != normalized_pre.get("digital_asset_id"))
        or (normalized_post.get("license_expiry_date") and normalized_post["license_expiry_date"] != normalized_pre.get("license_expiry_date"))
        or (
            normalized_post.get("license_state", "").lower() in {"active", "licensed", "verification complete"}
            and normalized_post.get("license_state") != normalized_pre.get("license_state")
        )
    )

    if not accepted:
        return {
            "success": False,
            "error_message": _format_switch_failure_message(
                normalized_pre.get("license_state"),
                normalized_post.get("license_state"),
                status_result,
            ),
            "license_state_pre": normalized_pre.get("license_state"),
            "license_state_post": normalized_post.get("license_state"),
            "steps": steps,
            "activate_result": activate_result,
            "license_status": status_result,
        }

    # Step 7: Persist the validated JWT to cpcl-config-cm (only after CWC confirmed it)
    try:
        jwt_patch_result = _patch_cpcl_config_jwt(api_client, jwt_data, operator_ns)
        steps.append({
            "step": "patch_jwt_configmap",
            "status": "ok",
            "detail": jwt_patch_result.get("status"),
        })
    except QKViewError as e:
        logger.warning(f"JWT ConfigMap patch failed (non-fatal): {e}")
        steps.append({"step": "patch_jwt_configmap", "status": "warning", "error": str(e)})

    return {
        "success": True,
        "steps": steps,
        "activate_result": activate_result,
        "license_status": status_result,
        "license_state_pre": normalized_pre.get("license_state"),
        "license_state_post": normalized_post.get("license_state"),
        "digital_asset_id_post": normalized_post.get("digital_asset_id"),
    }


def force_renew_license(
    k8s_service: KubernetesService, cluster_id: int, jwt_data: str
) -> dict[str, Any]:
    """
    Nuclear license renewal — deletes CPCL state, restarts CWC pod,
    reactivates with new JWT.

    Use this only when switch_license() fails because CWC is in a bad
    state.  This destroys telemetry history and causes brief licensing
    downtime (~2 min).

    Steps:
      1. Detect namespaces
      2. Ensure cpcl-key-cm has valid JWKS (fix placeholder if needed)
      3. JWT shape pre-check (fail fast before triggering 2-min CWC restart)
      4. Delete all CPCL state secrets (reset licensing state machine)
      5. Restart the CWC pod (picks up clean ConfigMaps)
      6. Clean up stale bnk-forge-agent pods
      7. Send new JWT to CWC /reactivate
      8. Verify the new license status — gate success on observable delta
      9. Patch cpcl-config-cm with validated JWT (only on confirmed success)
    """
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    steps: list[dict[str, Any]] = []

    # Step 1: Detect namespaces
    cwc_ns = _detect_cwc_namespace(api_client)
    steps.append({"step": "detect_namespace", "status": "ok", "namespace": cwc_ns})

    try:
        operator_ns = _detect_cwc_operator_namespace(api_client)
    except QKViewError:
        operator_ns = cwc_ns
    steps.append({"step": "detect_operator_ns", "status": "ok", "namespace": operator_ns})

    # Step 2: Ensure JWKS is valid (BEFORE restart so new CWC pod reads good data)
    try:
        jwks_result = ensure_valid_jwks(api_client, operator_ns)
        steps.append({
            "step": "ensure_jwks",
            "status": "ok",
            "detail": jwks_result.get("status"),
        })
    except QKViewError as e:
        logger.warning(f"JWKS validation failed (continuing anyway): {e}")
        steps.append({"step": "ensure_jwks", "status": "warning", "error": str(e)})

    # Step 3: JWT shape pre-check — bail out before triggering pod restart on garbage
    jwt_shape_error = _validate_jwt_shape(jwt_data)
    if jwt_shape_error:
        steps.append({"step": "jwt_shape_check", "status": "error", "error": jwt_shape_error})
        return {
            "success": False,
            "error_message": jwt_shape_error,
            "steps": steps,
        }
    steps.append({"step": "jwt_shape_check", "status": "ok"})

    # Step 4: Delete CPCL state secrets
    cleanup = _delete_cpcl_state_secrets(api_client, cwc_ns)
    steps.append({
        "step": "cpcl_cleanup",
        "status": "ok",
        "deleted_count": len(cleanup["deleted"]),
        "skipped_count": len(cleanup["skipped"]),
    })

    # Step 5: Restart CWC pod
    try:
        old_pod = _restart_cwc_pod(api_client, cwc_ns)
        steps.append({
            "step": "cwc_restart",
            "status": "ok",
            "old_pod": old_pod,
        })
    except QKViewError as e:
        steps.append({"step": "cwc_restart", "status": "error", "error": str(e)})
        return {
            "success": False,
            "error_message": f"CWC restart failed: {e}",
            "steps": steps,
        }

    # Step 6: Clean up stale bnk-forge-agent pod(s)
    try:
        _cleanup_all_client_pods(api_client, cwc_ns)
        steps.append({"step": "agent_pod_cleanup", "status": "ok"})
    except Exception as e:
        logger.warning(f"Agent pod cleanup failed (non-fatal): {e}")
        steps.append({"step": "agent_pod_cleanup", "status": "warning", "error": str(e)})

    # Step 7: Activate with new JWT. The pod-readiness gate in _restart_cwc_pod
    # waits for Ready=True, but the CWC service endpoint may still take a few
    # extra seconds to propagate through kube-proxy. Retry on connection-refused
    # errors specifically; bail immediately on any other QKViewError.
    activate_result: dict[str, Any] = {}
    last_conn_error: QKViewError | None = None
    for attempt in range(6):
        try:
            raw = _cwc_request(
                api_client, "POST", "/reactivate", raw_body=jwt_data,
            )
            activate_result = raw if isinstance(raw, dict) else {}
            steps.append({"step": "activate", "status": "ok", "attempts": attempt + 1})
            break
        except QKViewError as e:
            if "connection failed" in str(e).lower() or "could not connect" in str(e).lower():
                last_conn_error = e
                if attempt < 5:
                    time.sleep(5)
                    continue
            steps.append({"step": "activate", "status": "error", "error": str(e)})
            return {
                "success": False,
                "error_message": f"License activation failed after CWC restart: {e}",
                "steps": steps,
            }
    else:
        steps.append({"step": "activate", "status": "error", "error": str(last_conn_error)})
        return {
            "success": False,
            "error_message": (
                f"CWC pod restarted but the licensing service never accepted connections "
                f"on port 38081 within ~30s. Last error: {last_conn_error}"
            ),
            "steps": steps,
        }

    # Step 8: Verify new license status — gate success on observable state change.
    # After a force renewal the cluster starts from a clean slate, so we compare
    # against empty pre-state. Poll briefly to let CWC settle out of the
    # transient "Device Registration In Progress" state before we read.
    normalized_post: dict[str, Any] = {}
    status_result: dict[str, Any] = {}
    try:
        for attempt in range(4):
            raw = _cwc_request(api_client, "GET", "/status")
            status_result = raw if isinstance(raw, dict) else {}
            normalized_post = _normalize_cwc_status(status_result)
            switch_state, switch_error = _extract_switch_error(status_result)
            still_processing = (
                isinstance(switch_state, str)
                and switch_state.lower() == "device registration in progress"
                and not switch_error
            )
            if not still_processing:
                break
            if attempt < 3:
                time.sleep(2)
        steps.append({
            "step": "verify",
            "status": "ok",
            "license_state": normalized_post.get("license_state", "unknown"),
            "switch_state": switch_state,
            "polls": attempt + 1,
        })
    except QKViewError as e:
        logger.warning(f"License status check after renewal failed: {e}")
        steps.append({"step": "verify", "status": "warning", "error": str(e)})

    accepted = normalized_post.get("license_state", "").lower() in {
        "active", "licensed", "verification complete",
    }

    if not accepted:
        switch_state, switch_error = _extract_switch_error(status_result)
        if switch_error:
            error_message = (
                f"CWC rejected the JWT even after CPCL reset (state={switch_state!r}): {switch_error}"
            )
        else:
            error_message = (
                f"CWC restarted and accepted the JWT but license state did not reach active "
                f"(post={normalized_post.get('license_state')!r}). "
                "JWT may be invalid or for the wrong cluster."
            )
        return {
            "success": False,
            "error_message": error_message,
            "license_state_post": normalized_post.get("license_state"),
            "steps": steps,
            "activate_result": activate_result,
            "license_status": status_result,
        }

    # Step 9: Persist the validated JWT — only after CWC confirmed acceptance
    try:
        jwt_patch_result = _patch_cpcl_config_jwt(api_client, jwt_data, operator_ns)
        steps.append({
            "step": "patch_jwt_configmap",
            "status": "ok",
            "detail": jwt_patch_result.get("status"),
        })
    except QKViewError as e:
        logger.warning(f"JWT ConfigMap patch failed (non-fatal): {e}")
        steps.append({"step": "patch_jwt_configmap", "status": "warning", "error": str(e)})

    return {
        "success": True,
        "steps": steps,
        "activate_result": activate_result,
        "license_status": status_result,
        "license_state_post": normalized_post.get("license_state"),
        "digital_asset_id_post": normalized_post.get("digital_asset_id"),
    }


def renew_license(
    k8s_service: KubernetesService,
    cluster_id: int,
    jwt_data: str,
    force: bool = False,
) -> dict[str, Any]:
    """
    License renewal dispatcher — routes to the appropriate tier.

    Default (force=False): Uses switch_license() — clean API-based path
    that preserves telemetry and avoids CWC downtime.

    Force (force=True): Uses force_renew_license() — nuclear path that
    resets all CPCL state and restarts CWC.  Use when CWC is unresponsive
    or in a broken state.
    """
    if force:
        return force_renew_license(k8s_service, cluster_id, jwt_data)
    return switch_license(k8s_service, cluster_id, jwt_data)
