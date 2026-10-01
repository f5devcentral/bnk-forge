"""Read caches added for page performance stay correct after writes and on explicit refresh."""

from unittest.mock import MagicMock, patch

from services import qkview_service
from services.topology_builder_service import build_namespace_topology


class _DictCache:
    """Stand-in for core.cache with JSON-like round-tripping of tuples to lists."""

    def __init__(self):
        self.store: dict = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl_seconds=300):
        self.store[key] = list(value) if isinstance(value, tuple) else value
        return True

    def delete(self, key):
        self.store.pop(key, None)
        return True


def _api_client(host="https://10.0.0.1:6443"):
    api_client = MagicMock()
    api_client.configuration.host = host
    return api_client


class TestLicenseStatusInvalidation:
    def test_drops_license_and_cwc_status(self):
        fake = _DictCache()
        fake.store = {"license:status:7": {"a": 1}, "cwc:status:7": {"b": 2}, "cwc:status:8": {"c": 3}}
        with patch.object(qkview_service, "cache", fake):
            qkview_service.invalidate_license_status(7)
        assert fake.store == {"cwc:status:8": {"c": 3}}


class TestCwcClientMetaCache:
    def _request(self, fake, api_client, token="tok-1"):
        with patch.object(qkview_service, "cache", fake), \
             patch.object(qkview_service, "_detect_cwc_namespace", return_value="f5-utils") as detect, \
             patch.object(qkview_service, "_find_cert_secret", return_value={"name": "certs"}), \
             patch.object(qkview_service, "_get_cwc_rest_port", return_value=8443), \
             patch.object(qkview_service, "_get_admin_token", return_value=token) as get_token, \
             patch.object(qkview_service, "_get_or_create_client_pod", side_effect=RuntimeError("stop")):
            try:
                qkview_service._cwc_request(api_client, "GET", "/status")
            except RuntimeError:
                pass
        return detect.call_count, get_token.call_count

    def test_second_request_reuses_metadata_and_token(self):
        qkview_service._cwc_token_cache.clear()
        fake, api_client = _DictCache(), _api_client()
        assert self._request(fake, api_client) == (1, 1)
        assert self._request(fake, api_client) == (0, 0)

    def test_admin_token_is_not_written_to_the_shared_cache(self):
        qkview_service._cwc_token_cache.clear()
        fake = _DictCache()
        self._request(fake, _api_client(), token="secret-token")
        assert "secret-token" not in repr(fake.store)
        assert fake.store["cwc:client_meta:https://10.0.0.1:6443"] == ["f5-utils", {"name": "certs"}, 8443]

    def test_client_pod_cleanup_drops_cached_metadata(self):
        qkview_service._cwc_token_cache.clear()
        fake, api_client = _DictCache(), _api_client()
        self._request(fake, api_client)
        api_client_pods = MagicMock()
        api_client_pods.list_namespaced_pod.return_value.items = []
        with patch.object(qkview_service, "cache", fake), \
             patch.object(qkview_service.k8s_client, "CoreV1Api", return_value=api_client_pods):
            qkview_service._cleanup_all_client_pods(api_client, "f5-utils")
        assert self._request(fake, api_client) == (1, 1)


class TestTopologyForce:
    def _build(self, fake, k8s, force=False):
        with patch("services.topology_builder_service.cache", fake):
            return build_namespace_topology(k8s, 3, "default", force=force)

    def test_cached_graph_is_served_and_force_rebuilds(self):
        fake, k8s = _DictCache(), MagicMock()
        k8s.get_resources.return_value = []
        self._build(fake, k8s)
        calls = k8s.get_resources.call_count
        self._build(fake, k8s)
        assert k8s.get_resources.call_count == calls
        self._build(fake, k8s, force=True)
        assert k8s.get_resources.call_count == 2 * calls
