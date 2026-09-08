"""Component tests for graph-wide supply-chain resolution.

Exercises ``services/execution/supply_chain``:
  - walking the references graph to collect every container_image host,
  - enforcing the registry-host allowlist,
  - assembling a merged dockerconfigjson covering all hosts (standalone +
    derived icr/ecr token exchange, with cloud APIs mocked).

Uses the real SQLite DB from conftest with identity encryption.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from models import CloudCredentialTemplate, ContainerRegistry
from services.execution import supply_chain as sc
from tests.factories import ModuleLibraryFactory


def _decode(authfile_b64: str) -> dict:
    return json.loads(base64.b64decode(authfile_b64).decode("utf-8"))


def _ci_manifest(name: str, host: str, repo: str = "tools") -> dict:
    return {
        "schema_version": 1,
        "name": name,
        "version": "1.0.0",
        "kind": "container_image",
        "container_image": {
            "registry_host": host,
            "repository": repo,
            "digest": "sha256:" + "a" * 64,
        },
    }


@pytest.fixture(autouse=True)
def _identity_encryption():
    with patch("core.encryption.encrypt_value", side_effect=lambda v: v), \
         patch("core.encryption.decrypt_value", side_effect=lambda v: v), \
         patch("services.container_registry_service.decrypt_value", side_effect=lambda v: v), \
         patch("services.secrets_service.encrypt_value", side_effect=lambda v: v), \
         patch("services.secrets_service.decrypt_value", side_effect=lambda v: v):
        yield


@pytest.fixture(autouse=True)
def _wide_allowlist():
    # Default allowlist test isolation: permit the hosts the tests use.
    with patch(
        "services.execution.supply_chain.get_default",
        return_value="ghcr.io,us.icr.io,123.dkr.ecr.us-east-1.amazonaws.com",
    ):
        yield


@pytest.mark.component
class TestCollectHosts:
    def test_collects_root_and_referenced_hosts(self, db):
        # Referenced artifact lives in the catalog as its own ModuleLibrary row.
        ModuleLibraryFactory(
            db, name="sidecar", version="2.0.0",
            module_source_kind="artifact",
            pack_manifest=_ci_manifest("sidecar", "us.icr.io", "sidecar"),
        )
        root = ModuleLibraryFactory(
            db, name="root", version="1.0.0",
            module_source_kind="artifact",
            pack_manifest=_ci_manifest("root", "ghcr.io"),
            artifact_references={
                "root": "root@1.0.0",
                "nodes": ["root@1.0.0", "sidecar@2.0.0"],
                "edges": [{"from": "root@1.0.0", "to": "sidecar@2.0.0"}],
            },
        )

        hosts = sc.collect_container_image_hosts(db, root)
        assert hosts == ["ghcr.io", "us.icr.io"]

    def test_dedupes_repeated_hosts(self, db):
        ModuleLibraryFactory(
            db, name="sidecar", version="2.0.0",
            module_source_kind="artifact",
            pack_manifest=_ci_manifest("sidecar", "ghcr.io", "sidecar"),
        )
        root = ModuleLibraryFactory(
            db, name="root", version="1.0.0",
            module_source_kind="artifact",
            pack_manifest=_ci_manifest("root", "ghcr.io"),
            artifact_references={
                "root": "root@1.0.0",
                "nodes": ["root@1.0.0", "sidecar@2.0.0"],
                "edges": [],
            },
        )
        assert sc.collect_container_image_hosts(db, root) == ["ghcr.io"]


@pytest.mark.component
class TestAllowlistEnforcement:
    def test_rejects_host_not_on_allowlist(self, db):
        with patch(
            "services.execution.supply_chain.get_default", return_value="ghcr.io"
        ):
            with pytest.raises(sc.SupplyChainPolicyError, match="not in the configured"):
                sc.enforce_host_allowlist(db, ["evil.example.com"])

    def test_empty_allowlist_enforces_the_built_in_default(self, db):
        """#79: an empty/unset allowlist is FAIL-CLOSED.

        This test previously asserted the opposite -- that an empty setting
        disabled enforcement -- while the ingest-time reader in
        module_sync_service fell back to the built-in default. Same key, two
        readers, opposite meanings for "empty": an operator who cleared the
        setting, or a fresh install before the row existed, got an unenforced
        pull path while ingest still claimed to enforce. Both readers now
        share resolve_registry_host_allowlist, so "empty" means "the shipped
        default", never "anything goes".
        """
        with patch("services.execution.supply_chain.get_default", return_value=""):
            with pytest.raises(sc.SupplyChainPolicyError, match="not in the configured"):
                sc.enforce_host_allowlist(db, ["anything.example.com"])
            # ...but a host on the shipped default still passes.
            sc.enforce_host_allowlist(db, ["ghcr.io"])  # no raise

    def test_unset_and_raising_lookup_also_fail_closed(self, db):
        with patch("services.execution.supply_chain.get_default", return_value=None):
            with pytest.raises(sc.SupplyChainPolicyError):
                sc.enforce_host_allowlist(db, ["anything.example.com"])
        with patch("services.execution.supply_chain.get_default", side_effect=RuntimeError("db down")):
            with pytest.raises(sc.SupplyChainPolicyError):
                sc.enforce_host_allowlist(db, ["anything.example.com"])
            sc.enforce_host_allowlist(db, ["quay.io"])  # shipped default still honoured

    def test_ingest_and_runtime_resolve_the_same_allowlist(self, db):
        """The point of the shared resolver: the two readers cannot disagree."""
        from services.module_sync_service import ModuleSyncService

        for raw in ("", None, "  ", "harbor.internal, ghcr.io "):
            with patch("services.execution.supply_chain.get_default", return_value=raw):
                runtime = sc.resolve_registry_host_allowlist(db)
                ingest = set(ModuleSyncService(db)._registry_host_allowlist())
                assert runtime == ingest, f"readers disagree for raw={raw!r}: {runtime} vs {ingest}"
                assert runtime, f"resolver returned an empty allowlist for raw={raw!r}"


@pytest.mark.component
class TestGraphPullAuthfile:
    def test_merges_standalone_and_derived(self, db):
        # Standalone ghcr + derived icr; referenced sidecar uses icr.
        db.add(ContainerRegistry(
            name="ghcr", type="ghcr", registry_host="ghcr.io",
            username="jgruberf5", token_encrypted="ghp_tok",
        ))
        tpl = CloudCredentialTemplate(
            name="ibm", provider="ibm", ibmcloud_api_key_encrypted="ibm_key",
        )
        db.add(tpl)
        db.flush()
        db.add(ContainerRegistry(
            name="icr", type="icr", registry_host="us.icr.io",
            credential_template_id=tpl.id,
        ))
        db.flush()

        ModuleLibraryFactory(
            db, name="sidecar", version="2.0.0",
            module_source_kind="artifact",
            pack_manifest=_ci_manifest("sidecar", "us.icr.io", "sidecar"),
        )
        root = ModuleLibraryFactory(
            db, name="root", version="1.0.0",
            module_source_kind="artifact",
            pack_manifest=_ci_manifest("root", "ghcr.io"),
            artifact_references={
                "root": "root@1.0.0",
                "nodes": ["root@1.0.0", "sidecar@2.0.0"],
                "edges": [{"from": "root@1.0.0", "to": "sidecar@2.0.0"}],
            },
        )

        with patch(
            "services.ibm_cloud_service.IBMCloudService._exchange_api_key",
            return_value="iam-token",
        ):
            authfile = sc.resolve_graph_pull_authfile(db, root)

        assert authfile is not None
        doc = _decode(authfile)
        assert set(doc["auths"].keys()) == {"ghcr.io", "us.icr.io"}
        assert doc["auths"]["ghcr.io"]["username"] == "jgruberf5"
        assert doc["auths"]["us.icr.io"]["username"] == "iamapikey"
        assert doc["auths"]["us.icr.io"]["password"] == "iam-token"

    def test_ecr_token_assembled(self, db):
        tpl = CloudCredentialTemplate(
            name="aws", provider="aws", region="us-east-1",
            aws_access_key_id="AKIA", aws_secret_access_key_encrypted="secret",
        )
        db.add(tpl)
        db.flush()
        db.add(ContainerRegistry(
            name="ecr", type="ecr",
            registry_host="123.dkr.ecr.us-east-1.amazonaws.com",
            credential_template_id=tpl.id,
        ))
        db.flush()
        root = ModuleLibraryFactory(
            db, name="ecr-root", version="1.0.0",
            module_source_kind="artifact",
            pack_manifest=_ci_manifest("ecr-root", "123.dkr.ecr.us-east-1.amazonaws.com"),
        )

        token = base64.b64encode(b"AWS:ecr-pw").decode("ascii")
        fake_client = MagicMock()
        fake_client.get_authorization_token.return_value = {
            "authorizationData": [{"authorizationToken": token}]
        }
        with patch("boto3.client", return_value=fake_client):
            authfile = sc.resolve_graph_pull_authfile(db, root)

        doc = _decode(authfile)
        entry = doc["auths"]["123.dkr.ecr.us-east-1.amazonaws.com"]
        assert entry["username"] == "AWS"
        assert entry["password"] == "ecr-pw"

    def test_all_public_graph_returns_none(self, db):
        # No registry configured for the host → no credential.
        root = ModuleLibraryFactory(
            db, name="pub", version="1.0.0",
            module_source_kind="artifact",
            pack_manifest=_ci_manifest("pub", "ghcr.io"),
        )
        assert sc.resolve_graph_pull_authfile(db, root) is None
