"""F5 BNK discovery follows an InferencePool backend (BNK 2.4 F5 Endpoint Picker)."""

from services.proxy_discovery_service import _f5_bnk_routing_info, _find_routes_to_backend


def _route(backend: dict) -> dict:
    return {
        "metadata": {"name": "r", "namespace": "ai"},
        "spec": {"parentRefs": [{"name": "gw", "namespace": "ai"}], "rules": [{"backendRefs": [backend]}]},
    }


def test_route_to_a_pool_selecting_the_target_matches():
    pool_route = _route({"group": "inference.networking.k8s.io", "kind": "InferencePool", "name": "vllm-pool"})
    assert _find_routes_to_backend([pool_route], "gw", "ai", "vllm", "ai", frozenset({"vllm-pool"})) == [pool_route]
    # A pool of other pods, or a Service merely named like the pool, does not match.
    assert _find_routes_to_backend([pool_route], "gw", "ai", "vllm", "ai", frozenset()) == []
    assert _find_routes_to_backend([_route({"name": "vllm-pool"})], "gw", "ai", "vllm", "ai", frozenset({"vllm-pool"})) == []


def test_routing_info_prefers_the_inference_pool_route():
    pool_path = {"hostnames": ["ai.local"], "pool": "vllm-pool"}
    details = [{"name": "gw", "namespace": "ai", "route_paths": [pool_path]}]
    info = _f5_bnk_routing_info(details, "gw", "ai", {"vllm-pool": "vllm-pool-epp"})
    assert info == {"epp": "F5 Endpoint Picker (F5EPP vllm-pool-epp, InferencePool vllm-pool)", "host_header": "ai.local"}
    # A Service route on the same Gateway does not take over.
    details[0]["route_paths"].insert(0, {"hostnames": ["ai-lb.local"], "pool": None})
    assert _f5_bnk_routing_info(details, "gw", "ai", {"vllm-pool": "vllm-pool-epp"}) == info
    # Only a Service route: plain TMM load balancing.
    details[0]["route_paths"] = [{"hostnames": ["ai-lb.local"], "pool": None}]
    info = _f5_bnk_routing_info(details, "gw", "ai", {"vllm-pool": "vllm-pool-epp"})
    assert info == {"epp": "none (TMM load-balances the Service)", "host_header": "ai-lb.local"}
