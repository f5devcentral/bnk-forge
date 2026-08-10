/**
 * Catalog page — D-020 redesign.
 *
 * Unified home for shared, reusable building blocks behind a single tab strip:
 *   • Modules           — browsable module catalog with source management (Advanced only)
 *   • Blueprints        — browsable blueprint catalog with source management
 *   • Helm Repos        — chart repositories
 *   • DOCA Releases     — paired BFB + DOCA catalog
 *   • bf.conf Templates — DPU config templates
 *
 * The Modules tab is hidden by default and revealed by an "Advanced" toggle
 * (persisted in localStorage key `forge.catalog.advanced`).
 */
import { lazy, Suspense, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Tabs, TabsContent } from '@/components/ui/tabs';
import { Switch } from '@/components/ui/switch';
import { Layers, Package, HardDrive, FileCode, Loader2, Library, Cpu } from 'lucide-react';
import { PageHeader } from '@/components/layout/PageHeader';
import { ResourceViewTabs } from '@/components/layout/ResourceViewTabs';
import { usePageRefresh } from '@/hooks/usePageRefresh';

const Modules = lazy(() => import('@/pages/Modules'));
const BlueprintCatalogPanel = lazy(() => import('@/components/catalog/BlueprintCatalogPanel'));
const HelmReposPanel = lazy(() => import('@/components/catalog/HelmReposPanel'));
const BluefieldImages = lazy(() =>
  import('@/components/settings/BluefieldImages').then((m) => ({ default: m.BluefieldImages })),
);
const BfConfTemplates = lazy(() =>
  import('@/components/settings/BfConfTemplates').then((m) => ({ default: m.BfConfTemplates })),
);
const BnkReleasesPanel = lazy(() => import('@/components/catalog/BnkReleasesPanel'));

const ADVANCED_STORAGE_KEY = 'forge.catalog.advanced';

const VALID_TABS = ['modules', 'blueprints', 'helm-repos', 'doca-releases', 'bnk-releases', 'bf-conf-templates'] as const;
type CatalogTab = (typeof VALID_TABS)[number];

const DEFAULT_TAB: CatalogTab = 'blueprints';

function TabFallback() {
  return (
    <div className="flex justify-center p-8">
      <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
    </div>
  );
}

function readAdvancedFromStorage(): boolean {
  try {
    return localStorage.getItem(ADVANCED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

export default function Catalog() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [advanced, setAdvanced] = useState<boolean>(readAdvancedFromStorage);

  const urlTab = searchParams.get('tab');
  // Support legacy tab values — redirect old tab names to the new unified tab
  const resolvedTab =
    urlTab === 'bfb-images' || urlTab === 'doca-images'
      ? 'doca-releases'
      : urlTab === 'module-library'
        ? 'modules'
        : urlTab;
  const requestedTab: CatalogTab = VALID_TABS.includes(resolvedTab as CatalogTab) ? (resolvedTab as CatalogTab) : DEFAULT_TAB;

  // Deep-link safety: if arriving at ?tab=modules while advanced is off, auto-enable advanced.
  useEffect(() => {
    if (requestedTab === 'modules' && !advanced) {
      setAdvanced(true);
      try {
        localStorage.setItem(ADVANCED_STORAGE_KEY, 'true');
      } catch {
        // ignore
      }
    }
  // Only run on mount (or if requestedTab changes from a navigation).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedTab]);

  const activeTab: CatalogTab = requestedTab === 'modules' && !advanced ? DEFAULT_TAB : requestedTab;

  const handleTabChange = (tab: string) => {
    if (tab === DEFAULT_TAB) {
      searchParams.delete('tab');
    } else {
      searchParams.set('tab', tab);
    }
    setSearchParams(searchParams);
  };

  const handleAdvancedChange = (checked: boolean) => {
    // If turning off while Modules is active, switch to the default tab first.
    if (!checked && activeTab === 'modules') {
      searchParams.delete('tab');
      setSearchParams(searchParams);
    }
    setAdvanced(checked);
    try {
      localStorage.setItem(ADVANCED_STORAGE_KEY, String(checked));
    } catch {
      // ignore
    }
  };

  const { refresh, isRefreshing } = usePageRefresh();

  // Build tab list — Modules only shown when advanced is enabled
  const tabs = [
    ...(advanced ? [{ key: 'modules' as const, label: 'Modules', icon: Layers }] : []),
    { key: 'blueprints' as const, label: 'Blueprints', icon: Library },
    { key: 'helm-repos' as const, label: 'Helm Repos', icon: Package },
    { key: 'doca-releases' as const, label: 'DOCA Releases', icon: HardDrive },
    { key: 'bnk-releases' as const, label: 'BNK Releases', icon: Cpu },
    { key: 'bf-conf-templates' as const, label: 'bf.conf Templates', icon: FileCode },
  ];

  const advancedToggle = (
    <label className="flex items-center gap-2 cursor-pointer select-none text-sm text-muted-foreground">
      <Switch checked={advanced} onCheckedChange={handleAdvancedChange} />
      Advanced
    </label>
  );

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <PageHeader
        title="Catalog"
        subtitle="Shared building blocks used by blueprints and projects — modules, blueprints, DPU bootstreams, bf.conf templates, and their sources."
        actions={advancedToggle}
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

        <TabsContent value="modules" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <Modules />
          </Suspense>
        </TabsContent>

        <TabsContent value="blueprints" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <BlueprintCatalogPanel />
          </Suspense>
        </TabsContent>

        <TabsContent value="helm-repos" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <HelmReposPanel />
          </Suspense>
        </TabsContent>

        <TabsContent value="doca-releases" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <BluefieldImages />
          </Suspense>
        </TabsContent>

        <TabsContent value="bnk-releases" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <BnkReleasesPanel />
          </Suspense>
        </TabsContent>

        <TabsContent value="bf-conf-templates" className="mt-6">
          <Suspense fallback={<TabFallback />}>
            <BfConfTemplates />
          </Suspense>
        </TabsContent>
      </Tabs>
    </div>
  );
}
