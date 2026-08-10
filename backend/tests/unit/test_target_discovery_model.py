"""Unit tests for TargetDiscoveryService model-id extraction.

The benchmark target's model is what OpenAI requests send, so it must be the vLLM
``--served-model-name`` when set (else it falls back to ``--model`` / the positional
``vllm serve <model>``) — NOT the Service label (e.g. ``model=qwen3-32b``), which is a
short alias the server won't accept.
"""

from unittest.mock import MagicMock

import pytest

from services.target_discovery_service import TargetDiscoveryService


def _svc() -> TargetDiscoveryService:
    return TargetDiscoveryService(db=MagicMock())


def _pod(containers):
    p = MagicMock()
    p.spec.containers = containers
    return p


def _container(name="vllm", image="vllm/vllm-openai:v0.17.1", command=None, args=None):
    c = MagicMock()
    c.name = name
    c.image = image
    c.command = command
    c.args = args
    return c


@pytest.mark.unit
class TestModelFromServicePods:
    def test_extracts_positional_vllm_serve_from_shell(self):
        # The real dynamo-system vllm-qwen3-32b pod shape: sh -c '... vllm serve <model> ...'.
        shell = (
            'exec vllm serve Qwen/Qwen3-32B --served-model-name Qwen/Qwen3-32B '
            '--port 8000 --block-size 64'
        )
        core = MagicMock()
        core.list_namespaced_pod.return_value = MagicMock(
            items=[_pod([_container(command=["/bin/sh", "-c"], args=[shell])])]
        )
        model = _svc()._model_from_service_pods(core, "dynamo-system", {"app": "vllm-qwen3-32b"})
        assert model == "Qwen/Qwen3-32B"

    def test_prefers_served_model_name_over_model(self):
        # When --served-model-name differs from --model, requests must use the served name.
        shell = (
            'exec vllm serve neuralmagic/Meta-Llama-3.1-70B-Instruct-FP8 '
            '--served-model-name llama70b --port 8000'
        )
        core = MagicMock()
        core.list_namespaced_pod.return_value = MagicMock(
            items=[_pod([_container(command=["/bin/sh", "-c"], args=[shell])])]
        )
        assert _svc()._model_from_service_pods(core, "ns", {"app": "x"}) == "llama70b"

    def test_falls_back_to_model_when_no_served_name(self):
        core = MagicMock()
        core.list_namespaced_pod.return_value = MagicMock(
            items=[_pod([_container(command=["/bin/sh", "-c"], args=["vllm serve Qwen/Qwen3-32B"]) ])]
        )
        assert _svc()._model_from_service_pods(core, "ns", {"app": "x"}) == "Qwen/Qwen3-32B"

    def test_extracts_model_flag(self):
        core = MagicMock()
        core.list_namespaced_pod.return_value = MagicMock(
            items=[_pod([_container(args=["--model", "meta-llama/Llama-3.1-70B"])])]
        )
        model = _svc()._model_from_service_pods(core, "ns", {"app": "x"})
        assert model == "meta-llama/Llama-3.1-70B"

    def test_prefers_vllm_container(self):
        core = MagicMock()
        core.list_namespaced_pod.return_value = MagicMock(items=[_pod([
            _container(name="sidecar", image="busybox", args=["--model", "wrong"]),
            _container(name="vllm", args=["--model", "Qwen/Qwen3-32B"]),
        ])])
        # The sidecar also has --model; the vLLM container is scanned first.
        model = _svc()._model_from_service_pods(core, "ns", {"app": "x"})
        assert model == "Qwen/Qwen3-32B"

    def test_none_without_selector(self):
        assert _svc()._model_from_service_pods(MagicMock(), "ns", None) is None
        assert _svc()._model_from_service_pods(MagicMock(), "ns", {}) is None

    def test_none_when_no_model_arg(self):
        core = MagicMock()
        core.list_namespaced_pod.return_value = MagicMock(
            items=[_pod([_container(args=["--port", "8000"])])]
        )
        assert _svc()._model_from_service_pods(core, "ns", {"app": "x"}) is None

    def test_api_exception_returns_none(self):
        from kubernetes.client.rest import ApiException
        core = MagicMock()
        core.list_namespaced_pod.side_effect = ApiException(status=403)
        assert _svc()._model_from_service_pods(core, "ns", {"app": "x"}) is None
