"""
Component tests for services.k8s_builtin_module_seeder.

Verifies that seed_k8s_builtin_modules() correctly creates / updates
ModuleLibrary rows using a real (SQLite in-memory) database session.
"""

import pytest

from models import ModuleLibrary
from services.k8s_builtin_module_seeder import K8S_BUILTIN_MODULES, seed_k8s_builtin_modules
from services.module_capabilities import get_execution_engine


@pytest.mark.component
class TestSeedK8sBuiltinModules:
    # ------------------------------------------------------------------
    # Sanity: catalog definition
    # ------------------------------------------------------------------

    def test_catalog_contains_bnk_cert_issuer(self):
        """k8s/bnk-cert-issuer must be present in the seeder catalog."""
        paths = [m["path"] for m in K8S_BUILTIN_MODULES]
        assert "k8s/bnk-cert-issuer" in paths

    def test_catalog_contains_all_expected_k8s_modules(self):
        """All four known k8s builtin modules are declared."""
        paths = {m["path"] for m in K8S_BUILTIN_MODULES}
        expected = {
            "k8s/bnk-prerequisites",
            "k8s/network-setup",
            "k8s/cert-manager",
            "k8s/bnk-cert-issuer",
        }
        assert expected == paths

    def test_all_catalog_entries_have_required_fields(self):
        """Every catalog entry must have path, name, description, and all execution metadata."""
        for entry in K8S_BUILTIN_MODULES:
            assert "path" in entry, f"Missing 'path' in {entry}"
            assert "name" in entry, f"Missing 'name' in {entry}"
            assert "description" in entry, f"Missing 'description' in {entry}"
            assert entry.get("engine_type") == "kubernetes", (
                f"engine_type must be 'kubernetes' for {entry['path']}"
            )
            assert entry.get("module_source_kind") == "builtin", (
                f"module_source_kind must be 'builtin' for {entry['path']}"
            )
            assert entry.get("execution_engine") == "kubernetes-direct", (
                f"execution_engine must be 'kubernetes-direct' for {entry['path']}"
            )
            assert entry.get("deploy_model") == "kubernetes", (
                f"deploy_model must be 'kubernetes' for {entry['path']}"
            )
            # dependencies_metadata must be present with required/optional lists
            deps = entry.get("dependencies_metadata")
            assert isinstance(deps, dict), (
                f"dependencies_metadata must be a dict for {entry['path']}"
            )
            assert "required" in deps, (
                f"dependencies_metadata.required missing for {entry['path']}"
            )
            assert "optional" in deps, (
                f"dependencies_metadata.optional missing for {entry['path']}"
            )

    def test_dependency_chain_correctness(self):
        """Builtin k8s modules declare the correct bare-metal BNK dependency chain."""
        by_path = {m["path"]: m for m in K8S_BUILTIN_MODULES}

        # bnk-prerequisites: no BNK deps (root of the chain)
        assert by_path["k8s/bnk-prerequisites"]["dependencies_metadata"]["required"] == []

        # network-setup → bnk-prerequisites
        ns_deps = [d["module"] for d in by_path["k8s/network-setup"]["dependencies_metadata"]["required"]]
        assert ns_deps == ["k8s/bnk-prerequisites"]

        # cert-manager → network-setup (strictly sequential: must run after NADs are created)
        cm_deps = [d["module"] for d in by_path["k8s/cert-manager"]["dependencies_metadata"]["required"]]
        assert cm_deps == ["k8s/network-setup"]

        # bnk-cert-issuer → cert-manager
        ci_deps = [d["module"] for d in by_path["k8s/bnk-cert-issuer"]["dependencies_metadata"]["required"]]
        assert ci_deps == ["k8s/cert-manager"]

    def test_dependency_references_are_valid_paths(self):
        """All module paths referenced in dependencies_metadata exist in the catalog."""
        all_paths = {m["path"] for m in K8S_BUILTIN_MODULES}
        for entry in K8S_BUILTIN_MODULES:
            for dep in entry["dependencies_metadata"]["required"]:
                dep_path = dep.get("module")
                assert dep_path in all_paths, (
                    f"{entry['path']} depends on '{dep_path}' which is not in the builtin catalog"
                )

    # ------------------------------------------------------------------
    # Create path: empty DB → all rows inserted
    # ------------------------------------------------------------------

    def test_seed_creates_all_modules_on_empty_db(self, db):
        """All catalog entries are created when library is empty."""
        created, updated = seed_k8s_builtin_modules(db)

        assert created == len(K8S_BUILTIN_MODULES)
        assert updated == 0

        # Every path should now exist with full execution metadata
        for entry in K8S_BUILTIN_MODULES:
            row = db.query(ModuleLibrary).filter(
                ModuleLibrary.path == entry["path"]
            ).first()
            assert row is not None, f"Expected row for {entry['path']}"
            assert row.engine_type == "kubernetes"
            assert row.module_source_kind == "builtin"
            assert row.execution_engine == "kubernetes-direct"
            assert row.deploy_model == "kubernetes"
            assert row.is_active is True
            assert row.is_official is True

    def test_seed_bnk_cert_issuer_fields(self, db):
        """k8s/bnk-cert-issuer row has correct field values after seeding."""
        seed_k8s_builtin_modules(db)

        row = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "k8s/bnk-cert-issuer"
        ).first()
        assert row is not None
        assert row.name == "BNK Cert Issuer"
        assert row.category == "k8s"
        assert row.git_source == "builtin://k8s/bnk-cert-issuer"
        assert row.version == "1.0.0"
        assert row.engine_type == "kubernetes"
        assert row.module_source_kind == "builtin"
        assert row.execution_engine == "kubernetes-direct"
        assert row.deploy_model == "kubernetes"
        assert row.is_active is True
        assert row.is_official is True
        assert row.is_tested is True

    def test_seed_persists_dependencies_metadata(self, db):
        """dependencies_metadata is persisted to the DB for all seeded modules."""
        seed_k8s_builtin_modules(db)

        # bnk-cert-issuer should depend on cert-manager
        row = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "k8s/bnk-cert-issuer"
        ).first()
        assert row is not None
        deps = row.dependencies_metadata
        assert isinstance(deps, dict)
        assert deps["required"] == [{"module": "k8s/cert-manager"}]

        # network-setup should depend on bnk-prerequisites
        row2 = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "k8s/network-setup"
        ).first()
        assert row2 is not None
        assert row2.dependencies_metadata["required"] == [{"module": "k8s/bnk-prerequisites"}]

        # bnk-prerequisites should have empty deps
        row3 = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "k8s/bnk-prerequisites"
        ).first()
        assert row3 is not None
        assert row3.dependencies_metadata["required"] == []

    # ------------------------------------------------------------------
    # Idempotency: second run → 0 created, some updated (or 0)
    # ------------------------------------------------------------------

    def test_seed_is_idempotent_no_extra_rows(self, db):
        """Running the seeder twice does not create duplicate rows."""
        seed_k8s_builtin_modules(db)
        seed_k8s_builtin_modules(db)

        for entry in K8S_BUILTIN_MODULES:
            count = (
                db.query(ModuleLibrary)
                .filter(ModuleLibrary.path == entry["path"])
                .count()
            )
            assert count == 1, f"Duplicate row for {entry['path']}"

    def test_seed_second_run_returns_zero_created(self, db):
        """On a second run with no changes, created count is 0."""
        seed_k8s_builtin_modules(db)
        created, _updated = seed_k8s_builtin_modules(db)
        assert created == 0

    # ------------------------------------------------------------------
    # Update path: stale row gets refreshed
    # ------------------------------------------------------------------

    def test_seed_updates_stale_name(self, db):
        """A row with a stale name is updated to match the catalog."""
        # Insert a stale row manually
        stale = ModuleLibrary(
            path="k8s/bnk-cert-issuer",
            name="Old Stale Name",
            category="k8s",
            git_source="builtin://k8s/bnk-cert-issuer",
            version="0.0.1",
            engine_type="kubernetes",
            is_active=True,
            is_official=False,
            is_tested=False,
        )
        db.add(stale)
        db.commit()

        created, updated = seed_k8s_builtin_modules(db)

        # The bnk-cert-issuer row was stale — it should be counted as updated.
        # The other 3 paths didn't exist yet — created.
        assert created == 3
        assert updated == 1

        row = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "k8s/bnk-cert-issuer"
        ).first()
        assert row.name == "BNK Cert Issuer"
        assert row.is_official is True
        assert row.is_tested is True
        assert row.version == "1.0.0"

    # ------------------------------------------------------------------
    # Engine dispatch: execution_engine resolves to kubernetes-direct
    # ------------------------------------------------------------------

    def test_seeded_modules_resolve_kubernetes_direct_via_capabilities(self, db):
        """After seeding, get_execution_engine() returns 'kubernetes-direct' for all k8s modules.

        This is the critical dispatch check: engine_router routes to KubernetesEngine
        only when execution_engine is in {'kubernetes-direct', 'operator'}.
        Without module_source_kind='builtin' AND execution_engine='kubernetes-direct'
        in the seeded row, get_execution_engine() previously fell through to returning
        the raw 'kubernetes' string which is NOT in the dispatch set, causing k8s
        modules to be routed to OpenTofuEngine instead.
        """
        seed_k8s_builtin_modules(db)

        for entry in K8S_BUILTIN_MODULES:
            row = db.query(ModuleLibrary).filter(
                ModuleLibrary.path == entry["path"]
            ).first()
            assert row is not None, f"Missing row for {entry['path']}"
            resolved = get_execution_engine(row)
            assert resolved == "kubernetes-direct", (
                f"{entry['path']}: expected 'kubernetes-direct', got '{resolved}'"
            )

    def test_seeded_modules_upsert_repairs_missing_execution_metadata(self, db):
        """Running seeder on a row that lacks execution metadata fills in the fields."""
        # Insert a legacy-style row with only engine_type (pre-fix state)
        legacy = ModuleLibrary(
            path="k8s/network-setup",
            name="Old Name",
            category="k8s",
            git_source="builtin://k8s/network-setup",
            version="0.9.0",
            engine_type="kubernetes",
            # Deliberately omit: module_source_kind, execution_engine, deploy_model
            is_active=True,
            is_official=True,
            is_tested=True,
        )
        db.add(legacy)
        db.commit()

        seed_k8s_builtin_modules(db)

        row = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "k8s/network-setup"
        ).first()
        assert row.module_source_kind == "builtin"
        assert row.execution_engine == "kubernetes-direct"
        assert row.deploy_model == "kubernetes"
        assert get_execution_engine(row) == "kubernetes-direct"

    # ------------------------------------------------------------------
    # Return values
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Catalog sync preservation: don't clobber enriched fields
    # ------------------------------------------------------------------

    def test_seeder_preserves_catalog_enriched_fields_when_pack_manifest_populated(self, db):
        """When catalog sync has enriched a row (pack_manifest populated),
        the seeder must NOT overwrite module_source_kind, deploy_model,
        or git_source back to the pre-sync defaults.

        Regression: every container restart was clobbering these fields,
        causing is_catalog_k8s_context() to return False for k8s/bnk-cert-issuer,
        which broke output collection and all downstream modules.
        """
        # Simulate a row that catalog sync has already enriched
        enriched = ModuleLibrary(
            path="k8s/bnk-cert-issuer",
            name="Old Name",
            category="k8s",
            git_source="git::https://example.com//k8s/bnk-cert-issuer?ref=main",
            version="1.0.0",
            engine_type="kubernetes",
            module_source_kind="git_catalog",
            execution_engine="kubernetes-direct",
            deploy_model="manifests",
            pack_manifest={"module": {"name": "BNK Cert Issuer"}, "outputs": {"key_outputs": []}},
            is_active=True,
            is_official=True,
            is_tested=True,
            dependencies_metadata={"required": [{"module": "k8s/cert-manager"}], "optional": []},
        )
        db.add(enriched)
        db.commit()

        seed_k8s_builtin_modules(db)

        row = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "k8s/bnk-cert-issuer"
        ).first()

        # These must NOT be clobbered back to seeder defaults
        assert row.module_source_kind == "git_catalog", (
            f"Expected 'git_catalog', got '{row.module_source_kind}' — seeder clobbered catalog sync"
        )
        assert row.deploy_model == "manifests", (
            f"Expected 'manifests', got '{row.deploy_model}' — seeder clobbered catalog sync"
        )
        assert row.git_source == "git::https://example.com//k8s/bnk-cert-issuer?ref=main", (
            "git_source was clobbered back to builtin:// — seeder should preserve catalog URL"
        )

        # But non-enriched fields SHOULD still be refreshed
        assert row.name == "BNK Cert Issuer"

    def test_seeder_overwrites_fields_when_no_pack_manifest(self, db):
        """When pack_manifest is empty/None (no catalog sync yet),
        the seeder should overwrite all fields as before."""
        stale = ModuleLibrary(
            path="k8s/bnk-cert-issuer",
            name="Old Name",
            category="k8s",
            git_source="some-old-source",
            version="0.0.1",
            engine_type="kubernetes",
            module_source_kind="unknown",
            execution_engine="kubernetes-direct",
            deploy_model="unknown",
            pack_manifest=None,
            is_active=True,
            is_official=True,
            is_tested=True,
        )
        db.add(stale)
        db.commit()

        seed_k8s_builtin_modules(db)

        row = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "k8s/bnk-cert-issuer"
        ).first()

        # Without pack_manifest, seeder should overwrite everything
        assert row.module_source_kind == "builtin"
        assert row.deploy_model == "kubernetes"
        assert row.git_source == "builtin://k8s/bnk-cert-issuer"

    # ------------------------------------------------------------------
    # Return values
    # ------------------------------------------------------------------

    def test_seed_returns_tuple_of_ints(self, db):
        """Return value is always (int, int)."""
        result = seed_k8s_builtin_modules(db)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(v, int) for v in result)
