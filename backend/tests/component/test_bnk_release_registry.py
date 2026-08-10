"""
Component tests for BnkRelease model + seed data (issue #217).

Verifies:
  - BnkRelease rows can be written and queried via SQLAlchemy ORM
  - The seeded grounded matrix rows match expected ga_label / flo_version_prefix
  - ReleaseSourceType enum values are valid
"""

import pytest

from models.bnk_release import BnkRelease
from models.enums import ReleaseSourceType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_release(db, ga_label="BNK 2.3 GA", flo_prefix="2.21", source=ReleaseSourceType.CLOUDDOCS):
    rel = BnkRelease(
        ga_label=ga_label,
        product_line="BNK",
        flo_version_prefix=flo_prefix,
        source_type=source,
    )
    db.add(rel)
    db.flush()
    return rel


# ---------------------------------------------------------------------------
# ReleaseSourceType enum
# ---------------------------------------------------------------------------

class TestReleaseSourceType:
    def test_clouddocs_value(self):
        assert ReleaseSourceType.CLOUDDOCS == "clouddocs"

    def test_oci_value(self):
        assert ReleaseSourceType.OCI == "oci"

    def test_observed_value(self):
        assert ReleaseSourceType.OBSERVED == "observed"

    def test_manual_value(self):
        assert ReleaseSourceType.MANUAL == "manual"


# ---------------------------------------------------------------------------
# ORM round-trip
# ---------------------------------------------------------------------------

class TestBnkReleaseModel:
    def test_create_and_read(self, db):
        rel = _make_release(db, ga_label="BNK 2.3 GA", flo_prefix="2.21")
        db.commit()
        fetched = db.query(BnkRelease).filter_by(id=rel.id).one()
        assert fetched.ga_label == "BNK 2.3 GA"
        assert fetched.flo_version_prefix == "2.21"
        assert fetched.product_line == "BNK"
        assert fetched.is_active is True

    def test_defaults(self, db):
        rel = BnkRelease(ga_label="Test GA", source_type=ReleaseSourceType.MANUAL)
        db.add(rel)
        db.flush()
        assert rel.product_line == "BNK"
        assert rel.is_active is True
        assert rel.manifest_version is None
        assert rel.flo_version_min is None

    def test_source_type_stored_as_string(self, db):
        rel = _make_release(db, source=ReleaseSourceType.OBSERVED)
        db.commit()
        fetched = db.query(BnkRelease).filter_by(id=rel.id).one()
        assert fetched.source_type == "observed"

    def test_multiple_rows_queryable(self, db):
        _make_release(db, ga_label="BNK 2.2 GA", flo_prefix="2.9")
        _make_release(db, ga_label="BNK 2.1 GA", flo_prefix="1.198")
        db.commit()
        rows = db.query(BnkRelease).filter_by(product_line="BNK").all()
        labels = {r.ga_label for r in rows}
        assert "BNK 2.2 GA" in labels
        assert "BNK 2.1 GA" in labels

    def test_active_filter(self, db):
        active = _make_release(db, ga_label="BNK 2.3 GA")
        inactive = _make_release(db, ga_label="BNK 1.9")
        inactive.is_active = False
        db.commit()
        active_rows = db.query(BnkRelease).filter_by(is_active=True).all()
        active_labels = [r.ga_label for r in active_rows]
        assert active.ga_label in active_labels
        assert inactive.ga_label not in active_labels


# ---------------------------------------------------------------------------
# Seeded matrix shape validation
# These tests document the expected grounded rows from migration v2_132.
# They use pure Python (no DB) to validate the seed data dict structure.
# ---------------------------------------------------------------------------

class TestSeedDataStructure:
    """Validate the grounded matrix seed rows from the migration file."""

    def _load_seed_rows(self):
        # Import the migration module directly to access _SEED_ROWS
        import importlib.util
        import pathlib
        migration_path = pathlib.Path(
            __file__
        ).parent.parent.parent / "alembic" / "versions" / "v2_132_add_bnk_release_registry.py"
        spec = importlib.util.spec_from_file_location("v2_132", migration_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod._SEED_ROWS

    def test_five_seeded_rows(self):
        rows = self._load_seed_rows()
        assert len(rows) == 5, f"Expected 5 seed rows, got {len(rows)}"

    def test_bnk_23_row_present(self):
        rows = self._load_seed_rows()
        row = next((r for r in rows if "2.3" in r["ga_label"]), None)
        assert row is not None, "BNK 2.3 GA row missing from seed"
        assert row["flo_version_prefix"] == "2.21"
        assert row["manifest_version"] == "2.3.0"
        assert row["source_type"] == "clouddocs"

    def test_bnk_22_row_present(self):
        rows = self._load_seed_rows()
        row = next((r for r in rows if "2.2" in r["ga_label"]), None)
        assert row is not None, "BNK 2.2 GA row missing from seed"
        assert row["flo_version_prefix"] == "2.9"
        assert row["source_type"] == "clouddocs"

    def test_bnk_21_rows_present(self):
        rows = self._load_seed_rows()
        bnk21_rows = [r for r in rows if "2.1" in r["ga_label"]]
        assert len(bnk21_rows) >= 1, "Expected at least one BNK 2.1.x row"
        for row in bnk21_rows:
            assert row["flo_version_prefix"] == "1.198"

    def test_all_rows_have_source_url(self):
        rows = self._load_seed_rows()
        for row in rows:
            assert row.get("source_url"), f"Row {row['ga_label']} missing source_url"

    def test_all_rows_active_by_default(self):
        rows = self._load_seed_rows()
        for row in rows:
            assert row["is_active"] is True
