"""
Unit tests for ``_resolve_external_url`` in proxy_discovery_service.

Verifies that the LoadBalancer ingress path (hostname or IP) is resolved correctly
for the AWS NLB use-case introduced in Slice 3.  The function pre-existed; these
tests verify its contract so the NLB opt-in feature has a safety net.
"""

from unittest.mock import MagicMock

from services.proxy_discovery_service import _resolve_external_url


def _svc(svc_type: str, node_port: int | None = None) -> MagicMock:
    """Build a minimal mocked K8s Service object."""
    svc = MagicMock()
    svc.spec.type = svc_type
    svc.spec.ports = []
    return svc


def _port(port: int = 8080, node_port: int | None = None) -> MagicMock:
    p = MagicMock()
    p.port = port
    p.node_port = node_port
    return p


def _lb_svc_with_hostname(hostname: str, port: int = 8080) -> tuple:
    """Return (svc, port_obj) with a LoadBalancer ingress hostname."""
    svc = _svc("LoadBalancer")
    ingress = MagicMock()
    ingress.hostname = hostname
    ingress.ip = None
    svc.status.load_balancer.ingress = [ingress]
    return svc, _port(port)


def _lb_svc_with_ip(ip: str, port: int = 8080) -> tuple:
    """Return (svc, port_obj) with a LoadBalancer ingress IP."""
    svc = _svc("LoadBalancer")
    ingress = MagicMock()
    ingress.hostname = None
    ingress.ip = ip
    svc.status.load_balancer.ingress = [ingress]
    return svc, _port(port)


class TestResolveExternalUrlLoadBalancer:
    def test_hostname_ingress_builds_http_url(self):
        """An NLB hostname in ingress[0].hostname must become ``http://<host>:<port>``."""
        nlb_hostname = "a1234567890abc.elb.us-east-1.amazonaws.com"
        svc, port_obj = _lb_svc_with_hostname(nlb_hostname, port=8001)
        core = MagicMock()
        result = _resolve_external_url(core, svc, port_obj)
        assert result == f"http://{nlb_hostname}:8001"

    def test_ip_ingress_builds_http_url(self):
        """When hostname is None, ip should be used instead."""
        svc, port_obj = _lb_svc_with_ip("10.0.1.55", port=8001)
        core = MagicMock()
        result = _resolve_external_url(core, svc, port_obj)
        assert result == "http://10.0.1.55:8001"

    def test_no_ingress_returns_none(self):
        """LoadBalancer with empty ingress list must return None (provisioning in progress)."""
        svc = _svc("LoadBalancer")
        svc.status.load_balancer.ingress = []
        core = MagicMock()
        result = _resolve_external_url(core, svc, _port(8001))
        assert result is None

    def test_no_status_returns_none(self):
        """Missing status must return None gracefully."""
        svc = _svc("LoadBalancer")
        svc.status = None
        core = MagicMock()
        result = _resolve_external_url(core, svc, _port(8001))
        assert result is None

    def test_clusterip_returns_none(self):
        """ClusterIP Services are not reachable externally; must return None."""
        svc = _svc("ClusterIP")
        core = MagicMock()
        result = _resolve_external_url(core, svc, _port(8001))
        assert result is None
