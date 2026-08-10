"""Tests for normalized module capability serialization helpers."""

from models import ModuleLibrary
from services.module_capabilities import get_engine_type, get_execution_engine, serialize_engine_metadata


class TestExecutionEngineNormalization:
    def test_non_builtin_module_does_not_use_python_registry_for_execution_engine(self):
        module = ModuleLibrary(
            path="k8s/legacy-catalog-module",
            git_source="git::https://github.com/example/catalog.git//k8s/legacy-catalog-module?ref=main",
            module_source_kind="git_catalog",
            execution_engine=None,
            engine_type=None,
        )

        assert get_execution_engine(module) == "opentofu"
        assert get_engine_type(module) == "opentofu"

    def test_builtin_module_keeps_bounded_builtin_fallback(self):
        module = ModuleLibrary(
            path="bnk/gateway",
            git_source="builtin://bnk/gateway",
            module_source_kind="builtin",
            execution_engine=None,
            engine_type=None,
        )

        assert get_execution_engine(module) == "kubernetes-direct"
        assert get_engine_type(module) == "kubernetes"

    def test_serialize_engine_metadata_uses_normalized_execution_engine_for_catalog_rows(self):
        module = ModuleLibrary(
            path="k8s/catalog-service",
            git_source="git::https://github.com/example/catalog.git//k8s/catalog-service?ref=main",
            module_source_kind="git_catalog",
            execution_engine=None,
            engine_type=None,
        )

        metadata = serialize_engine_metadata(module)
        assert metadata["execution_engine"] == "opentofu"
        assert metadata["engine_type"] == "opentofu"

    def test_serialize_engine_metadata_does_not_classify_git_catalog_row_as_builtin_from_python_registry(self):
        module = ModuleLibrary(
            path="bnk/gateway",
            git_source="git::https://github.com/example/catalog.git//bnk/gateway?ref=release/2.2",
            module_source_kind="git_catalog",
            execution_engine="kubernetes-direct",
        )

        metadata = serialize_engine_metadata(
            module,
            include_module_classification=True,
        )
        assert metadata["is_builtin"] is False

    def test_non_builtin_module_ignores_legacy_engine_type_kubernetes(self):
        module = ModuleLibrary(
            path="k8s/catalog-service",
            git_source="git::https://github.com/example/catalog.git//k8s/catalog-service?ref=main",
            module_source_kind="git_catalog",
            execution_engine=None,
            engine_type="kubernetes",
        )

        assert get_execution_engine(module) == "opentofu"
        assert get_engine_type(module) == "opentofu"

    def test_module_type_for_catalog_helm_row_comes_from_persisted_metadata_not_registry(self):
        module = ModuleLibrary(
            path="k8s/catalog-service",
            git_source="git::https://github.com/example/catalog.git//k8s/catalog-service?ref=main",
            module_source_kind="git_catalog",
            execution_engine="operator",
            deploy_model="helm",
        )

        metadata = serialize_engine_metadata(
            module,
            include_module_classification=True,
        )
        assert metadata["module_type"] == "helm_chart"
