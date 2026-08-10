"""
Unit tests for named-kubectl-context threading through K8s/Helm execution.

Forge stores ``KubernetesCluster.context`` but historically passed only
``--kubeconfig`` and relied on the kubeconfig's ``current-context``. These
tests pin the contract that, when a context is set, every helm/kubectl
invocation (and the kr8s API client) targets that named context — and that
``context=None`` reproduces the prior behavior with no ``--context`` flag.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.execution.kubernetes_engine import KubernetesEngine
from services.helm_service import HelmService

# ---------------------------------------------------------------------------
# KubernetesEngine._conn_args — the shared argv builder
# ---------------------------------------------------------------------------

def test_conn_args_with_context_appends_context_flag():
    eng = KubernetesEngine("/tmp/kc.yaml", context="kubernetes-admin@kubernetes")
    assert eng._conn_args() == [
        "--kubeconfig", "/tmp/kc.yaml",
        "--context", "kubernetes-admin@kubernetes",
    ]


def test_conn_args_without_context_omits_context_flag():
    eng = KubernetesEngine("/tmp/kc.yaml")
    assert eng._conn_args() == ["--kubeconfig", "/tmp/kc.yaml"]


def test_conn_args_empty_context_omits_context_flag():
    # Empty string context must behave like None (no flag).
    eng = KubernetesEngine("/tmp/kc.yaml", context="")
    assert eng._conn_args() == ["--kubeconfig", "/tmp/kc.yaml"]


def test_conn_args_helm_uses_kube_context_flag():
    # Regression: helm rejects --context; it must get --kube-context. kubectl
    # (default) gets --context. Mixing them up breaks helm install entirely.
    eng = KubernetesEngine("/tmp/kc.yaml", context="kubernetes-admin@kubernetes")
    assert eng._conn_args(for_helm=True) == [
        "--kubeconfig", "/tmp/kc.yaml",
        "--kube-context", "kubernetes-admin@kubernetes",
    ]
    assert eng._conn_args() == [
        "--kubeconfig", "/tmp/kc.yaml",
        "--context", "kubernetes-admin@kubernetes",
    ]


def test_conn_args_honors_override_path():
    eng = KubernetesEngine("/tmp/default.yaml", context="ctxA")
    assert eng._conn_args("/tmp/other.yaml") == [
        "--kubeconfig", "/tmp/other.yaml",
        "--context", "ctxA",
    ]


def test_conn_args_returns_fresh_list_each_call():
    # Immutability: callers extend the result, so each call must be independent.
    eng = KubernetesEngine("/tmp/kc.yaml", context="ctxA")
    first = eng._conn_args()
    first.append("--mutated")
    assert "--mutated" not in eng._conn_args()


# ---------------------------------------------------------------------------
# KubernetesEngine._get_api — kr8s client receives context=
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_api_passes_context_to_kr8s(monkeypatch):
    captured: dict = {}

    async def fake_api(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    import kr8s.asyncio

    monkeypatch.setattr(kr8s.asyncio, "api", fake_api)

    eng = KubernetesEngine("/tmp/kc.yaml", context="kubernetes-admin@kubernetes")
    await eng._get_api()

    assert captured["kubeconfig"] == "/tmp/kc.yaml"
    assert captured["context"] == "kubernetes-admin@kubernetes"


@pytest.mark.asyncio
async def test_get_api_omits_context_when_unset(monkeypatch):
    captured: dict = {}

    async def fake_api(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    import kr8s.asyncio

    monkeypatch.setattr(kr8s.asyncio, "api", fake_api)

    eng = KubernetesEngine("/tmp/kc.yaml")
    await eng._get_api()

    assert captured["kubeconfig"] == "/tmp/kc.yaml"
    assert "context" not in captured


# ---------------------------------------------------------------------------
# HelmService._run_helm_command — argv contains --context only when set
# ---------------------------------------------------------------------------

def _helm_service_with_captured_argv(monkeypatch):
    """Build a HelmService whose subprocess + kubeconfig are stubbed.

    Returns (service, captured) where captured["cmd"] holds the helm argv.
    """
    svc = HelmService.__new__(HelmService)  # bypass __init__ (no DB needed)

    # Stub kubeconfig prep — return an object with a stable .name.
    monkeypatch.setattr(
        svc, "_prepare_kubeconfig",
        lambda cluster: SimpleNamespace(name="/tmp/prepared-kc.yaml"),
    )

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")

    monkeypatch.setattr("services.helm_service.subprocess.run", fake_run)
    # os.path.exists/unlink in the finally block — make cleanup a no-op.
    monkeypatch.setattr("services.helm_service.os.path.exists", lambda p: False)

    return svc, captured


def test_run_helm_command_with_context_adds_flag(monkeypatch):
    svc, captured = _helm_service_with_captured_argv(monkeypatch)
    cluster = SimpleNamespace(context="kubernetes-admin@kubernetes")

    svc._run_helm_command(
        cluster, ["list"], namespace="ns1", context="kubernetes-admin@kubernetes"
    )

    cmd = captured["cmd"]
    assert "--kube-context" in cmd
    assert cmd[cmd.index("--kube-context") + 1] == "kubernetes-admin@kubernetes"
    assert "--context" not in cmd  # helm rejects kubectl's --context flag
    # kubeconfig still present
    assert "--kubeconfig" in cmd


def test_run_helm_command_without_context_omits_flag(monkeypatch):
    svc, captured = _helm_service_with_captured_argv(monkeypatch)
    cluster = SimpleNamespace(context="kubernetes-admin@kubernetes")

    svc._run_helm_command(cluster, ["list"], namespace="ns1")

    assert "--kube-context" not in captured["cmd"]
    assert "--kubeconfig" in captured["cmd"]


def test_install_chart_threads_context_into_argv(monkeypatch):
    svc, captured = _helm_service_with_captured_argv(monkeypatch)
    cluster = SimpleNamespace(context="kubernetes-admin@kubernetes")
    monkeypatch.setattr(svc, "get_cluster", lambda cid: cluster)

    svc.install_chart(
        cluster_id=1,
        release_name="rel",
        chart="repo/chart",
        namespace="ns1",
        context="kubernetes-admin@kubernetes",
    )

    cmd = captured["cmd"]
    assert "--kube-context" in cmd
    assert cmd[cmd.index("--kube-context") + 1] == "kubernetes-admin@kubernetes"
    assert "--context" not in cmd  # helm rejects kubectl's --context flag


def test_install_chart_without_context_omits_flag(monkeypatch):
    svc, captured = _helm_service_with_captured_argv(monkeypatch)
    cluster = SimpleNamespace(context="kubernetes-admin@kubernetes")
    monkeypatch.setattr(svc, "get_cluster", lambda cid: cluster)

    svc.install_chart(
        cluster_id=1,
        release_name="rel",
        chart="repo/chart",
        namespace="ns1",
    )

    assert "--kube-context" not in captured["cmd"]


def test_uninstall_release_threads_context_into_argv(monkeypatch):
    svc, captured = _helm_service_with_captured_argv(monkeypatch)
    cluster = SimpleNamespace(context="kubernetes-admin@kubernetes")
    monkeypatch.setattr(svc, "get_cluster", lambda cid: cluster)

    svc.uninstall_release(
        cluster_id=1,
        release_name="rel",
        namespace="ns1",
        context="kubernetes-admin@kubernetes",
    )

    cmd = captured["cmd"]
    assert "--kube-context" in cmd
    assert cmd[cmd.index("--kube-context") + 1] == "kubernetes-admin@kubernetes"
    assert "--context" not in cmd  # helm rejects kubectl's --context flag
