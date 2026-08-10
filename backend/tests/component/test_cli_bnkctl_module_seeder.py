"""
Component tests for services.cli_bnkctl_module_seeder.
"""

from typing import Any
from unittest.mock import patch

import pytest

from models import ModuleLibrary
from services.cli_bnkctl_module_seeder import CLI_BNKCTL_MODULES, seed_cli_bnkctl_modules


@pytest.mark.component
class TestClibnkctlModuleSeeder:
    """Test cli-bnkctl module seeding.

    The awsbnkctl binary is not present in the test/CI environment (only mounted
    into real worker containers). These content tests assert on the seeded
    module.json regardless of binary presence, so binary availability is patched
    True for the duration of the class; the gate itself is covered separately by
    TestClibnkctlModuleSeederBinaryGate below.
    """

    @pytest.fixture(autouse=True)
    def _binary_available(self):
        with patch(
            "services.cli_bnkctl_module_seeder._bnkctl_binary_available", return_value=True,
        ):
            yield

    def test_seed_cli_bnkctl_modules_creates_entries(self, db: Any):
        """Test that seed_cli_bnkctl_modules creates ModuleLibrary entries."""
        created, updated = seed_cli_bnkctl_modules(db)
        assert created == len(CLI_BNKCTL_MODULES), f"Expected {len(CLI_BNKCTL_MODULES)} created, got {created}"
        assert updated == 0

    def test_seed_cli_bnkctl_modules_idempotent(self, db: Any):
        """Test that re-seeding is idempotent (updated count on second run)."""
        # First seed
        created1, updated1 = seed_cli_bnkctl_modules(db)
        assert created1 == len(CLI_BNKCTL_MODULES)
        assert updated1 == 0

        # Second seed (should update existing)
        created2, updated2 = seed_cli_bnkctl_modules(db)
        assert created2 == 0
        assert updated2 == 0  # No changes on re-run with same data

    def test_bnk_demo_module_exists(self, db: Any):
        """Test that the bnk-demo module is seeded with correct metadata."""
        seed_cli_bnkctl_modules(db)

        module = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "cli-bnkctl/awsbnkctl/bnk-demo"
        ).first()

        assert module is not None
        assert module.name == "AWS BNK Demo (CLI Deploy)"
        assert module.execution_engine == "cli-bnkctl"
        assert module.deploy_model == "cli-exec"
        assert module.module_source_kind == "builtin"
        assert module.is_active is True
        assert module.is_official is True

    def test_bnk_demo_variables_schema_populated(self, db: Any):
        """Test that the bnk-demo module has correct variable schema (list format)."""
        seed_cli_bnkctl_modules(db)

        module = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "cli-bnkctl/awsbnkctl/bnk-demo"
        ).first()

        assert module.variables_schema is not None
        schema = module.variables_schema
        assert isinstance(schema, list), "variables_schema must be a list of variable dicts"
        # 9 topology inputs: cluster_name, region, vpc_cidr, instance_type, pattern,
        # kubernetes_version, node_desired_size, node_min_size, node_max_size
        # + 10 demo-layer vars: demo_enabled, demo_ttl, jumphost_enabled,
        # jumphost_instance_type, bigipve_enabled, bigipve_instance_type,
        # bigipve_license_tier, bigipve_vip, ai_sagemaker_enabled, ai_sagemaker_model
        assert len(schema) == 19, f"Expected 19 variables, got {len(schema)}"

        # Extract variables by name for testing
        vars_by_name = {v["name"]: v for v in schema}

        # Check all editable topology inputs are present
        assert "cluster_name" in vars_by_name
        assert "region" in vars_by_name
        assert "vpc_cidr" in vars_by_name
        assert "instance_type" in vars_by_name
        assert "pattern" in vars_by_name

        # Check demo-layer vars are present
        assert "demo_enabled" in vars_by_name
        assert "jumphost_enabled" in vars_by_name
        assert "bigipve_enabled" in vars_by_name
        assert "ai_sagemaker_enabled" in vars_by_name

        # Check cluster_name (has default; all seeder vars are required=False to avoid blocking deploy)
        cluster_name = vars_by_name["cluster_name"]
        assert cluster_name["type"] == "string"
        assert cluster_name["default"] == "bnk-demo"
        assert cluster_name["required"] is False

        # Check region
        region = vars_by_name["region"]
        assert region["type"] == "string"
        assert region["default"] == "ap-southeast-2"
        assert region["required"] is False

        # Check vpc_cidr
        vpc_cidr = vars_by_name["vpc_cidr"]
        assert vpc_cidr["type"] == "string"
        assert vpc_cidr["default"] == "10.0.0.0/16"
        assert vpc_cidr["required"] is False

        # Check instance_type
        instance_type = vars_by_name["instance_type"]
        assert instance_type["type"] == "string"
        assert instance_type["default"] == "m5.2xlarge"
        assert instance_type["required"] is False

        # Check pattern
        pattern = vars_by_name["pattern"]
        assert pattern["type"] == "string"
        assert pattern["default"] == "external-only"
        assert pattern["required"] is False

    def test_bnk_demo_no_dependencies(self, db: Any):
        """Test that bnk-demo has no required dependencies."""
        seed_cli_bnkctl_modules(db)

        module = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "cli-bnkctl/awsbnkctl/bnk-demo"
        ).first()

        deps = module.dependencies_metadata
        assert deps["required"] == []
        assert deps["optional"] == []

    def test_seed_updates_existing_module_metadata(self, db: Any):
        """Test that re-seeding updates existing module metadata when it changes."""
        # First seed
        seed_cli_bnkctl_modules(db)

        # Manually change a field to simulate drift
        module = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "cli-bnkctl/awsbnkctl/bnk-demo"
        ).first()
        original_name = module.name
        module.name = "Modified Name"
        db.commit()

        # Re-seed should restore the original name
        created, updated = seed_cli_bnkctl_modules(db)
        assert created == 0
        assert updated == 1

        module = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "cli-bnkctl/awsbnkctl/bnk-demo"
        ).first()
        assert module.name == original_name

    def test_bnk_demo_inputs_metadata_declares_file_secrets(self, db: Any):
        """Test that bnk-demo inputs_metadata declares bnk_far_archive and bnk_jwt as file secrets."""
        seed_cli_bnkctl_modules(db)

        module = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "cli-bnkctl/awsbnkctl/bnk-demo"
        ).first()

        assert module.inputs_metadata is not None
        optional = module.inputs_metadata.get("optional", [])
        names = [inp["name"] for inp in optional]

        assert "bnk_far_archive" in names, "bnk_far_archive secret input must be declared"
        assert "bnk_jwt" in names, "bnk_jwt secret input must be declared"


@pytest.mark.component
class TestClibnkctlModuleSeederBinaryGate:
    """Regression: dist/registry workers have no awsbnkctl binary mounted.

    Seeding the module unconditionally there advertises a blueprint that fails
    every deploy at init with "Binary not found". The seeder must gate on binary
    presence instead.
    """

    def test_seed_skips_when_binary_absent(self, db: Any):
        """No binary -> no ModuleLibrary rows created; nothing advertised."""
        with patch(
            "services.cli_bnkctl_module_seeder._bnkctl_binary_available", return_value=False,
        ):
            created, updated = seed_cli_bnkctl_modules(db)

        assert created == 0
        assert updated == 0
        module = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "cli-bnkctl/awsbnkctl/bnk-demo"
        ).first()
        assert module is None

    def test_seed_deactivates_existing_rows_when_binary_becomes_absent(self, db: Any):
        """If rows were seeded while the binary was present and it later disappears
        (e.g. an image downgrade), the seeder must deactivate them rather than leave
        a broken module active in the library.
        """
        with patch(
            "services.cli_bnkctl_module_seeder._bnkctl_binary_available", return_value=True,
        ):
            seed_cli_bnkctl_modules(db)

        module = db.query(ModuleLibrary).filter(
            ModuleLibrary.path == "cli-bnkctl/awsbnkctl/bnk-demo"
        ).first()
        assert module is not None
        assert module.is_active is True

        with patch(
            "services.cli_bnkctl_module_seeder._bnkctl_binary_available", return_value=False,
        ):
            seed_cli_bnkctl_modules(db)

        db.refresh(module)
        assert module.is_active is False
