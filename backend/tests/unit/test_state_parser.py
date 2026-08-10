"""
BU-025: Unit tests for services.state_parser module.

Tests the TerraformStateParser with v3, v4, and encrypted state formats.
Uses real parser logic against in-memory state dicts and temp files — no mocks.
"""

import json
import os
import tempfile

import pytest

from services.state_parser import (
    EncryptedStateError,
    StateFileInvalidError,
    StateFileNotFoundError,
    TerraformStateParser,
    parse_state_file,
)

# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def v4_state():
    """A minimal but valid Terraform state v4."""
    return {
        "version": 4,
        "terraform_version": "1.6.0",
        "serial": 5,
        "lineage": "abc-123-def-456",
        "outputs": {
            "vpc_id": {"value": "vpc-12345", "type": "string", "sensitive": False},
            "private_subnets": {
                "value": ["subnet-a", "subnet-b"],
                "type": ["list", "string"],
                "sensitive": False,
            },
        },
        "resources": [
            {
                "mode": "managed",
                "type": "aws_vpc",
                "name": "main",
                "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
                "instances": [
                    {
                        "schema_version": 1,
                        "attributes": {
                            "id": "vpc-12345",
                            "cidr_block": "10.0.0.0/16",
                            "tags": {"Name": "main-vpc"},
                        },
                        "dependencies": [],
                    }
                ],
            },
            {
                "mode": "managed",
                "type": "aws_subnet",
                "name": "private",
                "provider": 'provider["registry.terraform.io/hashicorp/aws"]',
                "instances": [
                    {
                        "index_key": 0,
                        "attributes": {
                            "id": "subnet-a",
                            "vpc_id": "vpc-12345",
                            "cidr_block": "10.0.1.0/24",
                            "availability_zone": "us-west-2a",
                        },
                        "dependencies": ["aws_vpc.main"],
                    },
                    {
                        "index_key": 1,
                        "attributes": {
                            "id": "subnet-b",
                            "vpc_id": "vpc-12345",
                            "cidr_block": "10.0.2.0/24",
                            "availability_zone": "us-west-2b",
                        },
                        "dependencies": ["aws_vpc.main"],
                    },
                ],
            },
        ],
    }


@pytest.fixture()
def v3_state():
    """A minimal Terraform state v3."""
    return {
        "version": 3,
        "terraform_version": "0.12.31",
        "serial": 2,
        "lineage": "v3-lineage",
        "modules": [
            {
                "path": ["root"],
                "outputs": {
                    "instance_ip": {
                        "value": "10.0.0.5",
                        "type": "string",
                        "sensitive": False,
                    }
                },
                "resources": {
                    "aws_instance.web": {
                        "type": "aws_instance",
                        "provider": "provider.aws",
                        "primary": {
                            "id": "i-12345",
                            "attributes": {
                                "id": "i-12345",
                                "instance_type": "t3.micro",
                                "ami": "ami-abc123",
                            },
                        },
                        "depends_on": [],
                    }
                },
            }
        ],
    }


@pytest.fixture()
def encrypted_state():
    """A simulated encrypted state (OpenTofu 1.8+)."""
    return {
        "version": 4,
        "serial": 10,
        "lineage": "encrypted-lineage",
        "encryption_version": "1",
        "encrypted_data": "base64-encrypted-blob-here...",
    }


@pytest.fixture()
def state_file(tmp_path):
    """Helper to write state to a temp file and return the path."""

    def _write(state_data):
        path = tmp_path / "terraform.tfstate"
        path.write_text(json.dumps(state_data))
        return str(path)

    return _write


# ── Parse v4 state ───────────────────────────────────────────────────


class TestParseV4State:
    def test_parse_returns_version(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        assert result["version"] == 4
        assert result["is_encrypted"] is False

    def test_terraform_version(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        assert result["terraform_version"] == "1.6.0"

    def test_serial_and_lineage(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        assert result["serial"] == 5
        assert result["lineage"] == "abc-123-def-456"

    def test_extracts_resources(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        resources = result["resources"]
        assert len(resources) == 2
        assert resources[0]["type"] == "aws_vpc"
        assert resources[1]["type"] == "aws_subnet"

    def test_resource_full_address(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        vpc = result["resources"][0]
        assert vpc["full_address"] == "aws_vpc.main"

    def test_resource_provider_cleaned(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        assert result["resources"][0]["provider"] == "aws"

    def test_resource_instances(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        subnet = result["resources"][1]
        assert len(subnet["instances"]) == 2
        assert subnet["instances"][0]["index_key"] == 0
        assert subnet["instances"][1]["index_key"] == 1

    def test_resource_key_attributes(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        vpc = result["resources"][0]
        key_attrs = vpc["instances"][0]["key_attributes"]
        assert key_attrs["id"] == "vpc-12345"
        assert key_attrs["cidr_block"] == "10.0.0.0/16"

    def test_extracts_outputs(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        outputs = result["outputs"]
        assert "vpc_id" in outputs
        assert outputs["vpc_id"]["value"] == "vpc-12345"
        assert outputs["vpc_id"]["sensitive"] is False

    def test_extracts_dependencies(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        deps = result["dependencies"]
        # subnet depends on vpc
        assert any(d["target"] == "aws_vpc.main" for d in deps)

    def test_metadata_includes_counts(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        meta = result["metadata"]
        assert meta["resource_count"] == 2
        assert meta["output_count"] == 2
        assert meta["version"] == 4


# ── Parse v3 state ───────────────────────────────────────────────────


class TestParseV3State:
    def test_parse_v3(self, v3_state, state_file):
        path = state_file(v3_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        assert result["version"] == 3

    def test_v3_resources(self, v3_state, state_file):
        path = state_file(v3_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        resources = result["resources"]
        assert len(resources) == 1
        assert resources[0]["type"] == "aws_instance"

    def test_v3_outputs(self, v3_state, state_file):
        path = state_file(v3_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        assert result["outputs"]["instance_ip"]["value"] == "10.0.0.5"

    def test_v3_provider_cleaned(self, v3_state, state_file):
        path = state_file(v3_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        assert result["resources"][0]["provider"] == "aws"


# ── Encrypted state ──────────────────────────────────────────────────


class TestParseEncryptedState:
    def test_detects_encrypted(self, encrypted_state, state_file):
        path = state_file(encrypted_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        assert result["is_encrypted"] is True
        assert result["resources"] == []
        assert result["terraform_version"] == "encrypted"

    def test_encrypted_metadata(self, encrypted_state, state_file):
        path = state_file(encrypted_state)
        parser = TerraformStateParser(path)
        result = parser.parse()
        assert result["lineage"] == "encrypted-lineage"
        assert result["serial"] == 10

    def test_is_state_encrypted(self, encrypted_state, state_file):
        path = state_file(encrypted_state)
        parser = TerraformStateParser(path)
        assert parser.is_state_encrypted() is True

    def test_non_encrypted_is_not_encrypted(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        assert parser.is_state_encrypted() is False

    def test_get_encrypted_metadata(self, encrypted_state, state_file):
        path = state_file(encrypted_state)
        parser = TerraformStateParser(path)
        meta = parser.get_encrypted_metadata()
        assert meta["is_encrypted"] is True
        assert meta["lineage"] == "encrypted-lineage"
        assert meta["encryption_version"] == "1"


# ── Decrypted state passthrough ──────────────────────────────────────


class TestDecryptedStatePassthrough:
    def test_parse_with_decrypted_state(self, v4_state, tmp_path):
        """When decrypted_state is provided, parser uses it directly."""
        # Write a dummy file (parser still needs the path to exist)
        path = tmp_path / "terraform.tfstate"
        path.write_text("{}")  # dummy content
        parser = TerraformStateParser(str(path), decrypted_state=v4_state)
        result = parser.parse()
        assert result["version"] == 4
        assert result["is_encrypted"] is False
        assert len(result["resources"]) == 2


# ── Error handling ───────────────────────────────────────────────────


class TestParseErrors:
    def test_file_not_found(self, tmp_path):
        parser = TerraformStateParser(str(tmp_path / "nonexistent.tfstate"))
        with pytest.raises(FileNotFoundError):
            parser.parse()

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "bad.tfstate"
        path.write_text("not json at all")
        parser = TerraformStateParser(str(path))
        with pytest.raises(ValueError, match="Invalid JSON"):
            parser.parse()

    def test_missing_version_field(self, tmp_path):
        path = tmp_path / "no-version.tfstate"
        path.write_text(json.dumps({"resources": []}))
        parser = TerraformStateParser(str(path))
        with pytest.raises(ValueError, match="missing 'version'"):
            parser.parse()


# ── Convenience function ─────────────────────────────────────────────


class TestParseStateFileFunction:
    def test_convenience_function(self, v4_state, state_file):
        path = state_file(v4_state)
        result = parse_state_file(path)
        assert result["version"] == 4

    def test_file_not_found_raises_custom(self, tmp_path):
        with pytest.raises(StateFileNotFoundError):
            parse_state_file(str(tmp_path / "missing.tfstate"))

    def test_invalid_file_raises_custom(self, tmp_path):
        path = tmp_path / "bad.tfstate"
        path.write_text("not json")
        with pytest.raises(StateFileInvalidError):
            parse_state_file(str(path))


# ── Graph and lookup methods ─────────────────────────────────────────


class TestGraphAndLookup:
    def test_build_dependency_graph(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        parser.parse()
        graph = parser.build_dependency_graph()
        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["nodes"]) == 2
        # subnet depends on vpc
        assert any(e["to"] == "aws_vpc.main" for e in graph["edges"])

    def test_get_resource_by_address(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        parser.parse()
        vpc = parser.get_resource_by_address("aws_vpc.main")
        assert vpc is not None
        assert vpc["type"] == "aws_vpc"

    def test_get_resource_by_address_not_found(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        parser.parse()
        assert parser.get_resource_by_address("aws_nonexistent.foo") is None

    def test_get_resource_count_by_type(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        parser.parse()
        counts = parser.get_resource_count_by_type()
        assert counts["aws_vpc"] == 1
        assert counts["aws_subnet"] == 1

    def test_get_provider_summary(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        parser.parse()
        summary = parser.get_provider_summary()
        assert summary["aws"] == 2


# ── Key attribute extraction ─────────────────────────────────────────


class TestKeyAttributeExtraction:
    def test_aws_vpc_key_attrs(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        parser.parse()
        vpc = parser.get_resource_by_address("aws_vpc.main")
        key_attrs = vpc["instances"][0]["key_attributes"]
        assert "id" in key_attrs
        assert "cidr_block" in key_attrs
        assert "tags" in key_attrs

    def test_aws_subnet_key_attrs(self, v4_state, state_file):
        path = state_file(v4_state)
        parser = TerraformStateParser(path)
        parser.parse()
        subnet = parser.get_resource_by_address("aws_subnet.private")
        key_attrs = subnet["instances"][0]["key_attributes"]
        assert "vpc_id" in key_attrs
        assert "availability_zone" in key_attrs
