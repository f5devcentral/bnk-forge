"""Unit tests for catalog pruning (D-033 version retirement).

The interesting cases are the refusals. Deactivating is easy; what matters is
that a prune never removes something a deployment still depends on, and never
leaves a module path with no latest version.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.catalog_prune_service import prune_blueprint_source, prune_module_source


def _mod(rid, path, version, active=True, latest=False):
    m = MagicMock()
    m.id, m.path, m.version, m.is_active, m.is_latest = rid, path, version, active, latest
    return m


def _rel(rid, bp_id, version, active=True):
    r = MagicMock()
    r.id, r.blueprint_id, r.blueprint_version, r.is_active = rid, bp_id, version, active
    return r


def _db(rows, ref_count=0):
    """A Session whose module/release query returns rows and whose reference
    count query returns ref_count."""
    db = MagicMock()
    first_query = MagicMock()
    first_query.filter.return_value.all.return_value = rows
    ref_query = MagicMock()
    ref_query.filter.return_value.count.return_value = ref_count
    db.query.side_effect = lambda model: first_query if not hasattr(model, "_is_ref") else ref_query

    def q(model):
        name = getattr(model, "__name__", str(model))
        if name in ("ProjectModule", "StackInstance"):
            return ref_query
        return first_query
    db.query.side_effect = q
    return db


@pytest.mark.unit
class TestPruneModules:
    def test_keeps_newest_and_deactivates_the_rest(self):
        rows = [_mod(1, "harbor", "1.0.0"), _mod(2, "harbor", "2.0.0"), _mod(3, "harbor", "1.5.0")]
        db = _db(rows)
        with patch("services.catalog_prune_service.recompute_is_latest"):
            res = prune_module_source(db, 7, keep=1)
        by_ver = {i.version: i.action for i in res.items}
        assert by_ver["2.0.0"] == "kept"
        assert by_ver["1.5.0"] == "deactivated"
        assert by_ver["1.0.0"] == "deactivated"
        assert rows[1].is_active is True      # newest untouched
        assert rows[0].is_active is False

    def test_keep_n_retains_that_many(self):
        rows = [_mod(i, "harbor", f"{i}.0.0") for i in range(1, 5)]
        db = _db(rows)
        with patch("services.catalog_prune_service.recompute_is_latest"):
            res = prune_module_source(db, 7, keep=2)
        kept = sorted(i.version for i in res.items if i.action == "kept")
        assert kept == ["3.0.0", "4.0.0"]

    def test_dry_run_changes_nothing(self):
        rows = [_mod(1, "harbor", "1.0.0"), _mod(2, "harbor", "2.0.0")]
        db = _db(rows)
        with patch("services.catalog_prune_service.recompute_is_latest") as rc:
            res = prune_module_source(db, 7, keep=1, dry_run=True)
        assert any(i.action == "deactivated" for i in res.items)
        assert rows[0].is_active is True      # reported, not applied
        db.delete.assert_not_called()
        rc.assert_not_called()

    def test_delete_removes_only_unreferenced_versions(self):
        rows = [_mod(1, "harbor", "1.0.0"), _mod(2, "harbor", "2.0.0")]
        db = _db(rows, ref_count=0)
        with patch("services.catalog_prune_service.recompute_is_latest"):
            res = prune_module_source(db, 7, keep=1, delete=True)
        assert [i.action for i in res.items if i.version == "1.0.0"] == ["deleted"]
        db.delete.assert_called_once_with(rows[0])

    def test_a_version_with_a_project_is_left_completely_alone(self):
        """The guard that matters. Not deleted, and not even hidden — the catalog
        must not stop showing the version a running deployment is on."""
        rows = [_mod(1, "harbor", "1.0.0"), _mod(2, "harbor", "2.0.0")]
        db = _db(rows, ref_count=3)
        with patch("services.catalog_prune_service.recompute_is_latest"):
            res = prune_module_source(db, 7, keep=1, delete=True)
        item = next(i for i in res.items if i.version == "1.0.0")
        assert item.action == "in_use"
        assert "3 project module(s)" in item.reason
        db.delete.assert_not_called()
        assert rows[0].is_active is True

    def test_the_guard_applies_without_delete_too(self):
        """The reference check is not conditional on `delete`."""
        rows = [_mod(1, "harbor", "1.0.0"), _mod(2, "harbor", "2.0.0")]
        db = _db(rows, ref_count=1)
        with patch("services.catalog_prune_service.recompute_is_latest"):
            res = prune_module_source(db, 7, keep=1)          # deactivate mode
        assert next(i for i in res.items if i.version == "1.0.0").action == "in_use"
        assert rows[0].is_active is True

    def test_include_in_use_hides_but_still_never_deletes(self):
        rows = [_mod(1, "harbor", "1.0.0"), _mod(2, "harbor", "2.0.0")]
        db = _db(rows, ref_count=2)
        with patch("services.catalog_prune_service.recompute_is_latest"):
            res = prune_module_source(db, 7, keep=1, delete=True, include_in_use=True)
        item = next(i for i in res.items if i.version == "1.0.0")
        assert item.action == "deactivated"
        assert "hidden, not deleted" in item.reason
        db.delete.assert_not_called()
        assert rows[0].is_active is False

    def test_is_latest_is_recomputed_for_touched_paths(self):
        """Without this a path whose newest row was deactivated has no latest at
        all, and the module disappears instead of falling back."""
        rows = [_mod(1, "harbor", "1.0.0"), _mod(2, "harbor", "2.0.0")]
        db = _db(rows)
        with patch("services.catalog_prune_service.recompute_is_latest") as rc:
            prune_module_source(db, 7, keep=1)
        rc.assert_called_once_with(db, 7, "harbor")

    def test_each_path_is_pruned_independently(self):
        rows = [_mod(1, "harbor", "1.0.0"), _mod(2, "harbor", "2.0.0"), _mod(3, "flp", "9.0.0")]
        db = _db(rows)
        with patch("services.catalog_prune_service.recompute_is_latest"):
            res = prune_module_source(db, 7, keep=1)
        flp = [i for i in res.items if i.identity == "flp"]
        assert len(flp) == 1 and flp[0].action == "kept"   # sole version survives


@pytest.mark.unit
class TestPruneBlueprints:
    def test_keeps_newest_release_and_deactivates_the_rest(self):
        rows = [_rel(1, "ibm-harbor-registry", "1.0.0"), _rel(2, "ibm-harbor-registry", "4.2.0")]
        db = _db(rows)
        res = prune_blueprint_source(db, 3, keep=1)
        by_ver = {i.version: i.action for i in res.items}
        assert by_ver["4.2.0"] == "kept"
        assert by_ver["1.0.0"] == "deactivated"

    def test_a_deployed_release_is_left_completely_alone(self):
        """ON DELETE SET NULL means a delete would succeed and quietly strip the
        stack of what it was deployed from."""
        rows = [_rel(1, "ibm-harbor-registry", "1.0.0"), _rel(2, "ibm-harbor-registry", "4.2.0")]
        db = _db(rows, ref_count=1)
        res = prune_blueprint_source(db, 3, keep=1, delete=True)
        item = next(i for i in res.items if i.version == "1.0.0")
        assert item.action == "in_use"
        assert "stack instance" in item.reason
        db.delete.assert_not_called()
        assert rows[0].is_active is True

    def test_deployed_release_guard_applies_without_delete(self):
        rows = [_rel(1, "bp", "1.0.0"), _rel(2, "bp", "2.0.0")]
        db = _db(rows, ref_count=1)
        res = prune_blueprint_source(db, 3, keep=1)
        assert next(i for i in res.items if i.version == "1.0.0").action == "in_use"
        assert rows[0].is_active is True

    def test_counts_summarise_the_outcome(self):
        rows = [_rel(i, "bp", f"{i}.0.0") for i in range(1, 4)]
        db = _db(rows)
        out = prune_blueprint_source(db, 3, keep=1).as_dict()
        assert out["counts"]["kept"] == 1
        assert out["counts"]["deactivated"] == 2
        assert out["keep"] == 1 and out["source_id"] == 3
