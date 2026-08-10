"""Unit tests for services.ibm_cloud_service COS HMAC resource-key creation."""

from unittest.mock import MagicMock, patch

import pytest

from core.errors import ServiceError
from services.ibm_cloud_service import IBMCloudService

CRN = "crn:v1:bluemix:public:cloud-object-storage:global:a/acct:inst::"


@patch("services.ibm_cloud_service.requests.post")
def test_create_cos_hmac_resource_key_uses_source_field(mock_post):
    # Regression: IBM Resource Controller expects the instance CRN under "source",
    # not "source_crn" — the wrong key returns HTTP 400 and HMAC derivation fails.
    resp = MagicMock(status_code=201, ok=True)
    resp.json.return_value = {
        "credentials": {"cos_hmac_keys": {"access_key_id": "AK", "secret_access_key": "SK"}}
    }
    mock_post.return_value = resp

    svc = IBMCloudService(None)
    out = svc._create_cos_hmac_resource_key("tok", CRN, "cred-name")

    assert out == {"access_key_id": "AK", "secret_access_key": "SK"}
    body = mock_post.call_args.kwargs["json"]
    assert body["source"] == CRN
    assert "source_crn" not in body


@patch("services.ibm_cloud_service.requests.post")
def test_create_cos_hmac_resource_key_surfaces_error_message(mock_post):
    resp = MagicMock(status_code=400, ok=False)
    resp.json.return_value = {"message": "source field is missing or has invalid data type."}
    mock_post.return_value = resp

    svc = IBMCloudService(None)
    with pytest.raises(ServiceError, match="source field is missing"):
        svc._create_cos_hmac_resource_key("tok", CRN, "cred-name")
