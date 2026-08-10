"""
Component tests for Module Metadata Parser and Validator.

Tests cover: ModuleMetadataParser (parse, get_*_inputs, caching, invalidation),
ModuleMetadataValidator (validate sections, error cases),
and load_all_module_metadata.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from services.module_metadata import (
    PACK_MANIFEST_FILENAME,
    InvalidMetadataSchemaError,
    MissingMetadataError,
    ModuleMetadataError,
    ModuleMetadataParser,
    ModuleMetadataValidator,
    derive_platform_compatibility,
    load_all_module_metadata,
    normalize_pack_manifest_for_catalog,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _make_module_json(tmp_path: str, module_path: str, metadata: dict):
    """Write a module.json file to a temporary directory."""
    mod_dir = Path(tmp_path) / module_path
    mod_dir.mkdir(parents=True, exist_ok=True)
    with open(mod_dir / "module.json", "w") as f:
        json.dump(metadata, f)


VALID_METADATA = {
    "module": {
        "name": "vpc",
        "path": "infra/aws/vpc",
        "version": "1.0.0",
        "layer": "infrastructure",
        "category": "networking",
        "description": "AWS VPC module",
        "cloud_specific": True,
        "supported_platforms": ["aws"],
    },
    "dependencies": {
        "required": [
            {"module": "infra/aws/base", "reason": "Needs base networking"}
        ],
        "optional": [
            {"module": "infra/aws/transit-gw"}
        ],
    },
    "inputs": {
        "required": [
            {"name": "vpc_cidr", "type": "string", "description": "VPC CIDR block", "source": "user"},
            {"name": "region", "type": "string", "description": "AWS Region", "source": "auto"},
        ],
        "optional": [
            {"name": "base_vpc_id", "type": "string", "description": "From base module",
             "source": "module", "from_module": "infra/aws/base", "from_output": "vpc_id"},
        ],
    },
    "outputs": {
        "key_outputs": [
            {"name": "vpc_id", "type": "string", "description": "VPC ID"},
        ]
    },
    "providers": {
        "required": ["aws"],
        "optional": ["random"],
    },
    "backend": {
        "recommendations": {"aws": "s3", "azure": "azurerm"},
    },
    "deployment": {
        "order": 10,
        "sensitive_inputs": ["secret_key"],
    },
}


VALID_PACK_MANIFEST = {
    "schema_version": 1,
    "module": {
        "name": "web-app",
        "path": "app/web-app",
        "version": "1.2.3",
        "category": "app",
        "description": "Ansible-driven app deployment",
        "provider": "aws",
        "supported_platforms": ["eks"],
        "tags": ["app", "ansible"],
        "required_capabilities": ["gateway_api"],
    },
    "deployment_pack": {
        "engine": "ansible",
        "runner_profile": "ansible-default",
        "working_directory": ".",
        "entrypoints": {
            "playbook": "playbooks/apply.yml",
            "destroy_playbook": "playbooks/destroy.yml",
            "outputs_file": "outputs/result.json",
        },
        "lifecycle": {
            "supports_init": True,
            "supports_plan": True,
            "supports_apply": True,
            "supports_destroy": True,
            "supports_refresh": True,
            "supports_drift": False,
        },
    },
    "dependencies": {
        "required": [{"module": "infra/aws/vpc", "reason": "Network dependency"}],
        "optional": [{"module": "k8s/observability"}],
    },
    "inputs": {
        "required": [
            {
                "name": "cluster_name",
                "type": "string",
                "description": "Target cluster",
                "source": "user",
            }
        ],
        "optional": [
            {
                "name": "vpc_id",
                "type": "string",
                "description": "VPC ID",
                "source": "module",
                "from_module": "infra/aws/vpc",
                "from_output": "vpc_id",
            }
        ],
    },
    "outputs": {
        "key_outputs": [
            {
                "name": "service_url",
                "type": "string",
                "description": "Service URL",
            }
        ]
    },
}


# ── ModuleMetadataParser ────────────────────────────────────────────


class TestModuleMetadataParser:
    def test_parse_valid_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "infra/aws/vpc", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            result = parser.parse("infra/aws/vpc")
            assert result["module"]["name"] == "vpc"
            assert result["module"]["version"] == "1.0.0"

    def test_parse_missing_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            parser = ModuleMetadataParser(tmp)
            with pytest.raises(MissingMetadataError, match="module.json not found"):
                parser.parse("nonexistent/module")

    def test_parse_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "bad"
            mod_dir.mkdir()
            with open(mod_dir / "module.json", "w") as f:
                f.write("{invalid json")
            parser = ModuleMetadataParser(tmp)
            with pytest.raises(ModuleMetadataError, match="Invalid JSON"):
                parser.parse("bad")

    def test_caching(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "infra/test", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)

            result1 = parser.parse("infra/test")
            result2 = parser.parse("infra/test")
            assert result1 is result2  # Same object from cache

    def test_invalidate_cache_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "infra/test", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            parser.parse("infra/test")
            assert "infra/test" in parser._cache

            parser.invalidate_cache("infra/test")
            assert "infra/test" not in parser._cache

    def test_invalidate_cache_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "a", VALID_METADATA)
            _make_module_json(tmp, "b", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            parser.parse("a")
            parser.parse("b")

            parser.invalidate_cache()
            assert parser._cache == {}

    def test_get_module_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "m", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            metadata = parser.parse("m")
            info = parser.get_module_info(metadata)
            assert info.name == "vpc"
            assert info.layer == "infrastructure"
            assert info.cloud_specific is True
            assert info.supported_platforms == ["aws"]

    def test_get_required_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "m", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            metadata = parser.parse("m")
            required = parser.get_required_inputs(metadata)
            assert len(required) == 2
            assert required[0]["name"] == "vpc_cidr"

    def test_get_user_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "m", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            metadata = parser.parse("m")
            user_inputs = parser.get_user_inputs(metadata)
            assert len(user_inputs) == 1
            assert user_inputs[0]["source"] == "user"

    def test_get_module_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "m", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            metadata = parser.parse("m")
            mod_inputs = parser.get_module_inputs(metadata)
            assert len(mod_inputs) == 1
            assert mod_inputs[0]["from_module"] == "infra/aws/base"

    def test_get_auto_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "m", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            metadata = parser.parse("m")
            auto_inputs = parser.get_auto_inputs(metadata)
            assert len(auto_inputs) == 1
            assert auto_inputs[0]["name"] == "region"

    def test_get_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "m", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            metadata = parser.parse("m")
            outputs = parser.get_outputs(metadata)
            assert len(outputs) == 1
            assert outputs[0]["name"] == "vpc_id"

    def test_get_sensitive_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "m", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            metadata = parser.parse("m")
            sensitive = parser.get_sensitive_inputs(metadata)
            assert sensitive == ["secret_key"]

    def test_get_required_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "m", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            metadata = parser.parse("m")
            deps = parser.get_required_dependencies(metadata)
            assert len(deps) == 1
            assert deps[0]["module"] == "infra/aws/base"

    def test_get_backend_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "m", VALID_METADATA)
            parser = ModuleMetadataParser(tmp)
            metadata = parser.parse("m")
            recs = parser.get_backend_recommendations(metadata)
            assert recs["aws"] == "s3"

    def test_parse_pack_manifest_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            mod_dir = Path(tmp) / "packs" / "app"
            mod_dir.mkdir(parents=True)
            with open(mod_dir / PACK_MANIFEST_FILENAME, "w") as f:
                json.dump(VALID_PACK_MANIFEST, f)

            parser = ModuleMetadataParser(tmp)
            result = parser.parse_pack_manifest("packs/app")
            assert result["module"]["name"] == "web-app"
            assert result["deployment_pack"]["engine"] == "ansible"

    def test_parse_pack_manifest_missing_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            parser = ModuleMetadataParser(tmp)
            with pytest.raises(MissingMetadataError, match=PACK_MANIFEST_FILENAME):
                parser.parse_pack_manifest("missing/pack")


# ── ModuleMetadataValidator ──────────────────────────────────────────


class TestModuleMetadataValidator:
    def test_valid_metadata_passes(self):
        validator = ModuleMetadataValidator()
        validator.validate(VALID_METADATA)  # Should not raise

    def test_missing_module_section(self):
        validator = ModuleMetadataValidator()
        with pytest.raises(InvalidMetadataSchemaError, match="Missing 'module' section"):
            validator.validate({})

    def test_missing_required_module_field(self):
        validator = ModuleMetadataValidator()
        bad = {"module": {"name": "x", "path": "x", "version": "1", "layer": "infrastructure", "category": "net"}}
        with pytest.raises(InvalidMetadataSchemaError, match="Missing required field 'module.description'"):
            validator.validate(bad)

    def test_invalid_layer_value(self):
        validator = ModuleMetadataValidator()
        bad = {
            "module": {
                "name": "x", "path": "x", "version": "1",
                "layer": "invalid-layer", "category": "net", "description": "desc"
            }
        }
        with pytest.raises(InvalidMetadataSchemaError, match="Invalid layer"):
            validator.validate(bad)

    def test_invalid_platform(self):
        validator = ModuleMetadataValidator()
        bad = {
            "module": {
                "name": "x", "path": "x", "version": "1",
                "layer": "infrastructure", "category": "net", "description": "d",
                "supported_platforms": ["invalid-cloud"],
            }
        }
        with pytest.raises(InvalidMetadataSchemaError, match="Invalid platform"):
            validator.validate(bad)

    def test_canonical_platform_values_are_accepted(self):
        validator = ModuleMetadataValidator()
        canonical = {
            "module": {
                "name": "x",
                "path": "x",
                "version": "1",
                "layer": "infrastructure",
                "category": "net",
                "description": "d",
                "supported_platforms": ["eks", "aks", "gke", "ocp", "generic_onprem"],
            }
        }

        validator.validate(canonical)

    def test_invalid_input_source(self):
        validator = ModuleMetadataValidator()
        bad = {
            "module": {
                "name": "x", "path": "x", "version": "1",
                "layer": "infrastructure", "category": "net", "description": "d"
            },
            "inputs": {
                "required": [
                    {"name": "v", "type": "string", "description": "d", "source": "invalid"}
                ]
            },
        }
        with pytest.raises(InvalidMetadataSchemaError, match="Invalid input source"):
            validator.validate(bad)

    def test_module_source_missing_from_module(self):
        validator = ModuleMetadataValidator()
        bad = {
            "module": {
                "name": "x", "path": "x", "version": "1",
                "layer": "infrastructure", "category": "net", "description": "d"
            },
            "inputs": {
                "required": [
                    {"name": "v", "type": "string", "description": "d", "source": "module"}
                ]
            },
        }
        with pytest.raises(InvalidMetadataSchemaError, match="must have 'from_module'"):
            validator.validate(bad)

    def test_validate_pack_manifest_valid(self):
        validator = ModuleMetadataValidator()
        validator.validate_pack_manifest(VALID_PACK_MANIFEST)

    def test_validate_pack_manifest_invalid_runner_profile(self):
        validator = ModuleMetadataValidator()
        invalid = {
            **VALID_PACK_MANIFEST,
            "deployment_pack": {
                **VALID_PACK_MANIFEST["deployment_pack"],
                "engine": "ansible",
                "runner_profile": "kubernetes-default",
            },
        }

        with pytest.raises(InvalidMetadataSchemaError, match="runner_profile"):
            validator.validate_pack_manifest(invalid)

    def test_validate_pack_manifest_requires_supports_apply_true(self):
        validator = ModuleMetadataValidator()
        invalid = {
            **VALID_PACK_MANIFEST,
            "deployment_pack": {
                **VALID_PACK_MANIFEST["deployment_pack"],
                "lifecycle": {
                    **VALID_PACK_MANIFEST["deployment_pack"]["lifecycle"],
                    "supports_apply": False,
                },
            },
        }

        with pytest.raises(InvalidMetadataSchemaError, match="supports_apply"):
            validator.validate_pack_manifest(invalid)

    def test_validate_pack_manifest_ansible_requires_playbook(self):
        validator = ModuleMetadataValidator()
        invalid = {
            **VALID_PACK_MANIFEST,
            "deployment_pack": {
                **VALID_PACK_MANIFEST["deployment_pack"],
                "entrypoints": {
                    "destroy_playbook": "playbooks/destroy.yml",
                    "outputs_file": "outputs/result.json",
                },
            },
        }

        with pytest.raises(InvalidMetadataSchemaError, match="playbook"):
            validator.validate_pack_manifest(invalid)

    def test_validate_pack_manifest_rejects_raw_credential_secret_values(self):
        validator = ModuleMetadataValidator()
        invalid = {
            **VALID_PACK_MANIFEST,
            "credentials": {
                "required": [
                    {
                        "name": "cloud-creds",
                        "type": "cloud",
                        "description": "Cloud creds",
                        "value": "super-secret",
                    }
                ],
                "optional": [],
            },
        }

        with pytest.raises(InvalidMetadataSchemaError, match="raw secret field"):
            validator.validate_pack_manifest(invalid)

    def test_validate_pack_manifest_rejects_sensitive_input_default(self):
        validator = ModuleMetadataValidator()
        invalid = {
            **VALID_PACK_MANIFEST,
            "inputs": {
                "required": [
                    {
                        "name": "api_token",
                        "type": "string",
                        "description": "Token",
                        "source": "user",
                        "sensitive": True,
                        "default": "plain-token",
                    }
                ],
                "optional": [],
            },
        }

        with pytest.raises(InvalidMetadataSchemaError, match="Sensitive input"):
            validator.validate_pack_manifest(invalid)

    def test_validate_pack_manifest_accepts_json_compatible_output_value(self):
        validator = ModuleMetadataValidator()
        valid = {
            **VALID_PACK_MANIFEST,
            "outputs": {
                "key_outputs": [
                    {
                        "name": "service_url",
                        "type": "string",
                        "description": "Service URL",
                        "value": {
                            "primary": "${service_name}.svc",
                            "ports": [443, 8443],
                            "enabled": True,
                            "metadata": {"tier": "prod", "weight": 100},
                        },
                    }
                ]
            },
        }

        validator.validate_pack_manifest(valid)

    def test_validate_pack_manifest_rejects_non_json_output_value(self):
        validator = ModuleMetadataValidator()

        class _NotJsonSerializable:
            pass

        invalid = {
            **VALID_PACK_MANIFEST,
            "outputs": {
                "key_outputs": [
                    {
                        "name": "service_url",
                        "type": "string",
                        "description": "Service URL",
                        "value": _NotJsonSerializable(),
                    }
                ]
            },
        }

        with pytest.raises(InvalidMetadataSchemaError, match="must be JSON-compatible"):
            validator.validate_pack_manifest(invalid)

    def test_validate_pack_manifest_rejects_overly_nested_output_value(self):
        validator = ModuleMetadataValidator()
        too_deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "too-deep"}}}}}}}
        invalid = {
            **VALID_PACK_MANIFEST,
            "outputs": {
                "key_outputs": [
                    {
                        "name": "service_url",
                        "type": "string",
                        "description": "Service URL",
                        "value": too_deep,
                    }
                ]
            },
        }

        with pytest.raises(InvalidMetadataSchemaError, match="nesting is too deep"):
            validator.validate_pack_manifest(invalid)


# ── load_all_module_metadata ─────────────────────────────────────────


class TestLoadAllModuleMetadata:
    def test_loads_multiple_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "infra/aws/vpc", VALID_METADATA)
            meta2 = {**VALID_METADATA, "module": {**VALID_METADATA["module"], "name": "sg"}}
            _make_module_json(tmp, "infra/aws/sg", meta2)

            result = load_all_module_metadata(tmp)
            assert len(result) == 2
            assert "infra/aws/vpc" in result
            assert "infra/aws/sg" in result

    def test_skips_invalid_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            _make_module_json(tmp, "good", VALID_METADATA)
            # Write invalid JSON
            bad_dir = Path(tmp) / "bad"
            bad_dir.mkdir()
            with open(bad_dir / "module.json", "w") as f:
                f.write("{broken")

            result = load_all_module_metadata(tmp)
            assert len(result) == 1
            assert "good" in result


class TestPlatformCompatibilityNormalization:
    def test_maps_legacy_supported_platforms_to_canonical_profiles(self):
        result = derive_platform_compatibility(
            supported_platforms=["aws", "on-prem"],
            required_capabilities=["gateway_api"],
        )

        assert result["supported_platforms"] == ["aws", "on-prem"]
        assert result["required_capabilities"] == ["gateway_api"]
        assert result["platform_compatibility"]["supported_profiles"] == ["eks", "generic_onprem"]
        assert result["platform_compatibility"]["required_capabilities"] == ["gateway_api"]
        assert result["platform_compatibility"]["compatibility_scope"] == "declared_subset"
        assert result["platform_compatibility"]["declared_any"] is False

    def test_expands_any_to_all_canonical_profiles(self):
        result = derive_platform_compatibility(supported_platforms=["any"], required_capabilities=[])

        assert result["platform_compatibility"]["supported_profiles"] == [
            "generic_onprem",
            "eks",
            "aks",
            "gke",
            "ocp",
        ]
        assert result["platform_compatibility"]["compatibility_scope"] == "declared_all_profiles"
        assert result["platform_compatibility"]["declared_any"] is True

    def test_tracks_unmapped_values_without_overclaiming(self):
        result = derive_platform_compatibility(supported_platforms=["k3s"], required_capabilities=None)

        assert result["platform_compatibility"]["supported_profiles"] == []
        assert result["platform_compatibility"]["unmapped_supported_platforms"] == ["k3s"]
        assert result["platform_compatibility"]["compatibility_scope"] == "unspecified"

    def test_accepts_canonical_supported_platforms_as_pass_through(self):
        result = derive_platform_compatibility(
            supported_platforms=["ocp", "eks", "generic_onprem"],
            required_capabilities=["scc"],
        )

        assert result["platform_compatibility"]["supported_profiles"] == ["ocp", "eks", "generic_onprem"]
        assert result["platform_compatibility"]["unmapped_supported_platforms"] == []
        assert result["platform_compatibility"]["compatibility_scope"] == "declared_subset"


class TestPackManifestNormalization:
    def test_normalize_pack_manifest_for_catalog(self):
        normalized = normalize_pack_manifest_for_catalog(VALID_PACK_MANIFEST)

        assert normalized["module_source_kind"] == "git_catalog"
        assert normalized["execution_engine"] == "ansible"
        assert normalized["deploy_model"] == "ansible_playbook"
        assert normalized["engine_type"] == "ansible"
        assert normalized["pack_manifest"] == VALID_PACK_MANIFEST
        assert normalized["inputs_metadata"]["required"][0]["name"] == "cluster_name"
        assert normalized["outputs_metadata"][0]["name"] == "service_url"
        assert normalized["dependencies_metadata"]["required"][0]["module"] == "infra/aws/vpc"
        assert normalized["dependencies"] == ["infra/aws/vpc"]
