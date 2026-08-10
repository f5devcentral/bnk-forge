import type { DeploymentEngineType, LifecycleCapabilities, ModuleLibrary, ProjectModule } from '@/types';

type LifecycleAction = 'init' | 'plan' | 'apply' | 'destroy' | 'refresh' | 'drift';

const LEGACY_OPENTOFU_CAPABILITIES: LifecycleCapabilities = {
  supports_init: true,
  supports_plan: true,
  supports_apply: true,
  supports_destroy: true,
  supports_refresh: true,
  supports_drift: true,
};

const LEGACY_KUBERNETES_CAPABILITIES: LifecycleCapabilities = {
  supports_init: true,
  supports_plan: true,
  supports_apply: true,
  supports_destroy: true,
  supports_refresh: true,
  supports_drift: true,
};

const LEGACY_ANSIBLE_CAPABILITIES: LifecycleCapabilities = {
  supports_init: true,
  supports_plan: true,
  supports_apply: true,
  supports_destroy: false,
  supports_refresh: false,
  supports_drift: false,
};

const LEGACY_SCRIPT_CAPABILITIES: LifecycleCapabilities = {
  supports_init: false,
  supports_plan: false,
  supports_apply: true,
  supports_destroy: false,
  supports_refresh: false,
  supports_drift: false,
};

const LEGACY_SSH_CAPABILITIES: LifecycleCapabilities = {
  supports_init: true,
  supports_plan: true,
  supports_apply: true,
  supports_destroy: false,
  supports_refresh: false,
  supports_drift: false,
};

// Procedural container artifacts: apply/destroy are step-sets; there is no
// Terraform-style plan/refresh/drift. Real capabilities come from the manifest's
// lifecycle_capabilities; this is only the fallback when those are absent.
const LEGACY_CONTAINER_CAPABILITIES: LifecycleCapabilities = {
  supports_init: false,
  supports_plan: false,
  supports_apply: true,
  supports_destroy: true,
  supports_refresh: false,
  supports_drift: false,
};

export interface EnginePresentation {
  label: string;
  className: string;
}

export function getEnginePresentation(
  engineType?: DeploymentEngineType,
  moduleType?: ModuleLibrary['module_type']
): EnginePresentation {
  if (engineType === 'kubernetes') {
    return {
      label: moduleType === 'helm_chart' ? 'Helm' : 'Kubernetes',
      className: 'bg-success/10 text-success border-success/30',
    };
  }

  if (engineType === 'ansible') {
    return {
      label: 'Ansible',
      className: 'bg-warning/10 text-warning border-warning/30',
    };
  }

  if (engineType === 'script') {
    return {
      label: 'Governed Script',
      className: 'bg-primary/10 text-primary border-primary/30',
    };
  }

  if (engineType === 'ssh') {
    return {
      label: 'SSH',
      className: 'bg-warning/10 text-warning border-warning/30',
    };
  }

  if (engineType === 'container') {
    return {
      label: 'Container',
      className: 'bg-info/10 text-info border-info/30',
    };
  }

  return {
    label: 'OpenTofu',
    className: 'bg-primary/10 text-primary border-primary/30',
  };
}

export function resolveModuleEngineType(module: Pick<ModuleLibrary, 'engine_type' | 'module_type'>): DeploymentEngineType {
  if (module.engine_type) {
    return module.engine_type;
  }
  if (module.module_type === 'helm_chart') {
    return 'kubernetes';
  }
  return 'opentofu';
}

export function resolveProjectModuleEngineType(
  module: Pick<ProjectModule, 'engine_type' | 'library_module'>
): DeploymentEngineType {
  if (module.engine_type) {
    return module.engine_type;
  }

  if (module.library_module?.engine_type) {
    return module.library_module.engine_type;
  }

  return resolveModuleEngineType({
    engine_type: module.library_module?.engine_type,
    module_type: module.library_module?.module_type,
  });
}

function getLegacyCapabilitiesForEngine(engineType: DeploymentEngineType): LifecycleCapabilities {
  if (engineType === 'kubernetes') return LEGACY_KUBERNETES_CAPABILITIES;
  if (engineType === 'ansible') return LEGACY_ANSIBLE_CAPABILITIES;
  if (engineType === 'script') return LEGACY_SCRIPT_CAPABILITIES;
  if (engineType === 'ssh') return LEGACY_SSH_CAPABILITIES;
  if (engineType === 'container') return LEGACY_CONTAINER_CAPABILITIES;
  return LEGACY_OPENTOFU_CAPABILITIES;
}

export function resolveLifecycleCapabilities(module: Pick<ProjectModule, 'engine_type' | 'lifecycle_capabilities' | 'library_module'>): LifecycleCapabilities {
  if (module.lifecycle_capabilities) {
    return module.lifecycle_capabilities;
  }

  const resolvedEngineType = resolveProjectModuleEngineType(module);

  if (module.library_module?.lifecycle_capabilities) {
    return module.library_module.lifecycle_capabilities;
  }

  return getLegacyCapabilitiesForEngine(resolvedEngineType);
}

export function isActionSupported(module: Pick<ProjectModule, 'engine_type' | 'lifecycle_capabilities' | 'library_module'>, action: LifecycleAction): boolean {
  const capabilities = resolveLifecycleCapabilities(module);

  if (action === 'init') return capabilities.supports_init;
  if (action === 'plan') return capabilities.supports_plan;
  if (action === 'apply') return capabilities.supports_apply;
  if (action === 'destroy') return capabilities.supports_destroy;
  if (action === 'refresh') return capabilities.supports_refresh;
  return capabilities.supports_drift;
}

export function getLifecycleSummaryBadges(capabilities?: LifecycleCapabilities): string[] {
  if (!capabilities) {
    return [];
  }

  const supported: string[] = [];
  if (capabilities.supports_init) supported.push('Init');
  if (capabilities.supports_plan) supported.push('Plan');
  if (capabilities.supports_apply) supported.push('Apply');
  if (capabilities.supports_destroy) supported.push('Destroy');
  if (capabilities.supports_refresh) supported.push('Refresh');
  if (capabilities.supports_drift) supported.push('Drift');
  return supported;
}
