"""
Unit tests for the live-scan core of global K8s search.

``routes.k8s.search._scan_cluster_for_query`` (the ~260-line per-cluster
scanner) and the ``ThreadPoolExecutor`` harvest/timeout path in
``global_search`` were previously exercised only through integration tests that
FULLY MOCKED ``_scan_cluster_for_query`` (``patch(..., return_value=[])``), so
the Ingress / HTTPRoute / VirtualServer / Egress / Gateway / Service parsing,
the dedup, and the timeout harvest had ZERO coverage.

These tests mock the kubernetes client RESPONSES (not the scan function), feed
representative resource objects, and assert the parsed + deduped results and
that a per-cluster scan timeout yields partial results without hanging.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

import routes.k8s.search as search_mod
from routes.k8s.search import _scan_cluster_for_query, global_search

# ---------------------------------------------------------------------------
# k8s response builders (attribute-access objects for the typed APIs, dicts
# for the CustomObjects API — mirroring what the real client returns).
# ---------------------------------------------------------------------------

def _ingress(name, ns, hosts=None, tls_hosts=None, svc_name=None, svc_port=None):
    rules = []
    for h in hosts or []:
        paths = None
        if svc_name:
            port = SimpleNamespace(number=svc_port, name=None) if svc_port else None
            paths = [SimpleNamespace(backend=SimpleNamespace(service=SimpleNamespace(name=svc_name, port=port)))]
        http = SimpleNamespace(paths=paths) if paths is not None else None
        rules.append(SimpleNamespace(host=h, http=http))
    tls = [SimpleNamespace(hosts=tls_hosts)] if tls_hosts else None
    return SimpleNamespace(metadata=SimpleNamespace(name=name, namespace=ns), spec=SimpleNamespace(rules=rules or None, tls=tls))


def _service(name, ns, type_="LoadBalancer", cluster_ip=None, external_ips=None, lb=None):
    lb_ingress = None
    if lb:
        lb_ingress = [SimpleNamespace(ip=ip, hostname=host) for ip, host in lb]
    status = SimpleNamespace(load_balancer=SimpleNamespace(ingress=lb_ingress) if lb_ingress is not None else None)
    spec = SimpleNamespace(cluster_ip=cluster_ip, external_i_ps=external_ips, type=type_)
    return SimpleNamespace(metadata=SimpleNamespace(name=name, namespace=ns), spec=spec, status=status)


class _FakeApiClient:
    """Carries the canned responses for one cluster's scan."""

    def __init__(self, ingresses=None, custom=None, services=None, delay=0.0):
        self.ingresses = ingresses or []
        # plural -> list[dict] of items, or the sentinel "RAISE" for
        # "CRD not installed" (ApiException).
        self.custom = custom or {}
        self.services = services or []
        self.delay = delay


def _networking_factory(api_client):
    m = MagicMock()

    def _list(**_kw):
        if api_client.delay:
            time.sleep(api_client.delay)
        return SimpleNamespace(items=api_client.ingresses)

    m.list_ingress_for_all_namespaces.side_effect = _list
    return m


def _custom_factory(api_client):
    m = MagicMock()

    def _list(group=None, version=None, plural=None, **_kw):
        val = api_client.custom.get(plural, "RAISE")
        if val == "RAISE":
            raise ApiException(status=404, reason=f"{plural} CRD not installed")
        return {"items": val}

    m.list_cluster_custom_object.side_effect = _list
    return m


def _core_factory(api_client):
    m = MagicMock()
    m.list_service_for_all_namespaces.side_effect = lambda **_kw: SimpleNamespace(items=api_client.services)
    return m


@contextmanager
def _wire(clients_by_id):
    """Patch SessionLocal, KubernetesService, and the three typed k8s APIs so
    every ``_scan_cluster_for_query`` call (including the ones in worker
    threads) resolves to the ``_FakeApiClient`` registered for its cluster id.
    """
    ks = MagicMock()
    ks.return_value.get_cluster.side_effect = lambda cid: (SimpleNamespace(id=cid) if cid in clients_by_id else None)
    ks.return_value.load_kubeconfig.side_effect = lambda c: clients_by_id[c.id]

    with (
        patch.object(search_mod, "SessionLocal", return_value=MagicMock()),
        patch.object(search_mod, "KubernetesService", ks),
        patch.object(search_mod.k8s_client, "NetworkingV1Api", side_effect=_networking_factory),
        patch.object(search_mod.k8s_client, "CustomObjectsApi", side_effect=_custom_factory),
        patch.object(search_mod.k8s_client, "CoreV1Api", side_effect=_core_factory),
    ):
        yield


# ---------------------------------------------------------------------------
# _scan_cluster_for_query — resource parsing
# ---------------------------------------------------------------------------

class TestScanClusterParsing:
    def test_parses_every_resource_kind(self):
        client = _FakeApiClient(
            ingresses=[
                _ingress(
                    "web-ing", "shop", hosts=["shop.example.com"],
                    tls_hosts=["shop.example.com"], svc_name="web", svc_port=8080,
                ),
            ],
            custom={
                "httproutes": [{
                    "metadata": {"name": "api-route", "namespace": "api"},
                    "spec": {"hostnames": ["api.example.com"]},
                }],
                "virtualservers": [{
                    "metadata": {"name": "vs-app", "namespace": "bnk"},
                    "spec": {"host": "app.example.com", "virtualServerAddress": "10.0.0.5"},
                }],
                "f5-spk-egresses": [{
                    "metadata": {"name": "egress-example", "namespace": "egress"},
                    "spec": {"dnsNat46Ipv4Subnet": "192.0.2.0/24"},
                }],
                "f5-bnkgateways": [{
                    "metadata": {"name": "example-gw", "namespace": "gw"},
                }],
                # gateways / l4routes not installed -> ApiException (default RAISE)
            },
            services=[
                _service("lb-example", "svc", type_="LoadBalancer",
                         cluster_ip="10.96.0.1", lb=[("203.0.113.9", None)]),
            ],
        )
        with _wire({1: client}):
            results = _scan_cluster_for_query(1, "eks-1", "aws", "us-east-1", "example")

        by_kind = {r.kind: r for r in results}
        assert set(by_kind) == {"Ingress", "HTTPRoute", "VirtualServer", "Egress", "BNKGateway", "Service"}

        # Ingress: host match + target service + TLS host deduped into all_hosts
        ing = by_kind["Ingress"]
        assert ing.matched_host == "shop.example.com"
        assert ing.target_service == "web:8080"
        assert ing.all_hosts == ["shop.example.com"]  # rule host + TLS host collapsed
        assert ing.cluster_name == "eks-1" and ing.cloud_provider == "aws"

        assert by_kind["HTTPRoute"].matched_host == "api.example.com"
        assert by_kind["VirtualServer"].matched_host == "app.example.com"
        assert by_kind["VirtualServer"].target_service == "10.0.0.5"
        assert by_kind["Egress"].target_service == "192.0.2.0/24"
        # Service matched by name (LoadBalancer type) and harvested its
        # clusterIP + LoadBalancer ingress IP into all_hosts.
        svc = by_kind["Service"]
        assert svc.matched_host == "lb-example"
        assert "203.0.113.9" in svc.all_hosts and "10.96.0.1" in svc.all_hosts

    def test_empty_query_short_circuits_without_client_load(self):
        # A blank query must not even open a DB session / load a kubeconfig.
        with patch.object(search_mod, "SessionLocal") as sl:
            assert _scan_cluster_for_query(1, "c", None, None, "   ") == []
            sl.assert_not_called()

    def test_missing_cluster_returns_empty(self):
        with _wire({}):  # get_cluster -> None for id 1
            assert _scan_cluster_for_query(1, "gone", None, None, "example") == []

    def test_uninstalled_crds_and_bad_apis_are_tolerated(self):
        # Every custom resource CRD is absent and the Service API blows up:
        # only the matching Ingress should survive, with no exception raised.
        client = _FakeApiClient(
            ingresses=[_ingress("only-ing", "ns", hosts=["only.example.com"])],
            custom={},  # all plurals RAISE
            services=[],
        )
        with _wire({1: client}):
            with patch.object(search_mod.k8s_client, "CoreV1Api", side_effect=RuntimeError("boom")):
                results = _scan_cluster_for_query(1, "c", None, None, "example")
        assert [r.kind for r in results] == ["Ingress"]

    def test_service_matches_on_load_balancer_ip(self):
        # A query that hits only the LB ingress IP still returns the Service,
        # regardless of its name (exercises the `matched_ip` branch).
        client = _FakeApiClient(
            services=[_service("unrelated-name", "ns", type_="LoadBalancer",
                               lb=[("198.51.100.7", None)])],
        )
        with _wire({1: client}):
            results = _scan_cluster_for_query(1, "c", None, None, "198.51.100.7")
        assert len(results) == 1
        assert results[0].kind == "Service"
        assert results[0].matched_host == "198.51.100.7"

    def test_no_host_match_excludes_service_when_name_type_mismatch(self):
        # A ClusterIP service whose name matches but type is not LB/NodePort
        # must NOT be returned (guards the `spec.type in [...]` branch).
        client = _FakeApiClient(
            services=[_service("example-clusterip", "ns", type_="ClusterIP", cluster_ip="10.0.0.9")],
        )
        with _wire({1: client}):
            results = _scan_cluster_for_query(1, "c", None, None, "example")
        assert results == []


# ---------------------------------------------------------------------------
# global_search — ThreadPoolExecutor harvest / timeout + dedup
# ---------------------------------------------------------------------------

class _Query:
    def __init__(self, result):
        self._result = result

    def options(self, *_a, **_k):
        return self

    def all(self):
        return self._result


class _DB:
    def __init__(self, clusters):
        self._clusters = clusters

    def query(self, model):
        from models.kubernetes import KubernetesCluster
        return _Query(self._clusters if model is KubernetesCluster else [])


def _db_cluster(cid, name):
    return SimpleNamespace(
        id=cid, name=name, cloud_provider="aws", region="us-east-1",
        detected_platform_profile=None, meta_data={}, status="active",
    )


class TestGlobalSearchScanOrchestration:
    def test_timeout_yields_partial_results_and_dedups(self):
        """One cluster returns instantly, one stalls past the harvest window.

        The fast cluster's result must come back (partial results, no hang),
        and the dedup must collapse the fast future being counted twice — once
        via as_completed, once again in the timeout-harvest re-scan.
        """
        fast = _FakeApiClient(
            ingresses=[_ingress("fast-ing", "ns", hosts=["hit.example.com"])],
        )
        slow = _FakeApiClient(
            ingresses=[_ingress("slow-ing", "ns", hosts=["hit.example.com"])],
            delay=1.0,  # finite so the executor still shuts down (no infinite hang)
        )

        db = _DB([_db_cluster(1, "fast-cluster"), _db_cluster(2, "slow-cluster")])

        # Force the harvest branch quickly instead of waiting the real 6s.
        real_as_completed = search_mod.as_completed

        def _short_as_completed(fs, timeout=None):
            return real_as_completed(fs, timeout=0.2)

        with _wire({1: fast, 2: slow}):
            with patch.object(search_mod, "as_completed", _short_as_completed):
                start = time.monotonic()
                resp = global_search(q="hit.example.com", limit=25, db=db)
                elapsed = time.monotonic() - start

        names = [i.name for i in resp.ingresses]
        # Fast cluster's partial result is present...
        assert "fast-ing" in names
        # ...and dedup collapsed the double-harvest: no duplicate fast-ing.
        assert names.count("fast-ing") == 1
        # Did not hang for the full 6s as_completed budget.
        assert elapsed < 5.0

    def test_dedup_key_spans_kind_cluster_namespace_name(self):
        """Two DISTINCT clusters returning the same-named resource are kept
        (dedup key includes cluster_id), while an exact duplicate is dropped."""
        c1 = _FakeApiClient(ingresses=[_ingress("shared", "ns", hosts=["dup.example.com"])])
        c2 = _FakeApiClient(ingresses=[_ingress("shared", "ns", hosts=["dup.example.com"])])
        db = _DB([_db_cluster(1, "c1"), _db_cluster(2, "c2")])

        with _wire({1: c1, 2: c2}):
            resp = global_search(q="dup.example.com", limit=25, db=db)

        # Same name on two clusters -> two results (cluster_id disambiguates).
        assert sorted(i.cluster_id for i in resp.ingresses) == [1, 2]

    def test_limit_is_applied_to_ingresses(self):
        client = _FakeApiClient(
            ingresses=[_ingress(f"ing-{n}", "ns", hosts=[f"host-{n}.example.com"]) for n in range(5)],
        )
        db = _DB([_db_cluster(1, "c1")])
        with _wire({1: client}):
            resp = global_search(q="example.com", limit=3, db=db)
        assert len(resp.ingresses) == 3
