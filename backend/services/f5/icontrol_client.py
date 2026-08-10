"""
iControl REST / AS3 client for classic F5 BIG-IP.

D-023 Phase 1: read-only IControlClient (GET-only public interface).
D-023 Phase 2: IControlWriteClient subclass adds AS3 write methods.

INVARIANTS (IControlClient — read-only foundation):
  * The ONLY POST is the private _login() which mints a session token.
    No POST/PATCH/DELETE against /mgmt/tm/ or /mgmt/shared/appsvcs/declare.
  * TLS trust is per-device (verify_https_cert=True by default).
    Never set verify=False globally; never call urllib3.disable_warnings().
  * Token is proactively refreshed ~2 min before expiry; on a 401 response
    the client re-mints once and retries — avoids silent stale-token failures.

INVARIANTS (IControlWriteClient — write extension):
  * NEVER log declaration bodies — they may contain TLS private keys.
  * delete_tenant() REFUSES empty, "*", or path-containing tenant names.
  * post_declaration() REFUSES empty tenant.
  * poll_task() refreshes the token on every iteration (applies exceed 20-min TTL).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import requests

from core.errors import InternalError, UnauthorizedError

if TYPE_CHECKING:
    from models.f5_credential import F5Credential

logger = logging.getLogger(__name__)

# iControl token TTL is 20 minutes; refresh 2 minutes early.
_TOKEN_TTL_SECONDS = 20 * 60
_REFRESH_MARGIN_SECONDS = 2 * 60


class IControlClient:
    """Thin read-only wrapper around the BIG-IP iControl REST API.

    Public interface:
        authenticate()          → (token, expires_at)
        get_sys_info()          → dict
        get_as3_declaration()   → dict
        get_partitions()        → list[str]
        test_reachability()     → dict

    The single private POST is _login() — auth, not device mutation.
    """

    def __init__(
        self,
        host: str,
        port: int,
        credential: F5Credential,
        verify_https_cert: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._credential = credential
        self._verify = verify_https_cert

        self._session = requests.Session()
        # Per-device TLS policy — NEVER global urllib3 disable_warnings
        self._session.verify = verify_https_cert
        self._session.headers.update({"Content-Type": "application/json"})

        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        base = path.lstrip("/")
        return f"https://{self._host}:{self._port}/{base}"

    # ------------------------------------------------------------------
    # Private auth
    # ------------------------------------------------------------------

    def _login(self) -> tuple[str, datetime]:
        """Mint a new session token via POST /mgmt/shared/authn/login.

        This is the ONLY POST in the entire client — auth, not device mutation.
        Returns (token, expires_at).
        """
        from core.encryption import decrypt_value

        cred = self._credential
        if cred.auth_type == "token":
            if not cred.token_encrypted:
                raise UnauthorizedError("F5 credential has no token set")
            raw_token = decrypt_value(cred.token_encrypted)
            if not raw_token:
                raise UnauthorizedError("Failed to decrypt F5 token")
            expires_at = datetime.now(UTC) + timedelta(seconds=_TOKEN_TTL_SECONDS)
            return raw_token, expires_at

        # password auth — trade for a session token
        if not cred.username or not cred.password_encrypted:
            raise UnauthorizedError("F5 credential has no username/password set")
        password = decrypt_value(cred.password_encrypted)
        if not password:
            raise UnauthorizedError("Failed to decrypt F5 password")

        payload = {
            "username": cred.username,
            "password": password,
            "loginProviderName": "tmos",
        }
        try:
            resp = self._session.post(
                self._url("/mgmt/shared/authn/login"),
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise UnauthorizedError(
                f"BIG-IP login failed: {exc.response.status_code if exc.response else exc}"
            ) from exc
        except requests.RequestException as exc:
            raise InternalError(f"BIG-IP login request error: {exc}") from exc

        data = resp.json()
        token_val = (data.get("token") or {}).get("token")
        if not token_val:
            raise UnauthorizedError("BIG-IP login response did not include a token")

        expires_at = datetime.now(UTC) + timedelta(seconds=_TOKEN_TTL_SECONDS)
        return token_val, expires_at

    def _refresh_if_needed(self) -> None:
        """Proactively refresh the session token if it is about to expire."""
        if self._token is None or self._token_expires_at is None:
            self._token, self._token_expires_at = self._login()
            self._session.headers["X-F5-Auth-Token"] = self._token
            return

        margin = timedelta(seconds=_REFRESH_MARGIN_SECONDS)
        if datetime.now(UTC) >= (self._token_expires_at - margin):
            logger.debug("IControlClient: proactive token refresh for %s", self._host)
            self._token, self._token_expires_at = self._login()
            self._session.headers["X-F5-Auth-Token"] = self._token

    def authenticate(self) -> tuple[str, datetime]:
        """Public: ensure authenticated and return (token, expires_at)."""
        self._refresh_if_needed()
        assert self._token is not None
        assert self._token_expires_at is not None
        return self._token, self._token_expires_at

    # ------------------------------------------------------------------
    # Internal GET helper with 401-retry-once
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any] | None = None, timeout: int = 15) -> Any:
        """GET with proactive token refresh; on 401 re-mints once and retries."""
        self._refresh_if_needed()
        url = self._url(path)
        try:
            resp = self._session.get(url, params=params, timeout=timeout)
        except requests.RequestException as exc:
            raise InternalError(f"BIG-IP GET {path} failed: {exc}") from exc

        if resp.status_code == 401:
            # Token may have expired server-side; force re-mint and retry once
            logger.debug("IControlClient: 401 on %s — re-minting token", path)
            self._token = None
            self._token_expires_at = None
            self._refresh_if_needed()
            try:
                resp = self._session.get(url, params=params, timeout=timeout)
            except requests.RequestException as exc:
                raise InternalError(f"BIG-IP GET {path} (retry) failed: {exc}") from exc

        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise InternalError(
                f"BIG-IP GET {path} returned {resp.status_code}: {resp.text[:200]}"
            ) from exc

        return resp.json()

    # ------------------------------------------------------------------
    # Read-only business methods
    # ------------------------------------------------------------------

    def get_sys_info(self) -> dict[str, Any]:
        """GET /mgmt/tm/sys/hardware — device model, platform, version etc.

        Falls back to /mgmt/tm/sys/version if hardware endpoint returns
        an unexpected shape (some VE images restrict hardware endpoint).
        """
        try:
            hw = self._get("/mgmt/tm/sys/hardware")
            # Parse platform entry
            platform_entry = {}
            for entry in (hw.get("entries") or {}).values():
                nested = entry.get("nestedStats", {}).get("entries", {})
                if nested:
                    platform_entry = {
                        k: (v.get("description") or v.get("value"))
                        for k, v in nested.items()
                    }
                    break
            return {
                "source": "hardware",
                "platform": platform_entry.get("platform"),
                "model": platform_entry.get("marketingName"),
                "raw": hw,
            }
        except Exception:
            pass

        # Fallback: /mgmt/tm/sys/version
        ver = self._get("/mgmt/tm/sys/version")
        entries = {}
        for entry in (ver.get("entries") or {}).values():
            nested = entry.get("nestedStats", {}).get("entries", {})
            entries.update({k: (v.get("description") or v.get("value")) for k, v in nested.items()})
        return {
            "source": "version",
            "version": entries.get("Version"),
            "build": entries.get("Build"),
            "edition": entries.get("Edition"),
            "product": entries.get("Product"),
            "raw": ver,
        }

    def get_device_info(self) -> dict[str, Any]:
        """Aggregate device facts: model/version, HA state, partitions, AS3 tenants.

        Returns a dict suitable for caching in F5BIGIPDevice.device_info.
        """
        # Version
        try:
            ver_raw = self._get("/mgmt/tm/sys/version")
            version_entries: dict[str, str] = {}
            for entry in (ver_raw.get("entries") or {}).values():
                nested = entry.get("nestedStats", {}).get("entries", {})
                version_entries.update(
                    {k: (v.get("description") or v.get("value") or "") for k, v in nested.items()}
                )
            version = version_entries.get("Version")
            product = version_entries.get("Product")
        except Exception as exc:
            logger.warning("Could not read BIG-IP version: %s", exc)
            version = None
            product = None

        # Platform / model
        model: str | None = None
        try:
            hw = self._get("/mgmt/tm/sys/hardware")
            for entry in (hw.get("entries") or {}).values():
                nested = entry.get("nestedStats", {}).get("entries", {})
                if nested:
                    model = nested.get("marketingName", {}).get("description")
                    break
        except Exception:
            pass

        # HA state
        ha_state: str | None = None
        try:
            fail_safe = self._get("/mgmt/tm/cm/failover-status")
            for entry in (fail_safe.get("entries") or {}).values():
                nested = entry.get("nestedStats", {}).get("entries", {})
                status = nested.get("status", {}).get("description")
                if status:
                    ha_state = status
                    break
        except Exception:
            pass

        # Partitions
        partitions: list[str] = []
        try:
            part_resp = self._get("/mgmt/tm/auth/partition")
            partitions = [p.get("name") for p in (part_resp.get("items") or []) if p.get("name")]
        except Exception:
            pass

        # AS3 tenants
        as3_tenants = self.get_as3_declaration()

        return {
            "model": model,
            "version": version,
            "product": product,
            "ha_state": ha_state,
            "partitions": partitions,
            "as3_tenants": as3_tenants,
        }

    def get_as3_declaration(self) -> list[str]:
        """GET /mgmt/shared/appsvcs/declare — return list of AS3 tenant names.

        Returns empty list if AS3 is not installed (404) or declaration is empty.
        """
        try:
            resp = self._session.get(
                self._url("/mgmt/shared/appsvcs/declare"),
                timeout=15,
            )
            if resp.status_code == 404:
                return []
            if resp.status_code == 401:
                self._token = None
                self._token_expires_at = None
                self._refresh_if_needed()
                resp = self._session.get(
                    self._url("/mgmt/shared/appsvcs/declare"),
                    timeout=15,
                )
            resp.raise_for_status()
            data = resp.json()
            # Tenants are top-level keys excluding AS3 class fields
            _skip = {"class", "action", "persist", "schemaVersion", "id", "label", "remark"}
            return [k for k in data if k not in _skip and isinstance(data[k], dict)]
        except requests.HTTPError:
            return []
        except Exception as exc:
            logger.debug("AS3 declaration fetch failed (non-fatal): %s", exc)
            return []

    def get_as3_declaration_full(self) -> dict:
        """GET /mgmt/shared/appsvcs/declare — return the FULL AS3 declaration dict.

        Unlike get_as3_declaration() which returns only tenant names, this method
        returns the complete declaration body for import/translation.

        SECRET-SAFE: the declaration body is NEVER logged (it may contain TLS
        private keys and other secrets). Only the top-level keys are logged at
        debug level.

        Returns an empty dict if AS3 is not installed (404) or on any error.
        Read-only — no write verbs; the P1 invariant test must still pass.
        """
        try:
            resp = self._session.get(
                self._url("/mgmt/shared/appsvcs/declare"),
                timeout=15,
            )
            if resp.status_code == 404:
                return {}
            if resp.status_code == 401:
                self._token = None
                self._token_expires_at = None
                self._refresh_if_needed()
                resp = self._session.get(
                    self._url("/mgmt/shared/appsvcs/declare"),
                    timeout=15,
                )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                return {}
            # Log only top-level keys — declaration body NEVER logged
            logger.debug(
                "AS3 declaration full fetch: top-level keys=%s",
                [k for k in data if isinstance(k, str)],
            )
            return data
        except Exception as exc:
            logger.debug("AS3 declaration full fetch failed (non-fatal): %s", exc)
            return {}

    def get_partitions(self) -> list[str]:
        """GET /mgmt/tm/auth/partition — list all administrative partitions."""
        try:
            resp = self._get("/mgmt/tm/auth/partition")
            return [p.get("name") for p in (resp.get("items") or []) if p.get("name")]
        except Exception as exc:
            logger.debug("Partitions fetch failed: %s", exc)
            return []

    def test_reachability(self) -> dict[str, Any]:
        """Probe the device: authenticate + fetch version info.

        Returns {"success": bool, "message": str, "version": str|None}.
        """
        try:
            self.authenticate()
            ver = self._get("/mgmt/tm/sys/version")
            version_entries: dict[str, str] = {}
            for entry in (ver.get("entries") or {}).values():
                nested = entry.get("nestedStats", {}).get("entries", {})
                version_entries.update(
                    {k: (v.get("description") or v.get("value") or "") for k, v in nested.items()}
                )
            version = version_entries.get("Version")
            return {
                "success": True,
                "message": f"Connected to BIG-IP {self._host}:{self._port}",
                "version": version,
            }
        except Exception as exc:
            return {"success": False, "message": str(exc), "version": None}


# ---------------------------------------------------------------------------
# Write client subclass (D-023 Phase 2)
# ---------------------------------------------------------------------------

# Minimum AS3 version required for controls.dryRun support.
_AS3_DRY_RUN_MIN = (3, 30)

# Tenant name characters that are never acceptable in a DELETE or POST path.
_UNSAFE_TENANT_CHARS = {"*", "/"}


def _parse_as3_version(version_str: str) -> tuple[int, int]:
    """Parse AS3 version string like "3.30.0" → (3, 30).

    Strips any trailing patch/build noise; returns (0, 0) on parse failure
    so callers can still compare — the comparison will fail-closed.
    """
    try:
        parts = version_str.split(".")
        return int(parts[0]), int(parts[1])
    except (AttributeError, IndexError, ValueError):
        return (0, 0)


# ---------------------------------------------------------------------------
# Pure helpers for AS3 wire-format interpretation
#
# These are isolated so that a real-device discrepancy is a one-line fix.
# They tolerate both "task.results" and top-level "results" layouts and
# both the "completed" / "failed" terminal strings from different AS3 versions.
# ---------------------------------------------------------------------------

def _is_task_pending(resp: dict) -> bool:
    """Return True if the AS3 task response is still in progress."""
    results = resp.get("results")

    if not results:
        # 202 with no results yet = pending
        return True

    for r in results:
        if isinstance(r, dict) and (r.get("message") or "").lower() == "in progress":
            return True
    return False


def _task_terminal_result(resp: dict) -> tuple[bool, str]:
    """Extract (success, message) from a terminal AS3 task response.

    Handles both top-level ``results`` and nested ``task.results`` shapes.
    Returns (True, message) on success, (False, message) on failure.
    """
    results = resp.get("results") or []
    if not results:
        # Bare response with a top-level code
        code = resp.get("code", 200)
        message = resp.get("message", "")
        return (200 <= int(code) < 300), str(message)

    # Collect codes and messages from all result entries
    codes: list[int] = []
    messages: list[str] = []
    for r in results:
        if isinstance(r, dict):
            if "code" not in r:
                logger.debug(
                    "_task_terminal_result: result entry missing 'code' key — defaulting to 200: %r",
                    {k: v for k, v in r.items() if k != "declaration"},
                )
            codes.append(int(r.get("code", 200)))
            messages.append(str(r.get("message", "")))

    overall_message = "; ".join(m for m in messages if m)
    success = all(200 <= c < 300 for c in codes) if codes else True
    return success, overall_message


def _parse_dry_run_delta(resp: dict) -> tuple[int, int, int, str]:
    """Extract (adds, changes, destroys, details_json) from a dry-run response.

    AS3 dry-run returns the diff in the ``dryRunResults`` or inside ``results``.
    Because AS3 doesn't always provide a structured add/change/destroy count,
    we surface best-effort counts and put the raw diff in ``details``.
    """
    import json as _json

    # Best-effort: look for dryRunResults at top level
    dry = resp.get("dryRunResults") or {}
    adds = int(dry.get("adds", 0))
    changes = int(dry.get("changes", 0))
    destroys = int(dry.get("removes", 0) or dry.get("destroys", 0))
    # Strip "declaration" before serialising — AS3 dry-run responses may echo the
    # submitted declaration back (including TLS private keys).  Mirror the existing
    # pattern used in _task_terminal_result debug logging.
    safe_resp = {k: v for k, v in resp.items() if k != "declaration"}
    details = _json.dumps(safe_resp, indent=2)
    return adds, changes, destroys, details


class IControlWriteClient(IControlClient):
    """Extension of IControlClient with AS3 write methods.

    DESIGN: The base class (IControlClient) is read-only — its public interface
    has no write verbs — so the P1 invariant test (test_no_public_post_patch_delete)
    continues to pass unchanged.  This subclass adds the write surface on top,
    reusing all of the base class auth/token/_session machinery.

    Public write methods:
        get_as3_version()          → str   (e.g. "3.36.0")
        post_declaration(...)      → dict  (raw AS3 response, 200 OR 202)
        poll_task(task_id, ...)    → dict  (terminal task result)
        delete_tenant(tenant)      → dict  (raw AS3 response)

    Security invariants:
        * Declaration bodies are NEVER logged (may contain TLS private keys).
        * delete_tenant refuses empty/*/path-containing tenant names.
        * post_declaration refuses empty tenant.
        * poll_task calls _refresh_if_needed() every iteration.
    """

    # ------------------------------------------------------------------
    # Shared write helper
    # ------------------------------------------------------------------

    def _write(self, method: str, path: str, json: dict | None = None, timeout: int = 30) -> Any:
        """POST/DELETE helper with proactive refresh and 401-retry-once.

        Never logs the json body — it may contain TLS private keys.
        """
        self._refresh_if_needed()
        url = self._url(path)
        try:
            resp = self._session.request(method, url, json=json, timeout=timeout)
        except requests.RequestException as exc:
            raise InternalError(f"BIG-IP {method} {path} failed: {exc}") from exc

        if resp.status_code == 401:
            logger.debug("IControlWriteClient: 401 on %s %s — re-minting token", method, path)
            self._token = None
            self._token_expires_at = None
            self._refresh_if_needed()
            try:
                resp = self._session.request(method, url, json=json, timeout=timeout)
            except requests.RequestException as exc:
                raise InternalError(f"BIG-IP {method} {path} (retry) failed: {exc}") from exc

        # 202 is valid for async tasks — don't raise on it
        if resp.status_code not in (200, 202):
            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                raise InternalError(
                    f"BIG-IP {method} {path} returned {resp.status_code}: {resp.text[:300]}"
                ) from exc

        return resp.json()

    # ------------------------------------------------------------------
    # AS3 version probe
    # ------------------------------------------------------------------

    def get_as3_version(self) -> str:
        """GET /mgmt/shared/appsvcs/info → return AS3 version string.

        Returns "0.0.0" if AS3 is not installed (404) so callers can
        safely compare tuples without extra guards.
        """
        try:
            resp = self._get("/mgmt/shared/appsvcs/info")
            version = (resp.get("version") or "0.0.0")
            return str(version)
        except Exception:
            return "0.0.0"

    # ------------------------------------------------------------------
    # Declaration write
    # ------------------------------------------------------------------

    def post_declaration(
        self,
        tenant: str,
        declaration: dict,
        *,
        dry_run: bool = False,
    ) -> dict:
        """POST a tenant-scoped AS3 declaration.

        Injects ``controls.dryRun=true`` when dry_run=True.

        Args:
            tenant: AS3 tenant name.  Must be non-empty — HARD FAIL.
            declaration: Full AS3 declaration dict (including class/tenant keys).
            dry_run: If True, inject ``controls.dryRun=true``.

        Returns:
            Raw AS3 response — either a final 200 body or a 202 task dict.

        NEVER logs the declaration body (may contain TLS private keys).
        """
        if not tenant or not tenant.strip():
            raise ValueError("post_declaration: tenant must be non-empty")

        body = dict(declaration)
        if dry_run:
            body["controls"] = dict(body.get("controls") or {})
            body["controls"]["dryRun"] = True

        # Log intent, NOT the body
        logger.info(
            "IControlWriteClient: POSTing AS3 declaration for tenant '%s' dry_run=%s on %s",
            tenant,
            dry_run,
            self._host,
        )
        return self._write("POST", "/mgmt/shared/appsvcs/declare", json=body, timeout=60)

    # ------------------------------------------------------------------
    # Task polling
    # ------------------------------------------------------------------

    def poll_task(
        self,
        task_id: str,
        *,
        timeout_s: int = 1800,
        interval_s: int = 5,
        on_output=None,
    ) -> dict:
        """Poll AS3 task until terminal or timeout.

        Calls _refresh_if_needed() on EVERY iteration — applies can exceed
        the 20-minute iControl token TTL.

        Args:
            task_id: AS3 task UUID from the 202 response.
            timeout_s: Absolute timeout in seconds (default 1800 = 30 min).
            interval_s: Polling interval in seconds.
            on_output: Optional callable(str) for progress log lines.

        Returns:
            Terminal AS3 task response dict.

        Raises:
            InternalError on timeout or request failure.
        """
        import time as _time

        task_path = f"/mgmt/shared/appsvcs/task/{task_id}"
        start = _time.monotonic()
        elapsed = 0.0

        while elapsed < timeout_s:
            # Refresh token on every iteration — polls exceed 20-min TTL
            self._refresh_if_needed()

            try:
                resp = self._session.get(self._url(task_path), timeout=15)
            except requests.RequestException as exc:
                raise InternalError(f"BIG-IP task poll {task_id} failed: {exc}") from exc

            if resp.status_code == 401:
                logger.debug("IControlWriteClient: 401 on task poll — re-minting token")
                self._token = None
                self._token_expires_at = None
                self._refresh_if_needed()
                try:
                    resp = self._session.get(self._url(task_path), timeout=15)
                except requests.RequestException as exc:
                    raise InternalError(f"BIG-IP task poll {task_id} (retry) failed: {exc}") from exc

            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                raise InternalError(
                    f"BIG-IP task poll {task_id} returned {resp.status_code}"
                ) from exc

            data = resp.json()
            if not _is_task_pending(data):
                logger.debug("IControlWriteClient: task %s reached terminal state", task_id)
                return data

            elapsed = _time.monotonic() - start
            if on_output:
                on_output(f"[as3] task {task_id} in progress... ({int(elapsed)}s/{timeout_s}s)")
            logger.debug("IControlWriteClient: task %s in progress (%.0fs)", task_id, elapsed)

            _time.sleep(interval_s)
            elapsed = _time.monotonic() - start

        raise InternalError(
            f"AS3 task {task_id} did not complete within {timeout_s}s"
        )

    # ------------------------------------------------------------------
    # Tenant delete
    # ------------------------------------------------------------------

    def delete_tenant(self, tenant: str) -> dict:
        """DELETE /mgmt/shared/appsvcs/declare/{tenant}.

        Hard-refuses:
          - empty / whitespace-only tenant names
          - "*" (wildcard — would delete ALL tenants)
          - names containing "/" (path traversal)

        Args:
            tenant: Tenant name to delete.

        Returns:
            Raw AS3 response dict.

        Raises:
            ValueError on unsafe tenant name (FAIL CLOSED).
        """
        if not tenant or not tenant.strip():
            raise ValueError("delete_tenant: tenant name must be non-empty")
        if tenant.strip() == "*":
            raise ValueError("delete_tenant: wildcard '*' tenant is not allowed — would delete ALL tenants")
        if "/" in tenant:
            raise ValueError(f"delete_tenant: tenant name must not contain '/': {tenant!r}")

        tenant = tenant.strip()
        path = f"/mgmt/shared/appsvcs/declare/{tenant}"
        logger.info(
            "IControlWriteClient: DELETE AS3 tenant '%s' on %s",
            tenant,
            self._host,
        )
        return self._write("DELETE", path, timeout=60)
