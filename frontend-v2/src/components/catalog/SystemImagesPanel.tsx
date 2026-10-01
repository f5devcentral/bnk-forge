import { lazy, Suspense, useState } from 'react';
import { ResourceViewTabs, type ResourceViewTab } from '@/components/layout/ResourceViewTabs';
import { HardDrive, Cpu, FileCode, Loader2 } from 'lucide-react';

const BluefieldImages = lazy(() =>
  import('@/components/settings/BluefieldImages').then((m) => ({ default: m.BluefieldImages })),
);
const BfConfTemplates = lazy(() =>
  import('@/components/settings/BfConfTemplates').then((m) => ({ default: m.BfConfTemplates })),
);
const BnkReleasesPanel = lazy(() => import('@/components/catalog/BnkReleasesPanel'));

function TabFallback() {
  return (
    <div className="flex justify-center p-8">
      <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
    </div>
  );
}

export type SystemSubTab = 'doca' | 'bnk' | 'bfconf';

interface SystemImagesPanelProps {
  initialSubTab?: SystemSubTab;
}

export function SystemImagesPanel({ initialSubTab = 'doca' }: SystemImagesPanelProps) {
  const [subTab, setSubTab] = useState<SystemSubTab>(initialSubTab);

  const tabs: ResourceViewTab[] = [
    { key: 'doca', label: 'DOCA / BFB Bootstreams', icon: HardDrive },
    { key: 'bnk', label: 'BNK Operator Releases', icon: Cpu },
    { key: 'bfconf', label: 'bf.conf Templates', icon: FileCode },
  ];

  return (
    <div className="space-y-6">
      <div className="border-b border-border pb-3">
        <ResourceViewTabs
          variant="strip"
          aria-label="System and DPU sub-sections"
          active={subTab}
          onChange={(v) => setSubTab(v as SystemSubTab)}
          tabs={tabs}
        />
      </div>

      <div>
        {subTab === 'doca' && (
          <Suspense fallback={<TabFallback />}>
            <BluefieldImages />
          </Suspense>
        )}
        {subTab === 'bnk' && (
          <Suspense fallback={<TabFallback />}>
            <BnkReleasesPanel />
          </Suspense>
        )}
        {subTab === 'bfconf' && (
          <Suspense fallback={<TabFallback />}>
            <BfConfTemplates />
          </Suspense>
        )}
      </div>
    </div>
  );
}

export default SystemImagesPanel;
