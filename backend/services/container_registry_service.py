"""
Container Registry Service — CRUD and connectivity testing for the
Container Registries access method.

Mirrors SSHCredentialService: global registries (no project FK), name-unique,
multiple entries, encrypted secrets that are never serialized.

Registry-type families
----------------------
Standalone types carry their own secret:
  * ghcr / quay / artifactory / harbor / distribution / oci — HTTP Basic with
    (username, token) against the registry v2 API.
  * dockerhub    — Docker Hub. Credentials validated at its token service
                   (auth.docker.io); /v2/ answers a Bearer challenge.
  * far          — F5 Artifact Registry. Ingests a gzip tarball containing one
                   Google-style service-account JSON; auth is HTTP Basic with
                   username '_json_key_base64' and password = base64(SA JSON).
                   Emits a dockerconfigjson for the FAR host.

Derived (ecr | icr) reference a CloudCredentialTemplate and exchange it for a
short-lived registry token at pull time (ecr → AWS GetAuthorizationToken,
icr → IBM IAM). Only types with a real exchange are offered.
"""

import base64
import gzip
import io
import json
import logging
import re
import tarfile
from datetime import UTC, datetime
from typing import Any

import requests
from sqlalchemy.orm import Session

from core.encryption import decrypt_value, encrypt_value
from core.errors import BadRequestError, NotFoundError
from models import CloudCredentialTemplate, ContainerRegistry
from models.container_registry import DERIVED_TYPES, REGISTRY_TYPES

logger = logging.getLogger(__name__)

# FAR uses a fixed Basic-auth username; the password is base64(SA JSON).
FAR_BASIC_USERNAME = "_json_key_base64"

# Docker Hub validates credentials at its token service, not on /v2/ (which
# answers a Bearer challenge), so it has a dedicated connectivity probe.
DOCKERHUB_TOKEN_URL = "https://auth.docker.io/token"
DOCKERHUB_TOKEN_SERVICE = "registry.docker.io"

# icr exchanges an IBM Cloud API key for an IAM bearer token; the registry
# accepts Basic auth with this fixed username and the IAM token as the password.
ICR_BASIC_USERNAME = "iamapikey"


class DerivedTokenExchangeError(BadRequestError):
    """Raised when a derived registry token exchange fails or is unsupported."""


class ContainerRegistryService:
    """Service layer for container registry operations."""

    def __init__(self, db: Session):
        self.db = db

    # ================================================================
    # Helpers
    # ================================================================

    def _get_registry(self, registry_id: int) -> ContainerRegistry:
        reg = self.db.query(ContainerRegistry).filter(
            ContainerRegistry.id == registry_id
        ).first()
        if not reg:
            raise NotFoundError("container_registry", registry_id)
        return reg

    @staticmethod
    def serialize(reg: ContainerRegistry) -> dict[str, Any]:
        """Build a response dict (never exposes secrets)."""
        return {
            "id": reg.id,
            "name": reg.name,
            "description": reg.description,
            "type": reg.type,
            "registry_host": reg.registry_host,
            "username": reg.username,
            "has_token": bool(reg.token_encrypted),
            "has_far_service_account": bool(reg.far_service_account_encrypted),
            "credential_template_id": reg.credential_template_id,
            "created_at": reg.created_at,
            "updated_at": reg.updated_at,
            "created_by": reg.created_by,
            "last_test_status": reg.last_test_status,
            "last_test_at": reg.last_test_at,
            "last_test_message": reg.last_test_message,
        }

    @staticmethod
    def _normalize_far_service_account(raw: str) -> str:
        """Resolve a FAR auth secret into the raw service-account JSON string.

        Accepts either:
          * the *.tgz auth-key tarball (base64-encoded), containing one *.json
            Google-style service account, OR
          * a raw service-account JSON string already extracted.

        Returns the canonical JSON string to persist.
        """
        candidate = raw.strip()

        # Already plain JSON?
        if candidate.startswith("{"):
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError as exc:
                raise BadRequestError(f"FAR service-account JSON is invalid: {exc}") from exc

        # Otherwise treat as a base64-encoded gzip tarball (the *.tgz auth key).
        try:
            blob = base64.b64decode(candidate, validate=True)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise BadRequestError(
                "FAR auth key must be the base64-encoded *.tgz auth-key tarball "
                "or a raw service-account JSON."
            ) from exc

        sa_json = ContainerRegistryService._extract_first_json_from_tgz(blob)
        # Validate it parses as JSON.
        try:
            json.loads(sa_json)
        except json.JSONDecodeError as exc:
            raise BadRequestError(f"FAR tarball *.json is not valid JSON: {exc}") from exc
        return sa_json

    @staticmethod
    def _extract_first_json_from_tgz(blob: bytes) -> str:
        """Extract the first *.json member from a gzip tarball."""
        try:
            with gzip.open(io.BytesIO(blob)) as gz:
                decompressed = gz.read()
        except (OSError, EOFError) as exc:
            raise BadRequestError(f"FAR auth key is not a valid gzip tarball: {exc}") from exc

        try:
            with tarfile.open(fileobj=io.BytesIO(decompressed)) as tar:
                for member in tar.getmembers():
                    if member.isfile() and member.name.endswith(".json"):
                        extracted = tar.extractfile(member)
                        if extracted is None:
                            continue
                        return extracted.read().decode("utf-8")
        except tarfile.TarError as exc:
            raise BadRequestError(f"FAR auth key tarball could not be read: {exc}") from exc

        raise BadRequestError("FAR auth-key tarball contains no *.json service account.")

    @staticmethod
    def build_far_dockerconfigjson(registry_host: str, sa_json: str) -> str:
        """Emit a base64 dockerconfigjson for a FAR registry host.

        username '_json_key_base64', password base64(SA JSON).
        """
        password = base64.b64encode(sa_json.encode("utf-8")).decode("ascii")
        auth = base64.b64encode(f"{FAR_BASIC_USERNAME}:{password}".encode()).decode("ascii")
        docker_config = {
            "auths": {
                registry_host: {
                    "username": FAR_BASIC_USERNAME,
                    "password": password,
                    "auth": auth,
                }
            }
        }
        return base64.b64encode(json.dumps(docker_config).encode("utf-8")).decode("ascii")

    # ================================================================
    # CRUD
    # ================================================================

    def list_registries(self) -> list[dict]:
        regs = self.db.query(ContainerRegistry).order_by(ContainerRegistry.name).all()
        return [self.serialize(r) for r in regs]

    def get_registry(self, registry_id: int) -> dict:
        return self.serialize(self._get_registry(registry_id))

    def create_registry(self, data, created_by: str | None = None) -> dict:
        if data.type not in REGISTRY_TYPES:
            raise BadRequestError(
                f"Unsupported registry type '{data.type}'. "
                f"Must be one of: {', '.join(REGISTRY_TYPES)}"
            )

        existing = self.db.query(ContainerRegistry).filter(
            ContainerRegistry.name == data.name
        ).first()
        if existing:
            raise BadRequestError(f"Container registry '{data.name}' already exists")

        reg = ContainerRegistry(
            name=data.name,
            description=data.description,
            type=data.type,
            registry_host=data.registry_host,
            created_by=created_by,
        )

        if data.type in DERIVED_TYPES:
            self._apply_derived_template(reg, data.credential_template_id, required=True)
        else:
            self._apply_standalone_secrets(reg, data)

        self.db.add(reg)
        self.db.flush()
        self.db.refresh(reg)
        return self.serialize(reg)

    @staticmethod
    def canonical_host(value: str | None) -> str:
        """Canonical form of a registry host, for COMPARISON.

        Every consumer already canonicalizes before matching —
        container_run_secrets and supply_chain both `.strip().lower()` — so a
        raw string comparison here made the guard more sensitive than any real
        behaviour. "harbor.internal" -> "Harbor.Internal" is a no-op for
        matching, for DNS and for the https://{host}/v2/ URL, yet it tripped the
        clearing and destroyed write-only credentials that cannot be re-obtained.

        That was reachable from the UI, not theoretical: the edit dialog's type
        dropdown writes DEFAULT_HOSTS[type], which is '' for artifactory/harbor/
        distribution/oci, and '' is not nullish so the ?? fallback does not fire.
        """
        host = (value or "").strip().lower().rstrip("/")
        # A default port is not a different host.
        for scheme_port in (":443", ":80"):
            if host.endswith(scheme_port):
                host = host[: -len(scheme_port)]
        return host

    def update_registry(self, registry_id: int, data) -> dict:
        reg = self._get_registry(registry_id)

        if data.name and data.name != reg.name:
            existing = self.db.query(ContainerRegistry).filter(
                ContainerRegistry.name == data.name
            ).first()
            if existing:
                raise BadRequestError(f"Container registry '{data.name}' already exists")

        update_data = data.model_dump(exclude_unset=True)

        new_type = update_data.get("type", reg.type)
        if "type" in update_data and new_type not in REGISTRY_TYPES:
            raise BadRequestError(
                f"Unsupported registry type '{new_type}'. "
                f"Must be one of: {', '.join(REGISTRY_TYPES)}"
            )

        # Secrets are handled explicitly; strip them from the plain copy.
        token = update_data.pop("token", None)
        far_service_account = update_data.pop("far_service_account", None)
        credential_template_id = update_data.pop("credential_template_id", "__unset__")

        old_type = reg.type
        old_host = reg.registry_host
        for key, value in update_data.items():
            if hasattr(reg, key):
                setattr(reg, key, value)

        # Repointing a registry at a different host INVALIDATES every stored
        # credential (issue #79, item 3).
        #
        # Registries are global and `require_operator` is the only gate on the
        # PUT, so any operator reaches any other operator's registry. Without
        # this, operator A changes registry_host on a registry operator B
        # configured and presses Test: `_test_basic_v2` decrypts B's token,
        # `_test_far` base64s B's service account, `_test_derived` mints a live
        # ECR token — each sent to A's host.
        #
        # UNCONDITIONAL on host change, deliberately. A previous version gated
        # this on "did the caller supply a new credential", which was a
        # disjunction over all three families and therefore bypassable by
        # supplying an OFF-family value: a `far_service_account: "{}"` (which
        # _normalize_far_service_account accepts, since any parseable JSON
        # passes) preserved a harbor record's token; replaying the record's own
        # credential_template_id — serialized to every viewer — preserved a
        # derived record's template. The clearing must not depend on what the
        # caller sent.
        #
        # The re-apply blocks below then put back only what WAS supplied in this
        # same request, which is the legitimate "move the registry and give it
        # new credentials" flow.
        #
        # Explicit None comparison rather than a truthiness test: registry_host
        # has no min_length at create, so an empty-string host is reachable and
        # `"" -> "attacker.example.com"` must still clear.
        host_changed = self.canonical_host(old_host) != self.canonical_host(reg.registry_host)
        if host_changed:
            reg.username = None
            reg.token_encrypted = None
            reg.far_service_account_encrypted = None
            reg.credential_template_id = None
            # Clear the whole cached verdict — leaving last_test_at behind
            # renders a stale success timestamp beside a null status.
            reg.last_test_status = None
            reg.last_test_at = None
            reg.last_test_message = (
                "Credentials cleared because the registry host changed; supply "
                "credentials for the new host before testing."
            )
            logger.warning(
                "Registry %s host changed %r -> %r; all stored credentials "
                "cleared to prevent them being sent to the new host.",
                reg.id, old_host, reg.registry_host,
            )

        # A type switch crosses credential families (basic-auth / far / derived).
        # Drop the previous family's credential so no stale secret survives under
        # a type that never reads it.
        if new_type != old_type:
            self._clear_off_family_credentials(reg)

        if token:
            reg.token_encrypted = encrypt_value(token)
        if far_service_account:
            reg.far_service_account_encrypted = encrypt_value(
                self._normalize_far_service_account(far_service_account)
            )
        if credential_template_id != "__unset__":
            # Re-applied even on a host change, deliberately.
            #
            # A template id is a public reference rather than a secret, so at
            # first glance honouring it here lets an attacker re-attach the
            # victim's credentials to their own host. But `create_registry`
            # already accepts ANY template id on a NEW registry at ANY host
            # (:212, no ownership check), so refusing it here buys nothing —
            # the same outcome is one POST away — while making a derived
            # registry's host impossible to change at all, since the
            # derived-type invariant below then rejects the update.
            #
            # The real gap is that credential templates carry no per-operator
            # authorisation on either path. That is a separate, pre-existing
            # issue and is filed as such; it is NOT closed by this change.
            self._apply_derived_template(reg, credential_template_id, required=False)

        # Switching to a derived type without a template leaves a registry that
        # can never resolve a pull credential — reject it up front.
        if new_type in DERIVED_TYPES and reg.credential_template_id is None:
            raise BadRequestError(
                f"Derived registry type '{new_type}' requires a credential_template_id."
            )

        reg.updated_at = datetime.now(UTC)
        self.db.flush()
        self.db.refresh(reg)
        return self.serialize(reg)

    def delete_registry(self, registry_id: int) -> None:
        reg = self._get_registry(registry_id)
        self.db.delete(reg)

    # ----------------------------------------------------------------

    def _apply_standalone_secrets(self, reg: ContainerRegistry, data) -> None:
        """Apply the standalone secret for ghcr/quay/far on create."""
        if reg.type == "far":
            if not data.far_service_account:
                raise BadRequestError(
                    "FAR registry requires 'far_service_account' "
                    "(base64 *.tgz auth key or raw service-account JSON)."
                )
            reg.far_service_account_encrypted = encrypt_value(
                self._normalize_far_service_account(data.far_service_account)
            )
        else:  # Basic-auth standalone (ghcr/quay/artifactory/harbor/distribution/oci)
            reg.username = data.username
            if data.token:
                reg.token_encrypted = encrypt_value(data.token)

    @staticmethod
    def _clear_off_family_credentials(reg: ContainerRegistry) -> None:
        """Drop every credential that ``reg.type``'s family does not use.

        Called on a type switch: each family reads exactly one credential
        (basic-auth username+token / far service account / derived template), so
        the other two are dead columns under the new type and must not linger.
        """
        if reg.type in DERIVED_TYPES:
            reg.username = None
            reg.token_encrypted = None
            reg.far_service_account_encrypted = None
        elif reg.type == "far":
            reg.username = None
            reg.token_encrypted = None
            reg.credential_template_id = None
        else:  # basic-auth standalone
            reg.far_service_account_encrypted = None
            reg.credential_template_id = None

    def _apply_derived_template(
        self, reg: ContainerRegistry, template_id: int | None, *, required: bool
    ) -> None:
        """Wire a derived registry to a CloudCredentialTemplate."""
        if template_id is None:
            if required:
                raise BadRequestError(
                    f"Derived registry type '{reg.type}' requires a "
                    f"credential_template_id."
                )
            reg.credential_template_id = None
            return

        template = self.db.query(CloudCredentialTemplate).filter(
            CloudCredentialTemplate.id == template_id
        ).first()
        if not template:
            raise NotFoundError("cloud_credential_template", template_id)
        reg.credential_template_id = template_id

    # ================================================================
    # Connectivity Testing
    # ================================================================

    def test_registry(self, registry_id: int) -> dict[str, Any]:
        """Test registry connectivity. Persists the outcome on the row.

        All types are tested live: Basic-auth standalone via /v2/, FAR via the
        _json_key_base64 scheme, Docker Hub via its token service, and derived
        (icr/ecr) via a real short-lived token exchange.
        """
        reg = self._get_registry(registry_id)

        if reg.type in DERIVED_TYPES:
            result = self._test_derived(reg)
        elif reg.type == "far":
            result = self._test_far(reg)
        elif reg.type == "dockerhub":
            result = self._test_dockerhub(reg)
        else:  # Basic-auth standalone (ghcr/quay/artifactory/harbor/distribution/oci)
            result = self._test_basic_v2(reg)

        ok = bool(result.get("success"))
        reg.last_test_status = "ok" if ok else "failed"
        reg.last_test_at = datetime.now(UTC)
        reg.last_test_message = result.get("message") or result.get("error") or None
        self.db.commit()

        return {
            **result,
            "last_test_status": reg.last_test_status,
            "last_test_at": reg.last_test_at.isoformat() if reg.last_test_at else None,
            "last_test_message": reg.last_test_message,
        }

    def _test_basic_v2(self, reg: ContainerRegistry) -> dict[str, Any]:
        """Test a Basic-auth standalone registry via the v2 API (ghcr/quay/artifactory/harbor/distribution/oci)."""
        token = decrypt_value(reg.token_encrypted) if reg.token_encrypted else None
        if not token:
            return {"success": False, "error": "No token configured for this registry."}

        url = f"https://{reg.registry_host}/v2/"
        auth = (reg.username or "", token)
        try:
            # allow_redirects=False: a 302 off the vetted host would carry the
            # Authorization header to wherever it points, re-opening the exfil
            # path the allowlist just closed.
            resp = requests.get(url, auth=auth, timeout=15, allow_redirects=False)
        except requests.RequestException as exc:
            return {"success": False, "error": f"Connection to {reg.registry_host} failed: {exc}"}

        if resp.status_code == 200:
            return {"success": True, "message": f"Authenticated to {reg.registry_host}."}
        if resp.status_code in (401, 403):
            return {
                "success": False,
                "error": f"Authentication rejected by {reg.registry_host} (HTTP {resp.status_code}).",
            }
        return {
            "success": False,
            "error": f"Unexpected response from {reg.registry_host} (HTTP {resp.status_code}).",
        }

    def _test_dockerhub(self, reg: ContainerRegistry) -> dict[str, Any]:
        """Test Docker Hub credentials via its token service.

        Docker Hub's ``/v2/`` returns a Bearer challenge rather than validating
        Basic auth, so we exchange the username + token/PAT at auth.docker.io —
        invalid credentials there return 401, valid ones return 200.
        """
        token = decrypt_value(reg.token_encrypted) if reg.token_encrypted else None
        if not token:
            return {"success": False, "error": "No token/password configured for Docker Hub."}

        try:
            resp = requests.get(
                DOCKERHUB_TOKEN_URL,
                params={"service": DOCKERHUB_TOKEN_SERVICE},
                auth=(reg.username or "", token),
                timeout=15,
            )
        except requests.RequestException as exc:
            return {"success": False, "error": f"Connection to Docker Hub failed: {exc}"}

        if resp.status_code == 200:
            return {"success": True, "message": "Authenticated to Docker Hub."}
        if resp.status_code in (401, 403):
            return {
                "success": False,
                "error": f"Docker Hub authentication rejected (HTTP {resp.status_code}).",
            }
        return {
            "success": False,
            "error": f"Unexpected Docker Hub response (HTTP {resp.status_code}).",
        }

    def _test_far(self, reg: ContainerRegistry) -> dict[str, Any]:
        """Test FAR via the registry v2 API using _json_key_base64 Basic auth."""
        sa_json = (
            decrypt_value(reg.far_service_account_encrypted)
            if reg.far_service_account_encrypted
            else None
        )
        if not sa_json:
            return {"success": False, "error": "No FAR service account configured."}

        password = base64.b64encode(sa_json.encode("utf-8")).decode("ascii")
        url = f"https://{reg.registry_host}/v2/"
        try:
            resp = requests.get(url, auth=(FAR_BASIC_USERNAME, password), timeout=15)
        except requests.RequestException as exc:
            return {"success": False, "error": f"Connection to {reg.registry_host} failed: {exc}"}

        if resp.status_code == 200:
            return {"success": True, "message": f"Authenticated to FAR {reg.registry_host}."}
        if resp.status_code in (401, 403):
            return {
                "success": False,
                "error": f"FAR authentication rejected by {reg.registry_host} (HTTP {resp.status_code}).",
            }
        return {
            "success": False,
            "error": f"Unexpected response from FAR {reg.registry_host} (HTTP {resp.status_code}).",
        }

    # Derived registries mint a LIVE cloud credential at test time, so their
    # host must look like the provider's. Unlike a standalone Harbor or
    # Artifactory — which is legitimately self-hosted on any name, and is why a
    # general host allowlist was rejected earlier — ECR and ICR hosts are
    # provider-shaped and therefore constrainable.
    _DERIVED_HOST_PATTERNS = {
        # ecr-fips is a real endpoint family (govcloud/regulated), public.ecr.aws
        # is ECR Public, and IBM exposes private.<region>.icr.io — all legitimate
        # and all refused by a first pass that was too tight. A false positive
        # here blocks a working registry, which is the same mistake as the
        # allowlist that would have broken self-hosted Harbor.
        "ecr": re.compile(
            r"^(\d+\.dkr\.ecr(-fips)?\.[a-z0-9-]+\.amazonaws\.com(\.cn)?|public\.ecr\.aws)$"
        ),
        "icr": re.compile(r"^([a-z0-9-]+\.)*icr\.io$"),
    }

    def _assert_derived_host_matches_provider(self, reg: ContainerRegistry) -> None:
        """Refuse to mint a cloud token for a host that is not the provider's.

        credential_template_id is a PUBLIC reference — it is serialized to every
        viewer — so an operator can repoint a derived registry at a host they
        control, replay the id, press Test, and receive a live ECR
        authorization token minted from someone else's AWS keys. Clearing on
        host change does not stop it, because the replayed id re-attaches the
        template in the same request; and templates carry no per-operator
        authorisation (a separate, wider gap).

        Constraining the DESTINATION is what actually closes the exfil: the
        token can only ever be sent to the cloud provider it came from.
        """
        pattern = self._DERIVED_HOST_PATTERNS.get(reg.type)
        if not pattern:
            return
        host = self.canonical_host(reg.registry_host)
        if not pattern.match(host):
            raise BadRequestError(
                f"registry_host '{reg.registry_host}' is not a valid {reg.type.upper()} "
                f"endpoint. A derived registry mints a live cloud credential when "
                f"tested, so it may only point at its own provider."
            )

    def _test_derived(self, reg: ContainerRegistry) -> dict[str, Any]:
        """Connectivity test for derived registry types (icr, ecr).

        Performs a real short-lived token exchange against the referenced
        CloudCredentialTemplate, then probes the registry v2 API with the
        exchanged credential.
        """
        self._assert_derived_host_matches_provider(reg)

        try:
            username, password = self.resolve_pull_credentials(reg)
        except DerivedTokenExchangeError as exc:
            return {"success": False, "type": reg.type, "error": str(exc)}

        url = f"https://{reg.registry_host}/v2/"
        try:
            # allow_redirects=False on every test path, not just basic-auth: a
            # 302 lets the server steer the probe, and this one carries a live
            # minted cloud token.
            resp = requests.get(
                url, auth=(username, password), timeout=15, allow_redirects=False
            )
        except requests.RequestException as exc:
            return {"success": False, "error": f"Connection to {reg.registry_host} failed: {exc}"}

        if resp.status_code == 200:
            return {"success": True, "message": f"Authenticated to {reg.registry_host}."}
        if resp.status_code in (401, 403):
            return {
                "success": False,
                "error": f"Authentication rejected by {reg.registry_host} (HTTP {resp.status_code}).",
            }
        return {
            "success": False,
            "error": f"Unexpected response from {reg.registry_host} (HTTP {resp.status_code}).",
        }

    # ================================================================
    # Pull-credential resolution (standalone + derived token exchange)
    # ================================================================

    def resolve_pull_credentials(self, reg: ContainerRegistry) -> tuple[str, str]:
        """Resolve ``(username, password)`` Basic-auth pair for pulling.

        For standalone types this returns the stored secret; for derived types
        (``icr`` / ``ecr``) it performs the short-lived registry-token exchange
        against the referenced CloudCredentialTemplate.
        """
        if reg.type == "far":
            sa_json = (
                decrypt_value(reg.far_service_account_encrypted)
                if reg.far_service_account_encrypted
                else None
            )
            if not sa_json:
                raise DerivedTokenExchangeError("No FAR service account configured.")
            password = base64.b64encode(sa_json.encode("utf-8")).decode("ascii")
            return FAR_BASIC_USERNAME, password

        if reg.type not in DERIVED_TYPES:  # Basic-auth standalone
            token = decrypt_value(reg.token_encrypted) if reg.token_encrypted else None
            if not token:
                raise DerivedTokenExchangeError("No token configured for this registry.")
            return reg.username or "", token

        # Derived: exchange the referenced cloud credential for a registry token.
        template = self._derived_template(reg)
        if reg.type == "icr":
            return ICR_BASIC_USERNAME, self._exchange_icr_token(template)
        if reg.type == "ecr":
            return self._exchange_ecr_token(reg, template)
        # Defensive: DERIVED_TYPES is limited to the implemented exchanges (icr/ecr).
        raise DerivedTokenExchangeError(
            f"No token exchange implemented for derived registry type '{reg.type}'."
        )

    def _derived_template(self, reg: ContainerRegistry) -> CloudCredentialTemplate:
        if reg.credential_template_id is None:
            raise DerivedTokenExchangeError(
                f"Derived registry '{reg.name}' has no credential template configured."
            )
        template = self.db.query(CloudCredentialTemplate).filter(
            CloudCredentialTemplate.id == reg.credential_template_id
        ).first()
        if not template:
            raise DerivedTokenExchangeError(
                f"Credential template {reg.credential_template_id} for registry "
                f"'{reg.name}' no longer exists."
            )
        return template

    def _exchange_icr_token(self, template: CloudCredentialTemplate) -> str:
        """Exchange the template's IBM Cloud API key for an IAM bearer token.

        IBM Cloud Container Registry accepts Basic auth with username
        ``iamapikey`` and the IAM access token as the password.
        """
        from services.ibm_cloud_service import IBMCloudService

        api_key = (
            decrypt_value(template.ibmcloud_api_key_encrypted)
            if template.ibmcloud_api_key_encrypted
            else None
        )
        if not api_key:
            raise DerivedTokenExchangeError(
                f"Credential template '{template.name}' has no IBM Cloud API key for icr."
            )
        try:
            return IBMCloudService(self.db)._exchange_api_key(api_key, template=template)
        except Exception as exc:  # IAM exchange failures → structured error
            raise DerivedTokenExchangeError(f"IBM Cloud IAM token exchange failed: {exc}") from exc

    def _exchange_ecr_token(
        self, reg: ContainerRegistry, template: CloudCredentialTemplate
    ) -> tuple[str, str]:
        """Exchange the template's AWS keys for an ECR authorization token.

        ECR ``GetAuthorizationToken`` returns a base64 ``user:password`` blob
        (user is always ``AWS``). Region is taken from the template, falling
        back to the region embedded in the ``*.dkr.ecr.<region>.amazonaws.com``
        host.
        """
        import boto3

        access_key = template.aws_access_key_id
        secret_key = (
            decrypt_value(template.aws_secret_access_key_encrypted)
            if template.aws_secret_access_key_encrypted
            else None
        )
        if not access_key or not secret_key:
            raise DerivedTokenExchangeError(
                f"Credential template '{template.name}' has no AWS access keys for ecr."
            )
        session_token = (
            decrypt_value(template.aws_session_token_encrypted)
            if template.aws_session_token_encrypted
            else None
        )
        region = template.region or self._ecr_region_from_host(reg.registry_host)
        if not region:
            raise DerivedTokenExchangeError(
                f"Could not resolve an AWS region for ecr registry '{reg.name}'."
            )

        try:
            client = boto3.client(
                "ecr",
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                aws_session_token=session_token,
            )
            resp = client.get_authorization_token()
        except Exception as exc:  # boto3 / AWS API failures → structured error
            raise DerivedTokenExchangeError(f"AWS ECR token exchange failed: {exc}") from exc

        data = (resp.get("authorizationData") or [{}])[0]
        encoded = data.get("authorizationToken")
        if not encoded:
            raise DerivedTokenExchangeError("ECR GetAuthorizationToken returned no token.")
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise DerivedTokenExchangeError(f"ECR authorization token is malformed: {exc}") from exc
        user, _, password = decoded.partition(":")
        if not password:
            raise DerivedTokenExchangeError("ECR authorization token has no password component.")
        return user or "AWS", password

    @staticmethod
    def _ecr_region_from_host(host: str) -> str | None:
        """Extract ``<region>`` from ``*.dkr.ecr.<region>.amazonaws.com``."""
        parts = (host or "").strip().lower().split(".")
        if "ecr" in parts:
            idx = parts.index("ecr")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return None
