"""
Unit tests for services.bnk.policy_associations — security policy mapping.
"""

from services.bnk.policy_associations import _build_association, analyze_policy_associations


def _resource(name: str, namespace: str = "f5-bnk", **kw) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": kw.get("spec", {}),
        "status": kw.get("status", {}),
    }


def _empty_resources() -> dict:
    return {
        k: []
        for k in [
            "bnksecpolicy", "gateway", "f5bigfwpolicy", "f5spkegress",
            "f5bigcneaddresslist", "f5bigcneportlist",
        ]
    }


class TestAnalyzePolicyAssociations:
    def test_empty_cluster(self):
        result = analyze_policy_associations({"resources": _empty_resources()})
        assert result["associations"] == []
        assert result["count"] == 0

    def test_sec_policy_with_gateway_and_firewall(self):
        resources = _empty_resources()
        resources["gateway"] = [_resource("gw-prod", spec={
            "listeners": [{"name": "http", "port": 80, "protocol": "HTTP"}],
        }, status={
            "addresses": [{"value": "10.0.0.1"}],
        })]
        resources["f5bigfwpolicy"] = [_resource("fw-deny", spec={
            "rule": [
                {"name": "deny-ssh", "action": "drop", "ipProtocol": "tcp",
                 "source": {}, "destination": {"portLists": ["ssh-ports"]}, "logging": True},
            ],
        })]
        resources["bnksecpolicy"] = [_resource("sec-pol", spec={
            "targetRefs": [{"name": "gw-prod", "kind": "Gateway", "sectionName": "http"}],
            "extensionRefs": [{"kind": "F5BigFwPolicy", "name": "fw-deny"}],
        })]

        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 1
        a = result["associations"][0]
        assert a["kind"] == "gateway"
        assert a["bnk_policy_name"] == "sec-pol"
        assert a["gateway_name"] == "gw-prod"
        assert a["listener_name"] == "http"
        assert a["firewall_policy_name"] == "fw-deny"
        assert a["gateway_ip"] == "10.0.0.1"
        assert a["port"] == 80
        assert a["protocol"] == "HTTP"
        assert a["rules_count"] == 1
        assert a["rules"][0]["action"] == "drop"
        assert a["rules"][0]["logging"] is True
        # Referenced port list has no matching resource — name kept, ports empty
        assert a["rules"][0]["destination"]["ports"] == []
        assert a["rules"][0]["destination"]["portLists"] == ["ssh-ports"]
        assert a["rules"][0]["destination"]["addresses"] == []
        assert a["rules"][0]["destination"]["addressLists"] == []

    def test_non_gateway_target_skipped(self):
        resources = _empty_resources()
        resources["bnksecpolicy"] = [_resource("sp", spec={
            "targetRefs": [{"name": "some-svc", "kind": "Service"}],
            "extensionRefs": [],
        })]
        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 0

    def test_non_firewall_extension_skipped(self):
        resources = _empty_resources()
        resources["bnksecpolicy"] = [_resource("sp", spec={
            "targetRefs": [{"name": "gw", "kind": "Gateway"}],
            "extensionRefs": [{"kind": "F5BigCneIrule", "name": "ir-1"}],
        })]
        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 0

    def test_missing_gateway_no_ip_or_port(self):
        """If the gateway doesn't exist, association omits gateway_ip/port/protocol."""
        resources = _empty_resources()
        resources["bnksecpolicy"] = [_resource("sp", spec={
            "targetRefs": [{"name": "gw-missing", "kind": "Gateway", "sectionName": "http"}],
            "extensionRefs": [{"kind": "F5BigFwPolicy", "name": "fw-1"}],
        })]
        resources["f5bigfwpolicy"] = [_resource("fw-1", spec={"rule": []})]

        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 1
        a = result["associations"][0]
        assert "gateway_ip" not in a
        assert "port" not in a

    def test_missing_firewall_policy_no_rules(self):
        """If the firewall policy doesn't exist, association omits rules."""
        resources = _empty_resources()
        resources["gateway"] = [_resource("gw-prod")]
        resources["bnksecpolicy"] = [_resource("sp", spec={
            "targetRefs": [{"name": "gw-prod", "kind": "Gateway"}],
            "extensionRefs": [{"kind": "F5BigFwPolicy", "name": "fw-missing"}],
        })]

        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 1
        a = result["associations"][0]
        assert "rules" not in a
        assert "rules_count" not in a


class TestEgressAssociations:
    def test_egress_with_firewall_policy_produces_association(self):
        resources = _empty_resources()
        resources["f5bigfwpolicy"] = [_resource("egress-demo-fw", spec={
            "rule": [
                {"name": "deny-egress", "action": "drop", "ipProtocol": "tcp",
                 "source": {}, "destination": {}, "logging": True},
            ],
        })]
        resources["f5spkegress"] = [_resource("bnk-egress-demo", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
            "firewallEnforcedPolicy": "egress-demo-fw",
            "pseudoCNIConfig": {"namespaces": ["bnk-egress-demo"]},
        })]

        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 1
        a = result["associations"][0]
        assert a["kind"] == "egress"
        assert a["egress_name"] == "bnk-egress-demo"
        assert a["namespace"] == "f5-bnk"
        assert a["captured_namespaces"] == ["bnk-egress-demo"]
        assert a["snat_type"] == "SRC_TRANS_AUTOMAP"
        assert a["firewall_policy_name"] == "egress-demo-fw"
        assert a["rules_count"] == 1
        assert a["rules"][0]["action"] == "drop"
        assert a["rules"][0]["logging"] is True
        assert a["rules"][0]["source"]["addresses"] == []
        assert a["rules"][0]["destination"]["addresses"] == []

    def test_egress_without_firewall_policy_produces_no_association(self):
        resources = _empty_resources()
        resources["f5spkegress"] = [_resource("bnk-egress-demo", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
        })]

        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 0

    def test_egress_with_missing_firewall_policy_no_rules(self):
        resources = _empty_resources()
        resources["f5spkegress"] = [_resource("bnk-egress-demo", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
            "firewallEnforcedPolicy": "missing-fw",
        })]

        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 1
        a = result["associations"][0]
        assert "rules" not in a
        assert "rules_count" not in a


class TestResolvedListReferences:
    """Rules resolve addressLists/portLists into inline addresses/ports (both paths)."""

    def test_egress_rule_resolves_address_list_to_addresses(self):
        resources = _empty_resources()
        resources["f5bigcneaddresslist"] = [_resource("egress-demo-blocked", spec={
            "addresses": ["1.1.1.1/32"],
        })]
        resources["f5bigfwpolicy"] = [_resource("egress-demo-fw", spec={
            "rule": [
                {"name": "block-test-target", "action": "drop", "ipProtocol": "tcp",
                 "source": {}, "destination": {"addressLists": ["egress-demo-blocked"]},
                 "logging": True},
            ],
        })]
        resources["f5spkegress"] = [_resource("bnk-egress-demo", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
            "firewallEnforcedPolicy": "egress-demo-fw",
        })]

        result = analyze_policy_associations({"resources": resources})
        rule = result["associations"][0]["rules"][0]
        assert rule["destination"]["addresses"] == ["1.1.1.1/32"]
        assert rule["destination"]["addressLists"] == ["egress-demo-blocked"]
        assert rule["destination"]["ports"] == []
        assert rule["destination"]["portLists"] == []

    def test_gateway_rule_direct_addresses_unaffected(self):
        resources = _empty_resources()
        resources["gateway"] = [_resource("gw-prod")]
        resources["f5bigfwpolicy"] = [_resource("fw-1", spec={
            "rule": [
                {"name": "allow-direct", "action": "accept", "ipProtocol": "tcp",
                 "source": {"addresses": ["10.0.0.0/24"]}, "destination": {}, "logging": False},
            ],
        })]
        resources["bnksecpolicy"] = [_resource("sec-pol", spec={
            "targetRefs": [{"name": "gw-prod", "kind": "Gateway"}],
            "extensionRefs": [{"kind": "F5BigFwPolicy", "name": "fw-1"}],
        })]

        result = analyze_policy_associations({"resources": resources})
        rule = result["associations"][0]["rules"][0]
        assert rule["source"]["addresses"] == ["10.0.0.0/24"]
        assert rule["source"]["addressLists"] == []

    def test_referenced_but_missing_list_keeps_name_with_empty_addresses(self):
        resources = _empty_resources()
        resources["f5bigfwpolicy"] = [_resource("egress-demo-fw", spec={
            "rule": [
                {"name": "block-unknown", "action": "drop", "ipProtocol": "tcp",
                 "source": {}, "destination": {"addressLists": ["does-not-exist"]},
                 "logging": False},
            ],
        })]
        resources["f5spkegress"] = [_resource("bnk-egress-demo", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
            "firewallEnforcedPolicy": "egress-demo-fw",
        })]

        result = analyze_policy_associations({"resources": resources})
        rule = result["associations"][0]["rules"][0]
        assert rule["destination"]["addresses"] == []
        assert rule["destination"]["addressLists"] == ["does-not-exist"]

    def test_rule_with_null_source_and_destination_handles_gracefully(self):
        resources = _empty_resources()
        resources["f5bigfwpolicy"] = [_resource("fw-null", spec={
            "rule": [
                {"name": "null-rule", "action": "accept", "ipProtocol": "tcp",
                 "source": None, "destination": None, "logging": False},
            ],
        })]
        resources["f5spkegress"] = [_resource("bnk-egress", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
            "firewallEnforcedPolicy": "fw-null",
        })]

        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 1
        rule = result["associations"][0]["rules"][0]
        assert rule["source"] == {"addresses": [], "ports": [], "addressLists": [], "portLists": []}
        assert rule["destination"] == {"addresses": [], "ports": [], "addressLists": [], "portLists": []}

    def test_rule_direction_with_null_fields_handles_gracefully(self):
        resources = _empty_resources()
        resources["f5bigfwpolicy"] = [_resource("fw-null-fields", spec={
            "rule": [
                {"name": "null-fields-rule", "action": "accept", "ipProtocol": "tcp",
                 "source": {"addresses": None, "addressLists": None, "ports": None, "portLists": None},
                 "destination": {}, "logging": False},
            ],
        })]
        resources["f5spkegress"] = [_resource("bnk-egress", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
            "firewallEnforcedPolicy": "fw-null-fields",
        })]

        result = analyze_policy_associations({"resources": resources})
        assert result["count"] == 1
        rule = result["associations"][0]["rules"][0]
        assert rule["source"] == {"addresses": [], "ports": [], "addressLists": [], "portLists": []}

    def test_port_type_normalization_deduplicates_int_and_string_ports(self):
        resources = _empty_resources()
        resources["f5bigcneportlist"] = [_resource("http-ports", spec={
            "ports": [80, 8080],
        })]
        resources["f5bigfwpolicy"] = [_resource("fw-ports", spec={
            "rule": [
                {"name": "mix-ports", "action": "accept", "ipProtocol": "tcp",
                 "source": {}, "destination": {"ports": ["80"], "portLists": ["http-ports"]},
                 "logging": False},
            ],
        })]
        resources["f5spkegress"] = [_resource("bnk-egress", spec={
            "snatType": "SRC_TRANS_AUTOMAP",
            "firewallEnforcedPolicy": "fw-ports",
        })]

        result = analyze_policy_associations({"resources": resources})
        rule = result["associations"][0]["rules"][0]
        assert rule["destination"]["ports"] == ["80", "8080"]


class TestBuildAssociation:
    def test_full_association(self):
        bnk = _resource("sec-pol")
        gateway = _resource("gw-prod", spec={
            "listeners": [{"name": "http", "port": 80, "protocol": "HTTP"}],
        }, status={"addresses": [{"value": "1.2.3.4"}]})
        policy = _resource("fw-1", spec={
            "rule": [{"name": "r1", "action": "accept", "ipProtocol": "any",
                       "source": {}, "destination": {}, "logging": False}],
        })
        a = _build_association(bnk, "f5-bnk", "gw-prod", "http", "fw-1", gateway, policy)
        assert a["gateway_ip"] == "1.2.3.4"
        assert a["rules_count"] == 1

    def test_no_gateway_no_policy(self):
        bnk = _resource("sp")
        a = _build_association(bnk, "ns", "gw", "http", "fw", None, None)
        assert a["bnk_policy_name"] == "sp"
        assert "gateway_ip" not in a
        assert "rules" not in a
