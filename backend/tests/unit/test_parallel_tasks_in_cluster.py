"""
Unit tests for #328 fail-soft destroy and #329 force_destroy classification helpers.

Covers:
- is_in_cluster_module: correct classification by execution_engine / engine_type
- IN_CLUSTER_ENGINES constant: expected engines included, opentofu not included
"""

from tasks.parallel_tasks import IN_CLUSTER_ENGINES, is_in_cluster_module


class FakeLibModule:
    def __init__(self, execution_engine=None, engine_type=None):
        self.execution_engine = execution_engine
        self.engine_type = engine_type


class FakeModule:
    def __init__(self, execution_engine=None, engine_type=None):
        self.library_module = FakeLibModule(
            execution_engine=execution_engine,
            engine_type=engine_type,
        )


class FakeModuleNoLib:
    library_module = None


class TestInClusterEnginesConstant:
    def test_kubernetes_direct_in_set(self):
        assert "kubernetes-direct" in IN_CLUSTER_ENGINES

    def test_operator_in_set(self):
        assert "operator" in IN_CLUSTER_ENGINES

    def test_kubernetes_in_set(self):
        assert "kubernetes" in IN_CLUSTER_ENGINES

    def test_opentofu_not_in_set(self):
        assert "opentofu" not in IN_CLUSTER_ENGINES

    def test_ssh_not_in_set(self):
        assert "ssh" not in IN_CLUSTER_ENGINES

    def test_ansible_not_in_set(self):
        assert "ansible" not in IN_CLUSTER_ENGINES


class TestIsInClusterModule:
    """Classification of modules by execution_engine / engine_type."""

    def test_kubernetes_direct_engine_is_in_cluster(self):
        m = FakeModule(execution_engine="kubernetes-direct")
        assert is_in_cluster_module(m) is True

    def test_operator_engine_is_in_cluster(self):
        m = FakeModule(execution_engine="operator")
        assert is_in_cluster_module(m) is True

    def test_kubernetes_execution_engine_is_in_cluster(self):
        m = FakeModule(execution_engine="kubernetes")
        assert is_in_cluster_module(m) is True

    def test_kubernetes_engine_type_is_in_cluster(self):
        """engine_type='kubernetes' also classifies as in-cluster."""
        m = FakeModule(engine_type="kubernetes")
        assert is_in_cluster_module(m) is True

    def test_opentofu_execution_engine_not_in_cluster(self):
        m = FakeModule(execution_engine="opentofu")
        assert is_in_cluster_module(m) is False

    def test_ssh_engine_not_in_cluster(self):
        m = FakeModule(execution_engine="ssh")
        assert is_in_cluster_module(m) is False

    def test_none_execution_engine_not_in_cluster(self):
        m = FakeModule(execution_engine=None, engine_type=None)
        assert is_in_cluster_module(m) is False

    def test_no_library_module_not_in_cluster(self):
        m = FakeModuleNoLib()
        assert is_in_cluster_module(m) is False

    def test_case_insensitive_kubernetes_direct(self):
        """Matching is case-insensitive."""
        m = FakeModule(execution_engine="Kubernetes-Direct")
        assert is_in_cluster_module(m) is True

    def test_case_insensitive_operator(self):
        m = FakeModule(execution_engine="OPERATOR")
        assert is_in_cluster_module(m) is True

    def test_whitespace_stripped(self):
        """Execution engine with surrounding whitespace is still matched."""
        m = FakeModule(execution_engine="  kubernetes-direct  ")
        assert is_in_cluster_module(m) is True

    def test_engine_type_ssh_not_in_cluster(self):
        m = FakeModule(engine_type="ssh")
        assert is_in_cluster_module(m) is False

    def test_execution_engine_takes_precedence_over_engine_type(self):
        """When execution_engine matches, result is True regardless of engine_type."""
        m = FakeModule(execution_engine="kubernetes-direct", engine_type="opentofu")
        assert is_in_cluster_module(m) is True
