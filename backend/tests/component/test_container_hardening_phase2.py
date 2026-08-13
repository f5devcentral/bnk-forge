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

    def test_far_service_account_is_also_cleared(self, db):
        """Every credential family, not just basic-auth (review finding).

        _clear_off_family_credentials preserves the CURRENT family's credential,
        so clearing only token_encrypted left a FAR registry's service account
        intact — and _test_far sends it to the same registry_host.
        """
        from models import ContainerRegistry
        from routes.container_registries import ContainerRegistryUpdate
        from services.container_registry_service import ContainerRegistryService

        reg = ContainerRegistry(name="far-1", type="far",
                                registry_host="far.internal",
                                far_service_account_encrypted="enc_sa")
        db.add(reg)
        db.commit()
        db.refresh(reg)

        ContainerRegistryService(db).update_registry(
            reg.id, ContainerRegistryUpdate(registry_host="attacker.example.com"))
        db.refresh(reg)

        assert reg.far_service_account_encrypted is None, (
            "the FAR service account survived a host change — _test_far would "
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


@pytest.mark.component
class TestOffFamilyCredentialBypass:
    """Supplying an OFF-family credential must not preserve the at-risk one.

    Review finding: the clearing guard was `bool(token or far_service_account)
    or credential_template_id is not None` — a disjunction over all three
    families gating the block that protects the ONE family the record reads. So
    sending any other family's value satisfied the guard and left the stored
    secret intact for the next Test against the attacker's host.
    """

    def _reg(self, db, **kw):
        from models import ContainerRegistry
        reg = ContainerRegistry(registry_host="victim.internal", **kw)
        db.add(reg)
        db.commit()
        db.refresh(reg)
        return reg

    def _repoint(self, db, reg, **payload):
        from routes.container_registries import ContainerRegistryUpdate
        from services.container_registry_service import ContainerRegistryService
        ContainerRegistryService(db).update_registry(
            reg.id, ContainerRegistryUpdate(registry_host="attacker.example.com", **payload))
        db.refresh(reg)

    def test_far_service_account_does_not_preserve_a_basic_token(self, db):
        """`far_service_account: "{}"` is the cheapest variant — any parseable
        JSON is accepted, so no real secret is needed to trigger it."""
        reg = self._reg(db, name="h1", type="harbor", token_encrypted="enc_victimPAT")
        self._repoint(db, reg, far_service_account="{}")
        assert reg.token_encrypted is None, (
            "an off-family credential preserved the basic-auth token — "
            "_test_basic_v2 would send it to attacker.example.com"
        )

    def test_token_does_not_preserve_a_far_service_account(self, db):
        reg = self._reg(db, name="f1", type="far",
                        far_service_account_encrypted="enc_victimSA")
        self._repoint(db, reg, token="x")
        assert reg.far_service_account_encrypted is None, (
            "an off-family token preserved the FAR service account"
        )

    def test_derived_registry_still_clears_when_no_template_is_resupplied(self, db):
        """A derived registry loses its template on a bare host change.

        The reviewer's third variant — replaying the record's own template id —
        is deliberately NOT blocked: create_registry already accepts any
        template id on a new registry at any host, so refusing it on update buys
        nothing and makes a derived registry's host unchangeable. The underlying
        problem is that credential templates carry no per-operator authorisation
        on either path; that is filed separately and is not closed here.

        What this pins is that a host change with NO template re-supplied still
        detaches it.
        """
        from models.system import CloudCredentialTemplate
        tpl = CloudCredentialTemplate(name="ecr-tpl", provider="aws")
        db.add(tpl)
        db.flush()
        reg = self._reg(db, name="e1", type="harbor", credential_template_id=tpl.id,
                        token_encrypted="enc_v")
        self._repoint(db, reg)
        assert reg.credential_template_id is None
        assert reg.token_encrypted is None

    def test_empty_old_host_still_clears(self, db):
        """registry_host has no min_length at create, so "" is reachable and the
        old truthiness test skipped the guard entirely."""
        reg = self._reg(db, name="blank", type="harbor", token_encrypted="enc_v")
        reg.registry_host = ""
        db.commit()
        self._repoint(db, reg)
        assert reg.token_encrypted is None

    def test_the_whole_cached_verdict_is_cleared(self, db):
        """A null status beside a stale success timestamp is not 'cleared'."""
        from datetime import UTC, datetime
        reg = self._reg(db, name="v1", type="harbor", token_encrypted="enc_v",
                        last_test_status="ok", last_test_at=datetime.now(UTC))
        self._repoint(db, reg)
        assert reg.last_test_status is None
        assert reg.last_test_at is None

    def test_supplying_a_new_credential_for_the_new_host_still_works(self, db):
        """Contrast: the legitimate move-and-recredential flow must survive."""
        reg = self._reg(db, name="ok1", type="harbor", token_encrypted="enc_old")
        self._repoint(db, reg, token="brand-new")
        assert reg.token_encrypted, "a token supplied with the host change must be kept"


@pytest.mark.unit
class TestNetworkPolicyScoping:
    """Every egress rule must carry an explicit `to` (review finding).

    A rule with `ports` and no `to` permits ALL destinations on those ports, and
    an ipBlock `except` binds only to its own rule — so the DNS rule silently
    reopened 169.254.169.254:53 and every RFC1918 host on 53, TCP included.
    """

    def _policy(self):
        from services.execution.kubernetes_runner import KubernetesRunner, RunnerKubeConfig
        return KubernetesRunner(RunnerKubeConfig(kubeconfig_path="/dev/null", context=None,
                                                 namespace="bnk-forge-runner")).build_network_policy()

    def test_no_egress_rule_is_unscoped(self):
        for i, rule in enumerate(self._policy().spec.egress):
            assert getattr(rule, "to", None), (
                f"egress rule[{i}] has no 'to' — it permits ALL destinations on "
                "its ports, defeating the ipBlock except list"
            )

    def test_dns_is_scoped_to_the_resolver_namespace(self):
        rules = [r for r in self._policy().spec.egress
                 if any(p.port == 53 for p in (r.ports or []))]
        assert rules, "no DNS rule"
        peers = rules[0].to
        assert any(getattr(p, "namespace_selector", None) for p in peers), (
            "DNS egress is not namespace-scoped"
        )

    def test_the_builder_refuses_an_unscoped_rule(self):
        """The guard is enforced in code, not left to review."""
        from kubernetes import client as k

        from services.execution.kubernetes_runner import KubernetesRunner
        bad = k.V1NetworkPolicy(spec=k.V1NetworkPolicySpec(
            pod_selector=k.V1LabelSelector(), policy_types=["Egress"],
            egress=[k.V1NetworkPolicyEgressRule(ports=[k.V1NetworkPolicyPort(port=53)])]))
        with pytest.raises(ValueError, match="no 'to'"):
            KubernetesRunner._assert_every_egress_rule_is_scoped(bad)


@pytest.mark.unit
class TestCollisionCheckPrecision:
    """The collision check must not fire on a flag VALUE, and must cover actions."""

    def _manifest(self, secret_path, *, steps=None, actions=None):
        m = {"schema_version": 1, "name": "r", "version": "1.0.0", "kind": "container_image",
             "container_image": {"registry_host": "ghcr.io", "repository": "o/r",
                                 "digest": "sha256:" + "a" * 64},
             "secret_files": [{"secret_name": "k", "path": secret_path}],
             "steps": steps or {"apply": [{"name": "s", "args": ["tool", "run"]}]}}
        if actions:
            m["actions"] = actions
        return m

    def _validate(self, m):
        from services.module_metadata import ModuleMetadataValidator
        return ModuleMetadataValidator().validate_artifact_manifest(
            m, registry_host_allowlist=["ghcr.io"])

    def test_a_flag_value_is_not_a_creation_target(self):
        """`--name poc` names an argument, not a directory the step creates.

        This was rejected before — a false positive the docstring disclaims.
        """
        self._validate(self._manifest(
            "poc/keys/k.tgz",
            steps={"apply": [{"name": "init", "args": ["ocibnkctl", "init", "--name", "poc"]}]}))

    def test_inline_flag_value_is_also_not_a_target(self):
        self._validate(self._manifest(
            "poc/keys/k.tgz",
            steps={"apply": [{"name": "init", "args": ["ocibnkctl", "init", "--name=poc"]}]}))

    def test_a_real_positional_collision_is_still_caught(self):
        """Contrast: the genuine #102 shape must still be rejected."""
        from services.module_metadata import InvalidMetadataSchemaError
        with pytest.raises(InvalidMetadataSchemaError):
            self._validate(self._manifest(
                "poc/keys/k.tgz",
                steps={"apply": [{"name": "init", "args": ["ocibnkctl", "init", "poc"]}]}))

    def test_the_actions_step_set_is_also_checked(self):
        """materialize_secret_files runs on the action path too, so the same
        unrecoverable failure recurs there."""
        from services.module_metadata import InvalidMetadataSchemaError
        with pytest.raises(InvalidMetadataSchemaError, match="actions.run-e2e"):
            self._validate(self._manifest(
                "poc/keys/k.tgz",
                actions={"run-e2e": {"title": "E2E",
                                     "steps": [{"name": "e", "args": ["tool", "init", "poc"]}]}}))


@pytest.mark.unit
class TestRenderRejectsNonScalars:
    """The scalar check belongs at the render chokepoint, not in one validator.

    validate_action_inputs guards only the action path; lifecycle steps render
    from ctx.variables (module.variables + variable_overrides, both JSON
    columns), so a dict there still reached step argv as a Python repr.
    """

    def _engine(self, tmp_path):
        from unittest.mock import MagicMock

        from services.execution.container_engine import ContainerEngine
        return ContainerEngine(MagicMock(), workspace_host_path=str(tmp_path),
                               workspace_local_path=str(tmp_path))

    def test_dict_in_lifecycle_variables_is_rejected(self, tmp_path):
        e = self._engine(tmp_path)
        with pytest.raises(ValueError, match="only scalar"):
            e._render_str("--cfg={{inputs.blob}}", {"blob": {"a": 1}})

    def test_list_is_rejected(self, tmp_path):
        e = self._engine(tmp_path)
        with pytest.raises(ValueError, match="only scalar"):
            e._render_str("{{inputs.items}}", {"items": [1, 2]})

    def test_scalars_and_missing_keys_still_render(self, tmp_path):
        """Contrast: the normal path, and the documented empty-string fallback."""
        e = self._engine(tmp_path)
        assert e._render_str("{{inputs.a}}-{{inputs.b}}", {"a": "x", "b": 5}) == "x-5"
        assert e._render_str("{{inputs.missing}}", {}) == ""
