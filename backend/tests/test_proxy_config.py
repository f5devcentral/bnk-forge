"""
Tests for the nginx reverse proxy configuration.

Validates proxy/nginx.conf by parsing it as text — no nginx binary required.
Covers security headers, rate limiting, routing, timeouts, and best practices.

Run: pytest tests/test_proxy_config.py -v
"""

import os
import re

import pytest

# Path to nginx.conf — try multiple locations:
#   - Docker dev mount: /app/proxy/nginx.conf (one level up from /app/tests/)
#   - Host relative:    ../../proxy/nginx.conf (two levels up from backend/tests/)
_candidates = [
    os.path.join(os.path.dirname(__file__), "..", "proxy", "nginx.conf"),     # /app/proxy (Docker)
    os.path.join(os.path.dirname(__file__), "..", "..", "proxy", "nginx.conf"),  # host relative
]
NGINX_CONF_PATH = next((p for p in _candidates if os.path.isfile(p)), _candidates[0])


@pytest.fixture(scope="module")
def nginx_conf():
    """Load the nginx.conf content once for all tests."""
    assert os.path.isfile(NGINX_CONF_PATH), f"nginx.conf not found at {NGINX_CONF_PATH}"
    with open(NGINX_CONF_PATH) as f:
        return f.read()


# =========================================================================
# Security Headers
# =========================================================================


class TestSecurityHeaders:
    """Verify all required security headers are present."""

    def test_x_frame_options(self, nginx_conf):
        assert 'X-Frame-Options "SAMEORIGIN"' in nginx_conf

    def test_x_content_type_options(self, nginx_conf):
        assert 'X-Content-Type-Options "nosniff"' in nginx_conf

    def test_x_xss_protection(self, nginx_conf):
        assert 'X-XSS-Protection "1; mode=block"' in nginx_conf

    def test_referrer_policy(self, nginx_conf):
        assert 'Referrer-Policy "strict-origin-when-cross-origin"' in nginx_conf

    def test_permissions_policy(self, nginx_conf):
        assert "Permissions-Policy" in nginx_conf
        assert "camera=()" in nginx_conf
        assert "microphone=()" in nginx_conf
        assert "geolocation=()" in nginx_conf

    def test_server_tokens_off(self, nginx_conf):
        """server_tokens must be off to hide nginx version."""
        assert "server_tokens off" in nginx_conf


# =========================================================================
# Rate Limiting
# =========================================================================


class TestRateLimiting:
    """Verify rate limiting zones and their application."""

    def test_login_rate_limit_zone_defined(self, nginx_conf):
        """Login endpoint should have a strict rate limit zone."""
        match = re.search(r'limit_req_zone.*zone=login:\d+m\s+rate=(\d+)r/m', nginx_conf)
        assert match, "Login rate limit zone not found"
        rate = int(match.group(1))
        assert rate <= 10, f"Login rate limit too high: {rate}r/m (should be <=10)"

    def test_api_rate_limit_zone_defined(self, nginx_conf):
        """General API should have a moderate rate limit zone."""
        match = re.search(r'limit_req_zone.*zone=api:\d+m\s+rate=(\d+)r/s', nginx_conf)
        assert match, "API rate limit zone not found"

    def test_login_location_uses_login_zone(self, nginx_conf):
        """The /api/auth/login location must use the login rate limit zone."""
        # Find the login location block
        login_block = re.search(
            r'location\s*=\s*/api/auth/login\s*\{([^}]+)\}',
            nginx_conf,
            re.DOTALL,
        )
        assert login_block, "Login location block not found"
        assert "zone=login" in login_block.group(1)

    def test_api_location_uses_api_zone(self, nginx_conf):
        """The /api/ location must use the api rate limit zone."""
        # Find the /api/ location block
        api_block = re.search(
            r'location\s+/api/\s*\{([^}]+)\}',
            nginx_conf,
            re.DOTALL,
        )
        assert api_block, "API location block not found"
        assert "zone=api" in api_block.group(1)

    def test_rate_limit_returns_429(self, nginx_conf):
        """Rate limit responses should return HTTP 429."""
        assert "limit_req_status 429" in nginx_conf


# =========================================================================
# Routing
# =========================================================================


class TestRouting:
    """Verify proxy routing rules."""

    @staticmethod
    def _uses_loopback(nginx_conf: str, port: int) -> bool:
        return f"localhost:{port}" in nginx_conf or f"127.0.0.1:{port}" in nginx_conf

    def test_frontend_at_root(self, nginx_conf):
        """Root location (/) should proxy to the frontend via loopback on port 8080."""
        assert self._uses_loopback(nginx_conf, 8080)

    def test_api_proxies_to_backend(self, nginx_conf):
        """API location (/api/) should proxy to the backend via loopback on port 8000."""
        assert self._uses_loopback(nginx_conf, 8000)

    def test_websocket_location_exists(self, nginx_conf):
        """/ws/ location should exist for WebSocket proxy."""
        assert re.search(r'location\s+/ws/', nginx_conf)

    def test_websocket_upgrade_headers(self, nginx_conf):
        """WebSocket locations need Upgrade and Connection headers."""
        assert 'proxy_set_header Upgrade $http_upgrade' in nginx_conf
        assert 'proxy_set_header Connection "upgrade"' in nginx_conf

    def test_no_hardcoded_external_ips(self, nginx_conf):
        """Upstreams should not use hardcoded external IPs (localhost/127.0.0.1 are OK for host networking)."""
        # With host networking, proxy_pass to localhost/127.0.0.1 is correct.
        # Reject only non-loopback IPs (e.g. 10.x, 192.168.x).
        external_ips = re.findall(r'proxy_pass\s+http://(?!localhost|127\.0\.0\.1)\d+\.\d+\.\d+\.\d+', nginx_conf)
        assert len(external_ips) == 0, f"Found hardcoded external IP in proxy_pass: {external_ips}"

    def test_uses_localhost_upstreams(self, nginx_conf):
        """Host networking: upstreams should use loopback with explicit ports."""
        loopback_upstreams = re.findall(r'proxy_pass\s+http://(?:localhost|127\.0\.0\.1):\d+', nginx_conf)
        assert len(loopback_upstreams) >= 2, f"Expected at least 2 loopback upstreams (frontend + backend), found {len(loopback_upstreams)}"

    def test_proxy_http_version_1_1(self, nginx_conf):
        """API and WebSocket locations should use HTTP/1.1 for keep-alive."""
        assert "proxy_http_version 1.1" in nginx_conf


# =========================================================================
# Timeouts
# =========================================================================


class TestTimeouts:
    """Verify timeout values for different location types."""

    def test_api_timeout_300s(self, nginx_conf):
        """API location should have 300s timeouts for long-running operations."""
        api_block = re.search(
            r'location\s+/api/\s*\{([^}]+)\}',
            nginx_conf,
            re.DOTALL,
        )
        assert api_block, "API location block not found"
        block = api_block.group(1)
        assert "proxy_read_timeout 300s" in block

    def test_websocket_timeout_600s(self, nginx_conf):
        """WebSocket location should have 600s timeouts for terminal sessions."""
        ws_block = re.search(
            r'location\s+/ws/\s*\{([^}]+)\}',
            nginx_conf,
            re.DOTALL,
        )
        assert ws_block, "WebSocket location block not found"
        block = ws_block.group(1)
        assert "proxy_read_timeout 600s" in block


# =========================================================================
# Compression
# =========================================================================


class TestCompression:
    """Verify gzip compression is enabled and configured."""

    def test_gzip_enabled(self, nginx_conf):
        assert "gzip on" in nginx_conf

    def test_gzip_vary(self, nginx_conf):
        assert "gzip_vary on" in nginx_conf

    def test_gzip_includes_json(self, nginx_conf):
        """JSON responses should be compressed."""
        assert "application/json" in nginx_conf

    def test_gzip_includes_javascript(self, nginx_conf):
        assert "application/javascript" in nginx_conf


# =========================================================================
# DNS Resolution
# =========================================================================


class TestDNS:
    """Verify DNS resolver config matches networking mode."""

    def test_no_docker_dns_with_host_networking(self, nginx_conf):
        """Host networking: Docker DNS resolver (127.0.0.11) should NOT be present."""
        assert "resolver 127.0.0.11" not in nginx_conf, \
            "Docker DNS resolver should not be configured with host networking"


# =========================================================================
# Client Configuration
# =========================================================================


class TestClientConfig:
    """Verify client-facing configuration."""

    def test_client_max_body_size(self, nginx_conf):
        """Must allow large uploads (Helm charts, state files)."""
        match = re.search(r'client_max_body_size\s+(\d+)M', nginx_conf)
        assert match, "client_max_body_size not found"
        size = int(match.group(1))
        assert size >= 50, f"Max body size {size}M is too small (need >= 50M)"

    def test_buffering_disabled_for_api(self, nginx_conf):
        """API should have buffering disabled for real-time streaming."""
        assert "proxy_buffering off" in nginx_conf

    def test_standard_proxy_headers(self, nginx_conf):
        """Standard proxy headers should be set."""
        assert "X-Real-IP" in nginx_conf
        assert "X-Forwarded-For" in nginx_conf
        assert "X-Forwarded-Proto" in nginx_conf
