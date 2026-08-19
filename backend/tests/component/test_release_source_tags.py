"""Component tests for ReleaseSourceService.list_available_tags and pull_tags (ADR-494).

All OCI/subprocess calls are mocked — no network access.

Covers:
  - list: in_catalog annotation, semver-desc sort, listing failure → non-empty list_error
  - pull: happy path (added), idempotent re-add (skipped), one-tag-fails-others-succeed,
    FLO-missing failure (failed with reason), summary shape
"""

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from models.enums import ReleaseSourceKind
from models.release_source import ReleaseSource
from schemas.release_source import ReleaseSourceCreate
from services.release_source_service import ReleaseSourceService

# ---------------------------------------------------------------------------
# Sample manifests
# ---------------------------------------------------------------------------

MANIFEST_2_2_1 = """
releases:
  - version: "2.2.1-3.2226.0-0.0.511"
    helm_charts:
      - name: "charts/f5-lifecycle-operator"
        version: "v2.9.5-0.0.10"
    docker_images: []
"""

MANIFEST_2_3_1 = """
releases:
  - version: "2.3.1-3.2598.3-0.0.304"
    helm_charts:
      - name: "charts/f5-lifecycle-operator"
        version: "v2.21.13-0.0.53"
    docker_images: []
"""

MANIFEST_NO_FLO = """
releases:
  - version: "0.0.1-no-flo"
    helm_charts:
      - name: "charts/other-chart"
        version: "v1.0.0"
    docker_images: []
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(db, name: str = "oci-src", kind: str = "oci") -> ReleaseSource:
    svc = ReleaseSourceService(db)
    payload = ReleaseSourceCreate(
        name=name,
        kind=ReleaseSourceKind.OCI,
        url="repo.f5.com",
        credential="c2VjcmV0",  # base64("secret") — stored raw
    )
    return svc.create_source(payload)


def _fake_session(tags: list[str] = None, manifest_map: dict[str, str] = None):
    """Return a context manager that yields a mock OciRegistrySession."""
    tags = tags or []
    manifest_map = manifest_map or {}

    class FakeSession:
        def list_tags(self):
            return list(tags)

        def pull_manifest_yaml(self, tag):
            if tag not in manifest_map:
                raise RuntimeError(f"No manifest for tag {tag!r}")
            return manifest_map[tag]

    @contextmanager
    def _ctx(source):
        yield FakeSession()

    return _ctx


# ---------------------------------------------------------------------------
# list_available_tags
# ---------------------------------------------------------------------------


class TestListAvailableTags:
    @pytest.mark.component
    def test_tags_sorted_semver_desc(self, db):
        source = _make_source(db, name="list-sort")
        raw_tags = [
            "2.2.1-3.2226.0-0.0.511",
            "2.3.1-3.2598.3-0.0.304",
            "2.2.0-1.1000.0-0.0.100",
        ]
        with patch(
            "services.release_source_service.registry_session",
            _fake_session(tags=raw_tags),
        ):
            result = ReleaseSourceService(db).list_available_tags(source.id)

        assert result.list_error is None
        returned_tags = [t.tag for t in result.tags]
        assert returned_tags == [
            "2.3.1-3.2598.3-0.0.304",
            "2.2.1-3.2226.0-0.0.511",
            "2.2.0-1.1000.0-0.0.100",
        ]

    @pytest.mark.component
    def test_in_catalog_false_for_absent_version(self, db):
        source = _make_source(db, name="list-absent")
        with patch(
            "services.release_source_service.registry_session",
            _fake_session(tags=["2.2.1-3.2226.0-0.0.511"]),
        ):
            result = ReleaseSourceService(db).list_available_tags(source.id)

        assert result.tags[0].in_catalog is False

    @pytest.mark.component
    def test_in_catalog_true_after_sync(self, db):
        source = _make_source(db, name="list-present")
        # First sync the tag into the catalog via pull.
        with patch(
            "services.release_source_service.registry_session",
            _fake_session(
                tags=["2.2.1-3.2226.0-0.0.511"],
                manifest_map={"2.2.1-3.2226.0-0.0.511": MANIFEST_2_2_1},
            ),
        ):
            ReleaseSourceService(db).pull_tags(
                source.id, ["2.2.1-3.2226.0-0.0.511"]
            )

        # Now list — should show in_catalog=True.
        with patch(
            "services.release_source_service.registry_session",
            _fake_session(tags=["2.2.1-3.2226.0-0.0.511"]),
        ):
            result = ReleaseSourceService(db).list_available_tags(source.id)

        assert result.tags[0].in_catalog is True

    @pytest.mark.component
    def test_listing_failure_returns_structured_error(self, db):
        source = _make_source(db, name="list-fail")

        @contextmanager
        def _failing_session(src):
            raise RuntimeError("registry unreachable")
            yield  # type: ignore[misc]  # unreachable — needed to satisfy contextmanager protocol

        with patch(
            "services.release_source_service.registry_session",
            _failing_session,
        ):
            result = ReleaseSourceService(db).list_available_tags(source.id)

        assert result.tags == []
        assert result.list_error is not None
        assert "Failed to list tags from registry" in result.list_error


# ---------------------------------------------------------------------------
# pull_tags
# ---------------------------------------------------------------------------


class TestPullTags:
    @pytest.mark.component
    def test_pull_new_tag_appears_in_added(self, db):
        source = _make_source(db, name="pull-added")
        with patch(
            "services.release_source_service.registry_session",
            _fake_session(
                tags=["2.2.1-3.2226.0-0.0.511"],
                manifest_map={"2.2.1-3.2226.0-0.0.511": MANIFEST_2_2_1},
            ),
        ):
            summary = ReleaseSourceService(db).pull_tags(
                source.id, ["2.2.1-3.2226.0-0.0.511"]
            )

        assert "2.2.1-3.2226.0-0.0.511" in summary.added
        assert summary.skipped == []
        assert summary.failed == []

    @pytest.mark.component
    def test_idempotent_repull_appears_in_skipped(self, db):
        source = _make_source(db, name="pull-idem")
        session_ctx = _fake_session(
            tags=["2.2.1-3.2226.0-0.0.511"],
            manifest_map={"2.2.1-3.2226.0-0.0.511": MANIFEST_2_2_1},
        )
        svc = ReleaseSourceService(db)
        with patch("services.release_source_service.registry_session", session_ctx):
            svc.pull_tags(source.id, ["2.2.1-3.2226.0-0.0.511"])
        with patch("services.release_source_service.registry_session", session_ctx):
            summary2 = svc.pull_tags(source.id, ["2.2.1-3.2226.0-0.0.511"])

        assert summary2.added == []
        assert "2.2.1-3.2226.0-0.0.511" in summary2.skipped
        assert summary2.failed == []

    @pytest.mark.component
    def test_flo_missing_tag_appears_in_failed(self, db):
        source = _make_source(db, name="pull-flo-missing")
        with patch(
            "services.release_source_service.registry_session",
            _fake_session(
                tags=["0.0.1-no-flo"],
                manifest_map={"0.0.1-no-flo": MANIFEST_NO_FLO},
            ),
        ):
            summary = ReleaseSourceService(db).pull_tags(source.id, ["0.0.1-no-flo"])

        assert summary.added == []
        assert summary.skipped == []
        assert len(summary.failed) == 1
        assert summary.failed[0].tag == "0.0.1-no-flo"
        assert "f5-lifecycle-operator" in summary.failed[0].reason

    @pytest.mark.component
    def test_one_tag_pull_fails_others_succeed(self, db):
        """A network failure on one tag leaves sync_status=success; others added."""
        source = _make_source(db, name="pull-partial")
        with patch(
            "services.release_source_service.registry_session",
            _fake_session(
                tags=["2.2.1-3.2226.0-0.0.511", "2.3.1-3.2598.3-0.0.304"],
                # Manifest for 2.2.1 but NOT for 2.3.1 → pull error on 2.3.1
                manifest_map={"2.2.1-3.2226.0-0.0.511": MANIFEST_2_2_1},
            ),
        ):
            summary = ReleaseSourceService(db).pull_tags(
                source.id, ["2.2.1-3.2226.0-0.0.511", "2.3.1-3.2598.3-0.0.304"]
            )

        assert "2.2.1-3.2226.0-0.0.511" in summary.added
        assert len(summary.failed) == 1
        assert summary.failed[0].tag == "2.3.1-3.2598.3-0.0.304"

        # Source sync_status should be success (partial batch, not whole-op failure).
        db.refresh(source)
        assert source.sync_status == "success"

    @pytest.mark.component
    def test_summary_shape_has_nested_failed_reason(self, db):
        """PullTagsSummary.failed must have a non-empty reason field (CT-012 shape)."""
        source = _make_source(db, name="pull-shape")
        with patch(
            "services.release_source_service.registry_session",
            _fake_session(
                tags=["0.0.1-no-flo"],
                manifest_map={"0.0.1-no-flo": MANIFEST_NO_FLO},
            ),
        ):
            summary = ReleaseSourceService(db).pull_tags(source.id, ["0.0.1-no-flo"])

        assert hasattr(summary, "added")
        assert hasattr(summary, "skipped")
        assert hasattr(summary, "failed")
        assert len(summary.failed) == 1
        ft = summary.failed[0]
        assert hasattr(ft, "tag")
        assert hasattr(ft, "reason")
        assert ft.reason  # non-empty

    @pytest.mark.component
    def test_source_stats_updated_after_pull(self, db):
        source = _make_source(db, name="pull-stats")
        with patch(
            "services.release_source_service.registry_session",
            _fake_session(
                tags=["2.2.1-3.2226.0-0.0.511"],
                manifest_map={"2.2.1-3.2226.0-0.0.511": MANIFEST_2_2_1},
            ),
        ):
            ReleaseSourceService(db).pull_tags(source.id, ["2.2.1-3.2226.0-0.0.511"])

        db.refresh(source)
        assert source.sync_status == "success"
        assert source.last_synced_at is not None
        assert source.release_count >= 1

    @pytest.mark.component
    def test_all_zero_result_lands_in_failed(self, db):
        """A manifest that parses cleanly but contains zero releases returns {inserted:0,updated:0,skipped:0}.
        Such a tag must land in failed (not silently dropped) — strict partition completeness."""
        source = _make_source(db, name="pull-zero")

        import unittest.mock as mock

        with patch(
            "services.release_source_service.registry_session",
            _fake_session(
                tags=["2.2.1-3.2226.0-0.0.511"],
                manifest_map={"2.2.1-3.2226.0-0.0.511": MANIFEST_2_2_1},
            ),
        ):
            with mock.patch(
                "services.bare_metal.deployable_release_refresh.DeployableReleaseRefreshService.refresh_deployable_releases_from_oci",
                return_value={"inserted": 0, "updated": 0, "skipped": 0},
            ):
                summary = ReleaseSourceService(db).pull_tags(
                    source.id, ["2.2.1-3.2226.0-0.0.511"]
                )

        assert summary.added == []
        assert summary.skipped == []
        assert len(summary.failed) == 1
        assert summary.failed[0].tag == "2.2.1-3.2226.0-0.0.511"
        assert "no releases found" in summary.failed[0].reason

    @pytest.mark.component
    def test_strict_partition_inserted_and_service_skipped_lands_in_added_only(self, db):
        """A tag whose refresh returns both inserted>0 and skipped>0 (FLO check)
        must land ONLY in added — the strict partition gives inserted precedence."""
        source = _make_source(db, name="pull-partition")

        # Mock refresh to return both inserted=1 and skipped=1 for the tag.
        # (This simulates a manifest with one FLO release and one non-FLO release;
        # the FLO release was inserted, the non-FLO release was service-skipped.)
        import unittest.mock as mock

        with patch(
            "services.release_source_service.registry_session",
            _fake_session(
                tags=["2.2.1-3.2226.0-0.0.511"],
                manifest_map={"2.2.1-3.2226.0-0.0.511": MANIFEST_2_2_1},
            ),
        ):
            with patch(
                "services.bare_metal.deployable_release_refresh.DeployableReleaseRefreshService.refresh_deployable_releases_from_oci",
                return_value={"inserted": 1, "updated": 0, "skipped": 1},
            ):
                summary = ReleaseSourceService(db).pull_tags(
                    source.id, ["2.2.1-3.2226.0-0.0.511"]
                )

        assert "2.2.1-3.2226.0-0.0.511" in summary.added
        assert summary.skipped == []
        assert summary.failed == []
