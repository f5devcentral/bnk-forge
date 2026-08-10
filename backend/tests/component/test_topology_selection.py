"""
Component tests for _auto_select_topology_modules in stack_deployment_service.

Tests that the correct modules are enabled/disabled based on the
BareMetalHost's topology field. Uses real DB objects.

Covers:
- topology="regular" → set-nic-mode enabled, stage-vf-netplan disabled
- topology="bf3" → set-nic-mode disabled, stage-vf-netplan enabled
- topology="bf3_ipmi" → set-nic-mode + deposit-phase-c + power-cycle enabled
- topology="bmc" → stage-vf-netplan + deposit-phase-c + power-cycle enabled
- topology="dual_dpu_obmc" → all optional modules disabled
- No topology → all optional modules left as-is
"""

import pytest

from models import ModuleLibrary, Project, ProjectModule, StackInstance, StackTemplate
from models.bare_metal import BareMetalHost

# ── Fixtures ──────────────────────────────────────────────────────────

# All optional bare-metal module paths used in topology selection
OPTIONAL_PATHS = [
    "bare-metal/set-nic-mode",
    "bare-metal/stage-vf-netplan",
    "bare-metal/install-fallback-timer",
    "bare-metal/deposit-phase-c",
    "bare-metal/power-cycle-bmc",
    "bare-metal/wait-connectivity",
]


@pytest.fixture()
def project(db):
    proj = Project(
        name="Topology Test",
        description="Test topology selection",
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
def bare_metal_host(db, project):
    """Create a host with topology set later per test."""
    host = BareMetalHost(
        project_id=project.id,
        name="topo-test-host",
        host_ip="10.0.0.1",
    )
    db.add(host)
    db.flush()
    return host


@pytest.fixture()
def stack_template(db):
    """Create a template with optional bare-metal modules."""
    template_modules = []
    for path in OPTIONAL_PATHS:
        template_modules.append({
            "path": path,
            "required": False,
        })
    # Add a required module
    template_modules.append({
        "path": "bare-metal/probe-dpu",
        "required": True,
    })
    template_modules.append({
        "path": "bare-metal/flash-dpu",
        "required": True,
    })

    tmpl = StackTemplate(
        name="BM Deploy Template",
        slug="bm-deploy",
        description="Bare-metal deployment template",
        category="bare-metal",
        cloud_provider="on-prem",
        modules=template_modules,
        is_public=True,
        is_active=True,
    )
    db.add(tmpl)
    db.flush()
    return tmpl


@pytest.fixture()
def stack_instance(db, project, stack_template, bare_metal_host):
    """Create a stack instance linked to the host."""
    instance = StackInstance(
        project_id=project.id,
        template_id=stack_template.id,
        name="test-deploy",
        status="pending",
        variables={"bare_metal_host_id": bare_metal_host.id},
    )
    db.add(instance)
    db.flush()
    return instance


def _create_modules(db, project, optional_paths=None):
    """Create ProjectModules for all paths (required + optional)."""
    optional_paths = optional_paths or OPTIONAL_PATHS
    all_paths = ["bare-metal/probe-dpu", "bare-metal/flash-dpu"] + list(optional_paths)

    modules = []
    module_map = {}
    for path in all_paths:
        lib = ModuleLibrary(
            name=path.split("/")[-1],
            category="bare-metal",
            path=path,
            provider="bnk-forge",
            description=f"Module {path}",
            git_source="https://github.com/example/modules.git",
            engine_type="ssh",
            is_official=True,
            is_active=True,
        )
        db.add(lib)
        db.flush()

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=lib.id,
            path_in_project=path,
            status="not_initialized",
            variables={},
            enabled=True,
        )
        db.add(mod)
        db.flush()

        modules.append(mod)
        module_map[path] = mod

    return modules, module_map


# ── Tests ─────────────────────────────────────────────────────────────


class TestAutoSelectTopologyModules:
    """Test _auto_select_topology_modules for each topology variant."""

    def test_regular_topology_enables_set_nic_mode(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "regular"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        # set-nic-mode should be enabled
        assert module_map["bare-metal/set-nic-mode"].enabled is True

    def test_regular_topology_disables_stage_vf_netplan(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "regular"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        assert module_map["bare-metal/stage-vf-netplan"].enabled is False

    def test_regular_topology_disables_power_cycle_bmc(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "regular"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        assert module_map["bare-metal/power-cycle-bmc"].enabled is False

    def test_bf3_topology_disables_set_nic_mode(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "bf3"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        assert module_map["bare-metal/set-nic-mode"].enabled is False

    def test_bf3_topology_enables_stage_vf_netplan(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "bf3"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        assert module_map["bare-metal/stage-vf-netplan"].enabled is True

    def test_bf3_topology_enables_install_fallback_timer(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "bf3"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        assert module_map["bare-metal/install-fallback-timer"].enabled is True

    def test_bf3_ipmi_topology_enables_set_nic_mode(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "bf3_ipmi"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        assert module_map["bare-metal/set-nic-mode"].enabled is True
        assert module_map["bare-metal/deposit-phase-c"].enabled is True
        assert module_map["bare-metal/power-cycle-bmc"].enabled is True
        assert module_map["bare-metal/wait-connectivity"].enabled is True

    def test_bf3_ipmi_topology_disables_stage_vf_netplan(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "bf3_ipmi"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        assert module_map["bare-metal/stage-vf-netplan"].enabled is False

    def test_bmc_topology_enables_stage_vf_netplan_and_power_cycle(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "bmc"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        assert module_map["bare-metal/stage-vf-netplan"].enabled is True
        assert module_map["bare-metal/deposit-phase-c"].enabled is True
        assert module_map["bare-metal/power-cycle-bmc"].enabled is True
        assert module_map["bare-metal/wait-connectivity"].enabled is True

    def test_bmc_topology_disables_set_nic_mode(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "bmc"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        assert module_map["bare-metal/set-nic-mode"].enabled is False

    def test_dual_dpu_obmc_disables_all_optional_modules(
        self, db, project, bare_metal_host, stack_instance,
    ):
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "dual_dpu_obmc"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        for path in OPTIONAL_PATHS:
            assert module_map[path].enabled is False, f"{path} should be disabled for dual_dpu_obmc"

    def test_required_modules_never_disabled(
        self, db, project, bare_metal_host, stack_instance,
    ):
        """Required modules (probe-dpu, flash-dpu) must remain enabled."""
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = "regular"
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        # Required modules from template
        assert module_map["bare-metal/probe-dpu"].enabled is True
        assert module_map["bare-metal/flash-dpu"].enabled is True

    def test_no_topology_skips_selection(
        self, db, project, bare_metal_host, stack_instance,
    ):
        """When host has no topology, all optional modules stay enabled."""
        from services.stack_deployment_service import _auto_select_topology_modules

        bare_metal_host.topology = None
        db.flush()

        modules, module_map = _create_modules(db, project)
        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        # All should remain enabled (no selection applied)
        for path in OPTIONAL_PATHS:
            assert module_map[path].enabled is True, f"{path} should remain enabled with no topology"

    def test_no_host_id_skips_selection(
        self, db, project, bare_metal_host, stack_instance,
    ):
        """When stack has no bare_metal_host_id, skip topology selection."""
        from services.stack_deployment_service import _auto_select_topology_modules

        stack_instance.variables = {}
        db.flush()

        modules, module_map = _create_modules(db, project)

        # Clear variable_overrides too so host_id isn't found
        for mod in modules:
            mod.variable_overrides = {}
        db.flush()

        _auto_select_topology_modules(db, stack_instance, modules, module_map)

        # All should remain as-is
        for path in OPTIONAL_PATHS:
            assert module_map[path].enabled is True
