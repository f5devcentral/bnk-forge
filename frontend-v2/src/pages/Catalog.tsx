/**
 * Catalog page — Streamlined 4-tab architecture.
 *
 * Unified home for shared, reusable building blocks behind 4 clear primary tabs:
 *   • Blueprints & Stacks — browsable blueprint catalog with source management
 *   • Modules             — browsable module catalog with source management
 *   • Helm Charts & Repos — chart repositories & registries
 *   • System & DPU Images — DOCA/BFB releases, BNK releases, and bf.conf templates
 */
import { lazy, Suspense } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Tabs, TabsContent } from '@/components/ui/tabs';
import { Layers, Package, HardDrive, Loader2, Library } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { ResourceViewTabs } from '@/components/layout/ResourceViewTabs';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import type { SystemSubTab } from '@/components/catalog/SystemImagesPanel';

const Modules = lazy(() => import('@/pages/Modules'));
const BlueprintCatalogPanel = lazy(() => import('@/components/catalog/BlueprintCatalogPanel'));
const HelmReposPanel = lazy(() => import('@/components/catalog/HelmReposPanel'));
const SystemImagesPanel = lazy(() => import('@/components/catalog/SystemImagesPanel'));

const VALID_TABS = ['blueprints', 'modules', 'helm-repos', 'system-images'] as const;
type CatalogTab = (typeof VALID_TABS)[number];

const DEFAULT_TAB: CatalogTab = 'blueprints';

function TabFallback() {
  return (
    <div className="flex justify-center p-8">
      <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
    </div>
  );
}

export default function Catalog() {
  const [searchParams, setSearchParams] = useSearchParams();

  const urlTab = searchParams.get('tab');
  // Map legacy / deep-link subtab values to primary tab + initial system subtab
  let initialSystemSubTab: SystemSubTab = 'doca';
  let resolvedTab = urlTab;
  if (urlTab === 'bfb-images' || urlTab === 'doca-images' || urlTab === 'doca-releases') {
    resolvedTab = 'system-images';
    initialSystemSubTab = 'doca';
  } else if (urlTab === 'bnk-releases') {
    resolvedTab = 'system-images';
    initialSystemSubTab = 'bnk';
  } else if (urlTab === 'bf-conf-templates') {
    resolvedTab = 'system-images';
    initialSystemSubTab = 'bfconf';
  } else if (urlTab === 'module-library') {
    resolvedTab = 'modules';
  }

  const activeTab: CatalogTab = VALID_TABS.includes(resolvedTab as CatalogTab)
    ? (resolvedTab as CatalogTab)
    : DEFAULT_TAB;

  const handleTabChange = (tab: string) => {
    if (tab === DEFAULT_TAB) {
      searchParams.delete('tab');
    } else {
      searchParams.set('tab', tab);
    }
    setSearchParams(searchParams);
  };

  const { refresh, isRefreshing } = usePageRefresh();

  const tabs = [
    { key: 'blueprints' as const, label: 'Blueprints & Stacks', icon: Library },
    { key: 'modules' as const, label: 'Modules', icon: Layers },
    { key: 'helm-repos' as const, label: 'Helm Charts & Repos', icon: Package },
    { key: 'system-images' as const, label: 'System & DPU Images', icon: HardDrive },
  ];

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <PageHeader
        title="Catalog"
        subtitle="Shared building blocks used by blueprints and projects — blueprints, modules, helm repositories, DPU bootstreams, and BNK releases."
        onRefresh={refresh}
        isRefreshing={isRefreshing}
      />

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <ResourceViewTabs
          variant="inline"
          aria-label="Catalog sections"
          active={activeTab}
          onChange={handleTabChange}
          tabs={tabs}
        />

        <TabsContent value="blueprints" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <BlueprintCatalogPanel />
          </Suspense>
        </TabsContent>

        <TabsContent value="modules" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <Modules />
          </Suspense>
        </TabsContent>

        <TabsContent value="helm-repos" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <HelmReposPanel />
          </Suspense>
        </TabsContent>

        <TabsContent value="system-images" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <SystemImagesPanel initialSubTab={initialSystemSubTab} />
          </Suspense>
        </TabsContent>
      </Tabs>
    </div>
  );
}
