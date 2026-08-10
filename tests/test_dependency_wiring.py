#!/usr/bin/env python3
"""
Test script for Sprint 2 dependency wiring.
Verifies that ExecutionEngine correctly wires outputs from VPC to Security module.
"""

import sys
import os
import json

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from database import SessionLocal
from models import ProjectModule
from services.execution_engine import ExecutionEngine

def test_dependency_wiring():
    """Test that Security module gets variables wired from VPC outputs"""
    db = SessionLocal()

    try:
        print("=" * 80)
        print("Sprint 2: Testing Dependency Wiring (VPC → Security)")
        print("=" * 80)

        # Get the security module (ID=2)
        security_module = db.query(ProjectModule).filter(ProjectModule.id == 2).first()
        if not security_module:
            print("❌ Security module not found (ID=2)")
            return False

        print(f"\n✓ Found security module: {security_module.library_module.name}")
        print(f"  Dependencies: {security_module.dependencies}")
        print(f"  User variables: {json.dumps(security_module.variables or {}, indent=2)}")

        # Create ExecutionEngine
        engine = ExecutionEngine(db)

        # Test 1: Check if module can execute (dependencies satisfied)
        print("\n" + "-" * 80)
        print("Test 1: Checking if dependencies are satisfied...")
        print("-" * 80)

        can_exec, missing = engine.can_execute(security_module)

        if not can_exec:
            print(f"❌ Cannot execute: {missing}")
            return False
        else:
            print(f"✅ All dependencies satisfied!")

        # Test 2: Build variables and check wiring
        print("\n" + "-" * 80)
        print("Test 2: Building variables with dependency wiring...")
        print("-" * 80)

        variables = engine.build_variables(security_module)

        print(f"\n✓ Built {len(variables)} variables:")
        print(json.dumps(variables, indent=2))

        # Verify specific wired variables
        expected_wired_vars = {
            "vpc_id": "vpc-mock-12345",
            "vpc_cidr_block": "10.0.0.0/16",
            "user_ip": "203.0.113.10/32"
        }

        print("\n" + "-" * 80)
        print("Test 3: Verifying wired variables...")
        print("-" * 80)

        all_correct = True
        for var_name, expected_value in expected_wired_vars.items():
            actual_value = variables.get(var_name)

            if actual_value == expected_value:
                print(f"✅ {var_name}: {actual_value}")
            else:
                print(f"❌ {var_name}: expected={expected_value}, actual={actual_value}")
                all_correct = False

        # Check that public_subnet_id was wired (should be first element of list)
        if "public_subnet_id" in variables:
            print(f"✅ public_subnet_id: {variables['public_subnet_id']} (wired from VPC)")
        else:
            print(f"❌ public_subnet_id: NOT FOUND (should be wired from VPC public_subnet_ids)")
            all_correct = False

        print("\n" + "=" * 80)
        if all_correct:
            print("✅ Sprint 2 Test: PASSED - Dependency wiring works correctly!")
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
    success = test_dependency_wiring()
    sys.exit(0 if success else 1)
