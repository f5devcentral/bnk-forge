import sys
import os
import unittest
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

# Configure logging to suppress noisy output during test
logging.basicConfig(level=logging.ERROR)

from models import Base, Project, ProjectModule
from services.project_service import update_project_counts

# Use in-memory SQLite for testing
TEST_DATABASE_URL = "sqlite:///:memory:"

class TestProjectCounts(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = SessionLocal()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)

    def test_counts_sync_logic(self):
        print("\nTesting project count synchronization logic...")

        # Create project
        project = Project(name="Test Project")
        self.db.add(project)
        self.db.commit()
        project_id = project.id

        # Verify initial counts
        self.assertEqual(project.module_count, 0)
        self.assertEqual(project.deployed_count, 0)

        # 1. Simulate adding module (using update_project_counts as per my fix)
        print("1. Adding module...")
        module = ProjectModule(
            project_id=project_id,
            module_library_id=1,
            path_in_project="infra/vpc",
            status="not_initialized",
            enabled=True
        )
        self.db.add(module)
        self.db.commit()

        # Call update (mimicking add_module_to_project fix)
        update_project_counts(self.db, project_id)
        self.db.refresh(project)

        print(f"   Counts: module={project.module_count}, deployed={project.deployed_count}")
        self.assertEqual(project.module_count, 1)
        self.assertEqual(project.deployed_count, 0)

        # 2. Simulate deployment success
        print("2. Setting status to applied (deployed)...")
        module.status = "applied"
        self.db.commit()

        # Call update (mimicking task completion)
        update_project_counts(self.db, project_id)
        self.db.refresh(project)

        print(f"   Counts: module={project.module_count}, deployed={project.deployed_count}")
        self.assertEqual(project.module_count, 1)
        self.assertEqual(project.deployed_count, 1)

        # 3. Simulate removing module (using update_project_counts as per my fix)
        print("3. Removing module...")
        self.db.delete(module)
        self.db.commit()

        # Call update (mimicking remove_module_from_project fix)
        update_project_counts(self.db, project_id)
        self.db.refresh(project)

        print(f"   Counts: module={project.module_count}, deployed={project.deployed_count}")
        self.assertEqual(project.module_count, 0)
        self.assertEqual(project.deployed_count, 0) # This verifies the fix works (previously would stay 1)

        print("SUCCESS: Project counts are correctly synchronized!")

if __name__ == '__main__':
    unittest.main()
