"""Unit tests for blueprint_catalog_common helpers."""

import pytest

from services.blueprint_catalog_common import _resolve_category


class TestResolveCategoryReturnsManifestValue:
    def test_returns_category_from_manifest(self):
        manifest = {"category": "infrastructure", "blueprint": {"id": "x", "version": "1.0.0"}}
        assert _resolve_category(manifest) == "infrastructure"

    def test_returns_stripped_category(self):
        manifest = {"category": "  bnk  "}
        assert _resolve_category(manifest) == "bnk"

    def test_returns_category_regardless_of_source_path(self):
        manifest = {"category": "security"}
        assert _resolve_category(manifest, source_path="infra/aws/some/module") == "security"


class TestResolveCategoryFallsBackToBnk:
    def test_none_manifest_returns_bnk(self):
        assert _resolve_category(None) == "bnk"

    def test_empty_dict_returns_bnk(self):
        assert _resolve_category({}) == "bnk"

    def test_missing_category_key_returns_bnk(self):
        manifest = {"blueprint": {"id": "x", "version": "1.0.0"}}
        assert _resolve_category(manifest) == "bnk"

    def test_none_category_value_returns_bnk(self):
        assert _resolve_category({"category": None}) == "bnk"

    def test_empty_string_category_returns_bnk(self):
        assert _resolve_category({"category": ""}) == "bnk"

    def test_whitespace_only_category_returns_bnk(self):
        assert _resolve_category({"category": "   "}) == "bnk"


class TestResolveCategoryIgnoresSourcePath:
    @pytest.mark.parametrize(
        "source_path",
        [
            "infra/aws/vpc",
            "bnk/cluster",
            "k8s/manifests",
            None,
        ],
    )
    def test_source_path_never_affects_fallback(self, source_path: str | None):
        # Whether source_path looks like "infra" or anything else, fallback is always "bnk"
        assert _resolve_category(None, source_path=source_path) == "bnk"
        assert _resolve_category({}, source_path=source_path) == "bnk"
