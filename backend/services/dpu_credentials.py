"""Centralised DPU BMC credential resilience.

Pattern (Phase 3 of the DPU Provisioning plan):

  1. Attempt the operation using the user-configured password.
  2. If the BMC rejects it, retry once with the Nvidia factory default
     (`0penBmc`).
  3. On successful fallback, asynchronously PATCH the BMC's `root`
     account to the user-configured password so subsequent calls
     authenticate normally.

Used by the Reset action (Redfish POST), the DPU OS probe (SSH to BMC),
and — eventually — the Phase 4 Flash engine.

The auto-PATCH is non-fatal: if it fails (BMC cert issue, transient
network error, role restriction) we log a warning and keep the fallback
result, so the caller's operation still succeeds.
"""

import logging

import paramiko
import requests
from paramiko.ssh_exception import AuthenticationException
from requests.auth import HTTPBasicAuth

from services.dpu_tunnel import bmc_http_request, ssh_connect_to_bmc

logger = logging.getLogger(__name__)

# Nvidia default `root` password on a factory-fresh BlueField-3 BMC.
NVIDIA_DEFAULT_BMC_PASSWORD = "0penBmc"

# Redfish endpoint for the `root` account on a BlueField BMC.
_ROOT_ACCOUNT_URL_TMPL = "https://{host}/redfish/v1/AccountService/Accounts/root"
_ROOT_ACCOUNT_PATH = "/redfish/v1/AccountService/Accounts/root"


# ── Redfish POST with fallback ─────────────────────────────────────────────

def redfish_post_with_bmc_fallback(
    host: str,
    user: str,
    configured_password: str,
    *,
    url: str,
    json_body: dict | None = None,
    timeout: int = 15,
    jumphost_cred: dict | None = None,
) -> requests.Response:
    """POST to a Redfish URL with BMC creds; fall back to 0penBmc on 401.

    When `jumphost_cred` is set the call is tunneled through the project's
    jumphost via paramiko/TLS-over-MemoryBIO. On successful fallback the
    configured password is PATCHed back onto the root account so
    subsequent calls don't have to fall back again.
    """
    # Parse `url` into (path) for the tunnel helper; the direct path uses
    # the full URL as before.
    path = _path_from_url(url)

    def _do(pw: str):
        if jumphost_cred is None:
            try:
                return requests.post(
                    url, auth=HTTPBasicAuth(user, pw), json=json_body,
                    verify=False, timeout=timeout,
                )
            except requests.RequestException as exc:
                raise RedfishError(f"Redfish POST failed: {exc}") from exc
        # Tunneled path
        r = bmc_http_request(
            bmc_ip=host, method="POST", path=path,
            json_body=json_body, auth=(user, pw),
            jumphost_cred=jumphost_cred, timeout=timeout,
        )
        if r is None:
            raise RedfishError(f"Redfish POST via jumphost {jumphost_cred['host']} failed")
        return _TunneledResponse(r[0], r[1])

    resp = _do(configured_password)
    if resp.status_code != 401 or configured_password == NVIDIA_DEFAULT_BMC_PASSWORD:
        return resp

    logger.info(
        "Redfish POST %s 401 with configured password; retrying with Nvidia default", url,
    )
    fallback_resp = _do(NVIDIA_DEFAULT_BMC_PASSWORD)
    if _is_auth_ok(fallback_resp):
        patch_bmc_root_password(
            host, user, NVIDIA_DEFAULT_BMC_PASSWORD, configured_password,
            jumphost_cred=jumphost_cred,
        )
    return fallback_resp


def _path_from_url(url: str) -> str:
    """Extract the path + query portion of an HTTPS URL."""
    if "://" not in url:
        return url
    after_scheme = url.split("://", 1)[1]
    slash = after_scheme.find("/")
    return after_scheme[slash:] if slash >= 0 else "/"


class _TunneledResponse:
    """Minimal `requests.Response` stand-in for tunneled calls — exposes
    just the two attributes our callers read (status_code, text)."""

    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self._body


# ── SSH connect with fallback ──────────────────────────────────────────────

def ssh_connect_bmc_with_fallback(
    host: str,
    user: str,
    configured_password: str,
    *,
    timeout: int = 15,
    jumphost_cred: dict | None = None,
) -> paramiko.SSHClient:
    """Open an SSH session to the BMC; fall back to 0penBmc on auth failure.

    When `jumphost_cred` is set the BMC connection is tunneled through
    the project's jumphost via paramiko direct-tcpip.

    On successful fallback the configured password is PATCHed back onto
    the root account. Non-auth failures (connect, host-key) propagate.

    The caller owns the returned client and is responsible for closing it.
    """
    try:
        return ssh_connect_to_bmc(
            host, username=user, password=configured_password,
            jumphost_cred=jumphost_cred, timeout=timeout,
        )
    except AuthenticationException:
        if configured_password == NVIDIA_DEFAULT_BMC_PASSWORD:
            raise
        logger.info(
            "BMC SSH auth for %s failed with configured password; retrying with Nvidia default",
            host,
        )
        client = ssh_connect_to_bmc(
            host, username=user, password=NVIDIA_DEFAULT_BMC_PASSWORD,
            jumphost_cred=jumphost_cred, timeout=timeout,
        )
        # Fire-and-forget PATCH; SSH session stays open regardless of outcome.
        patch_bmc_root_password(
            host, user, NVIDIA_DEFAULT_BMC_PASSWORD, configured_password,
            jumphost_cred=jumphost_cred,
        )
        return client


# ── Implementation details ────────────────────────────────────────────────

class RedfishError(Exception):
    """Raised when a Redfish call fails at the HTTP/transport layer."""


def _is_auth_ok(resp: requests.Response) -> bool:
    # 2xx = success; 3xx = redirect (rare for Redfish actions). 401/403 = auth fail.
    return 200 <= resp.status_code < 400


def patch_bmc_root_password(
    host: str,
    user: str,
    old_password: str,
    new_password: str,
    *,
    jumphost_cred: dict | None = None,
) -> None:
    """PATCH the BMC root account's password. Best-effort; logs on failure.

    Honours the project jumphost when provided.
    """
    if old_password == new_password:
        return

    if jumphost_cred is None:
        url = _ROOT_ACCOUNT_URL_TMPL.format(host=host)
        try:
            resp = requests.patch(
                url,
                auth=HTTPBasicAuth(user, old_password),
                json={"Password": new_password},
                verify=False,
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Could not PATCH BMC %s root password after fallback: %s", host, exc,
            )
            return
        status_code, body = resp.status_code, resp.text[:200]
    else:
        r = bmc_http_request(
            bmc_ip=host, method="PATCH", path=_ROOT_ACCOUNT_PATH,
            json_body={"Password": new_password}, auth=(user, old_password),
            jumphost_cred=jumphost_cred, timeout=15,
        )
        if r is None:
            logger.warning(
                "Could not PATCH BMC %s root password via jumphost %s",
                host, jumphost_cred.get("host"),
            )
            return
        status_code, body = r[0], r[1][:200].decode("utf-8", errors="replace")

    if 200 <= status_code < 400:
        logger.info(
            "BMC %s root password PATCHed back to configured value after 0penBmc fallback",
            host,
        )
    else:
        logger.warning(
            "PATCH BMC %s root password returned HTTP %s — keeping Nvidia default; "
            "operator may need to rotate manually. Body: %s",
            host, status_code, body,
        )
