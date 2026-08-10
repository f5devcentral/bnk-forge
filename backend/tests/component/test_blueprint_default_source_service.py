"""Tests for default blueprint source seeding."""

from services.blueprint_default_source_service import ensure_default_blueprint_source, is_default_blueprint_source
from services.defaults_service import seed_defaults, set_default


class TestEnsureDefaultBlueprintSource:
    def test_skips_when_git_url_not_configured(self, db):
        seed_defaults(db)
        result = ensure_default_blueprint_source(db)
        assert result is None

    def test_creates_source_when_configured(self, db):
        seed_defaults(db)
        set_default(db, "blueprint_library.git_url", "https://github.com/example/blueprints.git")
        set_default(db, "blueprint_library.git_ref", "main")
        db.commit()

        source = ensure_default_blueprint_source(db)

        assert source is not None
        assert source.url == "https://github.com/example/blueprints.git"
        assert source.branch == "main"
        assert source.git_ref == "main"
        assert source.name == "official-blueprints-blueprints"

    def test_reuses_existing_source_and_updates_ref(self, db):
        seed_defaults(db)
        set_default(db, "blueprint_library.git_url", "https://github.com/example/blueprints.git")
        set_default(db, "blueprint_library.git_ref", "main")
        db.commit()

        created = ensure_default_blueprint_source(db)

        set_default(db, "blueprint_library.git_ref", "release/2.3")
        db.commit()

        reused = ensure_default_blueprint_source(db)
        assert reused is not None
        assert reused.id == created.id
        assert reused.branch == "release/2.3"
        assert reused.git_ref == "release/2.3"

    def test_detects_default_source(self, db):
        seed_defaults(db)
        set_default(db, "blueprint_library.git_url", "https://github.com/example/blueprints.git")
        set_default(db, "blueprint_library.git_ref", "main")
        db.commit()

        source = ensure_default_blueprint_source(db)
        assert source is not None
        assert is_default_blueprint_source(source, db) is True
