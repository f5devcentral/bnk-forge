"""Cloud provider case-insensitivity: 'IBM' and 'ibm' must behave identically."""

import pytest

from routes.k8s._shared import ClusterCreateRequest, ClusterUpdateRequest
from utils.provider_config import normalize_cloud_provider


@pytest.mark.unit
class TestNormalizeCloudProvider:
    @pytest.mark.parametrize("raw,expected", [
        ("IBM", "ibm"),
        ("ibm", "ibm"),
        ("  IBM  ", "ibm"),
        ("Ibm", "ibm"),
        ("AWS", "aws"),
        ("ROKS", "roks"),
        ("", None),
        ("   ", None),
        (None, None),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_cloud_provider(raw) == expected


@pytest.mark.unit
class TestClusterRequestNormalization:
    def test_create_request_lowercases_provider(self):
        req = ClusterCreateRequest(name="c", kubeconfig="x", cloud_provider="IBM")
        assert req.cloud_provider == "ibm"

    def test_update_request_lowercases_provider(self):
        req = ClusterUpdateRequest(cloud_provider="IBM")
        assert req.cloud_provider == "ibm"

    def test_none_provider_stays_none(self):
        assert ClusterCreateRequest(name="c", kubeconfig="x").cloud_provider is None
