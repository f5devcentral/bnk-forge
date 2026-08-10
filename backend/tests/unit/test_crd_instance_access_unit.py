"""
Unit tests for CrdDiscoveryService.resolve_crd (P1.5 — D-018).

Pure tests over the resolver ladder: no DB, Redis, or real cluster.
All tests mock list_crds to return a controlled CrdListEnvelope.
"""
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, NotFoundError
from schemas.k8s import CRDInfo, CrdListEnvelope
from services.crd_discovery_service import CrdDiscoveryService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _crd(plural: str, group: str, kind: str = "", namespaced: bool = True,
         version: str = "v1") -> CRDInfo:
    kind = kind or plural.rstrip("s").capitalize()
    return CRDInfo(
        name=f"{plural}.{group}",
        kind=kind,
        plural=plural,
        group=group,
        version=version,
        namespaced=namespaced,
        display_name=None,
        category=None,
        source="discovered",
    )


def _envelope(*crds: CRDInfo, cluster_id: int = 1) -> CrdListEnvelope:
    return CrdListEnvelope(
        crds=list(crds),
        count=len(crds),
        cluster_id=cluster_id,
        group_filter=None,
        info=None,
    )


def _make_svc() -> CrdDiscoveryService:
    """Return a CrdDiscoveryService with a mock db session."""
    db = MagicMock()
    return CrdDiscoveryService(db)


# ---------------------------------------------------------------------------
# Registry-first: resolve_crd raises ValueError for registered kinds
# ---------------------------------------------------------------------------

class TestResolveCrdRegistryFirst:
    """Registry-key exact match must win — discovery must NOT be consulted.

    resolve_crd raises ValueError when called for a kind that is already in the
    static registry.  This is a loud guard against caller misuse: callers must
    resolve registered kinds via get_resource_type() before calling resolve_crd.
    """

    def test_registered_kind_raises_value_error_without_calling_list_crds(self):
        """A kind in the static registry → resolve_crd raises ValueError.

        list_crds must never be called — the guard fires before any discovery.
        """
        svc = _make_svc()
        with patch.object(svc, "list_crds") as mock_list_crds:
            # "pod" is a well-known registered kind
            with pytest.raises(ValueError, match="registered kind 'pod'"):
                svc.resolve_crd(1, "pod")
        mock_list_crds.assert_not_called()

    def test_registered_custom_kind_raises_value_error_without_calling_list_crds(self):
        """A registered CRD kind (e.g. gateway) → ValueError, no discovery."""
        svc = _make_svc()
        with patch.object(svc, "list_crds") as mock_list_crds:
            # "gateway" is registered in the static registry
            with pytest.raises(ValueError, match="registered kind 'gateway'"):
                svc.resolve_crd(1, "gateway")
        mock_list_crds.assert_not_called()


# ---------------------------------------------------------------------------
# Dotted FQ name: <plural>.<group>
# ---------------------------------------------------------------------------

class TestResolveCrdDottedFQName:
    """resource_type contains a dot → match on CRDInfo.name."""

    def test_fq_name_resolves_correctly(self):
        crd = _crd("analyzers", "k8s.f5.com", kind="Analyzer")
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope(crd)):
            result = svc.resolve_crd(1, "analyzers.k8s.f5.com")
        assert result is not None
        assert result.plural == "analyzers"
        assert result.group == "k8s.f5.com"

    def test_fq_name_disambiguates_same_plural_different_groups(self):
        """FQ name resolves even when two groups share the same plural."""
        crd_a = _crd("widgets", "group-a.io", kind="Widget")
        crd_b = _crd("widgets", "group-b.io", kind="Widget")
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope(crd_a, crd_b)):
            result = svc.resolve_crd(1, "widgets.group-b.io")
        assert result.group == "group-b.io"

    def test_fq_name_not_installed_raises_not_found(self):
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope()):
            with pytest.raises(NotFoundError):
                svc.resolve_crd(1, "missing.some-group.io")

    def test_fq_name_wrong_group_raises_not_found(self):
        crd = _crd("analyzers", "k8s.f5.com")
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope(crd)):
            with pytest.raises(NotFoundError):
                svc.resolve_crd(1, "analyzers.wrong-group.io")


# ---------------------------------------------------------------------------
# Bare plural — single match
# ---------------------------------------------------------------------------

class TestResolveCrdBarePluralSingleMatch:
    def test_bare_plural_single_match_resolves(self):
        crd = _crd("rabbitmqs", "rabbitmq.com", kind="RabbitMQ")
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope(crd)):
            result = svc.resolve_crd(1, "rabbitmqs")
        assert result.kind == "RabbitMQ"
        assert result.group == "rabbitmq.com"

    def test_bare_plural_not_installed_raises_not_found(self):
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope()):
            with pytest.raises(NotFoundError):
                svc.resolve_crd(1, "notexistent")


# ---------------------------------------------------------------------------
# Bare plural — ambiguous (>1 match, no group)
# ---------------------------------------------------------------------------

class TestResolveCrdAmbiguous:
    def test_ambiguous_bare_plural_raises_bad_request(self):
        """Two groups share the same plural + no ?group= → 400 with candidates."""
        crd_a = _crd("crds_foo", "group-a.io", kind="Foo")
        crd_b = _crd("crds_foo", "group-b.io", kind="Foo")
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope(crd_a, crd_b)):
            with pytest.raises(BadRequestError) as exc_info:
                svc.resolve_crd(1, "crds_foo")
        err = exc_info.value
        assert err.status_code == 400
        assert "crds_foo.group-a.io" in err.details.get("candidates", [])
        assert "crds_foo.group-b.io" in err.details.get("candidates", [])

    def test_ambiguous_bare_plural_resolved_with_group_param(self):
        """Same ambiguous plural + ?group= → resolves to unique match."""
        crd_a = _crd("crds_foo", "group-a.io", kind="FooA")
        crd_b = _crd("crds_foo", "group-b.io", kind="FooB")
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope(crd_a, crd_b)):
            result = svc.resolve_crd(1, "crds_foo", group="group-b.io")
        assert result.kind == "FooB"

    def test_group_narrowing_no_match_raises_not_found(self):
        """Bare plural + group that doesn't exist in cluster → 404."""
        crd = _crd("widgets", "group-a.io")
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope(crd)):
            with pytest.raises(NotFoundError):
                svc.resolve_crd(1, "widgets", group="nonexistent.io")


# ---------------------------------------------------------------------------
# Scope: namespaced vs cluster-scoped
# ---------------------------------------------------------------------------

class TestResolveCrdScope:
    def test_namespaced_crd_returns_namespaced_true(self):
        crd = _crd("namespacedthings", "example.io", namespaced=True)
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope(crd)):
            result = svc.resolve_crd(1, "namespacedthings")
        assert result.namespaced is True

    def test_cluster_scoped_crd_returns_namespaced_false(self):
        crd = _crd("clusterthings", "example.io", namespaced=False)
        svc = _make_svc()
        with patch.object(svc, "list_crds", return_value=_envelope(crd)):
            result = svc.resolve_crd(1, "clusterthings")
        assert result.namespaced is False
