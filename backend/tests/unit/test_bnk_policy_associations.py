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
    return {k: [] for k in ["bnksecpolicy", "gateway", "f5bigfwpolicy"]}


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
