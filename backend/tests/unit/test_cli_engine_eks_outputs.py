"""Unit tests for BnkctlEngine.get_outputs() EKS-contract extension.

Covers:
  - Real apply: state.env + kubeconfig present → full EKS contract keys emitted.
  - Dry-run: no state.env → minimal dict returned, no exception raised.
  - Partial: state.env present but CA missing → minimal dict returned, no exception.
  - Auto-discover: cluster_name variable absent, state.env found by scanning .awsbnkctl/.
"""

import textwrap
from pathlib import Path

import pytest
import yaml

from services.execution.cli_engine import BnkctlEngine
from services.execution.engine_interface import ModuleContext

# ── Fixtures ──────────────────────────────────────────────────────────────────

_STATE_ENV = textwrap.dedent("""\
    EKS_CLUSTER_NAME=bnkcli1
    EKS_ENDPOINT=https://ABCD1234.gr7.ap-southeast-2.eks.amazonaws.com
    AWS_REGION=ap-southeast-2
    EKS_VERSION=1.30
    EKS_CLUSTER_ARN=arn:aws:eks:ap-southeast-2:292785712872:cluster/bnkcli1
    EKS_OIDC_URL=https://oidc.eks.ap-southeast-2.amazonaws.com/id/ABCD1234
""")

_CA_DATA = "LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0t"  # base64 stub

def _make_kubeconfig(ca_data: str) -> str:
    doc = {
        "apiVersion": "v1",
        "kind": "Config",
        "clusters": [
            {
                "name": "bnkcli1",
                "cluster": {
                    "server": "https://ABCD1234.gr7.ap-southeast-2.eks.amazonaws.com",
                    "certificate-authority-data": ca_data,
                },
            }
        ],
    }
    return yaml.dump(doc)


def _make_ctx(
    tmp_path: Path,
    variables: dict | None = None,
) -> tuple[BnkctlEngine, ModuleContext]:
    engine = BnkctlEngine(db_session_factory=None)
    engine._WORKSPACE_ROOT = str(tmp_path)
    ctx = ModuleContext(
        module_id=10,
        project_id=1,
        path="cli-bnkctl/bnk-demo",
        category="infra",
        variables=variables or {"name": "bnkcli1", "region": "ap-southeast-2"},
        credentials_env={},
    )
    return engine, ctx


def _write_state_files(tmp_path: Path, cluster_name: str = "bnkcli1") -> Path:
    """Write state.env + kubeconfig under <workspace>/.awsbnkctl/<cluster>."""
    workspace = tmp_path / "1" / "awsbnkctl"
    state_dir = workspace / ".awsbnkctl" / cluster_name
    state_dir.mkdir(parents=True)
    (state_dir / "state.env").write_text(_STATE_ENV)
    (state_dir / "kubeconfig").write_text(_make_kubeconfig(_CA_DATA))
    return state_dir


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_get_outputs_emits_eks_contract_keys_on_real_apply(tmp_path):
    """get_outputs() returns all 4 EKS-contract keys when state.env+kubeconfig exist."""
    _write_state_files(tmp_path)
    engine, ctx = _make_ctx(tmp_path)

    outputs = engine.get_outputs(ctx)

    assert outputs["cluster_name"] == "bnkcli1"
    assert outputs["cluster_endpoint"] == "https://ABCD1234.gr7.ap-southeast-2.eks.amazonaws.com"
    assert outputs["cluster_certificate_authority_data"] == _CA_DATA
    assert outputs["region"] == "ap-southeast-2"


def test_get_outputs_emits_optional_eks_keys_on_real_apply(tmp_path):
    """get_outputs() also returns cluster_version, cluster_arn, oidc_provider_url."""
    _write_state_files(tmp_path)
    engine, ctx = _make_ctx(tmp_path)

    outputs = engine.get_outputs(ctx)

    assert outputs["cluster_version"] == "1.30"
    assert outputs["cluster_arn"] == "arn:aws:eks:ap-southeast-2:292785712872:cluster/bnkcli1"
    assert outputs["oidc_provider_url"] == "https://oidc.eks.ap-southeast-2.amazonaws.com/id/ABCD1234"


def test_get_outputs_returns_minimal_dict_on_dry_run_no_raise(tmp_path):
    """Dry-run: no state.env → minimal dict returned without raising."""
    engine, ctx = _make_ctx(tmp_path)  # no state files written

    outputs = engine.get_outputs(ctx)  # must NOT raise

    assert outputs["cluster_name"] == "bnkcli1"
    assert outputs["region"] == "ap-southeast-2"
    assert "cluster_endpoint" not in outputs
    assert "cluster_certificate_authority_data" not in outputs


def test_get_outputs_returns_minimal_dict_when_ca_missing(tmp_path):
    """Partial state: state.env present but kubeconfig absent → minimal dict, no raise."""
    workspace = tmp_path / "1" / "awsbnkctl"
    state_dir = workspace / ".awsbnkctl" / "bnkcli1"
    state_dir.mkdir(parents=True)
    (state_dir / "state.env").write_text(_STATE_ENV)
    # kubeconfig intentionally NOT written

    engine, ctx = _make_ctx(tmp_path)
    outputs = engine.get_outputs(ctx)

    assert "cluster_certificate_authority_data" not in outputs
    assert outputs["cluster_name"] == "bnkcli1"


def test_get_outputs_auto_discovers_state_dir_when_name_absent(tmp_path):
    """When cluster_name variable absent, get_outputs auto-discovers via .awsbnkctl scan."""
    _write_state_files(tmp_path, cluster_name="bnkcli1")
    # ctx has no "name" in variables
    engine, ctx = _make_ctx(tmp_path, variables={"region": "ap-southeast-2"})

    outputs = engine.get_outputs(ctx)

    assert outputs["cluster_endpoint"] == "https://ABCD1234.gr7.ap-southeast-2.eks.amazonaws.com"
    assert outputs["cluster_certificate_authority_data"] == _CA_DATA


def test_get_outputs_preserves_base_keys_on_real_apply(tmp_path):
    """Existing back-compat keys (kubeconfig_path, vip, workspace) survive real apply."""
    _write_state_files(tmp_path)
    engine, ctx = _make_ctx(tmp_path, variables={"name": "bnkcli1", "region": "ap-southeast-2", "vip": "1.2.3.4"})

    outputs = engine.get_outputs(ctx)

    assert "kubeconfig_path" in outputs
    assert outputs["vip"] == "1.2.3.4"
    assert "workspace" in outputs
