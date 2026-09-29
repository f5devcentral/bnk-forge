"""test_connection feeds and honours the cluster reachability breaker."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BreakerOpenError
from services.kubernetes._base import KubernetesServiceBase
from services.reachability.registry import registry

_CLUSTER_ID = 999_101


def _svc() -> KubernetesServiceBase:
    svc = KubernetesServiceBase(db=MagicMock())
    svc.get_cluster = MagicMock(return_value=SimpleNamespace(
        name="c", api_server="https://x", cloud_provider=None, region=None,
    ))
    svc.load_kubeconfig = MagicMock(return_value=MagicMock())
    return svc


@pytest.mark.unit
def test_failures_open_the_breaker_and_short_circuit():
    core = MagicMock()
    core.list_namespace.side_effect = ConnectionRefusedError("refused")
    svc = _svc()
    with patch("services.kubernetes._base.client.CoreV1Api", return_value=core):
        for _ in range(5):
            assert svc.test_connection(_CLUSTER_ID)["success"] is False
        with pytest.raises(BreakerOpenError):
            svc.test_connection(_CLUSTER_ID)
    assert core.list_namespace.call_count == 5


@pytest.mark.unit
def test_success_is_recorded():
    svc = _svc()
    with (
        patch("services.kubernetes._base.client.CoreV1Api"),
        patch("services.kubernetes._base.client.VersionApi") as version_api,
    ):
        version_api.return_value.get_code.return_value = SimpleNamespace(major="1", minor="30")
        assert svc.test_connection(_CLUSTER_ID)["success"] is True
    assert registry.get_last_success_iso("cluster", _CLUSTER_ID) is not None


@pytest.mark.unit
def test_open_breaker_skips_kubeconfig_load():
    core = MagicMock()
    core.list_namespace.side_effect = ConnectionRefusedError("refused")
    svc = _svc()
    with patch("services.kubernetes._base.client.CoreV1Api", return_value=core):
        for _ in range(5):
            svc.test_connection(_CLUSTER_ID)
    loads = svc.load_kubeconfig.call_count
    with pytest.raises(BreakerOpenError):
        svc.test_connection(_CLUSTER_ID)
    assert svc.load_kubeconfig.call_count == loads
