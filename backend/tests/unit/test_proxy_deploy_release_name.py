"""
Unit tests for proxy_deploy_service._safe_release_name.

Auto-discovered targets often have long names (service+namespace concatenated).
Combined with chart-derived suffixes (envoy gateway-helm appends
`-gateway-helm-certgen`, 21 chars) the resulting k8s pod label can blow past
the 63-char limit.  The helper must keep the final label well under 63
while staying deterministic per (proxy_type, target_name).
"""

from services.proxy_deploy_service import (
    MAX_RELEASE_NAME_LEN,
    _safe_release_name,
)

# Worst chart suffix in our default set — envoy gateway-helm certgen Job.
ENVOY_CERTGEN_SUFFIX = "-gateway-helm-certgen"


class TestSafeReleaseName:
    def test_short_name_unchanged(self):
        # `perf-envoy-vllm` = 15 chars, well under the cap
        assert _safe_release_name("envoy", "vllm") == "perf-envoy-vllm"

    def test_long_name_truncated_under_cap(self):
        # The original failing case from production
        result = _safe_release_name("envoy", "vllm-agg-router-frontend-dynamo-system")
        assert len(result) <= MAX_RELEASE_NAME_LEN
        # Keeps recognizable prefix + ends with -<6 hex chars>
        assert result.startswith("perf-envoy-vllm-agg-router")
        assert result[-7] == "-"
        assert all(c in "0123456789abcdef" for c in result[-6:])

    def test_truncated_name_fits_envoy_certgen_suffix(self):
        """The whole point: truncated release + chart suffix must fit k8s label limit."""
        result = _safe_release_name("envoy", "vllm-agg-router-frontend-dynamo-system")
        assert len(result + ENVOY_CERTGEN_SUFFIX) <= 63

    def test_deterministic_for_same_input(self):
        # Idempotent re-deploys depend on this
        a = _safe_release_name("envoy", "very-long-target-name-with-many-segments-and-suffix")
        b = _safe_release_name("envoy", "very-long-target-name-with-many-segments-and-suffix")
        assert a == b

    def test_different_long_names_dont_collide(self):
        a = _safe_release_name("envoy", "very-long-target-name-with-suffix-A")
        b = _safe_release_name("envoy", "very-long-target-name-with-suffix-B")
        # Both truncate to the same prefix, but the hash discriminates them
        assert a != b

    def test_no_trailing_dash_before_hash(self):
        """Don't produce `perf-envoy-foo--abc123` if truncation lands on a dash."""
        # Construct an input where char at the truncation boundary is a dash
        target = "a" * 30 + "-" + "b" * 20
        result = _safe_release_name("envoy", target)
        assert "--" not in result

    def test_extreme_length_still_safe(self):
        result = _safe_release_name("envoy", "x" * 200)
        assert len(result) <= MAX_RELEASE_NAME_LEN
        assert len(result + ENVOY_CERTGEN_SUFFIX) <= 63
