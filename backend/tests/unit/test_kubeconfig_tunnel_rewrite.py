"""Tests for rewrite_kubeconfig_for_tunnel (#7).

Both SSH-tunnel consumers -- the OpenTofu kubernetes/helm providers via
config_writer, and the in-process clients via cluster_utils -- used to point the
kubeconfig at 127.0.0.1:<port> and set insecure-skip-tls-verify, stripping the
CA. That sidesteps the "certificate is valid for <real host>, not 127.0.0.1"
x509 error by disabling verification outright, so a tunnelled plan/apply had no
protection against a MITM on the tunnel path.

The shared helper keeps the CA, keeps verification ON, and sets tls-server-name
so the client verifies against the ORIGINAL hostname while dialling the tunnel.
It may only ever RESTORE verification, never invent it: with no CA to verify
against it falls back to the old behaviour so a working cluster keeps working.
"""

from __future__ import annotations

import pytest
import yaml

from services.kubeconfig_normalizer import rewrite_kubeconfig_for_tunnel

CA = "LS0tLS1CRUdJTi..."  # any non-empty CA data


def _kc(server: str, *, ca: str | None = CA, insecure: bool = False, name: str = "c1") -> str:
    cluster: dict = {"server": server}
    if ca:
        cluster["certificate-authority-data"] = ca
    if insecure:
        cluster["insecure-skip-tls-verify"] = True
    return yaml.dump({
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": name, "cluster": cluster}],
        "contexts": [{"name": name, "context": {"cluster": name, "user": "u"}}],
        "current-context": name,
        "users": [{"name": "u", "user": {"token": "t"}}],
    })


def _cluster(doc_yaml: str) -> dict:
    return yaml.safe_load(doc_yaml)["clusters"][0]["cluster"]


@pytest.mark.unit
class TestVerificationRestored:
    def test_eks_hostname_becomes_tls_server_name(self):
        out = _cluster(rewrite_kubeconfig_for_tunnel(
            _kc("https://ABC123.gr7.us-east-1.eks.amazonaws.com"), 41234
        ))
        assert out["server"] == "https://127.0.0.1:41234"
        # urlparse().hostname lowercases -- correct: DNS names and TLS SNI /
        # hostname verification are case-insensitive, so this cannot mismatch.
        assert out["tls-server-name"] == "abc123.gr7.us-east-1.eks.amazonaws.com"
        # The two things that make verification real:
        assert out["certificate-authority-data"] == CA, "CA was stripped"
        assert "insecure-skip-tls-verify" not in out, "verification was disabled"

    def test_ip_server_uses_ip_as_server_name(self):
        """On-prem: server is an IP; the cert has that IP in its SANs."""
        out = _cluster(rewrite_kubeconfig_for_tunnel(_kc("https://10.145.33.194:6443"), 5000))
        assert out["tls-server-name"] == "10.145.33.194"
        assert "insecure-skip-tls-verify" not in out

    def test_port_in_original_server_is_dropped_from_server_name(self):
        out = _cluster(rewrite_kubeconfig_for_tunnel(_kc("https://api.example.com:6443"), 5000))
        assert out["tls-server-name"] == "api.example.com"

    def test_certificate_authority_file_ref_also_counts_as_a_ca(self):
        kc = yaml.dump({"clusters": [{"name": "c", "cluster": {
            "server": "https://api.example.com", "certificate-authority": "/etc/ca.crt"}}]})
        out = _cluster(rewrite_kubeconfig_for_tunnel(kc, 5000))
        assert out["tls-server-name"] == "api.example.com"
        assert out["certificate-authority"] == "/etc/ca.crt"

    def test_all_clusters_rewritten(self):
        kc = yaml.dump({"clusters": [
            {"name": "a", "cluster": {"server": "https://a.example.com", "certificate-authority-data": CA}},
            {"name": "b", "cluster": {"server": "https://b.example.com", "certificate-authority-data": CA}},
        ]})
        doc = yaml.safe_load(rewrite_kubeconfig_for_tunnel(kc, 7))
        names = {c["cluster"]["tls-server-name"] for c in doc["clusters"]}
        assert names == {"a.example.com", "b.example.com"}
        assert all(c["cluster"]["server"] == "https://127.0.0.1:7" for c in doc["clusters"])


@pytest.mark.unit
class TestFailSafeFallback:
    """Verification may be restored, never invented."""

    def test_no_ca_falls_back_to_skip(self):
        out = _cluster(rewrite_kubeconfig_for_tunnel(_kc("https://api.example.com", ca=None), 5000))
        assert out["server"] == "https://127.0.0.1:5000"
        assert out["insecure-skip-tls-verify"] is True
        assert "tls-server-name" not in out

    def test_originally_insecure_stays_insecure(self):
        """A cluster the operator already marked insecure must not suddenly start
        failing on cert verification because we 'helpfully' turned it on."""
        out = _cluster(rewrite_kubeconfig_for_tunnel(
            _kc("https://api.example.com", insecure=True), 5000
        ))
        assert out["insecure-skip-tls-verify"] is True
        assert "certificate-authority-data" not in out
        assert "tls-server-name" not in out

    def test_unparseable_server_falls_back_to_skip(self):
        out = _cluster(rewrite_kubeconfig_for_tunnel(_kc("not a url"), 5000))
        assert out["insecure-skip-tls-verify"] is True
        assert "tls-server-name" not in out

    def test_missing_server_falls_back_to_skip(self):
        kc = yaml.dump({"clusters": [{"name": "c", "cluster": {"certificate-authority-data": CA}}]})
        out = _cluster(rewrite_kubeconfig_for_tunnel(kc, 5000))
        assert out["insecure-skip-tls-verify"] is True

    def test_stale_tls_server_name_is_cleared_on_fallback(self):
        """If a previous rewrite set tls-server-name and this one must fall back,
        the stale name is removed rather than left pointing at the wrong host."""
        kc = yaml.dump({"clusters": [{"name": "c", "cluster": {
            "server": "https://api.example.com", "tls-server-name": "old.example.com"}}]})
        out = _cluster(rewrite_kubeconfig_for_tunnel(kc, 5000))
        assert "tls-server-name" not in out
        assert out["insecure-skip-tls-verify"] is True


@pytest.mark.unit
class TestShape:
    def test_uses_127_0_0_1_not_localhost(self):
        """localhost resolves to ::1 first; the tunnel listener is IPv4-only."""
        out = _cluster(rewrite_kubeconfig_for_tunnel(_kc("https://api.example.com"), 9))
        assert out["server"].startswith("https://127.0.0.1:")

    def test_rest_of_kubeconfig_untouched(self):
        doc = yaml.safe_load(rewrite_kubeconfig_for_tunnel(_kc("https://api.example.com"), 9))
        assert doc["users"] == [{"name": "u", "user": {"token": "t"}}]
        assert doc["current-context"] == "c1"

    def test_empty_document_is_tolerated(self):
        assert yaml.safe_load(rewrite_kubeconfig_for_tunnel("", 9)) == {}
