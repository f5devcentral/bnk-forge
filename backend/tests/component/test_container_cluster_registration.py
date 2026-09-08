"""Component tests for generic container-engine cluster auto-registration.

A container module that surfaces a kubeconfig in its outputs should register a
KubernetesCluster (cloud-agnostic, linked to its project so the credential
template keeps rotating tokens current). Destroying the module unregisters it.
"""

import pytest

from models import KubernetesCluster
from services.cluster_auto_registration_service import (
    maybe_register_container_cluster,
    maybe_unregister_container_cluster,
)
from tests.factories import ProjectFactory, ProjectModuleFactory

PORTABLE_KUBECONFIG = """\
apiVersion: v1
kind: Config
clusters:
- cluster:
    server: https://c1.us-south.containers.cloud.ibm.com:30000
    certificate-authority-data: dGVzdC1jYQ==
  name: roks-e2e
contexts:
- context:
    cluster: roks-e2e
    user: roks-e2e-admin
  name: roks-e2e
current-context: roks-e2e
users:
- name: roks-e2e-admin
  user:
    token: fake-iam-token
"""


def _ibm_cluster_module(db, *, outputs):
    project = ProjectFactory(db, cloud_provider="ibm", region="us-south")
    module = ProjectModuleFactory(db, project=project, status="applied")
    module.outputs = outputs
    db.flush()
    return module


@pytest.mark.component
class TestContainerClusterRegistration:
    def test_registers_cluster_from_surfaced_kubeconfig(self, db):
        module = _ibm_cluster_module(db, outputs={
            "cluster_name": "roks-e2e",
            "cluster_id": "d8q-123",
            "master_url": "https://c1.us-south.containers.cloud.ibm.com:30000",
            "region": "us-south",
            "kubeconfig": PORTABLE_KUBECONFIG,
        })

        cluster = maybe_register_container_cluster(db, module)
        assert cluster is not None
        assert cluster.name == "roks-e2e"
        assert cluster.cloud_provider == "ibm"  # from the project → drives credential-template refresh
        assert cluster.api_server == "https://c1.us-south.containers.cloud.ibm.com:30000"
        assert cluster.kubeconfig_encrypted  # the surfaced kubeconfig was stored
        assert cluster.project_id == module.project_id
        assert cluster.meta_data["source_module_id"] == module.id

        rows = db.query(KubernetesCluster).filter(KubernetesCluster.name == "roks-e2e").all()
        assert len(rows) == 1

    def test_idempotent_update_not_duplicate(self, db):
        module = _ibm_cluster_module(db, outputs={
            "cluster_name": "roks-e2e",
            "master_url": "https://old:30000",
            "kubeconfig": PORTABLE_KUBECONFIG,
        })
        first = maybe_register_container_cluster(db, module)
        assert first is not None

        # Re-apply with a new endpoint — updates the existing row, no duplicate.
        module.outputs = {**module.outputs, "master_url": "https://new:30000"}
        db.flush()
        second = maybe_register_container_cluster(db, module)
        assert second.id == first.id
        assert second.api_server == "https://new:30000"
        assert db.query(KubernetesCluster).filter(KubernetesCluster.name == "roks-e2e").count() == 1

    def test_same_name_from_a_different_module_does_not_clobber(self, db):
        """#79 item 7: two modules in one project surfacing the same cluster_name.

        The lookup keys on (name, project); unregister keys on source_module_id.
        B used to overwrite A's row, and A's destroy could then no longer find
        it. B must be refused; A's row and A's destroy path must be intact.
        """
        from tests.factories import ProjectModuleFactory

        module_a = _ibm_cluster_module(db, outputs={
            "cluster_name": "roks-e2e",
            "master_url": "https://a:30000",
            "kubeconfig": PORTABLE_KUBECONFIG,
        })
        row_a = maybe_register_container_cluster(db, module_a)
        assert row_a is not None

        # Module B in the SAME project, same cluster_name, different endpoint.
        module_b = ProjectModuleFactory(db, project=module_a.project, status="applied")
        module_b.outputs = {
            "cluster_name": "roks-e2e",
            "master_url": "https://b:30000",
            "kubeconfig": PORTABLE_KUBECONFIG,
        }
        db.flush()

        assert maybe_register_container_cluster(db, module_b) is None

        db.refresh(row_a)
        assert row_a.api_server == "https://a:30000", "B clobbered A's endpoint"
        assert (row_a.meta_data or {}).get("source_module_id") == module_a.id
        assert db.query(KubernetesCluster).filter(KubernetesCluster.name == "roks-e2e").count() == 1
        # And A can still clean up after itself.
        assert maybe_unregister_container_cluster(db, module_a) is True
        assert db.query(KubernetesCluster).count() == 0

    def test_hand_registered_row_is_refreshed_but_not_claimed(self, db):
        """A row with no source_module_id is hand-registered (or pre-dates
        ownership). Its kubeconfig may be refreshed -- the pre-existing
        behaviour -- but it must NOT be adopted, or destroying the module
        would delete the operator's cluster, which unregister's
        source_module_id keying exists to prevent."""
        module = _ibm_cluster_module(db, outputs={
            "cluster_name": "roks-e2e",
            "master_url": "https://new:30000",
            "kubeconfig": PORTABLE_KUBECONFIG,
        })
        hand = KubernetesCluster(
            name="roks-e2e", context="roks-e2e", api_server="https://old:30000",
            status="active", project_id=module.project_id,
            kubeconfig_encrypted="x", default_namespace="default", meta_data=None,
        )
        db.add(hand)
        db.flush()

        refreshed = maybe_register_container_cluster(db, module)
        assert refreshed is not None and refreshed.id == hand.id
        assert refreshed.api_server == "https://new:30000"          # refreshed
        assert (refreshed.meta_data or {}).get("source_module_id") is None  # not claimed

        # Destroying the module must leave the hand-registered cluster alone.
        assert maybe_unregister_container_cluster(db, module) is False
        assert db.query(KubernetesCluster).count() == 1

    def test_no_kubeconfig_surfaced_skips(self, db):
        # roksbnkctl's pre-fix outputs: cluster_id present but no kubeconfig → skip.
        module = _ibm_cluster_module(db, outputs={
            "cluster_name": "roks-e2e",
            "cluster_id": "d8q-123",
            "master_url": "https://c1:30000",
        })
        assert maybe_register_container_cluster(db, module) is None
        assert db.query(KubernetesCluster).count() == 0

    def test_declared_cluster_block_without_kubeconfig_warns(self, db, caplog):
        """A manifest that promises a cluster but surfaces nothing must say so
        loudly — the silent skip cost a live debugging session (#452)."""
        import logging

        module = _ibm_cluster_module(db, outputs={"cluster_name": "poc"})
        module.library_module.pack_manifest = {
            "cluster": {"kubeconfig_file": "poc/artifacts/kubeconfig"}
        }
        db.flush()

        with caplog.at_level(logging.WARNING, logger="services.cluster_auto_registration_service"):
            assert maybe_register_container_cluster(db, module) is None
        assert any(
            "declares a cluster block" in rec.message for rec in caplog.records
        )

    def test_undeclared_module_without_kubeconfig_stays_silent(self, db, caplog):
        """No cluster block declared → the skip is the normal case for
        non-cluster modules; no warning noise."""
        import logging

        module = _ibm_cluster_module(db, outputs={"cluster_name": "poc"})
        with caplog.at_level(logging.WARNING, logger="services.cluster_auto_registration_service"):
            assert maybe_register_container_cluster(db, module) is None
        assert not caplog.records

    def test_unregister_on_destroy(self, db):
        module = _ibm_cluster_module(db, outputs={
            "cluster_name": "roks-e2e",
            "kubeconfig": PORTABLE_KUBECONFIG,
        })
        maybe_register_container_cluster(db, module)
        assert db.query(KubernetesCluster).count() == 1

        assert maybe_unregister_container_cluster(db, module) is True
        assert db.query(KubernetesCluster).count() == 0

    def test_registers_from_manifest_declared_kubeconfig_file(self, db, tmp_path):
        # The module declares it surfaces the kubeconfig at a workspace file path
        # (roksbnkctl writes /work/.roksbnkctl/.kube/config). bnk-forge reads it
        # from the workspace — no kubeconfig in outputs.
        module = _ibm_cluster_module(db, outputs={"cluster_name": "roks-e2e", "master_url": "https://c1:30000"})
        module.library_module.pack_manifest = {
            "cluster": {
                "kubeconfig_file": ".roksbnkctl/.kube/config",
                "name_output": "cluster_name",
                "api_server_output": "master_url",
            }
        }
        db.flush()
        kube_dir = tmp_path / ".roksbnkctl" / ".kube"
        kube_dir.mkdir(parents=True)
        (kube_dir / "config").write_text(PORTABLE_KUBECONFIG)

        cluster = maybe_register_container_cluster(db, module, workspace_path=str(tmp_path))
        assert cluster is not None
        assert cluster.name == "roks-e2e"
        assert cluster.api_server == "https://c1:30000"
        assert cluster.kubeconfig_encrypted

    def test_registers_from_manifest_declared_output_key(self, db):
        module = _ibm_cluster_module(db, outputs={"cluster_name": "roks-e2e", "my_kubeconfig": PORTABLE_KUBECONFIG})
        module.library_module.pack_manifest = {"cluster": {"kubeconfig_output": "my_kubeconfig"}}
        db.flush()

        cluster = maybe_register_container_cluster(db, module)
        assert cluster is not None
        assert cluster.name == "roks-e2e"

    def test_declared_cloud_provider_and_region_win_over_project(self, db):
        # The module declares ibm + region_output; even if the project were
        # mis-set, the registered cluster uses the module's declaration so the
        # credential-template (IAM) refresh dispatches correctly.
        project = ProjectFactory(db, cloud_provider="aws", region="us-east-1")
        module = ProjectModuleFactory(db, project=project, status="applied")
        module.outputs = {"cluster_name": "roks-e2e", "region": "us-south", "kubeconfig": PORTABLE_KUBECONFIG}
        module.library_module.pack_manifest = {
            "cluster": {"cloud_provider": "ibm", "region_output": "region"}
        }
        db.flush()

        cluster = maybe_register_container_cluster(db, module)
        assert cluster is not None
        assert cluster.cloud_provider == "ibm"
        assert cluster.region == "us-south"

    def test_declared_templated_name_used_when_no_outputs(self, db, tmp_path):
        """ocibnkctl writes no outputs file at all — the declared (templated)
        cluster.name is the fallback so registration still has a name (#452)."""
        module = _ibm_cluster_module(db, outputs={})
        module.variables = {"poc_name": "poc"}
        module.library_module.pack_manifest = {
            "cluster": {
                "name": "{{inputs.poc_name}}",
                "kubeconfig_file": "{{inputs.poc_name}}/artifacts/kubeconfig",
                "cloud_provider": "on-prem",
            }
        }
        db.flush()
        kube_dir = tmp_path / "poc" / "artifacts"
        kube_dir.mkdir(parents=True)
        (kube_dir / "kubeconfig").write_text(PORTABLE_KUBECONFIG)

        cluster = maybe_register_container_cluster(db, module, workspace_path=str(tmp_path))
        assert cluster is not None
        assert cluster.name == "poc"
        assert cluster.cloud_provider == "on-prem"

    def test_outputs_name_wins_over_declared_name(self, db):
        """Outputs are the tool's ground truth; the declared name is only a fallback."""
        module = _ibm_cluster_module(db, outputs={
            "cluster_name": "from-outputs",
            "kubeconfig": PORTABLE_KUBECONFIG,
        })
        module.library_module.pack_manifest = {"cluster": {"name": "from-manifest"}}
        db.flush()

        cluster = maybe_register_container_cluster(db, module)
        assert cluster is not None
        assert cluster.name == "from-outputs"

    def test_kubeconfig_file_templates_inputs(self, db, tmp_path):
        """ocibnkctl writes under a {{inputs.poc_name}}/ directory it creates —
        the declared kubeconfig_file path must template inputs like step args do."""
        module = _ibm_cluster_module(db, outputs={"cluster_name": "poc"})
        module.variables = {"poc_name": "poc"}
        module.library_module.pack_manifest = {
            "cluster": {"kubeconfig_file": "{{inputs.poc_name}}/artifacts/kubeconfig"}
        }
        db.flush()
        kube_dir = tmp_path / "poc" / "artifacts"
        kube_dir.mkdir(parents=True)
        (kube_dir / "kubeconfig").write_text(PORTABLE_KUBECONFIG)

        cluster = maybe_register_container_cluster(db, module, workspace_path=str(tmp_path))
        assert cluster is not None
        assert cluster.name == "poc"

    def test_templated_kubeconfig_file_traversal_is_guarded(self, db, tmp_path):
        """A malicious input value must not escape the workspace — the traversal
        guard runs on the rendered path."""
        module = _ibm_cluster_module(db, outputs={"cluster_name": "poc"})
        module.variables = {"poc_name": "../../etc"}
        module.library_module.pack_manifest = {
            "cluster": {"kubeconfig_file": "{{inputs.poc_name}}/passwd"}
        }
        db.flush()

        assert maybe_register_container_cluster(db, module, workspace_path=str(tmp_path)) is None
        assert db.query(KubernetesCluster).count() == 0

    def test_kubeconfig_file_path_traversal_is_guarded(self, db, tmp_path):
        module = _ibm_cluster_module(db, outputs={"cluster_name": "roks-e2e"})
        module.library_module.pack_manifest = {"cluster": {"kubeconfig_file": "../../etc/passwd"}}
        db.flush()

        assert maybe_register_container_cluster(db, module, workspace_path=str(tmp_path)) is None
        assert db.query(KubernetesCluster).count() == 0


@pytest.mark.component
class TestContainerRegistrationTaskWrapper:
    """The container_tasks wrapper must enqueue the BNK cluster scan after
    registering — the opentofu/ssh paths do; without it the cluster shows
    under Kubernetes but never under F5 BNK until a manual scan (#452)."""

    def test_wrapper_enqueues_cluster_scan_after_registration(self, db):
        from unittest.mock import patch

        from tasks.container_tasks import _maybe_register_container_cluster

        module = _ibm_cluster_module(db, outputs={
            "cluster_name": "roks-e2e",
            "kubeconfig": PORTABLE_KUBECONFIG,
        })

        with patch("tasks.cluster_scan_task.enqueue_cluster_scan") as mock_enqueue:
            _maybe_register_container_cluster(db, module)

        cluster = db.query(KubernetesCluster).filter(KubernetesCluster.name == "roks-e2e").one()
        mock_enqueue.assert_called_once_with(cluster.id)

    def test_wrapper_does_not_enqueue_when_nothing_registered(self, db):
        from unittest.mock import patch

        from tasks.container_tasks import _maybe_register_container_cluster

        module = _ibm_cluster_module(db, outputs={"cluster_name": "roks-e2e"})  # no kubeconfig

        with patch("tasks.cluster_scan_task.enqueue_cluster_scan") as mock_enqueue:
            _maybe_register_container_cluster(db, module)

        mock_enqueue.assert_not_called()
