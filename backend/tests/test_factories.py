"""
Tests for the factory system itself.

Verifies that all factories create valid model instances
that pass database constraints and can be used in further queries.
"""

import pytest


class TestUserFactory:
    """Test UserFactory creates valid users."""

    def test_creates_user_with_defaults(self, make_user):
        user = make_user()
        assert user.id is not None
        assert user.username.startswith("user-")
        assert user.email.endswith("@bnk-forge.test")
        assert user.role == "operator"
        assert user.is_active is True

    def test_creates_admin(self, make_user):
        admin = make_user(role="admin", username="testadmin")
        assert admin.role == "admin"
        assert admin.username == "testadmin"

    def test_unique_usernames(self, make_user):
        u1 = make_user()
        u2 = make_user()
        assert u1.username != u2.username
        assert u1.id != u2.id


class TestProjectFactory:
    """Test ProjectFactory creates valid projects."""

    def test_creates_project_with_defaults(self, make_project):
        project = make_project()
        assert project.id is not None
        assert project.name.startswith("test-project-")
        assert project.cloud_provider == "on-prem"
        assert project.environment == "dev"
        assert project.is_active is True

    def test_creates_aws_project(self, make_project):
        project = make_project(cloud_provider="aws", region="us-east-1")
        assert project.cloud_provider == "aws"
        assert project.region == "us-east-1"

    def test_unique_names(self, make_project):
        p1 = make_project()
        p2 = make_project()
        assert p1.name != p2.name


class TestModuleLibraryFactory:
    """Test ModuleLibraryFactory creates valid catalog entries."""

    def test_creates_module_library(self, make_module_library):
        ml = make_module_library()
        assert ml.id is not None
        assert ml.name.startswith("test-module-")
        assert ml.category == "kubernetes"
        assert ml.is_official is True

    def test_custom_module(self, make_module_library):
        ml = make_module_library(name="my-custom-module", category="network", is_official=False)
        assert ml.name == "my-custom-module"
        assert ml.category == "network"
        assert ml.is_official is False


class TestProjectModuleFactory:
    """Test ProjectModuleFactory creates valid project modules."""

    def test_creates_with_auto_dependencies(self, make_project_module):
        """Factory auto-creates project and library_module if not provided."""
        pm = make_project_module()
        assert pm.id is not None
        assert pm.project_id is not None
        assert pm.module_library_id is not None
        assert pm.status == "not_initialized"

    def test_creates_with_explicit_project(self, make_project, make_project_module):
        project = make_project(name="explicit-project")
        pm = make_project_module(project=project)
        assert pm.project_id == project.id

    def test_creates_with_variables(self, make_project_module):
        pm = make_project_module(variables={"cidr_block": "10.0.0.0/16"})
        assert pm.variables["cidr_block"] == "10.0.0.0/16"


class TestTaskFactory:
    """Test TaskFactory creates valid tasks."""

    def test_creates_task_with_defaults(self, make_task):
        task = make_task()
        assert task.id is not None
        assert task.task_type == "apply"
        assert task.status == "queued"
        assert task.project_id is not None

    def test_creates_failed_task(self, make_task, make_project):
        project = make_project()
        task = make_task(
            project=project,
            task_type="plan",
            status="failed",
            error="Something went wrong",
        )
        assert task.status == "failed"
        assert task.error == "Something went wrong"
        assert task.project_id == project.id


class TestStackFactories:
    """Test StackTemplate and StackInstance factories."""

    def test_creates_template(self, make_stack_template):
        template = make_stack_template()
        assert template.id is not None
        assert template.slug.startswith("test-stack-")

    def test_creates_instance(self, make_stack_instance):
        instance = make_stack_instance()
        assert instance.id is not None
        assert instance.project_id is not None
        assert instance.template_id is not None
        assert instance.status == "pending"

    def test_instance_links_to_template(self, make_stack_template, make_stack_instance, make_project):
        project = make_project()
        template = make_stack_template(name="BNK Full", slug="bnk-full")
        instance = make_stack_instance(project=project, template=template)
        assert instance.project_id == project.id
        assert instance.template_id == template.id


class TestK8sClusterFactory:
    """Test KubernetesClusterFactory creates valid clusters."""

    def test_creates_cluster(self, make_k8s_cluster):
        cluster = make_k8s_cluster()
        assert cluster.id is not None
        assert cluster.name.startswith("test-cluster-")
        assert cluster.context.startswith("test-context-")

    def test_creates_cluster_with_project(self, make_k8s_cluster, make_project):
        project = make_project()
        cluster = make_k8s_cluster(project=project)
        assert cluster.project_id == project.id
