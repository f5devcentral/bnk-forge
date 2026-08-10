"""ADR-204 destroy()/revert ports — verify each SSH module reverses its apply.

Uses a recording fake SSH session (no real SSH) to assert the commands a destroy
issues: helm uninstall for helm modules, kubectl delete (reverse) for manifest
modules, and the F5 webhook/finalizer/CRD cleanup for bnk-prerequisites.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


class FakeSession:
    host = "test-host"

    def __init__(self, fail_on: str | None = None):
        # fail_on: if set, any command containing this substring returns exit_code=1
        # (models a real teardown failure so the destroy exit-code path is exercised).
        self.commands: list[str] = []
        self.fail_on = fail_on

    def execute(self, command, timeout=300):
        self.commands.append(command)
        # Manifests/values are written to an mktemp path; return a realistic path so
        # _write_remote_tmp doesn't treat empty stdout as a mktemp failure.
        stdout = "/tmp/bnk.testABCD" if "mktemp" in command else ""
        failed = bool(self.fail_on and self.fail_on in command)
        return SimpleNamespace(
            exit_code=1 if failed else 0,
            stdout=stdout,
            stderr="teardown boom" if failed else "",
        )


def _noop(_):  # on_output
    pass


def test_flo_destroy_helm_uninstall():
    from modules.bare_metal.bnk_flo import BnkFloSSHModule

    sess = FakeSession()
    BnkFloSSHModule().destroy(sess, {"flo_namespace": "f5-operator"}, _noop)
    joined = "\n".join(sess.commands)
    assert "helm uninstall" in joined
    assert "flo" in joined
    assert "-n f5-operator" in joined


def test_network_setup_destroy_kubectl_delete():
    from modules.bare_metal.bnk_network_setup import NetworkSetupSSHModule

    sess = FakeSession()
    NetworkSetupSSHModule().destroy(
        sess,
        {"namespace": "f5-bnk", "external_nad_name": "sf-external", "internal_nad_name": "sf-internal"},
        _noop,
    )
    joined = "\n".join(sess.commands)
    assert "kubectl delete" in joined
    assert "sf-external" in joined and "sf-internal" in joined


def test_prerequisites_destroy_cleans_f5_then_deletes_namespaces():
    from modules.bare_metal.bnk_prerequisites import BnkPrerequisitesSSHModule

    sess = FakeSession()
    BnkPrerequisitesSSHModule().destroy(
        sess,
        {"operator_namespace": "f5-operator", "utils_namespace": "f5-utils",
         "gateway_namespace": "bnk-gw", "instance_namespace": "f5-bnk",
         "cne_pull_secret": "eyJrIjoidiJ9"},
        _noop,
    )
    # Cleanup must run before the manifest delete. The manifest (incl. the
    # Namespace resources) is written to a temp file first, then applied via a
    # separate `kubectl delete -f <file>` command.
    cleanup_idx = next(i for i, c in enumerate(sess.commands) if "validatingwebhookconfiguration" in c)
    delete_idx = next(i for i, c in enumerate(sess.commands) if "kubectl delete" in c)
    assert cleanup_idx < delete_idx
    cleanup = sess.commands[cleanup_idx]
    assert "finalizers" in cleanup
    assert r"k8s\.f5" in cleanup  # F5 CRD grep (escaped regex)
    assert "delete crd" in cleanup
    # Namespaces are rendered into the temp-file write (the non-sudo cat heredoc).
    manifest_cmd = next(c for c in sess.commands if "BNK_TMP_EOF" in c)
    assert "Namespace" in manifest_cmd
    assert "f5-operator" in manifest_cmd


def test_cneinstance_destroy_deletes_cr():
    from modules.bare_metal.bnk_cneinstance import BnkCneInstanceSSHModule

    sess = FakeSession()
    BnkCneInstanceSSHModule().destroy(
        sess,
        {"instance_namespace": "f5-bnk", "instance_name": "bnk-instance",
         "manifest_version": "2.2.1-x", "tmm_data_plane_mode": "sriov"},
        _noop,
    )
    joined = "\n".join(sess.commands)
    assert "kubectl delete" in joined
    assert "CNEInstance" in joined


# ── B7: destroy must surface a real teardown failure, not report success ──────


def test_helm_destroy_raises_on_uninstall_failure():
    """A failed `helm uninstall` must raise so the engine reports failure —
    previously it was `|| true` and always returned {destroyed: True}, silently
    orphaning the release."""
    from modules.bare_metal.bnk_flo import BnkFloSSHModule

    sess = FakeSession(fail_on="helm uninstall")
    with pytest.raises(RuntimeError, match="helm uninstall failed"):
        BnkFloSSHModule().destroy(sess, {"flo_namespace": "f5-operator"}, _noop)


def test_manifest_destroy_raises_on_delete_failure_and_shreds_tmp():
    """A failed `kubectl delete` must raise, and the manifest temp file must still
    be shredded (finally)."""
    from modules.bare_metal.bnk_network_setup import NetworkSetupSSHModule

    sess = FakeSession(fail_on="kubectl delete")
    with pytest.raises(RuntimeError, match="kubectl delete failed"):
        NetworkSetupSSHModule().destroy(
            sess,
            {"namespace": "f5-bnk", "external_nad_name": "sf-external", "internal_nad_name": "sf-internal"},
            _noop,
        )
    assert any("shred -u" in c for c in sess.commands)
