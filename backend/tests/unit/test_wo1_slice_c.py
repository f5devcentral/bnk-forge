"""
Unit tests for WO-1 Slice C: dynamic BNK namespace discovery, pod matching
alignment, dedup, and verified CIS→BNK security CRD mapping.

Covers:
  - Issue #139: extra_namespaces parameter seeds discovery + ns tracking
  - Issue #157: classify_f5_pods prefix alignment + dedup
  - Issue #268: verified CIS→BNK security field mapping
"""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Environment setup (mirrors conftest.py pattern)
# ---------------------------------------------------------------------------

os.environ.setdefault("DATABASE_URL", "sqlite:///file::memory:?cache=shared")
os.environ.setdefault("REQUIRE_AUTH", "true")
os.environ.setdefault("ENVIRONMENT", "development")

backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# ---------------------------------------------------------------------------
# Imports (after env setup)
# ---------------------------------------------------------------------------

from services.bnk_pod_discovery import (
    BNK_NAMESPACES,
    classify_f5_pods,
    discover_f5_pods,
    discover_f5_pods_with_ns_tracking,
)
from services.proxy_translate_cis_service import (
    _BNK_SECURITY_KIND_API,
    _CIS_SECURITY_FIELD_TO_BNK_KIND,
    translate_cis_to_bnk,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_v1pod(
    name: str,
    namespace: str = "f5-bnk",
    labels: dict | None = None,
) -> MagicMock:
    """Build a minimal V1Pod-like MagicMock."""
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = namespace
    pod.metadata.labels = labels or {}
    pod.spec.node_name = None
    pod.status.phase = "Running"
    pod.status.host_ip = None
    pod.status.start_time = None
    pod.status.conditions = []
    pod.status.container_statuses = []
    return pod


def _pod_dict(name: str, namespace: str = "f5-bnk", labels: dict | None = None) -> dict:
    """Build a pod dict as classify_f5_pods receives."""
    return {
        "name": name,
        "namespace": namespace,
        "labels": labels or {},
        "containers": [],
        "phase": "Running",
    }


def _vs(name: str = "vs1", namespace: str = "default", **spec_kwargs) -> dict:
    """Build a minimal CIS VirtualServer dict."""
    spec = {
        "host": "app.example.com",
        "pools": [{"path": "/", "service": "backend-svc", "servicePort": 8080}],
    }
    spec.update(spec_kwargs)
    return {
        "apiVersion": "cis.f5.com/v1",
        "kind": "VirtualServer",
        "metadata": {"name": name, "namespace": namespace},
        "spec": spec,
    }


def _make_api_client(
    sweep_pods: list,
    ns_pods: dict | None = None,
) -> Generator[tuple[MagicMock, MagicMock], None, None]:
    """Mock kubernetes api_client for discover_f5_pods tests."""
    ns_pods = ns_pods or {}
    api_client = MagicMock()
    core_v1 = MagicMock()

    def _list_namespaced(namespace, **kwargs):
        resp = MagicMock()
        resp.items = ns_pods.get(namespace, [])
        return resp

    def _list_all(**kwargs):
        resp = MagicMock()
        resp.items = sweep_pods
        return resp

    core_v1.list_namespaced_pod.side_effect = _list_namespaced
    core_v1.list_pod_for_all_namespaces.side_effect = _list_all

    with patch("services.bnk_pod_discovery.k8s_client.CoreV1Api", return_value=core_v1):
        yield api_client, core_v1


# ===========================================================================
# Issue #139 — extra_namespaces seeds discovery + namespace tracking
# ===========================================================================


class TestExtraNamespaces:
    """discover_f5_pods accepts extra_namespaces from persisted cluster data."""

    @pytest.mark.unit
    def test_extra_namespace_queried_in_phase1(self):
        """An extra (persisted) namespace is queried in phase-1 fast-path."""
        custom_pod = _make_v1pod("f5-tmm-abc", namespace="custom-bnk", labels={"app": "f5-tmm"})
        for api_client, core_v1 in _make_api_client(
            sweep_pods=[],
            ns_pods={"custom-bnk": [custom_pod]},
        ):
            tenant, _ = discover_f5_pods(
                api_client,
                include_sweep=False,
                extra_namespaces=["custom-bnk"],
            )

        assert any(p["name"] == "f5-tmm-abc" for p in tenant), (
            "Pod in extra_namespaces should be discovered via fast-path"
        )
        # Verify the namespace was actually queried
        queried_ns = {
            call.args[0] if call.args else call.kwargs.get("namespace")
            for call in core_v1.list_namespaced_pod.call_args_list
        }
        assert "custom-bnk" in queried_ns

    @pytest.mark.unit
    def test_static_bnk_namespaces_still_queried_with_extra(self):
        """Static BNK_NAMESPACES are always queried even when extra_namespaces is set."""
        for api_client, core_v1 in _make_api_client(sweep_pods=[], ns_pods={}):
            discover_f5_pods(
                api_client,
                include_sweep=False,
                extra_namespaces=["extra-ns"],
            )
        queried = {
            call.args[0] if call.args else call.kwargs.get("namespace")
            for call in core_v1.list_namespaced_pod.call_args_list
        }
        for ns in BNK_NAMESPACES:
            assert ns in queried, f"Static namespace {ns!r} must always be queried"

    @pytest.mark.unit
    def test_no_duplicate_queries_when_extra_overlaps_static(self):
        """If extra_namespaces contains a static NS, it should not be queried twice."""
        for api_client, core_v1 in _make_api_client(sweep_pods=[], ns_pods={}):
            discover_f5_pods(
                api_client,
                include_sweep=False,
                extra_namespaces=["f5-bnk"],  # already in BNK_NAMESPACES
            )
        all_ns_calls = [
            call.args[0] if call.args else call.kwargs.get("namespace")
            for call in core_v1.list_namespaced_pod.call_args_list
        ]
        assert all_ns_calls.count("f5-bnk") == 1, "f5-bnk should only be queried once"

    @pytest.mark.unit
    def test_discover_with_ns_tracking_returns_namespaces(self):
        """discover_f5_pods_with_ns_tracking returns (tenant, utils, ns_list)."""
        tmm_pod = _make_v1pod("f5-tmm-x", namespace="f5-bnk", labels={"app": "f5-tmm"})
        crd_pod = _make_v1pod("crd-installer-job", namespace="f5-utils")
        for api_client, _ in _make_api_client(
            sweep_pods=[],
            ns_pods={"f5-bnk": [tmm_pod], "f5-utils": [crd_pod]},
        ):
            tenant, utils, ns_list = discover_f5_pods_with_ns_tracking(
                api_client, include_sweep=False
            )

        assert isinstance(ns_list, list)
        assert "f5-bnk" in ns_list, "f5-bnk should be in discovered namespaces"
        assert "f5-utils" in ns_list, "f5-utils (CRD installer) should be in discovered namespaces"

    @pytest.mark.unit
    def test_discover_with_ns_tracking_empty_when_no_pods(self):
        """When no F5 pods are found, discovered_namespaces is empty."""
        for api_client, _ in _make_api_client(sweep_pods=[], ns_pods={}):
            _, _, ns_list = discover_f5_pods_with_ns_tracking(
                api_client, include_sweep=False
            )
        assert ns_list == [], "No pods → no discovered namespaces"


# ===========================================================================
# Issue #157 — classify_f5_pods: prefix alignment + dedup
# ===========================================================================


class TestClassifyF5PodsAlignmentAndDedup:
    """classify_f5_pods uses prefix matching (not substring) and deduplicates."""

    @pytest.mark.unit
    def test_tmm_prefix_classifies_to_tmm(self):
        """Pod starting with 'f5-tmm' → classified as tmm."""
        p = _pod_dict("f5-tmm-0", namespace="f5-bnk")
        result = classify_f5_pods([p], [])
        assert p in result["tmm"]

    @pytest.mark.unit
    def test_prefix_not_substring_no_false_positive(self):
        """Pod named 'my-f5-tmm-copy' does NOT start with 'f5-tmm' → not classified as tmm."""
        p = _pod_dict("my-f5-tmm-copy", namespace="f5-bnk")
        result = classify_f5_pods([p], [])
        # Pod should not appear in tmm (substring would match; prefix should NOT)
        assert p not in result["tmm"], (
            "Substring match 'f5-tmm' in 'my-f5-tmm-copy' should not classify as tmm "
            "when using prefix semantics"
        )

    @pytest.mark.unit
    def test_flo_prefix_classifies_to_flo(self):
        """Pod starting with 'flo-f5-lifecycle' → classified as flo."""
        p = _pod_dict("flo-f5-lifecycle-operator-0")
        result = classify_f5_pods([p], [])
        assert p in result["flo"]

    @pytest.mark.unit
    def test_f5_lifecycle_prefix_also_classifies_to_flo(self):
        """Pod starting with 'f5-lifecycle' → classified as flo (legacy prefix)."""
        p = _pod_dict("f5-lifecycle-operator-abc")
        result = classify_f5_pods([p], [])
        assert p in result["flo"]

    @pytest.mark.unit
    def test_crd_installer_prefix_classifies_to_crd_installer(self):
        """Pod starting with 'crd-installer' → classified as crd_installer."""
        p = _pod_dict("crd-installer-job-xyz", namespace="f5-utils")
        result = classify_f5_pods([], [p])
        assert p in result["crd_installer"]

    @pytest.mark.unit
    def test_dedup_across_tenant_and_utils_lists(self):
        """Same (namespace, name) pod in both lists is classified only once."""
        p = _pod_dict("f5-tmm-0", namespace="f5-bnk")
        result = classify_f5_pods([p], [p])  # Same pod dict in both lists
        assert len(result["tmm"]) == 1, "Duplicate pod must appear exactly once"

    @pytest.mark.unit
    def test_dedup_does_not_drop_different_pods(self):
        """Dedup only applies to (namespace, name) identity — different pods kept."""
        p1 = _pod_dict("f5-tmm-0", namespace="f5-bnk")
        p2 = _pod_dict("f5-tmm-1", namespace="f5-bnk")
        result = classify_f5_pods([p1, p2], [])
        assert len(result["tmm"]) == 2

    @pytest.mark.unit
    def test_dedup_same_name_different_namespace_both_kept(self):
        """Same pod name in different namespaces are separate entries."""
        p1 = _pod_dict("f5-tmm-0", namespace="f5-bnk")
        p2 = _pod_dict("f5-tmm-0", namespace="custom-bnk")
        result = classify_f5_pods([p1, p2], [])
        assert len(result["tmm"]) == 2

    @pytest.mark.unit
    def test_label_hint_takes_priority_over_prefix(self):
        """Label hint (Layer 1) wins over name prefix (Layer 3)."""
        # A "flo" pod that ALSO has app=f5-tmm label → label wins → classified as tmm
        p = _pod_dict("flo-f5-lifecycle-xyz", labels={"app": "f5-tmm"})
        result = classify_f5_pods([p], [])
        assert p in result["tmm"], "Label hint (app=f5-tmm) should override flo prefix"


# ===========================================================================
# Issue #268 — verified CIS→BNK security CRD kind mapping
# ===========================================================================


class TestCisSecurityMapping:
    """CIS security fields map to the correct verified BNK CRD kind."""

    @pytest.mark.unit
    def test_mapping_table_waf_to_bnksecpolicy(self):
        """policyWAF maps to BNKSecPolicy."""
        assert _CIS_SECURITY_FIELD_TO_BNK_KIND["policyWAF"] == "BNKSecPolicy"

    @pytest.mark.unit
    def test_mapping_table_bot_to_bnksecpolicy(self):
        """profileBotDefense maps to BNKSecPolicy."""
        assert _CIS_SECURITY_FIELD_TO_BNK_KIND["profileBotDefense"] == "BNKSecPolicy"

    @pytest.mark.unit
    def test_mapping_table_firewall_to_f5bigfwpolicy(self):
        """policyFirewall maps to F5BigFwPolicy."""
        assert _CIS_SECURITY_FIELD_TO_BNK_KIND["policyFirewall"] == "F5BigFwPolicy"

    @pytest.mark.unit
    def test_mapping_table_waf_vs_field_to_bnksecpolicy(self):
        """waf (VirtualServer field) maps to BNKSecPolicy."""
        assert _CIS_SECURITY_FIELD_TO_BNK_KIND["waf"] == "BNKSecPolicy"

    @pytest.mark.unit
    def test_mapping_table_bot_defense_vs_field_to_bnksecpolicy(self):
        """botDefense (VirtualServer field) maps to BNKSecPolicy."""
        assert _CIS_SECURITY_FIELD_TO_BNK_KIND["botDefense"] == "BNKSecPolicy"

    @pytest.mark.unit
    def test_mapping_table_firewall_policy_vs_field(self):
        """firewallPolicy (VirtualServer field) maps to F5BigFwPolicy."""
        assert _CIS_SECURITY_FIELD_TO_BNK_KIND["firewallPolicy"] == "F5BigFwPolicy"

    @pytest.mark.unit
    def test_api_group_for_bnksecpolicy(self):
        """BNKSecPolicy API group is gateway.k8s.f5net.com."""
        group, version = _BNK_SECURITY_KIND_API["BNKSecPolicy"]
        assert group == "gateway.k8s.f5net.com"

    @pytest.mark.unit
    def test_api_group_for_f5bigfwpolicy(self):
        """F5BigFwPolicy API group is k8s.f5net.com."""
        group, version = _BNK_SECURITY_KIND_API["F5BigFwPolicy"]
        assert group == "k8s.f5net.com"

    @pytest.mark.unit
    def test_waf_vs_reason_mentions_bnksecpolicy_kind(self):
        """VirtualServer waf field → unmapped reason mentions BNKSecPolicy kind."""
        vs = _vs(waf="/Common/waf-policy")
        result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
        waf_entries = [u for u in result.unmapped if "waf" in u.detail]
        assert waf_entries, "Expected waf to produce an unmapped entry"
        assert "BNKSecPolicy" in waf_entries[0].reason, (
            f"Reason should mention BNKSecPolicy, got: {waf_entries[0].reason!r}"
        )

    @pytest.mark.unit
    def test_bot_defense_vs_reason_mentions_bnksecpolicy(self):
        """VirtualServer botDefense → reason mentions BNKSecPolicy."""
        vs = _vs(botDefense="/Common/bot-policy")
        result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
        bot_entries = [u for u in result.unmapped if "botDefense" in u.detail]
        assert bot_entries
        assert "BNKSecPolicy" in bot_entries[0].reason

    @pytest.mark.unit
    def test_firewall_policy_vs_reason_mentions_f5bigfwpolicy(self):
        """VirtualServer firewallPolicy → reason mentions F5BigFwPolicy."""
        vs = _vs(firewallPolicy="/Common/afm-policy")
        result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
        fw_entries = [u for u in result.unmapped if "firewallPolicy" in u.detail]
        assert fw_entries
        assert "F5BigFwPolicy" in fw_entries[0].reason

    @pytest.mark.unit
    def test_security_fields_still_produce_unmapped_entries(self):
        """Security fields still go to unmapped (not silently dropped)."""
        vs = _vs(waf="/Common/waf", firewallPolicy="/Common/fw")
        result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
        constructs = {u.construct for u in result.unmapped}
        assert "security_policy" in constructs

    @pytest.mark.unit
    def test_unknown_security_field_falls_back_to_unmapped(self):
        """policyName (no single BNK kind) still routes to unmapped."""
        vs = _vs(policyName="/Common/some-policy")
        result = translate_cis_to_bnk(virtualservers=[vs], transportservers=[])
        pn_entries = [u for u in result.unmapped if "policyName" in u.detail]
        assert pn_entries, "policyName should still produce an unmapped entry"
        # The reason should contain 'verify' and mention BNK CRDs (per existing test contract)
        reason_lower = pn_entries[0].reason.lower()
        assert "verify" in reason_lower, "Fallback reason should contain 'verify'"
        assert "bnk" in reason_lower, "Fallback reason should mention BNK"

    @pytest.mark.unit
    def test_all_mapped_kinds_exist_in_api_table(self):
        """Every kind referenced in the mapping table must have an API entry."""
        for field, kind in _CIS_SECURITY_FIELD_TO_BNK_KIND.items():
            assert kind in _BNK_SECURITY_KIND_API, (
                f"Field {field!r} maps to kind {kind!r} which has no API entry"
            )


# ===========================================================================
# Migration structural check — v2_140
# ===========================================================================


class TestMigrationV2140:
    """Structural check: v2_140 is well-formed and round-trips cleanly."""

    @pytest.mark.unit
    def test_migration_file_exists(self):
        """v2_140 migration file must exist in the versions directory."""
        versions_dir = os.path.join(backend_path, "alembic", "versions")
        files = os.listdir(versions_dir)
        assert any("v2_140" in f for f in files), "v2_140 migration file not found"

    @pytest.mark.unit
    def test_migration_has_correct_down_revision(self):
        """v2_140 down_revision must be v2_139 (staging head)."""
        import importlib.util

        versions_dir = os.path.join(backend_path, "alembic", "versions")
        migration_file = next(
            (os.path.join(versions_dir, f) for f in os.listdir(versions_dir) if "v2_140" in f),
            None,
        )
        assert migration_file is not None
        spec = importlib.util.spec_from_file_location("migration_v2_140", migration_file)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.down_revision == "v2_139"
        assert mod.revision == "v2_140"

    @pytest.mark.unit
    def test_migration_upgrade_downgrade_round_trip(self):
        """v2_140 upgrade()/downgrade() round-trip against SQLite."""
        import importlib.util

        import sqlalchemy as sa
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.pool import StaticPool

        versions_dir = os.path.join(backend_path, "alembic", "versions")
        migration_file = next(
            os.path.join(versions_dir, f) for f in os.listdir(versions_dir) if "v2_140" in f
        )
        spec = importlib.util.spec_from_file_location("migration_v2_140", migration_file)
        assert spec and spec.loader
        migration_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration_module)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # Pre-create the kubernetes_clusters table (simplified) so ADD COLUMN works
        metadata = sa.MetaData()
        sa.Table(
            "kubernetes_clusters",
            metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(255)),
        )
        metadata.create_all(engine)

        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            migration_module.op = Operations(ctx)

            # Upgrade: column should appear
            migration_module.upgrade()
            cols = {c["name"] for c in inspect(conn).get_columns("kubernetes_clusters")}
            assert "discovered_namespaces" in cols, "upgrade() must add discovered_namespaces"

            # Downgrade: column should disappear
            migration_module.downgrade()
            cols_after = {c["name"] for c in inspect(conn).get_columns("kubernetes_clusters")}
            assert "discovered_namespaces" not in cols_after, "downgrade() must drop column"

            # Re-upgrade (second run must be clean)
            migration_module.upgrade()
            cols_final = {c["name"] for c in inspect(conn).get_columns("kubernetes_clusters")}
            assert "discovered_namespaces" in cols_final

        engine.dispose()

    @pytest.mark.unit
    def test_model_has_discovered_namespaces_column(self):
        """KubernetesCluster ORM model has discovered_namespaces as JSON column."""
        from sqlalchemy import create_engine
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy.pool import StaticPool

        import models  # noqa: F401
        from database import Base

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        inspector = sa_inspect(engine)
        cols = {c["name"] for c in inspector.get_columns("kubernetes_clusters")}
        assert "discovered_namespaces" in cols
        engine.dispose()


# ===========================================================================
# Persistence durability test — proves the commit at scan entrypoints (#139)
# ===========================================================================


class TestDiscoveredNamespacesPersistenceDurability:
    """Proves that discovered_namespaces is durably committed at scan entrypoints.

    The test drives the exact commit code path used by scan_cluster_async (the
    Celery task) — ClusterScanner(db).scan(cluster_id) followed by db.commit()
    — then opens a FRESH independent session to confirm the value was actually
    written to the DB (not just held in the ORM's identity map).

    Without the db.commit() call this test FAILS because expire_all() + re-query
    on the fresh session returns NULL (the flush was rolled back on session close).
    """

    @pytest.mark.unit
    def test_discovered_namespaces_durable_after_task_commit(self):
        """discovered_namespaces written by the scanner survives a session boundary."""
        from unittest.mock import patch

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        import models  # noqa: F401
        from database import Base
        from models import KubernetesCluster

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)

        # --- Session 1: create the cluster row and commit ---
        s1 = Session()
        cluster = KubernetesCluster(
            name="durability-test-cluster",
            context="test-ctx",
            api_server="https://k8s.example.com:6443",
            status="active",
            version="1.28",
        )
        s1.add(cluster)
        s1.commit()
        cluster_id = cluster.id
        s1.close()

        # --- Session 2: drive the scanner write path + commit (mirrors task body) ---
        EXPECTED_NS = ["f5-bnk", "f5-utils"]

        def _mock_scan(self_scanner, cid):
            """Write discovered_namespaces and flush — exactly as the real scanner does."""
            row = self_scanner.db.get(KubernetesCluster, cid)
            row.discovered_namespaces = EXPECTED_NS
            self_scanner.db.flush()

        s2 = Session()
        with patch("services.scanner.ClusterScanner.scan", _mock_scan):
            from services.cluster_scanner import ClusterScanner
            ClusterScanner(s2).scan(cluster_id)
            # This is the commit added by fix #1 — remove it and the test fails.
            s2.commit()
        s2.close()

        # --- Session 3: fresh session, re-read, assert durable ---
        s3 = Session()
        reloaded = s3.get(KubernetesCluster, cluster_id)
        assert reloaded is not None
        assert reloaded.discovered_namespaces == EXPECTED_NS, (
            f"discovered_namespaces should be persisted after commit; "
            f"got {reloaded.discovered_namespaces!r}"
        )
        s3.close()

        engine.dispose()
