"""
Unit tests for ``_resolve_service_external_url`` in ProxyDeployService.
"""

from unittest.mock import MagicMock, patch

import pytest
from kubernetes.client.rest import ApiException

from services.proxy_deploy_service import ProxyDeployService


def _svc(svc_type: str, ports: list | None = None) -> MagicMock:
    svc = MagicMock()
    svc.metadata.name = "perf-haproxy-test"
    svc.metadata.namespace = "perf-proxies"
    svc.spec.type = svc_type
    svc.spec.ports = ports or []
    return svc


def _port(port: int = 80, node_port: int | None = None, name: str = "http") -> MagicMock:
    p = MagicMock()
    p.port = port
    p.node_port = node_port
    p.name = name
    return p


def _node(internal_ip: str | None = None, external_ip: str | None = None) -> MagicMock:
    node = MagicMock()
    addrs = []
    if internal_ip:
        a = MagicMock()
        a.type = "InternalIP"
        a.address = internal_ip
        addrs.append(a)
    if external_ip:
        a = MagicMock()
        a.type = "ExternalIP"
        a.address = external_ip
        addrs.append(a)
    node.status.addresses = addrs
    return node


class TestResolveServiceExternalUrl:
    @patch("services.proxy_deploy_service.k8s_client.CoreV1Api")
    @patch("services.proxy_deploy_service.KubernetesService")
    def test_nodeport_with_internal_ip(self, mock_k8s_cls, mock_core_cls):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        svc_obj = _svc("NodePort", [_port(80, node_port=31235, name="http")])
        mock_core.read_namespaced_service.return_value = svc_obj
        mock_core.list_node.return_value = MagicMock(items=[_node(internal_ip="10.0.1.45")])

        service = ProxyDeployService(db=MagicMock())
        result = service._resolve_service_external_url(
            cluster=MagicMock(),
            release="perf-haproxy-test",
            namespace="perf-proxies",
        )
        assert result == "http://10.0.1.45:31235"

    @patch("services.proxy_deploy_service.k8s_client.CoreV1Api")
    @patch("services.proxy_deploy_service.KubernetesService")
    def test_nodeport_with_external_ip_fallback(self, mock_k8s_cls, mock_core_cls):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        svc_obj = _svc("NodePort", [_port(80, node_port=31235, name="http")])
        mock_core.read_namespaced_service.return_value = svc_obj
        mock_core.list_node.return_value = MagicMock(items=[_node(external_ip="203.0.113.120")])

        service = ProxyDeployService(db=MagicMock())
        result = service._resolve_service_external_url(
            cluster=MagicMock(),
            release="perf-haproxy-test",
            namespace="perf-proxies",
        )
        assert result == "http://203.0.113.120:31235"

    @patch("services.proxy_deploy_service.k8s_client.CoreV1Api")
    @patch("services.proxy_deploy_service.KubernetesService")
    def test_loadbalancer_with_hostname(self, mock_k8s_cls, mock_core_cls):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        svc_obj = _svc("LoadBalancer", [_port(8080, name="http")])
        ingress = MagicMock()
        ingress.hostname = "a123.elb.us-east-1.amazonaws.com"
        ingress.ip = None
        svc_obj.status.load_balancer.ingress = [ingress]
        mock_core.read_namespaced_service.return_value = svc_obj

        service = ProxyDeployService(db=MagicMock())
        result = service._resolve_service_external_url(
            cluster=MagicMock(),
            release="perf-haproxy-test",
            namespace="perf-proxies",
        )
        assert result == "http://a123.elb.us-east-1.amazonaws.com:8080"

    @patch("services.proxy_deploy_service.k8s_client.CoreV1Api")
    @patch("services.proxy_deploy_service.KubernetesService")
    def test_clusterip_returns_none(self, mock_k8s_cls, mock_core_cls):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        svc_obj = _svc("ClusterIP", [_port(80, name="http")])
        mock_core.read_namespaced_service.return_value = svc_obj

        service = ProxyDeployService(db=MagicMock())
        result = service._resolve_service_external_url(
            cluster=MagicMock(),
            release="perf-haproxy-test",
            namespace="perf-proxies",
        )
        assert result is None

    @patch("services.proxy_deploy_service.k8s_client.CoreV1Api")
    @patch("services.proxy_deploy_service.KubernetesService")
    def test_service_not_found_returns_none(self, mock_k8s_cls, mock_core_cls):
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        mock_core.read_namespaced_service.side_effect = ApiException(status=404)
        mock_core.list_namespaced_service.return_value = MagicMock(items=[])

        service = ProxyDeployService(db=MagicMock())
        result = service._resolve_service_external_url(
            cluster=MagicMock(),
            release="perf-haproxy-test",
            namespace="perf-proxies",
        )
        assert result is None
