"""
Component tests for ContainerRegistryService — CRUD, encryption, FAR ingest,
and connectivity testing (ghcr standalone + far standalone + derived stub).

Uses a real SQLite DB (from conftest) with mocked encryption and mocked HTTP.
Exercises the service layer directly without HTTP routing.
"""

import base64
import gzip
import io
import json
import tarfile
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, NotFoundError
from models import CloudCredentialTemplate, ContainerRegistry
from routes.container_registries import ContainerRegistryCreate, ContainerRegistryUpdate
from services.container_registry_service import (
    FAR_BASIC_USERNAME,
    ContainerRegistryService,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _insert_registry(db, **overrides):
    defaults = dict(
        name="ghcr-prod",
        type="ghcr",
        registry_host="ghcr.io",
        username="jgruberf5",
        token_encrypted="enc_token",
    )
    defaults.update(overrides)
    reg = ContainerRegistry(**defaults)
    db.add(reg)
    db.flush()
    db.refresh(reg)
    return reg


def _make_create(**overrides) -> ContainerRegistryCreate:
    defaults = dict(
        name="ghcr-new",
        type="ghcr",
        registry_host="ghcr.io",
        username="jgruberf5",
        token="ghp_faketoken",
    )
    defaults.update(overrides)
    return ContainerRegistryCreate(**defaults)


def _make_far_tgz_b64(sa: dict) -> str:
    """Build a base64-encoded gzip tarball containing one SA JSON file."""
    sa_bytes = json.dumps(sa).encode("utf-8")
    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w") as tar:
        info = tarfile.TarInfo(name="f5-cne-far-auth-key.json")
        info.size = len(sa_bytes)
        tar.addfile(info, io.BytesIO(sa_bytes))
    gz = gzip.compress(raw_tar.getvalue())
    return base64.b64encode(gz).decode("ascii")


SAMPLE_SA = {"type": "service_account", "project_id": "f5-far", "private_key": "x"}


# ── Serialization ────────────────────────────────────────────────────


class TestSerialize:
    def test_serialize_includes_expected_keys(self, db):
        reg = _insert_registry(db)
        result = ContainerRegistryService.serialize(reg)
        assert result["id"] == reg.id
        assert result["name"] == "ghcr-prod"
        assert result["type"] == "ghcr"
        assert result["registry_host"] == "ghcr.io"
        assert result["username"] == "jgruberf5"

    def test_serialize_never_exposes_secrets(self, db):
        reg = _insert_registry(
            db,
            token_encrypted="enc_token",
            far_service_account_encrypted="enc_sa",
        )
        result = ContainerRegistryService.serialize(reg)
        assert result["has_token"] is True
        assert result["has_far_service_account"] is True
        assert "token_encrypted" not in result
        assert "far_service_account_encrypted" not in result


# ── List / Get ───────────────────────────────────────────────────────


class TestListGet:
    def test_list_empty(self, db):
        assert ContainerRegistryService(db).list_registries() == []

    def test_list_returns_all(self, db):
        _insert_registry(db, name="reg-a")
        _insert_registry(db, name="reg-b")
        result = ContainerRegistryService(db).list_registries()
        assert {r["name"] for r in result} == {"reg-a", "reg-b"}

    def test_get_found(self, db):
        reg = _insert_registry(db)
        result = ContainerRegistryService(db).get_registry(reg.id)
        assert result["id"] == reg.id

    def test_get_not_found(self, db):
        with pytest.raises(NotFoundError):
            ContainerRegistryService(db).get_registry(99999)


# ── Create ───────────────────────────────────────────────────────────


class TestCreate:
    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_ghcr_success(self, _mock_enc, db):
        result = ContainerRegistryService(db).create_registry(_make_create())
        assert result["name"] == "ghcr-new"
        assert result["type"] == "ghcr"
        assert result["has_token"] is True
        assert result["username"] == "jgruberf5"

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_records_created_by(self, _mock_enc, db):
        result = ContainerRegistryService(db).create_registry(_make_create(), created_by="alice")
        assert result["created_by"] == "alice"

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_ghcr_encrypts_token(self, _mock_enc, db):
        result = ContainerRegistryService(db).create_registry(_make_create(token="secret-pat"))
        db_reg = db.query(ContainerRegistry).filter(ContainerRegistry.id == result["id"]).first()
        assert db_reg.token_encrypted == "enc_secret-pat"

    @pytest.mark.parametrize("reg_type", ["artifactory", "harbor", "distribution", "oci"])
    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_self_hosted_basic_type_success(self, _mock_enc, db, reg_type):
        # Self-hostable Basic-auth registries: own username+token, any host.
        result = ContainerRegistryService(db).create_registry(
            _make_create(name=f"{reg_type}-1", type=reg_type, registry_host="registry.internal:5000")
        )
        assert result["type"] == reg_type
        assert result["registry_host"] == "registry.internal:5000"
        assert result["has_token"] is True

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_duplicate_name_rejected(self, _mock_enc, db):
        _insert_registry(db, name="dup")
        with pytest.raises(BadRequestError, match="already exists"):
            ContainerRegistryService(db).create_registry(_make_create(name="dup"))

    def test_create_invalid_type_rejected(self, db):
        with pytest.raises(BadRequestError, match="Unsupported registry type"):
            ContainerRegistryService(db).create_registry(_make_create(type="bogus-type"))

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_far_from_tgz(self, _mock_enc, db):
        tgz_b64 = _make_far_tgz_b64(SAMPLE_SA)
        data = _make_create(
            name="far-prod", type="far", registry_host="far.f5.com",
            username=None, token=None, far_service_account=tgz_b64,
        )
        result = ContainerRegistryService(db).create_registry(data)
        assert result["has_far_service_account"] is True
        db_reg = db.query(ContainerRegistry).filter(ContainerRegistry.id == result["id"]).first()
        # Stored value is the normalized SA JSON, encrypted.
        assert db_reg.far_service_account_encrypted == f"enc_{json.dumps(SAMPLE_SA)}"

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_far_from_raw_json(self, _mock_enc, db):
        data = _make_create(
            name="far-raw", type="far", registry_host="far.f5.com",
            username=None, token=None, far_service_account=json.dumps(SAMPLE_SA),
        )
        result = ContainerRegistryService(db).create_registry(data)
        assert result["has_far_service_account"] is True

    def test_create_far_without_secret_rejected(self, db):
        data = _make_create(
            name="far-bad", type="far", registry_host="far.f5.com",
            username=None, token=None, far_service_account=None,
        )
        with pytest.raises(BadRequestError, match="far_service_account"):
            ContainerRegistryService(db).create_registry(data)

    def test_create_derived_requires_template(self, db):
        data = _make_create(
            name="ecr-bad", type="ecr", registry_host="123.dkr.ecr.us-east-1.amazonaws.com",
            username=None, token=None, credential_template_id=None,
        )
        with pytest.raises(BadRequestError, match="credential_template_id"):
            ContainerRegistryService(db).create_registry(data)

    def test_create_derived_with_valid_template(self, db):
        tpl = CloudCredentialTemplate(name="aws-tpl", provider="aws")
        db.add(tpl)
        db.flush()
        data = _make_create(
            name="ecr-good", type="ecr", registry_host="123.dkr.ecr.us-east-1.amazonaws.com",
            username=None, token=None, credential_template_id=tpl.id,
        )
        result = ContainerRegistryService(db).create_registry(data)
        assert result["credential_template_id"] == tpl.id

    def test_create_derived_missing_template_404(self, db):
        data = _make_create(
            name="ecr-missing", type="ecr", registry_host="x",
            username=None, token=None, credential_template_id=99999,
        )
        with pytest.raises(NotFoundError):
            ContainerRegistryService(db).create_registry(data)


# ── Update ───────────────────────────────────────────────────────────


class TestUpdate:
    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_name(self, _mock_enc, db):
        reg = _insert_registry(db, name="old")
        result = ContainerRegistryService(db).update_registry(reg.id, ContainerRegistryUpdate(name="new"))
        assert result["name"] == "new"

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_token_encrypted(self, _mock_enc, db):
        reg = _insert_registry(db)
        ContainerRegistryService(db).update_registry(reg.id, ContainerRegistryUpdate(token="rotated"))
        db.refresh(reg)
        assert reg.token_encrypted == "enc_rotated"

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_duplicate_name_rejected(self, _mock_enc, db):
        _insert_registry(db, name="taken")
        reg = _insert_registry(db, name="mine")
        with pytest.raises(BadRequestError, match="already exists"):
            ContainerRegistryService(db).update_registry(reg.id, ContainerRegistryUpdate(name="taken"))

    def test_update_not_found(self, db):
        with pytest.raises(NotFoundError):
            ContainerRegistryService(db).update_registry(99999, ContainerRegistryUpdate(name="x"))

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_type_switch_to_far_clears_basic_auth_secret(self, _mock_enc, db):
        reg = _insert_registry(db, type="ghcr", username="jgruberf5", token_encrypted="enc_ghp_x")
        ContainerRegistryService(db).update_registry(
            reg.id,
            ContainerRegistryUpdate(
                type="far",
                registry_host="repo.f5.com",
                far_service_account=json.dumps(SAMPLE_SA),
            ),
        )
        db.refresh(reg)
        assert reg.far_service_account_encrypted is not None
        assert reg.token_encrypted is None
        assert reg.username is None

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_type_switch_to_derived_clears_standalone_secret(self, _mock_enc, db):
        template = CloudCredentialTemplate(name="ibm-tpl", provider="ibm")
        db.add(template)
        db.flush()
        reg = _insert_registry(db, type="ghcr", username="jgruberf5", token_encrypted="enc_ghp_x")

        ContainerRegistryService(db).update_registry(
            reg.id,
            ContainerRegistryUpdate(
                type="icr", registry_host="us.icr.io", credential_template_id=template.id
            ),
        )
        db.refresh(reg)
        assert reg.credential_template_id == template.id
        assert reg.token_encrypted is None
        assert reg.username is None
        assert reg.far_service_account_encrypted is None

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_type_switch_from_derived_clears_template(self, _mock_enc, db):
        template = CloudCredentialTemplate(name="ibm-tpl", provider="ibm")
        db.add(template)
        db.flush()
        reg = _insert_registry(
            db, type="icr", registry_host="us.icr.io", username=None,
            token_encrypted=None, credential_template_id=template.id,
        )

        ContainerRegistryService(db).update_registry(
            reg.id,
            ContainerRegistryUpdate(
                type="ghcr", registry_host="ghcr.io", username="jgruberf5", token="ghp_new"
            ),
        )
        db.refresh(reg)
        assert reg.credential_template_id is None
        assert reg.token_encrypted == "enc_ghp_new"

    @patch("services.container_registry_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_type_switch_to_derived_without_template_rejected(self, _mock_enc, db):
        reg = _insert_registry(db, type="ghcr")
        with pytest.raises(BadRequestError, match="credential_template_id"):
            ContainerRegistryService(db).update_registry(
                reg.id, ContainerRegistryUpdate(type="icr", registry_host="us.icr.io")
            )


# ── Delete ───────────────────────────────────────────────────────────


class TestDelete:
    def test_delete_success(self, db):
        reg = _insert_registry(db)
        rid = reg.id
        ContainerRegistryService(db).delete_registry(rid)
        db.flush()
        assert db.query(ContainerRegistry).filter(ContainerRegistry.id == rid).first() is None

    def test_delete_not_found(self, db):
        with pytest.raises(NotFoundError):
            ContainerRegistryService(db).delete_registry(99999)


# ── Test (connectivity) ──────────────────────────────────────────────


class TestTestRegistry:
    @patch("services.container_registry_service.decrypt_value", side_effect=lambda v: v.replace("enc_", ""))
    @patch("services.container_registry_service.requests.get")
    def test_ghcr_success(self, mock_get, _mock_dec, db):
        reg = _insert_registry(db, token_encrypted="enc_ghp_x")
        mock_get.return_value = MagicMock(status_code=200)

        result = ContainerRegistryService(db).test_registry(reg.id)
        assert result["success"] is True
        assert result["last_test_status"] == "ok"
        # Basic auth with (username, token)
        _, kwargs = mock_get.call_args
        assert kwargs["auth"] == ("jgruberf5", "ghp_x")

    @patch("services.container_registry_service.decrypt_value", side_effect=lambda v: v.replace("enc_", ""))
    @patch("services.container_registry_service.requests.get")
    def test_harbor_self_hosted_uses_basic_v2(self, mock_get, _mock_dec, db):
        # A self-hosted Harbor routes through the Basic-auth /v2/ path on its own host.
        reg = _insert_registry(
            db, name="harbor-1", type="harbor", registry_host="harbor.internal",
            username="robot$ci", token_encrypted="enc_robottoken",
        )
        mock_get.return_value = MagicMock(status_code=200)
        result = ContainerRegistryService(db).test_registry(reg.id)
        assert result["success"] is True
        args, kwargs = mock_get.call_args
        assert args[0] == "https://harbor.internal/v2/"
        assert kwargs["auth"] == ("robot$ci", "robottoken")

    @patch("services.container_registry_service.decrypt_value", side_effect=lambda v: v.replace("enc_", ""))
    @patch("services.container_registry_service.requests.get")
    def test_ghcr_auth_rejected(self, mock_get, _mock_dec, db):
        reg = _insert_registry(db, token_encrypted="enc_bad")
        mock_get.return_value = MagicMock(status_code=401)
        result = ContainerRegistryService(db).test_registry(reg.id)
        assert result["success"] is False
        assert result["last_test_status"] == "failed"

    @patch("services.container_registry_service.decrypt_value", side_effect=lambda v: v.replace("enc_", ""))
    @patch("services.container_registry_service.requests.get")
    def test_far_success_uses_json_key_basic(self, mock_get, _mock_dec, db):
        sa_json = json.dumps(SAMPLE_SA)
        reg = _insert_registry(
            db, name="far-test", type="far", registry_host="far.f5.com",
            username=None, token_encrypted=None,
            far_service_account_encrypted=f"enc_{sa_json}",
        )
        mock_get.return_value = MagicMock(status_code=200)

        result = ContainerRegistryService(db).test_registry(reg.id)
        assert result["success"] is True
        assert result["last_test_status"] == "ok"
        _, kwargs = mock_get.call_args
        user, password = kwargs["auth"]
        assert user == FAR_BASIC_USERNAME
        assert password == base64.b64encode(sa_json.encode()).decode("ascii")

    @patch("services.container_registry_service.decrypt_value", side_effect=lambda v: v.replace("enc_", ""))
    @patch("services.container_registry_service.requests.get")
    def test_dockerhub_validates_via_token_service(self, mock_get, _mock_dec, db):
        # Docker Hub /v2/ answers a Bearer challenge, so creds are validated at
        # the token service (auth.docker.io), not via Basic on /v2/.
        reg = _insert_registry(
            db, name="dockerhub-1", type="dockerhub", registry_host="docker.io",
            username="myuser", token_encrypted="enc_dckr_pat",
        )
        mock_get.return_value = MagicMock(status_code=200)
        result = ContainerRegistryService(db).test_registry(reg.id)
        assert result["success"] is True
        assert result["last_test_status"] == "ok"
        args, kwargs = mock_get.call_args
        assert args[0] == "https://auth.docker.io/token"
        assert kwargs["params"] == {"service": "registry.docker.io"}
        assert kwargs["auth"] == ("myuser", "dckr_pat")

    def test_create_rejects_removed_acr_gar_types(self, db):
        # acr/gar were removed (no real token exchange). They must not validate.
        with pytest.raises(BadRequestError):
            ContainerRegistryService(db).create_registry(
                _make_create(name="acr-x", type="acr", registry_host="x.azurecr.io")
            )

    @patch("services.container_registry_service.decrypt_value", side_effect=lambda v: v.replace("enc_", ""))
    @patch("services.container_registry_service.requests.get")
    def test_derived_icr_exchanges_iam_token(self, mock_get, _mock_dec, db):
        tpl = CloudCredentialTemplate(
            name="ibm-tpl", provider="ibm",
            ibmcloud_api_key_encrypted="enc_ibm_api_key",
        )
        db.add(tpl)
        db.flush()
        reg = _insert_registry(
            db, name="icr-test", type="icr", registry_host="us.icr.io",
            username=None, token_encrypted=None, credential_template_id=tpl.id,
        )
        mock_get.return_value = MagicMock(status_code=200)

        with patch(
            "services.ibm_cloud_service.IBMCloudService._exchange_api_key",
            return_value="iam-bearer-token",
        ) as mock_exchange:
            result = ContainerRegistryService(db).test_registry(reg.id)

        assert result["success"] is True
        assert result["last_test_status"] == "ok"
        mock_exchange.assert_called_once()
        # icr Basic auth = (iamapikey, IAM token)
        _, kwargs = mock_get.call_args
        assert kwargs["auth"] == ("iamapikey", "iam-bearer-token")

    @patch("services.container_registry_service.decrypt_value", side_effect=lambda v: v.replace("enc_", ""))
    @patch("services.container_registry_service.requests.get")
    def test_derived_ecr_exchanges_authorization_token(self, mock_get, _mock_dec, db):
        tpl = CloudCredentialTemplate(
            name="aws-ecr-tpl", provider="aws", region="us-east-1",
            aws_access_key_id="AKIA_X", aws_secret_access_key_encrypted="enc_secret",
        )
        db.add(tpl)
        db.flush()
        reg = _insert_registry(
            db, name="ecr-test", type="ecr",
            registry_host="123.dkr.ecr.us-east-1.amazonaws.com",
            username=None, token_encrypted=None, credential_template_id=tpl.id,
        )
        mock_get.return_value = MagicMock(status_code=200)

        token = base64.b64encode(b"AWS:ecr-password").decode("ascii")
        fake_client = MagicMock()
        fake_client.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": token}]
        }
        with patch("boto3.client", return_value=fake_client) as mock_boto:
            result = ContainerRegistryService(db).test_registry(reg.id)

        assert result["success"] is True
        assert result["last_test_status"] == "ok"
        # boto3 ECR client built with the template's region + keys.
        _, boto_kwargs = mock_boto.call_args
        assert boto_kwargs["region_name"] == "us-east-1"
        assert boto_kwargs["aws_access_key_id"] == "AKIA_X"
        # ECR Basic auth = (AWS, decoded password)
        _, kwargs = mock_get.call_args
        assert kwargs["auth"] == ("AWS", "ecr-password")

    def test_derived_ecr_region_from_host_when_template_has_none(self, db):
        tpl = CloudCredentialTemplate(
            name="aws-ecr-noreg", provider="aws",
            aws_access_key_id="AKIA_Y", aws_secret_access_key_encrypted="enc_secret",
        )
        db.add(tpl)
        db.flush()
        reg = _insert_registry(
            db, name="ecr-noreg", type="ecr",
            registry_host="999.dkr.ecr.eu-west-2.amazonaws.com",
            username=None, token_encrypted=None, credential_template_id=tpl.id,
        )
        assert (
            ContainerRegistryService._ecr_region_from_host(reg.registry_host) == "eu-west-2"
        )


# ── FAR dockerconfigjson emission ────────────────────────────────────


class TestFarDockerConfig:
    def test_build_far_dockerconfigjson(self):
        sa_json = json.dumps(SAMPLE_SA)
        out_b64 = ContainerRegistryService.build_far_dockerconfigjson("far.f5.com", sa_json)
        decoded = json.loads(base64.b64decode(out_b64))
        entry = decoded["auths"]["far.f5.com"]
        assert entry["username"] == FAR_BASIC_USERNAME
        assert entry["password"] == base64.b64encode(sa_json.encode()).decode("ascii")
        expected_auth = base64.b64encode(
            f"{FAR_BASIC_USERNAME}:{entry['password']}".encode()
        ).decode("ascii")
        assert entry["auth"] == expected_auth
