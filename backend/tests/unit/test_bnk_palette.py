"""
Unit tests for services.bnk.palette — lightweight resource summaries.
"""

from services.bnk.palette import _summarize_irule, extract_palette_data


def _resource(name: str, namespace: str = "f5-bnk", **kw) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": kw.get("spec", {}),
    }


def _empty_resources() -> dict:
    return {k: [] for k in [
        "gatewayclass", "service", "f5bigcneaddresslist",
        "f5bigcneportlist", "f5bigcneirule",
    ]}


class TestExtractPaletteData:
    def test_empty_cluster(self):
        result = extract_palette_data({"resources": _empty_resources()})
        assert result["gatewayClasses"] == []
        assert result["services"] == []
        assert result["addressLists"] == []
        assert result["portLists"] == []
        assert result["iRules"] == []

    def test_gateway_classes(self):
        resources = _empty_resources()
        resources["gatewayclass"] = [
            _resource("f5-bnk", spec={"controllerName": "f5.io/gateway-controller"}),
        ]
        result = extract_palette_data({"resources": resources})
        assert len(result["gatewayClasses"]) == 1
        assert result["gatewayClasses"][0]["name"] == "f5-bnk"
        assert result["gatewayClasses"][0]["controllerName"] == "f5.io/gateway-controller"

    def test_services_with_null_ports(self):
        """Services with null ports (ExternalName) don't crash."""
        resources = _empty_resources()
        svc = _resource("ext-svc", spec={"ports": None})
        resources["service"] = [svc]
        result = extract_palette_data({"resources": resources})
        assert result["services"][0]["ports"] == []

    def test_services_sorted_by_namespace_name(self):
        resources = _empty_resources()
        resources["service"] = [
            _resource("svc-b", namespace="ns-2"),
            _resource("svc-a", namespace="ns-1"),
            _resource("svc-a", namespace="ns-2"),
        ]
        result = extract_palette_data({"resources": resources})
        names = [(s["namespace"], s["name"]) for s in result["services"]]
        assert names == [("ns-1", "svc-a"), ("ns-2", "svc-a"), ("ns-2", "svc-b")]

    def test_address_lists(self):
        resources = _empty_resources()
        resources["f5bigcneaddresslist"] = [
            _resource("allow-list", spec={"addresses": ["10.0.0.0/8"]}),
        ]
        result = extract_palette_data({"resources": resources})
        assert len(result["addressLists"]) == 1
        assert result["addressLists"][0]["addresses"] == ["10.0.0.0/8"]

    def test_port_lists_stringified(self):
        resources = _empty_resources()
        resources["f5bigcneportlist"] = [
            _resource("web-ports", spec={"ports": [80, 443, 8080]}),
        ]
        result = extract_palette_data({"resources": resources})
        assert result["portLists"][0]["ports"] == ["80", "443", "8080"]

    def test_irules_with_event_handlers(self):
        resources = _empty_resources()
        irule_code = (
            "when HTTP_REQUEST {\n"
            "  log local0. \"request\"\n"
            "}\n"
            "when HTTP_RESPONSE {\n"
            "  log local0. \"response\"\n"
            "}"
        )
        resources["f5bigcneirule"] = [_resource("ir-1", spec={"iRule": irule_code})]
        result = extract_palette_data({"resources": resources})
        ir = result["iRules"][0]
        assert ir["name"] == "ir-1"
        assert ir["lineCount"] == 6
        assert set(ir["eventHandlers"]) == {"HTTP_REQUEST", "HTTP_RESPONSE"}


class TestSummarizeIrule:
    def test_empty_irule(self):
        ir = _resource("ir-empty", spec={"iRule": ""})
        result = _summarize_irule(ir)
        assert result["lineCount"] == 0
        assert result["eventHandlers"] == []

    def test_missing_irule_spec(self):
        ir = _resource("ir-none", spec={})
        result = _summarize_irule(ir)
        assert result["lineCount"] == 0
