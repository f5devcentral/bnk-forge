/**
 * Infrastructure page — D-022 P6 IA
 *
 * Top-level section for hardware infrastructure: DPUs / BlueField and bare-metal hosts.
 * DPU Infrastructure was previously a tab in Fleet; it lives here as a dedicated section
 * per ADR D-026 (Fleet operator model — hardware belongs in Infrastructure, not Fleet).
 */

import { useState, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Tabs, TabsContent } from '@/components/ui/tabs';
import { ResourceViewTabs } from '@/components/layout/ResourceViewTabs';
import { SectionCard } from '@/components/ui/section-card';
import { PageHeader } from '@/components/layout/PageHeader';
import { EmptyState } from '@/components/ui/empty-state';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { CircuitBoard, Server } from 'lucide-react';
import { DpuInfrastructurePanel } from '@/components/infrastructure/DpuInfrastructurePanel';

// ──────────────────────────────────────────────────────────────────────────────
// Bare-metal hosts placeholder
// ──────────────────────────────────────────────────────────────────────────────

function BareMetalPlaceholder() {
  return (
    <SectionCard>
      <EmptyState
        icon={Server}
        title="Bare-metal host inventory"
        description="A dedicated bare-metal host inventory view will live here. Hosts can be registered as fleet members and managed alongside clusters and DPUs."
      />
    </SectionCard>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Main page
// ──────────────────────────────────────────────────────────────────────────────

type InfraView = 'dpus' | 'bare-metal';

export default function Infrastructure() {
  const [searchParams, setSearchParams] = useSearchParams();

  const urlView = searchParams.get('tab') as InfraView | null;
  const validViews: InfraView[] = ['dpus', 'bare-metal'];
  const initialView: InfraView =
    urlView && validViews.includes(urlView) ? urlView : 'dpus';

  const [activeView, setActiveView] = useState<InfraView>(initialView);

  const dpfClusterParam = searchParams.get('cluster');

  const handleSelectView = useCallback(
    (view: string) => {
      const v = validViews.includes(view as InfraView) ? (view as InfraView) : 'dpus';
      setActiveView(v);
      const next = new URLSearchParams(searchParams);
      next.set('tab', v);
      next.delete('cluster');
      setSearchParams(next);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [searchParams, setSearchParams],
  );

  const { refresh, isRefreshing } = usePageRefresh();

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <PageHeader
        title="Infrastructure"
        subtitle="Hardware infrastructure — DPU / BlueField accelerators and bare-metal hosts."
        onRefresh={refresh}
        isRefreshing={isRefreshing}
      />

      <Tabs value={activeView} onValueChange={handleSelectView}>
        <ResourceViewTabs
          variant="inline"
          aria-label="Infrastructure views"
          active={activeView}
          onChange={(key) => handleSelectView(key)}
          tabs={[
            { key: 'dpus', label: 'DPUs / BlueField', icon: CircuitBoard },
            { key: 'bare-metal', label: 'Bare-Metal Hosts', icon: Server },
          ]}
        />

        <TabsContent value="dpus" className="mt-6">
          <DpuInfrastructurePanel
            initialClusterId={dpfClusterParam ? Number(dpfClusterParam) : undefined}
          />
        </TabsContent>

        <TabsContent value="bare-metal" className="mt-6">
          <BareMetalPlaceholder />
        </TabsContent>
      </Tabs>
    </div>
  );
}
