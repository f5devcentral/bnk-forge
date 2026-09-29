"""fetch_all_bnk_data: overall deadline, partial-result caching, sweep seeding."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import services.bnk.fetch as fetch_mod
from services.bnk.fetch import _cached_discover_f5_pods, fetch_all_bnk_data


def _k8s_service(discovered_namespaces=None):
    svc = MagicMock()
    svc.get_cluster.return_value = MagicMock(discovered_namespaces=discovered_namespaces)
    svc._fetch_from_k8s.return_value = []
    return svc


def _slow_pods(*_a, **_k):
    time.sleep(1.0)
    return [{"name": "tmm"}], []


class TestFetchDeadline:
    def test_slow_call_returns_partial_and_is_cached_briefly(self):
        cache = MagicMock()
        cache.get.return_value = None
        with (
            patch.object(fetch_mod, "cache", cache),
            patch.object(fetch_mod, "_BNK_FETCH_DEADLINE_SECONDS", 0.2),
            patch.object(fetch_mod, "resolve_resource_type", return_value=MagicMock()),
            patch.object(fetch_mod, "_cached_discover_f5_pods", side_effect=_slow_pods),
            patch.object(fetch_mod, "classify_f5_pods", return_value={}),
            patch("kubernetes.client.BatchV1Api") as batch,
        ):
            batch.return_value.list_namespaced_job.return_value.items = []
            start = time.monotonic()
            result = fetch_all_bnk_data(_k8s_service(), 1)
            elapsed = time.monotonic() - start

        assert elapsed < 0.9
        assert result["partial"] is True
        assert result["pods"] == {"tenant": [], "utils": []}
        assert cache.set.call_args.kwargs["ttl_seconds"] == fetch_mod._BNK_PARTIAL_CACHE_TTL

    def test_complete_fetch_uses_the_normal_ttl(self):
        cache = MagicMock()
        cache.get.return_value = None
        with (
            patch.object(fetch_mod, "cache", cache),
            patch.object(fetch_mod, "resolve_resource_type", return_value=MagicMock()),
            patch.object(fetch_mod, "_cached_discover_f5_pods", return_value=([], [])),
            patch.object(fetch_mod, "classify_f5_pods", return_value={}),
            patch("kubernetes.client.BatchV1Api") as batch,
        ):
            batch.return_value.list_namespaced_job.return_value.items = []
            result = fetch_all_bnk_data(_k8s_service(), 1)

        assert "partial" not in result
        assert cache.set.call_args.kwargs["ttl_seconds"] == fetch_mod._BNK_DATA_CACHE_TTL


class TestPodDiscoverySweep:
    def _run(self, extra_namespaces):
        with (
            patch("core.cache.cache") as cache,
            patch.object(fetch_mod, "discover_f5_pods", return_value=([], [])) as discover,
        ):
            cache.get.return_value = None
            _cached_discover_f5_pods(1, MagicMock(), extra_namespaces)
        return discover.call_args.kwargs["include_sweep"]

    def test_never_scanned_cluster_sweeps(self):
        assert self._run([]) is True

    def test_persisted_namespaces_skip_the_sweep(self):
        assert self._run(["custom-bnk"]) is False
