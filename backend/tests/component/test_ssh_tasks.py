"""
Component tests for SSH engine Celery tasks — DB + mocked SSH.

Tests the task-layer functions that bridge between DB models and the
SSHEngine. Uses real SQLite DB (from conftest) but mocks SSH connections.

Covers:
- _build_ssh_context() with a real BareMetalHost in DB
- _resolve_jumphost_chain() decrypts credential chain
- build_variables_for_ssh() injects host columns
- _try_auto_register_cluster() creates cluster and links to host
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from core.encryption import encrypt_value
from models import ModuleLibrary, Project, ProjectModule
from models.bare_metal import BareMetalHost
from models.dpu import Dpu
from models.ssh_credential import SSHCredential

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def ssh_credential(db):
    """Create an SSH credential with encrypted fields."""
    cred = SSHCredential(
        name="test-host-cred",
        host="10.176.11.91",
        port=22,
        username="ubuntu",
        auth_type="password",
        password_encrypted=encrypt_value("F5@apcj"),
    )
    db.add(cred)
    db.flush()
    return cred


@pytest.fixture()
def jumphost_credential(db):
    """Create a jumphost SSH credential."""
    cred = SSHCredential(
        name="test-jumphost-cred",
        host="bastion.example.com",
        port=22,
        username="jump_user",
        auth_type="password",
        password_encrypted=encrypt_value("jump_pass"),
    )
    db.add(cred)
    db.flush()
    return cred


@pytest.fixture()
def dpu_credential(db):
    """Create a DPU SSH credential."""
    cred = SSHCredential(
        name="test-dpu-cred",
        host="192.168.100.2",
        port=22,
        username="ubuntu",
        auth_type="password",
        password_encrypted=encrypt_value("dpu_pass"),
    )
    db.add(cred)
    db.flush()
    return cred


@pytest.fixture()
def project(db):
    """Create a bare-metal project."""
    proj = Project(
        name="BM Test Project",
        description="Bare metal test",
        project_type="bare-metal",
        cloud_provider="on-prem",
        environment="dev",
        backend_type="local",
        color="#2563eb",
        icon="server",
        is_active=True,
    )
    db.add(proj)
    db.flush()
    return proj


@pytest.fixture()
def bare_metal_host(db, project, ssh_credential, dpu_credential):
    """Create a BareMetalHost with discovery facts populated."""
    host = BareMetalHost(
        project_id=project.id,
        name="dpu0-sg",
        host_ip="10.176.11.143",
        ssh_credential_id=ssh_credential.id,
        dpu_credential_id=dpu_credential.id,
        ssh_port=22,
        topology="regular",
        nic_mode="SEPARATED_HOST(0)",
        mst_device="/dev/mst/mt41692_pciconf0",
        rshim_present=True,
        default_route_iface="ens4f1",
        vf_count=0,
        dpu_info=[{"index": 0, "mgmt_ip": "192.168.100.2"}],
    )
    db.add(host)
    db.flush()
    return host


@pytest.fixture()
def module_library_probe(db):
    """Create a ModuleLibrary entry for probe-dpu."""
    lib = ModuleLibrary(
        name="probe-dpu",
        category="bare-metal",
        path="bare-metal/probe-dpu",
        provider="bnk-forge",
        description="Probe DPU state",
        git_source="https://github.com/example/modules.git",
        engine_type="ssh",
        is_official=True,
        is_active=True,
    )
    db.add(lib)
    db.flush()
    return lib


@pytest.fixture()
def project_module(db, project, module_library_probe, bare_metal_host):
    """Create a ProjectModule linked to the bare-metal host."""
    mod = ProjectModule(
        project_id=project.id,
        module_library_id=module_library_probe.id,
        path_in_project="bare-metal/probe-dpu",
        status="not_initialized",
        variables={},
        variable_overrides={"bare_metal_host_id": str(bare_metal_host.id)},
        deployment_order=0,
        enabled=True,
    )
    db.add(mod)
    db.flush()
    return mod


# ── _build_ssh_context ────────────────────────────────────────────────


class TestBuildSSHContext:
    """Test _build_ssh_context with real DB objects."""

    def test_build_ssh_context_populates_ssh_fields(
        self, db, project_module, bare_metal_host, ssh_credential,
    ):
        from tasks.ssh_tasks import _build_ssh_context

        # Need to patch the ServiceRegistry for _get_module_def_for_target
        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, project_module)

        assert ctx.ssh_host == "10.176.11.143"
        assert ctx.ssh_port == 22
        assert ctx.ssh_username == "ubuntu"
        assert ctx.module_id == project_module.id
        assert ctx.project_id == project_module.project_id
        assert ctx.path == "bare-metal/probe-dpu"
        assert ctx.category == "bare-metal"

    def test_build_ssh_context_populates_dpu_fields(
        self, db, project_module, bare_metal_host, dpu_credential,
    ):
        from tasks.ssh_tasks import _build_ssh_context

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, project_module)

        assert ctx.dpu_host == "192.168.100.2"
        assert ctx.dpu_username == "ubuntu"

    def test_build_ssh_context_decrypts_password(
        self, db, project_module, bare_metal_host, ssh_credential,
    ):
        from tasks.ssh_tasks import _build_ssh_context

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, project_module)

        assert ctx.ssh_password == "F5@apcj"

    def test_build_ssh_context_threads_operation_to_assembler(
        self, db, project_module, bare_metal_host, ssh_credential,
    ):
        """Regression (ADR-204 blocker 4): destroy must assemble variables in
        destroy-lenient mode. The hardcoded operation="apply" made teardown raise
        on an already-torn-down required dependency, orphaning the release."""
        import tasks.ssh_tasks as st

        captured: dict[str, str] = {}
        real = st.build_variables_for_ssh

        def _capture(*args, **kwargs):
            captured["operation"] = kwargs.get("operation")
            return real(*args, **kwargs)

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None), \
                patch.object(st, "build_variables_for_ssh", _capture):
            st._build_ssh_context(db, project_module)
            assert captured["operation"] == "apply"  # default

            st._build_ssh_context(db, project_module, operation="destroy")
            assert captured["operation"] == "destroy"

    def test_build_ssh_context_raises_without_host_id(
        self, db, project, module_library_probe,
    ):
        from tasks.ssh_tasks import _build_ssh_context

        # Create module without bare_metal_host_id
        mod = ProjectModule(
            project_id=project.id,
            module_library_id=module_library_probe.id,
            path_in_project="bare-metal/probe-dpu",
            status="not_initialized",
            variables={},
            variable_overrides={},
        )
        db.add(mod)
        db.flush()

        with pytest.raises(ValueError, match="no bare_metal_host_id"):
            _build_ssh_context(db, mod)

    def test_build_ssh_context_raises_when_host_not_found(
        self, db, project, module_library_probe,
    ):
        from tasks.ssh_tasks import _build_ssh_context

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=module_library_probe.id,
            path_in_project="bare-metal/probe-dpu",
            status="not_initialized",
            variables={},
            variable_overrides={"bare_metal_host_id": "99999"},
        )
        db.add(mod)
        db.flush()

        with pytest.raises(ValueError, match="not found"):
            _build_ssh_context(db, mod)

    def test_build_ssh_context_raises_without_ssh_credential(
        self, db, project, module_library_probe,
    ):
        from tasks.ssh_tasks import _build_ssh_context

        # Host without SSH credential
        host = BareMetalHost(
            project_id=project.id,
            name="no-cred-host",
            host_ip="10.0.0.1",
            ssh_credential_id=None,
        )
        db.add(host)
        db.flush()

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=module_library_probe.id,
            path_in_project="bare-metal/probe-dpu",
            status="not_initialized",
            variables={},
            variable_overrides={"bare_metal_host_id": str(host.id)},
        )
        db.add(mod)
        db.flush()

        with pytest.raises(ValueError, match="no SSH credential"):
            _build_ssh_context(db, mod)

    def test_build_ssh_context_sets_target_from_module_def(
        self, db, project_module, bare_metal_host,
    ):
        from tasks.ssh_tasks import _build_ssh_context

        mock_mod_def = MagicMock()
        mock_mod_def.target = "dpu"

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=mock_mod_def):
            ctx = _build_ssh_context(db, project_module)

        assert ctx.variables.get("target") == "dpu"

    def test_build_ssh_context_falls_back_to_variables_for_host_id(
        self, db, project, module_library_probe, bare_metal_host,
    ):
        """If bare_metal_host_id not in variable_overrides, check variables."""
        from tasks.ssh_tasks import _build_ssh_context

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=module_library_probe.id,
            path_in_project="bare-metal/probe-dpu",
            status="not_initialized",
            variables={"bare_metal_host_id": str(bare_metal_host.id)},
            variable_overrides={},
        )
        db.add(mod)
        db.flush()

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, mod)

        assert ctx.ssh_host == bare_metal_host.host_ip

    def test_build_ssh_context_uses_dpu_tmfifo_ip_when_dpu_mgmt_ip_absent(
        self, db, project_module, bare_metal_host, project,
    ):
        """ADR-424: when exactly one cluster-member DPU has a persisted tmfifo IP
        and dpu_mgmt_ip is not set, the relay dials that IP (not .2)."""
        from tasks.ssh_tasks import _build_ssh_context

        assert bare_metal_host.dpu_mgmt_ip is None

        from models.kubernetes import KubernetesCluster
        cluster = KubernetesCluster(name="c-tmfifo", context="ctx-tmfifo", project_id=project.id)
        db.add(cluster)
        db.flush()

        dpu = Dpu(
            project_id=project.id,
            access_mode="in-band",
            host_node_ip=bare_metal_host.host_ip,
            pci_address="0000:0d:00",
            kubernetes_cluster_id=cluster.id,
            dpu_tmfifo_ip="192.168.100.6",
        )
        db.add(dpu)
        db.flush()

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, project_module)

        assert ctx.dpu_host == "192.168.100.6"

    def test_build_ssh_context_dpu_mgmt_ip_wins_over_tmfifo_ip(
        self, db, project_module, bare_metal_host, project,
    ):
        """ADR-424: BareMetalHost.dpu_mgmt_ip (OOB) takes precedence over any
        cluster-member DPU tmfifo IP so that dual_dpu_obmc hosts are unaffected."""
        from tasks.ssh_tasks import _build_ssh_context

        bare_metal_host.dpu_mgmt_ip = "10.1.1.50"
        db.flush()

        dpu = Dpu(
            project_id=project.id,
            access_mode="in-band",
            host_node_ip=bare_metal_host.host_ip,
            pci_address="0000:0d:00",
            dpu_tmfifo_ip="192.168.100.6",
        )
        db.add(dpu)
        db.flush()

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, project_module)

        assert ctx.dpu_host == "10.1.1.50"

    def test_build_ssh_context_warns_and_falls_through_for_ambiguous_multi_dpu(
        self, db, project_module, bare_metal_host, project, caplog,
    ):
        """ADR-424 Phase 2 boundary: when two cluster-member DPUs on the same host
        both have tmfifo IPs, the relay cannot unambiguously pick one — it must
        warn and fall back to the legacy dpu_info / 192.168.100.2 path."""
        from tasks.ssh_tasks import _build_ssh_context

        assert bare_metal_host.dpu_mgmt_ip is None
        # bare_metal_host fixture has dpu_info[0].mgmt_ip == "192.168.100.2"

        from models.kubernetes import KubernetesCluster
        cluster = KubernetesCluster(name="c-ambiguous", context="ctx-ambiguous", project_id=project.id)
        db.add(cluster)
        db.flush()

        dpu_a = Dpu(
            project_id=project.id,
            access_mode="in-band",
            host_node_ip=bare_metal_host.host_ip,
            pci_address="0000:0d:00",
            kubernetes_cluster_id=cluster.id,
            dpu_tmfifo_ip="192.168.100.6",
        )
        dpu_b = Dpu(
            project_id=project.id,
            access_mode="in-band",
            host_node_ip=bare_metal_host.host_ip,
            pci_address="0000:0e:00",
            kubernetes_cluster_id=cluster.id,
            dpu_tmfifo_ip="192.168.100.10",
        )
        db.add_all([dpu_a, dpu_b])
        db.flush()

        with caplog.at_level(logging.WARNING, logger="tasks.ssh_tasks"), \
                patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, project_module)

        assert "multi-DPU-per-host relay target selection is not yet supported" in caplog.text
        # Falls through to dpu_info[0].mgmt_ip from the bare_metal_host fixture
        assert ctx.dpu_host == "192.168.100.2"

    def test_build_ssh_context_ignores_dpu_in_other_project(
        self, db, project_module, bare_metal_host, project,
    ):
        """ADR-424 security: a DPU sharing host_node_ip but owned by ANOTHER
        project must not be picked as the relay target (host_node_ip is unique
        only per (project_id, host_node_ip, pci_address))."""
        from models.kubernetes import KubernetesCluster
        from tasks.ssh_tasks import _build_ssh_context

        other = Project(
            name="Other BM Project", description="", project_type="bare-metal",
            cloud_provider="on-prem", environment="dev", backend_type="local",
            color="#000000", icon="server", is_active=True,
        )
        db.add(other)
        db.flush()
        other_cluster = KubernetesCluster(name="c-foreign", context="ctx-foreign", project_id=other.id)
        db.add(other_cluster)
        db.flush()
        foreign = Dpu(
            project_id=other.id,
            access_mode="in-band",
            host_node_ip=bare_metal_host.host_ip,  # same host IP, different project
            pci_address="0000:0d:00",
            kubernetes_cluster_id=other_cluster.id,
            dpu_tmfifo_ip="192.168.104.6",
        )
        db.add(foreign)
        db.flush()

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, project_module)

        # Foreign-project DPU ignored → falls back to dpu_info default.
        assert ctx.dpu_host == "192.168.100.2"

    def test_build_ssh_context_ignores_orphaned_dpu(
        self, db, project_module, bare_metal_host, project,
    ):
        """ADR-424: an orphaned DPU (kubernetes_cluster_id NULL, dpu_tmfifo_ip
        still populated after ondelete=SET NULL) must not be dialed as the relay
        target — only live cluster members qualify."""
        from tasks.ssh_tasks import _build_ssh_context

        orphan = Dpu(
            project_id=project.id,
            access_mode="in-band",
            host_node_ip=bare_metal_host.host_ip,
            pci_address="0000:0d:00",
            kubernetes_cluster_id=None,  # orphan
            dpu_tmfifo_ip="192.168.105.6",
        )
        db.add(orphan)
        db.flush()

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, project_module)

        assert ctx.dpu_host == "192.168.100.2"


# ── _resolve_jumphost_chain ──────────────────────────────────────────


class TestResolveJumphostChain:
    def test_resolve_jumphost_chain_decrypts_credentials(
        self, db, jumphost_credential,
    ):
        from tasks.ssh_tasks import _resolve_jumphost_chain

        chain_spec = [{"ssh_credential_id": jumphost_credential.id}]
        resolved = _resolve_jumphost_chain(db, chain_spec)

        assert len(resolved) == 1
        assert resolved[0]["host"] == "bastion.example.com"
        assert resolved[0]["port"] == 22
        assert resolved[0]["username"] == "jump_user"
        assert resolved[0]["password"] == "jump_pass"

    def test_resolve_jumphost_chain_skips_missing_credentials(self, db):
        from tasks.ssh_tasks import _resolve_jumphost_chain

        chain_spec = [{"ssh_credential_id": 99999}]
        resolved = _resolve_jumphost_chain(db, chain_spec)

        assert len(resolved) == 0

    def test_resolve_jumphost_chain_skips_empty_credential_id(self, db):
        from tasks.ssh_tasks import _resolve_jumphost_chain

        chain_spec = [{}]
        resolved = _resolve_jumphost_chain(db, chain_spec)

        assert len(resolved) == 0

    def test_resolve_jumphost_chain_handles_multi_hop(
        self, db, jumphost_credential, ssh_credential,
    ):
        from tasks.ssh_tasks import _resolve_jumphost_chain

        chain_spec = [
            {"ssh_credential_id": jumphost_credential.id},
            {"ssh_credential_id": ssh_credential.id},
        ]
        resolved = _resolve_jumphost_chain(db, chain_spec)

        assert len(resolved) == 2
        assert resolved[0]["host"] == "bastion.example.com"
        assert resolved[1]["host"] == "10.176.11.91"

    def test_resolve_jumphost_chain_with_jumphost_on_host(
        self, db, project_module, bare_metal_host, jumphost_credential,
    ):
        """Verify the full chain works when BareMetalHost has jumphost_chain set."""
        from tasks.ssh_tasks import _build_ssh_context

        bare_metal_host.jumphost_chain = [{"ssh_credential_id": jumphost_credential.id}]
        db.flush()

        with patch("tasks.ssh_tasks._get_module_def_for_target", return_value=None):
            ctx = _build_ssh_context(db, project_module)

        assert ctx.jumphost_chain is not None
        assert len(ctx.jumphost_chain) == 1
        assert ctx.jumphost_chain[0]["host"] == "bastion.example.com"


# ── build_variables_for_ssh ──────────────────────────────────────────


class TestBuildVariablesForSSH:
    def test_injects_host_columns(
        self, db, project_module, bare_metal_host,
    ):
        from services.execution.variable_assembler import build_variables_for_ssh

        variables = build_variables_for_ssh(db, project_module, host=bare_metal_host)

        assert variables.get("host_ip") == "10.176.11.143"
        assert variables.get("mst_device") == "/dev/mst/mt41692_pciconf0"
        assert variables.get("nic_mode") == "SEPARATED_HOST(0)"
        assert variables.get("rshim_present") is True
        assert variables.get("default_route_iface") == "ens4f1"
        assert variables.get("vf_count") == 0
        assert variables.get("topology") == "regular"
        # bare_metal_host_id may be injected as int (from host.id) or kept as string (from overrides)
        assert int(variables.get("bare_metal_host_id")) == bare_metal_host.id

    def test_user_overrides_take_precedence_for_low_precedence_fields(
        self, db, project_module, bare_metal_host,
    ):
        from services.execution.variable_assembler import build_variables_for_ssh

        # Low-precedence fields (mst_device, nic_mode, etc.) can be overridden by the user.
        # Authoritative fields (host_ip, topology, rshim_source, etc.) always use the host DB value.
        project_module.variable_overrides = {
            "bare_metal_host_id": str(bare_metal_host.id),
            "nic_mode": "EMBEDDED(1)",  # user override of a low-precedence field
        }
        db.flush()

        variables = build_variables_for_ssh(db, project_module, host=bare_metal_host)

        # Low-precedence user override takes precedence over host DB value
        assert variables.get("nic_mode") == "EMBEDDED(1)"
        # Authoritative field: host DB value always wins (cannot be overridden by template)
        assert variables.get("host_ip") == "10.176.11.143"

    def test_resolves_host_from_module_variables_when_not_provided(
        self, db, project_module, bare_metal_host,
    ):
        from services.execution.variable_assembler import build_variables_for_ssh

        # Don't pass host — should resolve from variable_overrides
        variables = build_variables_for_ssh(db, project_module)

        assert variables.get("host_ip") == "10.176.11.143"

    def test_returns_base_variables_without_host(
        self, db, project, module_library_probe,
    ):
        from services.execution.variable_assembler import build_variables_for_ssh

        # Module with no host_id
        mod = ProjectModule(
            project_id=project.id,
            module_library_id=module_library_probe.id,
            path_in_project="bare-metal/probe-dpu",
            status="not_initialized",
            variables={},
            variable_overrides={},
        )
        db.add(mod)
        db.flush()

        variables = build_variables_for_ssh(db, mod)

        # Should still have project-level variables
        assert variables.get("project_name") == "BM Test Project"
        # Should NOT have host-injected variables
        assert "host_ip" not in variables or variables.get("host_ip") is None

    def test_none_host_fields_are_not_injected(
        self, db, project, module_library_probe, ssh_credential,
    ):
        from services.execution.variable_assembler import build_variables_for_ssh

        # Host with only some fields populated
        host = BareMetalHost(
            project_id=project.id,
            name="sparse-host",
            host_ip="10.0.0.1",
            ssh_credential_id=ssh_credential.id,
            # mst_device, nic_mode, etc. are all None
        )
        db.add(host)
        db.flush()

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=module_library_probe.id,
            path_in_project="bare-metal/probe-dpu",
            status="not_initialized",
            variables={},
            variable_overrides={"bare_metal_host_id": str(host.id)},
        )
        db.add(mod)
        db.flush()

        variables = build_variables_for_ssh(db, mod, host=host)

        assert variables.get("host_ip") == "10.0.0.1"
        # None values should not be injected
        assert "mst_device" not in variables
        assert "nic_mode" not in variables

    def test_authoritative_host_fields_override_template_defaults(
        self, db, project, module_library_probe, ssh_credential,
    ):
        """Authoritative host fields (topology, rshim_source, etc.) must override
        any earlier-layer variables such as a stack template's variable_overrides
        containing topology=regular for a host that was detected as dual_dpu_obmc.

        Regression guard for the 'if key not in variables' guard that previously
        applied to topology and other identity fields.
        """
        from services.execution.variable_assembler import build_variables_for_ssh

        host = BareMetalHost(
            project_id=project.id,
            name="mgx-dual-dpu-host",
            host_ip="10.176.11.19",
            ssh_credential_id=ssh_credential.id,
            topology="dual_dpu_obmc",
            rshim_source="bmc",
            deploy_dpu_pci_address="0000:03",
            bmc_ip="192.168.1.10",
        )
        db.add(host)
        db.flush()

        # Simulate a stack template that set topology=regular in variable_overrides
        # (e.g., the blueprint default before auto-detection ran)
        mod = ProjectModule(
            project_id=project.id,
            module_library_id=module_library_probe.id,
            path_in_project="bare-metal/probe-dpu",
            status="not_initialized",
            variables={},
            variable_overrides={
                "bare_metal_host_id": str(host.id),
                "topology": "regular",        # template default — must be overridden
                "host_ip": "0.0.0.0",         # template default — must be overridden
            },
        )
        db.add(mod)
        db.flush()

        variables = build_variables_for_ssh(db, mod, host=host)

        # Authoritative fields: host DB values must win over template defaults
        assert variables["topology"] == "dual_dpu_obmc"
        assert variables["host_ip"] == "10.176.11.19"
        assert variables["rshim_source"] == "bmc"
        assert variables["deploy_dpu_pci_address"] == "0000:03"
        assert variables["bmc_ip"] == "192.168.1.10"


# ── _try_auto_register_cluster ───────────────────────────────────────


class TestTryAutoRegisterCluster:
    def test_try_auto_register_creates_cluster_and_links_host(
        self, db, project_module, bare_metal_host,
    ):
        from models import KubernetesCluster
        from tasks.ssh_tasks import _try_auto_register_cluster

        # Create a real cluster so FK constraint is satisfied
        cluster = KubernetesCluster(
            name="test-bm-cluster",
            context="test-bm-context",
            api_server="https://10.176.11.143:6443",
            status="active",
        )
        db.add(cluster)
        db.flush()

        outputs = {
            "cluster_name": "test-bm-cluster",
            "remote_kubeconfig_path": "/etc/kubernetes/admin.conf",
            "remote_host": "10.176.11.143",
        }

        mock_result = {
            "success": True,
            "cluster_name": "test-bm-cluster",
            "cluster_id": cluster.id,
        }

        with patch("services.cluster_auto_registration_service.matches_cluster_output_contract", return_value=True), \
             patch("services.cluster_auto_registration_service.maybe_auto_register_cluster", return_value=mock_result):
            _try_auto_register_cluster(db, project_module, outputs)

        # Verify host was linked
        db.refresh(bare_metal_host)
        assert bare_metal_host.kubernetes_cluster_id == cluster.id

    def test_try_auto_register_noop_when_no_cluster_outputs(
        self, db, project_module, bare_metal_host,
    ):
        from tasks.ssh_tasks import _try_auto_register_cluster

        # Probe module outputs don't match the cluster contract
        outputs = {"mst_device": "/dev/mst/mt41692_pciconf0"}

        with patch("services.cluster_auto_registration_service.matches_cluster_output_contract", return_value=False) as mock_check, \
             patch("services.cluster_auto_registration_service.maybe_auto_register_cluster") as mock_reg:
            _try_auto_register_cluster(db, project_module, outputs)

        # Host should NOT be linked
        db.refresh(bare_metal_host)
        assert bare_metal_host.kubernetes_cluster_id is None

    def test_try_auto_register_injects_ssh_credential_id(
        self, db, project_module, bare_metal_host, ssh_credential,
    ):
        from models import KubernetesCluster
        from tasks.ssh_tasks import _try_auto_register_cluster

        # Create a real cluster so FK constraint is satisfied
        cluster = KubernetesCluster(
            name="test-cluster",
            context="test-context",
            api_server="https://10.176.11.143:6443",
            status="active",
        )
        db.add(cluster)
        db.flush()

        outputs = {
            "cluster_name": "test-cluster",
            "remote_kubeconfig_path": "/etc/kubernetes/admin.conf",
            "remote_host": "10.176.11.143",
        }

        mock_result = {"success": True, "cluster_name": "test-cluster", "cluster_id": cluster.id}

        with patch("services.cluster_auto_registration_service.matches_cluster_output_contract", return_value=True), \
             patch("services.cluster_auto_registration_service.maybe_auto_register_cluster", return_value=mock_result):
            _try_auto_register_cluster(db, project_module, outputs)

        # ssh_credential_id should have been injected into outputs
        assert outputs.get("ssh_credential_id") == ssh_credential.id

    def test_try_auto_register_handles_failure_gracefully(
        self, db, project_module, bare_metal_host,
    ):
        """Auto-registration failure should NOT raise."""
        from tasks.ssh_tasks import _try_auto_register_cluster

        outputs = {
            "cluster_name": "test-cluster",
            "remote_kubeconfig_path": "/etc/kubernetes/admin.conf",
            "remote_host": "10.176.11.143",
        }

        with patch("services.cluster_auto_registration_service.matches_cluster_output_contract", side_effect=RuntimeError("boom")):
            # Should not raise — auto-registration is non-fatal
            _try_auto_register_cluster(db, project_module, outputs)

        db.refresh(bare_metal_host)
        assert bare_metal_host.kubernetes_cluster_id is None

    def test_try_auto_register_handles_registration_failure(
        self, db, project_module, bare_metal_host,
    ):
        from tasks.ssh_tasks import _try_auto_register_cluster

        outputs = {
            "cluster_name": "test-cluster",
            "remote_kubeconfig_path": "/etc/kubernetes/admin.conf",
            "remote_host": "10.176.11.143",
        }

        mock_result = {"success": False, "error": "kubeconfig fetch failed"}

        with patch("services.cluster_auto_registration_service.matches_cluster_output_contract", return_value=True), \
             patch("services.cluster_auto_registration_service.maybe_auto_register_cluster", return_value=mock_result):
            _try_auto_register_cluster(db, project_module, outputs)

        db.refresh(bare_metal_host)
        assert bare_metal_host.kubernetes_cluster_id is None

    def test_try_auto_register_enqueues_scan_on_success(
        self, db, project_module, bare_metal_host,
    ):
        """Successful SSH auto-registration triggers enqueue_cluster_scan."""
        from models import KubernetesCluster
        from tasks.ssh_tasks import _try_auto_register_cluster

        cluster = KubernetesCluster(
            name="ssh-cluster",
            context="ssh-context",
            api_server="https://10.176.11.143:6443",
            status="active",
        )
        db.add(cluster)
        db.flush()

        outputs = {
            "cluster_name": "ssh-cluster",
            "remote_kubeconfig_path": "/etc/kubernetes/admin.conf",
            "remote_host": "10.176.11.143",
        }

        mock_result = {
            "success": True,
            "cluster_name": "ssh-cluster",
            "cluster_id": cluster.id,
        }

        with patch("services.cluster_auto_registration_service.matches_cluster_output_contract", return_value=True), \
             patch("services.cluster_auto_registration_service.maybe_auto_register_cluster", return_value=mock_result), \
             patch("tasks.cluster_scan_task.scan_cluster_async") as mock_scan_task:
            _try_auto_register_cluster(db, project_module, outputs)

        mock_scan_task.delay.assert_called_once_with(cluster.id)

    def test_try_auto_register_no_scan_enqueue_on_failure(
        self, db, project_module, bare_metal_host,
    ):
        """Failed SSH auto-registration does NOT enqueue a scan."""
        from tasks.ssh_tasks import _try_auto_register_cluster

        outputs = {
            "cluster_name": "ssh-cluster",
            "remote_kubeconfig_path": "/etc/kubernetes/admin.conf",
            "remote_host": "10.176.11.143",
        }

        mock_result = {"success": False, "error": "kubeconfig fetch failed"}

        with patch("services.cluster_auto_registration_service.matches_cluster_output_contract", return_value=True), \
             patch("services.cluster_auto_registration_service.maybe_auto_register_cluster", return_value=mock_result), \
             patch("tasks.cluster_scan_task.scan_cluster_async") as mock_scan_task:
            _try_auto_register_cluster(db, project_module, outputs)

        mock_scan_task.delay.assert_not_called()

    def test_try_auto_register_stamps_cluster_deployable_release_id(
        self, db, project_module, bare_metal_host,
    ):
        """ADR-478 P1b: cluster.deployable_release_id is stamped from host.version_profile_id at link seam."""
        from models import KubernetesCluster
        from models.bnk_deployable_release import BnkDeployableRelease
        from tasks.ssh_tasks import _try_auto_register_cluster

        # Create a deployable release row so the FK is valid
        release = BnkDeployableRelease(
            name="bnk-2.2",
            display_name="BNK 2.2 (GA)",
            description="test",
            is_default=True,
            is_active=True,
            source_type="manual",
            bnk_manifest_version="2.2.1-3.2226.0-0.0.511",
            bnk_cr_kind="CNEInstance",
            flo_version="2.17.2",
            k8s_version="1.29.8",
            doca_version="2.6.0",
            containerd_version="1.7.22",
            runc_version="1.1.14",
            calico_version="3.27.3",
            cert_manager_version="v1.16.1",
            gateway_api_version="v1.1.0",
            multus_version="4.0.2",
            sriov_version="1.5.1",
            storage_class_type="local-path",
            storage_provisioner="rancher.io/local-path",
        )
        db.add(release)
        db.flush()

        # Stamp the host with this release (simulates what deploy_stack does)
        bare_metal_host.version_profile_id = release.id
        db.flush()

        cluster = KubernetesCluster(
            name="adr478-cluster",
            context="adr478-context",
            api_server="https://10.176.11.143:6443",
            status="active",
        )
        db.add(cluster)
        db.flush()

        outputs = {
            "cluster_name": "adr478-cluster",
            "remote_kubeconfig_path": "/etc/kubernetes/admin.conf",
            "remote_host": "10.176.11.143",
        }
        mock_result = {"success": True, "cluster_name": "adr478-cluster", "cluster_id": cluster.id}

        with patch("services.cluster_auto_registration_service.matches_cluster_output_contract", return_value=True), \
             patch("services.cluster_auto_registration_service.maybe_auto_register_cluster", return_value=mock_result), \
             patch("tasks.cluster_scan_task.enqueue_cluster_scan"):
            _try_auto_register_cluster(db, project_module, outputs)

        db.refresh(cluster)
        assert cluster.deployable_release_id == release.id, (
            "Cluster should record the deployable_release_id from the host's version_profile_id"
        )

    def test_try_auto_register_skips_cluster_stamp_when_no_host_release(
        self, db, project_module, bare_metal_host,
    ):
        """ADR-478 P1b: cluster.deployable_release_id stays NULL when host has no version_profile_id."""
        from models import KubernetesCluster
        from tasks.ssh_tasks import _try_auto_register_cluster

        # host.version_profile_id is None (default in fixture)
        assert bare_metal_host.version_profile_id is None

        cluster = KubernetesCluster(
            name="adr478-no-release-cluster",
            context="adr478-no-release-context",
            api_server="https://10.176.11.143:6443",
            status="active",
        )
        db.add(cluster)
        db.flush()

        outputs = {
            "cluster_name": "adr478-no-release-cluster",
            "remote_kubeconfig_path": "/etc/kubernetes/admin.conf",
            "remote_host": "10.176.11.143",
        }
        mock_result = {"success": True, "cluster_name": "adr478-no-release-cluster", "cluster_id": cluster.id}

        with patch("services.cluster_auto_registration_service.matches_cluster_output_contract", return_value=True), \
             patch("services.cluster_auto_registration_service.maybe_auto_register_cluster", return_value=mock_result), \
             patch("tasks.cluster_scan_task.enqueue_cluster_scan"):
            _try_auto_register_cluster(db, project_module, outputs)

        db.refresh(cluster)
        assert cluster.deployable_release_id is None
