"""
Tests for services.defaults_service — system defaults CRUD with real DB.

BC-013: Settings get/set/seed/check — real SQLite, no external mocks.
"""

import pytest

from models.system import ApplicationSetting
from services.defaults_service import (
    SYSTEM_DEFAULTS,
    _convert_value,
    check_required_configured,
    get_all_defaults,
    get_default,
    seed_defaults,
    set_default,
    set_defaults_batch,
)

# ---------------------------------------------------------------------------
# _convert_value (pure function)
# ---------------------------------------------------------------------------

class TestConvertValue:
    """Type conversion for stored string values."""

    def test_int_conversion(self):
        assert _convert_value("42", "int") == 42

    def test_float_conversion(self):
        assert _convert_value("3.14", "float") == 3.14

    def test_bool_true_variants(self):
        assert _convert_value("true", "bool") is True
        assert _convert_value("1", "bool") is True
        assert _convert_value("yes", "bool") is True
        assert _convert_value("True", "bool") is True

    def test_bool_false(self):
        assert _convert_value("false", "bool") is False
        assert _convert_value("no", "bool") is False

    def test_json_conversion(self):
        result = _convert_value('{"a": 1}', "json")
        assert result == {"a": 1}

    def test_string_passthrough(self):
        assert _convert_value("hello", "string") == "hello"

    def test_empty_value_returns_none(self):
        assert _convert_value("", "string") is None
        assert _convert_value(None, "int") is None


# ---------------------------------------------------------------------------
# seed_defaults
# ---------------------------------------------------------------------------

class TestSeedDefaults:
    """Seed default settings into DB on first run."""

    def test_seeds_all_defaults(self, db):
        count = seed_defaults(db)
        assert count == len(SYSTEM_DEFAULTS)

    def test_idempotent_second_run(self, db):
        seed_defaults(db)
        count2 = seed_defaults(db)
        assert count2 == 0  # No new settings seeded

    def test_seeded_values_readable(self, db):
        seed_defaults(db)
        value = get_default(db, "cloud.aws.default_region")
        assert value == "us-east-1"


# ---------------------------------------------------------------------------
# get_default
# ---------------------------------------------------------------------------

class TestGetDefault:
    """Read a single setting from DB."""

    def test_returns_none_when_not_found(self, db):
        result = get_default(db, "nonexistent.key")
        assert result is None

    def test_returns_seeded_value(self, db):
        seed_defaults(db)
        result = get_default(db, "opentofu.timeout.init")
        assert result == 300  # int conversion

    def test_returns_none_for_empty_value(self, db):
        seed_defaults(db)
        # git_token defaults to "" (empty)
        result = get_default(db, "module_library.git_token")
        assert result is None

    def test_type_conversion_int(self, db):
        seed_defaults(db)
        result = get_default(db, "execution.max_retries")
        assert isinstance(result, int)
        assert result == 3

    def test_type_conversion_string(self, db):
        seed_defaults(db)
        result = get_default(db, "cloud.aws.default_region")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# set_default
# ---------------------------------------------------------------------------

class TestSetDefault:
    """Write a single setting to DB."""

    def test_creates_new_setting(self, db):
        seed_defaults(db)
        result = set_default(db, "cloud.aws.default_region", "eu-west-1")
        assert result is True
        db.commit()
        assert get_default(db, "cloud.aws.default_region") == "eu-west-1"

    def test_updates_existing_setting(self, db):
        seed_defaults(db)
        set_default(db, "execution.max_retries", 5)
        db.commit()
        assert get_default(db, "execution.max_retries") == 5

    def test_unknown_key_returns_false(self, db):
        result = set_default(db, "unknown.key", "value")
        assert result is False

    def test_none_value_stored_as_empty(self, db):
        seed_defaults(db)
        set_default(db, "cloud.aws.default_region", None)
        db.commit()
        assert get_default(db, "cloud.aws.default_region") is None

    def test_creates_setting_if_not_seeded(self, db):
        """set_default can create records that weren't seeded yet."""
        result = set_default(db, "cloud.aws.default_region", "ap-southeast-1")
        assert result is True
        db.commit()
        assert get_default(db, "cloud.aws.default_region") == "ap-southeast-1"


# ---------------------------------------------------------------------------
# set_defaults_batch
# ---------------------------------------------------------------------------

class TestSetDefaultsBatch:
    """Batch update multiple settings."""

    def test_batch_update(self, db):
        seed_defaults(db)
        results = set_defaults_batch(db, {
            "cloud.aws.default_region": "ap-northeast-1",
            "execution.max_retries": 10,
        })
        db.commit()
        assert results["cloud.aws.default_region"] is True
        assert results["execution.max_retries"] is True
        assert get_default(db, "cloud.aws.default_region") == "ap-northeast-1"
        assert get_default(db, "execution.max_retries") == 10

    def test_batch_with_unknown_key(self, db):
        results = set_defaults_batch(db, {
            "cloud.aws.default_region": "us-west-2",
            "invalid.key": "value",
        })
        assert results["invalid.key"] is False
        assert results["cloud.aws.default_region"] is True


# ---------------------------------------------------------------------------
# get_all_defaults
# ---------------------------------------------------------------------------

class TestGetAllDefaults:
    """Retrieve all settings organized by category."""

    def test_returns_all_categories(self, db):
        seed_defaults(db)
        result = get_all_defaults(db)
        assert "module_library" in result
        assert "blueprint_library" in result
        assert "bnk" in result
        assert "cloud" in result
        assert "opentofu" in result
        assert "execution" in result

    def test_includes_metadata(self, db):
        seed_defaults(db)
        result = get_all_defaults(db)
        # Check a specific setting's metadata
        aws_region = result["cloud"]["aws_default_region"]
        assert aws_region["key"] == "cloud.aws.default_region"
        assert aws_region["value"] == "us-east-1"
        assert aws_region["value_type"] == "string"
        assert aws_region["is_configured"] is True
        assert "description" in aws_region

    def test_unconfigured_setting_marked(self, db):
        """Settings with empty values are marked as not configured."""
        seed_defaults(db)
        result = get_all_defaults(db)
        token = result["module_library"]["git_token"]
        # git_token defaults to "" which means not configured
        assert token["is_configured"] is False
        assert token["is_optional"] is True

    def test_empty_db_returns_all_keys(self, db):
        """Even with no DB records, all system defaults are listed."""
        result = get_all_defaults(db)
        # All keys should be present (with None values)
        total_settings = sum(len(cat) for cat in result.values())
        assert total_settings == len(SYSTEM_DEFAULTS)


# ---------------------------------------------------------------------------
# check_required_configured
# ---------------------------------------------------------------------------

class TestCheckRequiredConfigured:
    """Check if all required settings are configured."""

    def test_all_configured_after_seed(self, db):
        seed_defaults(db)
        result = check_required_configured(db)
        # After seeding, required settings should be configured (they have default values)
        # Optional ones (git_token, project.default_type) don't count
        assert result["all_configured"] is True
        assert result["missing"] == []

    def test_missing_when_empty_db(self, db):
        result = check_required_configured(db)
        assert result["all_configured"] is False
        assert len(result["missing"]) > 0

    def test_skips_optional_settings(self, db):
        """Optional settings are not reported as missing."""
        result = check_required_configured(db)
        missing_keys = [m["key"] for m in result["missing"]]
        # git_token and project.default_type are optional
        assert "module_library.git_token" not in missing_keys
        assert "project.default_type" not in missing_keys

    def test_count_fields(self, db):
        seed_defaults(db)
        result = check_required_configured(db)
        assert "total_required" in result
        assert "configured_count" in result
        assert result["configured_count"] == result["total_required"]


# ---------------------------------------------------------------------------
# SYSTEM_DEFAULTS constant
# ---------------------------------------------------------------------------

class TestSystemDefaults:
    """Validate the SYSTEM_DEFAULTS definition itself."""

    def test_all_have_required_fields(self):
        for key, defn in SYSTEM_DEFAULTS.items():
            assert "value" in defn, f"{key} missing 'value'"
            assert "value_type" in defn, f"{key} missing 'value_type'"
            assert "description" in defn, f"{key} missing 'description'"
            assert "category" in defn, f"{key} missing 'category'"

    def test_value_types_are_valid(self):
        valid_types = {"string", "int", "float", "bool", "json"}
        for key, defn in SYSTEM_DEFAULTS.items():
            assert defn["value_type"] in valid_types, f"{key} has invalid type: {defn['value_type']}"

    def test_categories_are_known(self):
        known = {"module_library", "blueprint_library", "project", "cloud", "opentofu", "execution", "system", "bnk", "container"}
        for key, defn in SYSTEM_DEFAULTS.items():
            assert defn["category"] in known, f"{key} has unknown category: {defn['category']}"

    def test_encrypted_bnk_default_is_stored_encrypted_and_masked_in_listing(self, db):
        seed_defaults(db)
        assert set_default(db, "bnk.far_pull_secret_default", "abc123") is True

        row = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "bnk.far_pull_secret_default"
        ).first()
        assert row is not None
        assert row.is_encrypted is True
        assert row.value != "abc123"

        assert get_default(db, "bnk.far_pull_secret_default") == "abc123"

        all_defaults = get_all_defaults(db)
        bnk_default = all_defaults["bnk"]["far_pull_secret_default"]
        assert bnk_default["is_encrypted"] is True
        assert bnk_default["raw_value"] == ""
        assert bnk_default["is_configured"] is True
