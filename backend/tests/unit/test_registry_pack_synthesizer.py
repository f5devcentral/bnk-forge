"""Unit tests for the registry pack manifest synthesizer.

Confirms that synthesized manifests pass the existing validator and that user
overrides are spliced into the right places.
"""

from __future__ import annotations

import pytest

from services.module_metadata import ModuleMetadataValidator
from services.registry_pack_synthesizer import ModuleIdentity, infer_engine, synthesize
from services.registry_variable_scraper import (
    ScrapedOutput,
    ScrapedVariable,
    ScrapeResult,
)


def _identity(**overrides) -> ModuleIdentity:
    base: ModuleIdentity = {
        "name": "vpc",
        "catalog_path": "registry/terraform/hashicorp/aws/vpc",
        "version": "5.1.2",
        "provider": "aws",
        "description": "AWS VPC module",
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def _scraped_basic() -> ScrapeResult:
    return ScrapeResult(
        variables=[
            ScrapedVariable(name="region", type="string", description="AWS region",
                            default="us-east-1", has_default=True),
            ScrapedVariable(name="vpc_id", type="string", description="VPC ID",
                            default=None, has_default=False),
            ScrapedVariable(name="api_token", type="string",
                            sensitive=True, has_default=False),
        ],
        outputs=[
            ScrapedOutput(name="vpc_arn", description="ARN of the VPC"),
        ],
        required_providers=["aws"],
    )


def test_synthesized_manifest_validates() -> None:
    manifest = synthesize(
        identity=_identity(),
        scraped=_scraped_basic(),
        sha256="a" * 64,
        source_type="terraform_registry",
        upstream_url="https://registry.terraform.io/v1/modules/x/y/z/5.1.2",
    )

    ModuleMetadataValidator().validate_pack_manifest(manifest)


def test_required_vs_optional_split() -> None:
    manifest = synthesize(
        identity=_identity(),
        scraped=_scraped_basic(),
        sha256="b" * 64,
        source_type="opentofu_registry",
        upstream_url="https://registry.opentofu.org/v1/...",
    )

    required_names = {i["name"] for i in manifest["inputs"]["required"]}
    optional_names = {i["name"] for i in manifest["inputs"]["optional"]}
    assert "vpc_id" in required_names
    assert "api_token" in required_names
    assert "region" in optional_names


def test_sensitive_inputs_have_no_default() -> None:
    manifest = synthesize(
        identity=_identity(),
        scraped=_scraped_basic(),
        sha256="c" * 64,
        source_type="public_git",
        upstream_url="https://github.com/x/y",
    )

    for inp in manifest["inputs"]["required"] + manifest["inputs"]["optional"]:
        if inp.get("sensitive"):
            assert "default" not in inp or not inp["default"]


def test_overrides_replace_description() -> None:
    overrides = {
        "variables": {"region": {"description": "Stage your AWS region here"}},
        "outputs": {"vpc_arn": {"description": "VPC ARN (renamed)", "sensitive": True}},
    }
    manifest = synthesize(
        identity=_identity(),
        scraped=_scraped_basic(),
        sha256="d" * 64,
        source_type="terraform_registry",
        upstream_url="https://registry.terraform.io/v1/...",
        overrides=overrides,
    )

    region_input = next(i for i in manifest["inputs"]["optional"] if i["name"] == "region")
    assert region_input["description"] == "Stage your AWS region here"

    vpc_arn_output = next(o for o in manifest["outputs"]["key_outputs"] if o["name"] == "vpc_arn")
    assert vpc_arn_output["description"] == "VPC ARN (renamed)"
    assert vpc_arn_output["sensitive"] is True


def test_synthesized_marker_includes_provenance() -> None:
    manifest = synthesize(
        identity=_identity(),
        scraped=_scraped_basic(),
        sha256="e" * 64,
        source_type="terraform_registry",
        upstream_url="https://example/v1/modules/a/b/c/1.0.0",
    )

    synthesized = manifest["_synthesized"]
    assert synthesized["sha256"] == "e" * 64
    assert synthesized["source_type"] == "terraform_registry"
    assert synthesized["upstream_url"].endswith("1.0.0")
    assert synthesized["required_providers"] == ["aws"]


# ---------------------------------------------------------------------------
# Engine inference — infer_engine()
# ---------------------------------------------------------------------------

class TestInferEngine:
    def test_opentofu_from_tf_files(self, tmp_path) -> None:
        (tmp_path / "main.tf").write_text('variable "region" {}')
        assert infer_engine(tmp_path) == "opentofu"

    def test_kubernetes_from_chart_yaml(self, tmp_path) -> None:
        (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart\n")
        assert infer_engine(tmp_path) == "kubernetes"

    def test_kubernetes_from_chart_yaml_lowercase(self, tmp_path) -> None:
        (tmp_path / "chart.yaml").write_text("apiVersion: v2\nname: my-chart\n")
        assert infer_engine(tmp_path) == "kubernetes"

    def test_ansible_from_playbook_yaml(self, tmp_path) -> None:
        (tmp_path / "site.yml").write_text("---\n- hosts: all\n  tasks: []\n")
        assert infer_engine(tmp_path) == "ansible"

    def test_engine_override_wins_over_chart_yaml(self, tmp_path) -> None:
        """Explicit override always takes precedence."""
        (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart\n")
        assert infer_engine(tmp_path, engine_override="opentofu") == "opentofu"

    def test_chart_yaml_wins_over_tf_files(self, tmp_path) -> None:
        """Chart.yaml checked before *.tf — helm chart takes priority."""
        (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart\n")
        (tmp_path / "values.tf").write_text('variable "x" {}')
        assert infer_engine(tmp_path) == "kubernetes"

    def test_missing_dir_defaults_to_opentofu(self, tmp_path) -> None:
        assert infer_engine(tmp_path / "nonexistent") == "opentofu"

    def test_empty_dir_defaults_to_opentofu(self, tmp_path) -> None:
        assert infer_engine(tmp_path) == "opentofu"


# ---------------------------------------------------------------------------
# Engine inference wired through synthesize()
# ---------------------------------------------------------------------------

class TestSynthesizeEngineInference:
    def test_helm_module_gets_kubernetes_engine(self, tmp_path) -> None:
        """A Chart.yaml-bearing module must NOT be stamped engine=opentofu."""
        (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart\n")

        manifest = synthesize(
            identity=_identity(),
            scraped=ScrapeResult(),
            sha256="f" * 64,
            source_type="public_git",
            upstream_url="https://github.com/example/helm-chart",
            module_dir=tmp_path,
        )

        assert manifest["deployment_pack"]["engine"] == "kubernetes"
        assert manifest["deployment_pack"]["engine"] != "opentofu"
        # Must still pass the validator
        ModuleMetadataValidator().validate_pack_manifest(manifest)

    def test_opentofu_module_still_infers_opentofu(self, tmp_path) -> None:
        """Existing .tf modules must still get engine=opentofu."""
        (tmp_path / "main.tf").write_text('variable "region" {}')

        manifest = synthesize(
            identity=_identity(),
            scraped=_scraped_basic(),
            sha256="g" * 64,
            source_type="opentofu_registry",
            upstream_url="https://registry.opentofu.org/...",
            module_dir=tmp_path,
        )

        assert manifest["deployment_pack"]["engine"] == "opentofu"
        ModuleMetadataValidator().validate_pack_manifest(manifest)

    def test_ansible_module_infers_ansible(self, tmp_path) -> None:
        (tmp_path / "site.yml").write_text("---\n- hosts: all\n  tasks: []\n")

        manifest = synthesize(
            identity=_identity(),
            scraped=ScrapeResult(),
            sha256="h" * 64,
            source_type="public_git",
            upstream_url="https://github.com/example/ansible-role",
            module_dir=tmp_path,
        )

        assert manifest["deployment_pack"]["engine"] == "ansible"
        ModuleMetadataValidator().validate_pack_manifest(manifest)

    def test_engine_override_respected(self, tmp_path) -> None:
        """engine_override wins regardless of directory contents."""
        (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart\n")

        manifest = synthesize(
            identity=_identity(),
            scraped=ScrapeResult(),
            sha256="i" * 64,
            source_type="public_git",
            upstream_url="https://github.com/example/chart",
            module_dir=tmp_path,
            engine_override="opentofu",
        )

        assert manifest["deployment_pack"]["engine"] == "opentofu"

    def test_synthesized_marker_records_inferred_engine(self, tmp_path) -> None:
        (tmp_path / "Chart.yaml").write_text("apiVersion: v2\nname: my-chart\n")

        manifest = synthesize(
            identity=_identity(),
            scraped=ScrapeResult(),
            sha256="j" * 64,
            source_type="public_git",
            upstream_url="https://github.com/example/chart",
            module_dir=tmp_path,
        )

        assert manifest["_synthesized"]["inferred_engine"] == "kubernetes"

    def test_no_module_dir_defaults_to_opentofu(self) -> None:
        """Omitting module_dir should still produce a valid opentofu manifest."""
        manifest = synthesize(
            identity=_identity(),
            scraped=_scraped_basic(),
            sha256="k" * 64,
            source_type="terraform_registry",
            upstream_url="https://registry.terraform.io/...",
        )

        assert manifest["deployment_pack"]["engine"] == "opentofu"
        ModuleMetadataValidator().validate_pack_manifest(manifest)
