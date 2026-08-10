"""
Unit tests for ProxyDiscoveryService._scan_f5_bnk — bug-5 regression suite.

Bug 5 (2026-06-14): discover-proxies returns 0 records for a BNK-on-EKS
cluster when the target is registered with llm_base_url = "http://<VIP>"
(an IP literal) and no llm_namespace.  The scanner only set found=True on a
backendRef service-name match — structurally impossible for an IP-literal
target — so the BNK Gateway was never selected.

Two fixes:
  1. VIP source: read status.addresses first, fall back to spec.addresses.
  2. VIP-match fallback: if target host is an IP literal that equals the
     gateway VIP, qualify the gateway without needing an HTTPRoute backendRef.

Tests
-----
  test_vip_match_spec_addresses_only
      Regression: GW has spec.addresses VIP only (empty status.addresses) +
      target llm_base_url = "http://<VIP>" and no llm_namespace.
      Expect: found=True, proxy_url = "http://<VIP>:80".

  test_vip_match_status_addresses_preferred
      When both status and spec addresses are set, status wins.

  test_existing_svc_backendref_path_unchanged
      Service-name target still resolves via the backendRef path (no regression).

  test_vip_mismatch_no_false_positive
      BNK GW exists but its VIP != target host and no matching route → found=False.

  test_hostname_target_not_matched_by_vip_path
      Non-IP hostname target does not trigger the VIP-match path.

  test_no_f5_infra_returns_not_found
      No F5 GatewayClass + no BNK pods → early return found=False.

Patching note:
  _scan_f5_bnk does a *local* import at call time:
    ``from services.bnk_pod_discovery import BNK_NAMESPACES, _fetch_pods_in_namespace``
  So we patch those names at their *source* module (services.bnk_pod_discovery),
  not on proxy_discovery_service which has no module-level reference to them.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.proxy_discovery_service import ProxyDiscoveryService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target(llm_base_url: str, llm_namespace: str | None = None) -> MagicMock:
    t = MagicMock()
    t.llm_base_url = llm_base_url
    t.llm_namespace = llm_namespace
    return t


def _make_gateway(
    name: str = "bnk-gw",
    namespace: str = "f5-bnk",
    vip: str = "10.0.10.101",
    *,
    set_status_addresses: bool = True,
    set_spec_addresses: bool = True,
    listeners: list | None = None,
    gateway_class_name: str = "f5-gateway",
) -> dict:
    """Build a minimal fake Gateway dict."""
    if listeners is None:
        listeners = [{"name": "http", "port": 80, "protocol": "HTTP"}]

    status_addr = [{"type": "IPAddress", "value": vip}] if set_status_addresses else []
    spec_addr = [{"type": "IPAddress", "value": vip}] if set_spec_addresses else []

    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "gatewayClassName": gateway_class_name,
            "addresses": spec_addr,
            "listeners": listeners,
        },
        "status": {
            "addresses": status_addr,
        },
    }


def _make_gateway_class(name: str = "f5-gateway", controller: str = "f5.io/gateway-controller") -> dict:
    return {
        "metadata": {"name": name},
        "spec": {"controllerName": controller},
    }


def _make_httproute(
    name: str,
    namespace: str,
    gateway_name: str,
    gateway_ns: str,
    backend_svc: str,
    backend_ns: str,
) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "parentRefs": [{"name": gateway_name, "namespace": gateway_ns}],
            "rules": [{"backendRefs": [{"name": backend_svc, "namespace": backend_ns}]}],
        },
    }


def _make_svc(db: MagicMock) -> ProxyDiscoveryService:
    """Create a ProxyDiscoveryService with a mocked __init__."""
    with patch.object(ProxyDiscoveryService, "__init__", return_value=None):
        svc = ProxyDiscoveryService.__new__(ProxyDiscoveryService)
        svc.db = db
        svc.k8s = MagicMock()
    return svc


def _bnk_pod_patches(pod_list: list | None = None):
    """Context manager stack: patch BNK_NAMESPACES + _fetch_pods_in_namespace at source."""
    pods = pod_list if pod_list is not None else [MagicMock()]
    return (
        patch("services.bnk_pod_discovery.BNK_NAMESPACES", ["f5-bnk"]),
        patch("services.bnk_pod_discovery._fetch_pods_in_namespace", return_value=pods),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestScanF5BnkVipMatch:
    """Bug-5 regression: IP-literal target matched by Gateway VIP."""

    def test_vip_match_spec_addresses_only(self):
        """
        BNK Gateway with VIP only in spec.addresses (status.addresses empty) +
        target llm_base_url = "http://10.0.10.101" and no llm_namespace.

        Expect: found=True, proxy_url = "http://10.0.10.101:80".
        """
        vip = "10.0.10.101"
        db = MagicMock()
        api_client = MagicMock()
        target = _make_target(f"http://{vip}", llm_namespace=None)

        gc = _make_gateway_class()
        gw = _make_gateway(
            vip=vip,
            set_status_addresses=False,  # <-- empty status.addresses (the bug condition)
            set_spec_addresses=True,     # VIP only in spec.addresses
        )

        svc = _make_svc(db)

        ns_patch, pods_patch = _bnk_pod_patches()
        with patch("services.proxy_discovery_service._safe_list_cluster_custom") as mock_gc, \
             patch("services.proxy_discovery_service._safe_list_all_custom") as mock_all, \
             ns_patch, pods_patch:

            def _list_cluster(custom, *, group, version, plural):
                if plural == "gatewayclasses":
                    return [gc]
                return []

            def _list_all(custom, *, group, version, plural):
                if plural == "gateways":
                    return [gw]
                if plural == "httproutes":
                    return []  # No HTTPRoutes — this is the bug-5 scenario
                return []

            mock_gc.side_effect = _list_cluster
            mock_all.side_effect = _list_all

            result = svc._scan_f5_bnk(api_client, target)

        assert result.found is True, (
            f"Expected found=True for IP-literal target matching gateway VIP; "
            f"got found=False. details={result.details}"
        )
        assert result.proxy_url == f"http://{vip}:80", (
            f"Expected proxy_url=http://{vip}:80, got {result.proxy_url}"
        )

    def test_vip_match_status_addresses_preferred(self):
        """
        When BOTH status.addresses and spec.addresses are set, status is used
        (they should agree in practice; this confirms priority).
        """
        vip = "10.0.10.201"
        alt_vip = "10.0.10.202"  # spec.addresses has a different (hypothetical) value
        db = MagicMock()
        api_client = MagicMock()
        target = _make_target(f"http://{vip}", llm_namespace=None)

        gc = _make_gateway_class()
        gw = _make_gateway(vip=vip, set_status_addresses=True, set_spec_addresses=True)
        # Override to make status and spec differ, so we can verify which wins
        gw["spec"]["addresses"] = [{"type": "IPAddress", "value": alt_vip}]
        gw["status"]["addresses"] = [{"type": "IPAddress", "value": vip}]

        svc = _make_svc(db)

        ns_patch, pods_patch = _bnk_pod_patches()
        with patch("services.proxy_discovery_service._safe_list_cluster_custom") as mock_gc, \
             patch("services.proxy_discovery_service._safe_list_all_custom") as mock_all, \
             ns_patch, pods_patch:

            mock_gc.side_effect = lambda c, *, group, version, plural: [gc] if plural == "gatewayclasses" else []
            mock_all.side_effect = lambda c, *, group, version, plural: (
                [gw] if plural == "gateways" else []
            )

            result = svc._scan_f5_bnk(api_client, target)

        # status.addresses VIP == target VIP → found
        assert result.found is True, (
            "Expected found=True when status.addresses VIP matches the target; "
            f"got found=False. details={result.details}"
        )
        assert vip in result.proxy_url


class TestScanF5BnkBackendRefPath:
    """Existing service-name backendRef path must be unchanged (regression guard)."""

    def test_existing_svc_backendref_path_unchanged(self):
        """
        Service-name target (llm_base_url = "http://vllm-svc.default:8000")
        with a matching HTTPRoute backendRef → found=True via backendRef path.
        """
        vip = "10.0.10.101"
        db = MagicMock()
        api_client = MagicMock()
        target = _make_target("http://vllm-svc.default:8000", llm_namespace="default")

        gc = _make_gateway_class()
        gw = _make_gateway(vip=vip, set_status_addresses=True, set_spec_addresses=False)
        route = _make_httproute(
            name="vllm-route",
            namespace="default",
            gateway_name="bnk-gw",
            gateway_ns="f5-bnk",
            backend_svc="vllm-svc",
            backend_ns="default",
        )

        svc = _make_svc(db)

        ns_patch, pods_patch = _bnk_pod_patches()
        with patch("services.proxy_discovery_service._safe_list_cluster_custom") as mock_gc, \
             patch("services.proxy_discovery_service._safe_list_all_custom") as mock_all, \
             ns_patch, pods_patch:

            mock_gc.side_effect = lambda c, *, group, version, plural: [gc] if plural == "gatewayclasses" else []
            mock_all.side_effect = lambda c, *, group, version, plural: (
                [gw] if plural == "gateways" else [route] if plural == "httproutes" else []
            )

            result = svc._scan_f5_bnk(api_client, target)

        assert result.found is True
        assert vip in result.proxy_url


class TestScanF5BnkNoFalsePositive:
    """VIP-match must NOT fire when gateway VIP != target host."""

    def test_vip_mismatch_no_false_positive(self):
        """
        BNK Gateway VIP = 10.0.10.101.
        Target llm_base_url = "http://10.0.10.199" (different IP, no routes).
        Expect: found=False.
        """
        gateway_vip = "10.0.10.101"
        target_ip = "10.0.10.199"  # different from gateway VIP
        db = MagicMock()
        api_client = MagicMock()
        target = _make_target(f"http://{target_ip}", llm_namespace=None)

        gc = _make_gateway_class()
        gw = _make_gateway(vip=gateway_vip, set_status_addresses=False, set_spec_addresses=True)

        svc = _make_svc(db)

        ns_patch, pods_patch = _bnk_pod_patches()
        with patch("services.proxy_discovery_service._safe_list_cluster_custom") as mock_gc, \
             patch("services.proxy_discovery_service._safe_list_all_custom") as mock_all, \
             ns_patch, pods_patch:

            mock_gc.side_effect = lambda c, *, group, version, plural: [gc] if plural == "gatewayclasses" else []
            mock_all.side_effect = lambda c, *, group, version, plural: (
                [gw] if plural == "gateways" else []
            )

            result = svc._scan_f5_bnk(api_client, target)

        assert result.found is False, (
            f"VIP mismatch must not produce a false positive; got found=True. "
            f"Gateway VIP={gateway_vip}, target={target_ip}"
        )

    def test_hostname_target_not_matched_by_vip_path(self):
        """
        Service-name target (not an IP) does not trigger the VIP-match path
        even if the gateway has a VIP with no matching routes.
        Verifies: the ipaddress.ip_address() gate prevents hostname false-positives.
        """
        vip = "10.0.10.101"
        db = MagicMock()
        api_client = MagicMock()
        # hostname that is NOT an IP literal — no route for it either
        target = _make_target("http://my-llm-service.prod:8000", llm_namespace="prod")

        gc = _make_gateway_class()
        gw = _make_gateway(vip=vip, set_status_addresses=False, set_spec_addresses=True)

        svc = _make_svc(db)

        ns_patch, pods_patch = _bnk_pod_patches()
        with patch("services.proxy_discovery_service._safe_list_cluster_custom") as mock_gc, \
             patch("services.proxy_discovery_service._safe_list_all_custom") as mock_all, \
             ns_patch, pods_patch:

            mock_gc.side_effect = lambda c, *, group, version, plural: [gc] if plural == "gatewayclasses" else []
            mock_all.side_effect = lambda c, *, group, version, plural: (
                [gw] if plural == "gateways" else []
            )

            result = svc._scan_f5_bnk(api_client, target)

        assert result.found is False, (
            "Hostname target must not be matched by the VIP-match path"
        )


class TestScanF5BnkNoInfra:
    """No F5 infra → early return, not found."""

    def test_no_f5_infra_returns_not_found(self):
        """No F5 GatewayClass + no BNK pods → ProxyDiscoveryResult(found=False)."""
        db = MagicMock()
        api_client = MagicMock()
        target = _make_target("http://10.0.10.101", llm_namespace=None)

        svc = _make_svc(db)

        ns_patch, pods_patch = _bnk_pod_patches(pod_list=[])
        with patch("services.proxy_discovery_service._safe_list_cluster_custom") as mock_gc, \
             patch("services.proxy_discovery_service._safe_list_all_custom") as mock_all, \
             ns_patch, pods_patch:

            mock_gc.return_value = []
            mock_all.return_value = []

            result = svc._scan_f5_bnk(api_client, target)

        assert result.found is False
        assert result.proxy_type == "f5-bnk"
