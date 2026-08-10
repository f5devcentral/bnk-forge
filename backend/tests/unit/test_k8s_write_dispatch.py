"""
Unit tests for K8s write/describe dispatch generalization (issue #129 / ADR D-019).

Verifies that create + delete work for:
  - core ("")  — ConfigMap
  - apps       — Deployment
  - batch      — Job
  - rbac.authorization.k8s.io — Role
  - unknown CRD group         — falls through to CustomObjectsApi, never ValueError

All K8s API calls are mocked; no live cluster required.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.k8s_types import K8sResourceType, ResourceCategory
from services.kubernetes._resources import API_GROUP_CLIENTS, _kind_to_snake

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rt(api_group: str, kind: str, plural: str, namespaced: bool = True) -> K8sResourceType:
    return K8sResourceType(
        api_group=api_group,
        api_version="v1",
        kind=kind,
        plural=plural,
        namespaced=namespaced,
        display_name=kind,
        description="",
        category=ResourceCategory.CORE,
    )


def _make_subject():
    """Return an object with both write-dispatch methods mixed in."""
    from services.kubernetes._operations import OperationsMixin
    from services.kubernetes._resources import ResourcesMixin

    class Subject(ResourcesMixin, OperationsMixin):
        pass

    return Subject()


SUBJECT = _make_subject()


# ---------------------------------------------------------------------------
# _kind_to_snake
# ---------------------------------------------------------------------------

class TestKindToSnake:
    def test_simple(self):
        assert _kind_to_snake("Pod") == "pod"

    def test_camel(self):
        assert _kind_to_snake("ConfigMap") == "config_map"

    def test_multi_word(self):
        assert _kind_to_snake("HorizontalPodAutoscaler") == "horizontal_pod_autoscaler"

    def test_pvc(self):
        assert _kind_to_snake("PersistentVolumeClaim") == "persistent_volume_claim"

    def test_role_binding(self):
        assert _kind_to_snake("RoleBinding") == "role_binding"


# ---------------------------------------------------------------------------
# API_GROUP_CLIENTS completeness
# ---------------------------------------------------------------------------

class TestApiGroupClients:
    def test_all_expected_groups_present(self):
        expected = {
            "",
            "apps",
            "networking.k8s.io",
            "batch",
            "autoscaling",
            "rbac.authorization.k8s.io",
            "policy",
            "storage.k8s.io",
        }
        assert expected.issubset(set(API_GROUP_CLIENTS.keys()))

    def test_unknown_group_not_in_map(self):
        assert "k8s.f5net.com" not in API_GROUP_CLIENTS
        assert "cert-manager.io" not in API_GROUP_CLIENTS
        assert "gateway.networking.k8s.io" not in API_GROUP_CLIENTS


# ---------------------------------------------------------------------------
# _write_typed_resource — dispatch via mock API class in the map
# ---------------------------------------------------------------------------

def _run_write(verb: str, api_group: str, kind: str, plural: str,
               expected_method: str, namespaced: bool = True, **extra_kwargs):
    """
    Helper: replace the api_group entry in API_GROUP_CLIENTS with a MagicMock class,
    call _write_typed_resource, assert the expected method was invoked.
    """
    rt = _make_rt(api_group, kind, plural, namespaced=namespaced)
    mock_api_client = MagicMock()
    fake_result = MagicMock()
    fake_result.to_dict.return_value = {"kind": kind}

    mock_cls = MagicMock()
    mock_instance = MagicMock()
    mock_cls.return_value = mock_instance
    getattr(mock_instance, expected_method).return_value = fake_result

    with patch.dict("services.kubernetes._resources.API_GROUP_CLIENTS", {api_group: mock_cls}):
        SUBJECT._write_typed_resource(
            verb, mock_api_client, rt, "default", **extra_kwargs
        )

    getattr(mock_instance, expected_method).assert_called_once()


class TestWriteTypedResourceCreate:
    def test_core_configmap(self):
        _run_write("create", "", "ConfigMap", "configmaps",
                   "create_namespaced_config_map",
                   body={"kind": "ConfigMap"}, dry_run=None)

    def test_apps_deployment(self):
        _run_write("create", "apps", "Deployment", "deployments",
                   "create_namespaced_deployment",
                   body={"kind": "Deployment"}, dry_run=None)

    def test_batch_job(self):
        _run_write("create", "batch", "Job", "jobs",
                   "create_namespaced_job",
                   body={"kind": "Job"}, dry_run=None)

    def test_rbac_role(self):
        _run_write("create", "rbac.authorization.k8s.io", "Role", "roles",
                   "create_namespaced_role",
                   body={"kind": "Role"}, dry_run=None)


class TestWriteTypedResourceDelete:
    def test_core_configmap(self):
        _run_write("delete", "", "ConfigMap", "configmaps",
                   "delete_namespaced_config_map",
                   name="my-cm", dry_run=None)

    def test_apps_deployment(self):
        _run_write("delete", "apps", "Deployment", "deployments",
                   "delete_namespaced_deployment",
                   name="my-deploy", dry_run=None)

    def test_batch_job(self):
        _run_write("delete", "batch", "Job", "jobs",
                   "delete_namespaced_job",
                   name="my-job", dry_run=None)

    def test_rbac_role(self):
        _run_write("delete", "rbac.authorization.k8s.io", "Role", "roles",
                   "delete_namespaced_role",
                   name="my-role", dry_run=None)


# ---------------------------------------------------------------------------
# Unknown api_group falls through to CustomObjectsApi — never ValueError
# ---------------------------------------------------------------------------

class TestUnknownGroupFallsToCustomObjects:
    """Groups NOT in API_GROUP_CLIENTS must be absent (so callers route to CustomObjectsApi)."""

    def test_unknown_crd_groups_absent_from_map(self):
        unknown = [
            "k8s.f5net.com",
            "cert-manager.io",
            "gateway.networking.k8s.io",
            "totally.unknown.group",
        ]
        for group in unknown:
            assert group not in API_GROUP_CLIENTS, (
                f"Group {group!r} should NOT be in API_GROUP_CLIENTS — "
                "it must fall through to CustomObjectsApi"
            )

    def test_method_not_found_raises_value_error(self):
        """When a typed api has no matching method, ValueError is raised cleanly (not AttributeError)."""
        rt = _make_rt("batch", "FakeKind", "fakekinds")
        mock_api_client = MagicMock()

        mock_cls = MagicMock()
        mock_cls.__name__ = "MockBatchV1Api"
        mock_instance = MagicMock(spec=[])  # spec=[] means no attributes
        mock_cls.return_value = mock_instance

        with patch.dict("services.kubernetes._resources.API_GROUP_CLIENTS", {"batch": mock_cls}):
            with pytest.raises(ValueError, match="not found on"):
                SUBJECT._write_typed_resource(
                    "create", mock_api_client, rt, "default",
                    body={}, dry_run=None
                )
