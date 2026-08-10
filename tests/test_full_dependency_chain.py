#!/usr/bin/env python3
"""
Test script for Sprint 2 full dependency chain.
Verifies that EKS module gets variables wired from both VPC and Security modules.
"""

import sys
import os
import json

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from database import SessionLocal
from models import ProjectModule
from services.execution_engine import ExecutionEngine

def test_full_chain():
    """Test full VPC → Security → EKS dependency chain"""
    db = SessionLocal()

    try:
        print("=" * 80)
        print("Sprint 2: Testing Full Dependency Chain (VPC → Security → EKS)")
        print("=" * 80)

        # Get the EKS module (ID=3)
        eks_module = db.query(ProjectModule).filter(ProjectModule.id == 3).first()
        if not eks_module:
            print("❌ EKS module not found (ID=3)")
            return False

        print(f"\n✓ Found EKS module: {eks_module.library_module.name}")
        print(f"  Dependencies: {eks_module.dependencies}")

        # Create ExecutionEngine
        engine = ExecutionEngine(db)

        # Test 1: Check if all dependencies are satisfied
        print("\n" + "-" * 80)
        print("Test 1: Checking if all dependencies are satisfied...")
        print("-" * 80)

        can_exec, missing = engine.can_execute(eks_module)

        if not can_exec:
            print(f"❌ Cannot execute: {missing}")
            return False
        else:
            print(f"✅ All dependencies satisfied (VPC and Security both applied)!")

        # Test 2: Build variables with multi-level dependency wiring
        print("\n" + "-" * 80)
        print("Test 2: Building variables with multi-level dependency wiring...")
        print("-" * 80)

        variables = engine.build_variables(eks_module)

        print(f"\n✓ Built {len(variables)} variables:")
        print(json.dumps(variables, indent=2))

        # Test 3: Verify wired variables from VPC
        print("\n" + "-" * 80)
        print("Test 3: Verifying variables wired from VPC module...")
        print("-" * 80)

        vpc_vars = {
            "vpc_id": "vpc-mock-12345",
            "private_external_subnet_ids": ["subnet-priv-ext-1", "subnet-priv-ext-2"],
            "private_internal_subnet_ids": ["subnet-priv-int-1", "subnet-priv-int-2"]
        }

        vpc_correct = True
        for var_name, expected_value in vpc_vars.items():
            actual_value = variables.get(var_name)
            if actual_value == expected_value:
                print(f"✅ {var_name}: {actual_value} (from VPC)")
            else:
                print(f"❌ {var_name}: expected={expected_value}, actual={actual_value}")
                vpc_correct = False

        # Test 4: Verify wired variables from Security
        print("\n" + "-" * 80)
        print("Test 4: Verifying variables wired from Security module...")
        print("-" * 80)

        security_vars = {
            "eks_cluster_role_arn": "arn:aws:iam::123456789012:role/mock-eks-cluster-role",
            "nodegroup_role_arn": "arn:aws:iam::123456789012:role/mock-nodegroup-role",
            "vpc_security_group_id": "sg-mock-12345",
            "infrastructure_key_name": "mock-ssh-key"
        }

        security_correct = True
        for var_name, expected_value in security_vars.items():
            actual_value = variables.get(var_name)
            if actual_value == expected_value:
                print(f"✅ {var_name}: {actual_value} (from Security)")
            else:
                print(f"❌ {var_name}: expected={expected_value}, actual={actual_value}")
                security_correct = False

        # Test 5: Verify project-level variables
        print("\n" + "-" * 80)
        print("Test 5: Verifying project-level variables...")
        print("-" * 80)

        project_vars = {
            "project_name": "sprint1-test",
            "environment": "dev",
            "aws_region": "us-west-2"
        }

        project_correct = True
        for var_name, expected_value in project_vars.items():
            actual_value = variables.get(var_name)
            if actual_value == expected_value:
                print(f"✅ {var_name}: {actual_value}")
            else:
                print(f"❌ {var_name}: expected={expected_value}, actual={actual_value}")
                project_correct = False

        # Final result
        print("\n" + "=" * 80)
        all_correct = vpc_correct and security_correct and project_correct

        if all_correct:
            print("✅ Sprint 2 COMPLETE: Full dependency chain works correctly!")
            print("\nSummary:")
            print("  • VPC → Security: Variables wired correctly ✅")
            print("  • VPC → EKS: Variables wired correctly ✅")
            print("  • Security → EKS: Variables wired correctly ✅")
            print("  • Project → EKS: Variables injected correctly ✅")
            print("\n🎉 Multi-level dependency wiring is fully functional!")
        else:
            print("❌ Sprint 2 Test: FAILED - Some variables not wired correctly")

        print("=" * 80)

        return all_correct

    except Exception as e:
        print(f"\n❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    success = test_full_chain()
    sys.exit(0 if success else 1)
