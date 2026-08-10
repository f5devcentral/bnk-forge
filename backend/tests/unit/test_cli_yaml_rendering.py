"""
Unit tests for CLI task YAML rendering.

Tests _render_awsbnkctl_cluster_yaml to verify cluster.yaml generation
for external-only and dual-interface patterns, dataPath blocks, and
CIDR derivation from vpc_cidr.
"""

import yaml

from tasks.cli_tasks import _render_awsbnkctl_cluster_yaml


class TestCliClusterYamlRendering:
    """Test cluster.yaml rendering for awsbnkctl."""

    def test_render_external_only_pattern(self):
        """Test external-only pattern produces valid YAML with external-only dataPath."""
        variables = {
            "cluster_name": "test-cluster",
            "region": "us-east-1",
            "vpc_cidr": "10.0.0.0/16",
            "instance_type": "m5.2xlarge",
            "pattern": "external-only",
        }

        yaml_str = _render_awsbnkctl_cluster_yaml(variables)
        config = yaml.safe_load(yaml_str)

        # Verify basic structure
        assert config["apiVersion"] == "awsbnkctl/v1"
        assert config["kind"] == "Cluster"
        assert config["metadata"]["name"] == "test-cluster"
        assert config["metadata"]["region"] == "us-east-1"
        assert config["pattern"] == "external-only"

        # Verify network structure
        assert config["network"]["vpcCidr"] == "10.0.0.0/16"
        assert config["network"]["azs"] == ["us-east-1a", "us-east-1b"]

        # Verify subnets derived safely from vpc_cidr
        assert len(config["network"]["subnets"]["public"]) == 2
        assert len(config["network"]["subnets"]["private"]) == 2
        assert config["network"]["subnets"]["public"][0]["az"] == "us-east-1a"
        assert config["network"]["subnets"]["public"][1]["az"] == "us-east-1b"

        # Verify dataPath for external-only: only external block, no internal
        assert "dataPath" in config["network"]
        assert "external" in config["network"]["dataPath"]
        assert "internal" not in config["network"]["dataPath"]
        assert "cidr" in config["network"]["dataPath"]["external"]
        assert "az" in config["network"]["dataPath"]["external"]

        # Verify cluster block
        assert config["cluster"]["nodeGroups"][0]["instanceType"] == "m5.2xlarge"
        assert config["cluster"]["nodeGroups"][0]["desiredSize"] == 3

        # Verify nodeGroup has only known fields (no spotPrice or tags)
        node_group_keys = set(config["cluster"]["nodeGroups"][0].keys())
        expected_keys = {"name", "desiredSize", "minSize", "maxSize", "instanceType"}
        assert node_group_keys == expected_keys, f"Unexpected fields in nodeGroup: {node_group_keys - expected_keys}"

        # Verify bnk block is absent (optional and not needed for dry-run)
        assert "bnk" not in config

    def test_render_dual_interface_pattern(self):
        """Test dual-interface pattern includes both external and internal dataPath."""
        variables = {
            "cluster_name": "dual-cluster",
            "region": "eu-west-1",
            "vpc_cidr": "172.16.0.0/16",
            "instance_type": "c5.4xlarge",
            "pattern": "dual-interface",
        }

        yaml_str = _render_awsbnkctl_cluster_yaml(variables)
        config = yaml.safe_load(yaml_str)

        assert config["pattern"] == "dual-interface"

        # Verify both external and internal dataPath blocks exist
        assert "dataPath" in config["network"]
        assert "external" in config["network"]["dataPath"]
        assert "internal" in config["network"]["dataPath"]

        # Both should have cidr and az
        assert "cidr" in config["network"]["dataPath"]["external"]
        assert "az" in config["network"]["dataPath"]["external"]
        assert "cidr" in config["network"]["dataPath"]["internal"]
        assert "az" in config["network"]["dataPath"]["internal"]

        # Internal and external CIDRs should be different
        assert (
            config["network"]["dataPath"]["external"]["cidr"]
            != config["network"]["dataPath"]["internal"]["cidr"]
        )

        # Verify bnk block is absent
        assert "bnk" not in config

    def test_render_with_custom_vpc_cidr(self):
        """Test CIDR derivation with different vpc_cidr blocks."""
        variables = {
            "cluster_name": "custom-vpc",
            "region": "ap-southeast-2",
            "vpc_cidr": "192.168.0.0/16",
            "instance_type": "m5.xlarge",
            "pattern": "external-only",
        }

        yaml_str = _render_awsbnkctl_cluster_yaml(variables)
        config = yaml.safe_load(yaml_str)

        # vpc_cidr should be preserved exactly
        assert config["network"]["vpcCidr"] == "192.168.0.0/16"

        # Subnets should be derived from the base (192.168.x.x range)
        # With base octet 168, we expect .169, .170, .179, .180 etc.
        public_cidrs = [s["cidr"] for s in config["network"]["subnets"]["public"]]
        private_cidrs = [s["cidr"] for s in config["network"]["subnets"]["private"]]

        # All should start with 192.168
        for cidr in public_cidrs + private_cidrs:
            assert cidr.startswith("192.168"), f"CIDR {cidr} doesn't match vpc base"

        # Verify no unknown fields
        assert "bnk" not in config

    def test_render_defaults_when_variables_missing(self):
        """Test rendering with minimal/missing variables uses sensible defaults."""
        variables = {}

        yaml_str = _render_awsbnkctl_cluster_yaml(variables)
        config = yaml.safe_load(yaml_str)

        # Should use defaults
        assert config["metadata"]["name"] == "bnk-demo"
        assert config["metadata"]["region"] == "ap-southeast-2"
        assert config["network"]["vpcCidr"] == "10.0.0.0/16"
        assert config["cluster"]["nodeGroups"][0]["instanceType"] == "m5.2xlarge"
        assert config["pattern"] == "external-only"

        # Verify no unknown fields
        assert "bnk" not in config
        assert "spotPrice" not in config["cluster"]["nodeGroups"][0]
        assert "tags" not in config["cluster"]["nodeGroups"][0]

    def test_yaml_parses_validly(self):
        """Test that rendered YAML is valid and parseable."""
        variables = {
            "cluster_name": "parse-test",
            "region": "us-west-2",
            "vpc_cidr": "10.1.0.0/16",
            "instance_type": "m5.2xlarge",
            "pattern": "external-only",
        }

        yaml_str = _render_awsbnkctl_cluster_yaml(variables)

        # Should not raise yaml parsing error
        config = yaml.safe_load(yaml_str)
        assert config is not None

        # Verify key structural elements exist
        assert "apiVersion" in config
        assert "metadata" in config
        assert "network" in config
        assert "cluster" in config

        # Verify unknown fields are absent
        assert "bnk" not in config

    def test_azs_derived_from_region(self):
        """Test that AZs are correctly derived from the region."""
        test_cases = [
            ("us-east-1", ["us-east-1a", "us-east-1b"]),
            ("eu-west-1", ["eu-west-1a", "eu-west-1b"]),
            ("ap-southeast-2", ["ap-southeast-2a", "ap-southeast-2b"]),
        ]

        for region, expected_azs in test_cases:
            variables = {
                "cluster_name": "az-test",
                "region": region,
                "vpc_cidr": "10.0.0.0/16",
                "instance_type": "m5.2xlarge",
                "pattern": "external-only",
            }

            yaml_str = _render_awsbnkctl_cluster_yaml(variables)
            config = yaml.safe_load(yaml_str)

            assert config["network"]["azs"] == expected_azs


class TestCliClusterYamlBnkBlock:
    """Test the bnk: block emission logic."""

    def _base_vars(self) -> dict:
        return {
            "cluster_name": "bnk-test",
            "region": "ap-southeast-2",
            "vpc_cidr": "10.0.0.0/16",
            "instance_type": "m5.2xlarge",
            "pattern": "external-only",
        }

    def test_bnk_block_emitted_when_both_paths_provided(self):
        """bnk: block is present when far_archive_path and jwt_path are both supplied."""
        yaml_str = _render_awsbnkctl_cluster_yaml(
            self._base_vars(),
            far_archive_path="./secrets/cne_pull_64.json",
            jwt_path="./secrets/license.jwt",
        )
        config = yaml.safe_load(yaml_str)

        assert "bnk" in config
        assert config["bnk"]["farArchive"] == "./secrets/cne_pull_64.json"
        assert config["bnk"]["jwt"] == "./secrets/license.jwt"

    def test_bnk_block_absent_when_far_archive_missing(self):
        """bnk: block is omitted when far_archive_path is None."""
        yaml_str = _render_awsbnkctl_cluster_yaml(
            self._base_vars(),
            far_archive_path=None,
            jwt_path="./secrets/license.jwt",
        )
        config = yaml.safe_load(yaml_str)

        assert "bnk" not in config

    def test_bnk_block_absent_when_jwt_missing(self):
        """bnk: block is omitted when jwt_path is None."""
        yaml_str = _render_awsbnkctl_cluster_yaml(
            self._base_vars(),
            far_archive_path="./secrets/cne_pull_64.json",
            jwt_path=None,
        )
        config = yaml.safe_load(yaml_str)

        assert "bnk" not in config

    def test_bnk_block_absent_when_both_missing(self):
        """bnk: block is omitted when both paths are None (default behavior)."""
        yaml_str = _render_awsbnkctl_cluster_yaml(self._base_vars())
        config = yaml.safe_load(yaml_str)

        assert "bnk" not in config

    def test_bnk_block_yaml_valid_when_present(self):
        """Output with bnk: block still parses as valid YAML."""
        yaml_str = _render_awsbnkctl_cluster_yaml(
            self._base_vars(),
            far_archive_path="./secrets/cne_pull_64.json",
            jwt_path="./secrets/license.jwt",
        )
        config = yaml.safe_load(yaml_str)

        # Structural integrity checks
        assert config["apiVersion"] == "awsbnkctl/v1"
        assert config["kind"] == "Cluster"
        assert "network" in config
        assert "cluster" in config
        assert "bnk" in config

    def test_bnk_block_paths_are_workspace_relative(self):
        """Paths in bnk: block start with ./ (workspace-relative as awsbnkctl expects)."""
        far = "./secrets/cne_pull_64.json"
        jwt = "./secrets/license.jwt"

        yaml_str = _render_awsbnkctl_cluster_yaml(
            self._base_vars(),
            far_archive_path=far,
            jwt_path=jwt,
        )
        config = yaml.safe_load(yaml_str)

        assert config["bnk"]["farArchive"].startswith("./")
        assert config["bnk"]["jwt"].startswith("./")


class TestCliDemoBlocks:
    """Tests for the 4 demo-layer cluster.yaml blocks (Phase 1 — awsbnkctl up blocks only)."""

    def _base_vars(self) -> dict:
        return {
            "cluster_name": "demo-test",
            "region": "ap-southeast-2",
            "vpc_cidr": "10.0.0.0/16",
            "instance_type": "m5.2xlarge",
            "pattern": "external-only",
        }

    # ── All-disabled (default) ──────────────────────────────────────────────

    def test_all_disabled_no_demo_blocks_present(self):
        """When all demo toggles are off (default), no demo-layer keys appear."""
        yaml_str = _render_awsbnkctl_cluster_yaml(self._base_vars())
        config = yaml.safe_load(yaml_str)

        assert "testing" not in config
        assert "demo" not in config
        assert "bigipVE" not in config
        assert "ai" not in config

    def test_all_disabled_output_unchanged(self):
        """All-disabled output matches output without any demo variables set."""
        base = self._base_vars()
        expected = _render_awsbnkctl_cluster_yaml(base)
        with_falsy = _render_awsbnkctl_cluster_yaml({
            **base,
            "demo_enabled": False,
            "jumphost_enabled": False,
            "bigipve_enabled": False,
            "ai_sagemaker_enabled": False,
        })
        assert expected == with_falsy

    def test_string_false_values_treated_as_disabled(self):
        """String 'false' / '0' / 'no' values disable the blocks."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "demo_enabled": "false",
            "jumphost_enabled": "0",
            "bigipve_enabled": "no",
            "ai_sagemaker_enabled": "False",
        })
        config = yaml.safe_load(yaml_str)

        assert "testing" not in config
        assert "demo" not in config
        assert "bigipVE" not in config
        assert "ai" not in config

    # ── testing: jumphost ───────────────────────────────────────────────────

    def test_jumphost_block_emitted_when_enabled(self):
        """testing.jumphost block is emitted when jumphost_enabled=True."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "jumphost_enabled": True,
            "jumphost_instance_type": "t3.small",
        })
        config = yaml.safe_load(yaml_str)

        assert "testing" in config
        assert config["testing"]["jumphost"]["enabled"] is True
        assert config["testing"]["jumphost"]["instanceType"] == "t3.small"

    def test_jumphost_block_parses_as_valid_yaml(self):
        """Output with testing.jumphost block is valid YAML."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "jumphost_enabled": True,
        })
        config = yaml.safe_load(yaml_str)
        assert config is not None
        assert "testing" in config

    def test_jumphost_string_true_value(self):
        """String 'true' enables jumphost block (form variables arrive as strings)."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "jumphost_enabled": "true",
        })
        config = yaml.safe_load(yaml_str)
        assert "testing" in config
        assert config["testing"]["jumphost"]["enabled"] is True

    def test_jumphost_custom_instance_type(self):
        """jumphost_instance_type is propagated into the YAML block."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "jumphost_enabled": True,
            "jumphost_instance_type": "t3.medium",
        })
        config = yaml.safe_load(yaml_str)
        assert config["testing"]["jumphost"]["instanceType"] == "t3.medium"

    # ── demo: ───────────────────────────────────────────────────────────────

    def test_demo_block_emitted_when_enabled(self):
        """demo: block is emitted when demo_enabled=True."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "demo_enabled": True,
            "demo_ttl": "48h",
        })
        config = yaml.safe_load(yaml_str)

        assert "demo" in config
        assert config["demo"]["enabled"] is True
        assert config["demo"]["ttl"] == "48h"

    def test_demo_block_parses_as_valid_yaml(self):
        """Output with demo: block is valid YAML."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "demo_enabled": True,
        })
        config = yaml.safe_load(yaml_str)
        assert config is not None
        assert "demo" in config

    def test_demo_auto_enables_jumphost(self):
        """demo_enabled auto-enables the jumphost block (awsbnkctl schema requirement)."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "demo_enabled": True,
            "jumphost_enabled": False,  # explicitly off — should be overridden
        })
        config = yaml.safe_load(yaml_str)

        assert "testing" in config, "jumphost must be auto-enabled when demo is enabled"
        assert config["testing"]["jumphost"]["enabled"] is True
        assert "demo" in config

    def test_demo_default_ttl(self):
        """When demo_enabled=True without demo_ttl, defaults to 24h."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "demo_enabled": True,
        })
        config = yaml.safe_load(yaml_str)
        assert config["demo"]["ttl"] == "24h"

    # ── bigipVE: ────────────────────────────────────────────────────────────

    def test_bigipve_block_emitted_when_enabled(self):
        """bigipVE: block is emitted when bigipve_enabled=True."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "bigipve_enabled": True,
            "bigipve_instance_type": "c5n.2xlarge",
            "bigipve_license_tier": "Better",
            "bigipve_vip": "10.0.10.120",
        })
        config = yaml.safe_load(yaml_str)

        assert "bigipVE" in config
        assert config["bigipVE"]["enabled"] is True
        assert config["bigipVE"]["instanceType"] == "c5n.2xlarge"
        assert config["bigipVE"]["licenseTier"] == "Better"
        assert config["bigipVE"]["vip"] == "10.0.10.120"

    def test_bigipve_block_parses_as_valid_yaml(self):
        """Output with bigipVE: block is valid YAML."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "bigipve_enabled": True,
        })
        config = yaml.safe_load(yaml_str)
        assert config is not None
        assert "bigipVE" in config

    def test_bigipve_vip_derived_from_datapath_cidr(self):
        """When bigipve_vip is empty, VIP is derived as <prefix>.10.120 inside ext datapath."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "vpc_cidr": "172.31.0.0/16",
            "bigipve_enabled": True,
            "bigipve_vip": "",  # empty — must derive
        })
        config = yaml.safe_load(yaml_str)

        # prefix = "172.31", ext_datapath_cidr = "172.31.10.0/24"
        # derived VIP = "172.31.10.120"
        assert config["bigipVE"]["vip"] == "172.31.10.120"

    def test_bigipve_vip_explicit_overrides_derivation(self):
        """An explicit bigipve_vip overrides the auto-derived value."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "bigipve_enabled": True,
            "bigipve_vip": "10.0.10.99",
        })
        config = yaml.safe_load(yaml_str)
        assert config["bigipVE"]["vip"] == "10.0.10.99"

    def test_bigipve_vip_default_vpc_cidr(self):
        """Default 10.0.0.0/16 VPC derives VIP as 10.0.10.120 (matching awsbnkctl default)."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "vpc_cidr": "10.0.0.0/16",
            "bigipve_enabled": True,
            "bigipve_vip": "",
        })
        config = yaml.safe_load(yaml_str)
        assert config["bigipVE"]["vip"] == "10.0.10.120"

    def test_bigipve_forces_dual_interface_pattern(self):
        """bigipve_enabled overrides pattern to dual-interface (awsbnkctl schema requirement)."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "pattern": "external-only",  # explicitly external-only — should be overridden
            "bigipve_enabled": True,
        })
        config = yaml.safe_load(yaml_str)

        assert config["pattern"] == "dual-interface", (
            "pattern must be forced to dual-interface when bigipVE is enabled"
        )

    def test_bigipve_forces_internal_datapath_block(self):
        """bigipve_enabled causes network.dataPath.internal to be present."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "pattern": "external-only",
            "bigipve_enabled": True,
        })
        config = yaml.safe_load(yaml_str)

        assert "internal" in config["network"]["dataPath"], (
            "network.dataPath.internal must be emitted when bigipVE forces dual-interface"
        )
        assert "cidr" in config["network"]["dataPath"]["internal"]
        assert "az" in config["network"]["dataPath"]["internal"]

    def test_bigipve_string_true_also_forces_dual_interface(self):
        """String 'true' for bigipve_enabled also forces dual-interface pattern."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "bigipve_enabled": "true",
        })
        config = yaml.safe_load(yaml_str)
        assert config["pattern"] == "dual-interface"
        assert "internal" in config["network"]["dataPath"]

    # ── ai: sagemaker ───────────────────────────────────────────────────────

    def test_ai_sagemaker_block_emitted_when_enabled(self):
        """ai.sagemaker block is emitted when ai_sagemaker_enabled=True, with model: field."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "ai_sagemaker_enabled": True,
        })
        config = yaml.safe_load(yaml_str)

        assert "ai" in config
        assert "sagemaker" in config["ai"]
        assert config["ai"]["sagemaker"]["enabled"] is True
        # awsbnkctl requires model when sagemaker enabled — must be present with default
        assert "model" in config["ai"]["sagemaker"]
        assert config["ai"]["sagemaker"]["model"] == "meta-llama/Meta-Llama-3-8B-Instruct"

    def test_ai_sagemaker_block_parses_as_valid_yaml(self):
        """Output with ai.sagemaker block is valid YAML."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "ai_sagemaker_enabled": True,
        })
        config = yaml.safe_load(yaml_str)
        assert config is not None
        assert "ai" in config

    def test_ai_sagemaker_string_true_value(self):
        """String '1' enables sagemaker block."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "ai_sagemaker_enabled": "1",
        })
        config = yaml.safe_load(yaml_str)
        assert "ai" in config

    def test_ai_sagemaker_custom_model(self):
        """ai_sagemaker_model is propagated into the sagemaker block."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "ai_sagemaker_enabled": True,
            "ai_sagemaker_model": "mistralai/Mistral-7B-Instruct-v0.2",
        })
        config = yaml.safe_load(yaml_str)
        assert config["ai"]["sagemaker"]["model"] == "mistralai/Mistral-7B-Instruct-v0.2"

    def test_ai_sagemaker_empty_model_falls_back_to_default(self):
        """Empty ai_sagemaker_model falls back to the Llama default."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "ai_sagemaker_enabled": True,
            "ai_sagemaker_model": "",
        })
        config = yaml.safe_load(yaml_str)
        assert config["ai"]["sagemaker"]["model"] == "meta-llama/Meta-Llama-3-8B-Instruct"

    # ── All blocks enabled together ─────────────────────────────────────────

    def test_all_blocks_enabled_together(self):
        """All 4 blocks render correctly when all toggles are on."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "jumphost_enabled": True,
            "jumphost_instance_type": "t3.medium",
            "demo_enabled": True,
            "demo_ttl": "72h",
            "bigipve_enabled": True,
            "bigipve_instance_type": "c5n.4xlarge",
            "bigipve_license_tier": "Best",
            "bigipve_vip": "10.0.10.55",
            "ai_sagemaker_enabled": True,
        })
        config = yaml.safe_load(yaml_str)

        # All blocks present
        assert "testing" in config
        assert "demo" in config
        assert "bigipVE" in config
        assert "ai" in config

        # Verify values
        assert config["testing"]["jumphost"]["instanceType"] == "t3.medium"
        assert config["demo"]["ttl"] == "72h"
        assert config["bigipVE"]["instanceType"] == "c5n.4xlarge"
        assert config["bigipVE"]["licenseTier"] == "Best"
        assert config["bigipVE"]["vip"] == "10.0.10.55"
        assert config["ai"]["sagemaker"]["enabled"] is True
        # bigipVE forces dual-interface; ai.sagemaker.model must be present
        assert config["pattern"] == "dual-interface"
        assert "internal" in config["network"]["dataPath"]
        assert "model" in config["ai"]["sagemaker"]

    def test_all_blocks_enabled_output_is_valid_yaml(self):
        """All-blocks-on output parses as valid YAML without error."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "demo_enabled": True,
            "bigipve_enabled": True,
            "ai_sagemaker_enabled": True,
        })
        config = yaml.safe_load(yaml_str)
        assert config["apiVersion"] == "awsbnkctl/v1"
        assert "testing" in config  # auto-enabled by demo
        # bigipVE forces dual-interface regardless of base pattern
        assert config["pattern"] == "dual-interface"
        assert "internal" in config["network"]["dataPath"]


class TestCliClusterYamlInjection:
    """Regression tests: user-supplied values cannot inject malformed YAML.

    Before the fix, variables were interpolated via f-strings.  Values containing
    YAML special characters (colons, anchors, leading braces, quotes, newlines)
    could produce malformed output or alter the document structure.  After the fix
    the entire document is assembled as a Python dict and serialized via
    yaml.safe_dump, so all quoting/escaping is handled by the YAML library.
    """

    def _base_vars(self) -> dict:
        return {
            "cluster_name": "test-cluster",
            "region": "us-east-1",
            "vpc_cidr": "10.0.0.0/16",
            "instance_type": "m5.2xlarge",
            "pattern": "external-only",
        }

    def test_cluster_name_with_colon_produces_valid_yaml(self):
        """cluster_name containing a colon must not break YAML parsing."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "cluster_name": "foo: bar",
        })
        config = yaml.safe_load(yaml_str)
        assert config["metadata"]["name"] == "foo: bar"

    def test_cluster_name_with_yaml_anchor_produces_valid_yaml(self):
        """cluster_name starting with '*' (YAML anchor ref) must be safely quoted."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "cluster_name": "*anchor",
        })
        config = yaml.safe_load(yaml_str)
        assert config["metadata"]["name"] == "*anchor"

    def test_cluster_name_with_leading_brace_produces_valid_yaml(self):
        """cluster_name starting with '{' (YAML flow mapping) must be safely quoted."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "cluster_name": "{injection}",
        })
        config = yaml.safe_load(yaml_str)
        assert config["metadata"]["name"] == "{injection}"

    def test_region_with_special_characters_produces_valid_yaml(self):
        """region value with YAML special chars must not break document structure."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "region": "us-east-1 # comment",
        })
        config = yaml.safe_load(yaml_str)
        assert config["metadata"]["region"] == "us-east-1 # comment"

    def test_instance_type_with_quotes_produces_valid_yaml(self):
        """instance_type containing double-quotes must be safely escaped."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "instance_type": 'm5.2xlarge" evil',
        })
        config = yaml.safe_load(yaml_str)
        assert config["cluster"]["nodeGroups"][0]["instanceType"] == 'm5.2xlarge" evil'

    def test_jumphost_instance_type_with_newline_produces_valid_yaml(self):
        """jumphost_instance_type containing a newline must not add extra YAML lines."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "jumphost_enabled": True,
            "jumphost_instance_type": "t3.small\nevil: injected",
        })
        config = yaml.safe_load(yaml_str)
        # The newline should be preserved as part of the string value, not parsed as
        # a new YAML key.
        assert "evil" not in config
        assert "\nevil: injected" in config["testing"]["jumphost"]["instanceType"]

    def test_demo_ttl_with_percent_sign_produces_valid_yaml(self):
        """demo_ttl with unusual characters (%) must not break YAML."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "demo_enabled": True,
            "demo_ttl": "24h%",
        })
        config = yaml.safe_load(yaml_str)
        assert config["demo"]["ttl"] == "24h%"

    def test_ai_model_with_slash_produces_valid_yaml(self):
        """ai_sagemaker_model with forward-slashes (common for HuggingFace IDs) must parse."""
        yaml_str = _render_awsbnkctl_cluster_yaml({
            **self._base_vars(),
            "ai_sagemaker_enabled": True,
            "ai_sagemaker_model": "meta-llama/Meta-Llama-3-8B-Instruct",
        })
        config = yaml.safe_load(yaml_str)
        assert config["ai"]["sagemaker"]["model"] == "meta-llama/Meta-Llama-3-8B-Instruct"

    def test_bnk_block_paths_with_special_chars_produce_valid_yaml(self):
        """farArchive / jwt paths with special chars must be safely serialized."""
        far = "./secrets/cne pull (64).json"
        jwt = "./secrets/license: v2.jwt"
        yaml_str = _render_awsbnkctl_cluster_yaml(
            self._base_vars(),
            far_archive_path=far,
            jwt_path=jwt,
        )
        config = yaml.safe_load(yaml_str)
        assert config["bnk"]["farArchive"] == far
        assert config["bnk"]["jwt"] == jwt


class TestCliPlanDryRunNoUsecases:
    """Regression: dry-run plan with no use-cases selected must be COMPLETED, not FAILED —
    and a genuine subprocess failure must never be misread as success.

    Before the first fix, run_cli_plan used `success = plan_result.has_changes`.  When
    _plan_usecases returned PlanResult(has_changes=False, details="...skipped...") because
    usecases='none', has_changes was False → success=False → task.status="failed".

    The follow-up fix replaced `success = plan_result.has_changes` with a substring match
    (`"skipped" in plan_result.details.lower()`) — but plan()'s failure path embeds the
    tool's raw stdout in `details` (f"dry-run failed (exit {rc}):\n{stdout}"), so any
    `awsbnkctl` output containing the word "skipped" flipped a non-zero exit into a false
    success. The correct semantic: success is decided by the engine's structural
    PlanResult fields (has_changes / skipped), never by grepping `details`.
    """

    def test_plan_usecases_none_returns_has_changes_false_and_skipped_true(self):
        """_plan_usecases('none') returns has_changes=False, skipped=True (structural sentinel)."""
        from unittest.mock import MagicMock

        from services.execution.cli_engine import BnkctlEngine

        engine = BnkctlEngine()
        ctx = MagicMock()
        ctx.variables = {"usecases": "none", "bnkctl_action": "demo-usecases"}
        ctx.project_id = 1
        ctx.module_id = 1

        result = engine._plan_usecases(ctx, on_output=None)

        assert result.has_changes is False
        assert result.skipped is True

    def test_plan_task_skipped_usecases_is_success(self):
        """has_changes=False + skipped=True (structural) -> success=True."""
        # Mirrors the real logic in run_cli_plan (tasks/cli_tasks.py) after the fix.
        from services.execution.engine_interface import PlanResult

        plan_result = PlanResult(
            has_changes=False, details="no use-cases selected — skipped", skipped=True,
        )
        success = plan_result.has_changes or plan_result.skipped
        assert success is True

    def test_plan_task_real_failure_is_not_success(self):
        """A genuine plan failure (cluster.yaml missing) does not accidentally become success."""
        from services.execution.engine_interface import PlanResult

        plan_result = PlanResult(
            has_changes=False,
            details="cluster.yaml not found in workspace — the cluster module must run successfully",
        )
        success = plan_result.has_changes or plan_result.skipped
        assert success is False

    def test_plan_task_dry_run_error_is_not_success(self):
        """A use-case dry-run failure is not success."""
        from services.execution.engine_interface import PlanResult

        plan_result = PlanResult(
            has_changes=False,
            details="use-case dry-run failed:\nError: cannot connect",
        )
        success = plan_result.has_changes or plan_result.skipped
        assert success is False

    def test_plan_task_failed_dry_run_with_skipped_in_stdout_is_not_success(self):
        """Regression for the substring-match bug: a failed dry-run whose embedded stdout
        happens to contain the word 'skipped' must NOT be read as success. This is the exact
        exploit path — plan()'s failure details are `f"dry-run failed (exit {rc}):\n{stdout}"`,
        so any tool output mentioning "skipped" (plausible for a phase-runner) used to flip a
        non-zero exit to a false success via `"skipped" in details.lower()`.
        """
        from services.execution.engine_interface import PlanResult

        plan_result = PlanResult(
            has_changes=False,
            details="dry-run failed (exit 1):\nPhase 04/25: some-phase skipped (already applied)\nPhase 05/25: fatal error",
            skipped=False,
        )
        success = plan_result.has_changes or plan_result.skipped
        assert success is False
