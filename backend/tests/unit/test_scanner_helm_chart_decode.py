"""
Unit tests for _decode_helm_release_secret in services.scanner.fetch.

The Helm 3 release secret payload is stored as base64(gzip(json)) in the
.data["release"] field.  The k8s Python client base64-decodes .data values
once; _decode_helm_release_secret applies the second b64 decode + gunzip.
"""

import base64
import gzip
import json

from services.scanner.fetch import _decode_helm_release_secret

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_payload(release_obj: dict) -> str:
    """Encode a release dict the way Helm 3 does: base64(gzip(json))."""
    raw_json = json.dumps(release_obj).encode()
    gzipped = gzip.compress(raw_json)
    return base64.b64encode(gzipped).decode()


def _make_release(chart_name: str = "f5-lifecycle-operator", chart_version: str = "2.21.13") -> dict:
    return {
        "chart": {
            "metadata": {
                "name": chart_name,
                "version": chart_version,
            }
        }
    }


# ---------------------------------------------------------------------------
# Tests for _decode_helm_release_secret
# ---------------------------------------------------------------------------


class TestDecodeHelmReleaseSecret:
    def test_decodes_valid_payload_returns_dict(self):
        payload = _make_payload(_make_release())
        result = _decode_helm_release_secret(payload)
        assert result is not None
        assert result["chart"]["metadata"]["name"] == "f5-lifecycle-operator"
        assert result["chart"]["metadata"]["version"] == "2.21.13"

    def test_none_input_returns_none(self):
        assert _decode_helm_release_secret(None) is None

    def test_empty_string_returns_none(self):
        assert _decode_helm_release_secret("") is None

    def test_invalid_base64_returns_none(self):
        assert _decode_helm_release_secret("not-valid-base64!!!") is None

    def test_valid_b64_but_not_gzip_returns_none(self):
        # valid b64 of random bytes that aren't gzip
        bad = base64.b64encode(b"plain text not gzip").decode()
        assert _decode_helm_release_secret(bad) is None

    def test_bytes_input_also_works(self):
        payload = _make_payload(_make_release("my-chart", "1.2.3")).encode()
        result = _decode_helm_release_secret(payload)
        assert result is not None
        assert result["chart"]["metadata"]["version"] == "1.2.3"

    def test_chart_metadata_preserved(self):
        rel = _make_release("f5-lifecycle-operator", "2.21.13")
        # Add extra fields that Helm stores
        rel["name"] = "flo"
        rel["namespace"] = "f5-bnk"
        payload = _make_payload(rel)
        result = _decode_helm_release_secret(payload)
        assert result is not None
        assert result.get("name") == "flo"
        assert result["chart"]["metadata"]["name"] == "f5-lifecycle-operator"
