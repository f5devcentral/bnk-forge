"""
Unit tests for bare-metal SSH module definitions.

Tests all 18 SSHModule subclasses for:
- Instantiation and class attributes
- apply_commands() returns non-empty lists with variable substitution
- parse_apply_output() extracts expected keys from sample output
- Dependency declarations form a valid DAG
- module_type == "ssh" and category == "bare-metal"

No DB, no SSH — pure Python tests on the module class definitions.
"""

import pytest

from modules.bare_metal.flash_dpu import FlashDPUModule
from modules.bare_metal.install_cni import InstallCNIModule
from modules.bare_metal.install_dpu_prereqs import InstallDPUPrereqsModule
from modules.bare_metal.install_gateway_api import InstallGatewayAPIModule
from modules.bare_metal.install_k8s_prereqs import InstallK8sPrereqsModule
from modules.bare_metal.install_multus import InstallMultusModule
from modules.bare_metal.install_sriov import InstallSRIOVModule
from modules.bare_metal.install_storage import InstallStorageModule
from modules.bare_metal.kubeadm_init import KubeadmInitModule
from modules.bare_metal.kubeadm_join import KubeadmJoinModule
from modules.bare_metal.label_dpu_node import LabelDPUNodeModule
from modules.bare_metal.probe_dpu import ProbeDPUModule
from modules.bare_metal.reboot_host import RebootHostModule
from modules.bare_metal.set_nic_mode import SetNicModeModule
from modules.bare_metal.setup_dpu_networking import SetupDPUNetworkingModule
from modules.bare_metal.taint_dpu_node import TaintDPUNodeModule
from modules.bare_metal.validate_dpu_ready import ValidateDPUReadyModule
from modules.bare_metal.wait_dpu_ready import WaitDPUReadyModule
from modules.base import SSHModule

# All 18 modules in dependency order
ALL_MODULE_CLASSES = [
    ProbeDPUModule,
    SetNicModeModule,
    RebootHostModule,
    FlashDPUModule,
    WaitDPUReadyModule,
    SetupDPUNetworkingModule,
    InstallDPUPrereqsModule,
    InstallK8sPrereqsModule,
    KubeadmInitModule,
    KubeadmJoinModule,
    InstallCNIModule,
    InstallMultusModule,
    InstallGatewayAPIModule,
    InstallSRIOVModule,
    InstallStorageModule,
    LabelDPUNodeModule,
    TaintDPUNodeModule,
    ValidateDPUReadyModule,
]

# Map path → class for dependency resolution
ALL_PATHS = {cls.path for cls in ALL_MODULE_CLASSES}


def _make_instance(cls):
    """Instantiate module (no args needed — all config is class-level)."""
    return cls()


# Minimal variables that satisfy all module inputs
SAMPLE_VARIABLES = {
    "bare_metal_host_id": 1,
    "host_ip": "10.176.11.91",
    "bfb_url": "https://example.com/bfb.bfb",
    "bfb_config_url": "https://example.com/bf.cfg",
    "mst_device": "/dev/mst/mt41692_pciconf0",
    "target_nic_mode": "1",
    "dpu_ip": "192.168.100.2",
    "max_wait_seconds": 300,
    "containerd_version": "1.7.22",
    "runc_version": "1.1.14",
    "k8s_version": "1.30.2",
    "cluster_name": "test-cluster",
    "pod_cidr": "10.244.0.0/16",
    "service_cidr": "10.96.0.0/12",
    "join_command": "kubeadm join 10.176.11.91:6443 --token abc.123 --discovery-token-ca-cert-hash sha256:abc",
    "calico_version": "3.28.0",
    "sriov_dp_version": "v3.9.0",
    "storage_class_name": "local-path",
    "set_default": True,
    "default_route_iface": "ens4f1",
    "node_name": "bf3-dpu-0",
    "dpu_node_name": "bf3-dpu-0",
    "tmm_label": "app=f5-tmm",
    "taint_key": "f5-tmm",
    "taint_value": "true",
    "taint_effect": "NoSchedule",
    "hugepages_minimum": "8192",
}


# ── Class attributes ──────────────────────────────────────────────────


class TestSSHModuleClassAttributes:
    """Verify every module has correct class-level metadata."""

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_module_type_is_ssh(self, cls):
        mod = _make_instance(cls)
        assert mod.module_type == "ssh", f"{cls.__name__}.module_type should be 'ssh'"

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_category_is_bare_metal(self, cls):
        mod = _make_instance(cls)
        assert mod.category == "bare-metal", f"{cls.__name__}.category should be 'bare-metal'"

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_is_subclass_of_ssh_module(self, cls):
        assert issubclass(cls, SSHModule)

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_path_starts_with_bare_metal(self, cls):
        assert cls.path.startswith("bare-metal/"), f"{cls.__name__}.path should start with 'bare-metal/'"

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_has_nonempty_name(self, cls):
        assert cls.name, f"{cls.__name__}.name should not be empty"

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_has_nonempty_description(self, cls):
        assert cls.description, f"{cls.__name__}.description should not be empty"

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_version_is_set(self, cls):
        # Most modules are 1.0.0; probe-dpu and install-sriov are 2.0.0 after rewrites
        expected = {"bare-metal/probe-dpu": "2.0.0", "bare-metal/install-sriov": "2.0.0"}
        assert cls.version == expected.get(cls.path, "1.0.0")

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_timeout_is_positive(self, cls):
        assert cls.timeout > 0

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_estimated_duration_is_positive(self, cls):
        assert cls.estimated_duration > 0

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_target_is_host_or_dpu(self, cls):
        assert cls.target in ("host", "dpu"), f"{cls.__name__}.target must be 'host' or 'dpu'"


# ── Instantiation ─────────────────────────────────────────────────────


class TestSSHModuleInstantiation:
    """Every module can be instantiated without errors."""

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_instantiation_succeeds(self, cls):
        mod = _make_instance(cls)
        assert mod is not None
        assert mod.path == cls.path


# ── apply_commands() ──────────────────────────────────────────────────


class TestApplyCommands:
    """apply_commands() returns non-empty list with proper variable substitution."""

    # Modules that override execute() (Python path) intentionally return [] from
    # apply_commands() — the shell command list is never used.
    # ProbeDPUModule has both paths (apply_commands + execute) so is NOT excluded.
    _PYTHON_PATH_MODULES = {
        WaitDPUReadyModule, KubeadmInitModule, KubeadmJoinModule, FlashDPUModule,
        RebootHostModule, InstallDPUPrereqsModule, InstallK8sPrereqsModule,
        InstallCNIModule, InstallMultusModule, InstallGatewayAPIModule,
        InstallSRIOVModule, InstallStorageModule, SetupDPUNetworkingModule,
        SetNicModeModule, LabelDPUNodeModule, TaintDPUNodeModule,
        ValidateDPUReadyModule,
    }

    @pytest.mark.parametrize(
        "cls",
        [c for c in ALL_MODULE_CLASSES if c not in {
            WaitDPUReadyModule, KubeadmInitModule, KubeadmJoinModule, FlashDPUModule,
            RebootHostModule, InstallDPUPrereqsModule, InstallK8sPrereqsModule,
            InstallCNIModule, InstallMultusModule, InstallGatewayAPIModule,
            InstallSRIOVModule, InstallStorageModule, SetupDPUNetworkingModule,
            SetNicModeModule, LabelDPUNodeModule, TaintDPUNodeModule,
            ValidateDPUReadyModule,
        }],
        ids=lambda c: c.path,
    )
    def test_apply_commands_returns_nonempty_list(self, cls):
        mod = _make_instance(cls)
        cmds = mod.apply_commands(SAMPLE_VARIABLES)
        assert isinstance(cmds, list), f"{cls.__name__}.apply_commands() should return a list"
        assert len(cmds) > 0, f"{cls.__name__}.apply_commands() should return at least one command"

    def test_wait_dpu_ready_uses_python_execute_path(self):
        """WaitDPUReadyModule overrides execute() — apply_commands() returns [] by design."""
        mod = WaitDPUReadyModule()
        # apply_commands() is a stub; Python path is used instead
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        # Verify the execute() override exists (not inherited from SSHModule base)
        assert type(mod).execute is not SSHModule.execute, (
            "WaitDPUReadyModule must override execute() to use the Python path"
        )

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_apply_commands_are_strings(self, cls):
        mod = _make_instance(cls)
        cmds = mod.apply_commands(SAMPLE_VARIABLES)
        for i, cmd in enumerate(cmds):
            assert isinstance(cmd, str), f"{cls.__name__}.apply_commands()[{i}] should be str, got {type(cmd)}"

    def test_probe_dpu_apply_commands_returns_eight_commands(self):
        mod = ProbeDPUModule()
        cmds = mod.apply_commands(SAMPLE_VARIABLES)
        assert len(cmds) == 8

    def test_set_nic_mode_uses_python_execute_path(self):
        """SetNicModeModule overrides execute() — apply_commands() returns [] by design."""
        mod = SetNicModeModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "SetNicModeModule must override execute() to use the Python path"
        )

    def test_flash_dpu_uses_python_execute_path(self):
        """FlashDPUModule overrides execute() — apply_commands() returns [] by design."""
        mod = FlashDPUModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "FlashDPUModule must override execute() to use the Python path"
        )

    def test_kubeadm_init_uses_python_execute_path(self):
        """KubeadmInitModule overrides execute() — apply_commands() returns [] by design."""
        mod = KubeadmInitModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "KubeadmInitModule must override execute() to use the Python path"
        )

    def test_kubeadm_join_uses_python_execute_path(self):
        """KubeadmJoinModule overrides execute() — apply_commands() returns [] by design."""
        mod = KubeadmJoinModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "KubeadmJoinModule must override execute() to use the Python path"
        )

    def test_install_dpu_prereqs_uses_python_execute_path(self):
        """InstallDPUPrereqsModule overrides execute() — apply_commands() returns [] by design."""
        mod = InstallDPUPrereqsModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "InstallDPUPrereqsModule must override execute() to use the Python path"
        )

    def test_wait_dpu_ready_execute_polls_dpu_and_returns_outputs(self):
        """execute() polls DPU SSH and returns dpu_ssh_ready + dpu_hostname + dpu_kernel."""
        from unittest.mock import MagicMock, patch

        mod = WaitDPUReadyModule()

        # Fake SSHResult returned by the DPU session on first attempt
        fake_dpu_result = MagicMock()
        fake_dpu_result.exit_code = 0
        fake_dpu_result.stdout = "DPU_READY\nbf3-dpu-0\n5.15.0-91-generic\n"

        # Fake DPU SSHSession: execute() succeeds immediately
        fake_dpu_session = MagicMock()
        fake_dpu_session.execute.return_value = fake_dpu_result

        # Fake host session (target="host") — supplies jumphost chain attributes
        fake_host_session = MagicMock()
        fake_host_session.host = "10.176.11.91"
        fake_host_session.port = 22
        fake_host_session.username = "ubuntu"
        fake_host_session.password = "host-secret"
        fake_host_session.private_key_content = None
        fake_host_session.jumphost_chain = []

        variables = dict(SAMPLE_VARIABLES)
        variables["dpu_username"] = "ubuntu"
        variables["dpu_password"] = "dpu-secret"
        variables["max_wait_seconds"] = 10  # short timeout — succeeds on first attempt

        logs: list[str] = []

        # execute() does `from services.bare_metal.ssh_session import SSHSession` internally;
        # patch at that exact import point so SSHSession(...) returns our fake.
        with patch(
            "services.bare_metal.ssh_session.SSHSession",
            return_value=fake_dpu_session,
        ):
            result = mod.execute(fake_host_session, variables, logs.append)

        assert result["dpu_ssh_ready"] is True
        assert result["dpu_hostname"] == "bf3-dpu-0"
        assert result["dpu_kernel"] == "5.15.0-91-generic"
        assert any("DPU ready" in line for line in logs)

    def test_taint_dpu_node_uses_python_execute_path(self):
        """TaintDPUNodeModule overrides execute() — apply_commands() returns [] by design."""
        mod = TaintDPUNodeModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "TaintDPUNodeModule must override execute() to use the Python path"
        )

    def test_label_dpu_node_uses_python_execute_path(self):
        """LabelDPUNodeModule overrides execute() — apply_commands() returns [] by design."""
        mod = LabelDPUNodeModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "LabelDPUNodeModule must override execute() to use the Python path"
        )

    def test_install_k8s_prereqs_uses_python_execute_path(self):
        """InstallK8sPrereqsModule overrides execute() — apply_commands() returns [] by design."""
        mod = InstallK8sPrereqsModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "InstallK8sPrereqsModule must override execute() to use the Python path"
        )

    def test_install_cni_uses_python_execute_path(self):
        """InstallCNIModule overrides execute() — apply_commands() returns [] by design."""
        mod = InstallCNIModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "InstallCNIModule must override execute() to use the Python path"
        )

    def test_install_multus_uses_python_execute_path(self):
        """InstallMultusModule overrides execute() — apply_commands() returns [] by design."""
        mod = InstallMultusModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "InstallMultusModule must override execute() to use the Python path"
        )

    def test_install_gateway_api_uses_python_execute_path(self):
        """InstallGatewayAPIModule overrides execute() — apply_commands() returns [] by design."""
        mod = InstallGatewayAPIModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "InstallGatewayAPIModule must override execute() to use the Python path"
        )

    def test_install_sriov_uses_python_execute_path(self):
        """InstallSRIOVModule overrides execute() — apply_commands() returns [] by design."""
        mod = InstallSRIOVModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "InstallSRIOVModule must override execute() to use the Python path"
        )

    def test_install_storage_uses_python_execute_path(self):
        """InstallStorageModule overrides execute() — apply_commands() returns [] by design."""
        mod = InstallStorageModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "InstallStorageModule must override execute() to use the Python path"
        )

    def test_setup_dpu_networking_uses_python_execute_path(self):
        """SetupDPUNetworkingModule overrides execute() — apply_commands() returns [] by design."""
        mod = SetupDPUNetworkingModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "SetupDPUNetworkingModule must override execute() to use the Python path"
        )

    def test_validate_dpu_ready_uses_python_execute_path(self):
        """ValidateDPUReadyModule overrides execute() — apply_commands() returns [] by design."""
        mod = ValidateDPUReadyModule()
        assert mod.apply_commands(SAMPLE_VARIABLES) == []
        assert type(mod).execute is not SSHModule.execute, (
            "ValidateDPUReadyModule must override execute() to use the Python path"
        )

    def test_validate_dpu_ready_parse_apply_output_is_empty_stub(self):
        mod = ValidateDPUReadyModule()
        assert mod.parse_apply_output("validation done", SAMPLE_VARIABLES) == {}

    def test_validate_dpu_ready_depends_on_install_dpu_prereqs(self):
        assert "bare-metal/install-dpu-prereqs" in ValidateDPUReadyModule.dependencies

    def test_validate_dpu_ready_target_is_host(self):
        assert ValidateDPUReadyModule.target == "host"


# ── parse_apply_output() ──────────────────────────────────────────────


class TestParseApplyOutput:
    """parse_apply_output() extracts expected keys from sample output."""

    def test_probe_dpu_parse_output_extracts_mst_device(self):
        """parse_apply_output() uses PROBE_ markers emitted by apply_commands()."""
        mod = ProbeDPUModule()
        output = (
            "PROBE_MST_DEVICE=/dev/mst/mt41692_pciconf0\n"
            "PROBE_NIC_MODE=DPU\n"
            "PROBE_NIC_MODE_RAW=INTERNAL_CPU_MODEL EMBEDDED_CPU(1)\n"
            "PROBE_RSHIM=true\n"
            "PROBE_DPU_SSH=true\n"
            "PROBE_DOCA_VERSION=5.8-2\n"
            "PROBE_VF_COUNT=8\n"
            "PROBE_DEFAULT_ROUTE=ens4f1\n"
        )
        result = mod.parse_apply_output(output, SAMPLE_VARIABLES)
        assert result["mst_device"] == "/dev/mst/mt41692_pciconf0"
        assert result["nic_mode"] == "dpu"
        assert result["rshim_present"] is True
        assert result["vf_count"] == 8
        assert result["default_route_iface"] == "ens4f1"

    def test_probe_dpu_parse_output_handles_supernic_mode(self):
        """NIC mode (0) maps to 'nic' in the new PROBE_ marker format."""
        mod = ProbeDPUModule()
        output = "PROBE_NIC_MODE=NIC\n"
        result = mod.parse_apply_output(output, SAMPLE_VARIABLES)
        assert result["nic_mode"] == "nic"

    def test_probe_dpu_parse_output_handles_dpu_unreachable(self):
        mod = ProbeDPUModule()
        output = "PROBE_DPU_SSH=false\n"
        result = mod.parse_apply_output(output, SAMPLE_VARIABLES)
        assert result.get("doca_version") is None
        assert result["dpu_ssh_reachable"] is False

    def test_flash_dpu_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — FlashDPUModule uses execute() path."""
        mod = FlashDPUModule()
        result = mod.parse_apply_output("BFB flash complete", SAMPLE_VARIABLES)
        assert result == {}

    def test_set_nic_mode_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — SetNicModeModule uses execute() path."""
        mod = SetNicModeModule()
        result = mod.parse_apply_output("NIC mode configured", SAMPLE_VARIABLES)
        assert result == {}

    def test_wait_dpu_ready_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — WaitDPUReadyModule uses execute() path."""
        mod = WaitDPUReadyModule()
        # execute() returns outputs directly; parse_apply_output is never called
        result = mod.parse_apply_output("DPU_READY\nbf3-dpu-0\n5.15.0-91-generic\n", SAMPLE_VARIABLES)
        assert result == {}

    def test_install_dpu_prereqs_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — InstallDPUPrereqsModule uses execute() path."""
        mod = InstallDPUPrereqsModule()
        result = mod.parse_apply_output("installed ok", SAMPLE_VARIABLES)
        assert result == {}

    def test_install_k8s_prereqs_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — InstallK8sPrereqsModule uses execute() path."""
        mod = InstallK8sPrereqsModule()
        result = mod.parse_apply_output("installed ok", SAMPLE_VARIABLES)
        assert result == {}

    def test_kubeadm_init_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — KubeadmInitModule uses execute() path."""
        mod = KubeadmInitModule()
        output = "kubeadm join 10.176.11.91:6443 --token abc.123\nCONTROL_PLANE_ENDPOINT=10.176.11.91:6443\n"
        result = mod.parse_apply_output(output, SAMPLE_VARIABLES)
        assert result == {}

    def test_kubeadm_join_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — KubeadmJoinModule uses execute() path."""
        mod = KubeadmJoinModule()
        output = "Joining cluster...\nNODE_NAME=bf3-dpu-0\nDPU successfully joined\n"
        result = mod.parse_apply_output(output, SAMPLE_VARIABLES)
        assert result == {}

    def test_install_cni_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — InstallCNIModule uses execute() path."""
        mod = InstallCNIModule()
        result = mod.parse_apply_output("calico installed", SAMPLE_VARIABLES)
        assert result == {}

    def test_install_multus_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — InstallMultusModule uses execute() path."""
        mod = InstallMultusModule()
        result = mod.parse_apply_output("Multus installed", SAMPLE_VARIABLES)
        assert result == {}

    def test_install_gateway_api_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — InstallGatewayAPIModule uses execute() path."""
        mod = InstallGatewayAPIModule()
        result = mod.parse_apply_output("Gateway API installed", SAMPLE_VARIABLES)
        assert result == {}

    def test_install_sriov_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — InstallSRIOVModule uses execute() path."""
        mod = InstallSRIOVModule()
        result = mod.parse_apply_output("SR-IOV installed", SAMPLE_VARIABLES)
        assert result == {}

    def test_install_storage_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — InstallStorageModule uses execute() path."""
        mod = InstallStorageModule()
        result = mod.parse_apply_output("storage installed", SAMPLE_VARIABLES)
        assert result == {}

    def test_setup_dpu_networking_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — SetupDPUNetworkingModule uses execute() path."""
        mod = SetupDPUNetworkingModule()
        result = mod.parse_apply_output("networking configured", SAMPLE_VARIABLES)
        assert result == {}

    def test_label_dpu_node_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — LabelDPUNodeModule uses execute() path."""
        mod = LabelDPUNodeModule()
        result = mod.parse_apply_output("Labeling node: bf3-dpu-0\nDPU_NODE_NAME=bf3-dpu-0\n", SAMPLE_VARIABLES)
        assert result == {}

    def test_taint_dpu_node_parse_apply_output_is_empty_stub(self):
        """parse_apply_output() is a stub — TaintDPUNodeModule uses execute() path."""
        mod = TaintDPUNodeModule()
        result = mod.parse_apply_output("node tainted", SAMPLE_VARIABLES)
        assert result == {}


# ── validate_commands() ───────────────────────────────────────────────


class TestValidateCommands:
    """validate_commands() returns a list (possibly empty)."""

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_validate_commands_returns_list(self, cls):
        mod = _make_instance(cls)
        cmds = mod.validate_commands(SAMPLE_VARIABLES)
        assert isinstance(cmds, list)


# ── plan_commands() ───────────────────────────────────────────────────


class TestPlanCommands:
    """plan_commands() returns a list (empty means 'always apply')."""

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_plan_commands_returns_list(self, cls):
        mod = _make_instance(cls)
        cmds = mod.plan_commands(SAMPLE_VARIABLES)
        assert isinstance(cmds, list)


# ── Connectivity risk ─────────────────────────────────────────────────


class TestConnectivityRisk:
    """Modules with connectivity_risk=True must provide pre_stage_commands."""

    def test_flash_dpu_has_no_connectivity_risk(self):
        """connectivity_risk=False: bfb-install runs on the host; SSH stays up throughout."""
        assert FlashDPUModule.connectivity_risk is False

    def test_flash_dpu_has_empty_pre_stage_commands(self):
        """No pre-stage needed since connectivity_risk=False."""
        mod = FlashDPUModule()
        cmds = mod.pre_stage_commands(SAMPLE_VARIABLES)
        assert cmds == []

    def test_flash_dpu_has_reconnect_timeout(self):
        assert FlashDPUModule.reconnect_timeout >= 300

    def test_probe_dpu_has_no_connectivity_risk(self):
        assert ProbeDPUModule.connectivity_risk is False

    def test_set_nic_mode_has_no_connectivity_risk(self):
        assert SetNicModeModule.connectivity_risk is False

    @pytest.mark.parametrize(
        "cls",
        [c for c in ALL_MODULE_CLASSES if not c.connectivity_risk],
        ids=lambda c: c.path,
    )
    def test_non_risk_modules_return_empty_pre_stage(self, cls):
        mod = _make_instance(cls)
        cmds = mod.pre_stage_commands(SAMPLE_VARIABLES)
        assert cmds == [], f"{cls.__name__} should have empty pre_stage_commands"


# ── Dependency DAG validation ─────────────────────────────────────────


class TestDependencyDAG:
    """Dependencies form a valid DAG (no cycles, all deps exist)."""

    def test_all_dependency_paths_exist(self):
        for cls in ALL_MODULE_CLASSES:
            for dep in cls.dependencies:
                assert dep in ALL_PATHS, (
                    f"{cls.__name__} depends on '{dep}' which is not a known module path. "
                    f"Known paths: {ALL_PATHS}"
                )

    def test_no_self_dependencies(self):
        for cls in ALL_MODULE_CLASSES:
            assert cls.path not in cls.dependencies, (
                f"{cls.__name__} depends on itself"
            )

    def test_no_cycles(self):
        """Use topological sort to verify no cycles exist."""
        # Build adjacency list
        graph: dict[str, set[str]] = {cls.path: set(cls.dependencies) for cls in ALL_MODULE_CLASSES}

        visited: set[str] = set()
        in_stack: set[str] = set()

        def _dfs(node: str) -> bool:
            """Return True if a cycle is detected."""
            visited.add(node)
            in_stack.add(node)
            for dep in graph.get(node, set()):
                if dep not in visited:
                    if _dfs(dep):
                        return True
                elif dep in in_stack:
                    return True
            in_stack.discard(node)
            return False

        for path in graph:
            if path not in visited:
                assert not _dfs(path), f"Cycle detected involving '{path}'"

    def test_probe_dpu_has_no_dependencies(self):
        assert ProbeDPUModule.dependencies == []

    def test_flash_dpu_depends_on_reboot_host(self):
        """flash-dpu runs after reboot-host (which itself depends on set-nic-mode)."""
        assert "bare-metal/reboot-host" in FlashDPUModule.dependencies

    def test_kubeadm_init_depends_on_k8s_prereqs(self):
        assert "bare-metal/install-k8s-prereqs" in KubeadmInitModule.dependencies

    def test_install_k8s_prereqs_depends_on_dpu_prereqs(self):
        """install-k8s-prereqs must run after DPU prereqs are installed."""
        assert InstallK8sPrereqsModule.dependencies == ["bare-metal/install-dpu-prereqs"]

    def test_kubeadm_join_depends_only_on_install_storage(self):
        """kubeadm-join waits for install-storage (last in the strict sequential chain).

        The previous multi-dep list allowed parallel execution after install-cni.
        The new single dep ensures bare-metal modules run strictly in order:
        install-cni → install-multus → install-gateway-api → install-sriov
        → install-storage → kubeadm-join.
        """
        deps = KubeadmJoinModule.dependencies
        assert deps == ["bare-metal/install-storage"], (
            f"kubeadm-join should depend only on install-storage for strict sequencing, got {deps}"
        )

    def test_taint_depends_on_label(self):
        assert "bare-metal/label-dpu-node" in TaintDPUNodeModule.dependencies


# ── Target (host vs dpu) ─────────────────────────────────────────────


class TestModuleTarget:
    """Modules targeting DPU run via relay; host modules run directly."""

    def test_dpu_targeted_modules(self):
        dpu_modules = [cls for cls in ALL_MODULE_CLASSES if cls.target == "dpu"]
        dpu_paths = {cls.path for cls in dpu_modules}
        assert "bare-metal/install-dpu-prereqs" in dpu_paths
        assert "bare-metal/kubeadm-join" in dpu_paths

    def test_host_targeted_modules(self):
        host_modules = [cls for cls in ALL_MODULE_CLASSES if cls.target == "host"]
        host_paths = {cls.path for cls in host_modules}
        assert "bare-metal/probe-dpu" in host_paths
        assert "bare-metal/kubeadm-init" in host_paths
        assert "bare-metal/label-dpu-node" in host_paths
        assert "bare-metal/taint-dpu-node" in host_paths


# ── Prereq commands ───────────────────────────────────────────────────


class TestPrereqCommands:
    """Modules with prereq_commands should return non-empty lists."""

    def test_install_dpu_prereqs_has_prereq_commands(self):
        mod = InstallDPUPrereqsModule()
        cmds = mod.prereq_commands(SAMPLE_VARIABLES)
        assert len(cmds) > 0

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_prereq_commands_returns_list(self, cls):
        mod = _make_instance(cls)
        cmds = mod.prereq_commands(SAMPLE_VARIABLES)
        assert isinstance(cmds, list)


# ── render_manifests() returns empty ──────────────────────────────────


class TestRenderManifests:
    """SSH modules do not render K8s manifests."""

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_render_manifests_returns_empty_list(self, cls):
        mod = _make_instance(cls)
        manifests = mod.render_manifests(SAMPLE_VARIABLES)
        assert manifests == []


# ── validate_inputs() ─────────────────────────────────────────────────


class TestValidateInputs:
    """SSHModule.validate_inputs() enforces required inputs before execution."""

    def test_flash_dpu_rejects_missing_bfb_url(self):
        mod = FlashDPUModule()
        errors = mod.validate_inputs({})
        assert any("bfb_url" in e for e in errors), "Should flag missing bfb_url"

    def test_flash_dpu_rejects_empty_bfb_url(self):
        mod = FlashDPUModule()
        errors = mod.validate_inputs({"bfb_url": ""})
        assert any("bfb_url" in e for e in errors), "Should flag empty bfb_url"

    def test_flash_dpu_rejects_whitespace_bfb_url(self):
        mod = FlashDPUModule()
        errors = mod.validate_inputs({"bfb_url": "   "})
        assert any("bfb_url" in e for e in errors), "Should flag whitespace-only bfb_url"

    def test_flash_dpu_accepts_valid_bfb_url(self):
        mod = FlashDPUModule()
        errors = mod.validate_inputs({"bfb_url": "https://content.mellanox.com/BlueField/BFBs/test.bfb"})
        assert not any("bfb_url" in e for e in errors), "Should accept valid bfb_url"

    def test_probe_dpu_has_no_user_required_inputs(self):
        """Probe has no user-sourced required inputs — host_id is source='host'."""
        mod = ProbeDPUModule()
        errors = mod.validate_inputs({})
        assert errors == [], f"Probe should have no required user inputs, got: {errors}"

    def test_skips_host_sourced_inputs(self):
        """bare_metal_host_id (source='host') is injected by engine, never user-provided."""
        mod = FlashDPUModule()
        # Only bfb_url should be flagged, not bare_metal_host_id
        errors = mod.validate_inputs({})
        assert len(errors) == 1, f"Expected 1 error (bfb_url), got {len(errors)}: {errors}"
        assert "bfb_url" in errors[0]

    def test_skips_inputs_with_defaults(self):
        """Inputs with non-empty defaults should not be flagged when missing."""
        mod = SetNicModeModule()
        # target_nic_mode has default="1" — should not be required
        errors = mod.validate_inputs({})
        assert not any("target_nic_mode" in e for e in errors)

    @pytest.mark.parametrize("cls", ALL_MODULE_CLASSES, ids=lambda c: c.path)
    def test_all_modules_pass_with_sample_variables(self, cls):
        """All modules should validate cleanly with the standard sample variables."""
        mod = _make_instance(cls)
        errors = mod.validate_inputs(SAMPLE_VARIABLES)
        assert errors == [], f"{cls.path} failed validation with sample vars: {errors}"


# ── bf.cfg shellcheck validation ──────────────────────────────────────


class TestFlashDPUShellcheck:
    """Validate the generated bf.cfg script with shellcheck and bash -n."""

    def test_bf_cfg_shellcheck(self, tmp_path):
        """Generated bf.cfg must pass shellcheck (errors only — warnings OK)."""
        import shutil
        import subprocess

        shellcheck = shutil.which("shellcheck")
        if shellcheck is None:
            pytest.skip("shellcheck not installed")

        content = FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$test$fakehash",
            dpu_hostname="test-dpu",
            dpu_password="testpassword",
        )

        bf_cfg = tmp_path / "bf.cfg"
        bf_cfg.write_text(content)

        result = subprocess.run(
            [shellcheck, "-S", "error", str(bf_cfg)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"shellcheck found errors in generated bf.cfg:\n{result.stdout}\n{result.stderr}"
        )

    def test_bf_cfg_valid_bash_syntax(self, tmp_path):
        """Generated bf.cfg must have valid bash syntax (bash -n)."""
        import subprocess

        content = FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$test$fakehash",
            dpu_hostname="test-dpu",
            dpu_password="testpassword",
        )

        bf_cfg = tmp_path / "bf.cfg"
        bf_cfg.write_text(content)

        result = subprocess.run(
            ["bash", "-n", str(bf_cfg)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"bash syntax error in generated bf.cfg:\n{result.stdout}\n{result.stderr}"
        )

    def test_bf_cfg_cloud_init_valid_yaml(self, tmp_path):
        """Cloud-init user-data embedded in bf.cfg must be valid YAML."""
        import yaml

        content = FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$test$fakehash",
            dpu_hostname="test-dpu",
            dpu_password="testpassword",
        )

        # Extract the cloud-init section between #cloud-config and EOFCLOUDINIT
        lines = content.split("\n")
        in_cloudinit = False
        cloudinit_lines: list[str] = []
        for line in lines:
            if line.strip() == "#cloud-config":
                in_cloudinit = True
                cloudinit_lines.append(line)
                continue
            if in_cloudinit:
                if line.strip() == "EOFCLOUDINIT":
                    break
                cloudinit_lines.append(line)

        assert cloudinit_lines, "No cloud-init section found in bf.cfg"
        cloudinit_text = "\n".join(cloudinit_lines)

        parsed = yaml.safe_load(cloudinit_text)
        assert parsed is not None, "Cloud-init YAML parsed as empty"
        assert "write_files" in parsed, "Missing write_files in cloud-init"
        assert "chpasswd" in parsed, "Missing chpasswd in cloud-init"


# ── bf.cfg poc-deployer parity checks ────────────────────────────────


class TestBfCfgPocParityChecks:
    """BM2-001 through BM2-004: bf.cfg poc-deployer parity checks."""

    def _get_bf_cfg(self) -> str:
        return FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$test$fakehash",
            dpu_hostname="test-dpu",
            dpu_password="testpassword",
        )

    def test_bf_cfg_contains_netplan_50_mgmt(self):
        """BM2-001: 50-mgmt.yaml with tmfifo_net0 + oob_net0."""
        content = self._get_bf_cfg()
        assert "/mnt/etc/netplan/50-mgmt.yaml" in content
        assert "tmfifo_net0:" in content
        assert "192.168.100.2/30" in content
        assert "oob_net0:" in content

    def test_bf_cfg_contains_netplan_chmod(self):
        """BM2-001: netplan file has correct permissions."""
        content = self._get_bf_cfg()
        assert "chmod 600 /mnt/etc/netplan/50-mgmt.yaml" in content

    def test_bf_cfg_contains_cloud_init_network_disable(self):
        """BM2-002: cloud-init network disable prevents netplan overwrite."""
        content = self._get_bf_cfg()
        assert "99-disable-network-config.cfg" in content
        assert "network: {config: disabled}" in content

    def test_bf_cfg_contains_ovs_br_ports_timeout(self):
        """BM2-003: OVS bridge ports timeout is set to 300s."""
        content = self._get_bf_cfg()
        assert "OVS_BR_PORTS_TIMEOUT=300" in content

    def test_bf_cfg_ovs_bridge_names_use_underscores(self):
        """BM2-004: OVS bridges use underscores (BF3 convention)."""
        content = self._get_bf_cfg()
        assert 'OVS_BRIDGE1="sf_external"' in content
        assert 'OVS_BRIDGE2="sf_internal"' in content
        # Ensure no hyphenated bridge names in OVS assignments
        for line in content.split("\n"):
            if line.startswith("OVS_BRIDGE") and "=" in line:
                assert "sf-external" not in line, f"Hyphenated bridge name found: {line}"
                assert "sf-internal" not in line, f"Hyphenated bridge name found: {line}"


# ── install-dpu-prereqs poc-deployer parity ───────────────────────────


class TestInstallDpuPrereqsPocParity:
    """BM2-010: install-dpu-prereqs aligns with poc-deployer setup-dpu-script.sh."""

    def test_install_dpu_prereqs_no_crictl_input(self):
        """BM2-010: crictl removed from module inputs."""
        mod = InstallDPUPrereqsModule()
        assert "crictl_version" not in mod.inputs

    def test_install_dpu_prereqs_k8s_version_default(self):
        """BM2-010: K8s version default matches poc-deployer."""
        mod = InstallDPUPrereqsModule()
        assert mod.inputs["k8s_version"].default == "1.29"

    def test_install_dpu_prereqs_validate_commands_no_crictl(self):
        """BM2-010: validate_commands does not check crictl."""
        mod = InstallDPUPrereqsModule()
        cmds = mod.validate_commands(SAMPLE_VARIABLES)
        for cmd in cmds:
            assert "crictl" not in cmd, f"crictl should not be in validate_commands: {cmd}"


# ── bf.cfg Sprint 2 parity checks ────────────────────────────────────


class TestBfCfgSprint2Parity:
    """BM2-005 through BM2-014: bf.cfg Sprint 2 parity checks."""

    def _get_bf_cfg_defaults(self) -> str:
        """Generate bf.cfg with default parameters only."""
        return FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$test$fakehash",
            dpu_hostname="test-dpu",
            dpu_password="testpassword",
        )

    def _get_bf_cfg_full(self) -> str:
        """Generate bf.cfg with ALL Sprint 2 options enabled."""
        return FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$test$fakehash",
            dpu_hostname="test-dpu",
            dpu_password="testpassword",
            net_rshim_mac="AA:BB:CC:DD:EE:FF",
            ovs_ext_sf="en3f0pf0sf2",
            ovs_int_sf="en3f1pf1sf2",
            ovs_int_vf="pf1vf1",
            dpu_versions_env="RUNC_VERSION=1.2.1\nK8S_VERSION=1.29\n",
            dns_nameservers="nameserver 1.1.1.1\noptions edns0",
            ssh_pub_key="ssh-rsa AAAAB3testkey user@host",
        )

    def test_bf_cfg_net_rshim_mac_present_when_provided(self):
        """BM2-005: NET_RSHIM_MAC included when provided."""
        content = self._get_bf_cfg_full()
        assert "NET_RSHIM_MAC='AA:BB:CC:DD:EE:FF'" in content

    def test_bf_cfg_net_rshim_mac_absent_when_empty(self):
        """BM2-005: NET_RSHIM_MAC omitted when empty (default)."""
        content = self._get_bf_cfg_defaults()
        assert "NET_RSHIM_MAC" not in content

    def test_bf_cfg_dnsmasq_mask(self):
        """BM2-006: dnsmasq service masked."""
        content = self._get_bf_cfg_defaults()
        assert "ln -sf /dev/null /mnt/etc/systemd/system/dnsmasq.service" in content

    def test_bf_cfg_ovs_ports_default_values(self):
        """BM2-007: OVS ports use defaults when not specified."""
        content = self._get_bf_cfg_defaults()
        assert 'OVS_BRIDGE1_PORTS="p0 en3f0pf0sf1"' in content
        assert 'OVS_BRIDGE2_PORTS="p1 en3f1pf1sf1 pf1vf0"' in content

    def test_bf_cfg_ovs_ports_custom_values(self):
        """BM2-007: OVS ports use custom values when specified."""
        content = self._get_bf_cfg_full()
        assert 'OVS_BRIDGE1_PORTS="p0 en3f0pf0sf2"' in content
        assert 'OVS_BRIDGE2_PORTS="p1 en3f1pf1sf2 pf1vf1"' in content

    def test_bf_cfg_versions_env_staged(self):
        """BM2-011: dpu-versions.env staged when provided."""
        content = self._get_bf_cfg_full()
        assert "/var/tmp/bnk/dpu-versions.env" in content
        # Base64 of "RUNC_VERSION=1.2.1\nK8S_VERSION=1.29\n"
        import base64
        expected_b64 = base64.b64encode(b"RUNC_VERSION=1.2.1\nK8S_VERSION=1.29\n").decode()
        assert expected_b64 in content

    def test_bf_cfg_versions_env_absent_when_empty(self):
        """BM2-011: dpu-versions.env omitted when empty (default)."""
        content = self._get_bf_cfg_defaults()
        assert "dpu-versions.env" not in content

    def test_bf_cfg_dns_resolv_staged(self):
        """BM2-012: DNS resolv.conf staged in write_files."""
        content = self._get_bf_cfg_full()
        assert "/var/tmp/bnk/etc/resolv.conf" in content

    def test_bf_cfg_dns_runcmd_copy(self):
        """BM2-012: DNS resolv.conf copied in runcmd."""
        content = self._get_bf_cfg_full()
        assert "cp /var/tmp/bnk/etc/resolv.conf /etc/resolv.conf" in content

    def test_bf_cfg_ssh_key_staged(self):
        """BM2-013: SSH authorized_keys staged in write_files."""
        content = self._get_bf_cfg_full()
        assert "/var/tmp/bnk/home/ubuntu/.ssh/authorized_keys" in content

    def test_bf_cfg_ssh_key_runcmd_deploy(self):
        """BM2-013: SSH key deployment commands in runcmd."""
        content = self._get_bf_cfg_full()
        assert "mkdir -p /home/ubuntu/.ssh" in content
        assert "chown -R ubuntu:ubuntu /home/ubuntu/.ssh" in content
        assert "chmod 700 /home/ubuntu/.ssh" in content
        assert "chmod 600 /home/ubuntu/.ssh/authorized_keys" in content

    def test_bf_cfg_ssh_key_absent_when_empty(self):
        """BM2-013: SSH key entries omitted when empty (default)."""
        content = self._get_bf_cfg_defaults()
        # Should not have the staging path (write_files)
        assert "/var/tmp/bnk/home/ubuntu/.ssh" not in content
        # Should not have the deployment commands (runcmd) - check unique command
        assert "chown -R ubuntu:ubuntu /home/ubuntu/.ssh" not in content

    def test_bf_cfg_runcmd_completeness_with_all_options(self):
        """BM2-014: runcmd has all entries when all options enabled."""
        content = self._get_bf_cfg_full()
        # Bridge-retry (existing)
        assert "systemctl enable bnk-ovs-bridge-retry.service" in content
        assert "systemctl start bnk-ovs-bridge-retry.service" in content
        # DNS copy
        assert "cp /var/tmp/bnk/etc/resolv.conf /etc/resolv.conf" in content
        # SSH deployment
        assert "mkdir -p /home/ubuntu/.ssh" in content

    def test_bf_cfg_dns_resolv_staged_by_default(self):
        """BM2-012: DNS resolv.conf is staged even with defaults (dns_nameservers has a default value)."""
        content = self._get_bf_cfg_defaults()
        assert "/var/tmp/bnk/etc/resolv.conf" in content
        assert "cp /var/tmp/bnk/etc/resolv.conf /etc/resolv.conf" in content

    def test_bf_cfg_cloud_init_valid_yaml_with_all_options(self):
        """BM2-014: Cloud-init YAML validates even with all options."""
        import yaml
        content = self._get_bf_cfg_full()

        # Extract cloud-init section
        lines = content.split("\n")
        in_cloudinit = False
        cloudinit_lines: list[str] = []
        for line in lines:
            if line.strip() == "#cloud-config":
                in_cloudinit = True
                cloudinit_lines.append(line)
                continue
            if in_cloudinit:
                if line.strip() == "EOFCLOUDINIT":
                    break
                cloudinit_lines.append(line)

        cloudinit_text = "\n".join(cloudinit_lines)
        parsed = yaml.safe_load(cloudinit_text)
        assert parsed is not None
        assert "write_files" in parsed
        assert "runcmd" in parsed
        assert "chpasswd" in parsed
