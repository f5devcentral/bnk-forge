"""translate_proxy_to_bnk for a standalone proxy Deployment."""

from unittest.mock import MagicMock, patch

from schemas.k8s import ProxyTranslateRequest


def _cm(name, cfg):
    cm = MagicMock()
    cm.metadata.name = name
    cm.data = {"haproxy.cfg": cfg}
    return cm


def test_deployment_translate_uses_only_its_own_configmap():
    from routes.k8s.f5bnk import translate_proxy_to_bnk

    vol = MagicMock()
    vol.config_map.name = "perf-haproxy-cfg"
    dep = MagicMock()
    dep.spec.template.spec.volumes = [vol]
    k8s = MagicMock()
    k8s.AppsV1Api.return_value.read_namespaced_deployment.return_value = dep
    cms = [
        _cm("perf-haproxy-cfg", "    server s1 vllm.inference:8000 check"),
        _cm("unrelated-cfg", "    server s1 billing.finance:9000 check"),
    ]
    body = ProxyTranslateRequest(
        proxy_type="haproxy", source_kind="Deployment", class_name="perf-haproxy", namespace="perf-proxies",
    )

    with (
        patch("routes.k8s.f5bnk.KubernetesService"),
        patch("routes.k8s.f5bnk.k8s_client", k8s),
        patch("routes.k8s.f5bnk._safe_list_all_ingresses", return_value=[]),
        patch("routes.k8s.f5bnk._safe_list_all_custom", return_value=[]),
        patch("services.proxy_discovery_service._safe_list_namespaced_configmaps", return_value=cms),
    ):
        resp = translate_proxy_to_bnk(cluster_id=1, body=body, db=MagicMock())

    assert "vllm" in resp.httproute_yaml
    assert "billing" not in resp.httproute_yaml
    k8s.AppsV1Api.return_value.read_namespaced_deployment.assert_called_once_with(
        name="perf-haproxy", namespace="perf-proxies", _request_timeout=10,
    )
