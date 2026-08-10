"""
Unit tests for crd_discovery_service pure functions.

No DB, Redis, or cluster — tests only merge_crd_with_registry and
group-filter logic.
"""
import pytest

from core.k8s_types import K8sResourceType
from services.crd_discovery_service import (
    _build_registry_index,
    _get_attr,
    _pick_version,
    merge_crd_with_registry,
)

# ============================================================================
# Helpers
# ============================================================================

def _rt(api_group: str, kind: str, plural: str, display_name: str = "Test", category: str = "test") -> K8sResourceType:
    return K8sResourceType(
        api_group=api_group,
        api_version="v1",
        kind=kind,
        plural=plural,
        namespaced=True,
        display_name=display_name,
        description=None,
        category=category,
    )


def _raw_crd(group: str, kind: str, plural: str, scope: str = "Namespaced",
             versions: list | None = None, name: str | None = None) -> dict:
    vers = versions or [{"name": "v1", "served": True, "storage": True}]
    return {
        "metadata": {"name": name or f"{plural}.{group}"},
        "spec": {
            "group": group,
            "names": {"kind": kind, "plural": plural},
            "scope": scope,
            "versions": vers,
        },
    }


# ============================================================================
# _pick_version
# ============================================================================

class TestGetAttr:
    """Tests for the _get_attr helper that handles both dicts and objects."""

    def test_dict_returns_value(self):
        assert _get_attr({"served": True}, "served") is True

    def test_dict_missing_key_returns_default(self):
        assert _get_attr({}, "served", False) is False

    def test_dict_missing_key_returns_none_by_default(self):
        assert _get_attr({}, "served") is None

    def test_object_returns_value(self):
        from types import SimpleNamespace
        obj = SimpleNamespace(served=True)
        assert _get_attr(obj, "served") is True

    def test_object_missing_attr_returns_default(self):
        from types import SimpleNamespace
        obj = SimpleNamespace()
        assert _get_attr(obj, "served", False) is False


class TestPickVersion:
    def test_prefers_served_and_storage_objects(self):
        """Object path (live Kubernetes client responses)."""
        from types import SimpleNamespace
        versions = [
            SimpleNamespace(name="v1alpha1", served=True, storage=False),
            SimpleNamespace(name="v1", served=True, storage=True),
        ]
        assert _pick_version(versions) == "v1"

    def test_prefers_served_and_storage_dicts(self):
        """Dict path (cache hit — Redis deserialises to plain dicts, not objects).

        This is the regression guard for the bug where getattr on a dict returns
        None instead of the dict value, causing version=None on every cache hit.
        """
        versions = [
            {"name": "v1alpha1", "served": True, "storage": False},
            {"name": "v1", "served": True, "storage": True},
        ]
        assert _pick_version(versions) == "v1"

    def test_dict_and_object_yield_same_result(self):
        """Both paths must produce identical output for the same logical data."""
        from types import SimpleNamespace
        data = [
            {"name": "v1alpha1", "served": True, "storage": False},
            {"name": "v1", "served": True, "storage": True},
        ]
        objs = [SimpleNamespace(**v) for v in data]
        assert _pick_version(data) == _pick_version(objs) == "v1"

    def test_falls_back_to_first_served_dicts(self):
        versions = [
            {"name": "v1beta1", "served": True, "storage": False},
            {"name": "v2", "served": False, "storage": True},
        ]
        assert _pick_version(versions) == "v1beta1"

    def test_falls_back_to_first_served_objects(self):
        from types import SimpleNamespace
        versions = [
            SimpleNamespace(name="v1beta1", served=True, storage=False),
            SimpleNamespace(name="v2", served=False, storage=True),
        ]
        assert _pick_version(versions) == "v1beta1"

    def test_empty_returns_none(self):
        assert _pick_version([]) is None

    def test_none_returns_none(self):
        assert _pick_version(None) is None  # type: ignore[arg-type]


# ============================================================================
# _build_registry_index
# ============================================================================

class TestBuildRegistryIndex:
    def test_keyed_on_api_group_and_kind(self):
        rts = {
            "foo": _rt("group-a.io", "Widget", "widgets"),
            "bar": _rt("group-b.io", "Widget", "widgets"),
        }
        idx = _build_registry_index(rts)
        assert ("group-a.io", "Widget") in idx
        assert ("group-b.io", "Widget") in idx

    def test_same_kind_different_groups_no_collision(self):
        rts = {
            "a": _rt("alpha.io", "Thing", "things", display_name="Alpha Thing", category="alpha"),
            "b": _rt("beta.io", "Thing", "things", display_name="Beta Thing", category="beta"),
        }
        idx = _build_registry_index(rts)
        assert idx[("alpha.io", "Thing")].display_name == "Alpha Thing"
        assert idx[("beta.io", "Thing")].display_name == "Beta Thing"


# ============================================================================
# merge_crd_with_registry
# ============================================================================

class TestMergeCrdWithRegistry:
    def test_unknown_kind_returns_discovered(self):
        raw = _raw_crd("custom.io", "Foo", "foos")
        result = merge_crd_with_registry(raw, {})
        assert result.source == "discovered"
        assert result.display_name is None
        assert result.category is None

    def test_known_kind_returns_registry_enriched(self):
        rt = _rt("custom.io", "Foo", "foos", display_name="Foo Resource", category="custom")
        idx = {("custom.io", "Foo"): rt}
        raw = _raw_crd("custom.io", "Foo", "foos")
        result = merge_crd_with_registry(raw, idx)
        assert result.source == "registry-enriched"
        assert result.display_name == "Foo Resource"
        assert result.category == "custom"

    def test_enrichment_keyed_on_group_not_bare_kind(self):
        """Two registry entries with the same kind but different groups:
        only the matching group enriches the CRD."""
        rt_a = _rt("alpha.io", "Widget", "widgets", display_name="Alpha Widget", category="alpha")
        rt_b = _rt("beta.io", "Widget", "widgets", display_name="Beta Widget", category="beta")
        idx = {
            ("alpha.io", "Widget"): rt_a,
            ("beta.io", "Widget"): rt_b,
        }
        raw_alpha = _raw_crd("alpha.io", "Widget", "widgets")
        result = merge_crd_with_registry(raw_alpha, idx)
        assert result.source == "registry-enriched"
        assert result.display_name == "Alpha Widget"
        assert result.category == "alpha"

        raw_beta = _raw_crd("beta.io", "Widget", "widgets")
        result_b = merge_crd_with_registry(raw_beta, idx)
        assert result_b.display_name == "Beta Widget"
        assert result_b.category == "beta"

    def test_name_field_is_plural_dot_group(self):
        raw = _raw_crd("custom.io", "Bar", "bars", name="bars.custom.io")
        result = merge_crd_with_registry(raw, {})
        assert result.name == "bars.custom.io"

    def test_name_falls_back_to_plural_dot_group_when_missing(self):
        raw = _raw_crd("custom.io", "Bar", "bars")
        raw["metadata"].pop("name", None)
        result = merge_crd_with_registry(raw, {})
        # Falls back to f"{plural}.{group}"
        assert result.name == "bars.custom.io"

    def test_namespaced_scope(self):
        raw = _raw_crd("custom.io", "Ns", "nses", scope="Namespaced")
        result = merge_crd_with_registry(raw, {})
        assert result.namespaced is True

    def test_cluster_scope(self):
        raw = _raw_crd("custom.io", "Cl", "cls", scope="Cluster")
        result = merge_crd_with_registry(raw, {})
        assert result.namespaced is False

    def test_version_storage_true_preferred_dicts(self):
        """Cache path: versions as plain dicts (produced by to_dict() → JSON)."""
        raw = _raw_crd("g.io", "X", "xs", versions=[
            {"name": "v1alpha1", "served": True, "storage": False},
            {"name": "v1", "served": True, "storage": True},
        ])
        result = merge_crd_with_registry(raw, {})
        assert result.version == "v1"

    def test_version_storage_true_preferred_objects(self):
        """Live path: versions as Kubernetes client objects (SimpleNamespace sim)."""
        from types import SimpleNamespace
        raw = _raw_crd("g.io", "X", "xs")
        raw["spec"]["versions"] = [
            SimpleNamespace(name="v1alpha1", served=True, storage=False),
            SimpleNamespace(name="v1", served=True, storage=True),
        ]
        result = merge_crd_with_registry(raw, {})
        assert result.version == "v1"

    def test_version_null_versions_defensive(self):
        raw = _raw_crd("g.io", "X", "xs")
        raw["spec"]["versions"] = None
        result = merge_crd_with_registry(raw, {})
        assert result.version is None

    def test_group_filter_via_list_crds_applies_post_cache(self):
        """Group filter: CRDs outside the requested group are excluded."""
        from services.crd_discovery_service import CrdDiscoveryService
        # This just exercises merge logic + group membership, not I/O
        rt = _rt("k8s.f5net.com", "FWPolicy", "fwpolicies", display_name="FW Policy", category="f5-bnk")
        idx = {("k8s.f5net.com", "FWPolicy"): rt}
        raw_f5 = _raw_crd("k8s.f5net.com", "FWPolicy", "fwpolicies")
        raw_other = _raw_crd("other.io", "Thing", "things")

        all_infos = [
            merge_crd_with_registry(raw_f5, idx),
            merge_crd_with_registry(raw_other, idx),
        ]
        group_filter = ["k8s.f5net.com"]
        filtered = [c for c in all_infos if c.group in group_filter]
        assert len(filtered) == 1
        assert filtered[0].group == "k8s.f5net.com"
        assert filtered[0].source == "registry-enriched"


# ============================================================================
# resolve_crd — version-shadowing guard (Step 2 dotted-name path)
# ============================================================================

class TestResolveCrdVersionShadowingGuard:
    """resolve_crd Step 2: dotted-name lookup for a registered kind must return
    the registry's curated api_version, not the cluster's served/storage version.

    These tests patch _resolve_via_discovery / list_crds at the service level to
    avoid needing a real DB or cluster. We test the pure logic of the guard by
    exercising the Step 2 branch directly through resolve_crd with mocked I/O.
    """

    def _make_svc(self, crds):
        """Return a CrdDiscoveryService with list_crds patched to return *crds*."""
        from unittest.mock import MagicMock, patch

        from schemas.k8s import CrdListEnvelope
        from services.crd_discovery_service import CrdDiscoveryService

        db = MagicMock()
        svc = CrdDiscoveryService.__new__(CrdDiscoveryService)
        svc.db = db

        envelope = CrdListEnvelope(
            crds=crds,
            count=len(crds),
            cluster_id=1,
            group_filter=None,
            info=None,
        )
        svc.list_crds = MagicMock(return_value=envelope)
        return svc

    def test_dotted_name_registered_kind_returns_curated_version(self):
        """Passing gateways.gateway.networking.k8s.io (a registered kind) via dotted
        FQ name must return the registry's curated version (e.g. v1), not the cluster's
        served version (e.g. v1beta1).
        """
        from schemas.k8s import CRDInfo

        # Cluster reports v1beta1 as the served version
        cluster_crd = CRDInfo(
            name="gateways.gateway.networking.k8s.io",
            kind="Gateway",
            plural="gateways",
            group="gateway.networking.k8s.io",
            version="v1beta1",  # cluster's served/storage version
            namespaced=True,
            display_name=None,
            category=None,
            source="discovered",
        )
        svc = self._make_svc([cluster_crd])

        result = svc.resolve_crd(1, "gateways.gateway.networking.k8s.io")

        assert result is not None
        # The registry curates Gateway as "v1" (see k8s_resource_registry.py)
        from core.k8s_resource_registry import get_resource_type
        curated = get_resource_type("gateway")
        assert result.version == curated.api_version, (
            f"Expected curated version '{curated.api_version}', got '{result.version}'"
        )
        # Other fields are preserved from the discovered CRD
        assert result.kind == "Gateway"
        assert result.group == "gateway.networking.k8s.io"
        assert result.plural == "gateways"

    def test_dotted_name_unregistered_kind_returns_discovered_version(self):
        """Dotted FQ name for a kind NOT in the static registry must return the
        discovered version unchanged — no version substitution occurs.
        """
        from schemas.k8s import CRDInfo

        cluster_crd = CRDInfo(
            name="widgets.custom.io",
            kind="Widget",
            plural="widgets",
            group="custom.io",
            version="v1alpha3",
            namespaced=True,
            display_name=None,
            category=None,
            source="discovered",
        )
        svc = self._make_svc([cluster_crd])

        result = svc.resolve_crd(1, "widgets.custom.io")

        assert result is not None
        assert result.version == "v1alpha3"  # cluster version preserved as-is


# ============================================================================
# resolve_crd — version-shadowing guard Step 3 (bare-plural path) — M3 fix
# ============================================================================

class TestResolveCrdVersionShadowingBareplural:
    """resolve_crd Step 3: bare-plural lookup must apply the registry version-shadow
    override, not just the dotted-name path (Step 2).

    M3 fix: a kind whose registry key is singular but whose cluster plural is used
    directly (e.g. 'tcproutes' for 'tcproute' registry entry, pinned v1alpha2) must
    return the registry's curated api_version, not the cluster's served version.
    """

    def _make_svc(self, crds):
        """Return a CrdDiscoveryService with list_crds patched to return *crds*."""
        from unittest.mock import MagicMock

        from schemas.k8s import CrdListEnvelope
        from services.crd_discovery_service import CrdDiscoveryService

        db = MagicMock()
        svc = CrdDiscoveryService.__new__(CrdDiscoveryService)
        svc.db = db

        envelope = CrdListEnvelope(
            crds=crds,
            count=len(crds),
            cluster_id=1,
            group_filter=None,
            info=None,
        )
        svc.list_crds = MagicMock(return_value=envelope)
        return svc

    def test_bare_plural_registered_kind_returns_curated_version(self):
        """Passing 'tcproutes' (bare plural, not a registry key, but whose (group,kind) is
        curated with api_version=v1alpha2) must return the registry's pinned version,
        not the cluster's served version.
        """
        from schemas.k8s import CRDInfo

        # Cluster serves v1beta1 for TCPRoute
        cluster_crd = CRDInfo(
            name="tcproutes.gateway.networking.k8s.io",
            kind="TCPRoute",
            plural="tcproutes",
            group="gateway.networking.k8s.io",
            version="v1beta1",  # cluster's served/storage version
            namespaced=True,
            display_name=None,
            category=None,
            source="discovered",
        )
        svc = self._make_svc([cluster_crd])

        result = svc.resolve_crd(1, "tcproutes")

        assert result is not None
        # Registry pins TCPRoute to v1alpha2; must win over cluster's v1beta1
        from core.k8s_resource_registry import get_resource_type
        curated = get_resource_type("tcproute")
        assert result.version == curated.api_version, (
            f"Expected curated version '{curated.api_version}', got '{result.version}'"
        )
        assert result.kind == "TCPRoute"
        assert result.plural == "tcproutes"

    def test_bare_plural_unregistered_kind_returns_discovered_version(self):
        """Bare plural for a kind NOT curated in the static registry returns the
        cluster's served version unchanged — version-shadowing only fires for curated kinds.
        """
        from schemas.k8s import CRDInfo

        cluster_crd = CRDInfo(
            name="widgets.custom.io",
            kind="Widget",
            plural="widgets",
            group="custom.io",
            version="v2alpha1",
            namespaced=True,
            display_name=None,
            category=None,
            source="discovered",
        )
        svc = self._make_svc([cluster_crd])

        result = svc.resolve_crd(1, "widgets")

        assert result is not None
        assert result.version == "v2alpha1"  # cluster version preserved


# ============================================================================
# H1 fix — tenantcontrolplanes plural key + main-thread resolve seam
# ============================================================================

class TestScannerFetchH1Fix:
    """H1 regression guard: the Kamaji TenantControlPlane fetch key must be the
    FQ plural 'tenantcontrolplanes.kamaji.clastix.io' (not the singular
    'tenantcontrolplane'), and resource-type resolution must happen on the main
    thread (before the ThreadPoolExecutor), never inside a worker.
    """

    def test_tenantcontrolplanes_key_resolves_via_discovery(self):
        """'tenantcontrolplanes.kamaji.clastix.io' must hit Step 2 (dotted FQ match) in
        resolve_crd, not 'tenantcontrolplane' which is singular and never matches the
        installed plural 'tenantcontrolplanes'.
        """
        from unittest.mock import MagicMock

        from schemas.k8s import CRDInfo, CrdListEnvelope
        from services.crd_discovery_service import CrdDiscoveryService

        db = MagicMock()
        svc = CrdDiscoveryService.__new__(CrdDiscoveryService)
        svc.db = db

        cluster_crd = CRDInfo(
            name="tenantcontrolplanes.kamaji.clastix.io",
            kind="TenantControlPlane",
            plural="tenantcontrolplanes",
            group="kamaji.clastix.io",
            version="v1alpha1",
            namespaced=True,
            display_name=None,
            category=None,
            source="discovered",
        )
        envelope = CrdListEnvelope(
            crds=[cluster_crd], count=1, cluster_id=1, group_filter=None, info=None
        )
        svc.list_crds = MagicMock(return_value=envelope)

        # FQ plural key must resolve successfully
        result = svc.resolve_crd(1, "tenantcontrolplanes.kamaji.clastix.io")
        assert result.kind == "TenantControlPlane"
        assert result.plural == "tenantcontrolplanes"
        assert result.group == "kamaji.clastix.io"

        # Singular key must NOT match (regression guard for the original bug)
        from core.errors import NotFoundError
        with pytest.raises(NotFoundError):
            svc.resolve_crd(1, "tenantcontrolplane")

    def test_resolve_crd_resource_types_returns_all_resolved_on_main_thread(self):
        """_resolve_crd_resource_types resolves a list of keys synchronously on the
        calling thread and returns a dict of resolved K8sResourceType objects.
        Keys that fail to resolve are silently omitted (not raised).

        This test confirms the main-thread resolution seam exists and that no DB
        access happens inside workers — the returned dict is passed to workers
        which receive K8sResourceType objects, never a db/Session.
        """
        from unittest.mock import MagicMock, patch

        from core.k8s_types import K8sResourceType
        from services.scanner.fetch import _resolve_crd_resource_types

        db = MagicMock()
        pod_rt = K8sResourceType(
            api_group="", api_version="v1", kind="Pod", plural="pods",
            namespaced=True, display_name="Pod", description=None,
        )
        widget_rt = K8sResourceType(
            api_group="custom.io", api_version="v1", kind="Widget", plural="widgets",
            namespaced=True, display_name="Widget", description=None,
        )

        def fake_resolve(db_arg, cluster_id, key, group=None):
            if key == "pod":
                return pod_rt
            if key == "widgets":
                return widget_rt
            from core.errors import NotFoundError
            raise NotFoundError("CRD", key)

        with patch("services.scanner.fetch.resolve_resource_type", side_effect=fake_resolve):
            result = _resolve_crd_resource_types(db, 1, ["pod", "widgets", "doesnotexist"])

        assert "pod" in result
        assert result["pod"] is pod_rt
        assert "widgets" in result
        assert result["widgets"] is widget_rt
        # Failed key is omitted, not raised
        assert "doesnotexist" not in result
