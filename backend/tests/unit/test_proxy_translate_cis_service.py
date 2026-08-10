"""
Unit tests for proxy_translate_cis_service (D-023 P3 + P4a/P4b/P4c).

Verifies:
  1. VirtualServer → Gateway + HTTPRoute (host / pool / TLS → backendRefs)
  2. TransportServer → TCPRoute / UDPRoute
  3. EVERY lossy construct → asserted in unmapped[]
  4. Policy → unmapped[], NOT silently mapped (escalation gate)
  5. servicePort-by-name → port_by_name unmapped + placeholder port
  6. cross-namespace backend → cross_namespace_backend unmapped
  7. tls_profile_ref (BIG-IP native TLS) → tls_profile_ref unmapped
  8. All iRules, GTM, SNAT, IPAM/VIP, monitor → flagged
  (P4c) OpenShift Route → HTTPRoute / unmapped (edge/passthrough/reencrypt/wildcardPolicy)
"""

import pytest
import yaml

from services.proxy_translate_cis_service import (
    _build_tcpudp_route,
    _collect_vs_unmapped,
    _transportserver_to_route,
    _virtualserver_to_httproute,
    translate_cis_to_bnk,
)
from services.proxy_translate_service import TranslationResult, UnmappedEntry

# ---------------------------------------------------------------------------
# Minimal fixtures
# ---------------------------------------------------------------------------

def _vs(name="vs1", namespace="default", **spec_kwargs) -> dict:
    """Build a minimal CIS VirtualServer dict."""
    spec = {
        "host": "app.example.com",
        "pools": [
            {"path": "/", "service": "backend-svc", "servicePort": 8080}
        ],
    }
    spec.update(spec_kwargs)
    return {
        "apiVersion": "cis.f5.com/v1",
        "kind": "VirtualServer",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


def _ts(name="ts1", namespace="default", ts_type="tcp", **spec_kwargs) -> dict:
    """Build a minimal CIS TransportServer dict."""
    spec = {
        "type": ts_type,
        "pool": {"service": "tcp-svc", "servicePort": 9090},
        "virtualServerPort": 9090,
    }
    spec.update(spec_kwargs)
    return {
        "apiVersion": "cis.f5.com/v1",
        "kind": "TransportServer",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


# ===========================================================================
# 1. VirtualServer → Gateway + HTTPRoute
# ===========================================================================

@pytest.mark.unit
def test_translate_cis_vs_produces_gateway_and_httproute():
    """VirtualServer → translate_cis_to_bnk returns all three YAML docs."""
    result = translate_cis_to_bnk(virtualservers=[_vs()], transportservers=[])

    assert isinstance(result, TranslationResult)
    # All three YAML docs present
    assert result.gatewayclass_yaml
    assert result.gateway_yaml
    assert result.httproute_yaml

    # GatewayClass has correct controller
    gc = yaml.safe_load(result.gatewayclass_yaml)
    assert gc["kind"] == "GatewayClass"
    assert gc["spec"]["controllerName"] == "cne.f5.io/gateway-controller"

    # Gateway has correct gatewayClassName
    gw = yaml.safe_load(result.gateway_yaml)
    assert gw["kind"] == "Gateway"
    assert gw["spec"]["gatewayClassName"] == "f5-bnk"

    # HTTPRoute has backendRef pointing to backend-svc:8080
    route = yaml.safe_load(result.httproute_yaml)
    assert route["kind"] == "HTTPRoute"
    rules = route["spec"]["rules"]
    assert len(rules) == 1
    backend_ref = rules[0]["backendRefs"][0]
    assert backend_ref["name"] == "backend-svc"
    assert backend_ref["port"] == 8080


@pytest.mark.unit
def test_translate_cis_vs_host_in_httproute_hostnames():
    """spec.host ends up in HTTPRoute.spec.hostnames."""
    result = translate_cis_to_bnk(virtualservers=[_vs(host="shop.example.com")], transportservers=[])
    route = yaml.safe_load(result.httproute_yaml)
    assert "shop.example.com" in route["spec"].get("hostnames", [])


@pytest.mark.unit
def test_translate_cis_vs_multiple_pools():
    """Multiple pools → multiple HTTPRoute rules."""
    vs = _vs(pools=[
        {"path": "/api", "service": "api-svc", "servicePort": 8000},
        {"path": "/web", "service": "web-svc", "servicePort": 80},
    ])
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    route = yaml.safe_load(result.httproute_yaml)
    assert len(route["spec"]["rules"]) == 2


@pytest.mark.unit
def test_translate_cis_combined_yaml_contains_all_docs():
    """combined_yaml joins all three manifests with --- separator."""
    result = translate_cis_to_bnk(virtualservers=[_vs()], transportservers=[])
    docs = list(yaml.safe_load_all(result.combined_yaml))
    kinds = {d["kind"] for d in docs}
    assert "GatewayClass" in kinds
    assert "Gateway" in kinds
    assert "HTTPRoute" in kinds


@pytest.mark.unit
def test_translate_cis_source_metadata():
    """result.source contains correct counts."""
    result = translate_cis_to_bnk(virtualservers=[_vs(), _vs(name="vs2")], transportservers=[_ts()])
    assert result.source["proxy_type"] == "cis-bigip"
    assert result.source["virtualserver_count"] == 2
    assert result.source["transportserver_count"] == 1


# ===========================================================================
# 2. TransportServer → TCPRoute / UDPRoute
# ===========================================================================

@pytest.mark.unit
def test_translate_cis_tcp_transport_server():
    """TransportServer type=tcp → TCPRoute doc in combined_yaml."""
    result = translate_cis_to_bnk(virtualservers=[], transportservers=[_ts(ts_type="tcp")])
    docs = list(yaml.safe_load_all(result.combined_yaml))
    kinds = [d["kind"] for d in docs]
    assert "TCPRoute" in kinds

    tcp_route = next(d for d in docs if d["kind"] == "TCPRoute")
    assert tcp_route["apiVersion"] == "gateway.networking.k8s.io/v1alpha2"
    backend = tcp_route["spec"]["rules"][0]["backendRefs"][0]
    assert backend["name"] == "tcp-svc"
    assert backend["port"] == 9090


@pytest.mark.unit
def test_translate_cis_udp_transport_server():
    """TransportServer type=udp → UDPRoute doc in combined_yaml."""
    result = translate_cis_to_bnk(virtualservers=[], transportservers=[_ts(ts_type="udp")])
    docs = list(yaml.safe_load_all(result.combined_yaml))
    kinds = [d["kind"] for d in docs]
    assert "UDPRoute" in kinds


@pytest.mark.unit
def test_translate_cis_transport_server_unknown_type_unmapped():
    """TransportServer with unknown type → routed to unmapped[], not emitted."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[_ts(ts_type="sctp")],
    )
    unmapped_constructs = {u.construct for u in result.unmapped}
    assert "transport_type" in unmapped_constructs

    # Should NOT produce a SCTP route
    docs = list(yaml.safe_load_all(result.combined_yaml))
    kinds = {d["kind"] for d in docs}
    assert "SCTPRoute" not in kinds


# ===========================================================================
# 3. Lossy constructs → unmapped[] (D-017, never silently dropped)
# ===========================================================================

@pytest.mark.unit
def test_irules_flagged_as_unmapped():
    """iRules → unmapped with construct='irule'."""
    vs = _vs(iRules=["/Common/my-irule"])
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "irule" in constructs


@pytest.mark.unit
def test_snat_flagged_as_unmapped():
    """SNAT → unmapped with construct='snat'."""
    vs = _vs(snat="automap")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "snat" in constructs


@pytest.mark.unit
def test_vip_address_flagged_as_unmapped():
    """virtualServerAddress → unmapped with construct='vip_address'."""
    vs = _vs(virtualServerAddress="10.1.2.3")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "vip_address" in constructs


@pytest.mark.unit
def test_ipam_label_flagged_as_unmapped():
    """ipamLabel → unmapped with construct='vip_address'."""
    vs = _vs(ipamLabel="prod-vip")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "vip_address" in constructs


@pytest.mark.unit
def test_gtm_profile_flagged_as_unmapped():
    """gTMProfile → unmapped with construct='gtm_externaldns'."""
    vs = _vs(gTMProfile="/Common/gslb-pool")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "gtm_externaldns" in constructs


@pytest.mark.unit
def test_partition_flagged_as_unmapped():
    """spec.partition → unmapped with construct='route_domain_partition'."""
    vs = _vs(partition="prod")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "route_domain_partition" in constructs


@pytest.mark.unit
def test_bigip_tls_profile_ref_flagged_as_unmapped():
    """BIG-IP-native TLS profile by name → tls_profile_ref unmapped."""
    vs = _vs(tls={"clientSSLProfiles": "/Common/clientssl"})
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "tls_profile_ref" in constructs


@pytest.mark.unit
def test_k8s_secret_tls_produces_https_listener():
    """TLS with k8s Secret (clientSSL) → HTTPS listener, NOT unmapped."""
    vs = _vs(tls={"clientSSL": "my-tls-secret"})
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])

    # Should NOT produce tls_profile_ref unmapped
    constructs = {u.construct for u in result.unmapped}
    assert "tls_profile_ref" not in constructs

    # HTTPS listener should be in gateway_yaml
    gw = yaml.safe_load(result.gateway_yaml)
    listener_protocols = [lst["protocol"] for lst in gw["spec"]["listeners"]]
    assert "HTTPS" in listener_protocols


@pytest.mark.unit
def test_pool_monitor_flagged_as_unmapped():
    """Pool health monitor → unmapped with construct='monitor'."""
    vs = _vs(pools=[
        {"path": "/", "service": "svc", "servicePort": 80, "monitor": "/Common/http"}
    ])
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "monitor" in constructs


@pytest.mark.unit
def test_port_by_name_flagged_as_unmapped():
    """servicePort-by-name → port_by_name unmapped + placeholder port 80."""
    vs = _vs(pools=[
        {"path": "/", "service": "svc", "servicePort": "http"}
    ])
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "port_by_name" in constructs

    # Placeholder port
    route = yaml.safe_load(result.httproute_yaml)
    backend_port = route["spec"]["rules"][0]["backendRefs"][0]["port"]
    assert backend_port == 80


@pytest.mark.unit
def test_cross_namespace_backend_flagged_as_unmapped():
    """Pool in a different namespace → cross_namespace_backend unmapped."""
    vs = _vs(
        namespace="ns-a",
        pools=[
            {"path": "/", "service": "svc", "servicePort": 80, "namespace": "ns-b"}
        ],
    )
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "cross_namespace_backend" in constructs


# ===========================================================================
# 4. Policy → unmapped[], NOT silently mapped (ESCALATION — D-023 critical gate)
# ===========================================================================

@pytest.mark.unit
def test_policy_name_routes_to_unmapped_not_silently_mapped():
    """policyName → unmapped with construct='security_policy'. Must NOT be mapped."""
    vs = _vs(policyName="/Common/asm-policy")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])

    # MUST appear in unmapped
    security_unmapped = [u for u in result.unmapped if u.construct == "security_policy"]
    assert len(security_unmapped) >= 1, "policyName MUST appear in unmapped[] (escalation gate)"

    # MUST NOT silently appear as any kind of BNK resource in the YAML
    combined = result.combined_yaml
    assert "WAFPolicy" not in combined
    assert "SecurityPolicy" not in combined
    assert "asm-policy" not in combined


@pytest.mark.unit
def test_waf_routes_to_unmapped():
    """waf field → unmapped with construct='security_policy'."""
    vs = _vs(waf="/Common/waf-policy")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "security_policy" in constructs


@pytest.mark.unit
def test_bot_defense_routes_to_unmapped():
    """botDefense → unmapped with construct='security_policy'."""
    vs = _vs(botDefense="/Common/bot-defense")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "security_policy" in constructs


@pytest.mark.unit
def test_firewall_policy_routes_to_unmapped():
    """firewallPolicy → unmapped with construct='security_policy'."""
    vs = _vs(firewallPolicy="/Common/afm-policy")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "security_policy" in constructs


@pytest.mark.unit
def test_transport_server_policy_routes_to_unmapped():
    """TransportServer policyName → unmapped (same escalation, different CR)."""
    ts = _ts(policyName="/Common/afm-policy")
    result = translate_cis_to_bnk(virtualservers=[], transportservers=[ts])
    constructs = {u.construct for u in result.unmapped}
    assert "security_policy" in constructs


# ===========================================================================
# 5. Empty input → placeholder rule + unmapped
# ===========================================================================

@pytest.mark.unit
def test_empty_input_produces_placeholder():
    """No VS/TS → placeholder backendRef + no_rules unmapped."""
    result = translate_cis_to_bnk(virtualservers=[], transportservers=[])
    route = yaml.safe_load(result.httproute_yaml)
    rules = route["spec"]["rules"]
    assert any(
        br.get("name") == "REPLACE_WITH_SERVICE_NAME"
        for rule in rules
        for br in rule.get("backendRefs", [])
    )
    constructs = {u.construct for u in result.unmapped}
    assert "no_rules" in constructs


# ===========================================================================
# 6. Gateway name derivation
# ===========================================================================

@pytest.mark.unit
def test_gateway_name_override_respected():
    """Explicit gateway_name is used verbatim."""
    result = translate_cis_to_bnk(
        virtualservers=[_vs()],
        transportservers=[],
        gateway_name="my-gateway",
    )
    gw = yaml.safe_load(result.gateway_yaml)
    assert gw["metadata"]["name"] == "my-gateway"


@pytest.mark.unit
def test_gateway_name_auto_derived_from_vs_name():
    """Auto-derived gateway name slugifies the VS name."""
    result = translate_cis_to_bnk(
        virtualservers=[_vs(name="my-vs")],
        transportservers=[],
    )
    gw = yaml.safe_load(result.gateway_yaml)
    assert "my-vs" in gw["metadata"]["name"]


# ===========================================================================
# 7. Unmapped reason text for security_policy contains escalation note
# ===========================================================================

@pytest.mark.unit
def test_policy_unmapped_reason_mentions_escalation():
    """UnmappedEntry for security_policy carries the D-023 escalation text."""
    vs = _vs(policyName="/Common/asm-policy")
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    sec_entries = [u for u in result.unmapped if u.construct == "security_policy"]
    assert sec_entries
    reason = sec_entries[0].reason.lower()
    assert "verify" in reason
    assert "bnk" in reason


# ===========================================================================
# 8. TCPRoute / UDPRoute port-by-name unmapped
# ===========================================================================

@pytest.mark.unit
def test_transport_server_port_by_name_unmapped():
    """TransportServer servicePort by name → port_by_name unmapped."""
    ts = _ts(pool={"service": "my-svc", "servicePort": "http"})
    result = translate_cis_to_bnk(virtualservers=[], transportservers=[ts])
    constructs = {u.construct for u in result.unmapped}
    assert "port_by_name" in constructs


# ===========================================================================
# 9. TCPRoute escalation annotation emitted
# ===========================================================================

@pytest.mark.unit
def test_tcp_route_escalation_annotation_present():
    """TCPRoute carries bnk-forge/escalation annotation about v1alpha2."""
    result = translate_cis_to_bnk(virtualservers=[], transportservers=[_ts(ts_type="tcp")])
    docs = list(yaml.safe_load_all(result.combined_yaml))
    tcp_route = next((d for d in docs if d["kind"] == "TCPRoute"), None)
    assert tcp_route is not None
    annotations = tcp_route["metadata"].get("annotations", {})
    assert "bnk-forge/escalation" in annotations
    assert "v1alpha2" in annotations["bnk-forge/escalation"]


# ===========================================================================
# 10. serverSSL / serverSSLProfiles (D-017 fix — mwiget audit)
# ===========================================================================

@pytest.mark.unit
def test_server_ssl_alone_flagged_as_unmapped():
    """serverSSL without clientSSL → server_ssl_profile unmapped."""
    vs = _vs(tls={"serverSSL": "/Common/serverssl"})
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "server_ssl_profile" in constructs


@pytest.mark.unit
def test_client_ssl_and_server_ssl_both_handled():
    """VS with clientSSL (k8s Secret) AND serverSSL → HTTPS listener created AND serverSSL in unmapped."""
    vs = _vs(tls={"clientSSL": "my-tls-secret", "serverSSL": "/Common/serverssl"})
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])

    # clientSSL → HTTPS listener (mappable)
    gw = yaml.safe_load(result.gateway_yaml)
    listener_protocols = [lst["protocol"] for lst in gw["spec"]["listeners"]]
    assert "HTTPS" in listener_protocols, "clientSSL must still produce an HTTPS listener"

    # serverSSL → unmapped (D-017 — not silently dropped)
    constructs = {u.construct for u in result.unmapped}
    assert "server_ssl_profile" in constructs, (
        "serverSSL must appear in unmapped[] even when clientSSL is also present (D-017)"
    )
    # clientSSL handling must NOT generate a tls_profile_ref unmapped entry
    assert "tls_profile_ref" not in constructs


@pytest.mark.unit
def test_server_ssl_profiles_flagged_as_unmapped():
    """serverSSLProfiles (list form) → server_ssl_profile unmapped."""
    vs = _vs(tls={"serverSSLProfiles": ["/Common/serverssl"]})
    result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
    constructs = {u.construct for u in result.unmapped}
    assert "server_ssl_profile" in constructs


# ===========================================================================
# 11. AS3 declaration → BNK translator (P4a/P4d)
# ===========================================================================

from services.proxy_translate_cis_service import _as3_declaration_to_bnk  # noqa: E402


def _as3_decl(
    tenant: str = "tenant1",
    app: str = "app1",
    vs_name: str = "vs1",
    pool_name: str = "pool1",
    vs_class: str = "Service_HTTP",
    **vs_kwargs,
) -> dict:
    """Build a minimal AS3 declaration dict for testing."""
    pool_members = vs_kwargs.pop("pool_members", [{"serviceAddress": "my-svc", "servicePort": 8080}])
    pool = {
        "class": "Pool",
        "members": pool_members,
    }
    vs = {"class": vs_class, "pool": pool_name, **vs_kwargs}
    return {
        "class": "AS3",
        "declaration": {
            "class": "ADC",
            tenant: {
                "class": "Tenant",
                app: {
                    "class": "Application",
                    vs_name: vs,
                    pool_name: pool,
                },
            },
        },
    }


@pytest.mark.unit
def test_as3_single_tenant_http_produces_httproute_rule():
    """AS3 Service_HTTP with a K8s service member → HTTPRoute rule."""
    decl = _as3_decl()
    unmapped: list = []
    rules, hostnames, tls_listeners = _as3_declaration_to_bnk(
        decl,
        source_kind="AS3ConfigMap",
        source_name="kube-system/as3-cm",
        source_namespace="kube-system",
        unmapped=unmapped,
    )
    assert len(rules) == 1
    backend = rules[0]["backendRefs"][0]
    assert backend["name"] == "my-svc"
    assert backend["port"] == 8080
    assert not unmapped  # no lossy constructs


@pytest.mark.unit
def test_as3_https_client_tls_string_ref_is_unmapped():
    """Service_HTTPS with clientTLS string ref → tls_profile_ref in unmapped[]."""
    decl = _as3_decl(vs_class="Service_HTTPS", clientTLS="/Common/clientssl")
    unmapped: list = []
    _as3_declaration_to_bnk(
        decl,
        source_kind="AS3ConfigMap",
        source_name="default/my-cm",
        source_namespace="default",
        unmapped=unmapped,
    )
    constructs = {u.construct for u in unmapped}
    assert "tls_profile_ref" in constructs


@pytest.mark.unit
def test_as3_raw_ip_pool_member_is_unmapped_no_backend_ref():
    """Raw-IP pool members (serverAddresses) → raw_ip_pool_member unmapped, NO backendRef."""
    pool_members = [{"serverAddresses": ["10.0.0.5"], "servicePort": 443}]
    decl = _as3_decl(pool_members=pool_members)
    unmapped: list = []
    rules, _, _ = _as3_declaration_to_bnk(
        decl,
        source_kind="AS3Device",
        source_name="bigip-01",
        source_namespace="(device)",
        unmapped=unmapped,
    )
    constructs = {u.construct for u in unmapped}
    assert "raw_ip_pool_member" in constructs
    # Must NOT produce a backendRef pointing at the IP
    assert not rules or not any("10.0.0.5" in str(r) for r in rules)


@pytest.mark.unit
def test_as3_common_pool_ref_is_unmapped():
    """/Common pool reference → cross_tenant_pool_ref unmapped, no backendRef."""
    decl = {
        "class": "AS3",
        "declaration": {
            "class": "ADC",
            "tenant1": {
                "class": "Tenant",
                "app1": {
                    "class": "Application",
                    "vs1": {"class": "Service_HTTP", "pool": "/Common/shared-pool"},
                },
            },
        },
    }
    unmapped: list = []
    rules, _, _ = _as3_declaration_to_bnk(
        decl,
        source_kind="AS3ConfigMap",
        source_name="ns/cm",
        source_namespace="ns",
        unmapped=unmapped,
    )
    constructs = {u.construct for u in unmapped}
    assert "cross_tenant_pool_ref" in constructs
    assert not rules  # no backendRef generated


@pytest.mark.unit
def test_as3_tcp_service_produces_unmapped_note():
    """Service_TCP → tcp_service unmapped note (TCPRoute review required)."""
    pool_members = [{"serviceAddress": "tcp-svc", "servicePort": 9000}]
    decl = _as3_decl(vs_class="Service_TCP", pool_members=pool_members)
    unmapped: list = []
    _as3_declaration_to_bnk(
        decl,
        source_kind="AS3ConfigMap",
        source_name="ns/cm",
        source_namespace="ns",
        unmapped=unmapped,
    )
    constructs = {u.construct for u in unmapped}
    assert "tcp_service" in constructs


@pytest.mark.unit
def test_as3_multi_tenant_each_produces_rules():
    """Two AS3 tenants in one declaration → rules from both."""
    decl = {
        "class": "AS3",
        "declaration": {
            "class": "ADC",
            "tenant1": {
                "class": "Tenant",
                "app1": {
                    "class": "Application",
                    "vs1": {"class": "Service_HTTP", "pool": "pool1"},
                    "pool1": {"class": "Pool", "members": [{"serviceAddress": "svc1", "servicePort": 80}]},
                },
            },
            "tenant2": {
                "class": "Tenant",
                "app2": {
                    "class": "Application",
                    "vs2": {"class": "Service_HTTP", "pool": "pool2"},
                    "pool2": {"class": "Pool", "members": [{"serviceAddress": "svc2", "servicePort": 8080}]},
                },
            },
        },
    }
    unmapped: list = []
    rules, _, _ = _as3_declaration_to_bnk(
        decl,
        source_kind="AS3ConfigMap",
        source_name="ns/cm",
        source_namespace="ns",
        unmapped=unmapped,
    )
    assert len(rules) == 2
    backend_names = {r["backendRefs"][0]["name"] for r in rules}
    assert backend_names == {"svc1", "svc2"}


@pytest.mark.unit
def test_as3_irule_is_unmapped():
    """iRules in AS3 Service → irule unmapped."""
    pool_members = [{"serviceAddress": "svc", "servicePort": 80}]
    decl = _as3_decl(pool_members=pool_members, iRules=["/Common/my-irule"])
    unmapped: list = []
    _as3_declaration_to_bnk(
        decl,
        source_kind="AS3ConfigMap",
        source_name="ns/cm",
        source_namespace="ns",
        unmapped=unmapped,
    )
    constructs = {u.construct for u in unmapped}
    assert "irule" in constructs


@pytest.mark.unit
def test_as3_vip_placeholder_is_unmapped():
    """virtualAddresses=0.0.0.0 → vip_address unmapped."""
    pool_members = [{"serviceAddress": "svc", "servicePort": 80}]
    decl = _as3_decl(pool_members=pool_members, virtualAddresses=["0.0.0.0"])
    unmapped: list = []
    _as3_declaration_to_bnk(
        decl,
        source_kind="AS3ConfigMap",
        source_name="ns/cm",
        source_namespace="ns",
        unmapped=unmapped,
    )
    constructs = {u.construct for u in unmapped}
    assert "vip_address" in constructs


@pytest.mark.unit
def test_as3_invalid_class_is_unmapped_no_exception():
    """class != 'AS3'/'ADC' → as3_parse_error unmapped, no exception raised."""
    decl = {"class": "SomethingElse", "foo": "bar"}
    unmapped: list = []
    # Must not raise
    rules, hostnames, tls = _as3_declaration_to_bnk(
        decl,
        source_kind="AS3ConfigMap",
        source_name="ns/cm",
        source_namespace="ns",
        unmapped=unmapped,
    )
    assert rules == []
    constructs = {u.construct for u in unmapped}
    assert "as3_parse_error" in constructs


@pytest.mark.unit
def test_as3_empty_dict_is_unmapped_no_exception():
    """Empty dict → as3_parse_error unmapped, no exception raised."""
    unmapped: list = []
    rules, _, _ = _as3_declaration_to_bnk(
        {},
        source_kind="AS3ConfigMap",
        source_name="ns/cm",
        source_namespace="ns",
        unmapped=unmapped,
    )
    assert rules == []
    constructs = {u.construct for u in unmapped}
    assert "as3_parse_error" in constructs


@pytest.mark.unit
def test_as3_none_declaration_is_unmapped_no_exception():
    """Non-dict declaration → as3_parse_error unmapped, no exception raised."""
    unmapped: list = []
    rules, _, _ = _as3_declaration_to_bnk(
        None,  # type: ignore[arg-type]
        source_kind="AS3ConfigMap",
        source_name="ns/cm",
        source_namespace="ns",
        unmapped=unmapped,
    )
    assert rules == []
    constructs = {u.construct for u in unmapped}
    assert "as3_parse_error" in constructs


@pytest.mark.unit
def test_translate_cis_to_bnk_with_as3_declarations_folded_in():
    """as3_declarations kwarg folds AS3 rules into translate_cis_to_bnk result."""
    decl = {
        "_source_kind": "AS3ConfigMap",
        "_source_name": "kube-system/as3-cm",
        "_source_namespace": "kube-system",
        "class": "AS3",
        "declaration": {
            "class": "ADC",
            "tenant1": {
                "class": "Tenant",
                "app1": {
                    "class": "Application",
                    "vs1": {"class": "Service_HTTP", "pool": "pool1"},
                    "pool1": {"class": "Pool", "members": [{"serviceAddress": "svc1", "servicePort": 80}]},
                },
            },
        },
    }
    result = translate_cis_to_bnk(virtualservers=[], transportservers=[], as3_declarations=[decl])
    route = yaml.safe_load(result.httproute_yaml)
    # Should have the AS3 rule
    backend_names = [r["backendRefs"][0]["name"] for r in route["spec"]["rules"]]
    assert "svc1" in backend_names
    assert result.source["as3_declaration_count"] == 1
    assert result.source["source_kind"] == "AS3ConfigMap"


@pytest.mark.unit
def test_translate_cis_to_bnk_as3_malformed_template_no_exception():
    """Malformed AS3 (class=None) → unmapped, no exception from translate_cis_to_bnk."""
    bad_decl = {
        "_source_kind": "AS3ConfigMap",
        "_source_name": "default/bad-cm",
        "_source_namespace": "default",
        "class": None,  # triggers parse_error path
    }
    # Must not raise
    result = translate_cis_to_bnk(virtualservers=[], transportservers=[], as3_declarations=[bad_decl])
    constructs = {u.construct for u in result.unmapped}
    assert "as3_parse_error" in constructs


# ===========================================================================
# P4b — F5-annotated Ingress → BNK HTTPRoute (via shared _ingress_to_httproute)
# ===========================================================================


def _f5_ingress(
    name: str = "f5-ing",
    namespace: str = "default",
    host: str | None = "app.example.com",
    path: str = "/",
    svc_name: str = "backend-svc",
    svc_port: int = 8080,
    annotations: dict | None = None,
    tls_secret: str | None = None,
) -> dict:
    """Build a minimal F5 CIS-annotated Ingress plain dict."""
    base_annotations: dict = {"virtual-server.f5.com/ip": "10.0.0.5"}
    if annotations:
        base_annotations.update(annotations)
    ing: dict = {
        "metadata": {
            "name": name,
            "namespace": namespace,
            "annotations": base_annotations,
        },
        "spec": {
            "rules": [
                {
                    "host": host,
                    "http": {
                        "paths": [
                            {
                                "path": path,
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": svc_name,
                                        "port": {"number": svc_port},
                                    }
                                },
                            }
                        ]
                    },
                }
            ],
        },
    }
    if tls_secret:
        ing["spec"]["tls"] = [{"hosts": [host or "*"], "secretName": tls_secret}]
    return ing


@pytest.mark.unit
def test_f5_ingress_host_path_fanout_produces_httproute():
    """F5 Ingress with host + path → HTTPRoute with backendRef."""
    ing = _f5_ingress(host="app.example.com", path="/api", svc_name="api-svc", svc_port=8080)
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresses=[ing],
    )
    assert result.httproute_yaml
    route = yaml.safe_load(result.httproute_yaml)
    rules = route["spec"]["rules"]
    assert len(rules) >= 1
    # The backend must be api-svc
    backend_names = [r["backendRefs"][0]["name"] for r in rules if r.get("backendRefs")]
    assert "api-svc" in backend_names


@pytest.mark.unit
def test_f5_ingress_source_kind_is_ingress():
    """Ingress-only mode → source.source_kind == 'Ingress'."""
    ing = _f5_ingress()
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresses=[ing],
    )
    assert result.source["source_kind"] == "Ingress"
    assert result.source["ingress_count"] == 1


@pytest.mark.unit
def test_f5_ingress_mixed_with_vs_gives_mixed_source_kind():
    """VS + Ingress → source.source_kind == 'Mixed'."""
    vs = {
        "apiVersion": "cis.f5.com/v1",
        "kind": "VirtualServer",
        "metadata": {"name": "vs1", "namespace": "default"},
        "spec": {"host": "vs.example.com", "pools": [{"path": "/", "service": "svc", "servicePort": 80}]},
    }
    ing = _f5_ingress()
    result = translate_cis_to_bnk(
        virtualservers=[vs],
        transportservers=[],
        ingresses=[ing],
    )
    assert result.source["source_kind"] == "Mixed"


@pytest.mark.unit
def test_f5_ingress_ip_annotation_is_unmapped():
    """virtual-server.f5.com/ip → annotation unmapped entry."""
    ing = _f5_ingress(annotations={"virtual-server.f5.com/ip": "10.0.0.5"})
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresses=[ing],
    )
    constructs = [u.construct for u in result.unmapped]
    details = [u.detail for u in result.unmapped]
    assert "annotation" in constructs
    assert any("virtual-server.f5.com/ip" in d for d in details)


@pytest.mark.unit
def test_f5_ingress_clientssl_annotation_is_unmapped():
    """virtual-server.f5.com/clientssl → annotation unmapped entry."""
    ing = _f5_ingress(annotations={
        "virtual-server.f5.com/ip": "10.0.0.5",
        "virtual-server.f5.com/clientssl": "/Common/my-ssl-profile",
    })
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresses=[ing],
    )
    details = [u.detail for u in result.unmapped]
    assert any("clientssl" in d for d in details)


@pytest.mark.unit
def test_f5_ingress_health_annotation_is_unmapped():
    """virtual-server.f5.com/health → annotation unmapped entry."""
    ing = _f5_ingress(annotations={
        "virtual-server.f5.com/ip": "10.0.0.5",
        "virtual-server.f5.com/health": '[{"path":"/healthz","send":"HTTP GET /healthz"}]',
    })
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresses=[ing],
    )
    details = [u.detail for u in result.unmapped]
    assert any("/health" in d for d in details)


@pytest.mark.unit
def test_f5_ingress_balance_annotation_is_unmapped():
    """virtual-server.f5.com/balance → annotation unmapped entry."""
    ing = _f5_ingress(annotations={
        "virtual-server.f5.com/ip": "10.0.0.5",
        "virtual-server.f5.com/balance": "least-connections-member",
    })
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresses=[ing],
    )
    details = [u.detail for u in result.unmapped]
    assert any("balance" in d for d in details)


@pytest.mark.unit
def test_f5_ingress_ssl_redirect_annotation_is_unmapped():
    """ingress.kubernetes.io/ssl-redirect → annotation unmapped entry."""
    ing = _f5_ingress(annotations={
        "virtual-server.f5.com/ip": "10.0.0.5",
        "ingress.kubernetes.io/ssl-redirect": "true",
    })
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresses=[ing],
    )
    details = [u.detail for u in result.unmapped]
    assert any("ssl-redirect" in d for d in details)


@pytest.mark.unit
def test_f5_ingress_tls_secret_produces_https_listener():
    """F5 Ingress with spec.tls.secretName → HTTPS listener with certificateRefs."""
    ing = _f5_ingress(
        host="secure.example.com",
        tls_secret="my-tls-secret",
    )
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresses=[ing],
    )
    gw = yaml.safe_load(result.gateway_yaml)
    listeners = gw["spec"]["listeners"]
    # At least one HTTPS listener should be present
    https_listeners = [li for li in listeners if li.get("protocol") == "HTTPS"]
    assert len(https_listeners) >= 1
    cert_refs = https_listeners[0]["tls"]["certificateRefs"]  # type: ignore[index]
    assert cert_refs[0]["name"] == "my-tls-secret"


@pytest.mark.unit
def test_f5_ingress_default_backend():
    """F5 Ingress defaultBackend → HTTPRoute fallback rule."""
    ing = {
        "metadata": {
            "name": "f5-default-backend",
            "namespace": "default",
            "annotations": {"virtual-server.f5.com/ip": "10.0.0.5"},
        },
        "spec": {
            "defaultBackend": {
                "service": {"name": "default-svc", "port": {"number": 80}}
            },
            "rules": [],
        },
    }
    # Must not raise; defaultBackend provides the only backend reference
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresses=[ing],
    )
    assert result.httproute_yaml
    # Source kind should be Ingress
    assert result.source["ingress_count"] == 1


# ===========================================================================
# P4c — OpenShift Route → BNK HTTPRoute (gap 3)
# ===========================================================================

from services.proxy_translate_cis_service import _route_to_bnk  # noqa: E402


def _route(
    name: str = "my-route",
    namespace: str = "default",
    host: str = "app.example.com",
    path: str = "/",
    to_name: str = "backend-svc",
    to_weight: int = 100,
    target_port=8080,
    tls_termination: str | None = None,
    wildcard_policy: str | None = None,
    alternate_backends: list | None = None,
    tls_inline: bool = False,
) -> dict:
    """Build a minimal OpenShift Route dict for testing."""
    spec: dict = {
        "host": host,
        "path": path,
        "to": {"kind": "Service", "name": to_name, "weight": to_weight},
        "port": {"targetPort": target_port},
    }
    if tls_termination:
        tls: dict = {"termination": tls_termination}
        if tls_inline:
            tls["certificate"] = "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----"
            tls["key"] = "-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----"
        spec["tls"] = tls
    if wildcard_policy:
        spec["wildcardPolicy"] = wildcard_policy
    if alternate_backends:
        spec["alternateBackends"] = alternate_backends
    return {
        "apiVersion": "route.openshift.io/v1",
        "kind": "Route",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


# ---------------------------------------------------------------------------
# Basic host / path / service / port mapping
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_route_host_path_to_name_produces_httproute_rule():
    """Route spec.host/path/to.name → HTTPRoute rule with backendRef."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route()],
    )
    assert result.httproute_yaml
    route_doc = yaml.safe_load(result.httproute_yaml)
    rules = route_doc["spec"]["rules"]
    assert len(rules) == 1
    backend = rules[0]["backendRefs"][0]
    assert backend["name"] == "backend-svc"
    assert backend["port"] == 8080
    assert rules[0]["matches"][0]["path"]["value"] == "/"


@pytest.mark.unit
def test_route_host_in_httproute_hostnames():
    """Route spec.host ends up in HTTPRoute.spec.hostnames."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(host="shop.example.com")],
    )
    route_doc = yaml.safe_load(result.httproute_yaml)
    assert "shop.example.com" in route_doc["spec"].get("hostnames", [])


@pytest.mark.unit
def test_route_source_kind_is_route():
    """Route-only mode → source.source_kind == 'Route'."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route()],
    )
    assert result.source["source_kind"] == "Route"
    assert result.source["route_count"] == 1


@pytest.mark.unit
def test_route_mixed_with_vs_gives_mixed_source_kind():
    """VS + Route → source.source_kind == 'Mixed'."""
    vs = {
        "apiVersion": "cis.f5.com/v1",
        "kind": "VirtualServer",
        "metadata": {"name": "vs1", "namespace": "default"},
        "spec": {"host": "vs.example.com", "pools": [{"path": "/", "service": "svc", "servicePort": 80}]},
    }
    result = translate_cis_to_bnk(
        virtualservers=[vs],
        transportservers=[],
        routes=[_route()],
    )
    assert result.source["source_kind"] == "Mixed"


# ---------------------------------------------------------------------------
# Port-by-name
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_route_port_by_name_is_unmapped_with_placeholder():
    """Route spec.port.targetPort by name → port_by_name unmapped + placeholder port 80."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(target_port="http")],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "port_by_name" in constructs
    # Placeholder port in the backendRef
    route_doc = yaml.safe_load(result.httproute_yaml)
    backend_port = route_doc["spec"]["rules"][0]["backendRefs"][0]["port"]
    assert backend_port == 80


# ---------------------------------------------------------------------------
# TLS termination — edge
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_route_tls_edge_no_inline_cert_is_unmapped():
    """edge TLS (router default cert) → tls_edge_router_default_cert unmapped, NOT an HTTPS listener."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(tls_termination="edge")],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "tls_edge_router_default_cert" in constructs
    # Must NOT emit an HTTPS listener (no real cert ref)
    gw = yaml.safe_load(result.gateway_yaml)
    protocols = [li.get("protocol") for li in gw["spec"]["listeners"]]
    assert "HTTPS" not in protocols


@pytest.mark.unit
def test_route_tls_edge_inline_cert_is_unmapped_never_logged():
    """edge TLS with inline cert/key → tls_edge_inline_cert unmapped; key bytes not in unmapped detail; no HTTPS listener."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(tls_termination="edge", tls_inline=True)],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "tls_edge_inline_cert" in constructs
    # Inline key bytes must NOT appear in any unmapped detail
    for u in result.unmapped:
        assert "BEGIN RSA PRIVATE KEY" not in (u.detail or "")
        assert "FAKE" not in (u.detail or "")
    # Must NOT emit an HTTPS listener (no real cert ref for edge inline cert)
    gw = yaml.safe_load(result.gateway_yaml)
    protocols = [li.get("protocol") for li in gw["spec"]["listeners"]]
    assert "HTTPS" not in protocols


# ---------------------------------------------------------------------------
# TLS termination — passthrough
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_route_tls_passthrough_is_unmapped_no_listener_no_rule():
    """passthrough → tls_passthrough unmapped; NO HTTPRoute rule, NO HTTPS listener."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(tls_termination="passthrough")],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "tls_passthrough" in constructs

    # Must NOT emit an HTTPS listener (no empty-cert Terminate listener)
    gw = yaml.safe_load(result.gateway_yaml)
    protocols = [li.get("protocol") for li in gw["spec"]["listeners"]]
    assert "HTTPS" not in protocols

    # Must NOT produce a backendRef rule for the passthrough route
    route_doc = yaml.safe_load(result.httproute_yaml)
    for rule in route_doc["spec"].get("rules", []):
        for br in rule.get("backendRefs", []):
            assert br.get("name") != "backend-svc", (
                "passthrough route must NOT produce a backendRef"
            )


@pytest.mark.unit
def test_route_tls_passthrough_unmapped_mentions_tlsroute():
    """passthrough unmapped reason mentions TLSRoute and experimental."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(tls_termination="passthrough")],
    )
    passthrough_entries = [u for u in result.unmapped if u.construct == "tls_passthrough"]
    assert passthrough_entries
    reason = passthrough_entries[0].reason.lower()
    assert "tlsroute" in reason
    assert "passthrough" in reason


# ---------------------------------------------------------------------------
# TLS termination — reencrypt
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_route_tls_reencrypt_emits_backend_tls_unmapped():
    """reencrypt → backend_tls unmapped entry always present."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(tls_termination="reencrypt")],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "backend_tls" in constructs


@pytest.mark.unit
def test_route_tls_reencrypt_no_inline_emits_terminate_listener():
    """reencrypt without inline cert → Terminate listener placeholder + tls_reencrypt_no_secret unmapped."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(tls_termination="reencrypt")],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "tls_reencrypt_no_secret" in constructs

    gw = yaml.safe_load(result.gateway_yaml)
    https_listeners = [li for li in gw["spec"]["listeners"] if li.get("protocol") == "HTTPS"]
    assert len(https_listeners) >= 1
    # The listener has a Terminate mode
    assert https_listeners[0]["tls"]["mode"] == "Terminate"


@pytest.mark.unit
def test_route_tls_reencrypt_inline_cert_is_unmapped_never_logged():
    """reencrypt with inline cert → tls_reencrypt_inline_cert unmapped; key bytes never in detail; no HTTPS listener."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(tls_termination="reencrypt", tls_inline=True)],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "tls_reencrypt_inline_cert" in constructs
    for u in result.unmapped:
        assert "BEGIN RSA PRIVATE KEY" not in (u.detail or "")
        assert "FAKE" not in (u.detail or "")
    # Must NOT emit an HTTPS listener (no real cert ref for reencrypt inline cert)
    gw = yaml.safe_load(result.gateway_yaml)
    protocols = [li.get("protocol") for li in gw["spec"]["listeners"]]
    assert "HTTPS" not in protocols


# ---------------------------------------------------------------------------
# wildcardPolicy
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_route_wildcard_subdomain_is_unmapped():
    """wildcardPolicy: Subdomain → wildcard_subdomain unmapped."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(wildcard_policy="Subdomain")],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "wildcard_subdomain" in constructs


# ---------------------------------------------------------------------------
# alternateBackends (weighted backendRefs)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_route_alternate_backends_produce_weighted_backendrefs():
    """alternateBackends → multiple weighted backendRefs in the HTTPRoute rule."""
    alternate_backends = [{"kind": "Service", "name": "alt-svc", "weight": 20}]
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        routes=[_route(to_weight=80, alternate_backends=alternate_backends)],
    )
    route_doc = yaml.safe_load(result.httproute_yaml)
    backend_refs = route_doc["spec"]["rules"][0]["backendRefs"]
    assert len(backend_refs) == 2
    names = {br["name"] for br in backend_refs}
    assert "backend-svc" in names
    assert "alt-svc" in names
    # Weights should be present
    weights = {br["name"]: br.get("weight") for br in backend_refs}
    assert weights["backend-svc"] == 80
    assert weights["alt-svc"] == 20


# ---------------------------------------------------------------------------
# _route_to_bnk unit tests (internal function)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_route_to_bnk_direct_http_no_tls():
    """_route_to_bnk with no TLS → rules + hostnames, no tls_listeners."""
    from services.proxy_translate_service import UnmappedEntry
    unmapped: list[UnmappedEntry] = []
    rules, hostnames, tls_listeners, ns = _route_to_bnk(
        route_dict=_route(),
        gateway_name="test-gw",
        target_namespace=None,
        unmapped=unmapped,
    )
    assert len(rules) == 1
    assert "app.example.com" in hostnames
    assert tls_listeners == []
    assert not unmapped


@pytest.mark.unit
def test_route_to_bnk_passthrough_returns_empty_rules():
    """_route_to_bnk passthrough → empty rules, empty tls_listeners, passthrough unmapped."""
    from services.proxy_translate_service import UnmappedEntry
    unmapped: list[UnmappedEntry] = []
    rules, hostnames, tls_listeners, ns = _route_to_bnk(
        route_dict=_route(tls_termination="passthrough"),
        gateway_name="test-gw",
        target_namespace=None,
        unmapped=unmapped,
    )
    assert rules == []
    assert tls_listeners == []
    constructs = {u.construct for u in unmapped}
    assert "tls_passthrough" in constructs


# ---------------------------------------------------------------------------
# Discovery gating (fetch.py)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_fetch_openshift_routes_returns_empty_on_404():
    """_fetch_openshift_routes returns [] on ApiException 404 (non-OpenShift cluster)."""
    from unittest.mock import MagicMock, patch

    from kubernetes.client.exceptions import ApiException

    from services.scanner.fetch import _fetch_openshift_routes

    mock_api_client = MagicMock()
    with patch("services.scanner.fetch.client.CustomObjectsApi") as mock_cls:
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.side_effect = ApiException(status=404)
        mock_cls.return_value = mock_api
        result = _fetch_openshift_routes(mock_api_client)
    assert result == []


@pytest.mark.unit
def test_fetch_openshift_routes_returns_empty_on_generic_exception():
    """_fetch_openshift_routes returns [] on any unexpected exception."""
    from unittest.mock import MagicMock, patch

    from services.scanner.fetch import _fetch_openshift_routes

    mock_api_client = MagicMock()
    with patch("services.scanner.fetch.client.CustomObjectsApi") as mock_cls:
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.side_effect = RuntimeError("connection refused")
        mock_cls.return_value = mock_api
        result = _fetch_openshift_routes(mock_api_client)
    assert result == []


@pytest.mark.unit
def test_fetch_scan_data_includes_openshift_routes_key():
    """fetch_scan_data return dict always has openshift_routes key (empty on non-OCP)."""
    from unittest.mock import MagicMock, patch

    from services.scanner.fetch import fetch_scan_data

    # Stub out all the fetcher functions to return minimal data
    minimal_data = {
        "items": [],
        "groups": [],
    }
    mock_api_client = MagicMock()
    mock_k8s_service = MagicMock()
    mock_k8s_service._fetch_from_k8s.return_value = []

    with patch("services.scanner.fetch._discover_api_groups", return_value=frozenset()):
        with patch("services.scanner.fetch._fetch_version", return_value={}):
            with patch("services.scanner.fetch._fetch_nodes", return_value=[]):
                with patch("services.scanner.fetch._fetch_crds", return_value=[]):
                    with patch("services.scanner.fetch._fetch_storage_classes", return_value=[]):
                        with patch("services.scanner.fetch._fetch_namespaces", return_value=[]):
                            with patch("services.scanner.fetch._fetch_daemonsets", return_value=[]):
                                with patch("services.scanner.fetch._fetch_helm_releases", return_value=[]):
                                    with patch("services.scanner.fetch.discover_f5_pods", return_value=([], [])):
                                        with patch("services.scanner.fetch._fetch_pods_in_ns", return_value=[]):
                                            with patch("services.scanner.fetch._fetch_cis_as3_configmaps", return_value=[]):
                                                result = fetch_scan_data(mock_api_client, mock_k8s_service, cluster_id=1)
    assert "openshift_routes" in result
    assert result["openshift_routes"] == []


# ---------------------------------------------------------------------------
# prereqs analyze_cis — Route-only cluster → PARTIAL
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_analyze_cis_route_only_cluster_is_partial():
    """Route-only cluster (no controller, no CRDs) → PARTIAL status; routes in inventory."""
    from services.scanner.prereqs import analyze_cis

    routes = [
        {"metadata": {"name": "my-route", "namespace": "default"}, "spec": {"host": "app.example.com"}},
    ]
    result = analyze_cis(
        cis_controllers=[],
        cis_virtualservers=[],
        cis_transportservers=[],
        cis_ingresslinks=[],
        cis_as3_configmaps=[],
        cis_f5_ingresses=[],
        openshift_routes=routes,
    )
    assert result["status"] == "partial"
    assert len(result["inventory"]["openshift_routes"]) == 1


@pytest.mark.unit
def test_analyze_cis_no_surfaces_missing():
    """No CIS surfaces at all → MISSING; openshift_routes empty."""
    from services.scanner.prereqs import analyze_cis

    result = analyze_cis(
        cis_controllers=[],
        cis_virtualservers=[],
        cis_transportservers=[],
        cis_ingresslinks=[],
        cis_as3_configmaps=[],
        cis_f5_ingresses=[],
        openshift_routes=[],
    )
    assert result["status"] == "missing"
    assert result["inventory"]["openshift_routes"] == []


@pytest.mark.unit
def test_analyze_cis_backward_compat_no_routes_kwarg():
    """analyze_cis works when openshift_routes kwarg is omitted (backward compat)."""
    from services.scanner.prereqs import analyze_cis

    result = analyze_cis(
        cis_controllers=[],
        cis_virtualservers=[],
        cis_transportservers=[],
        cis_ingresslinks=[],
        cis_as3_configmaps=[],
        cis_f5_ingresses=[],
        # openshift_routes intentionally omitted
    )
    assert result["status"] == "missing"
    assert "openshift_routes" in result["inventory"]
    assert result["inventory"]["openshift_routes"] == []


# ===========================================================================
# P4e — TLSProfile resolution (gap 5a)
# ===========================================================================

def _tls_profile_cr(
    name: str = "my-tls",
    namespace: str = "default",
    reference: str = "secret",
    client_ssl: str = "my-cert-secret",
) -> dict:
    """Build a minimal CIS TLSProfile CR dict."""
    return {
        "apiVersion": "cis.f5.com/v1",
        "kind": "TLSProfile",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "tls": {
                "reference": reference,
                "clientSSL": client_ssl,
            },
        },
    }


def _vs_with_tlsprofile(
    name: str = "vs-tls",
    namespace: str = "default",
    tls_profile_name: str = "my-tls",
) -> dict:
    """Build a CIS VirtualServer with spec.tls.tlsProfileName set."""
    return {
        "apiVersion": "cis.f5.com/v1",
        "kind": "VirtualServer",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "host": "tls.example.com",
            "pools": [{"path": "/", "service": "backend-svc", "servicePort": 8080}],
            "tls": {"tlsProfileName": tls_profile_name},
        },
    }


@pytest.mark.unit
def test_tlsprofile_reference_secret_emits_https_listener():
    """VS tlsProfileName → TLSProfile with reference=secret → HTTPS Terminate listener."""
    tp = _tls_profile_cr(reference="secret", client_ssl="my-cert-secret")
    vs = _vs_with_tlsprofile(tls_profile_name="my-tls")
    result = translate_cis_to_bnk(
        virtualservers=[vs],
        transportservers=[],
        tlsprofiles=[tp],
    )
    gw = yaml.safe_load(result.gateway_yaml)
    https_listeners = [li for li in gw["spec"]["listeners"] if li.get("protocol") == "HTTPS"]
    assert len(https_listeners) >= 1
    cert_refs = https_listeners[0]["tls"]["certificateRefs"]
    assert any(cr["name"] == "my-cert-secret" for cr in cert_refs)
    # Must NOT have a tls_profile_ref unmapped entry
    constructs = {u.construct for u in result.unmapped}
    assert "tls_profile_ref" not in constructs


@pytest.mark.unit
def test_tlsprofile_reference_bigip_is_unmapped():
    """VS tlsProfileName → TLSProfile with reference=bigip → tls_profile_ref unmapped, no HTTPS listener."""
    tp = _tls_profile_cr(reference="bigip", client_ssl="/Common/clientssl")
    vs = _vs_with_tlsprofile(tls_profile_name="my-tls")
    result = translate_cis_to_bnk(
        virtualservers=[vs],
        transportservers=[],
        tlsprofiles=[tp],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "tls_profile_ref" in constructs
    # Reason must mention bigip
    tls_entries = [u for u in result.unmapped if u.construct == "tls_profile_ref"]
    assert any("bigip" in (u.detail or "").lower() or "bigip" in (u.reason or "").lower()
               for u in tls_entries)
    # Must NOT emit an HTTPS listener
    gw = yaml.safe_load(result.gateway_yaml)
    protocols = [li.get("protocol") for li in gw["spec"]["listeners"]]
    assert "HTTPS" not in protocols


@pytest.mark.unit
def test_tlsprofile_not_found_is_unmapped():
    """VS tlsProfileName → TLSProfile CR not in cluster scan → tls_profile_ref unmapped."""
    vs = _vs_with_tlsprofile(tls_profile_name="missing-tls")
    # No tlsprofiles provided
    result = translate_cis_to_bnk(
        virtualservers=[vs],
        transportservers=[],
        tlsprofiles=[],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "tls_profile_ref" in constructs
    tls_entries = [u for u in result.unmapped if u.construct == "tls_profile_ref"]
    assert any("missing-tls" in (u.detail or "") for u in tls_entries)
    # Must NOT emit an HTTPS listener
    gw = yaml.safe_load(result.gateway_yaml)
    protocols = [li.get("protocol") for li in gw["spec"]["listeners"]]
    assert "HTTPS" not in protocols


# ===========================================================================
# P4e — IngressLink translation (gap 5b)
# ===========================================================================

def _ingresslink(
    name: str = "il1",
    namespace: str = "default",
    host: str = "nginx.example.com",
    vip: str = "10.0.0.100",
    selector_labels: dict | None = None,
) -> dict:
    """Build a minimal CIS IngressLink CR dict."""
    selector_labels = selector_labels or {"app": "nginx-ingress"}
    return {
        "apiVersion": "cis.f5.com/v1",
        "kind": "IngressLink",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "host": host,
            "virtualServerAddress": vip,
            "selector": {"matchLabels": selector_labels},
        },
    }


@pytest.mark.unit
def test_ingresslink_placeholder_backendref_and_vip_unmapped():
    """IngressLink → placeholder backendRef + vip_address unmapped + ingresslink_selector unmapped."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresslinks=[_ingresslink()],
    )
    # Placeholder backendRef must be present
    route_doc = yaml.safe_load(result.httproute_yaml)
    rules = route_doc["spec"].get("rules") or []
    placeholder_found = any(
        br.get("name") == "REPLACE_WITH_SELECTED_SERVICE"
        for rule in rules
        for br in rule.get("backendRefs", [])
    )
    assert placeholder_found, "Expected REPLACE_WITH_SELECTED_SERVICE placeholder backendRef"

    constructs = {u.construct for u in result.unmapped}
    assert "vip_address" in constructs, "Expected vip_address unmapped for IngressLink VIP"
    assert "ingresslink_selector" in constructs, "Expected ingresslink_selector unmapped"


@pytest.mark.unit
def test_ingresslink_hostname_in_httproute():
    """IngressLink spec.host → hostname appears in HTTPRoute hostnames."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresslinks=[_ingresslink(host="nginx.example.com")],
    )
    route_doc = yaml.safe_load(result.httproute_yaml)
    hostnames = route_doc["spec"].get("hostnames") or []
    assert "nginx.example.com" in hostnames


@pytest.mark.unit
def test_ingresslink_source_count_in_result():
    """translate_cis_to_bnk result.source contains ingresslink_count."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        ingresslinks=[_ingresslink()],
    )
    assert result.source.get("ingresslink_count") == 1


# ===========================================================================
# P4e — _parse_cis_args captures --bigip-partition (gap 5c)
# ===========================================================================

@pytest.mark.unit
def test_parse_cis_args_captures_partition_equals_form():
    """--bigip-partition=Production captured from args."""
    from services.scanner.prereqs import _parse_cis_args

    _, _, partition = _parse_cis_args([
        "--bigip-url=https://10.0.0.1",
        "--bigip-partition=Production",
    ])
    assert partition == "Production"


@pytest.mark.unit
def test_parse_cis_args_captures_partition_space_form():
    """--bigip-partition Production (space form) captured from args."""
    from services.scanner.prereqs import _parse_cis_args

    _, _, partition = _parse_cis_args([
        "--bigip-url", "https://10.0.0.1",
        "--bigip-partition", "MyPartition",
    ])
    assert partition == "MyPartition"


@pytest.mark.unit
def test_parse_cis_args_partition_none_when_absent():
    """--bigip-partition not in args → None returned."""
    from services.scanner.prereqs import _parse_cis_args

    _, _, partition = _parse_cis_args(["--bigip-url=https://10.0.0.1"])
    assert partition is None


@pytest.mark.unit
def test_parse_cis_args_partition_surfaced_in_controller_info():
    """analyze_cis surfaces bigip_partition in controller_info when --bigip-partition present."""
    from services.scanner.prereqs import analyze_cis

    controller_raw = {
        "images": ["f5networks/k8s-bigip-ctlr:2.x"],
        "namespace": "kube-system",
        "replicas_ready": 1,
        "args": [
            "--bigip-url=https://10.0.0.1",
            "--bigip-partition=Production",
            "--bigip-ctlr-creds=kube-system/bigip-creds",
        ],
    }
    result = analyze_cis(
        cis_controllers=[controller_raw],
        cis_virtualservers=[],
        cis_transportservers=[],
        cis_ingresslinks=[],
        cis_as3_configmaps=[],
        cis_f5_ingresses=[],
    )
    assert result["controller"]["bigip_partition"] == "Production"


# ===========================================================================
# P4e — legacy non-AS3 ConfigMap → unmapped (gap 5d)
# ===========================================================================

@pytest.mark.unit
def test_legacy_f5type_configmap_is_unmapped():
    """Legacy ConfigMap with frontend/backend schema → as3_parse_error unmapped, not translated."""
    result = translate_cis_to_bnk(
        virtualservers=[],
        transportservers=[],
        as3_declarations=[{
            "_source_kind": "AS3ConfigMap",
            "_source_name": "default/legacy-cm",
            "_source_namespace": "default",
            "_template_parse_error": (
                "legacy non-AS3 F5 ConfigMap schema (frontend/backend) is not "
                "auto-translated; migrate it to AS3 or recreate as a VirtualServer CR"
            ),
            "class": "legacy-non-as3",
        }],
    )
    constructs = {u.construct for u in result.unmapped}
    assert "as3_parse_error" in constructs
    # Must not produce any real backendRef rules beyond the placeholder
    route_doc = yaml.safe_load(result.httproute_yaml)
    rules = route_doc["spec"].get("rules") or []
    # Only placeholder rules allowed (no real service backendRefs from legacy CM)
    real_backend_names = [
        br.get("name")
        for rule in rules
        for br in rule.get("backendRefs", [])
        if br.get("name") not in ("REPLACE_WITH_SERVICE_NAME",)
    ]
    assert real_backend_names == [], f"Expected no real backendRefs from legacy CM, got {real_backend_names}"


# ===========================================================================
# P4e — fetch helpers for TLSProfile and IngressLink
# ===========================================================================

@pytest.mark.unit
def test_fetch_cis_tlsprofiles_full_returns_empty_on_404():
    """_fetch_cis_tlsprofiles_full returns [] on ApiException 404 (CRD not installed)."""
    from unittest.mock import MagicMock, patch

    from kubernetes.client.exceptions import ApiException

    from services.scanner.fetch import _fetch_cis_tlsprofiles_full

    mock_api_client = MagicMock()
    with patch("services.scanner.fetch.client.CustomObjectsApi") as mock_cls:
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.side_effect = ApiException(status=404)
        mock_cls.return_value = mock_api
        result = _fetch_cis_tlsprofiles_full(mock_api_client)
    assert result == []


@pytest.mark.unit
def test_fetch_cis_ingresslinks_full_returns_empty_on_404():
    """_fetch_cis_ingresslinks_full returns [] on ApiException 404 (CRD not installed)."""
    from unittest.mock import MagicMock, patch

    from kubernetes.client.exceptions import ApiException

    from services.scanner.fetch import _fetch_cis_ingresslinks_full

    mock_api_client = MagicMock()
    with patch("services.scanner.fetch.client.CustomObjectsApi") as mock_cls:
        mock_api = MagicMock()
        mock_api.list_cluster_custom_object.side_effect = ApiException(status=404)
        mock_cls.return_value = mock_api
        result = _fetch_cis_ingresslinks_full(mock_api_client)
    assert result == []
