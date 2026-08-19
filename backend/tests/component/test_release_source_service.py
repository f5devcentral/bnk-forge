"""
Component tests for ReleaseSourceService (ADR-494).

Covers:
  - CRUD: create (credential encrypted), get, list, update, delete
  - Sync happy path: source_id + last_synced stamped on catalog rows;
    release_count / last_synced_at / sync_status updated on the source
  - Sync error path (bad YAML): sync_status="error" + sync_error set
  - Sync error path (mid-flush IntegrityError inside savepoint): savepoint
    rolls back cleanly; original exception propagates; error state persists
  - Route-level sync error path: POST bad YAML → error HTTP response;
    GET source confirms sync_status="error" and sync_error is populated
  - Regression: default-None source_id in DeployableReleaseRefreshService
    leaves existing rows without provenance stamping
"""

import pytest

from models.bnk_deployable_release import BnkDeployableRelease
from models.enums import ReleaseSourceKind
from models.release_source import ReleaseSource
from schemas.release_source import ReleaseSourceCreate, ReleaseSourceUpdate
from services.release_source_service import ReleaseSourceService

# ---------------------------------------------------------------------------
# Minimal BNK manifest YAML (two releases) used across sync tests.
# "charts/f5-lifecycle-operator" must be present — it is the FLO version key.
# ---------------------------------------------------------------------------

SAMPLE_MANIFEST_YAML = """
releases:
  - version: "2.3.1-3.2598.3-0.0.304"
    helm_charts:
      - name: "charts/f5-lifecycle-operator"
        version: "v2.21.13-0.0.53"
      - name: "charts/f5-cnf"
        version: "v1.10.0"
    docker_images:
      - name: "images/f5networks/f5-lifecycle-operator"
        version: "v2.21.13-0.0.53"
  - version: "2.2.0-1.1000.0-0.0.100"
    helm_charts:
      - name: "charts/f5-lifecycle-operator"
        version: "v2.9.5-0.0.10"
    docker_images: []
"""

INVALID_YAML = "not: valid: yaml: [\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(db, name="test-oci-source", kind=ReleaseSourceKind.OCI) -> ReleaseSource:
    svc = ReleaseSourceService(db)
    data = ReleaseSourceCreate(name=name, kind=kind, url="oci://repo.example.com/manifest")
    return svc.create_source(data)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestReleaseSourceCrud:
    @pytest.mark.component
    def test_create_source_stores_and_returns(self, db):
        svc = ReleaseSourceService(db)
        data = ReleaseSourceCreate(
            name="my-source",
            kind=ReleaseSourceKind.OCI,
            url="oci://repo.f5.com/manifest",
        )
        source = svc.create_source(data)
        assert source.id is not None
        assert source.name == "my-source"
        assert source.kind == "oci"
        assert source.sync_status == "idle"
        assert source.release_count == 0

    @pytest.mark.component
    def test_create_source_encrypts_credential(self, db):
        svc = ReleaseSourceService(db)
        data = ReleaseSourceCreate(
            name="cred-source",
            kind=ReleaseSourceKind.OCI,
            credential="my-secret-token",
        )
        source = svc.create_source(data)
        # Credential must NOT be stored as plaintext
        assert source.credential_encrypted is not None
        assert source.credential_encrypted != "my-secret-token"

    @pytest.mark.component
    def test_create_source_no_credential(self, db):
        source = _make_source(db, name="no-cred")
        assert source.credential_encrypted is None

    @pytest.mark.component
    def test_create_source_duplicate_name_raises(self, db):
        from core.errors import ConflictError

        _make_source(db, name="dup")
        svc = ReleaseSourceService(db)
        with pytest.raises(ConflictError):
            svc.create_source(ReleaseSourceCreate(name="dup", kind=ReleaseSourceKind.MANUAL))

    @pytest.mark.component
    def test_get_source_returns_row(self, db):
        source = _make_source(db, name="get-test")
        fetched = ReleaseSourceService(db).get_source(source.id)
        assert fetched.id == source.id
        assert fetched.name == "get-test"

    @pytest.mark.component
    def test_get_source_missing_raises(self, db):
        from core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            ReleaseSourceService(db).get_source(999_999)

    @pytest.mark.component
    def test_list_sources_all(self, db):
        _make_source(db, name="ls-a")
        _make_source(db, name="ls-b")
        sources = ReleaseSourceService(db).list_sources()
        names = {s.name for s in sources}
        assert {"ls-a", "ls-b"}.issubset(names)

    @pytest.mark.component
    def test_list_sources_active_only_filters_inactive(self, db):
        active = _make_source(db, name="active-one")
        inactive = _make_source(db, name="inactive-one")
        inactive.is_active = False
        db.flush()

        sources = ReleaseSourceService(db).list_sources(active_only=True)
        names = {s.name for s in sources}
        assert "active-one" in names
        assert "inactive-one" not in names

    @pytest.mark.component
    def test_update_source_changes_fields(self, db):
        source = _make_source(db, name="upd-test")
        svc = ReleaseSourceService(db)
        updated = svc.update_source(source.id, ReleaseSourceUpdate(description="hello"))
        assert updated.description == "hello"
        assert updated.name == "upd-test"  # unchanged

    @pytest.mark.component
    def test_update_source_re_encrypts_credential(self, db):
        source = _make_source(db, name="recrypt")
        svc = ReleaseSourceService(db)
        svc.update_source(source.id, ReleaseSourceUpdate(credential="new-token"))
        db.refresh(source)
        assert source.credential_encrypted is not None
        assert source.credential_encrypted != "new-token"

    @pytest.mark.component
    def test_update_source_clears_credential(self, db):
        svc = ReleaseSourceService(db)
        source = svc.create_source(
            ReleaseSourceCreate(name="clear-cred", kind=ReleaseSourceKind.OCI, credential="tok")
        )
        assert source.credential_encrypted is not None
        svc.update_source(source.id, ReleaseSourceUpdate(credential=None))
        db.refresh(source)
        assert source.credential_encrypted is None

    @pytest.mark.component
    def test_delete_source_removes_row(self, db):
        from core.errors import NotFoundError

        source = _make_source(db, name="del-test")
        sid = source.id
        ReleaseSourceService(db).delete_source(sid)
        db.commit()
        with pytest.raises(NotFoundError):
            ReleaseSourceService(db).get_source(sid)

    @pytest.mark.component
    def test_delete_source_nullifies_catalog_fk(self, db):
        """Catalog rows tied to the deleted source should have source_id set to NULL."""
        source = _make_source(db, name="del-fk")
        # Create a catalog row manually tied to this source
        row = BnkDeployableRelease(
            name="bnk-del-fk",
            display_name="BNK del-fk",
            is_default=False,
            is_active=True,
            source_type="manual",
            bnk_manifest_version="9.9.9",
            bnk_cr_kind="CNEInstance",
            flo_version="v9.9.9",
            k8s_version="",
            doca_version="",
            containerd_version="",
            runc_version="",
            calico_version="",
            cert_manager_version="",
            gateway_api_version="",
            multus_version="",
            sriov_version="",
            storage_class_type="local-path",
            storage_provisioner="rancher.io/local-path",
            source_id=source.id,
        )
        db.add(row)
        db.flush()

        ReleaseSourceService(db).delete_source(source.id)
        db.commit()

        db.refresh(row)
        assert row.source_id is None


# ---------------------------------------------------------------------------
# to_response helper
# ---------------------------------------------------------------------------


class TestToResponse:
    @pytest.mark.component
    def test_to_response_has_credential_true(self, db):
        from core.encryption import encrypt_value

        svc = ReleaseSourceService(db)
        source = svc.create_source(
            ReleaseSourceCreate(name="resp-cred", kind=ReleaseSourceKind.MANUAL, credential="x")
        )
        resp = svc.to_response(source)
        assert resp.has_credential is True

    @pytest.mark.component
    def test_to_response_has_credential_false(self, db):
        source = _make_source(db, name="resp-no-cred")
        resp = ReleaseSourceService.to_response(source)
        assert resp.has_credential is False


# ---------------------------------------------------------------------------
# Sync — happy path
# ---------------------------------------------------------------------------


class TestSyncSource:
    @pytest.mark.component
    def test_sync_inserts_catalog_rows_with_provenance(self, db):
        source = _make_source(db, name="sync-happy")
        svc = ReleaseSourceService(db)

        result = svc.sync_source(source.id, SAMPLE_MANIFEST_YAML)

        assert result["inserted"] == 2
        assert result["updated"] == 0

        # All inserted rows must carry source_id and last_synced
        rows = (
            db.query(BnkDeployableRelease)
            .filter(BnkDeployableRelease.source_id == source.id)
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.source_id == source.id
            assert row.last_synced is not None

    @pytest.mark.component
    def test_sync_updates_source_stats(self, db):
        source = _make_source(db, name="sync-stats")
        svc = ReleaseSourceService(db)

        svc.sync_source(source.id, SAMPLE_MANIFEST_YAML)
        db.refresh(source)

        assert source.sync_status == "success"
        assert source.sync_error is None
        assert source.last_synced_at is not None
        assert source.release_count == 2

    @pytest.mark.component
    def test_sync_idempotent_updates_existing_rows(self, db):
        source = _make_source(db, name="sync-idem")
        svc = ReleaseSourceService(db)

        svc.sync_source(source.id, SAMPLE_MANIFEST_YAML)
        result2 = svc.sync_source(source.id, SAMPLE_MANIFEST_YAML)

        # Second sync should update (not re-insert) the same rows
        assert result2["inserted"] == 0
        assert result2["updated"] == 2

        db.refresh(source)
        assert source.release_count == 2

    @pytest.mark.component
    def test_sync_stamps_last_synced_on_updated_rows(self, db):
        from datetime import UTC, datetime, timezone

        source = _make_source(db, name="sync-ts")
        svc = ReleaseSourceService(db)

        before = datetime.now(UTC).replace(tzinfo=None)  # SQLite returns naive datetimes
        svc.sync_source(source.id, SAMPLE_MANIFEST_YAML)

        rows = (
            db.query(BnkDeployableRelease)
            .filter(BnkDeployableRelease.source_id == source.id)
            .all()
        )
        for row in rows:
            # Strip tzinfo if present (SQLite returns naive; Postgres returns aware)
            last_synced = row.last_synced
            if last_synced.tzinfo is not None:
                last_synced = last_synced.replace(tzinfo=None)
            assert last_synced >= before


# ---------------------------------------------------------------------------
# Sync — error path
# ---------------------------------------------------------------------------


class TestSyncSourceError:
    @pytest.mark.component
    def test_sync_bad_yaml_sets_error_status(self, db):
        source = _make_source(db, name="sync-bad-yaml")
        svc = ReleaseSourceService(db)

        with pytest.raises(Exception):
            svc.sync_source(source.id, INVALID_YAML)

        db.refresh(source)
        assert source.sync_status == "error"
        assert source.sync_error is not None
        assert len(source.sync_error) > 0

    @pytest.mark.component
    def test_sync_missing_releases_key_sets_error(self, db):
        source = _make_source(db, name="sync-no-key")
        svc = ReleaseSourceService(db)

        with pytest.raises(Exception):
            svc.sync_source(source.id, "foo: bar\n")

        db.refresh(source)
        assert source.sync_status == "error"


# ---------------------------------------------------------------------------
# Regression: default-None source_id leaves rows without provenance
# ---------------------------------------------------------------------------


class TestRefreshServiceNoneSourceId:
    @pytest.mark.component
    def test_refresh_without_source_id_does_not_stamp(self, db):
        """Existing ADR-478 callers pass no source_id — rows must stay unlinked."""
        from services.bare_metal.deployable_release_refresh import DeployableReleaseRefreshService

        DeployableReleaseRefreshService(db).refresh_deployable_releases_from_oci(
            SAMPLE_MANIFEST_YAML
        )

        rows = db.query(BnkDeployableRelease).all()
        for row in rows:
            # Only check rows that were just inserted (no name starting from seed)
            if row.bnk_manifest_version in (
                "2.3.1-3.2598.3-0.0.304",
                "2.2.0-1.1000.0-0.0.100",
            ):
                assert row.source_id is None
                assert row.last_synced is None


# ---------------------------------------------------------------------------
# Sync — savepoint isolation (mid-flush IntegrityError)
# ---------------------------------------------------------------------------


class TestSyncSavepointIsolation:
    @pytest.mark.component
    def test_sync_midflush_integrityerror_isolates_savepoint(self, db):
        """IntegrityError inside begin_nested() rolls back only the savepoint.

        Arrange: pre-insert a BnkDeployableRelease with name="bnk-2.3.1" but a
        different bnk_manifest_version.  When sync_source runs, the refresh
        service cannot find the row by manifest_version → tries to INSERT a new
        row with the same name="bnk-2.3.1" → IntegrityError on flush inside the
        SAVEPOINT.

        Assert:
          (a) The original IntegrityError propagates (not a PendingRollbackError).
          (b) sync_status="error" and sync_error are persisted on the source
              (proves the savepoint isolated the failure and the error-stamp flush
              in the except block succeeded on the still-valid outer session).
        """
        from sqlalchemy.exc import IntegrityError

        source = _make_source(db, name="savepoint-test")

        # Pre-insert a row that will collide on `name` during the refresh INSERT.
        # Use a phantom manifest_version so the refresh service's lookup-by-version
        # returns nothing and falls through to INSERT (triggering the collision).
        blocker = BnkDeployableRelease(
            name="bnk-2.3.1",
            display_name="Blocker row",
            is_default=False,
            is_active=True,
            source_type="manual",
            bnk_manifest_version="phantom-version-999",
            bnk_cr_kind="CNEInstance",
            flo_version="v0.0.0",
            k8s_version="",
            doca_version="",
            containerd_version="",
            runc_version="",
            calico_version="",
            cert_manager_version="",
            gateway_api_version="",
            multus_version="",
            sriov_version="",
            storage_class_type="local-path",
            storage_provisioner="rancher.io/local-path",
        )
        db.add(blocker)
        db.flush()

        svc = ReleaseSourceService(db)

        # (a) The original IntegrityError propagates — not a PendingRollbackError.
        with pytest.raises(IntegrityError):
            svc.sync_source(source.id, SAMPLE_MANIFEST_YAML)

        # (b) Error state was persisted via the except-block flush on the outer session.
        db.refresh(source)
        assert source.sync_status == "error"
        assert source.sync_error is not None
        assert len(source.sync_error) > 0


# ---------------------------------------------------------------------------
# Route-level sync error path
# ---------------------------------------------------------------------------


class TestSyncSourceRoute:
    """Verify the route's `db.commit()` in the except path persists the error state.

    The service-level tests exercise sync_source() directly.  This class hits
    the HTTP routes so the route's commit-on-error behaviour is exercised and
    a subsequent GET reflects the persisted sync_status="error".
    """

    @pytest.mark.component
    def test_sync_bad_yaml_route_returns_error_and_persists_status(
        self, client, admin_headers, sample_user, db
    ):
        """POST bad manifest YAML returns an HTTP error; GET shows sync_status='error'."""
        # Create a release source via the API so it is committed through the route.
        create_resp = client.post(
            "/api/bare-metal/release-sources",
            json={"name": "route-err-src", "kind": "oci", "url": "oci://example.com/m"},
            headers=admin_headers,
        )
        assert create_resp.status_code == 200, create_resp.text
        source_id = create_resp.json()["id"]

        # POST bad YAML to the sync endpoint — expect an HTTP error (4xx / 5xx).
        sync_resp = client.post(
            f"/api/bare-metal/release-sources/{source_id}/sync",
            json={"manifest_yaml": INVALID_YAML},
            headers=admin_headers,
        )
        assert sync_resp.status_code >= 400, (
            f"Expected error status, got {sync_resp.status_code}: {sync_resp.text}"
        )

        # GET the source and confirm the error state was persisted.
        get_resp = client.get(
            f"/api/bare-metal/release-sources/{source_id}",
            headers=admin_headers,
        )
        assert get_resp.status_code == 200, get_resp.text
        data = get_resp.json()
        assert data["sync_status"] == "error", f"Expected 'error', got {data['sync_status']!r}"
        assert data["sync_error"], "sync_error should be populated after a failed sync"
