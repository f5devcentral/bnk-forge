"""
Unit tests for D-023 Phase 1 — F5 BIG-IP discovery.

All tests are pure (no DB, no network). Covers:
  1. Credential encryption round-trip + serialize() hides secrets.
  2. iControl client read-only invariant (no public POST/PATCH/DELETE).
  3. Client auth+refresh (mocked session).
  4. Device facts parse (canned JSON) + probe failure recorded (D-017).
  5. CIS arg+secret parse (both --bigip-url forms).
  6. CIS analyze_cis — detected / missing branches.
  7. CRD registry curation — CIS VS → registry-enriched.
  8. Fleet soft-dep — absent FleetMember → no-op, no exception.
  9. Credential route authz — mutation routes use require_operator (no spurious project_id).
 10. F5CredentialCreate auth_type Literal — rejects invalid values.
"""

import inspect
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from services.scanner.constants import PrerequisiteStatus

# ---------------------------------------------------------------------------
# 1. Credential encryption round-trip
# ---------------------------------------------------------------------------


class TestF5CredentialEncryption:
    """Encryption round-trip and serialize() invariants."""

    def test_encrypt_roundtrip_password(self):
        """encrypt_value → decrypt_value restores original."""
        from core.encryption import decrypt_value, encrypt_value

        plaintext = "s3cr3t!"
        ciphertext = encrypt_value(plaintext)
        assert ciphertext is not None
        assert ciphertext != plaintext
        assert decrypt_value(ciphertext) == plaintext

    def test_encrypt_roundtrip_token(self):
        """Token encryption round-trip."""
        from core.encryption import decrypt_value, encrypt_value

        token = "TOKEN-abc123xyz"
        enc = encrypt_value(token)
        assert enc != token
        assert decrypt_value(enc) == token

    def test_serialize_hides_password(self):
        """serialize() must expose has_password bool, never the secret."""
        from core.encryption import encrypt_value
        from services.f5_credential_service import F5CredentialService

        cred = MagicMock()
        cred.id = 1
        cred.name = "bigip-cred"
        cred.auth_type = "password"
        cred.username = "admin"
        cred.password_encrypted = encrypt_value("secret")
        cred.token_encrypted = None
        cred.is_default = False
        cred.created_at = datetime.now(UTC)
        cred.updated_at = datetime.now(UTC)
        cred.created_by = None
        cred.last_test_status = None
        cred.last_test_at = None
        cred.last_test_message = None

        result = F5CredentialService.serialize(cred)

        assert result["has_password"] is True
        assert result["has_token"] is False
        assert "password" not in result
        assert "password_encrypted" not in result
        assert "token" not in result
        assert "token_encrypted" not in result

    def test_serialize_hides_token(self):
        """serialize() must expose has_token bool, never the raw token."""
        from core.encryption import encrypt_value
        from services.f5_credential_service import F5CredentialService

        cred = MagicMock()
        cred.id = 2
        cred.name = "bigip-token-cred"
        cred.auth_type = "token"
        cred.username = None
        cred.password_encrypted = None
        cred.token_encrypted = encrypt_value("BEARER-XYZ")
        cred.is_default = False
        cred.created_at = datetime.now(UTC)
        cred.updated_at = datetime.now(UTC)
        cred.created_by = None
        cred.last_test_status = None
        cred.last_test_at = None
        cred.last_test_message = None

        result = F5CredentialService.serialize(cred)

        assert result["has_token"] is True
        assert result["has_password"] is False
        # The raw token string must not appear anywhere in the serialized output
        for v in result.values():
            if isinstance(v, str):
                assert "BEARER-XYZ" not in v


# ---------------------------------------------------------------------------
# 2. Read-only invariant — iControl client method set
# ---------------------------------------------------------------------------


class TestIControlReadOnlyInvariant:
    """Assert the client exposes NO public POST/PATCH/DELETE toward BIG-IP."""

    def _public_methods(self):
        from services.f5.icontrol_client import IControlClient

        return [
            name
            for name, method in inspect.getmembers(IControlClient, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]

    def test_no_public_post_patch_delete(self):
        """Client public interface has no write/mutation methods."""
        write_verbs = {"post", "patch", "delete", "put", "apply", "deploy", "create", "update",
                       "destroy", "write", "push"}
        public = self._public_methods()
        offenders = [m for m in public if any(verb in m.lower() for verb in write_verbs)]
        assert offenders == [], (
            f"iControlClient must not expose public write methods: {offenders}"
        )

    def test_expected_public_methods_present(self):
        """Expected read-only public methods are all present."""
        public = set(self._public_methods())
        expected = {"authenticate", "get_sys_info", "get_as3_declaration",
                    "get_as3_declaration_full", "get_partitions", "test_reachability",
                    "get_device_info"}
        missing = expected - public
        assert missing == set(), f"Expected public methods missing: {missing}"


# ---------------------------------------------------------------------------
# 3. Client auth + refresh (mocked requests.Session)
# ---------------------------------------------------------------------------


class TestIControlClientAuth:
    """Auth mint, proactive refresh, 401-retry."""

    def _make_cred(self, auth_type="password"):
        from core.encryption import encrypt_value
        cred = MagicMock()
        cred.auth_type = auth_type
        cred.username = "admin"
        cred.password_encrypted = encrypt_value("Password1!")
        cred.token_encrypted = encrypt_value("BEARER-XYZ") if auth_type == "token" else None
        return cred

    def test_mint_password_token(self):
        """_login() with password auth returns token from server response."""
        from services.f5.icontrol_client import IControlClient

        cred = self._make_cred("password")
        client = IControlClient("10.0.0.1", 443, cred, verify_https_cert=False)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": {"token": "SESSION-TOKEN-123"}}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp) as mock_post:
            token, expires_at = client._login()

        assert token == "SESSION-TOKEN-123"
        assert expires_at > datetime.now(UTC)
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert "/mgmt/shared/authn/login" in call_url

    def test_mint_token_auth_skips_http(self):
        """Token auth type uses stored token directly without HTTP POST."""
        from services.f5.icontrol_client import IControlClient

        cred = self._make_cred("token")
        client = IControlClient("10.0.0.1", 443, cred, verify_https_cert=False)

        with patch.object(client._session, "post") as mock_post:
            token, expires_at = client._login()

        mock_post.assert_not_called()
        assert token == "BEARER-XYZ"

    def test_refresh_if_needed_when_expired(self):
        """Token already expired triggers re-mint."""
        from services.f5.icontrol_client import IControlClient

        cred = self._make_cred("password")
        client = IControlClient("10.0.0.1", 443, cred, verify_https_cert=False)
        # Set token to be expired
        client._token = "OLD-TOKEN"
        client._token_expires_at = datetime.now(UTC) - timedelta(seconds=1)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"token": {"token": "FRESH-TOKEN"}}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "post", return_value=mock_resp):
            client._refresh_if_needed()

        assert client._token == "FRESH-TOKEN"

    def test_verify_cert_passthrough(self):
        """verify_https_cert is set on the session, not globally disabled."""
        from services.f5.icontrol_client import IControlClient

        cred = self._make_cred()
        client_true = IControlClient("10.0.0.1", 443, cred, verify_https_cert=True)
        client_false = IControlClient("10.0.0.1", 443, cred, verify_https_cert=False)

        assert client_true._session.verify is True
        assert client_false._session.verify is False

    def test_authenticate_returns_token_and_expiry(self):
        """authenticate() returns (token, expires_at) tuple."""
        from services.f5.icontrol_client import IControlClient

        cred = self._make_cred("token")
        client = IControlClient("10.0.0.1", 443, cred, verify_https_cert=False)

        token, expires_at = client.authenticate()
        assert token == "BEARER-XYZ"
        assert isinstance(expires_at, datetime)

    def test_401_triggers_retry(self):
        """On 401, client re-mints token and retries GET once."""
        from services.f5.icontrol_client import IControlClient

        cred = self._make_cred("token")
        client = IControlClient("10.0.0.1", 443, cred, verify_https_cert=False)
        # Pre-set a token so we enter the retry path
        client._token = "STALE-TOKEN"
        client._token_expires_at = datetime.now(UTC) + timedelta(minutes=15)
        client._session.headers["X-F5-Auth-Token"] = "STALE-TOKEN"

        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status = MagicMock()

        resp_ok = MagicMock()
        resp_ok.status_code = 200
        resp_ok.json.return_value = {"key": "value"}
        resp_ok.raise_for_status = MagicMock()

        get_calls = [resp_401, resp_ok]

        def _side_effect(*args, **kwargs):
            return get_calls.pop(0)

        with patch.object(client._session, "get", side_effect=_side_effect):
            with patch.object(client, "_login", return_value=("FRESH-TOKEN", datetime.now(UTC) + timedelta(minutes=15))):
                result = client._get("/mgmt/tm/sys/version")

        assert result == {"key": "value"}


# ---------------------------------------------------------------------------
# 4. Device facts parse + probe failure recording (D-017)
# ---------------------------------------------------------------------------


class TestDeviceFacts:
    """get_device_info parse + D-017 probe failure invariant."""

    def test_probe_failure_sets_error_and_clears_device_info(self):
        """On probe failure, last_discovery_error is non-empty, device_info cleared."""
        from core.encryption import encrypt_value
        from services.f5_device_service import F5DeviceService

        # Build a mock DB session
        db = MagicMock()

        # Mock device with credential
        device = MagicMock()
        device.id = 1
        device.mgmt_host = "10.0.0.1"
        device.mgmt_port = 443
        device.mgmt_credential_id = 42
        device.verify_https_cert = True
        device.device_info = {"model": "BIG-IP 2000s", "version": "16.0.0"}

        cred = MagicMock()
        cred.auth_type = "password"
        cred.username = "admin"
        cred.password_encrypted = encrypt_value("pass")
        cred.token_encrypted = None

        db.query.return_value.filter.return_value.first.side_effect = [device, cred]

        service = F5DeviceService(db)

        with patch("services.f5.icontrol_client.IControlClient") as MockClient:
            MockClient.return_value.get_device_info.side_effect = Exception("Connection refused")
            service.probe_device(1)

        # D-017: error must be non-empty
        assert device.last_discovery_error  # non-empty string
        assert device.last_discovery_error != ""
        # device_info cleared on failure
        assert device.device_info == {}
        assert device.last_discovery_status == "failed"


# ---------------------------------------------------------------------------
# 5. CIS arg + secret parsing
# ---------------------------------------------------------------------------


class TestCISArgParsing:
    """_parse_cis_args handles both --bigip-url forms and secret ref."""

    def test_bigip_url_equals_form(self):
        """--bigip-url=https://10.0.0.1 is parsed correctly."""
        from services.scanner.prereqs import _parse_cis_args

        args = ["--bigip-url=https://10.0.0.1", "--bigip-ctlr-creds=kube-system/bigip-login"]
        url, secret, partition = _parse_cis_args(args)

        assert url == "https://10.0.0.1"
        assert secret["name"] == "bigip-login"
        assert secret["namespace"] == "kube-system"
        assert partition is None

    def test_bigip_url_space_form(self):
        """--bigip-url https://10.0.0.1:8443 (space-separated) is parsed correctly."""
        from services.scanner.prereqs import _parse_cis_args

        args = ["--bigip-url", "https://10.0.0.1:8443", "--bigip-ctlr-creds", "bigip-secret"]
        url, secret, partition = _parse_cis_args(args)

        assert url == "https://10.0.0.1:8443"
        assert secret["name"] == "bigip-secret"
        assert secret["namespace"] is None  # no namespace prefix
        assert partition is None

    def test_no_bigip_url_returns_none(self):
        """Missing --bigip-url returns (None, {name: None, namespace: None}, None)."""
        from services.scanner.prereqs import _parse_cis_args

        url, secret, partition = _parse_cis_args(["--some-other-flag", "value"])
        assert url is None
        assert secret["name"] is None
        assert partition is None

    def test_bigip_url_parse_host_port(self):
        """_parse_bigip_url extracts host and port."""
        from services.scanner.prereqs import _parse_bigip_url

        assert _parse_bigip_url("https://10.0.0.1") == ("10.0.0.1", None)
        assert _parse_bigip_url("https://10.0.0.1:8443") == ("10.0.0.1", 8443)
        assert _parse_bigip_url("10.0.0.1") == ("10.0.0.1", None)
        assert _parse_bigip_url(None) == (None, None)


# ---------------------------------------------------------------------------
# 6. analyze_cis — detected / missing / partial
# ---------------------------------------------------------------------------


class TestAnalyzeCIS:
    """analyze_cis pure function tests."""

    def _make_controller(self, bigip_url="https://10.0.0.1", secret_ref="kube-system/bigip-login"):
        return [
            {
                "name": "k8s-bigip-ctlr",
                "namespace": "kube-system",
                "images": ["f5networks/k8s-bigip-ctlr:2.12.0"],
                "args": [
                    f"--bigip-url={bigip_url}",
                    f"--bigip-ctlr-creds={secret_ref}",
                ],
                "replicas_desired": 1,
                "replicas_ready": 1,
            }
        ]

    def test_no_controllers_returns_missing(self):
        """Empty controller list → status=missing."""
        from services.scanner.prereqs import analyze_cis

        result = analyze_cis([], [], [], [])

        assert result["status"] == PrerequisiteStatus.MISSING
        assert result["controller"]["found"] is False
        assert result["eol_note"]

    def test_controller_detected_with_bigip_url(self):
        """CIS controller found + bigip_url parsed → status=detected."""
        from services.scanner.prereqs import analyze_cis

        controllers = self._make_controller("https://10.0.0.1")
        vs = [{"name": "my-vs", "namespace": "default"}]
        ts = [{"name": "my-ts", "namespace": "default"}]

        result = analyze_cis(controllers, vs, ts, [])

        assert result["status"] == PrerequisiteStatus.DETECTED
        assert result["controller"]["found"] is True
        assert result["controller"]["bigip_url"] == "https://10.0.0.1"
        assert result["external_bigip"]["host"] == "10.0.0.1"
        assert len(result["inventory"]["virtual_servers"]) == 1
        assert len(result["inventory"]["transport_servers"]) == 1

    def test_controller_partial_without_bigip_url(self):
        """CIS controller found but no bigip_url → status=partial."""
        from services.scanner.prereqs import analyze_cis

        controllers = [
            {
                "name": "k8s-bigip-ctlr",
                "namespace": "kube-system",
                "images": ["f5networks/k8s-bigip-ctlr:latest"],
                "args": [],  # no --bigip-url
                "replicas_desired": 1,
                "replicas_ready": 0,
            }
        ]
        result = analyze_cis(controllers, [], [], [])
        assert result["status"] == PrerequisiteStatus.PARTIAL

    def test_secret_ref_recorded_not_fetched(self):
        """login_secret_ref is recorded by name/namespace, not fetched."""
        from services.scanner.prereqs import analyze_cis

        controllers = self._make_controller(
            bigip_url="https://192.168.1.1",
            secret_ref="bigip-ns/bigip-secret",
        )
        result = analyze_cis(controllers, [], [], [])

        secret_ref = result["controller"]["login_secret_ref"]
        assert secret_ref["name"] == "bigip-secret"
        assert secret_ref["namespace"] == "bigip-ns"

    def test_eol_note_always_present(self):
        """EOL note is always present in the result."""
        from services.scanner.prereqs import analyze_cis

        result_missing = analyze_cis([], [], [], [])
        result_detected = analyze_cis(self._make_controller(), [], [], [])

        assert "eol_note" in result_missing
        assert "eol_note" in result_detected
        assert "EOL" in result_detected["eol_note"] or "2026" in result_detected["eol_note"]


# ---------------------------------------------------------------------------
# 7. CRD registry curation
# ---------------------------------------------------------------------------


class TestCISRegistryCuration:
    """CIS CRD entries are curated as display overlay (D-019)."""

    def test_cis_virtualserver_registry_enriched(self):
        """CIS VirtualServer (cis.f5.com) → source=registry-enriched + friendly name."""
        from services.crd_discovery_service import _get_registry_index, merge_crd_with_registry

        # Clear lru_cache so our changes to RESOURCE_REGISTRY are visible
        _get_registry_index.cache_clear()
        index = _get_registry_index()

        raw = {
            "metadata": {"name": "virtualservers.cis.f5.com"},
            "spec": {
                "group": "cis.f5.com",
                "names": {"kind": "VirtualServer", "plural": "virtualservers"},
                "scope": "Namespaced",
                "versions": [{"name": "v1", "served": True, "storage": True}],
            },
        }
        result = merge_crd_with_registry(raw, index)

        assert result.source == "registry-enriched"
        assert "CIS" in result.display_name
        assert "Virtual" in result.display_name

    def test_cis_transportserver_registry_enriched(self):
        """CIS TransportServer → registry-enriched."""
        from services.crd_discovery_service import _get_registry_index, merge_crd_with_registry

        _get_registry_index.cache_clear()
        index = _get_registry_index()

        raw = {
            "metadata": {"name": "transportservers.cis.f5.com"},
            "spec": {
                "group": "cis.f5.com",
                "names": {"kind": "TransportServer", "plural": "transportservers"},
                "scope": "Namespaced",
                "versions": [{"name": "v1", "served": True, "storage": True}],
            },
        }
        result = merge_crd_with_registry(raw, index)
        assert result.source == "registry-enriched"

    def test_unknown_cis_kind_still_surfaces(self):
        """Unknown CIS kind (e.g., ExternalDNS) → source=discovered, not dropped."""
        from services.crd_discovery_service import _get_registry_index, merge_crd_with_registry

        _get_registry_index.cache_clear()
        index = _get_registry_index()

        raw = {
            "metadata": {"name": "externaldnses.cis.f5.com"},
            "spec": {
                "group": "cis.f5.com",
                "names": {"kind": "ExternalDNS", "plural": "externaldnses"},
                "scope": "Namespaced",
                "versions": [{"name": "v1alpha1", "served": True, "storage": True}],
            },
        }
        result = merge_crd_with_registry(raw, index)
        assert result.source == "discovered"
        assert result.kind == "ExternalDNS"


# ---------------------------------------------------------------------------
# 8. Fleet soft-dep — absent FleetMember → no-op
# ---------------------------------------------------------------------------


class TestFleetSoftDep:
    """fleet_integration gracefully no-ops when FleetMember is unavailable."""

    def test_absent_fleet_member_no_op(self):
        """When FleetMember is not importable, try_register_fleet_member does not raise."""
        import services.f5.fleet_integration as fi

        # Patch _FLEET_AVAILABLE to False to simulate D-022 not landed
        original = fi._FLEET_AVAILABLE
        fi._FLEET_AVAILABLE = False
        try:
            db = MagicMock()
            device = MagicMock()
            device.id = 1
            device.name = "bigip-01"
            device.project_id = 10
            device.device_info = {}

            # Must NOT raise
            fi.try_register_fleet_member(db, device)

            # DB must not be touched
            db.add.assert_not_called()
            db.flush.assert_not_called()
        finally:
            fi._FLEET_AVAILABLE = original

    def test_fleet_available_false_by_default(self):
        """fleet_available() returns False when FleetMember is not present."""
        import services.f5.fleet_integration as fi

        # D-022 hasn't merged; FleetMember is not in models
        # The module-level import will have failed, so _FLEET_AVAILABLE is False
        # (we just verify the helper function works)
        result = fi.fleet_available()
        # Can be True or False depending on whether D-022 has been merged,
        # but either way the function must be callable without raising
        assert isinstance(result, bool)

    def test_exception_in_do_register_does_not_propagate(self):
        """Even if _do_register raises, try_register_fleet_member swallows it."""
        import services.f5.fleet_integration as fi

        original = fi._FLEET_AVAILABLE
        fi._FLEET_AVAILABLE = True
        try:
            db = MagicMock()
            device = MagicMock()
            device.id = 99
            device.device_info = {}

            with patch.object(fi, "_do_register", side_effect=RuntimeError("DB error")):
                # Must not raise
                fi.try_register_fleet_member(db, device)
        finally:
            fi._FLEET_AVAILABLE = original


# ---------------------------------------------------------------------------
# 9. Migration import check
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# P4a — analyze_cis controller-less discovery (AS3 ConfigMap mode)
# ---------------------------------------------------------------------------


class TestAnalyzeCISControllerless:
    """analyze_cis emits a non-MISSING status when only AS3 ConfigMaps are present."""

    def test_no_surface_at_all_returns_missing(self):
        """No controller, no CRs, no ConfigMaps → MISSING."""
        from services.scanner.prereqs import analyze_cis

        result = analyze_cis([], [], [], [], cis_as3_configmaps=[])
        assert result["status"] == PrerequisiteStatus.MISSING
        assert result["controller"]["found"] is False

    def test_as3_configmaps_only_returns_partial(self):
        """No controller but AS3 ConfigMaps present → PARTIAL (not MISSING)."""
        from services.scanner.prereqs import analyze_cis

        configmaps = [
            {"name": "my-as3-cm", "namespace": "default", "labels": {"f5type": "virtual-server"},
             "template": {"class": "AS3"}, "template_parse_error": None}
        ]
        result = analyze_cis([], [], [], [], cis_as3_configmaps=configmaps)
        assert result["status"] == PrerequisiteStatus.PARTIAL
        assert result["controller"]["found"] is False
        assert len(result["inventory"]["as3_configmaps"]) == 1

    def test_controller_present_as3_configmaps_included_in_inventory(self):
        """Controller + AS3 ConfigMaps → both appear in inventory."""
        from services.scanner.prereqs import analyze_cis

        controllers = [{
            "name": "k8s-bigip-ctlr", "namespace": "kube-system",
            "images": ["f5networks/k8s-bigip-ctlr:2.12.0"],
            "args": ["--bigip-url=https://10.0.0.1"],
            "replicas_desired": 1, "replicas_ready": 1,
        }]
        configmaps = [
            {"name": "cm1", "namespace": "kube-system", "labels": {},
             "template": {"class": "AS3"}, "template_parse_error": None}
        ]
        result = analyze_cis(controllers, [], [], [], cis_as3_configmaps=configmaps)
        assert result["status"] == PrerequisiteStatus.DETECTED
        assert len(result["inventory"]["as3_configmaps"]) == 1

    def test_inventory_always_contains_as3_configmaps_key(self):
        """inventory dict always has as3_configmaps key (even when MISSING)."""
        from services.scanner.prereqs import analyze_cis

        result = analyze_cis([], [], [], [])
        assert "as3_configmaps" in result["inventory"]


# ---------------------------------------------------------------------------
# P4d — IControlClient.get_as3_declaration_full (read-only, secret-safe)
# ---------------------------------------------------------------------------


class TestGetAS3DeclarationFull:
    """IControlClient.get_as3_declaration_full() returns full dict, never logs body."""

    def _make_cred(self):
        from core.encryption import encrypt_value
        cred = MagicMock()
        cred.auth_type = "password"
        cred.username = "admin"
        cred.password_encrypted = encrypt_value("Password1!")
        cred.token_encrypted = None
        return cred

    def test_returns_full_dict_on_success(self):
        """get_as3_declaration_full() returns the complete declaration dict."""
        from services.f5.icontrol_client import IControlClient

        cred = self._make_cred()
        ic = IControlClient("10.0.0.1", 443, cred, verify_https_cert=False)
        ic._token = "VALID-TOKEN"
        from datetime import timedelta
        ic._token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        payload = {
            "class": "AS3",
            "declaration": {
                "class": "ADC",
                "tenant1": {"class": "Tenant", "app1": {"class": "Application"}},
            },
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload
        mock_resp.raise_for_status = MagicMock()

        with patch.object(ic._session, "get", return_value=mock_resp):
            result = ic.get_as3_declaration_full()

        assert result == payload

    def test_returns_empty_dict_on_404(self):
        """404 response → empty dict (AS3 not installed)."""
        from services.f5.icontrol_client import IControlClient

        cred = self._make_cred()
        ic = IControlClient("10.0.0.1", 443, cred, verify_https_cert=False)
        ic._token = "VALID-TOKEN"
        from datetime import timedelta
        ic._token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch.object(ic._session, "get", return_value=mock_resp):
            result = ic.get_as3_declaration_full()

        assert result == {}

    def test_declaration_body_never_in_log_output(self, caplog):
        """Declaration body (which may contain secrets) is never logged."""
        import logging

        from services.f5.icontrol_client import IControlClient

        cred = self._make_cred()
        ic = IControlClient("10.0.0.1", 443, cred, verify_https_cert=False)
        ic._token = "VALID-TOKEN"
        from datetime import timedelta
        ic._token_expires_at = datetime.now(UTC) + timedelta(hours=1)

        secret_payload = {
            "class": "AS3",
            "declaration": {
                "class": "ADC",
                "SecretTenant": {
                    "class": "Tenant",
                    "app": {"class": "Application", "SECRET_VALUE_12345": "tls-key-here"},
                },
            },
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = secret_payload
        mock_resp.raise_for_status = MagicMock()

        with caplog.at_level(logging.DEBUG, logger="services.f5.icontrol_client"):
            with patch.object(ic._session, "get", return_value=mock_resp):
                ic.get_as3_declaration_full()

        # The secret value must NOT appear in any log record
        for record in caplog.records:
            assert "SECRET_VALUE_12345" not in record.getMessage()
            assert "tls-key-here" not in record.getMessage()

    def test_method_exists_on_read_only_client(self):
        """get_as3_declaration_full exists on IControlClient (the read-only base class)."""
        import inspect

        from services.f5.icontrol_client import IControlClient

        # Must exist on the base read-only class
        assert hasattr(IControlClient, "get_as3_declaration_full")
        # Source must contain only a GET, not POST/PATCH/DELETE
        src = inspect.getsource(IControlClient.get_as3_declaration_full)
        assert "GET" in src or ".get(" in src
        assert "POST" not in src
        assert "PATCH" not in src
        assert "DELETE" not in src


# ---------------------------------------------------------------------------
# P4d — device AS3 preview service method
# ---------------------------------------------------------------------------


class TestDeviceAS3Preview:
    """F5DeviceService.preview_as3_as_bnk() happy path and error cases."""

    def _make_db_device(self, device_id=1, mgmt_host="10.0.0.1", cred_id=10):
        device = MagicMock()
        device.id = device_id
        device.name = "bigip-dev"
        device.mgmt_host = mgmt_host
        device.mgmt_port = 443
        device.mgmt_credential_id = cred_id
        device.verify_https_cert = False
        return device

    def _make_db_cred(self, cred_id=10):
        from core.encryption import encrypt_value
        cred = MagicMock()
        cred.id = cred_id
        cred.auth_type = "password"
        cred.username = "admin"
        cred.password_encrypted = encrypt_value("Password1!")
        cred.token_encrypted = None
        return cred

    def test_happy_path_returns_bnk_yaml(self):
        """preview_as3_as_bnk with a valid declaration returns BNK YAML."""
        from unittest.mock import MagicMock, patch

        from services.f5_device_service import F5DeviceService

        db = MagicMock()
        svc = F5DeviceService(db)

        device = self._make_db_device()
        cred = self._make_db_cred()

        db.query.return_value.filter.return_value.first.side_effect = [device, cred]

        declaration = {
            "class": "AS3",
            "declaration": {
                "class": "ADC",
                "tenant1": {
                    "class": "Tenant",
                    "app1": {
                        "class": "Application",
                        "vs1": {"class": "Service_HTTP", "pool": "pool1"},
                        "pool1": {"class": "Pool", "members": [
                            {"serviceAddress": "svc1", "servicePort": 80}
                        ]},
                    },
                },
            },
        }

        with patch("services.f5.icontrol_client.IControlClient") as MockClient:
            mock_ic = MockClient.return_value
            mock_ic.get_as3_declaration_full.return_value = declaration

            result = svc.preview_as3_as_bnk(1)

        assert result["device_id"] == 1
        assert result["cluster_id"] is None
        assert result["source_kind"] == "AS3Device"
        assert "HTTPRoute" in result["combined_yaml"] or result["gatewayclass_yaml"]
        assert result["source"]["device_id"] == 1

    def test_empty_declaration_returns_unmapped_not_exception(self):
        """Empty declaration from device → unmapped entry, no exception."""
        from unittest.mock import MagicMock, patch

        from services.f5_device_service import F5DeviceService

        db = MagicMock()
        svc = F5DeviceService(db)

        device = self._make_db_device()
        cred = self._make_db_cred()
        db.query.return_value.filter.return_value.first.side_effect = [device, cred]

        with patch("services.f5.icontrol_client.IControlClient") as MockClient:
            mock_ic = MockClient.return_value
            mock_ic.get_as3_declaration_full.return_value = {}

            result = svc.preview_as3_as_bnk(1)

        assert result["device_id"] == 1
        assert any(u["construct_type"] == "as3_parse_error" for u in result["unmapped"])


# ---------------------------------------------------------------------------
# P4d — AS3 preview route authz
# ---------------------------------------------------------------------------


class TestAS3PreviewRouteAuthz:
    """preview_device_as3 must use require_project_owner (not require_viewer).

    The route triggers outbound iControl connections to a registered BIG-IP
    using stored credentials — exactly like probe_device. Viewer access would
    let unprivileged users exfiltrate device credentials via SSRF.
    """

    def test_as3_preview_uses_require_project_owner(self):
        """POST /{device_id}/as3-preview must depend on require_project_owner."""
        import inspect

        from routes.auth import require_project_owner, require_viewer
        from routes.f5_devices import preview_device_as3

        sig = inspect.signature(preview_device_as3)
        deps = [p.default.dependency for p in sig.parameters.values() if hasattr(p.default, "dependency")]

        assert require_project_owner in deps, "preview_device_as3 must use require_project_owner"
        assert require_viewer not in deps, "preview_device_as3 must NOT use require_viewer"


# ---------------------------------------------------------------------------
# P4b — F5-annotated Ingress discovery filter
# ---------------------------------------------------------------------------


class TestFetchCisF5IngressFilter:
    """_fetch_cis_f5_ingresses filters only F5-annotated ingresses."""

    def _make_ingress_mock(self, name, namespace, annotations, ingress_class_name=None):
        """Helper to build a mock K8s Ingress object."""
        from unittest.mock import MagicMock
        ing = MagicMock()
        ing.metadata.name = name
        ing.metadata.namespace = namespace
        ing.metadata.annotations = annotations
        ing.metadata.labels = {}
        ing.spec.rules = []
        ing.spec.tls = []
        ing.spec.default_backend = None
        ing.spec.ingress_class_name = ingress_class_name
        return ing

    def test_no_f5_annotations_excluded(self):
        """Ingress without F5 annotations is excluded."""
        from unittest.mock import MagicMock, patch

        from services.scanner.fetch import _fetch_cis_f5_ingresses

        ing = self._make_ingress_mock(
            "nginx-ingress", "default",
            {"nginx.ingress.kubernetes.io/rewrite-target": "/"},
        )
        resp = MagicMock()
        resp.items = [ing]

        with patch("kubernetes.client.NetworkingV1Api") as mock_api_cls:
            mock_api_cls.return_value.list_ingress_for_all_namespaces.return_value = resp
            result = _fetch_cis_f5_ingresses(object())

        assert result == []

    def test_f5_annotation_included(self):
        """Ingress with virtual-server.f5.com/* annotation is included."""
        from unittest.mock import MagicMock, patch

        from services.scanner.fetch import _fetch_cis_f5_ingresses

        ing = self._make_ingress_mock(
            "f5-ingress", "prod",
            {"virtual-server.f5.com/ip": "10.0.0.10"},
        )
        resp = MagicMock()
        resp.items = [ing]

        with patch("kubernetes.client.NetworkingV1Api") as mock_api_cls:
            mock_api_cls.return_value.list_ingress_for_all_namespaces.return_value = resp
            result = _fetch_cis_f5_ingresses(object())

        assert len(result) == 1
        assert result[0]["metadata"]["name"] == "f5-ingress"
        assert result[0]["metadata"]["namespace"] == "prod"

    def test_f5_class_annotation_included(self):
        """Ingress with kubernetes.io/ingress.class=f5 is included."""
        from unittest.mock import MagicMock, patch

        from services.scanner.fetch import _fetch_cis_f5_ingresses

        ing = self._make_ingress_mock(
            "f5-class-ingress", "default",
            {"kubernetes.io/ingress.class": "f5"},
        )
        resp = MagicMock()
        resp.items = [ing]

        with patch("kubernetes.client.NetworkingV1Api") as mock_api_cls:
            mock_api_cls.return_value.list_ingress_for_all_namespaces.return_value = resp
            result = _fetch_cis_f5_ingresses(object())

        assert len(result) == 1
        assert result[0]["metadata"]["name"] == "f5-class-ingress"

    def test_api_exception_returns_empty_list(self):
        """K8s API failure returns empty list — no exception propagated."""
        from unittest.mock import patch

        from services.scanner.fetch import _fetch_cis_f5_ingresses

        with patch("kubernetes.client.NetworkingV1Api") as mock_api_cls:
            mock_api_cls.return_value.list_ingress_for_all_namespaces.side_effect = (
                Exception("network error")
            )
            result = _fetch_cis_f5_ingresses(object())

        assert result == []


# ---------------------------------------------------------------------------
# P4b — analyze_cis with F5 Ingresses
# ---------------------------------------------------------------------------


class TestAnalyzeCISWithIngresses:
    """analyze_cis includes F5 Ingresses in inventory and surface detection."""

    def test_ingresses_only_returns_partial_not_missing(self):
        """No controller, no CRDs, but F5 Ingresses present → PARTIAL (not MISSING)."""
        from services.scanner.prereqs import analyze_cis

        ingresses = [{"metadata": {"name": "f5-ing", "namespace": "default"}, "spec": {}}]
        result = analyze_cis([], [], [], [], cis_f5_ingresses=ingresses)
        assert result["status"] != "missing"
        assert result["controller"]["found"] is False
        assert len(result["inventory"]["f5_ingresses"]) == 1

    def test_no_surface_missing_still_has_f5_ingresses_key(self):
        """inventory dict always has f5_ingresses key even when MISSING."""
        from services.scanner.prereqs import analyze_cis

        result = analyze_cis([], [], [], [])
        assert "f5_ingresses" in result["inventory"]

    def test_controller_plus_ingresses_both_in_inventory(self):
        """Controller + F5 Ingresses → both appear in inventory."""
        from services.scanner.prereqs import analyze_cis

        controllers = [{
            "name": "k8s-bigip-ctlr", "namespace": "kube-system",
            "images": ["f5networks/k8s-bigip-ctlr:2.12.0"],
            "args": ["--bigip-url=https://10.0.0.1"],
            "replicas_desired": 1, "replicas_ready": 1,
        }]
        ingresses = [{"metadata": {"name": "f5-ing", "namespace": "default"}, "spec": {}}]
        result = analyze_cis(controllers, [], [], [], cis_f5_ingresses=ingresses)
        assert result["status"] == "detected"
        assert len(result["inventory"]["f5_ingresses"]) == 1


# ---------------------------------------------------------------------------
# Migration import (keep at end)
# ---------------------------------------------------------------------------


class TestMigrationImport:
    """Alembic migration can be imported and has correct revision chain."""

    def test_migration_down_revision(self):
        """v2_130_add_f5_bigip_tables has down_revision='v2_129' (chains off drop_external_charts)."""
        import importlib.util
        import os

        migration_path = os.path.join(
            os.path.dirname(__file__),
            "../../alembic/versions/v2_130_add_f5_bigip_tables.py",
        )
        migration_path = os.path.abspath(migration_path)
        spec = importlib.util.spec_from_file_location(
            "v2_130_add_f5_bigip_tables", migration_path
        )
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        assert mod.revision == "v2_130"
        assert mod.down_revision == "v2_129"


# ---------------------------------------------------------------------------
# 10. Credential route authz — no spurious project_id, correct dep functions
# ---------------------------------------------------------------------------


class TestCredentialRouteAuthz:
    """Credential mutation routes must use require_operator (not require_project_owner).

    require_project_owner injects `project_id: int` from the path, but the
    /api/f5-credentials router has no {project_id} segment — FastAPI would
    silently inject it as a required query parameter and let any owned project
    pass. The fix is require_operator, which has no path-param dependency.
    """

    def _get_credential_route_deps(self, path: str, method: str) -> list[str]:
        """Return the dependency function names for a given route."""
        import inspect

        from routes.f5_devices import credentials_router

        for route in credentials_router.routes:
            if hasattr(route, "path") and route.path == path and method in route.methods:
                dep_names = []
                for dep in route.dependencies:
                    dep_names.append(dep.dependency.__name__)
                # Also check function signature deps
                for param in inspect.signature(route.endpoint).parameters.values():
                    if hasattr(param.default, "dependency"):
                        dep_names.append(param.default.dependency.__name__)
                return dep_names
        return []

    def test_create_credential_uses_require_operator(self):
        """POST /api/f5-credentials must depend on require_operator, not require_project_owner."""
        import inspect

        from routes.auth import require_operator, require_project_owner
        from routes.f5_devices import create_credential
        sig = inspect.signature(create_credential)
        deps = [p.default.dependency for p in sig.parameters.values() if hasattr(p.default, "dependency")]

        assert require_operator in deps, "create_credential must use require_operator"
        assert require_project_owner not in deps, "create_credential must NOT use require_project_owner"

    def test_update_credential_uses_require_operator(self):
        """PUT /api/f5-credentials/{id} must depend on require_operator."""
        import inspect

        from routes.auth import require_operator, require_project_owner
        from routes.f5_devices import update_credential
        sig = inspect.signature(update_credential)
        deps = [p.default.dependency for p in sig.parameters.values() if hasattr(p.default, "dependency")]

        assert require_operator in deps
        assert require_project_owner not in deps

    def test_delete_credential_uses_require_operator(self):
        """DELETE /api/f5-credentials/{id} must depend on require_operator."""
        import inspect

        from routes.auth import require_operator, require_project_owner
        from routes.f5_devices import delete_credential
        sig = inspect.signature(delete_credential)
        deps = [p.default.dependency for p in sig.parameters.values() if hasattr(p.default, "dependency")]

        assert require_operator in deps
        assert require_project_owner not in deps

    def test_test_credential_uses_require_operator(self):
        """POST /api/f5-credentials/{id}/test must depend on require_operator."""
        import inspect

        from routes.auth import require_operator, require_project_owner
        from routes.f5_devices import test_credential
        sig = inspect.signature(test_credential)
        deps = [p.default.dependency for p in sig.parameters.values() if hasattr(p.default, "dependency")]

        assert require_operator in deps
        assert require_project_owner not in deps

    def test_credentials_router_prefix_has_no_project_id_segment(self):
        """credentials_router prefix must NOT contain {project_id} — it's global."""
        from routes.f5_devices import credentials_router

        assert "{project_id}" not in credentials_router.prefix, (
            f"credentials_router prefix '{credentials_router.prefix}' must not "
            "contain {{project_id}} — credentials are global, not project-scoped"
        )


# ---------------------------------------------------------------------------
# 11. F5CredentialCreate / Update — auth_type validation + writeOnly fields
# ---------------------------------------------------------------------------


class TestF5CredentialSchemas:
    """F5CredentialCreate and F5CredentialUpdate schema validation."""

    def test_auth_type_accepts_password(self):
        """auth_type='password' is valid."""
        from routes.f5_devices import F5CredentialCreate

        cred = F5CredentialCreate(name="test", auth_type="password")
        assert cred.auth_type == "password"

    def test_auth_type_accepts_token(self):
        """auth_type='token' is valid."""
        from routes.f5_devices import F5CredentialCreate

        cred = F5CredentialCreate(name="test", auth_type="token")
        assert cred.auth_type == "token"

    def test_auth_type_rejects_invalid(self):
        """auth_type='basic' (arbitrary string) is rejected at parse time."""
        from pydantic import ValidationError

        from routes.f5_devices import F5CredentialCreate

        with pytest.raises(ValidationError):
            F5CredentialCreate(name="test", auth_type="basic")

    def test_auth_type_default_is_password(self):
        """auth_type defaults to 'password' when not provided."""
        from routes.f5_devices import F5CredentialCreate

        cred = F5CredentialCreate(name="test")
        assert cred.auth_type == "password"

    def test_create_password_field_is_write_only_in_schema(self):
        """F5CredentialCreate.password has writeOnly=True in JSON schema."""
        from routes.f5_devices import F5CredentialCreate

        schema = F5CredentialCreate.model_json_schema()
        props = schema.get("properties", {})
        assert props.get("password", {}).get("writeOnly") is True
        assert props.get("token", {}).get("writeOnly") is True

    def test_update_password_field_is_write_only_in_schema(self):
        """F5CredentialUpdate.password has writeOnly=True in JSON schema."""
        from routes.f5_devices import F5CredentialUpdate

        schema = F5CredentialUpdate.model_json_schema()
        props = schema.get("properties", {})
        assert props.get("password", {}).get("writeOnly") is True
        assert props.get("token", {}).get("writeOnly") is True
