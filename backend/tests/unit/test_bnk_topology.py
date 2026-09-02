"""
Unit tests for services.bnk.topology — gateway topology tree analysis.

Tests the topology builder with constructed K8s Gateway API data.
No mocking — these are pure data transformations.
"""

import pytest

from services.bnk.topology import (
    _build_cne_instance,
    _build_data_plane,
    _build_egress,
    _build_vlan,
    _match_analyzers,
    _match_net_policies,
    _match_routes_to_listener,
    _match_sec_policies,
    analyze_topology,
    resolve_list_refs,
)

# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------


def _resource(name: str, namespace: str = "f5-bnk", **kw) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace, "creationTimestamp": "2026-01-01T00:00:00Z"},
        "spec": kw.get("spec", {}),
        "status": kw.get("status", {}),
    }


def _gateway(name: str = "gw-prod", namespace: str = "f5-bnk", listeners=None, addresses=None,
             conditions=None, listener_status=None) -> dict:
    listeners = listeners or [{"name": "http", "protocol": "HTTP", "port": 80}]
    status: dict = {
        "addresses": [{"value": a} for a in (addresses or ["10.0.0.1"])],
        "conditions": conditions or [
            {"type": "Programmed", "status": "True"},
            {"type": "Accepted", "status": "True"},
        ],
    }
    if listener_status:
        status["listeners"] = listener_status
    return _resource(name, namespace, spec={
        "gatewayClassName": "f5-bnk",
        "listeners": listeners,
    }, status=status)


def _httproute(name: str, gw_name: str, gw_ns: str = "f5-bnk",
               namespace: str = "f5-bnk", backends=None, section_name=None,
               parent_conditions=None) -> dict:
    parent_ref: dict = {"name": gw_name, "namespace": gw_ns}
    if section_name:
        parent_ref["sectionName"] = section_name
    status: dict = {}
    if parent_conditions is not None:
        status["parents"] = [{
            "parentRef": {"name": gw_name, "namespace": gw_ns, "sectionName": section_name} if section_name else {"name": gw_name, "namespace": gw_ns},
            "conditions": parent_conditions,
        }]
    return _resource(name, namespace, spec={
        "parentRefs": [parent_ref],
        "hostnames": ["example.com"],
        "rules": [{
            "backendRefs": backends or [
                {"name": "svc-1", "port": 8080, "kind": "Service", "group": ""},
            ],
        }],
    }, status=status)


def _empty_resources() -> dict:
    return {k: [] for k in [
        "gatewayclass", "gateway", "httproute", "grpcroute", "tcproute", "udproute",
        "tlsroute", "l4route", "referencegrant", "bnksecpolicy", "bnknetpolicy",
        "f5bigfwpolicy", "f5bigcneirule", "f5biganalyzer",
        "f5bigcneaddresslist", "f5bigcneportlist",
        "f5spkvlan", "cneinstance", "f5spkstaticroute",
        "f5spksnatpool", "f5spkegress", "f5bigloghslpub", "f5biglogprofile",
        "service",
    ]}


# ---------------------------------------------------------------------------
# analyze_topology — end-to-end
# ---------------------------------------------------------------------------


class TestAnalyzeTopology:
    def test_empty_cluster(self):
        result = analyze_topology({"resources": _empty_resources()})
        assert result["topology"] == []
        assert result["dataPlane"]["vlans"] == []
        assert result["referenceGrants"] == []
        assert result["counts"]["totalRoutes"] == 0

    def test_gateway_with_listener_and_route(self):
        resources = _empty_resources()
        resources["gateway"] = [_gateway()]
        resources["httproute"] = [_httproute("web-route", "gw-prod")]

        result = analyze_topology({"resources": resources})

        assert len(result["topology"]) == 1
        gw = result["topology"][0]
        assert gw["name"] == "gw-prod"
        assert gw["gatewayClassName"] == "f5-bnk"
        assert gw["addresses"] == ["10.0.0.1"]

        assert len(gw["listeners"]) == 1
        listener = gw["listeners"][0]
        assert listener["name"] == "http"
        assert listener["port"] == 80
        assert len(listener["routes"]) == 1
        assert listener["routes"][0]["name"] == "web-route"
        assert listener["routes"][0]["backends"][0]["name"] == "svc-1"

    def test_route_matches_by_section_name(self):
        """Route with sectionName only attaches to the matching listener."""
        resources = _empty_resources()
        resources["gateway"] = [_gateway(listeners=[
            {"name": "http", "protocol": "HTTP", "port": 80},
            {"name": "https", "protocol": "HTTPS", "port": 443},
        ])]
        resources["httproute"] = [
            _httproute("http-route", "gw-prod", section_name="http"),
            _httproute("https-route", "gw-prod", section_name="https"),
        ]

        result = analyze_topology({"resources": resources})
        gw = result["topology"][0]

        http_listener = next(li for li in gw["listeners"] if li["name"] == "http")
        https_listener = next(li for li in gw["listeners"] if li["name"] == "https")

        assert len(http_listener["routes"]) == 1
        assert http_listener["routes"][0]["name"] == "http-route"
        assert len(https_listener["routes"]) == 1
        assert https_listener["routes"][0]["name"] == "https-route"

    def test_route_without_section_attaches_to_all_listeners(self):
        """Route with no sectionName attaches to every listener on the gateway."""
        resources = _empty_resources()
        resources["gateway"] = [_gateway(listeners=[
            {"name": "http", "protocol": "HTTP", "port": 80},
            {"name": "https", "protocol": "HTTPS", "port": 443},
        ])]
        resources["httproute"] = [_httproute("catch-all", "gw-prod")]

        result = analyze_topology({"resources": resources})
        gw = result["topology"][0]
        for listener in gw["listeners"]:
            assert len(listener["routes"]) == 1
            assert listener["routes"][0]["name"] == "catch-all"

    def test_multiple_route_types(self):
        """All route types (HTTP, TCP, UDP, TLS, GRPC, L4) are included."""
        resources = _empty_resources()
        resources["gateway"] = [_gateway()]
        resources["httproute"] = [_httproute("r1", "gw-prod")]
        resources["tcproute"] = [_resource("r2", spec={
            "parentRefs": [{"name": "gw-prod", "namespace": "f5-bnk"}],
            "rules": [{"backendRefs": [{"name": "tcp-svc", "port": 9090}]}],
        })]

        result = analyze_topology({"resources": resources})
        routes = result["topology"][0]["listeners"][0]["routes"]
        assert len(routes) == 2
        kinds = {r["kind"] for r in routes}
        assert "HTTPRoute" in kinds
        assert "TCPRoute" in kinds
        assert result["counts"]["httpRoutes"] == 1
        assert result["counts"]["tcpRoutes"] == 1
        assert result["counts"]["totalRoutes"] == 2

    def test_cross_namespace_route_ignored_when_not_matching(self):
        """Route targeting a different gateway namespace is not included."""
        resources = _empty_resources()
        resources["gateway"] = [_gateway()]
        resources["httproute"] = [_httproute("r1", "gw-prod", gw_ns="other-ns")]

        result = analyze_topology({"resources": resources})
        routes = result["topology"][0]["listeners"][0]["routes"]
        assert len(routes) == 0

    def test_counts_summary(self):
        resources = _empty_resources()
        resources["gateway"] = [_gateway()]
        resources["httproute"] = [_httproute("r1", "gw-prod")]
        resources["f5spkvlan"] = [_resource("v1")]
        resources["cneinstance"] = [_resource("c1")]

        result = analyze_topology({"resources": resources})
        counts = result["counts"]
        assert counts["gateways"] == 1
        assert counts["listeners"] == 1
        assert counts["httpRoutes"] == 1
        assert counts["vlans"] == 1
        assert counts["cneInstances"] == 1

    def test_reference_grants_included(self):
        resources = _empty_resources()
        resources["referencegrant"] = [_resource("rg-1", spec={
            "from": [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute", "namespace": "app"}],
            "to": [{"group": "", "kind": "Service"}],
        })]

        result = analyze_topology({"resources": resources})
        assert len(result["referenceGrants"]) == 1
        assert result["referenceGrants"][0]["name"] == "rg-1"

    def test_gateway_operational_state(self):
        resources = _empty_resources()
        resources["gateway"] = [_gateway(
            conditions=[
                {"type": "Accepted", "status": "True"},
                {"type": "Programmed", "status": "False", "message": "address conflict"},
            ],
        )]

        result = analyze_topology({"resources": resources})
        gw = result["topology"][0]
        assert gw["accepted"] is True
        assert gw["programmed"] is False
        assert any(c["type"] == "Programmed" and c["status"] == "False" for c in gw["conditions"])

    def test_listener_operational_state(self):
        resources = _empty_resources()
        resources["gateway"] = [_gateway(listener_status=[{
            "name": "http",
            "attachedRoutes": 3,
            "conditions": [{"type": "Accepted", "status": "True"}],
        }])]

        result = analyze_topology({"resources": resources})
        listener = result["topology"][0]["listeners"][0]
        assert listener["attachedRouteCount"] == 3
        assert listener["conditions"][0]["type"] == "Accepted"

    def test_route_operational_state(self):
        resources = _empty_resources()
        resources["gateway"] = [_gateway()]
        resources["httproute"] = [_httproute("web-route", "gw-prod", parent_conditions=[
            {"type": "Accepted", "status": "False", "message": "no matching listener"},
        ])]

        result = analyze_topology({"resources": resources})
        route = result["topology"][0]["listeners"][0]["routes"][0]
        assert route["accepted"] is False
        assert route["conditionMessage"] == "no matching listener"
        assert route["conditions"][0]["type"] == "Accepted"


# ---------------------------------------------------------------------------
# _match_routes_to_listener
# ---------------------------------------------------------------------------


class TestMatchRoutesToListener:
    def test_empty_routes(self):
        assert _match_routes_to_listener([], [], "gw", "ns", "http") == []

    def test_route_matching_gateway(self):
        route = _httproute("r1", "gw", gw_ns="ns")
        result = _match_routes_to_listener([(route, "HTTPRoute")], [], "gw", "ns", "http")
        assert len(result) == 1
        assert result[0]["name"] == "r1"
        assert result[0]["kind"] == "HTTPRoute"

    def test_route_not_matching_different_gateway(self):
        route = _httproute("r1", "other-gw", gw_ns="ns")
        result = _match_routes_to_listener([(route, "HTTPRoute")], [], "gw", "ns", "http")
        assert result == []


# ---------------------------------------------------------------------------
# _match_analyzers
# ---------------------------------------------------------------------------


class TestMatchAnalyzers:
    def test_analyzer_matching_route(self):
        analyzer = _resource("analyzer-1", spec={
            "applications": [{"name": "web-route", "kind": "HTTPRoute", "namespace": "f5-bnk"}],
            "schedule": "5m",
            "script": {"type": "custom", "custom": {"parameters": []}},
            "dataSources": [],
        })
        result = _match_analyzers([analyzer], "web-route", "HTTPRoute", "f5-bnk", "f5-bnk")
        assert len(result) == 1
        assert result[0]["name"] == "analyzer-1"

    def test_no_match(self):
        analyzer = _resource("analyzer-1", spec={
            "applications": [{"name": "other-route", "kind": "HTTPRoute", "namespace": "f5-bnk"}],
        })
        result = _match_analyzers([analyzer], "web-route", "HTTPRoute", "f5-bnk", "f5-bnk")
        assert result == []


# ---------------------------------------------------------------------------
# _match_net_policies / _match_sec_policies
# ---------------------------------------------------------------------------


class TestMatchNetPolicies:
    def test_policy_targeting_gateway_listener(self):
        np = _resource("np-1", spec={
            "targetRefs": [{"name": "gw-prod", "sectionName": "http", "kind": "Gateway"}],
            "extensionRefs": [{"kind": "F5BigCneIrule", "name": "ir-1", "group": ""}],
        }, status={"descendants": []})
        result = _match_net_policies([np], {}, "gw-prod", "http")
        assert len(result) == 1
        assert result[0]["name"] == "np-1"

    def test_policy_different_listener_not_matched(self):
        np = _resource("np-1", spec={
            "targetRefs": [{"name": "gw-prod", "sectionName": "https", "kind": "Gateway"}],
            "extensionRefs": [],
        }, status={"descendants": []})
        result = _match_net_policies([np], {}, "gw-prod", "http")
        assert result == []

    def test_net_policy_operational_state(self):
        np = _resource("np-1", spec={
            "targetRefs": [{"name": "gw-prod", "sectionName": "http", "kind": "Gateway"}],
            "extensionRefs": [],
        }, status={
            "descendants": [],
            "conditions": [
                {"type": "Resolved", "status": "True", "message": "resolved"},
                {"type": "Programmed", "status": "False", "message": "not programmed"},
            ],
        })
        result = _match_net_policies([np], {}, "gw-prod", "http")
        assert result[0]["resolved"] is True
        assert result[0]["programmed"] is False
        assert result[0]["messages"]["programmed"] == "not programmed"


class TestMatchSecPolicies:
    def test_sec_policy_with_firewall(self):
        sp = _resource("sp-1", spec={
            "targetRefs": [{"name": "gw-prod", "kind": "Gateway", "sectionName": "http"}],
            "extensionRefs": [{"kind": "F5BigFwPolicy", "name": "fw-1"}],
        })
        fw = _resource("fw-1", spec={
            "rule": [{"name": "allow-all", "action": "accept", "ipProtocol": "any", "logging": False}],
        })
        from services.bnk.helpers import make_resource_map
        fw_map = make_resource_map([fw])
        result = _match_sec_policies([sp], fw_map, {}, {}, "gw-prod")
        assert len(result) == 1
        assert result[0]["name"] == "sp-1"
        assert len(result[0]["firewallPolicies"]) == 1
        assert result[0]["firewallPolicies"][0]["rules"][0]["action"] == "accept"

    def test_sec_policy_operational_state(self):
        sp = _resource("sp-1", spec={
            "targetRefs": [{"name": "gw-prod", "kind": "Gateway", "sectionName": "http"}],
            "extensionRefs": [{"kind": "F5BigFwPolicy", "name": "fw-1"}],
        }, status={
            "conditions": [
                {"type": "Resolved", "status": "True", "message": "resolved"},
                {"type": "Programmed", "status": "True"},
            ],
        })
        from services.bnk.helpers import make_resource_map
        fw_map = make_resource_map([_resource("fw-1", spec={"rule": []})])
        result = _match_sec_policies([sp], fw_map, {}, {}, "gw-prod")
        assert result[0]["resolved"] is True
        assert result[0]["programmed"] is True
        assert result[0]["messages"]["resolved"] == "resolved"


# ---------------------------------------------------------------------------
# resolve_list_refs
# ---------------------------------------------------------------------------


class TestResolveListRefs:
    def test_resolves_address_list(self):
        al = _resource("allow-list", spec={"addresses": ["10.0.0.0/8", "192.168.0.0/16"]})
        from services.bnk.helpers import make_resource_map
        addr_map = make_resource_map([al])
        result = resolve_list_refs({"allow-list"}, addr_map, "f5-bnk", "addresses")
        assert len(result) == 1
        assert result[0]["name"] == "allow-list"
        assert result[0]["addresses"] == ["10.0.0.0/8", "192.168.0.0/16"]

    def test_missing_resource_returns_empty_list(self):
        result = resolve_list_refs({"nonexistent"}, {}, "f5-bnk", "addresses")
        assert result[0]["addresses"] == []


# ---------------------------------------------------------------------------
# _build_vlan / _build_cne_instance
# ---------------------------------------------------------------------------


class TestBuildVlan:
    def test_vlan_with_programmed_condition(self):
        v = _resource("int-vlan", spec={
            "interfaces": ["1.1"], "selfip_v4s": ["10.1.1.1"], "mtu": 1500,
        }, status={
            "conditions": [{"type": "Programmed", "status": "True"}],
        })
        result = _build_vlan(v)
        assert result["name"] == "int-vlan"
        assert result["ready"] is True
        assert result["mtu"] == 1500

    def test_vlan_not_ready(self):
        v = _resource("new-vlan", spec={"interfaces": []}, status={})
        result = _build_vlan(v)
        assert result["ready"] is False


class TestBuildCneInstance:
    def test_cne_with_features(self):
        cne = _resource("cne-1", spec={
            "firewallACL": {"enabled": True},
            "intelligentLB": {"enabled": False},
            "coreCollection": {"enabled": True},
            "envDiscovery": {"enabled": False},
            "containerPlatform": "k8s",
            "networkAttachments": ["net-1"],
        }, status={"phase": "Running"})
        result = _build_cne_instance(cne)
        assert result["name"] == "cne-1"
        assert result["features"]["firewallACL"] is True
        assert result["features"]["intelligentLB"] is False
        assert result["features"]["coreCollection"] is True
        assert result["features"]["envDiscovery"] is False
        assert result["containerPlatform"] == "k8s"
        assert result["phase"] == "Running"
        assert result["ready"] is False

    def test_cne_ready_when_programmed(self):
        cne = _resource("cne-1", spec={"containerPlatform": "k8s"}, status={
            "phase": "Running",
            "conditions": [{"type": "Programmed", "status": "True"}],
        })
        result = _build_cne_instance(cne)
        assert result["ready"] is True


class TestBuildEgress:
    def test_egress_with_full_spec(self):
        eg = _resource("bnk-egress-demo", namespace="f5-cne-system", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
            "egressSnatpool": "my-snatpool",
            "firewallEnforcedPolicy": "egress-demo-fw",
            "logProfile": "egress-log-profile",
            "pseudoCNIConfig": {
                "namespaces": ["bnk-egress-demo"],
                "appPodInterface": "eth0",
                "vxlan": {
                    "create": True,
                    "tmmInterfaceName": "ext-vlan",
                    "nodeInterfaceName": "ens5",
                    "ipv4Subnet": "192.168.0.0",
                    "ipv4PrefixLen": 16,
                },
            },
        }, status={
            "conditions": [
                {"type": "Accepted", "status": "True", "reason": "Accepted"},
                {"type": "Programmed", "status": "True", "reason": "Programmed",
                 "message": "CR config sent to all grpc endpoints"},
            ],
        })
        result = _build_egress(eg)
        assert result["name"] == "bnk-egress-demo"
        assert result["namespace"] == "f5-cne-system"
        assert result["snatType"] == "SRC_TRANS_AUTOMAP"
        assert result["egressSnatpool"] == "my-snatpool"
        assert result["firewallEnforcedPolicy"] == "egress-demo-fw"
        assert result["logProfile"] == "egress-log-profile"
        assert result["capturedNamespaces"] == ["bnk-egress-demo"]
        assert result["vxlan"] == {"tmmInterfaceName": "ext-vlan", "nodeInterfaceName": "ens5"}
        assert result["ready"] is True

    def test_egress_with_missing_fields_uses_defaults(self):
        eg = _resource("minimal-egress", spec={"snatType": "SRC_TRANS_NONE"})
        result = _build_egress(eg)
        assert result["snatType"] == "SRC_TRANS_NONE"
        assert result["egressSnatpool"] is None
        assert result["firewallEnforcedPolicy"] is None
        assert result["logProfile"] is None
        assert result["capturedNamespaces"] == []
        assert result["vxlan"] is None
        assert result["ready"] is False

    def test_egress_with_cni_config_but_no_vxlan_returns_none(self):
        eg = _resource("egress-no-vxlan", namespace="f5-cne-system", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
            "pseudoCNIConfig": {
                "namespaces": ["bnk-egress-demo"],
                "appPodInterface": "eth0",
            },
        })
        result = _build_egress(eg)
        assert result["capturedNamespaces"] == ["bnk-egress-demo"]
        assert result["vxlan"] is None

    def test_egress_with_null_namespaces_returns_empty_list(self):
        eg = _resource("egress-null-ns", namespace="f5-cne-system", spec={
            "pseudoCNIConfig": {
                "namespaces": None,
            },
        })
        result = _build_egress(eg)
        assert result["capturedNamespaces"] == []

    def test_egress_with_null_or_empty_vxlan_returns_none(self):
        eg_null = _resource("egress-null-vxlan", namespace="f5-cne-system", spec={
            "pseudoCNIConfig": {
                "namespaces": ["demo"],
                "vxlan": None,
            },
        })
        assert _build_egress(eg_null)["vxlan"] is None

        eg_empty = _resource("egress-empty-vxlan", namespace="f5-cne-system", spec={
            "pseudoCNIConfig": {
                "namespaces": ["demo"],
                "vxlan": {},
            },
        })
        assert _build_egress(eg_empty)["vxlan"] is None


# ---------------------------------------------------------------------------
# _build_data_plane
# ---------------------------------------------------------------------------


class TestBuildDataPlane:
    def test_empty_resources(self):
        dp = _build_data_plane(_empty_resources())
        assert dp["vlans"] == []
        assert dp["cneInstances"] == []
        assert dp["staticRoutes"] == []

    def test_builds_all_sections(self):
        resources = _empty_resources()
        resources["f5spkvlan"] = [_resource("v1")]
        resources["cneinstance"] = [_resource("c1", spec={})]
        resources["f5spkstaticroute"] = [_resource("sr-1", spec={"destination": "10.0.0.0/8", "gateway": "10.1.1.1"})]
        resources["f5spksnatpool"] = [_resource("sp-1", spec={"addresses": ["10.2.0.1"]})]
        resources["f5spkegress"] = [_resource("eg-1", spec={"snatType": "SRC_TRANS_AUTOMAP"})]
        resources["f5bigloghslpub"] = [_resource("hsl-1", spec={"pool": {"name": "pool-1"}, "protocol": "udp"})]
        resources["f5biglogprofile"] = [_resource("lp-1", spec={"publishers": ["pub-1"]})]

        dp = _build_data_plane(resources)
        assert len(dp["vlans"]) == 1
        assert len(dp["cneInstances"]) == 1
        assert len(dp["staticRoutes"]) == 1
        assert dp["staticRoutes"][0]["destination"] == "10.0.0.0/8"
        assert len(dp["snatPools"]) == 1
        assert len(dp["egresses"]) == 1
        assert len(dp["logging"]["hslPublishers"]) == 1
        assert len(dp["logging"]["logProfiles"]) == 1
