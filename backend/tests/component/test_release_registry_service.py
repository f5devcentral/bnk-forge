"""
Component tests for ReleaseRegistryService (issue #217).

Tests prefix matching, range fallback, manifest fallback,
sync_from_oci, and list_releases.
"""

import pytest

from models.bnk_release import BnkRelease
from models.enums import ReleaseSourceType
from services.release_registry_service import GaInfo, ReleaseRegistryService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _seed(db, rows: list[dict]) -> list[BnkRelease]:
    out = []
    for row in rows:
        rel = BnkRelease(**row)
        db.add(rel)
        out.append(rel)
    db.flush()
    return out


GROUNDED_ROWS = [
    dict(
        ga_label="BNK 2.3 GA",
        product_line="BNK",
        manifest_version="2.3.0",
        flo_version_prefix="2.21",
        flo_version_min="2.21.0",
        flo_version_max="2.22.0",
        min_k8s="1.30",
        max_k8s="1.31",
        source_type=ReleaseSourceType.CLOUDDOCS,
        is_active=True,
    ),
    dict(
        ga_label="BNK 2.2 GA",
        product_line="BNK",
        manifest_version="2.2",
        flo_version_prefix="2.9",
        flo_version_min="2.0.0",
        flo_version_max="2.20.0",
        min_k8s="1.30",
        max_k8s="1.30",
        source_type=ReleaseSourceType.CLOUDDOCS,
        is_active=True,
    ),
    dict(
        ga_label="BNK 2.1 GA",
        product_line="BNK",
        manifest_version="2.1.1",
        flo_version_prefix="1.198",
        flo_version_min="1.198.0",
        flo_version_max="1.199.0",
        min_k8s="1.26",
        max_k8s="1.29",
        source_type=ReleaseSourceType.CLOUDDOCS,
        is_active=True,
    ),
]


# ---------------------------------------------------------------------------
# resolve_ga — prefix match
# ---------------------------------------------------------------------------

class TestResolveGaPrefixMatch:
    def test_bnk_23_flo_version(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        info = svc.resolve_ga(flo_version="2.21.13-0.0.28")
        assert info is not None
        assert info.label == "BNK 2.3 GA"
        assert info.min_k8s == "1.30"

    def test_bnk_22_flo_version(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        info = svc.resolve_ga(flo_version="2.9.27")
        assert info is not None
        assert info.label == "BNK 2.2 GA"

    def test_bnk_21_flo_version(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        info = svc.resolve_ga(flo_version="v1.198.4-0.1.36")
        assert info is not None
        assert info.label == "BNK 2.1 GA"

    def test_unknown_flo_version_returns_none(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        info = svc.resolve_ga(flo_version="99.0.0")
        assert info is None

    def test_inactive_row_not_matched(self, db):
        _seed(db, [
            dict(
                ga_label="BNK Old",
                product_line="BNK",
                flo_version_prefix="1.197",
                flo_version_min="1.197.0",
                flo_version_max="1.198.0",
                source_type=ReleaseSourceType.MANUAL,
                is_active=False,
            )
        ])
        svc = ReleaseRegistryService(db)
        info = svc.resolve_ga(flo_version="1.197.5")
        assert info is None


# ---------------------------------------------------------------------------
# resolve_ga — manifest_version fallback
# ---------------------------------------------------------------------------

class TestResolveGaManifestFallback:
    def test_manifest_version_match(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        info = svc.resolve_ga(manifest_version="2.3.0")
        assert info is not None
        assert info.label == "BNK 2.3 GA"

    def test_no_match_when_neither_given(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        info = svc.resolve_ga()
        assert info is None


# ---------------------------------------------------------------------------
# list_releases
# ---------------------------------------------------------------------------

class TestListReleases:
    def test_active_only_default(self, db):
        _seed(db, GROUNDED_ROWS)
        _seed(db, [dict(
            ga_label="BNK 1.9",
            product_line="BNK",
            source_type=ReleaseSourceType.MANUAL,
            is_active=False,
        )])
        svc = ReleaseRegistryService(db)
        rows = svc.list_releases(active_only=True)
        labels = [r.ga_label for r in rows]
        assert "BNK 1.9" not in labels
        assert len(rows) == len(GROUNDED_ROWS)

    def test_all_releases_when_active_false(self, db):
        _seed(db, GROUNDED_ROWS)
        _seed(db, [dict(
            ga_label="BNK 1.9",
            product_line="BNK",
            source_type=ReleaseSourceType.MANUAL,
            is_active=False,
        )])
        svc = ReleaseRegistryService(db)
        rows = svc.list_releases(active_only=False)
        assert len(rows) == len(GROUNDED_ROWS) + 1


# ---------------------------------------------------------------------------
# sync_from_oci
# ---------------------------------------------------------------------------

class TestSyncFromOci:
    def test_matched_tags_create_oci_rows(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        result = svc.sync_from_oci(["2.21.13-0.0.28", "2.9.27"])
        assert result["matched"] == 2
        assert result["upserted"] == 2
        assert result["unmatched"] == 0

    def test_unmatched_tags_counted(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        result = svc.sync_from_oci(["99.0.0-unknown"])
        assert result["unmatched"] == 1
        assert result["upserted"] == 0

    def test_duplicate_tag_not_upserted_twice(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        svc.sync_from_oci(["2.21.13-0.0.28"])
        db.flush()
        result = svc.sync_from_oci(["2.21.13-0.0.28"])
        # Second call: already recorded → upserted = 0
        assert result["upserted"] == 0

    def test_oci_rows_created_inactive(self, db):
        _seed(db, GROUNDED_ROWS)
        svc = ReleaseRegistryService(db)
        svc.sync_from_oci(["2.21.13-0.0.28"])
        db.flush()
        oci_row = db.query(BnkRelease).filter_by(
            source_type=ReleaseSourceType.OCI
        ).first()
        assert oci_row is not None
        assert oci_row.is_active is False  # observed rows start inactive


# ---------------------------------------------------------------------------
# _prefix_matches static helper
# ---------------------------------------------------------------------------

class TestPrefixMatches:
    def test_matches_2x_prefix(self):
        assert ReleaseRegistryService._prefix_matches("2.21.13-0.0.28", "2.21") is True

    def test_no_match_partial_number(self):
        # "2.2" should NOT match "2.21.13" (would be a false prefix match)
        assert ReleaseRegistryService._prefix_matches("2.21.13", "2.2") is False

    def test_matches_v_prefix_stripped(self):
        assert ReleaseRegistryService._prefix_matches("v1.198.4-0.1.36", "1.198") is True

    def test_no_cross_scheme_match(self):
        # v1.198 should not match "2.198.x"
        assert ReleaseRegistryService._prefix_matches("2.198.0", "1.198") is False
