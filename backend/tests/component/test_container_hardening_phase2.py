"""Second container-runner hardening pass — issues #79 (items 3/5), #96 N2, #102."""

from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError


@pytest.mark.unit
@pytest.mark.component
class TestRegistryHostChangeClearsCredential:
    """Repointing a registry must invalidate its stored credential (#79 item 3).

    Registries are global and update_registry allows changing registry_host
    WITHOUT re-supplying the token. `_test_basic_v2` then decrypts the stored
    token and sends it as Basic auth to whatever host is now on the record — so
    operator A could repoint operator B's registry at a host they control, press
    Test, and collect B's PAT.

    Clearing on host change is deliberately narrower than an allowlist or a
    private-address check: both of those would also block a self-hosted Harbor
    or Artifactory on RFC1918, which is a supported configuration.
    """

    def test_changing_the_host_without_a_new_token_clears_the_credential(self, db):
        from models import ContainerRegistry
        from services.container_registry_service import ContainerRegistryService

        reg = ContainerRegistry(name="harbor-1", type="harbor",
                                registry_host="harbor.internal",
                                username="robot$ci", token_encrypted="enc_secret")
        db.add(reg)
        db.commit()
        db.refresh(reg)

        from routes.container_registries import ContainerRegistryUpdate

        ContainerRegistryService(db).update_registry(
            reg.id, ContainerRegistryUpdate(registry_host="attacker.example.com"))
        db.refresh(reg)

        assert reg.token_encrypted is None, (
            "the stored credential survived a host change — pressing Test would "
            "send it to the new host (#79 item 3)"
        )

    def test_changing_the_host_WITH_a_new_token_keeps_the_new_one(self, db):
        """Contrast: supplying a token for the new host is the legitimate flow."""
        from models import ContainerRegistry
        from services.container_registry_service import ContainerRegistryService

        reg = ContainerRegistry(name="harbor-2", type="harbor",
                                registry_host="harbor.internal",
                                username="robot$ci", token_encrypted="enc_old")
        db.add(reg)
        db.commit()
        db.refresh(reg)

        from routes.container_registries import ContainerRegistryUpdate

        ContainerRegistryService(db).update_registry(
            reg.id, ContainerRegistryUpdate(registry_host="harbor2.internal",
                                            token="brand-new"))
        db.refresh(reg)
        assert reg.token_encrypted, "a token supplied with the host change must be kept"


@pytest.mark.unit
class TestRunnerNetworkPolicy:
    """The runner NetworkPolicy must allow DNS + public egress (#79 item 5).

    `egress=[]` with policy_types including Egress denies ALL egress including
    DNS, so on an enforcing CNI a provisioning artifact cannot resolve anything
    or reach a cloud API — while the docstring claimed egress reached the cloud
    control plane.
    """

    def _policy(self):
        from services.execution.kubernetes_runner import KubernetesRunner, RunnerKubeConfig
        r = KubernetesRunner(RunnerKubeConfig(kubeconfig_path="/dev/null",
                                              context=None, namespace="bnk-forge-runner"))
        return r.build_network_policy()

    def test_ingress_is_still_fully_denied(self):
        assert self._policy().spec.ingress == []

    def test_dns_egress_is_allowed(self):
        rules = self._policy().spec.egress
        ports = [p for r in rules for p in (r.ports or [])]
        assert {(p.protocol, p.port) for p in ports} >= {("UDP", 53), ("TCP", 53)}, (
            "DNS egress is not allowed — on an enforcing CNI the artifact cannot "
            "resolve anything and the step fails (#79 item 5)"
        )

    def test_public_egress_allowed_but_private_ranges_excluded(self):
        rules = self._policy().spec.egress
        blocks = [p.ip_block for r in rules for p in (r.to or []) if p.ip_block]
        assert blocks, "no ipBlock egress rule — cloud control planes unreachable"
        b = blocks[0]
        assert b.cidr == "0.0.0.0/0"
        excepts = set(getattr(b, "_except", None) or [])
        for cidr in ("10.0.0.0/8", "192.168.0.0/16", "169.254.0.0/16"):
            assert cidr in excepts, (
                f"{cidr} is not excluded — a third-party artifact image can reach "
                "cluster-internal services or the cloud metadata endpoint"
            )


@pytest.mark.unit
class TestNonScalarActionInput:
    """A `type: string` input must reject dict/list values (#96 N2)."""

    DECL = [{"name": "scenario", "type": "string"}]

    def test_dict_value_is_rejected(self):
        from utils.security import validate_action_inputs
        with pytest.raises(ValueError, match="(?i)expected a string"):
            validate_action_inputs(self.DECL, {"scenario": {"nested": "object"}})

    def test_list_value_is_rejected(self):
        from utils.security import validate_action_inputs
        with pytest.raises(ValueError, match="(?i)expected a string"):
            validate_action_inputs(self.DECL, {"scenario": ["a", "b"]})

    def test_scalars_still_accepted(self):
        """Contrast: str/int/float/bool must keep working."""
        from utils.security import validate_action_inputs
        for v in ("tcpl4lb", 5, 1.5, True):
            assert validate_action_inputs(self.DECL, {"scenario": v})["scenario"] == v


@pytest.mark.unit
class TestSecretFileStepCollision:
    """A secret_files path must not collide with a directory a step creates (#102)."""

    def _manifest(self, secret_path, step_args):
        return {
            "schema_version": 1, "name": "runner", "version": "1.0.0",
            "kind": "container_image",
            "container_image": {"registry_host": "ghcr.io", "repository": "org/r",
                                "digest": "sha256:" + "a" * 64},
            "secret_files": [{"secret_name": "far-key", "path": secret_path}],
            "steps": {"apply": [{"name": "init-poc", "args": step_args, "run_once": True}]},
        }

    def _validate(self, manifest):
        from services.module_metadata import ModuleMetadataValidator
        return ModuleMetadataValidator().validate_artifact_manifest(
            manifest, registry_host_allowlist=["ghcr.io"]
        )

    def test_collision_is_rejected(self):
        """The real case: both fields template off the same input."""
        from services.module_metadata import InvalidMetadataSchemaError
        m = self._manifest("poc/keys/f5-far-auth-key.tgz", ["ocibnkctl", "init", "poc"])
        with pytest.raises(InvalidMetadataSchemaError, match="(?i)already exists|bare argument"):
            self._validate(m)

    def test_non_colliding_manifest_is_accepted(self):
        """Contrast: a different parent directory validates fine."""
        self._validate(self._manifest("secrets/f5-far-auth-key.tgz",
                                      ["ocibnkctl", "init", "poc"]))

    def test_flags_and_paths_are_not_treated_as_creation_targets(self):
        """Narrowness check: only BARE tokens count, or this false-positives."""
        self._validate(self._manifest("poc/keys/k.tgz",
                                      ["ocibnkctl", "init", "--name", "poc/sub"]))
