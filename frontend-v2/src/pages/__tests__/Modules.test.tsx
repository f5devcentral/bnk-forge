/**
 * Tests for Modules (Module Catalog) page
 *
 * Covers: page title, search input, module table rendering,
 * sidebar with sources/categories, loading state, empty state, Sync All button.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import Modules from '@/pages/Modules';
import { useModuleLibrary, useModuleProviders, useSyncModuleLibrary } from '@/hooks/useModules';
import { useModuleSources, useSyncModuleSource } from '@/hooks/useModuleSources';
import type { ModuleLibrary } from '@/types';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/hooks/useModules', () => ({
  useModuleLibrary: vi.fn(),
  useModuleProviders: vi.fn(),
  useSyncModuleLibrary: vi.fn(),
}));

vi.mock('@/hooks/useModuleSources', () => ({
  useModuleSources: vi.fn(),
  useSyncModuleSource: vi.fn(),
}));

vi.mock('@/components/modules/ModuleDetailSheet', () => ({
  ModuleDetailSheet: () => null,
}));

vi.mock('@/components/modules/AddModuleToProjectDialog', () => ({
  AddModuleToProjectDialog: () => null,
}));

vi.mock('@/components/modules/ModuleSourcesDialog', () => ({
  ModuleSourcesDialog: () => null,
}));

vi.mock('@/components/modules/RegistryBrowseDialog', () => ({
  RegistryBrowseDialog: () => null,
}));

const mockModules: ModuleLibrary[] = [
  {
    id: 1,
    name: 'vpc-network',
    description: 'Creates a VPC with subnets',
    category: 'infrastructure',
    provider: 'aws',
    version: '2.1.0',
    engine_type: 'opentofu',
    module_type: 'opentofu',
    is_builtin: false,
    update_available: false,
    latest_version: null,
    dependencies: [],
    module_source_id: 1,
  } as unknown as ModuleLibrary,
  {
    id: 2,
    name: 'bnk-gateway',
    description: 'F5 BNK Gateway configuration',
    category: 'bnk',
    provider: 'kubernetes',
    version: '1.0.0',
    engine_type: 'kubernetes',
    module_type: 'helm_chart',
    update_available: false,
    latest_version: null,
    dependencies: [],
    module_source_id: 2,
  } as unknown as ModuleLibrary,
  {
    id: 3,
    name: 'ansible-checks',
    description: 'Runs post-deploy validation checks',
    category: 'infrastructure',
    provider: 'aws',
    version: '0.2.0',
    engine_type: 'ansible',
    module_type: 'manifest',
    is_builtin: false,
    update_available: false,
    latest_version: null,
    dependencies: [],
    module_source_id: 1,
  } as unknown as ModuleLibrary,
];

function setupDefaultMocks(overrides: {
  modules?: ModuleLibrary[] | undefined;
  isLoadingModules?: boolean;
  isLoadingSources?: boolean;
} = {}) {
  vi.mocked(useModuleLibrary).mockReturnValue({
    data: overrides.modules !== undefined ? overrides.modules : mockModules,
    isLoading: overrides.isLoadingModules ?? false,
  } as unknown as ReturnType<typeof useModuleLibrary>);

  vi.mocked(useModuleProviders).mockReturnValue({
    data: [{ name: 'aws' }, { name: 'kubernetes' }],
  } as unknown as ReturnType<typeof useModuleProviders>);

  vi.mocked(useSyncModuleLibrary).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
  } as unknown as ReturnType<typeof useSyncModuleLibrary>);

  vi.mocked(useModuleSources).mockReturnValue({
    data: [
      { id: 1, name: 'my-git-repo', source_type: 'git', sync_status: 'success', last_synced_at: null },
      { id: 2, name: 'bnk-curated-repo', source_type: 'git', sync_status: 'success', last_synced_at: null },
    ],
    isLoading: overrides.isLoadingSources ?? false,
  } as unknown as ReturnType<typeof useModuleSources>);

  vi.mocked(useSyncModuleSource).mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useSyncModuleSource>);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Modules', () => {
  it('renders page title "Module Catalog"', () => {
    setupDefaultMocks();
    render(<Modules />);
    expect(screen.getByText('Module Catalog')).toBeInTheDocument();
  });

  it('renders search input with correct placeholder', () => {
    setupDefaultMocks();
    render(<Modules />);
    // D-020: shortened placeholder, uses ellipsis char — match leniently.
    expect(
      screen.getByPlaceholderText(/search by name, category, or provider/i),
    ).toBeInTheDocument();
  });

  it('renders module rows in the table', () => {
    setupDefaultMocks();
    render(<Modules />);
    expect(screen.getByText('vpc-network')).toBeInTheDocument();
    expect(screen.getByText('bnk-gateway')).toBeInTheDocument();
    expect(screen.getByText('ansible-checks')).toBeInTheDocument();
  });

  it('renders mixed-engine badges from backend engine metadata', () => {
    setupDefaultMocks();
    render(<Modules />);

    // bnk-gateway has engine_type=kubernetes + module_type=helm_chart →
    // engine presentation label is "Helm" (not "Kubernetes").
    expect(screen.getByText('OpenTofu')).toBeInTheDocument();
    expect(screen.getByText('Helm')).toBeInTheDocument();
    expect(screen.getByText('Ansible')).toBeInTheDocument();
  });

  it('renders source filter with All sources option', () => {
    // D-020: Source/category sidebar replaced by Select dropdowns. The "All
    // sources" placeholder + the source name in the module row remain.
    setupDefaultMocks();
    render(<Modules />);
    expect(screen.getByText('All sources')).toBeInTheDocument();
    // Source name appears in module row source column.
    expect(screen.getAllByText('my-git-repo').length).toBeGreaterThanOrEqual(1);
  });

  it('renders loading skeletons while data is fetching', () => {
    setupDefaultMocks({ modules: undefined, isLoadingModules: true, isLoadingSources: true });
    render(<Modules />);
    const skeletons = document.querySelectorAll('[class*="animate-pulse"], [class*="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
  });

  it('renders empty state when no modules exist', () => {
    setupDefaultMocks({ modules: [] });
    render(<Modules />);
    expect(screen.getByText('No modules found')).toBeInTheDocument();
  });

  it('renders Sync all button', () => {
    // D-020: button label is now "Sync all" (lowercased "all").
    setupDefaultMocks();
    render(<Modules />);
    expect(screen.getByText('Sync all')).toBeInTheDocument();
  });

  it('renders Sources and Browse registry buttons in the header', () => {
    // D-020: grid/table view toggle removed; the redesigned page is a single
    // sortable table in a SectionCard. Header actions are Sources / Browse
    // registry / Sync all.
    setupDefaultMocks();
    render(<Modules />);
    expect(screen.getByRole('button', { name: /sources/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /browse registry/i })).toBeInTheDocument();
  });
});
