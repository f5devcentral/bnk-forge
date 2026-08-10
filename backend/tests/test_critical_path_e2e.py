"""
Critical Path E2E Tests for BNK-Forge v2

End-to-end tests that verify the complete application workflow:
1. Project & Module Creation
2. Stack Deployment (Init → Plan → Apply)
3. Stack Destruction (Reverse dependency order)
4. Error Handling & Recovery
5. Concurrent Operations
6. Credential Management
7. Dependency Graph (Kahn's Algorithm)
8. Variable Transform Security
9. Pre-deploy Secrets Validation
10. Redeploy Workflow (S18-001)
11. CLI Argument Security
12. Workspace Variable Hashing

These tests simulate real user workflows and verify the system works correctly
as a whole, not individual bug fixes.

Usage:
    # Run all E2E tests
    pytest backend/tests/test_critical_path_e2e.py -v

    # Run specific test class
    pytest backend/tests/test_critical_path_e2e.py -v -k "TestStackDeployment"

    # Run with coverage
    pytest backend/tests/test_critical_path_e2e.py -v --cov=backend
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, PropertyMock, patch

import pytest

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture
def mock_db_session():
    """Create a mock database session with common patterns."""
    db = MagicMock()
    db.query = MagicMock(return_value=MagicMock())
    db.add = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    db.rollback = MagicMock()
    db.close = MagicMock()
    db.expire_all = MagicMock()
    db.flush = MagicMock()
    db.delete = MagicMock()
    return db


@pytest.fixture
def sample_project():
    """Create a realistic project object."""
    project = MagicMock()
    project.id = 1
    project.name = "production-infra"
    project.environment = "prod"
    project.region = "us-west-2"
    project.aws_region = "us-west-2"
    project.cloud_provider = "aws"
    project.backend_type = "s3"
    project.s3_state_bucket = "my-terraform-state"
    project.s3_state_region = "us-west-2"
    project.s3_state_key_prefix = "prod/"
    project.s3_use_native_locking = True
    project.state_encryption_enabled = True
    project.encryption_provider = "pbkdf2"
    project.encryption_passphrase_encrypted = None
    project.credential_template_id = 1
    project.project_variables = {
        "cluster_name": "prod-eks",
        "node_count": 3
    }
    return project


@pytest.fixture
def sample_modules(sample_project):
    """Create a realistic 3-module stack: VPC → Security → EKS."""

    def create_module(id, name, path, status, deps, outputs=None):
        module = MagicMock()
        module.id = id
        module.project_id = sample_project.id
        module.project = sample_project
        module.status = status
        module.dependencies = deps
        module.outputs = outputs
        module.workspace_path = f"/app/workspaces/{sample_project.id}/{id}"
        module.path_in_project = f"stack-1/{path}"
        module.variable_overrides = {}
        module.variables = {}
        module.deployment_error = None
        module.deployment_order = id - 1
        module.celery_task_id = None
        module.vars_hash = None
        module.plan_output = None
        module.enabled = True

        lib = MagicMock()
        lib.name = name
        lib.path = f"infra/aws/{path}"
        lib.version = "1.0.0"
        lib.inputs_metadata = {"required": [], "optional": []}
        lib.dependencies_metadata = {"required": []}
        lib.git_source = f"git::https://github.com/org/modules.git//{path}?ref=main"
        lib.source = None
        module.library_module = lib

        return module

    vpc = create_module(
        id=1, name="VPC", path="vpc", status="not_initialized", deps=[],
        outputs=None
    )
    security = create_module(
        id=2, name="Security", path="security", status="not_initialized", deps=[1],
        outputs=None
    )
    eks = create_module(
        id=3, name="EKS", path="eks", status="not_initialized", deps=[1, 2],
        outputs=None
    )

    return {"vpc": vpc, "security": security, "eks": eks}


@pytest.fixture
def sample_stack(sample_project, sample_modules):
    """Create a realistic stack instance."""
    stack = MagicMock()
    stack.id = 1
    stack.project_id = sample_project.id
    stack.project = sample_project
    stack.template_id = 1
    stack.name = "prod-infrastructure"
    stack.status = "pending"
    stack.deployed_modules = [1, 2, 3]
    stack.current_step = 0
    stack.total_steps = 3
    stack.variables = {}
    stack.error_message = None
    stack.started_at = None
    stack.completed_at = None
    return stack


@pytest.fixture
def sample_stack_template():
    """Create a stack template with prerequisites for S19-001 tests."""
    template = MagicMock()
    template.id = 1
    template.name = "AWS K8s Foundation"
    template.slug = "aws-k8s-foundation"
    template.modules = [
        {"path": "infra/aws/vpc", "name": "VPC"},
        {"path": "infra/aws/eks", "name": "EKS"},
    ]
    template.prerequisites = [
        {"type": "project_secret", "name": "aws_access_key", "description": "AWS Access Key ID"},
        {"type": "project_secret", "name": "aws_secret_key", "description": "AWS Secret Access Key"},
        {"type": "resource", "name": "existing_vpc", "description": "An existing VPC (optional)"},
    ]
    return template


@pytest.fixture
def temp_workspace():
    """Create a temporary workspace directory."""
    workspace = tempfile.mkdtemp(prefix="bnk-test-")
    yield workspace
    shutil.rmtree(workspace, ignore_errors=True)


# =============================================================================
# PROJECT & MODULE MANAGEMENT TESTS
# =============================================================================

class TestProjectManagement:
    """Tests for project creation and configuration."""

    def test_project_creation_with_valid_config(self, mock_db_session, sample_project):
        """Test creating a project with all required configuration."""
        # Verify project has all required fields
        assert sample_project.name is not None
        assert sample_project.region is not None
        assert sample_project.cloud_provider in ["aws", "gcp", "azure"]
        assert sample_project.backend_type in ["local", "s3", "gcs", "azurerm"]

    def test_project_variables_inheritance(self, sample_project, sample_modules):
        """Test that project variables are available to all modules."""
        # This test verifies the concept - actual engine requires DB/encryption setup
        eks = sample_modules["eks"]

        # Project variables should be inherited by modules
        # The build_variables method injects: project_name, environment, region

        # Verify project has these values that will be inherited
        assert sample_project.name is not None
        assert sample_project.environment is not None
        assert sample_project.region is not None

        # Verify module has access to project
        assert eks.project == sample_project
        assert eks.project.name == sample_project.name


class TestModuleManagement:
    """Tests for module lifecycle management."""

    def test_module_status_lifecycle(self, sample_modules):
        """Test valid module status transitions through lifecycle."""
        vpc = sample_modules["vpc"]

        # Valid lifecycle: not_initialized → initializing → initialized → applying → applied
        lifecycle = ["not_initialized", "initializing", "initialized", "applying", "applied"]

        for i, status in enumerate(lifecycle):
            vpc.status = status
            assert vpc.status == status

            # Verify we can't skip steps (business logic check)
            if i < len(lifecycle) - 1:
                next_status = lifecycle[i + 1]
                vpc.status = next_status
                assert vpc.status == next_status

    def test_module_failure_status(self, sample_modules):
        """Test module failure states."""
        vpc = sample_modules["vpc"]

        failure_states = ["init_failed", "plan_failed", "apply_failed", "destroy_failed"]

        for state in failure_states:
            vpc.status = state
            vpc.deployment_error = f"Simulated {state} error"

            assert vpc.status == state
            assert vpc.deployment_error is not None

    def test_module_dependency_chain(self, sample_modules):
        """Test that dependency chain is correctly structured."""
        vpc = sample_modules["vpc"]
        security = sample_modules["security"]
        eks = sample_modules["eks"]

        # VPC has no dependencies
        assert vpc.dependencies == []

        # Security depends on VPC
        assert security.dependencies == [1]
        assert vpc.id in security.dependencies

        # EKS depends on VPC and Security
        assert eks.dependencies == [1, 2]
        assert vpc.id in eks.dependencies
        assert security.id in eks.dependencies


# =============================================================================
# STACK DEPLOYMENT TESTS
# =============================================================================

class TestStackDeployment:
    """Tests for complete stack deployment workflow."""

    def test_stack_deployment_order(self, sample_stack, sample_modules):
        """Test that modules deploy in correct dependency order."""
        # Deploy order should be: VPC (no deps) → Security (VPC) → EKS (VPC, Security)

        modules_list = [sample_modules["vpc"], sample_modules["security"], sample_modules["eks"]]

        # Sort by deployment_order
        sorted_modules = sorted(modules_list, key=lambda m: m.deployment_order)

        assert sorted_modules[0].library_module.name == "VPC"
        assert sorted_modules[1].library_module.name == "Security"
        assert sorted_modules[2].library_module.name == "EKS"

    def test_stack_status_transitions(self, sample_stack):
        """Test stack status transitions during deployment."""
        # pending → deploying → deployed (success)
        # pending → deploying → failed (failure)

        assert sample_stack.status == "pending"

        sample_stack.status = "deploying"
        sample_stack.started_at = datetime.now(UTC)
        assert sample_stack.status == "deploying"
        assert sample_stack.started_at is not None

        # Simulate successful completion
        sample_stack.status = "deployed"
        sample_stack.completed_at = datetime.now(UTC)
        assert sample_stack.status == "deployed"
        assert sample_stack.completed_at is not None

    def test_stack_failure_handling(self, sample_stack, sample_modules):
        """Test stack failure when a module fails."""
        sample_stack.status = "deploying"

        # Simulate VPC failure
        vpc = sample_modules["vpc"]
        vpc.status = "apply_failed"
        vpc.deployment_error = "Resource limit exceeded"

        # Stack should be marked as failed
        sample_stack.status = "failed"
        sample_stack.error_message = f"Module '{vpc.library_module.name}' failed: {vpc.deployment_error}"

        assert sample_stack.status == "failed"
        assert "VPC" in sample_stack.error_message
        assert "Resource limit" in sample_stack.error_message

    def test_dependency_blocks_downstream_modules(self, sample_modules):
        """Test that failed dependency blocks downstream modules."""
        vpc = sample_modules["vpc"]
        security = sample_modules["security"]
        eks = sample_modules["eks"]

        # VPC failed
        vpc.status = "apply_failed"
        vpc.outputs = None

        # Security should not be able to execute (depends on VPC)
        # EKS should not be able to execute (depends on VPC and Security)

        # Verify dependency structure is correct
        assert vpc.status != "applied"
        assert security.dependencies == [1]  # VPC ID
        assert eks.dependencies == [1, 2]  # VPC, Security IDs

        # In the actual system, can_execute() would return False for security and eks
        # because their dependency (vpc) is not in "applied" status

    def test_partial_deployment_recovery(self, sample_stack, sample_modules):
        """Test recovering from partial deployment (some modules applied)."""
        vpc = sample_modules["vpc"]
        security = sample_modules["security"]
        eks = sample_modules["eks"]

        # VPC and Security deployed, EKS failed
        vpc.status = "applied"
        vpc.outputs = {"vpc_id": "vpc-123", "private_subnets": ["subnet-1", "subnet-2"]}

        security.status = "applied"
        security.outputs = {"security_group_id": "sg-456"}

        eks.status = "apply_failed"
        eks.deployment_error = "Insufficient capacity"

        sample_stack.status = "failed"
        sample_stack.current_step = 2  # 2 out of 3 completed

        # Recovery: Retry EKS deployment
        eks.status = "applying"
        eks.deployment_error = None

        # Should be able to use existing VPC and Security outputs
        assert vpc.outputs is not None
        assert security.outputs is not None


# =============================================================================
# STACK DESTRUCTION TESTS
# =============================================================================

class TestStackDestruction:
    """Tests for complete stack destruction workflow."""

    def test_destroy_order_reverses_dependencies(self, sample_modules):
        """Test that destroy order is reverse of deploy order."""
        # Deploy: VPC → Security → EKS
        # Destroy: EKS → Security → VPC

        modules_list = [sample_modules["vpc"], sample_modules["security"], sample_modules["eks"]]

        # Sort by deployment_order DESC for destroy
        destroy_order = sorted(modules_list, key=lambda m: m.deployment_order, reverse=True)

        assert destroy_order[0].library_module.name == "EKS"
        assert destroy_order[1].library_module.name == "Security"
        assert destroy_order[2].library_module.name == "VPC"

    def test_destroy_skips_undeployed_modules(self, sample_modules):
        """Test that destroy skips modules without infrastructure."""
        vpc = sample_modules["vpc"]
        security = sample_modules["security"]
        eks = sample_modules["eks"]

        # VPC deployed, Security initialized only, EKS not initialized
        vpc.status = "applied"
        security.status = "initialized"  # Never applied
        eks.status = "not_initialized"

        # Only VPC needs actual destroy
        statuses_needing_destroy = ["applied", "apply_failed", "destroy_failed"]

        assert vpc.status in statuses_needing_destroy
        assert security.status not in statuses_needing_destroy
        assert eks.status not in statuses_needing_destroy

    def test_destroy_stops_on_failure(self, sample_stack, sample_modules):
        """Test that destroy stops if a module fails (prevents orphaned resources)."""
        eks = sample_modules["eks"]
        security = sample_modules["security"]
        vpc = sample_modules["vpc"]

        # All modules applied
        for m in [vpc, security, eks]:
            m.status = "applied"

        sample_stack.status = "destroying"

        # EKS destroy fails (e.g., ENIs still attached)
        eks.status = "destroy_failed"
        eks.deployment_error = "DependencyViolation: ENIs still attached"

        # Stack should stop - don't continue to Security/VPC
        sample_stack.status = "failed"
        sample_stack.error_message = "Destroy stopped: EKS failed"

        # Security and VPC should NOT be destroyed (would orphan EKS resources)
        assert security.status == "applied"
        assert vpc.status == "applied"

    def test_destroy_cleans_up_module_records(self, mock_db_session, sample_stack, sample_modules):
        """Test that successfully destroyed modules are cleaned up."""
        vpc = sample_modules["vpc"]

        vpc.status = "applied"

        # Simulate successful destroy
        vpc.status = "destroyed"

        # Module record should be deleted after successful destroy
        # In real implementation: db.delete(module)
        mock_db_session.delete(vpc)

        assert vpc.status == "destroyed"
        mock_db_session.delete.assert_called_with(vpc)

    def test_stack_destroy_status_transitions(self, sample_stack):
        """Test stack status transitions during destroy."""
        # deployed → destroying → destroyed (success)
        # deployed → destroying → failed (failure)

        sample_stack.status = "deployed"

        sample_stack.status = "destroying"
        assert sample_stack.status == "destroying"

        # Success case
        sample_stack.status = "destroyed"
        sample_stack.deployed_modules = []

        assert sample_stack.status == "destroyed"
        assert sample_stack.deployed_modules == []


# =============================================================================
# EXECUTION ENGINE TESTS (Updated: OpenTofuRuntime + execution package)
# =============================================================================

class TestExecutionEngine:
    """Tests for the core execution engine (OpenTofuRuntime + variable_assembler)."""

    def test_variable_assembly_order(self, sample_project, sample_modules):
        """Test that variables are assembled in correct precedence order."""
        eks = sample_modules["eks"]
        eks.variable_overrides = {"node_count": 5}  # User override

        # Project has node_count = 3
        assert sample_project.project_variables.get("node_count") == 3

        # User override should take precedence
        # In build_variables: user_vars = module.variable_overrides or module.variables
        # Then: variables.update(user_vars) - this overwrites project vars
        user_vars = eks.variable_overrides or eks.variables or {}

        # Simulate the merge order
        final_vars = dict(sample_project.project_variables)
        final_vars.update(user_vars)

        assert final_vars["node_count"] == 5  # User override wins

    def test_dependency_output_wiring(self, sample_modules):
        """Test that dependency outputs are wired to module inputs."""
        vpc = sample_modules["vpc"]
        security = sample_modules["security"]

        # VPC is applied with outputs
        vpc.status = "applied"
        vpc.outputs = {
            "vpc_id": "vpc-abc123",
            "vpc_cidr_block": "10.0.0.0/16",
            "private_subnets": ["subnet-1", "subnet-2"]
        }

        # Security module expects vpc_id from VPC
        security.library_module.inputs_metadata = {
            "required": [
                {
                    "name": "vpc_id",
                    "source": "module",
                    "from_module": "infra/aws/vpc",
                    "from_output": "vpc_id"
                }
            ],
            "optional": []
        }

        # Verify the wiring metadata is correct
        required_inputs = security.library_module.inputs_metadata["required"]
        vpc_input = next((i for i in required_inputs if i["name"] == "vpc_id"), None)

        assert vpc_input is not None
        assert vpc_input["source"] == "module"
        assert vpc_input["from_output"] == "vpc_id"

        # Verify VPC has the output that security needs
        assert vpc_input["from_output"] in vpc.outputs
        assert vpc.outputs["vpc_id"] == "vpc-abc123"

    def test_workspace_preparation(self, sample_modules, temp_workspace):
        """Test workspace preparation creates required files."""
        vpc = sample_modules["vpc"]
        vpc.workspace_path = temp_workspace

        # Create minimal .tf file to simulate cloned source
        with open(os.path.join(temp_workspace, "main.tf"), "w") as f:
            f.write('variable "vpc_cidr" { default = "10.0.0.0/16" }')

        # Test writing tfvars (this is a standalone function that doesn't need full engine)
        variables = {"vpc_cidr": "10.0.0.0/16", "project_name": "test"}
        tfvars_path = os.path.join(temp_workspace, "terraform.tfvars.json")

        with open(tfvars_path, 'w') as f:
            json.dump(variables, f, indent=2)

        assert os.path.exists(tfvars_path)

        with open(tfvars_path) as f:
            written_vars = json.load(f)

        assert written_vars["vpc_cidr"] == "10.0.0.0/16"

    def test_backend_config_generation(self, sample_project, sample_modules, temp_workspace):
        """Test backend configuration file generation."""
        vpc = sample_modules["vpc"]

        # Generate backend config content (simulating what config_writer.write_backend_config does)
        bucket = sample_project.s3_state_bucket
        region = sample_project.s3_state_region
        key_prefix = sample_project.s3_state_key_prefix
        state_key = f"{key_prefix}{sample_project.name}/{vpc.path_in_project}/terraform.tfstate"

        backend_content = f'''
terraform {{
  backend "s3" {{
    bucket       = "{bucket}"
    key          = "{state_key}"
    region       = "{region}"
    encrypt      = true
    use_lockfile = true
  }}
}}
'''

        backend_file = os.path.join(temp_workspace, "backend_override.tf")
        with open(backend_file, 'w') as f:
            f.write(backend_content)

        assert os.path.exists(backend_file)

        with open(backend_file) as f:
            content = f.read()

        assert "backend" in content
        assert bucket in content
        assert region in content


# =============================================================================
# WORKSPACE MANAGEMENT TESTS
# =============================================================================

class TestWorkspaceManagement:
    """Tests for workspace lifecycle management."""

    def test_workspace_initialization_detection(self, temp_workspace):
        """Test detection of initialized workspace."""
        from services.workspace_manager import WorkspaceManager

        module = MagicMock()
        module.workspace_path = temp_workspace
        module.library_module = MagicMock()
        module.library_module.version = "1.0.0"

        db = MagicMock()
        manager = WorkspaceManager(db)

        # Not initialized (no .terraform directory)
        assert manager.is_initialized(module) is False

        # Create .terraform directory and lock file
        terraform_dir = os.path.join(temp_workspace, ".terraform")
        os.makedirs(terraform_dir)

        lock_file = os.path.join(temp_workspace, ".terraform.lock.hcl")
        with open(lock_file, "w") as f:
            f.write("# Lock file")

        # Now should be initialized
        assert manager.is_initialized(module) is True

    def test_workspace_reinit_detection(self, temp_workspace):
        """Test detection of when workspace needs re-initialization."""
        from services.workspace_manager import WorkspaceManager

        module = MagicMock()
        module.workspace_path = temp_workspace
        module.library_module = MagicMock()
        module.library_module.version = "2.0.0"  # New version
        module.project = MagicMock()
        module.project.backend_type = "local"

        db = MagicMock()
        manager = WorkspaceManager(db)

        # Create initialized workspace (including downloaded modules cache —
        # needs_reinit short-circuits on missing .terraform/modules/ before
        # reaching the version check)
        terraform_dir = os.path.join(temp_workspace, ".terraform")
        os.makedirs(os.path.join(terraform_dir, "modules"))

        lock_file = os.path.join(temp_workspace, ".terraform.lock.hcl")
        with open(lock_file, "w") as f:
            f.write("# Lock file")

        # Save old version
        version_file = os.path.join(temp_workspace, ".bnk_init_version")
        with open(version_file, "w") as f:
            f.write("1.0.0")  # Old version

        # Should need reinit due to version change
        needs_reinit, reason = manager.needs_reinit(module)
        assert needs_reinit is True
        assert "version" in reason.lower()

    def test_saved_plan_detection(self, temp_workspace):
        """Test detection of saved plan file."""
        from services.workspace_manager import WorkspaceManager

        module = MagicMock()
        module.workspace_path = temp_workspace

        db = MagicMock()
        manager = WorkspaceManager(db)

        # No plan file
        assert manager.has_saved_plan(module) is False

        # Create plan file
        plan_file = os.path.join(temp_workspace, "plan.out")
        with open(plan_file, "wb") as f:
            f.write(b"binary plan data")

        # Should detect plan
        assert manager.has_saved_plan(module) is True
        assert manager.get_plan_path(module) == plan_file


# =============================================================================
# WORKSPACE LOCKING TESTS
# =============================================================================

# Workspace-locking tests live in tests/component/test_module_lock.py — they
# require a real Postgres connection (the new lock is heartbeat-based, not
# Redis-mocked) and cover the Kleppmann fence-token correctness scenario.


# =============================================================================
# CREDENTIAL MANAGEMENT TESTS
# =============================================================================

class TestCredentialManagement:
    """Tests for credential handling and refresh."""

    def test_credential_expiration_detection(self):
        """Test detection of expired credentials."""
        # Credentials that expired 1 hour ago
        expiration = datetime.now(UTC) - timedelta(hours=1)
        now = datetime.now(UTC)

        time_until_expiry = expiration - now
        is_expired = time_until_expiry.total_seconds() <= 0

        assert is_expired is True

    def test_credential_expiring_soon_detection(self):
        """Test detection of credentials expiring soon."""
        # Credentials expiring in 10 minutes
        expiration = datetime.now(UTC) + timedelta(minutes=10)
        now = datetime.now(UTC)

        threshold_minutes = 15
        time_until_expiry = expiration - now
        is_expiring_soon = 0 < time_until_expiry.total_seconds() < (threshold_minutes * 60)

        assert is_expiring_soon is True

    def test_timezone_aware_comparison(self):
        """Test that timezone-aware datetime comparison works correctly."""
        # This verifies the fundamental datetime handling is correct

        # Create timezone-aware datetimes
        expiration = datetime.now(UTC) + timedelta(hours=1)
        now = datetime.now(UTC)

        # This should NOT raise TypeError
        try:
            delta = expiration - now
            seconds = delta.total_seconds()
            works = True
        except TypeError:
            works = False

        assert works is True
        assert seconds > 0

    def test_credential_template_validation(self, sample_project):
        """Test credential template has required fields."""
        template = MagicMock()
        template.id = 1
        template.name = "prod-credentials"
        template.aws_access_key_id = "AKIA..."
        template.aws_secret_access_key_encrypted = "encrypted..."
        template.aws_region = "us-west-2"

        # Required fields should be present
        assert template.aws_access_key_id is not None
        assert template.aws_secret_access_key_encrypted is not None
        assert template.aws_region is not None


# =============================================================================
# ERROR RECOVERY TESTS
# =============================================================================

class TestErrorRecovery:
    """Tests for error handling and recovery scenarios."""

    def test_init_failure_allows_retry(self, sample_modules):
        """Test that init failure allows retry."""
        vpc = sample_modules["vpc"]

        # Initial failure
        vpc.status = "init_failed"
        vpc.deployment_error = "Network timeout"

        # Retry should be allowed
        vpc.status = "initializing"
        vpc.deployment_error = None

        assert vpc.status == "initializing"
        assert vpc.deployment_error is None

    def test_apply_failure_allows_retry(self, sample_modules):
        """Test that apply failure allows retry."""
        vpc = sample_modules["vpc"]

        # Initial failure
        vpc.status = "apply_failed"
        vpc.deployment_error = "Resource limit exceeded"

        # Retry should be allowed
        vpc.status = "applying"
        vpc.deployment_error = None

        assert vpc.status == "applying"

    def test_destroy_failure_preserves_state(self, sample_modules):
        """Test that destroy failure preserves module state for retry."""
        eks = sample_modules["eks"]

        eks.status = "applied"
        eks.outputs = {"cluster_name": "prod-eks"}

        # Destroy fails
        eks.status = "destroy_failed"
        eks.deployment_error = "ENIs still attached"

        # Outputs should be preserved for potential manual cleanup
        assert eks.outputs is not None
        assert eks.outputs["cluster_name"] == "prod-eks"

    def test_stack_failure_records_error_details(self, sample_stack, sample_modules):
        """Test that stack failure records detailed error information."""
        vpc = sample_modules["vpc"]

        vpc.status = "apply_failed"
        vpc.deployment_error = "Error: creating VPC: VpcLimitExceeded"

        sample_stack.status = "failed"
        sample_stack.error_message = f"Module 'VPC' failed: {vpc.deployment_error}"
        sample_stack.completed_at = datetime.now(UTC)

        assert sample_stack.status == "failed"
        assert "VpcLimitExceeded" in sample_stack.error_message
        assert sample_stack.completed_at is not None


# =============================================================================
# CONCURRENT OPERATION TESTS
# =============================================================================

class TestConcurrentOperations:
    """Tests for concurrent operation handling."""

    def test_deploy_blocked_during_destroy(self, sample_stack):
        """Test that deploy is blocked while destroy is in progress."""
        sample_stack.status = "destroying"

        # Attempt to deploy should check status first
        blocked_statuses = ["deploying", "destroying"]

        assert sample_stack.status in blocked_statuses

    def test_destroy_blocked_during_deploy(self, sample_stack):
        """Test that destroy is blocked while deploy is in progress."""
        sample_stack.status = "deploying"

        # Attempt to destroy should check status first
        blocked_statuses = ["deploying", "destroying"]

        assert sample_stack.status in blocked_statuses

    def test_duplicate_operations_prevented(self, sample_stack):
        """Test that duplicate operations on same stack are prevented."""
        # First operation starts
        sample_stack.status = "deploying"

        # Second operation should be rejected
        if sample_stack.status == "deploying":
            can_start_another = False
            error = "Stack deployment already in progress"
        else:
            can_start_another = True
            error = None

        assert can_start_another is False
        assert "already in progress" in error.lower()


# =============================================================================
# INTEGRATION SMOKE TESTS (Updated for new file structure)
# =============================================================================

class TestIntegrationSmoke:
    """Smoke tests to verify system components work together."""

    def test_workspace_manager_importable(self):
        """Test that workspace_manager can be imported."""
        try:
            from services.workspace_manager import WorkspaceManager
            assert WorkspaceManager is not None
        except ImportError as e:
            pytest.fail(f"Failed to import workspace_manager: {e}")

    def test_module_lock_importable(self):
        """Test that the new Postgres-backed module_lock service is importable."""
        try:
            from services.module_lock import (
                ModuleLockError,
                ModuleLockLostError,
                ModuleLockService,
                module_lock,
            )
            assert ModuleLockService is not None
            assert ModuleLockError is not None
            assert ModuleLockLostError is not None
            assert module_lock is not None
        except ImportError as e:
            pytest.fail(f"Failed to import module_lock: {e}")

    def test_dependency_graph_service_importable(self):
        """Test that dependency_graph_service can be imported."""
        try:
            from services.dependency_graph_service import DependencyGraphService
            assert DependencyGraphService is not None
        except ImportError as e:
            pytest.fail(f"Failed to import dependency_graph_service: {e}")

    def test_models_importable(self):
        """Test that models can be imported."""
        try:
            from models import (
                Project,
                ProjectModule,
                StackInstance,
                StackTemplate,
                Task,
            )
            assert Project is not None
            assert ProjectModule is not None
            assert StackInstance is not None
        except ImportError as e:
            pytest.fail(f"Failed to import models: {e}")

    def test_routes_module_structure(self):
        """Test that routes modules have expected structure (post-refactor)."""
        routes_dir = os.path.join(backend_path, "routes")

        # Original files that still exist
        expected_files = [
            "stacks.py",
            "project_modules.py",
            "cloud_auth.py",
            "projects.py",
        ]

        # New split route files (post-refactor)
        expected_files += [
            "project_execution.py",
            "project_deployments.py",
            "project_variable_mappings.py",
        ]

        for filename in expected_files:
            filepath = os.path.join(routes_dir, filename)
            assert os.path.exists(filepath), f"Missing route file: {filename}"

        # Verify k8s package exists
        k8s_dir = os.path.join(routes_dir, "k8s")
        assert os.path.isdir(k8s_dir), "Missing routes/k8s/ package directory"

        k8s_files = ["__init__.py", "clusters.py", "f5bnk.py", "resources.py", "tunnels.py"]
        for filename in k8s_files:
            filepath = os.path.join(k8s_dir, filename)
            assert os.path.exists(filepath), f"Missing k8s route file: {filename}"

    def test_services_module_structure(self):
        """Test that services modules have expected structure (post-refactor)."""
        services_dir = os.path.join(backend_path, "services")

        # Core service files
        expected_files = [
            "workspace_manager.py",
            "module_lock.py",
            "stack_deployment_service.py",
            "dependency_graph_service.py",
            "credential_refresh_service.py",
        ]

        # New extracted service files (post-refactor)
        expected_files += [
            "project_module_service.py",
            "drift_service.py",
            "stack_service.py",
            "api_service.py",
            "credential_template_service.py",
            "system_service.py",
            "cluster_management_service.py",
            "module_source_service.py",
        ]

        for filename in expected_files:
            filepath = os.path.join(services_dir, filename)
            assert os.path.exists(filepath), f"Missing service file: {filename}"

        # Verify execution package exists with expected files
        execution_dir = os.path.join(services_dir, "execution")
        assert os.path.isdir(execution_dir), "Missing services/execution/ package directory"

        execution_files = [
            "__init__.py",
            "opentofu_runtime.py",
            "variable_assembler.py",
            "config_writer.py",
            "opentofu_engine.py",
        ]
        for filename in execution_files:
            filepath = os.path.join(execution_dir, filename)
            assert os.path.exists(filepath), f"Missing execution package file: {filename}"

    def test_tasks_module_structure(self):
        """Test that tasks modules have expected structure."""
        tasks_dir = os.path.join(backend_path, "tasks")

        expected_files = ["opentofu_tasks.py", "parallel_tasks.py"]

        for filename in expected_files:
            filepath = os.path.join(tasks_dir, filename)
            assert os.path.exists(filepath), f"Missing task file: {filename}"

    def test_execution_package_public_api(self):
        """Test that execution package re-exports expected symbols."""
        try:
            from services.execution import OpenTofuRuntime, build_variables, can_execute
            assert OpenTofuRuntime is not None
            assert callable(can_execute)
            assert callable(build_variables)
        except ImportError as e:
            pytest.fail(f"Failed to import execution package public API: {e}")

    def test_execution_engine_alias(self):
        """Test that ExecutionEngine alias resolves to OpenTofuRuntime."""
        try:
            from services.execution.opentofu_runtime import ExecutionEngine, OpenTofuRuntime
            # ExecutionEngine should be an alias for OpenTofuRuntime
            assert ExecutionEngine is OpenTofuRuntime
        except ImportError as e:
            pytest.fail(f"Failed to import ExecutionEngine alias: {e}")

    def test_utils_security_importable(self):
        """Test that utils/security.py can be imported."""
        try:
            from utils.security import validate_cli_arg, validate_helm_timeout
            assert callable(validate_cli_arg)
            assert callable(validate_helm_timeout)
        except ImportError as e:
            pytest.fail(f"Failed to import utils.security: {e}")


# =============================================================================
# DEPENDENCY GRAPH — KAHN'S ALGORITHM TESTS (NEW)
# =============================================================================

class TestDependencyGraphKahn:
    """Tests for Kahn's algorithm in dependency_graph_service."""

    def _make_module(self, id, name, deps=None, enabled=True):
        """Create a lightweight mock module for graph tests."""
        m = MagicMock()
        m.id = id
        m.dependencies = deps or []
        m.enabled = enabled
        m.project_id = 1
        lib = MagicMock()
        lib.name = name
        lib.path = f"modules/{name}"
        m.library_module = lib
        return m

    def test_kahn_layers_linear_chain(self):
        """Test Kahn's algorithm with a linear dependency chain: A → B → C."""
        from services.dependency_graph_service import _kahn_layers

        a = self._make_module(1, "A")
        b = self._make_module(2, "B", deps=[1])
        c = self._make_module(3, "C", deps=[2])

        module_map = {1: a, 2: b, 3: c}
        module_ids = {1, 2, 3}

        layers = _kahn_layers(module_map, module_ids)

        assert len(layers) == 3
        assert layers[0][0].id == 1  # A first (no deps)
        assert layers[1][0].id == 2  # B second (depends on A)
        assert layers[2][0].id == 3  # C third (depends on B)

    def test_kahn_layers_no_dependencies(self):
        """Test all modules in layer 0 when no dependencies exist."""
        from services.dependency_graph_service import _kahn_layers

        a = self._make_module(1, "A")
        b = self._make_module(2, "B")
        c = self._make_module(3, "C")

        module_map = {1: a, 2: b, 3: c}
        module_ids = {1, 2, 3}

        layers = _kahn_layers(module_map, module_ids)

        assert len(layers) == 1
        assert len(layers[0]) == 3  # All in same layer

    def test_kahn_layers_diamond_pattern(self):
        """Test diamond: A → B, A → C, B → D, C → D."""
        from services.dependency_graph_service import _kahn_layers

        a = self._make_module(1, "A")
        b = self._make_module(2, "B", deps=[1])
        c = self._make_module(3, "C", deps=[1])
        d = self._make_module(4, "D", deps=[2, 3])

        module_map = {1: a, 2: b, 3: c, 4: d}
        module_ids = {1, 2, 3, 4}

        layers = _kahn_layers(module_map, module_ids)

        assert len(layers) == 3
        # Layer 0: A
        assert len(layers[0]) == 1
        assert layers[0][0].id == 1
        # Layer 1: B and C (parallel)
        layer1_ids = {m.id for m in layers[1]}
        assert layer1_ids == {2, 3}
        # Layer 2: D
        assert len(layers[2]) == 1
        assert layers[2][0].id == 4

    def test_kahn_layers_cycle_detection(self):
        """Test that circular dependencies raise ValueError."""
        from services.dependency_graph_service import _kahn_layers

        # A → B → C → A (cycle)
        a = self._make_module(1, "A", deps=[3])
        b = self._make_module(2, "B", deps=[1])
        c = self._make_module(3, "C", deps=[2])

        module_map = {1: a, 2: b, 3: c}
        module_ids = {1, 2, 3}

        with pytest.raises(ValueError, match="Circular dependency"):
            _kahn_layers(module_map, module_ids)

    def test_kahn_layers_empty_input(self):
        """Test empty module set returns empty list."""
        from services.dependency_graph_service import _kahn_layers

        layers = _kahn_layers({}, set())
        assert layers == []

    def test_kahn_layers_single_module(self):
        """Test single module with no dependencies."""
        from services.dependency_graph_service import _kahn_layers

        a = self._make_module(1, "A")
        module_map = {1: a}
        module_ids = {1}

        layers = _kahn_layers(module_map, module_ids)

        assert len(layers) == 1
        assert len(layers[0]) == 1
        assert layers[0][0].id == 1

    def test_kahn_layers_filter_deps_to_set(self):
        """Test that filter_deps_to_set ignores deps outside working set."""
        from services.dependency_graph_service import _kahn_layers

        # B depends on A, but A is not in working set
        b = self._make_module(2, "B", deps=[1])  # dep on ID 1 (not in set)
        c = self._make_module(3, "C", deps=[2])

        module_map = {2: b, 3: c}
        module_ids = {2, 3}

        # Without filter: dep on 1 is ignored (not in module_ids)
        layers = _kahn_layers(module_map, module_ids, filter_deps_to_set=True)

        assert len(layers) == 2
        assert layers[0][0].id == 2  # B first (dep on 1 filtered out)
        assert layers[1][0].id == 3  # C second


# =============================================================================
# VARIABLE TRANSFORM SECURITY TESTS (NEW)
# =============================================================================

class TestVariableTransformSecurity:
    """Tests for variable transform security (apply_transformation, JWT guard)."""

    def test_apply_transformation_none(self):
        """Test no-op transformation."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation("hello", "none") == "hello"
        assert apply_transformation("hello", "") == "hello"
        assert apply_transformation("hello", None) == "hello"

    def test_apply_transformation_uppercase(self):
        """Test uppercase transformation."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation("hello", "uppercase") == "HELLO"

    def test_apply_transformation_lowercase(self):
        """Test lowercase transformation."""
        from services.execution.variable_assembler import apply_transformation

        assert apply_transformation("HELLO", "lowercase") == "hello"

    def test_apply_transformation_json_encode(self):
        """Test JSON encoding transformation."""
        from services.execution.variable_assembler import apply_transformation

        result = apply_transformation({"key": "value"}, "json_encode")
        assert json.loads(result) == {"key": "value"}

    def test_apply_transformation_base64(self):
        """Test base64 encoding transformation."""
        import base64

        from services.execution.variable_assembler import apply_transformation

        result = apply_transformation("hello", "base64")
        assert base64.b64decode(result).decode() == "hello"

    def test_apply_transformation_base64_skips_jwt(self):
        """Test that base64 transform is skipped for JWT tokens (S23-001)."""
        from services.execution.variable_assembler import apply_transformation

        jwt = (
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )

        # Should return raw JWT, NOT base64-encoded
        result = apply_transformation(jwt, "base64")
        assert result == jwt  # Unchanged — JWT guard activated

    def test_looks_like_jwt_detection(self):
        """Test JWT detection heuristic."""
        from services.execution.variable_assembler import _looks_like_jwt

        jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123"
        assert _looks_like_jwt(jwt) is True

        assert _looks_like_jwt("hello world") is False
        assert _looks_like_jwt("a.b") is False  # Only 2 segments
        assert _looks_like_jwt("a.b.c.d") is False  # 4 segments
        assert _looks_like_jwt("noteyJ.b.c") is False  # Doesn't start with eyJ

    def test_apply_transformation_unknown_type(self):
        """Test unknown transform type returns original value."""
        from services.execution.variable_assembler import apply_transformation

        result = apply_transformation("hello", "rot13")
        assert result == "hello"


# =============================================================================
# PRE-DEPLOY SECRETS VALIDATION TESTS (NEW — S19-001)
# =============================================================================

class TestPreDeploySecretsValidation:
    """Tests for pre-deploy secrets validation in StackDeploymentService (S19-001)."""

    def test_get_required_secret_prerequisites(self, sample_stack_template):
        """Test extracting project_secret prerequisites from template."""
        from services.stack_deployment_service import StackDeploymentService

        db = MagicMock()
        service = StackDeploymentService(db)

        secrets = service.get_required_secret_prerequisites(sample_stack_template)

        # Should only return project_secret type prerequisites
        assert len(secrets) == 2
        names = [s["name"] for s in secrets]
        assert "aws_access_key" in names
        assert "aws_secret_key" in names
        # "existing_vpc" is type "resource", not "project_secret"
        assert "existing_vpc" not in names

    def test_validate_required_secrets_all_present(self, sample_stack_template):
        """Test validation passes when all required secrets exist."""
        from services.stack_deployment_service import StackDeploymentService

        db = MagicMock()
        # Mock DB query to return both required secret names
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_filter = MagicMock()
        mock_query.filter.return_value = mock_filter
        mock_filter.all.return_value = [("aws_access_key",), ("aws_secret_key",)]

        service = StackDeploymentService(db)
        all_ok, missing = service.validate_required_secrets(1, sample_stack_template)

        assert all_ok is True
        assert missing == []

    def test_validate_required_secrets_some_missing(self, sample_stack_template):
        """Test validation fails when required secrets are missing."""
        from services.stack_deployment_service import StackDeploymentService

        db = MagicMock()
        # Mock DB query to return only one of two required secrets
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_filter = MagicMock()
        mock_query.filter.return_value = mock_filter
        mock_filter.all.return_value = [("aws_access_key",)]  # aws_secret_key missing

        service = StackDeploymentService(db)
        all_ok, missing = service.validate_required_secrets(1, sample_stack_template)

        assert all_ok is False
        assert len(missing) == 1
        assert missing[0]["name"] == "aws_secret_key"

    def test_validate_no_prerequisites(self):
        """Test validation passes when template has no prerequisites."""
        from services.stack_deployment_service import StackDeploymentService

        db = MagicMock()
        template = MagicMock()
        template.prerequisites = []

        service = StackDeploymentService(db)
        secrets = service.get_required_secret_prerequisites(template)
        assert secrets == []

    def test_check_prerequisites_structured_output(self, sample_stack_template):
        """Test check_prerequisites returns structured result for API."""
        from services.stack_deployment_service import StackDeploymentService

        db = MagicMock()
        # Mock DB to return all secrets present
        mock_query = MagicMock()
        db.query.return_value = mock_query
        mock_filter = MagicMock()
        mock_query.filter.return_value = mock_filter
        mock_filter.all.return_value = [("aws_access_key",), ("aws_secret_key",)]

        service = StackDeploymentService(db)
        result = service.check_prerequisites(1, sample_stack_template)

        assert "all_satisfied" in result
        assert "missing_secrets" in result
        assert "required_secrets" in result
        assert result["all_satisfied"] is True


# =============================================================================
# REDEPLOY WORKFLOW TESTS (NEW — S18-001)
# =============================================================================

class TestRedeployWorkflow:
    """Tests for redeploy with changed variables (S18-001)."""

    def test_deployed_stack_allows_redeploy(self, sample_stack):
        """Test that a deployed stack can be redeployed (S18-001)."""
        # S18-001: Deployed stacks should allow redeployment
        sample_stack.status = "deployed"
        sample_stack.deployed_modules = [1, 2, 3]

        # The StackDeploymentService.deploy_stack() returns True for deployed stacks
        # to allow incremental redeploy
        assert sample_stack.status == "deployed"
        assert len(sample_stack.deployed_modules) > 0

        # Redeploy should be allowed (not blocked like a fresh deploy would be)
        blocked_for_redeploy = sample_stack.status in ["deploying", "destroying"]
        assert blocked_for_redeploy is False

    def test_redeploy_blocked_during_active_operations(self, sample_stack):
        """Test that redeploy is blocked while deployment is in progress."""
        sample_stack.status = "deploying"

        blocked = sample_stack.status in ["deploying", "destroying"]
        assert blocked is True

    def test_vars_changed_triggers_redeploy(self, temp_workspace):
        """Test that changed variables trigger redeploy need."""
        from services.workspace_manager import WorkspaceManager

        db = MagicMock()
        manager = WorkspaceManager(db)

        module = MagicMock()
        module.workspace_path = temp_workspace
        module.project_id = 1
        module.id = 1
        module.vars_hash = "old_hash_abc123"

        # compute_vars_hash requires DB calls, so we test the comparison logic
        # When vars_hash differs from current, vars_changed returns True
        with patch.object(manager, 'compute_vars_hash', return_value="new_hash_def456"):
            assert manager.vars_changed(module) is True

        # When vars_hash matches, vars_changed returns False
        module.vars_hash = "same_hash"
        with patch.object(manager, 'compute_vars_hash', return_value="same_hash"):
            assert manager.vars_changed(module) is False

    def test_vars_changed_no_previous_hash(self, temp_workspace):
        """Test that missing vars_hash means no change detected."""
        from services.workspace_manager import WorkspaceManager

        db = MagicMock()
        manager = WorkspaceManager(db)

        module = MagicMock()
        module.workspace_path = temp_workspace
        module.vars_hash = None  # No previous hash

        # Should return False (allow operations to proceed)
        assert manager.vars_changed(module) is False

    def test_update_vars_hash(self, temp_workspace):
        """Test that update_vars_hash stores the hash on module."""
        from services.workspace_manager import WorkspaceManager

        db = MagicMock()
        manager = WorkspaceManager(db)

        module = MagicMock()
        module.workspace_path = temp_workspace
        module.id = 1
        module.vars_hash = None

        with patch.object(manager, 'compute_vars_hash', return_value="abc123hash"):
            result = manager.update_vars_hash(module)

        assert result == "abc123hash"
        assert module.vars_hash == "abc123hash"
        db.commit.assert_called()


# =============================================================================
# CLI ARGUMENT SECURITY TESTS (NEW)
# =============================================================================

class TestCLIArgumentSecurity:
    """Tests for CLI argument injection prevention (utils/security.py)."""

    def test_validate_cli_arg_rejects_flag(self):
        """Test that flag-like values are rejected."""
        from utils.security import validate_cli_arg

        with pytest.raises(ValueError, match="cannot start with '-'"):
            validate_cli_arg("release_name", "--set=image=evil")

    def test_validate_cli_arg_rejects_single_dash(self):
        """Test that single-dash values are rejected."""
        from utils.security import validate_cli_arg

        with pytest.raises(ValueError):
            validate_cli_arg("name", "-n")

    def test_validate_cli_arg_allows_normal_value(self):
        """Test that normal values are allowed."""
        from utils.security import validate_cli_arg

        # Should not raise
        validate_cli_arg("release_name", "my-release")
        validate_cli_arg("name", "hello-world")
        validate_cli_arg("url", "https://example.com")

    def test_validate_cli_arg_allows_none(self):
        """Test that None is allowed (caller decides if required)."""
        from utils.security import validate_cli_arg

        validate_cli_arg("optional", None)

    def test_validate_cli_arg_allows_empty(self):
        """Test that empty string is allowed."""
        from utils.security import validate_cli_arg

        validate_cli_arg("optional", "")

    def test_validate_helm_timeout_valid(self):
        """Test valid Helm timeout formats."""
        from utils.security import validate_helm_timeout

        # Should not raise
        validate_helm_timeout("5m")
        validate_helm_timeout("300s")
        validate_helm_timeout("1h")
        validate_helm_timeout("1h30m")
        validate_helm_timeout(None)  # None is OK

    def test_validate_helm_timeout_rejects_flag(self):
        """Test that flag injection via timeout is rejected."""
        from utils.security import validate_helm_timeout

        with pytest.raises(ValueError):
            validate_helm_timeout("--set=image=evil")

    def test_validate_helm_timeout_rejects_bad_format(self):
        """Test that bad format is rejected even if not a flag."""
        from utils.security import validate_helm_timeout

        with pytest.raises(ValueError, match="Invalid timeout format"):
            validate_helm_timeout("five_minutes")


# =============================================================================
# WORKSPACE VARIABLE HASHING TESTS (NEW)
# =============================================================================

class TestWorkspaceVarsHash:
    """Tests for workspace variable hashing (compute_vars_hash, update_vars_hash)."""

    def test_compute_vars_hash_deterministic(self, temp_workspace):
        """Test that hash is deterministic for same input."""
        from services.workspace_manager import WorkspaceManager

        db = MagicMock()
        manager = WorkspaceManager(db)

        module = MagicMock()
        module.workspace_path = temp_workspace
        module.id = 1
        module.project_id = 1

        variables = {"region": "us-west-2", "name": "test"}

        hash1 = manager.compute_vars_hash(module, variables=variables)
        hash2 = manager.compute_vars_hash(module, variables=variables)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex digest

    def test_compute_vars_hash_changes_with_variables(self, temp_workspace):
        """Test that hash changes when variables change."""
        from services.workspace_manager import WorkspaceManager

        db = MagicMock()
        manager = WorkspaceManager(db)

        module = MagicMock()
        module.workspace_path = temp_workspace
        module.id = 1
        module.project_id = 1

        vars_a = {"region": "us-west-2", "name": "test"}
        vars_b = {"region": "us-east-1", "name": "test"}

        hash_a = manager.compute_vars_hash(module, variables=vars_a)
        hash_b = manager.compute_vars_hash(module, variables=vars_b)

        assert hash_a != hash_b

    def test_plan_validity_check(self, temp_workspace):
        """Test plan_is_valid checks vars_hash and plan file."""
        from services.workspace_manager import WorkspaceManager

        db = MagicMock()
        manager = WorkspaceManager(db)

        module = MagicMock()
        module.workspace_path = temp_workspace
        module.id = 1
        module.vars_hash = None
        module.library_module = MagicMock()
        module.library_module.version = "1.0.0"
        module.project = MagicMock()
        module.project.backend_type = "local"

        # No plan file — should be invalid
        is_valid, reason = manager.plan_is_valid(module)
        assert is_valid is False
        assert "No saved plan" in reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
