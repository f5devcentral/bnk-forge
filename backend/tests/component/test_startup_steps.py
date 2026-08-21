"""Tests for startup initialization steps."""

from unittest.mock import MagicMock, call, patch

import pytest

from startup_steps import seed_auth_step, seed_defaults_step, sync_module_catalog_step


@patch("database.get_db_context")
@patch("services.auth_service.seed_admin_user")
def test_seed_auth_step_fails_closed_on_unpersistable_credential(
    mock_seed_admin, mock_get_db_context
):
    """#186 (bonnyr-f5 r4): when a generated credential can't be written to the
    keys dir, seed_auth_step must REFUSE TO START (SystemExit) rather than fall
    back to logging the plaintext. SystemExit (BaseException) escapes main.py's
    best-effort `except Exception`, so the process actually halts — and the
    error message must NOT contain the secret.
    """
    from services.auth_service import GeneratedCredentialPersistError

    db = MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = db
    ctx.__exit__.return_value = False
    mock_get_db_context.return_value = ctx
    # The seed path raised because /app/keys was unwritable. The exception
    # carries NO plaintext (that is the whole point of the fail-closed design).
    mock_seed_admin.side_effect = GeneratedCredentialPersistError(
        "could not persist generated credential to /app/keys/initial_admin_password: "
        "[Errno 13] Permission denied"
    )

    with pytest.raises(SystemExit) as ei:
        seed_auth_step()

    # SystemExit, not swallowed; and the message never carries a secret.
    assert "Refusing to log" in str(ei.value)
    assert "Permission denied" in str(ei.value)


@patch("database.get_db_context")
@patch("services.defaults_service.seed_defaults")
@patch("services.blueprint_default_source_service.ensure_default_blueprint_source")
@patch("services.blueprint_sync_service.BlueprintSyncService")
def test_seed_defaults_step_syncs_default_blueprint_source(
    mock_sync_cls,
    mock_ensure_source,
    mock_seed_defaults,
    mock_get_db_context,
):
    db = MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = db
    ctx.__exit__.return_value = False
    mock_get_db_context.return_value = ctx
    mock_seed_defaults.return_value = 0

    source = MagicMock()
    source.is_active = True
    mock_ensure_source.return_value = source

    sync_service = MagicMock()
    mock_sync_cls.return_value = sync_service

    seed_defaults_step()

    mock_seed_defaults.assert_called_once_with(db)
    mock_ensure_source.assert_called_once_with(db)
    sync_service.sync_git_source.assert_called_once_with(source)
    db.commit.assert_called()


@patch("database.get_db_context")
@patch("services.defaults_service.seed_defaults")
@patch("services.blueprint_default_source_service.ensure_default_blueprint_source")
@patch("services.blueprint_sync_service.BlueprintSyncService")
def test_seed_defaults_step_skips_sync_when_no_default_blueprint_source(
    mock_sync_cls,
    mock_ensure_source,
    mock_seed_defaults,
    mock_get_db_context,
):
    db = MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = db
    ctx.__exit__.return_value = False
    mock_get_db_context.return_value = ctx
    mock_seed_defaults.return_value = 0
    mock_ensure_source.return_value = None

    seed_defaults_step()

    mock_sync_cls.assert_not_called()
    db.commit.assert_called()


# ============================================================================
# sync_module_catalog_step
# ============================================================================

def _make_db_context(db):
    """Return a context manager mock that yields *db*."""
    ctx = MagicMock()
    ctx.__enter__.return_value = db
    ctx.__exit__.return_value = False
    return ctx


def _mock_db_for_git_url(url: str | None):
    """Build a db mock that returns a git_url ApplicationSetting and no ModuleSource."""
    db = MagicMock()

    def _query_side_effect(model):
        q = MagicMock()

        class _Filter:
            def __init__(self):
                self._call_count = 0

            def filter(self, *args, **kwargs):
                self._call_count += 1
                inner = MagicMock()
                # first filter call → ApplicationSetting (git_url)
                if self._call_count == 1:
                    setting = MagicMock()
                    setting.value = url
                    inner.first.return_value = setting if url is not None else None
                else:
                    # ModuleSource lookup — no row by default
                    inner.first.return_value = None
                return inner

        f = _Filter()
        q.filter = f.filter
        return q

    db.query.side_effect = _query_side_effect
    # SQLite dialect (tests bypass Postgres advisory lock)
    db.bind.dialect.name = "sqlite"
    return db


@patch("core.cache.invalidate_cache")
@patch("database.get_db_context")
@patch("services.module_catalog_service.sync_module_catalog")
def test_sync_module_catalog_step_skips_when_git_url_is_empty(
    mock_sync,
    mock_get_db_context,
    mock_invalidate_cache,
):
    """Step returns cleanly when git_url setting exists but is empty."""
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    mock_get_db_context.return_value = _make_db_context(db)

    git_url_setting = MagicMock()
    git_url_setting.value = ""
    # Return empty setting for all queries (simplest mock)
    db.query.return_value.filter.return_value.first.return_value = git_url_setting

    sync_module_catalog_step()

    mock_sync.assert_not_called()
    mock_invalidate_cache.assert_not_called()


@patch("core.cache.invalidate_cache")
@patch("database.get_db_context")
@patch("services.module_catalog_service.sync_module_catalog")
def test_sync_module_catalog_step_skips_when_no_git_url_setting_exists(
    mock_sync,
    mock_get_db_context,
    mock_invalidate_cache,
):
    """Step returns cleanly when git_url setting row is missing entirely."""
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    mock_get_db_context.return_value = _make_db_context(db)
    db.query.return_value.filter.return_value.first.return_value = None

    sync_module_catalog_step()

    mock_sync.assert_not_called()
    mock_invalidate_cache.assert_not_called()


@patch("core.cache.invalidate_cache")
@patch("database.get_db_context")
@patch("services.module_catalog_service.sync_module_catalog")
def test_sync_module_catalog_step_skips_when_source_is_deactivated(
    mock_sync,
    mock_get_db_context,
    mock_invalidate_cache,
):
    """Step returns cleanly without calling sync when the official source is is_active=False."""
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    mock_get_db_context.return_value = _make_db_context(db)

    # First filter call → git_url setting
    git_url_setting = MagicMock()
    git_url_setting.value = "https://github.com/org/bnk-forge-modules.git"

    # Second filter call → deactivated ModuleSource
    deactivated_source = MagicMock()
    deactivated_source.is_active = False
    deactivated_source.name = "official-bnk-forge-modules"

    call_count = [0]

    def _filter_side_effect(*args, **kwargs):
        call_count[0] += 1
        inner = MagicMock()
        if call_count[0] == 1:
            inner.first.return_value = git_url_setting
        else:
            inner.first.return_value = deactivated_source
        return inner

    db.query.return_value.filter.side_effect = _filter_side_effect

    sync_module_catalog_step()

    mock_sync.assert_not_called()
    mock_invalidate_cache.assert_not_called()
    db.commit.assert_not_called()


@patch("core.cache.invalidate_cache")
@patch("database.get_db_context")
@patch("services.module_catalog_service.sync_module_catalog")
def test_sync_module_catalog_step_syncs_when_no_source_row_exists(
    mock_sync,
    mock_get_db_context,
    mock_invalidate_cache,
):
    """Step calls sync on a fresh install where no ModuleSource row exists yet."""
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    mock_get_db_context.return_value = _make_db_context(db)

    git_url_setting = MagicMock()
    git_url_setting.value = "https://github.com/org/bnk-forge-modules.git"

    call_count = [0]

    def _filter_side_effect(*args, **kwargs):
        call_count[0] += 1
        inner = MagicMock()
        if call_count[0] == 1:
            inner.first.return_value = git_url_setting
        else:
            # No ModuleSource row → fresh install
            inner.first.return_value = None
        return inner

    db.query.return_value.filter.side_effect = _filter_side_effect

    mock_sync.return_value = {
        "total_modules": 10,
        "created": 10,
        "updated": 0,
        "quarantined": 0,
        "quarantined_paths": [],
        "errors": [],
        "source_rows_repaired": 0,
        "synced_version": "1.0.0",
    }

    sync_module_catalog_step()

    mock_sync.assert_called_once_with(db, force=False)
    db.commit.assert_called_once()
    mock_invalidate_cache.assert_called_once_with("module_library:*")


@patch("core.cache.invalidate_cache")
@patch("database.get_db_context")
@patch("services.module_catalog_service.sync_module_catalog")
def test_sync_module_catalog_step_syncs_when_source_is_active(
    mock_sync,
    mock_get_db_context,
    mock_invalidate_cache,
):
    """Step calls sync when an active ModuleSource row exists."""
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    mock_get_db_context.return_value = _make_db_context(db)

    git_url_setting = MagicMock()
    git_url_setting.value = "https://github.com/org/bnk-forge-modules.git"

    active_source = MagicMock()
    active_source.is_active = True
    active_source.name = "official-bnk-forge-modules"

    call_count = [0]

    def _filter_side_effect(*args, **kwargs):
        call_count[0] += 1
        inner = MagicMock()
        if call_count[0] == 1:
            inner.first.return_value = git_url_setting
        else:
            inner.first.return_value = active_source
        return inner

    db.query.return_value.filter.side_effect = _filter_side_effect

    mock_sync.return_value = {
        "total_modules": 38,
        "created": 34,
        "updated": 4,
        "quarantined": 0,
        "quarantined_paths": [],
        "errors": [],
        "source_rows_repaired": 0,
        "synced_version": "2.2.0",
    }

    sync_module_catalog_step()

    mock_sync.assert_called_once_with(db, force=False)
    db.commit.assert_called_once()
    mock_invalidate_cache.assert_called_once_with("module_library:*")


@patch("core.cache.invalidate_cache")
@patch("database.get_db_context")
@patch("services.module_catalog_service.sync_module_catalog")
def test_sync_module_catalog_step_rolls_back_and_skips_cache_on_errors(
    mock_sync,
    mock_get_db_context,
    mock_invalidate_cache,
):
    """When stats contain errors, the step rolls back instead of committing and does NOT invalidate cache."""
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    mock_get_db_context.return_value = _make_db_context(db)

    git_url_setting = MagicMock()
    git_url_setting.value = "https://github.com/org/bnk-forge-modules.git"

    call_count = [0]

    def _filter_side_effect(*args, **kwargs):
        call_count[0] += 1
        inner = MagicMock()
        if call_count[0] == 1:
            inner.first.return_value = git_url_setting
        else:
            inner.first.return_value = None  # no ModuleSource row
        return inner

    db.query.return_value.filter.side_effect = _filter_side_effect

    mock_sync.return_value = {
        "total_modules": 5,
        "created": 3,
        "updated": 2,
        "quarantined": 0,
        "quarantined_paths": [],
        "errors": ["Error syncing module X: timeout"],
        "source_rows_repaired": 0,
    }

    sync_module_catalog_step()

    mock_sync.assert_called_once_with(db, force=False)
    db.rollback.assert_called_once()
    db.commit.assert_not_called()
    mock_invalidate_cache.assert_not_called()


@patch("core.cache.invalidate_cache")
@patch("database.get_db_context")
@patch("services.module_catalog_service.sync_module_catalog")
def test_sync_module_catalog_step_does_not_abort_on_sync_exception(
    mock_sync,
    mock_get_db_context,
    mock_invalidate_cache,
):
    """Step logs a warning and completes without raising when sync throws (e.g. network error).
    Cache must NOT be invalidated — the commit was never reached.
    """
    db = MagicMock()
    db.bind.dialect.name = "sqlite"
    mock_get_db_context.return_value = _make_db_context(db)

    git_url_setting = MagicMock()
    git_url_setting.value = "https://github.com/org/bnk-forge-modules.git"

    call_count = [0]

    def _filter_side_effect(*args, **kwargs):
        call_count[0] += 1
        inner = MagicMock()
        if call_count[0] == 1:
            inner.first.return_value = git_url_setting
        else:
            inner.first.return_value = None
        return inner

    db.query.return_value.filter.side_effect = _filter_side_effect

    mock_sync.side_effect = OSError("Network unreachable")

    # Must NOT raise — a fresh or offline box must still boot
    sync_module_catalog_step()

    mock_sync.assert_called_once_with(db, force=False)
    db.commit.assert_not_called()
    mock_invalidate_cache.assert_not_called()
