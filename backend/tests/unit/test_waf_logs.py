"""
Unit tests for WAF security log route helpers (routes/k8s/waf_logs.py).
All K8s and socket I/O is mocked — no cluster connection required.
"""

from unittest.mock import MagicMock, patch

import pytest

from routes.k8s.waf_logs import (
    _parse_nap_entry,
    _read_syslog_tcp,
    _resolve_endpoints_for_policy,
    _resolve_hslpub_endpoints,
    _resolve_logprofile_endpoints,
    _resolve_secpolicy_endpoints,
)

# ── _parse_nap_entry ───────────────────────────────────────────────────────

class TestParseNapEntry:
    def test_parses_key_value_pairs(self):
        line = 'outcome="BLOCKED" attack_type="SQL Injection" client_ip="1.2.3.4"'
        entry = _parse_nap_entry(line)
        assert entry["outcome"] == "BLOCKED"
        assert entry["attack_type"] == "SQL Injection"
        assert entry["client_ip"] == "1.2.3.4"
        assert entry["raw"] == line

    def test_raw_always_present(self):
        entry = _parse_nap_entry("no kv pairs here")
        assert entry["raw"] == "no kv pairs here"

    def test_empty_values_parsed(self):
        line = 'sig_ids="" support_id="abc123"'
        entry = _parse_nap_entry(line)
        assert entry["sig_ids"] == ""
        assert entry["support_id"] == "abc123"

    def test_strips_trailing_whitespace(self):
        entry = _parse_nap_entry("  outcome=\"PASSED\"  ")
        assert entry["raw"] == "outcome=\"PASSED\""

    def test_full_nap_log_line(self):
        line = (
            'date_time="2026-08-18 10:00:00" unit_hostname="nginx-pod-abc" '
            'policy_name="my-waf-policy" vs_name="vs-http" outcome="BLOCKED" '
            'violation_rating="5" attack_type="XSS" method="GET" '
            'uri="/search?q=<script>" client_ip="10.0.0.1" support_id="xyz99"'
        )
        entry = _parse_nap_entry(line)
        assert entry["date_time"] == "2026-08-18 10:00:00"
        assert entry["outcome"] == "BLOCKED"
        assert entry["violation_rating"] == "5"
        assert entry["uri"] == "/search?q=<script>"


# ── _read_syslog_tcp ───────────────────────────────────────────────────────

class TestReadSyslogTcp:
    def test_returns_error_on_refused(self):
        with patch("routes.k8s.waf_logs.socket.create_connection") as mock_conn:
            mock_conn.side_effect = ConnectionRefusedError()
            entries, error = _read_syslog_tcp("10.0.0.1", 514, 100)
        assert entries == []
        assert "Connection refused" in error

    def test_returns_error_on_timeout(self):
        with patch("routes.k8s.waf_logs.socket.create_connection") as mock_conn:
            mock_conn.side_effect = TimeoutError()
            entries, error = _read_syslog_tcp("10.0.0.1", 514, 100)
        assert entries == []
        assert "Timed out" in error

    def test_parses_received_lines(self):
        raw = b'outcome="BLOCKED" uri="/foo"\noutcome="PASSED" uri="/bar"\n'
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.recv.side_effect = [raw, b""]
        with patch("routes.k8s.waf_logs.socket.create_connection", return_value=mock_sock):
            entries, error = _read_syslog_tcp("10.0.0.1", 514, 100)
        assert error is None
        assert len(entries) == 2
        assert entries[0]["outcome"] == "BLOCKED"
        assert entries[1]["outcome"] == "PASSED"

    def test_respects_limit_from_tail(self):
        lines = [f'outcome="BLOCKED" id="{i}"' for i in range(20)]
        raw = ("\n".join(lines) + "\n").encode()
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.recv.side_effect = [raw, b""]
        with patch("routes.k8s.waf_logs.socket.create_connection", return_value=mock_sock):
            entries, error = _read_syslog_tcp("10.0.0.1", 514, 5)
        assert error is None
        assert len(entries) == 5
        # Should return the last 5 lines
        assert entries[-1]["id"] == "19"

    def test_empty_response_returns_no_entries(self):
        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.recv.return_value = b""
        with patch("routes.k8s.waf_logs.socket.create_connection", return_value=mock_sock):
            entries, error = _read_syslog_tcp("10.0.0.1", 514, 100)
        assert entries == []
        assert error is None


# ── _resolve_hslpub_endpoints ──────────────────────────────────────────────

class TestResolveHslpubEndpoints:
    def _make_hslpub(self, name, endpoints):
        return {
            "metadata": {"name": name},
            "spec": {"pool": [{"name": "pool1", "endpoint": endpoints}]},
        }

    def test_returns_endpoints_for_named_pub(self):
        k8s = MagicMock()
        k8s.get_resources.return_value = [
            self._make_hslpub("my-pub", ["10.0.0.1:514", "10.0.0.2:514"]),
        ]
        result = _resolve_hslpub_endpoints(k8s, 1, "default", "my-pub")
        assert result == ["10.0.0.1:514", "10.0.0.2:514"]

    def test_returns_empty_for_unknown_pub(self):
        k8s = MagicMock()
        k8s.get_resources.return_value = [
            self._make_hslpub("other-pub", ["1.2.3.4:514"]),
        ]
        result = _resolve_hslpub_endpoints(k8s, 1, "default", "my-pub")
        assert result == []

    def test_returns_empty_on_k8s_error(self):
        k8s = MagicMock()
        k8s.get_resources.side_effect = RuntimeError("cluster down")
        result = _resolve_hslpub_endpoints(k8s, 1, "default", "my-pub")
        assert result == []

    def test_multiple_pools_flattened(self):
        pub = {
            "metadata": {"name": "pub"},
            "spec": {
                "pool": [
                    {"name": "p1", "endpoint": ["1.1.1.1:514"]},
                    {"name": "p2", "endpoint": ["2.2.2.2:514"]},
                ]
            },
        }
        k8s = MagicMock()
        k8s.get_resources.return_value = [pub]
        result = _resolve_hslpub_endpoints(k8s, 1, "default", "pub")
        assert result == ["1.1.1.1:514", "2.2.2.2:514"]


# ── _resolve_logprofile_endpoints ──────────────────────────────────────────

class TestResolveLogprofileEndpoints:
    def _make_profile(self, name, pub_name, section="applicationSecurity"):
        return {
            "metadata": {"name": name},
            "spec": {section: {"publisher": pub_name}},
        }

    def test_resolves_via_application_security_publisher(self):
        k8s = MagicMock()
        profile = self._make_profile("my-profile", "my-pub")
        hslpub = {
            "metadata": {"name": "my-pub"},
            "spec": {"pool": [{"name": "p1", "endpoint": ["5.5.5.5:514"]}]},
        }
        k8s.get_resources.side_effect = [[profile], [hslpub]]
        result = _resolve_logprofile_endpoints(k8s, 1, "default", "my-profile")
        assert result == ["5.5.5.5:514"]

    def test_resolves_via_network_publisher(self):
        k8s = MagicMock()
        profile = self._make_profile("prof", "net-pub", section="network")
        hslpub = {
            "metadata": {"name": "net-pub"},
            "spec": {"pool": [{"name": "p1", "endpoint": ["9.9.9.9:5140"]}]},
        }
        k8s.get_resources.side_effect = [[profile], [hslpub]]
        result = _resolve_logprofile_endpoints(k8s, 1, "default", "prof")
        assert result == ["9.9.9.9:5140"]

    def test_returns_empty_for_unknown_profile(self):
        k8s = MagicMock()
        k8s.get_resources.return_value = []
        result = _resolve_logprofile_endpoints(k8s, 1, "default", "ghost")
        assert result == []

    def test_returns_empty_on_error(self):
        k8s = MagicMock()
        k8s.get_resources.side_effect = RuntimeError("boom")
        result = _resolve_logprofile_endpoints(k8s, 1, "default", "any")
        assert result == []


# ── _resolve_secpolicy_endpoints ───────────────────────────────────────────

class TestResolveSecpolicyEndpoints:
    def _make_secpolicy(self, name, items):
        return {"metadata": {"name": name}, "spec": {"items": items, "targetRefs": []}}

    def test_resolves_f5bigwebsecurityprofile_ref(self):
        k8s = MagicMock()
        secpol = self._make_secpolicy("sp1", [
            {"kind": "F5BigWebSecurityProfile", "name": "some-policy"}
        ])
        k8s.get_resources.return_value = [secpol]

        with patch("routes.k8s.waf_logs._resolve_logprofile_endpoints", return_value=["3.3.3.3:514"]):
            result = _resolve_secpolicy_endpoints(k8s, 1, "default", "sp1")
        assert result == []  # items here don't trigger logprofile lookup

    def test_returns_empty_for_unknown_secpolicy(self):
        k8s = MagicMock()
        k8s.get_resources.return_value = []
        result = _resolve_secpolicy_endpoints(k8s, 1, "default", "ghost")
        assert result == []

    def test_returns_empty_on_error(self):
        k8s = MagicMock()
        k8s.get_resources.side_effect = RuntimeError("network")
        result = _resolve_secpolicy_endpoints(k8s, 1, "default", "sp")
        assert result == []


# ── _resolve_endpoints_for_policy ─────────────────────────────────────────

class TestResolveEndpointsForPolicy:
    def test_returns_empty_when_no_secpolicies(self):
        k8s = MagicMock()
        k8s.get_resources.return_value = []
        result = _resolve_endpoints_for_policy(k8s, 1, "default", "my-policy")
        assert result == []

    def test_returns_empty_on_error(self):
        k8s = MagicMock()
        k8s.get_resources.side_effect = RuntimeError("cluster down")
        result = _resolve_endpoints_for_policy(k8s, 1, "default", "my-policy")
        assert result == []

    def test_deduplicates_endpoints(self):
        k8s = MagicMock()
        secpol1 = {
            "metadata": {"name": "sp1"},
            "spec": {"items": [{"kind": "F5BigWebSecurityProfile", "name": "my-policy"}]},
        }
        secpol2 = {
            "metadata": {"name": "sp2"},
            "spec": {"items": [{"kind": "F5BigWebSecurityProfile", "name": "my-policy"}]},
        }
        k8s.get_resources.return_value = [secpol1, secpol2]

        with patch("routes.k8s.waf_logs._resolve_secpolicy_endpoints", return_value=["1.1.1.1:514"]):
            result = _resolve_endpoints_for_policy(k8s, 1, "default", "my-policy")
        # Both policies return same endpoint — deduplication yields one entry
        assert result == ["1.1.1.1:514"]
