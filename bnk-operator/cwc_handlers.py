"""
CWC Command Handlers — CWC REST API communication from inside the cluster.

K8S-UX-010: The operator pod serves as the single in-cluster agent for all
cluster operations including CWC QKView diagnostics and licensing.

The CWC REST API (port auto-discovered from K8s Service) requires:
  - mTLS client certificate (from cert-manager, mounted at CWC_CERT_DIR)
  - Bearer token (from cwc-auth-token secret)

Commands:
  cwc_request     — Generic CWC REST API call (method, path, body)
  cwc_setup_certs — One-time cert-manager setup (create certs, restart CWC)
  cwc_check_status— Check if CWC is available and certs are configured
  cwc_qkview_list — GET /v1/qkview
  cwc_qkview_create — POST /v1/qkview
  cwc_qkview_get  — GET /v1/qkview/{id}
  cwc_qkview_download — GET /v1/qkview/{id}/download (returns base64 binary)
  cwc_qkview_delete — DELETE /v1/qkview/{id}
  cwc_qkview_cancel — POST /v1/qkview/{id}/cancel
  cwc_license_status — GET /status
  cwc_license_report — GET /report
  cwc_license_activate — POST /reactivate
  cwc_license_receipt — POST /receipt

On fresh clusters (no CWC certs), only cwc_setup_certs and cwc_check_status
work. After setup, all CWC commands are available.
"""

import base64
import json
import logging
import os
import ssl
from typing import Any, Callable, Coroutine, Dict, Optional

import aiohttp
import kr8s

logger = logging.getLogger("bnk-operator.cwc")

# CWC service coordinates
CWC_NAMESPACE = "f5-utils"
CWC_SERVICE = "f5-spk-cwc"
CWC_REST_PORT_NAME = "cwc-rest"
CWC_AUTH_TOKEN_SECRET = "cwc-auth-token"

# cert-manager setup constants
CLUSTER_ISSUER_NAME = "bnk-ca-cluster-issuer"
CWC_SERVER_CERT_NAME = "bnk-forge-cwc-rest-api"
CWC_SERVER_CERT_SECRET = "bnk-forge-cwc-rest-api-tls"
CWC_CLIENT_CERT_NAME = "bnk-forge-cwc-client"
CWC_CLIENT_CERT_SECRET = "bnk-forge-cwc-client-tls"
CWC_LICENSE_CERTS_SECRET = "cwc-license-certs"

CERT_DURATION = "8760h"  # 1 year
CERT_RENEW_BEFORE = "720h"  # 30 days

# Type alias
OutputCallback = Callable[[str], Coroutine]

# Default cert mount path (can be overridden via CWC_CERT_DIR env)
CWC_CERT_DIR = os.environ.get("CWC_CERT_DIR", "/certs/cwc")


class CWCHandlers:
    """
    Handles CWC REST API commands. Uses aiohttp with mTLS for direct
    HTTPS calls to the CWC service inside the cluster.
    """

    def __init__(self):
        self._api: Optional[kr8s.asyncio.Api] = None
        self._cwc_port: Optional[int] = None

    async def _get_api(self) -> kr8s.asyncio.Api:
        """Get or create kr8s API client (ServiceAccount auth)."""
        if self._api is None:
            self._api = await kr8s.asyncio.api()
        return self._api

    # ------------------------------------------------------------------
    # Internal: CWC connectivity
    # ------------------------------------------------------------------

    def _certs_available(self) -> bool:
        """Check if CWC client certs are mounted."""
        cert_path = os.path.join(CWC_CERT_DIR, "tls.crt")
        key_path = os.path.join(CWC_CERT_DIR, "tls.key")
        return os.path.isfile(cert_path) and os.path.isfile(key_path)

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create an SSL context with mTLS client certs."""
        ctx = ssl.create_default_context()
        # Don't verify CWC server cert (self-signed / internal CA)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        cert_path = os.path.join(CWC_CERT_DIR, "tls.crt")
        key_path = os.path.join(CWC_CERT_DIR, "tls.key")
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        return ctx

    async def _get_cwc_port(self) -> int:
        """Auto-discover the CWC REST API port from the K8s Service spec."""
        if self._cwc_port is not None:
            return self._cwc_port

        api = await self._get_api()
        try:
            services = await api.async_get(
                "services", CWC_SERVICE, namespace=CWC_NAMESPACE
            )
            svc = services[0] if isinstance(services, list) else services
            for port in svc.spec.get("ports", []):
                if port.get("name") == CWC_REST_PORT_NAME:
                    self._cwc_port = port["port"]
                    return self._cwc_port
            # Fallback: first port
            ports = svc.spec.get("ports", [])
            if ports:
                self._cwc_port = ports[0]["port"]
                return self._cwc_port
        except Exception as e:
            logger.error(f"Failed to discover CWC port: {e}")

        # Hard fallback
        self._cwc_port = 38081
        return self._cwc_port

    async def _get_bearer_token(self) -> Optional[str]:
        """Read the CWC admin Bearer token from the cwc-auth-token K8s secret."""
        api = await self._get_api()
        try:
            secrets = await api.async_get(
                "secrets", CWC_AUTH_TOKEN_SECRET, namespace=CWC_NAMESPACE
            )
            secret = secrets[0] if isinstance(secrets, list) else secrets
            data = secret.data or {}
            if "token" in data:
                return base64.b64decode(data["token"]).decode("utf-8").strip()
        except Exception:
            logger.debug(f"Admin token secret '{CWC_AUTH_TOKEN_SECRET}' not found")
        return None

    async def _cwc_http(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        raw_body: Optional[str] = None,
        stream: bool = False,
    ) -> dict | bytes:
        """
        Make an HTTP request to the CWC REST API with mTLS + Bearer token.

        Args:
            body: JSON body (sent as application/json)
            raw_body: Raw string body (sent as-is, for /reactivate and /receipt
                      which expect raw JWT data per F5 docs)
            stream: If True, return raw bytes (for binary downloads)
        """
        if not self._certs_available():
            raise RuntimeError(
                "CWC client certificates not mounted. "
                "Run cwc_setup_certs first, then restart the operator with cert volume."
            )

        port = await self._get_cwc_port()
        url = f"https://{CWC_SERVICE}.{CWC_NAMESPACE}:{port}{path}"
        token = await self._get_bearer_token()

        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        ssl_ctx = self._create_ssl_context()
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)

        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                kwargs: dict[str, Any] = {"headers": headers}
                if raw_body is not None:
                    # /reactivate and /receipt expect raw JWT data, not JSON
                    kwargs["data"] = raw_body
                elif body is not None:
                    kwargs["json"] = body

                async with session.request(method, url, **kwargs) as resp:
                    if stream:
                        data = await resp.read()
                        if resp.status >= 400:
                            raise RuntimeError(
                                f"CWC API error ({resp.status}): {data[:500]}"
                            )
                        return data

                    text = await resp.text()
                    if resp.status >= 400:
                        raise RuntimeError(
                            f"CWC API error ({resp.status}): {text[:500]}"
                        )

                    if not text.strip():
                        return {}

                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, list):
                            return {"items": parsed}
                        return parsed
                    except json.JSONDecodeError:
                        return {"raw": text[:1000]}
        except aiohttp.ClientError as e:
            raise RuntimeError(f"CWC connection failed: {e}")

    # ------------------------------------------------------------------
    # Command: cwc_request — generic CWC API call
    # ------------------------------------------------------------------

    async def cwc_request(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Generic CWC REST API call.

        Payload:
          method: str      — HTTP method (GET, POST, DELETE)
          path: str        — URL path (e.g., /v1/qkview)
          body: dict       — optional JSON body
          raw_body: str    — optional raw string body (for /reactivate, /receipt
                             which expect raw JWT data per F5 docs)
          stream: bool     — if True, returns base64-encoded binary
        """
        method = payload.get("method", "GET")
        path = payload.get("path", "/")
        body = payload.get("body")
        raw_body = payload.get("raw_body")
        stream = payload.get("stream", False)

        if on_output:
            await on_output(f"CWC {method} {path}...")

        try:
            result = await self._cwc_http(
                method, path, body=body, raw_body=raw_body, stream=stream
            )
            if isinstance(result, bytes):
                if on_output:
                    await on_output(f"  Downloaded {len(result)} bytes")
                return {
                    "success": True,
                    "binary": True,
                    "data": base64.b64encode(result).decode("ascii"),
                    "size": len(result),
                }
            if on_output:
                await on_output(f"  OK")
            return {"success": True, **result}
        except Exception as e:
            if on_output:
                await on_output(f"  Failed: {e}")
            return {"success": False, "error_message": str(e)}

    # ------------------------------------------------------------------
    # Command: cwc_check_status — check CWC availability
    # ------------------------------------------------------------------

    async def cwc_check_status(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Check whether CWC is available and certs are configured.

        Returns:
          certs_mounted: bool    — are client certs available on disk
          cwc_service_found: bool — does the CWC K8s Service exist
          cwc_reachable: bool    — can we actually reach the CWC REST API
          setup_complete: bool   — is cert-manager setup done
          cert_manager_available: bool
        """
        if on_output:
            await on_output("Checking CWC status...")

        result: Dict[str, Any] = {
            "success": True,
            "certs_mounted": self._certs_available(),
            "cwc_service_found": False,
            "cwc_reachable": False,
            "setup_complete": False,
            "cert_manager_available": False,
        }

        api = await self._get_api()

        # Check CWC service
        try:
            svcs = await api.async_get(
                "services", CWC_SERVICE, namespace=CWC_NAMESPACE
            )
            result["cwc_service_found"] = bool(svcs)
            if on_output:
                await on_output(f"  CWC service: {'found' if svcs else 'not found'}")
        except Exception:
            if on_output:
                await on_output("  CWC service: not found")

        # Check cert-manager ClusterIssuer
        try:
            issuers = await api.async_get(
                "clusterissuers.cert-manager.io", CLUSTER_ISSUER_NAME
            )
            result["cert_manager_available"] = bool(issuers)
        except Exception:
            pass

        # Check if setup is done (both cert secrets exist)
        try:
            server_secrets = await api.async_get(
                "secrets", CWC_SERVER_CERT_SECRET, namespace=CWC_NAMESPACE
            )
            client_secrets = await api.async_get(
                "secrets", CWC_CLIENT_CERT_SECRET, namespace=CWC_NAMESPACE
            )
            server_ok = bool(server_secrets)
            client_ok = bool(client_secrets)
            result["setup_complete"] = server_ok and client_ok
            result["server_cert_exists"] = server_ok
            result["client_cert_exists"] = client_ok
        except Exception:
            pass

        # If certs are mounted, try to reach CWC
        if result["certs_mounted"] and result["cwc_service_found"]:
            try:
                resp = await self._cwc_http("GET", "/status")
                result["cwc_reachable"] = True
                result["cwc_status"] = resp
                if on_output:
                    await on_output("  CWC API: reachable")
            except Exception as e:
                if on_output:
                    await on_output(f"  CWC API: not reachable ({e})")

        if on_output:
            await on_output(f"  Certs mounted: {result['certs_mounted']}")
            await on_output(f"  Setup complete: {result['setup_complete']}")

        return result

    # ------------------------------------------------------------------
    # Command: cwc_setup_certs — one-time cert-manager setup
    # ------------------------------------------------------------------

    async def cwc_setup_certs(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        One-time cert-manager setup for CWC (see DECISIONS.md D1).

        Uses kr8s to create cert-manager Certificate CRs, update the
        cwc-license-certs secret, and restart the CWC pod.

        After this, the operator deployment must be updated to mount
        the client cert secret as a volume.
        """
        api = await self._get_api()
        steps: list[dict] = []

        if on_output:
            await on_output("Starting CWC cert-manager setup...")

        # Step 1: Verify cert-manager ClusterIssuer
        try:
            issuers = await api.async_get(
                "clusterissuers.cert-manager.io", CLUSTER_ISSUER_NAME
            )
            if not issuers:
                return {
                    "success": False,
                    "error_message": (
                        f"cert-manager ClusterIssuer '{CLUSTER_ISSUER_NAME}' not found. "
                        "Install cert-manager and create the ClusterIssuer first."
                    ),
                }
        except Exception as e:
            return {
                "success": False,
                "error_message": f"cert-manager check failed: {e}",
            }
        steps.append({"step": "cert_manager_check", "status": "ok"})
        if on_output:
            await on_output("  cert-manager ClusterIssuer: OK")

        # Step 2: Create server Certificate CR (skip if exists)
        try:
            existing = await api.async_get(
                "certificates.cert-manager.io",
                CWC_SERVER_CERT_NAME,
                namespace=CWC_NAMESPACE,
            )
            if existing:
                steps.append({"step": "server_cert_create", "status": "skipped"})
                if on_output:
                    await on_output("  Server Certificate: already exists, skipping")
            else:
                raise Exception("not found")
        except Exception:
            server_cert = {
                "apiVersion": "cert-manager.io/v1",
                "kind": "Certificate",
                "metadata": {
                    "name": CWC_SERVER_CERT_NAME,
                    "namespace": CWC_NAMESPACE,
                    "labels": {
                        "managed-by": "bnk-forge",
                        "bnk-forge/purpose": "cwc-rest-api",
                    },
                },
                "spec": {
                    "secretName": CWC_SERVER_CERT_SECRET,
                    "issuerRef": {
                        "name": CLUSTER_ISSUER_NAME,
                        "kind": "ClusterIssuer",
                        "group": "cert-manager.io",
                    },
                    "duration": CERT_DURATION,
                    "renewBefore": CERT_RENEW_BEFORE,
                    "usages": [
                        "server auth", "client auth",
                        "digital signature", "key encipherment",
                    ],
                    "dnsNames": [
                        CWC_SERVICE,
                        f"{CWC_SERVICE}.{CWC_NAMESPACE}",
                        f"{CWC_SERVICE}.{CWC_NAMESPACE}.svc",
                        f"{CWC_SERVICE}.{CWC_NAMESPACE}.svc.cluster.local",
                    ],
                    "commonName": f"{CWC_SERVICE}.{CWC_NAMESPACE}",
                    "privateKey": {"algorithm": "RSA", "size": 2048},
                },
            }
            await api.async_apply(server_cert, force=True)
            steps.append({"step": "server_cert_create", "status": "ok"})
            if on_output:
                await on_output("  Server Certificate: created")

        # Step 3: Wait for server cert secret
        if on_output:
            await on_output("  Waiting for server cert to be issued...")
        server_cert_data = await self._wait_for_secret(
            api, CWC_SERVER_CERT_SECRET, CWC_NAMESPACE
        )
        steps.append({"step": "server_cert_ready", "status": "ok"})

        # Step 4: Copy cert data into cwc-license-certs
        cwc_secret_body = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": CWC_LICENSE_CERTS_SECRET,
                "namespace": CWC_NAMESPACE,
                "labels": {
                    "managed-by": "bnk-forge",
                    "bnk-forge/purpose": "cwc-rest-api",
                },
            },
            "data": {
                "server-cert": server_cert_data["tls.crt"],
                "server-key": server_cert_data["tls.key"],
                "ca-root-cert": server_cert_data["ca.crt"],
            },
        }
        await api.async_apply(cwc_secret_body, force=True)
        steps.append({"step": "cwc_license_certs_updated", "status": "ok"})
        if on_output:
            await on_output("  cwc-license-certs: updated with cert-manager certs")

        # Step 5: Restart CWC pod
        if on_output:
            await on_output("  Restarting CWC pod...")
        try:
            cwc_pods = await api.async_get(
                "pods", namespace=CWC_NAMESPACE, label_selector="app=cwc"
            )
            if cwc_pods:
                pod = cwc_pods[0] if isinstance(cwc_pods, list) else cwc_pods
                pod_name = pod.metadata["name"]
                await pod.async_delete()
                if on_output:
                    await on_output(f"  Deleted CWC pod: {pod_name}")
                steps.append({
                    "step": "cwc_pod_restart",
                    "status": "ok",
                    "detail": f"Deleted: {pod_name}",
                })
            else:
                steps.append({"step": "cwc_pod_restart", "status": "skipped"})
                if on_output:
                    await on_output("  No CWC pod found to restart")
        except Exception as e:
            steps.append({
                "step": "cwc_pod_restart",
                "status": "error",
                "detail": str(e),
            })
            if on_output:
                await on_output(f"  CWC pod restart failed: {e}")

        # Step 6: Create client Certificate CR (skip if exists)
        try:
            existing = await api.async_get(
                "certificates.cert-manager.io",
                CWC_CLIENT_CERT_NAME,
                namespace=CWC_NAMESPACE,
            )
            if existing:
                steps.append({"step": "client_cert_create", "status": "skipped"})
                if on_output:
                    await on_output("  Client Certificate: already exists, skipping")
            else:
                raise Exception("not found")
        except Exception:
            client_cert = {
                "apiVersion": "cert-manager.io/v1",
                "kind": "Certificate",
                "metadata": {
                    "name": CWC_CLIENT_CERT_NAME,
                    "namespace": CWC_NAMESPACE,
                    "labels": {
                        "managed-by": "bnk-forge",
                        "bnk-forge/purpose": "cwc-client",
                    },
                },
                "spec": {
                    "secretName": CWC_CLIENT_CERT_SECRET,
                    "issuerRef": {
                        "name": CLUSTER_ISSUER_NAME,
                        "kind": "ClusterIssuer",
                        "group": "cert-manager.io",
                    },
                    "duration": CERT_DURATION,
                    "renewBefore": CERT_RENEW_BEFORE,
                    "usages": [
                        "client auth", "digital signature", "key encipherment",
                    ],
                    "dnsNames": [
                        "bnk-forge-client",
                        f"bnk-forge-client.{CWC_NAMESPACE}",
                    ],
                    "commonName": "bnk-forge-client",
                    "privateKey": {"algorithm": "RSA", "size": 2048},
                },
            }
            await api.async_apply(client_cert, force=True)
            steps.append({"step": "client_cert_create", "status": "ok"})
            if on_output:
                await on_output("  Client Certificate: created")

        # Wait for client cert
        if on_output:
            await on_output("  Waiting for client cert to be issued...")
        await self._wait_for_secret(api, CWC_CLIENT_CERT_SECRET, CWC_NAMESPACE)
        steps.append({"step": "client_cert_ready", "status": "ok"})

        if on_output:
            await on_output(
                "Setup complete. Update the operator deployment to mount "
                f"'{CWC_CLIENT_CERT_SECRET}' at {CWC_CERT_DIR}, then restart."
            )

        return {
            "success": True,
            "message": (
                "CWC cert-manager setup complete. "
                "Update operator deployment to mount the client cert secret, "
                "then restart the operator."
            ),
            "client_cert_secret": CWC_CLIENT_CERT_SECRET,
            "steps": steps,
        }

    async def _wait_for_secret(
        self,
        api: kr8s.asyncio.Api,
        secret_name: str,
        namespace: str,
        timeout_sec: int = 60,
    ) -> dict:
        """Wait for a cert-manager secret to be populated with cert data."""
        import asyncio
        import time

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                secrets = await api.async_get(
                    "secrets", secret_name, namespace=namespace
                )
                if secrets:
                    s = secrets[0] if isinstance(secrets, list) else secrets
                    data = s.data or {}
                    if "tls.crt" in data and "tls.key" in data and "ca.crt" in data:
                        return data
            except Exception:
                pass
            await asyncio.sleep(2)
        raise RuntimeError(
            f"Timed out waiting for secret '{secret_name}' ({timeout_sec}s)"
        )

    # ------------------------------------------------------------------
    # QKView convenience commands
    # ------------------------------------------------------------------

    async def cwc_qkview_list(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """List all QKView jobs."""
        return await self.cwc_request(
            {"method": "GET", "path": "/v1/qkview"}, on_output
        )

    async def cwc_qkview_create(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """Create a new QKView diagnostic tarball."""
        body = {}
        if payload.get("filename"):
            body["filename"] = payload["filename"]
        if payload.get("namespace"):
            body["namespace"] = payload["namespace"]
        if payload.get("pod_patterns"):
            body["pod_patterns"] = payload["pod_patterns"]
        if payload.get("include_core_files"):
            body["core_files"] = True
        if payload.get("core_files_max"):
            body["core_files_max"] = payload["core_files_max"]
        if payload.get("core_files_since"):
            body["core_files_since"] = payload["core_files_since"]

        return await self.cwc_request(
            {"method": "POST", "path": "/v1/qkview", "body": body or None},
            on_output,
        )

    async def cwc_qkview_get(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """Get QKView details by ID."""
        qkview_id = payload.get("qkview_id")
        if not qkview_id:
            return {"success": False, "error_message": "qkview_id is required"}
        return await self.cwc_request(
            {"method": "GET", "path": f"/v1/qkview/{qkview_id}"}, on_output
        )

    async def cwc_qkview_download(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """Download QKView tarball (returns base64-encoded binary)."""
        qkview_id = payload.get("qkview_id")
        if not qkview_id:
            return {"success": False, "error_message": "qkview_id is required"}
        return await self.cwc_request(
            {
                "method": "GET",
                "path": f"/v1/qkview/{qkview_id}/download",
                "stream": True,
            },
            on_output,
        )

    async def cwc_qkview_delete(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """Delete a QKView by ID."""
        qkview_id = payload.get("qkview_id")
        if not qkview_id:
            return {"success": False, "error_message": "qkview_id is required"}
        return await self.cwc_request(
            {"method": "DELETE", "path": f"/v1/qkview/{qkview_id}"}, on_output
        )

    async def cwc_qkview_cancel(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """Cancel a running QKView job."""
        qkview_id = payload.get("qkview_id")
        if not qkview_id:
            return {"success": False, "error_message": "qkview_id is required"}
        return await self.cwc_request(
            {"method": "POST", "path": f"/v1/qkview/{qkview_id}/cancel"},
            on_output,
        )

    # ------------------------------------------------------------------
    # Licensing convenience commands
    # ------------------------------------------------------------------

    async def cwc_license_status(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """Get CWC license + telemetry status."""
        return await self.cwc_request(
            {"method": "GET", "path": "/status"}, on_output
        )

    async def cwc_license_report(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """Get CWC telemetry report."""
        return await self.cwc_request(
            {"method": "GET", "path": "/report"}, on_output
        )

    async def cwc_license_activate(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Reactivate/switch CWC license.

        Payload:
          jwt: str — Raw JWT object (NOT JSON-wrapped). Per F5 docs,
                     /reactivate expects raw JWT data with -d <JWT>.

        See: https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/
             installing-bnk-dpu-using-f5-lifecycle-operator/installing/
             spk-cluster-license.html#reactivate-switch-license
        """
        jwt_data = payload.get("jwt", "")
        if not jwt_data:
            return {
                "success": False,
                "error_message": "jwt is required (raw JWT string for license activation)",
            }
        return await self.cwc_request(
            {"method": "POST", "path": "/reactivate", "raw_body": jwt_data},
            on_output,
        )

    async def cwc_license_receipt(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Send signed manifest (receipt) back to CWC.

        Payload:
          manifest: str — Raw manifest string (NOT JSON-wrapped). Per F5 docs,
                         /receipt expects raw manifest data without curly brackets
                         or quotes.

        See: https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/
             installing-bnk-dpu-using-f5-lifecycle-operator/installing/
             spk-cluster-license.html#send-manifest
        """
        manifest_data = payload.get("manifest", "")
        if not manifest_data:
            return {
                "success": False,
                "error_message": "manifest is required (raw manifest string from F5 telemetry server)",
            }
        return await self.cwc_request(
            {"method": "POST", "path": "/receipt", "raw_body": manifest_data},
            on_output,
        )
