"""
Tests for services.module_source_service — module source CRUD and sync.

BC-010: ModuleSourceService — real DB, mock encryption/cache/sync for external calls.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, ConflictError, NotFoundError, ServiceError
from models import ModuleLibrary, ModuleSource
from services.module_source_service import ModuleSourceService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_source(db, **overrides):
    """Insert a ModuleSource row directly and return it."""
    defaults = dict(
        name="test-source",
        source_type="git",
        url="https://github.com/example/modules.git",
        branch="main",
        is_active=True,
        sync_status="pending",
    )
    defaults.update(overrides)
    src = ModuleSource(**defaults)
    db.add(src)
    db.flush()
    db.refresh(src)
    return src


def _source_data(**overrides):
    """Build a SimpleNamespace that looks like a Pydantic schema for create/update."""
    defaults = dict(
        name="new-source",
        source_type="git",
        url="https://github.com/example/repo.git",
        branch="main",
        git_ref=None,
        auth_type=None,
        auth_token=None,
        credential_type=None,
        credential_scope=None,
        credential_capabilities=None,
        credential_metadata=None,
        credential_expires_at=None,
        credential_last_rotated_at=None,
        credential_validation_status=None,
        credential_last_validated_at=None,
        credential_validation_error=None,
        auto_sync=False,
        sync_interval_hours=24,
        description="A test source",
        is_active=None,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _source_update_data(*, provided_fields: set[str], **values):
    """Build update payload with explicit model_fields_set semantics."""
    payload = _source_data(**values)
    payload.model_fields_set = provided_fields
    return payload


# ---------------------------------------------------------------------------
# list_sources
# ---------------------------------------------------------------------------

class TestListSources:
    """Listing module sources with optional filters."""

    def test_list_empty(self, db):
        svc = ModuleSourceService(db)
        assert svc.list_sources() == []

    def test_list_returns_all(self, db):
        _make_source(db, name="src-a")
        _make_source(db, name="src-b")
        svc = ModuleSourceService(db)
        result = svc.list_sources()
        assert len(result) == 2

    def test_filter_by_source_type(self, db):
        _make_source(db, name="git-src", source_type="git")
        _make_source(db, name="reg-src", source_type="registry")
        svc = ModuleSourceService(db)
        result = svc.list_sources(source_type="git")
        assert len(result) == 1
        assert result[0]["source_type"] == "git"

    def test_filter_by_is_active(self, db):
        _make_source(db, name="active", is_active=True)
        _make_source(db, name="inactive", is_active=False)
        svc = ModuleSourceService(db)
        result = svc.list_sources(is_active=True)
        assert len(result) == 1
        assert result[0]["name"] == "active"


# ---------------------------------------------------------------------------
# get_source
# ---------------------------------------------------------------------------

class TestGetSource:
    """Retrieving a single module source by ID."""

    def test_get_existing(self, db):
        src = _make_source(db)
        svc = ModuleSourceService(db)
        result = svc.get_source(src.id)
        assert result["id"] == src.id
        assert result["name"] == src.name

    def test_get_nonexistent_raises(self, db):
        svc = ModuleSourceService(db)
        with pytest.raises(NotFoundError):
            svc.get_source(99999)


# ---------------------------------------------------------------------------
# create_source
# ---------------------------------------------------------------------------

class TestCreateSource:
    """Creating new module sources."""

    @patch("services.module_source_service.BlueprintSyncService")
    @patch("services.module_sync_service.ModuleSyncService")
    def test_create_git_source_runs_initial_sync(self, mock_sync_class, mock_blueprint_sync_cls, db):
        svc = ModuleSourceService(db)
        mock_sync = MagicMock()
        mock_sync.sync_git_source.return_value = {"modules_found": 1, "modules_created": 1, "modules_updated": 0, "errors": []}
        mock_sync_class.return_value = mock_sync

        mock_blueprint_sync = MagicMock()
        mock_blueprint_sync.sync_git_source.return_value = {
            "blueprints_found": 1,
            "releases_created": 1,
            "releases_existing": 0,
            "releases_invalid": 0,
            "errors": [],
        }
        mock_blueprint_sync_cls.return_value = mock_blueprint_sync

        result = svc.create_source(_source_data())

        assert result["name"] == "new-source"
        mock_sync.sync_git_source.assert_called_once()
        mock_blueprint_sync.sync_git_source.assert_called_once()
        args, kwargs = mock_blueprint_sync.sync_git_source.call_args
        assert args[0].url == "https://github.com/example/repo.git"
        assert kwargs == {"sync_related_modules": False}

    @patch("services.module_source_service.BlueprintSyncService")
    @patch("services.module_sync_service.ModuleSyncService")
    def test_create_survives_failed_initial_sync_and_leaves_session_committable(
        self, mock_sync_class, mock_blueprint_sync_cls, db
    ):
        """#9: a failing initial sync must not poison the session.

        _auto_sync_blueprints_for_git_source flushes on the *same* session. When
        one of those writes failed, catching the exception unwound the Python
        stack but left SQLAlchemy in PendingRollback -- so the route's
        db.commit() raised PendingRollbackError, surfaced as a 500 that hid the
        real cause. The savepoint must contain that damage.
        """
        mock_sync_class.return_value.sync_git_source.return_value = {
            "modules_found": 0, "modules_created": 0, "modules_updated": 0, "errors": [],
        }
        # Fail the way the real bug did: a flush error inside the blueprint sync,
        # which is what poisons the session (not a plain Python exception).
        def _explode(source, **kwargs):
            db.add(ModuleLibrary())  # NOT NULL violations on flush
            db.flush()

        mock_blueprint_sync_cls.return_value.sync_git_source.side_effect = _explode

        svc = ModuleSourceService(db)
        result = svc.create_source(_source_data())

        # The source still exists ...
        assert result["name"] == "new-source"
        # ... the failure is reported instead of being swallowed to a warning ...
        assert result["sync_status"] == "failed"
        assert result["sync_error"]
        # ... and the session is usable, which is the actual bug.
        db.commit()
        assert db.query(ModuleSource).filter_by(name="new-source").one().sync_status == "failed"

    @patch("services.module_source_service.BlueprintSyncService")
    @patch("services.module_sync_service.ModuleSyncService")
    def test_create_records_sync_error_text(self, mock_sync_class, mock_blueprint_sync_cls, db):
        """The real cause must reach the caller, not just the log."""
        mock_sync_class.return_value.sync_git_source.return_value = {
            "modules_found": 0, "modules_created": 0, "modules_updated": 0, "errors": [],
        }
        mock_blueprint_sync_cls.return_value.sync_git_source.side_effect = RuntimeError(
            "manifest.yaml is not valid YAML"
        )

        result = ModuleSourceService(db).create_source(_source_data())

        assert result["sync_status"] == "failed"
        assert "manifest.yaml is not valid YAML" in result["sync_error"]
        db.commit()

    @patch("services.module_sync_service.ModuleSyncService")
    def test_create_registry_source_does_not_run_initial_git_sync(self, mock_sync_class, db):
        svc = ModuleSourceService(db)

        result = svc.create_source(
            _source_data(
                source_type="registry",
                url="https://registry.terraform.io/modules/hashicorp",
            )
        )

        assert result["source_type"] == "registry"
        mock_sync_class.return_value.sync_git_source.assert_not_called()

    @patch("services.module_sync_service.ModuleSyncService")
    @patch("services.module_source_service.BlueprintSyncService")
    @patch("services.module_source_service.encrypt_value", return_value="encrypted-tok")
    def test_create_with_auth_token(self, mock_encrypt, mock_blueprint_sync_cls, mock_sync_class, db):
        mock_sync = MagicMock()
        mock_sync.sync_git_source.return_value = {"modules_found": 0, "modules_created": 0, "modules_updated": 0, "errors": []}
        mock_sync_class.return_value = mock_sync
        mock_blueprint_sync = MagicMock()
        mock_blueprint_sync.sync_git_source.return_value = {
            "blueprints_found": 0,
            "releases_created": 0,
            "releases_existing": 0,
            "releases_invalid": 0,
            "errors": [],
        }
        mock_blueprint_sync_cls.return_value = mock_blueprint_sync
        svc = ModuleSourceService(db)
        data = _source_data(auth_token="my-secret-token")
        result = svc.create_source(data)
        assert result["name"] == "new-source"
        assert result["has_secret"] is True
        assert result["sync_status"] in {"pending", "success"}
        assert result["credential_type"] == "legacy_pat"
        mock_encrypt.assert_called_once_with("my-secret-token")

    def test_create_without_auth_token(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(auth_token=None)
        result = svc.create_source(data)
        assert result["has_secret"] is False
        assert result["credential_type"] == "none"

    def test_create_with_explicit_credential_type(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            credential_type="github_app",
            credential_scope=None,
            credential_capabilities=None,
            credential_metadata={
                "github_app": {
                    "app_id": "12345",
                    "installation_id": "67890",
                    "owner": "example-org",
                    "repository": "infra-modules",
                }
            },
            credential_validation_status="valid",
            auth_token=None,
        )
        result = svc.create_source(data)
        assert result["credential_type"] == "github_app"
        assert result["auth_type"] == "token"
        assert result["credential_scope"] == "github_app_installation:67890:repo:example-org/infra-modules"
        assert result["credential_capabilities"] == ["repo:read"]
        assert result["credential_metadata"]["provider"] == "github"
        assert result["credential_metadata"]["github_app"]["app_id"] == "12345"
        assert result["credential_validation_status"] == "valid"

    def test_create_with_gitlab_project_token_metadata_derives_scope(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            url="https://gitlab.example.com/team/platform/modules.git",
            credential_type="gitlab_project_token",
            credential_scope="ignored",
            credential_capabilities=None,
            credential_metadata={
                "gitlab": {
                    "api_base_url": "https://gitlab.example.com/api/v4",
                    "project_path": "team/platform/modules",
                }
            },
            auth_token=None,
        )
        result = svc.create_source(data)
        assert result["credential_type"] == "gitlab_project_token"
        assert result["auth_type"] == "token"
        assert result["credential_scope"] == "gitlab_project:gitlab.example.com:repo:team/platform/modules"
        assert result["credential_capabilities"] == ["repo:read"]
        assert result["credential_metadata"]["provider"] == "gitlab"
        assert result["credential_metadata"]["gitlab"]["token_class"] == "project_access_token"

    def test_create_gitlab_group_token_requires_shared_scope_intent(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            url="https://gitlab.example.com/team/platform/modules.git",
            credential_type="gitlab_group_token",
            credential_metadata={
                "gitlab": {
                    "group_path": "team/platform",
                    "project_path": "team/platform/modules",
                }
            },
        )
        with pytest.raises(BadRequestError, match="shared_scope_intent"):
            svc.create_source(data)

    def test_create_with_gitlab_deploy_token_requires_username(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            url="https://gitlab.example.com/team/platform/modules.git",
            credential_type="gitlab_deploy_token",
            credential_metadata={
                "gitlab": {
                    "project_path": "team/platform/modules",
                }
            },
        )
        with pytest.raises(BadRequestError, match="deploy_username"):
            svc.create_source(data)

    def test_create_oauth_token_normalizes_metadata_scope_and_non_default_recommendation(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            url="https://github.com/example-org/infra-modules.git",
            credential_type="oauth_token",
            credential_metadata={
                "oauth": {
                    "provider": "github",
                    "principal": "alice@example.com",
                    "issuer": "https://idp.example.com",
                    "scopes": "repo read:org",
                }
            },
            credential_scope=None,
            credential_capabilities=None,
        )

        result = svc.create_source(data)

        assert result["credential_type"] == "oauth_token"
        assert result["auth_type"] == "token"
        assert result["credential_scope"] == "oauth:github:repo:example-org/infra-modules"
        assert result["credential_capabilities"] == ["repo:read"]
        assert result["credential_metadata"]["provider"] == "github"
        assert result["credential_metadata"]["oauth"]["token_class"] == "oauth_user_token"
        assert result["credential_metadata"]["oauth"]["supported_choice"] is True
        assert result["credential_metadata"]["oauth"]["recommended_default"] is False
        assert result["credential_metadata"]["oauth"]["recommended_default_credential_types"] == [
            "github_app",
            "ssh_deploy_key",
        ]
        assert result["credential_recommendation"]["supported"] is True
        assert result["credential_recommendation"]["is_recommended_default"] is False
        assert result["credential_recommendation"]["recommendation_tier"] == "supported_non_default"

    def test_create_oauth_token_rejects_unsupported_provider(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            url="https://bitbucket.example.com/team/infra.git",
            credential_type="oauth_token",
            credential_metadata={
                "oauth": {
                    "provider": "bitbucket",
                    "principal": "alice@example.com",
                    "scopes": ["repo:read"],
                }
            },
        )
        with pytest.raises(BadRequestError, match="not supported"):
            svc.create_source(data)

    def test_create_oauth_token_requires_provider_principal_and_scopes(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            url="https://github.com/example-org/infra-modules.git",
            credential_type="oauth_token",
            credential_metadata={
                "oauth": {
                    "provider": "github",
                    "issuer": "https://idp.example.com",
                }
            },
        )
        with pytest.raises(BadRequestError, match="oauth.principal"):
            svc.create_source(data)

    def test_create_github_app_missing_required_metadata_raises(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            credential_type="github_app",
            credential_metadata={"github_app": {"app_id": "12345"}},
        )
        with pytest.raises(BadRequestError, match="Missing GitHub App credential metadata"):
            svc.create_source(data)

    def test_create_invalid_credential_type_raises(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(credential_type="invalid_credential")
        with pytest.raises(BadRequestError, match="credential_type"):
            svc.create_source(data)

    def test_create_invalid_validation_status_raises(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(credential_validation_status="not_a_status")
        with pytest.raises(BadRequestError, match="credential_validation_status"):
            svc.create_source(data)

    def test_create_duplicate_name_raises(self, db):
        _make_source(db, name="dupe")
        svc = ModuleSourceService(db)
        data = _source_data(name="dupe")
        with pytest.raises(ConflictError):
            svc.create_source(data)

    def test_create_invalid_source_type_raises(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(source_type="ftp")
        with pytest.raises(BadRequestError, match="source_type"):
            svc.create_source(data)

    def test_create_ssh_deploy_key_requires_known_hosts(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            url="ssh://git@git.internal.example.com/team/modules.git",
            credential_type="ssh_deploy_key",
            credential_metadata={"ssh": {}},
        )
        with pytest.raises(BadRequestError, match="known_hosts"):
            svc.create_source(data)

    def test_create_ssh_deploy_key_rejects_malformed_ssh_url(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            url="git@git.internal.example.com/team/modules.git",
            credential_type="ssh_deploy_key",
            credential_metadata={
                "ssh": {
                    "known_hosts": "git.internal.example.com ssh-ed25519 AAAAC3Nza...",
                }
            },
        )
        with pytest.raises(BadRequestError, match="SSH deploy-key sources require an SSH Git URL"):
            svc.create_source(data)

    def test_create_ssh_deploy_key_normalizes_metadata_scope_and_read_only_capability(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            url="git@git.internal.example.com:team/modules.git",
            credential_type="ssh_deploy_key",
            credential_metadata={
                "ssh": {
                    "known_hosts": [
                        "git.internal.example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...",
                    ]
                }
            },
            credential_capabilities=None,
        )

        result = svc.create_source(data)
        assert result["credential_type"] == "ssh_deploy_key"
        assert result["auth_type"] == "ssh"
        assert result["credential_scope"] == "ssh_host:git.internal.example.com:repo:team/modules.git"
        assert result["credential_capabilities"] == ["repo:read"]
        assert result["credential_metadata"]["provider"] == "ssh"
        assert result["credential_metadata"]["ssh"]["host_key_policy"] == "strict"
        assert result["credential_metadata"]["ssh"]["read_only"] is True


# ---------------------------------------------------------------------------
# update_source
# ---------------------------------------------------------------------------

class TestUpdateSource:
    """Updating existing module sources."""

    def test_update_name(self, db):
        src = _make_source(db, name="old-name")
        svc = ModuleSourceService(db)
        data = _source_data(name="new-name", url=None, branch=None, git_ref=None,
                            auth_type=None, auth_token=None, auto_sync=None,
                            sync_interval_hours=None, is_active=None, description=None)
        result = svc.update_source(src.id, data)
        assert result["name"] == "new-name"

    def test_update_duplicate_name_raises(self, db):
        _make_source(db, name="existing")
        src = _make_source(db, name="to-update")
        svc = ModuleSourceService(db)
        data = _source_data(name="existing", url=None, branch=None, git_ref=None,
                            auth_type=None, auth_token=None, auto_sync=None,
                            sync_interval_hours=None, is_active=None, description=None)
        with pytest.raises(ConflictError):
            svc.update_source(src.id, data)

    def test_update_builtin_raises(self, db):
        src = _make_source(db, name="builtin-src", source_type="builtin")
        svc = ModuleSourceService(db)
        data = _source_data(name="renamed")
        with pytest.raises(BadRequestError, match="built-in"):
            svc.update_source(src.id, data)

    @patch("services.module_source_service.encrypt_value", return_value="enc-new")
    def test_update_auth_token_encrypts(self, mock_encrypt, db):
        src = _make_source(db)
        svc = ModuleSourceService(db)
        data = _source_data(name=None, url=None, branch=None, git_ref=None,
                            auth_type=None, auth_token="new-token", auto_sync=None,
                            sync_interval_hours=None, is_active=None, description=None)
        result = svc.update_source(src.id, data)
        assert result["has_secret"] is True
        assert result["credential_type"] == "legacy_pat"
        mock_encrypt.assert_called_once_with("new-token")

    def test_update_credential_metadata_fields(self, db):
        src = _make_source(db)
        svc = ModuleSourceService(db)
        data = _source_data(
            name=None,
            url=None,
            branch=None,
            git_ref=None,
            auth_type=None,
            auth_token=None,
            auto_sync=None,
            sync_interval_hours=None,
            is_active=None,
            description=None,
            credential_type="oauth_token",
            credential_scope="repo:team/app",
            credential_capabilities=["repo:read", "metadata:read"],
            credential_metadata={
                "oauth": {
                    "provider": "github",
                    "principal": "alice@example.com",
                    "issuer": "internal-idp",
                    "scopes": ["repo"],
                }
            },
            credential_validation_status="invalid",
            credential_validation_error="expired",
        )
        result = svc.update_source(src.id, data)
        assert result["credential_type"] == "oauth_token"
        assert result["auth_type"] == "token"
        assert result["credential_scope"] == "repo:team/app"
        assert result["credential_capabilities"] == ["repo:read", "metadata:read"]
        assert result["credential_metadata"]["provider"] == "github"
        assert result["credential_metadata"]["oauth"]["issuer"] == "internal-idp"
        assert result["credential_metadata"]["oauth"]["token_class"] == "oauth_user_token"
        assert result["credential_metadata"]["oauth"]["recommended_default"] is False
        assert result["credential_recommendation"]["recommendation_tier"] == "supported_non_default"
        assert result["credential_validation_status"] == "invalid"
        assert result["credential_validation_error"] == "expired"

    def test_update_oauth_token_rejects_unsupported_provider(self, db):
        src = _make_source(db)
        svc = ModuleSourceService(db)
        data = _source_data(
            credential_type="oauth_token",
            credential_metadata={
                "oauth": {
                    "provider": "generic_git",
                    "principal": "alice@example.com",
                    "scopes": ["repo"],
                }
            },
        )
        with pytest.raises(BadRequestError, match="not supported"):
            svc.update_source(src.id, data)

    def test_update_oauth_token_requires_scopes(self, db):
        src = _make_source(db)
        svc = ModuleSourceService(db)
        data = _source_data(
            credential_type="oauth_token",
            credential_metadata={
                "oauth": {
                    "provider": "gitlab",
                    "principal": "alice@example.com",
                }
            },
        )
        with pytest.raises(BadRequestError, match="oauth.scopes"):
            svc.update_source(src.id, data)

    def test_non_oauth_source_does_not_emit_credential_recommendation(self, db):
        svc = ModuleSourceService(db)
        data = _source_data(
            credential_type="github_app",
            credential_scope=None,
            credential_capabilities=None,
            credential_metadata={
                "github_app": {
                    "app_id": "12345",
                    "installation_id": "67890",
                    "owner": "example-org",
                    "repository": "infra-modules",
                }
            },
            auth_token=None,
        )
        result = svc.create_source(data)
        assert result["credential_type"] == "github_app"
        assert result["credential_recommendation"] is None

    def test_update_github_app_metadata_derives_scope(self, db):
        src = _make_source(db)
        svc = ModuleSourceService(db)
        data = _source_data(
            name=None,
            url=None,
            branch=None,
            git_ref=None,
            auth_type=None,
            auth_token=None,
            auto_sync=None,
            sync_interval_hours=None,
            is_active=None,
            description=None,
            credential_type="github_app",
            credential_scope="ignored",
            credential_capabilities=None,
            credential_metadata={
                "github_app": {
                    "app_id": "12345",
                    "installation_id": "67890",
                    "owner": "example-org",
                    "repository": "infra-modules",
                }
            },
        )
        result = svc.update_source(src.id, data)
        assert result["credential_type"] == "github_app"
        assert result["credential_scope"] == "github_app_installation:67890:repo:example-org/infra-modules"
        assert result["credential_capabilities"] == ["repo:read"]

    def test_update_gitlab_group_token_metadata_derives_scope(self, db):
        src = _make_source(db, url="https://gitlab.example.com/team/platform/modules.git")
        svc = ModuleSourceService(db)
        data = _source_data(
            name=None,
            url=None,
            branch=None,
            git_ref=None,
            auth_type=None,
            auth_token=None,
            auto_sync=None,
            sync_interval_hours=None,
            is_active=None,
            description=None,
            credential_type="gitlab_group_token",
            credential_scope="ignored",
            credential_capabilities=None,
            credential_metadata={
                "gitlab": {
                    "group_path": "team/platform",
                    "project_path": "team/platform/modules",
                    "shared_scope_intent": True,
                }
            },
        )
        result = svc.update_source(src.id, data)
        assert result["credential_type"] == "gitlab_group_token"
        assert result["credential_scope"] == "gitlab_group:gitlab.example.com:group:team/platform:repo:team/platform/modules"
        assert result["credential_capabilities"] == ["repo:read"]
        assert result["credential_metadata"]["gitlab"]["token_class"] == "group_access_token"

    def test_get_legacy_token_source_maps_to_legacy_pat(self, db):
        src = _make_source(db, auth_type="token", auth_token_encrypted="enc-legacy")
        svc = ModuleSourceService(db)
        result = svc.get_source(src.id)
        assert result["credential_type"] == "legacy_pat"
        assert result["has_secret"] is True

    def test_update_credential_metadata_omitted_preserves_existing(self, db):
        src = _make_source(
            db,
            credential_metadata={"existing": "value"},
            credential_scope="repo:existing",
        )
        svc = ModuleSourceService(db)
        data = _source_update_data(provided_fields={"description"}, description="updated")

        result = svc.update_source(src.id, data)
        assert result["credential_metadata"] == {"existing": "value"}
        assert result["credential_scope"] == "repo:existing"

    def test_update_credential_metadata_explicit_null_clears_field(self, db):
        src = _make_source(
            db,
            credential_metadata={"existing": "value"},
            credential_scope="repo:existing",
        )
        svc = ModuleSourceService(db)
        data = _source_update_data(
            provided_fields={"credential_metadata", "credential_scope"},
            credential_metadata=None,
            credential_scope=None,
        )

        result = svc.update_source(src.id, data)
        assert result["credential_metadata"] is None
        assert result["credential_scope"] is None

    def test_update_invalid_validation_status_raises(self, db):
        src = _make_source(db)
        svc = ModuleSourceService(db)
        data = _source_update_data(
            provided_fields={"credential_validation_status"},
            credential_validation_status="bogus",
        )
        with pytest.raises(BadRequestError, match="credential_validation_status"):
            svc.update_source(src.id, data)

    def test_update_ssh_deploy_key_rejects_write_capability(self, db):
        src = _make_source(
            db,
            url="ssh://git@git.internal.example.com/team/modules.git",
            credential_type="ssh_deploy_key",
            auth_type="ssh",
            credential_metadata={
                "provider": "ssh",
                "ssh": {
                    "known_hosts": "git.internal.example.com ssh-ed25519 AAAAC3Nza...",
                },
            },
            credential_capabilities=["repo:read"],
        )
        svc = ModuleSourceService(db)
        data = _source_update_data(
            provided_fields={"credential_type", "credential_capabilities", "url"},
            credential_type="ssh_deploy_key",
            credential_capabilities=["repo:read", "repo:write"],
            url="ssh://git@git.internal.example.com/team/modules.git",
        )
        with pytest.raises(BadRequestError, match="read-only"):
            svc.update_source(src.id, data)


# ---------------------------------------------------------------------------
# delete_source
# ---------------------------------------------------------------------------

class TestDeleteSource:
    """Deleting module sources with in-use checks."""

    @patch("services.module_source_service.invalidate_cache")
    def test_delete_empty_source(self, mock_cache, db):
        src = _make_source(db)
        svc = ModuleSourceService(db)
        result = svc.delete_source(src.id)
        assert result["success"] is True
        assert "0 catalog modules" in result["message"]
        mock_cache.assert_called_once_with("module_library:*")

    @patch("services.module_source_service.invalidate_cache")
    def test_delete_with_unused_modules(self, mock_cache, db):
        src = _make_source(db)
        lib = ModuleLibrary(
            name="mod-a", category="net", path="m/a", provider="test",
            git_source="https://github.com/example/modules.git",
            module_source_id=src.id, is_active=True,
        )
        db.add(lib)
        db.flush()
        svc = ModuleSourceService(db)
        result = svc.delete_source(src.id)
        assert result["success"] is True
        assert "1 catalog modules" in result["message"]

    def test_delete_builtin_raises(self, db):
        src = _make_source(db, source_type="builtin")
        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match="built-in"):
            svc.delete_source(src.id)

    def test_delete_with_modules_in_use_raises(self, db, make_project, make_project_module):
        """Source whose modules are referenced by a project cannot be deleted."""
        src = _make_source(db, name="in-use-src")
        lib = ModuleLibrary(
            name="used-mod", category="net", path="m/used", provider="test",
            git_source="https://github.com/example/modules.git",
            module_source_id=src.id, is_active=True,
        )
        db.add(lib)
        db.flush()
        project = make_project()
        make_project_module(project=project, library_module=lib)
        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match="in use"):
            svc.delete_source(src.id)

    def test_delete_nonexistent_raises(self, db):
        svc = ModuleSourceService(db)
        with pytest.raises(NotFoundError):
            svc.delete_source(99999)


# ---------------------------------------------------------------------------
# list_source_modules
# ---------------------------------------------------------------------------

class TestListSourceModules:
    """Listing modules belonging to a source."""

    def test_list_source_modules(self, db):
        src = _make_source(db)
        for i in range(3):
            db.add(ModuleLibrary(
                name=f"mod-{i}", category="net", path=f"m/{i}", provider="test",
                git_source="https://github.com/example/modules.git",
                module_source_id=src.id, is_active=True,
            ))
        db.flush()
        svc = ModuleSourceService(db)
        result = svc.list_source_modules(src.id)
        assert result["module_count"] == 3
        assert result["source_name"] == src.name

    def test_list_source_modules_excludes_inactive(self, db):
        src = _make_source(db)
        db.add(ModuleLibrary(
            name="active-mod", category="net", path="m/a", provider="test",
            git_source="https://github.com/example/modules.git",
            module_source_id=src.id, is_active=True,
        ))
        db.add(ModuleLibrary(
            name="inactive-mod", category="net", path="m/b", provider="test",
            git_source="https://github.com/example/modules.git",
            module_source_id=src.id, is_active=False,
        ))
        db.flush()
        svc = ModuleSourceService(db)
        result = svc.list_source_modules(src.id)
        assert result["module_count"] == 1


# ---------------------------------------------------------------------------
# sync_source
# ---------------------------------------------------------------------------

class TestSyncSource:
    """Sync delegation to ModuleSyncService."""

    @patch("services.module_source_service.BlueprintSyncService")
    @patch("services.module_sync_service.ModuleSyncService")
    def test_sync_git_source(self, mock_sync_class, mock_blueprint_sync_cls, db):
        src = _make_source(db, source_type="git")
        mock_sync_svc = MagicMock()
        mock_sync_svc.sync_git_source.return_value = {"added": 2, "updated": 0}
        mock_sync_class.return_value = mock_sync_svc
        mock_blueprint_sync = MagicMock()
        mock_blueprint_sync.sync_git_source.return_value = {
            "blueprints_found": 1,
            "releases_created": 1,
            "releases_existing": 0,
            "releases_invalid": 0,
            "errors": [],
        }
        mock_blueprint_sync_cls.return_value = mock_blueprint_sync

        svc = ModuleSourceService(db)
        result = svc.sync_source(src.id)
        assert result["success"] is True
        assert result["results"]["added"] == 2
        assert result["results"]["updated"] == 0
        assert result["results"]["blueprint_auto_sync"]["results"]["blueprints_found"] == 1
        mock_sync_svc.sync_git_source.assert_called_once_with(src)
        mock_blueprint_sync.sync_git_source.assert_called_once()
        args, kwargs = mock_blueprint_sync.sync_git_source.call_args
        assert args[0].url == src.url
        assert kwargs == {"sync_related_modules": False}

    @patch("services.module_source_service.invalidate_cache")
    @patch("services.module_source_service.BlueprintSyncService")
    @patch("services.module_sync_service.ModuleSyncService")
    def test_sync_git_source_invalidates_module_library_cache_with_glob(
        self, mock_sync_class, mock_blueprint_sync_cls, mock_cache, db
    ):
        """Sync must bust the module-library list cache with a real glob.

        delete_pattern passes the string straight to Redis SCAN match=, so a
        bare "module_library:" prefix matches nothing and freshly-synced
        versions stay invisible until the 15-min TTL expires.
        """
        src = _make_source(db, source_type="git")
        mock_sync_class.return_value = MagicMock(sync_git_source=MagicMock(return_value={"added": 1}))
        mock_blueprint_sync_cls.return_value = MagicMock(
            sync_git_source=MagicMock(return_value={"errors": []})
        )

        svc = ModuleSourceService(db)
        svc.sync_source(src.id)

        mock_cache.assert_called_once_with("module_library:*")

    @patch("services.module_source_service.BlueprintSyncService")
    @patch("services.module_sync_service.ModuleSyncService")
    def test_sync_git_source_auto_creates_blueprint_source_once(self, mock_sync_class, mock_blueprint_sync_cls, db):
        src = _make_source(db, source_type="git", name="ibm-cluster", url="https://github.com/jgruberf5/bnk-forge-ibm-roks-cluster.git")
        mock_sync_class.return_value = MagicMock(sync_git_source=MagicMock(return_value={"added": 1}))
        mock_blueprint_sync = MagicMock()
        mock_blueprint_sync.sync_git_source.return_value = {
            "blueprints_found": 1,
            "releases_created": 1,
            "releases_existing": 0,
            "releases_invalid": 0,
            "errors": [],
        }
        mock_blueprint_sync_cls.return_value = mock_blueprint_sync

        svc = ModuleSourceService(db)
        first = svc.sync_source(src.id)
        second = svc.sync_source(src.id)

        from models import BlueprintSource

        blueprint_sources = db.query(BlueprintSource).filter(BlueprintSource.url == src.url).all()
        assert len(blueprint_sources) == 1
        assert first["results"]["blueprint_auto_sync"]["source_id"] == blueprint_sources[0].id
        assert second["results"]["blueprint_auto_sync"]["created"] is False

    def test_get_or_create_linked_blueprint_source_ref_mismatch_creates_distinct_source(self, db):
        src_release_23 = _make_source(
            db,
            source_type="git",
            name="shared-modules",
            url="https://github.com/example/catalog-shared.git",
            branch="release/2.3",
            git_ref="release/2.3",
        )

        svc = ModuleSourceService(db)
        first_source, first_created = svc._get_or_create_linked_blueprint_source(src_release_23)
        assert first_created is True

        src_release_24 = _make_source(
            db,
            source_type="git",
            name="shared-modules-24",
            url="https://github.com/example/catalog-shared.git",
            branch="release/2.4",
            git_ref="release/2.4",
        )
        second_source, second_created = svc._get_or_create_linked_blueprint_source(src_release_24)

        assert second_created is True
        assert second_source.id != first_source.id

    def test_sync_inactive_source_raises(self, db):
        src = _make_source(db, is_active=False)
        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match="inactive"):
            svc.sync_source(src.id)

    def test_sync_registry_source(self, db):
        # Registry-derived sources (legacy 'registry' or new terraform/opentofu/tfc/git
        # types) iterate their imported modules and refresh upstream version metadata.
        # With no modules attached, sync is a clean no-op.
        src = _make_source(db, source_type="registry")
        svc = ModuleSourceService(db)
        result = svc.sync_source(src.id)
        assert result["success"] is True
        assert result["results"] == {
            "modules_checked": 0,
            "updates_available": 0,
            "errors": [],
        }

    def test_sync_builtin_unsupported(self, db):
        src = _make_source(db, source_type="builtin")
        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match="not supported"):
            svc.sync_source(src.id)

    @patch("services.module_sync_service.ModuleSyncService")
    def test_sync_git_failure_raises_service_error(self, mock_sync_class, db):
        src = _make_source(db, source_type="git")
        mock_sync_svc = MagicMock()
        mock_sync_svc.sync_git_source.side_effect = RuntimeError("clone failed")
        mock_sync_class.return_value = mock_sync_svc

        svc = ModuleSourceService(db)
        with pytest.raises(ServiceError, match="Sync failed"):
            svc.sync_source(src.id)


class TestValidateSourceCredentials:
    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.GitHubAppAuthService.validate_repository_access")
    @patch("services.module_source_service.decrypt_value_or_none", return_value="---PRIVATE KEY---")
    def test_validate_github_app_success(self, _mock_decrypt, mock_validate, mock_audit, db):
        src = _make_source(
            db,
            credential_type="github_app",
            auth_type="token",
            auth_token_encrypted="enc-key",
            credential_metadata={
                "github_app": {
                    "app_id": "12345",
                    "installation_id": "67890",
                    "owner": "example-org",
                    "repository": "infra-modules",
                }
            },
        )
        mock_validate.return_value = {
            "provider": "github",
            "owner": "example-org",
            "repository": "infra-modules",
            "installation_id": "67890",
            "app_id": "12345",
            "repository_private": True,
            "repository_full_name": "example-org/infra-modules",
            "permissions": {"contents": "read"},
            "token_expires_at": "2030-01-01T00:00:00Z",
        }

        svc = ModuleSourceService(db)
        result = svc.validate_source_credentials(src.id)

        assert result["success"] is True
        assert result["credential_validation_status"] == "valid"
        assert result["provider"] == "github"
        assert result["credential_scope"] == "github_app_installation:67890:repo:example-org/infra-modules"
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.GitHubAppAuthService.validate_repository_access", side_effect=RuntimeError("forbidden"))
    @patch("services.module_source_service.decrypt_value_or_none", return_value="---PRIVATE KEY---")
    def test_validate_github_app_failure_updates_status_and_raises(self, _mock_decrypt, _mock_validate, mock_audit, db):
        src = _make_source(
            db,
            credential_type="github_app",
            auth_type="token",
            auth_token_encrypted="enc-key",
            credential_metadata={
                "github_app": {
                    "app_id": "12345",
                    "installation_id": "67890",
                    "owner": "example-org",
                    "repository": "infra-modules",
                }
            },
        )

        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match="GitHub App credential validation failed"):
            svc.validate_source_credentials(src.id)

        db.refresh(src)
        assert src.credential_validation_status == "invalid"
        assert src.credential_validation_error is not None
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.GitLabAuthService.validate_repository_access")
    @patch("services.module_source_service.decrypt_value_or_none", return_value="glpat-abc")
    def test_validate_gitlab_project_token_success(self, _mock_decrypt, mock_validate, mock_audit, db):
        src = _make_source(
            db,
            url="https://gitlab.example.com/team/platform/modules.git",
            credential_type="gitlab_project_token",
            auth_type="token",
            auth_token_encrypted="enc-token",
            credential_metadata={
                "gitlab": {
                    "project_path": "team/platform/modules",
                }
            },
        )
        mock_validate.return_value = {
            "provider": "gitlab",
            "host": "gitlab.example.com",
            "project_path": "team/platform/modules",
            "group_path": None,
            "project_id": 42,
            "project_visibility": "private",
            "default_branch": "main",
            "namespace_full_path": "team/platform",
            "token_class": "project_access_token",
        }

        svc = ModuleSourceService(db)
        result = svc.validate_source_credentials(src.id)

        assert result["success"] is True
        assert result["provider"] == "gitlab"
        assert result["credential_type"] == "gitlab_project_token"
        assert result["credential_validation_status"] == "valid"
        assert result["credential_scope"] == "gitlab_project:gitlab.example.com:repo:team/platform/modules"
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.GitLabAuthService.validate_repository_access", side_effect=RuntimeError("boom"))
    @patch("services.module_source_service.decrypt_value_or_none", return_value="glpat-abc")
    def test_validate_gitlab_unexpected_failure_sets_error_status(self, _mock_decrypt, _mock_validate, mock_audit, db):
        src = _make_source(
            db,
            url="https://gitlab.example.com/team/platform/modules.git",
            credential_type="gitlab_project_token",
            auth_type="token",
            auth_token_encrypted="enc-token",
            credential_metadata={
                "gitlab": {
                    "project_path": "team/platform/modules",
                }
            },
        )

        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match="GitLab credential validation failed"):
            svc.validate_source_credentials(src.id)

        db.refresh(src)
        assert src.credential_validation_status == "error"
        assert src.credential_validation_error is not None
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.subprocess.run")
    @patch("services.module_source_service.GitAuthService.build_git_environment")
    @patch("services.module_source_service.GitAuthService.resolve_for_module_source")
    def test_validate_ssh_deploy_key_success(
        self,
        mock_resolve,
        mock_build_env,
        mock_subprocess,
        mock_audit,
        db,
    ):
        src = _make_source(
            db,
            url="ssh://git@git.internal.example.com/team/modules.git",
            credential_type="ssh_deploy_key",
            auth_type="ssh",
            auth_token_encrypted="enc-ssh-key",
            credential_metadata={
                "provider": "ssh",
                "ssh": {
                    "known_hosts": "git.internal.example.com ssh-ed25519 AAAAC3Nza...",
                },
            },
        )
        mock_resolve.return_value = SimpleNamespace(secret="PRIVATE KEY")
        mock_build_env.return_value = ({"GIT_SSH": "/tmp/git_ssh.sh"}, lambda: None)
        mock_subprocess.return_value = MagicMock(returncode=0, stderr="", stdout="")

        svc = ModuleSourceService(db)
        result = svc.validate_source_credentials(src.id)

        assert result["success"] is True
        assert result["provider"] == "ssh"
        assert result["credential_type"] == "ssh_deploy_key"
        assert result["validation"]["host"] == "git.internal.example.com"
        assert result["validation"]["host_key_trust"] == "trusted"
        assert result["credential_validation_status"] == "valid"
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.subprocess.run")
    @patch("services.module_source_service.GitAuthService.build_git_environment")
    @patch("services.module_source_service.GitAuthService.resolve_for_module_source")
    def test_validate_ssh_deploy_key_host_key_failure_is_explicit(
        self,
        mock_resolve,
        mock_build_env,
        mock_subprocess,
        mock_audit,
        db,
    ):
        src = _make_source(
            db,
            url="git@git.internal.example.com:team/modules.git",
            credential_type="ssh_deploy_key",
            auth_type="ssh",
            auth_token_encrypted="enc-ssh-key",
            credential_metadata={
                "provider": "ssh",
                "ssh": {
                    "known_hosts": "git.internal.example.com ssh-ed25519 AAAAC3Nza...",
                },
            },
        )
        mock_resolve.return_value = SimpleNamespace(secret="PRIVATE KEY")
        mock_build_env.return_value = ({"GIT_SSH": "/tmp/git_ssh.sh"}, lambda: None)
        mock_subprocess.return_value = MagicMock(returncode=128, stderr="Host key verification failed.", stdout="")

        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match=r"\[ssh_host_key\]"):
            svc.validate_source_credentials(src.id)

        db.refresh(src)
        assert src.credential_validation_status == "invalid"
        assert "ssh_host_key" in (src.credential_validation_error or "")
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.subprocess.run")
    @patch("services.module_source_service.GitAuthService.build_git_environment")
    @patch("services.module_source_service.GitAuthService.resolve_for_module_source")
    def test_validate_ssh_deploy_key_connectivity_failure_is_explicit(
        self,
        mock_resolve,
        mock_build_env,
        mock_subprocess,
        mock_audit,
        db,
    ):
        src = _make_source(
            db,
            url="ssh://git@git.internal.example.com/team/modules.git",
            credential_type="ssh_deploy_key",
            auth_type="ssh",
            auth_token_encrypted="enc-ssh-key",
            credential_metadata={
                "provider": "ssh",
                "ssh": {
                    "known_hosts": "git.internal.example.com ssh-ed25519 AAAAC3Nza...",
                },
            },
        )
        mock_resolve.return_value = SimpleNamespace(secret="PRIVATE KEY")
        mock_build_env.return_value = ({"GIT_SSH": "/tmp/git_ssh.sh"}, lambda: None)
        mock_subprocess.return_value = MagicMock(returncode=128, stderr="ssh: Could not resolve host git.internal.example.com", stdout="")

        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match=r"\[connectivity_dns\]"):
            svc.validate_source_credentials(src.id)

        db.refresh(src)
        assert src.credential_validation_status == "invalid"
        assert "connectivity_dns" in (src.credential_validation_error or "")
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.subprocess.run")
    @patch("services.module_source_service.GitAuthService.build_git_environment")
    @patch("services.module_source_service.GitAuthService.resolve_for_module_source")
    def test_validate_ssh_deploy_key_repo_not_found_is_explicit(
        self,
        mock_resolve,
        mock_build_env,
        mock_subprocess,
        mock_audit,
        db,
    ):
        src = _make_source(
            db,
            url="ssh://git@git.internal.example.com/team/missing.git",
            credential_type="ssh_deploy_key",
            auth_type="ssh",
            auth_token_encrypted="enc-ssh-key",
            credential_metadata={
                "provider": "ssh",
                "ssh": {
                    "known_hosts": "git.internal.example.com ssh-ed25519 AAAAC3Nza...",
                },
            },
        )
        mock_resolve.return_value = SimpleNamespace(secret="PRIVATE KEY")
        mock_build_env.return_value = ({"GIT_SSH": "/tmp/git_ssh.sh"}, lambda: None)
        mock_subprocess.return_value = MagicMock(returncode=128, stderr="Repository not found", stdout="")

        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match=r"\[repo_or_ref_not_found\]"):
            svc.validate_source_credentials(src.id)

        db.refresh(src)
        assert src.credential_validation_status == "invalid"
        assert "repo_or_ref_not_found" in (src.credential_validation_error or "")
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.subprocess.run")
    @patch("services.module_source_service.GitAuthService.build_git_environment")
    @patch("services.module_source_service.GitAuthService.resolve_for_module_source")
    def test_validate_oauth_token_success(
        self,
        mock_resolve,
        mock_build_env,
        mock_subprocess,
        mock_audit,
        db,
    ):
        src = _make_source(
            db,
            url="https://github.com/example-org/infra-modules.git",
            credential_type="oauth_token",
            auth_type="token",
            auth_token_encrypted="enc-oauth-token",
            credential_metadata={
                "oauth": {
                    "provider": "github",
                    "issuer": "https://idp.example.com",
                    "principal": "alice@example.com",
                    "scopes": ["repo"],
                }
            },
        )
        mock_resolve.return_value = SimpleNamespace(secret="oauth-token")
        mock_build_env.return_value = ({"GIT_ASKPASS": "/tmp/askpass.sh"}, lambda: None)
        mock_subprocess.return_value = MagicMock(returncode=0, stderr="", stdout="")

        svc = ModuleSourceService(db)
        result = svc.validate_source_credentials(src.id)

        assert result["success"] is True
        assert result["provider"] == "github"
        assert result["credential_type"] == "oauth_token"
        assert result["validation"]["recommendation_tier"] == "supported_non_default"
        assert result["validation"]["recommended_default_credential_types"] == ["github_app", "ssh_deploy_key"]
        db.refresh(src)
        assert src.credential_validation_status == "valid"
        assert src.credential_metadata["oauth"]["recommended_default"] is False
        assert src.credential_scope == "oauth:github:repo:example-org/infra-modules"
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.subprocess.run")
    @patch("services.module_source_service.GitAuthService.build_git_environment")
    @patch("services.module_source_service.GitAuthService.resolve_for_module_source")
    def test_validate_oauth_token_scope_failure_surfaces_explicit_error_kind(
        self,
        mock_resolve,
        mock_build_env,
        mock_subprocess,
        mock_audit,
        db,
    ):
        src = _make_source(
            db,
            url="https://gitlab.example.com/team/repo.git",
            credential_type="oauth_token",
            auth_type="token",
            auth_token_encrypted="enc-oauth-token",
            credential_metadata={"oauth": {"principal": "alice@example.com"}},
        )
        mock_resolve.return_value = SimpleNamespace(secret="oauth-token")
        mock_build_env.return_value = ({"GIT_ASKPASS": "/tmp/askpass.sh"}, lambda: None)
        mock_subprocess.return_value = MagicMock(returncode=128, stderr="remote: HTTP Basic: Access denied", stdout="")

        # ensure metadata is sufficient to reach runtime auth classification path
        src.credential_metadata = {
            "oauth": {
                "provider": "gitlab",
                "principal": "alice@example.com",
                "scopes": ["read_repository"],
            }
        }

        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match=r"OAuth credential validation failed \[auth_failure\]"):
            svc.validate_source_credentials(src.id)

        db.refresh(src)
        assert src.credential_validation_status == "invalid"
        assert "auth_failure" in (src.credential_validation_error or "")
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.subprocess.run")
    @patch("services.module_source_service.GitAuthService.build_git_environment")
    @patch("services.module_source_service.GitAuthService.resolve_for_module_source")
    def test_validate_oauth_token_rejects_unsupported_provider_before_runtime(
        self,
        mock_resolve,
        mock_build_env,
        mock_subprocess,
        mock_audit,
        db,
    ):
        src = _make_source(
            db,
            url="https://bitbucket.example.com/team/repo.git",
            credential_type="oauth_token",
            auth_type="token",
            auth_token_encrypted="enc-oauth-token",
            credential_metadata={
                "oauth": {
                    "provider": "bitbucket",
                    "principal": "alice@example.com",
                    "scopes": ["repo"],
                }
            },
        )

        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match="not supported"):
            svc.validate_source_credentials(src.id)

        db.refresh(src)
        assert src.credential_validation_status == "invalid"
        mock_resolve.assert_not_called()
        mock_build_env.assert_not_called()
        mock_subprocess.assert_not_called()
        assert mock_audit.call_count == 1

    @patch("services.module_source_service.create_audit_log")
    @patch("services.module_source_service.subprocess.run")
    @patch("services.module_source_service.GitAuthService.build_git_environment")
    @patch("services.module_source_service.GitAuthService.resolve_for_module_source")
    def test_validate_oauth_token_rejects_insufficient_metadata_before_runtime(
        self,
        mock_resolve,
        mock_build_env,
        mock_subprocess,
        mock_audit,
        db,
    ):
        src = _make_source(
            db,
            url="https://github.com/example-org/infra-modules.git",
            credential_type="oauth_token",
            auth_type="token",
            auth_token_encrypted="enc-oauth-token",
            credential_metadata={
                "oauth": {
                    "provider": "github",
                    "principal": "alice@example.com",
                }
            },
        )

        svc = ModuleSourceService(db)
        with pytest.raises(BadRequestError, match="oauth.scopes"):
            svc.validate_source_credentials(src.id)

        db.refresh(src)
        assert src.credential_validation_status == "invalid"
        mock_resolve.assert_not_called()
        mock_build_env.assert_not_called()
        mock_subprocess.assert_not_called()
        assert mock_audit.call_count == 1
