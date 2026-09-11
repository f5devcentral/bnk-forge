/**
 * System Administration page — D-020 redesign.
 *
 * Bold heading + tab strip. Each tab body renders existing settings panels
 * unchanged. The Appearance tab uses a SectionCard with a theme toggle and
 * two state pills (Light / Dark).
 */

import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import PerformanceMonitor from '@/components/settings/PerformanceMonitor';
import DatabaseManagement from '@/components/settings/DatabaseManagement';
import ContainerManagement from '@/components/settings/ContainerManagement';
import SystemDefaults from '@/components/settings/SystemDefaults';
import SystemUpgrade from '@/components/settings/SystemUpgrade';
import AuditLog from '@/components/settings/AuditLog';
import { AlertChannels } from '@/components/settings/AlertChannels';
import { BackupPanel } from '@/components/settings/BackupPanel';
import { McpPanel } from '@/components/system/McpPanel';
import { Tabs, TabsContent } from '@/components/ui/tabs';
import { ResourceViewTabs } from '@/components/layout/ResourceViewTabs';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { SectionCard } from '@/components/ui/section-card';
import { cn } from '@/lib/utils';
import {
  Palette,
  Moon,
  Sun,
  Monitor,
  Settings2,
  ScrollText,
  Bell,
  HardDrive,
  Bot,
} from 'lucide-react';
import { useUIStore } from '@/stores/uiStore';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';

// Old /system?tab=... URLs that now live under /catalog.
const URL_TAB_REDIRECTS: Record<string, string> = {
  'bluefield-images': '/catalog?tab=bfb-images',
  'bf-conf-templates': '/catalog?tab=bf-conf-templates',
  'module-library': '/catalog?tab=module-library',
  'helm-repos': '/catalog?tab=helm-repos',
};

const VALID_TABS = ['monitor', 'mcp', 'audit', 'alerts', 'defaults', 'appearance', 'backup'] as const;

export default function System() {
  const { theme, setTheme } = useUIStore();
  const { refresh, isRefreshing } = usePageRefresh();
  const [searchParams, setSearchParams] = useSearchParams();
  const urlTab = searchParams.get('tab');
  const initialTab = urlTab && (VALID_TABS as readonly string[]).includes(urlTab) ? urlTab : 'monitor';
  const [activeTab, setActiveTab] = useState(initialTab);

  useEffect(() => {
    if (urlTab && URL_TAB_REDIRECTS[urlTab]) {
      window.location.replace(URL_TAB_REDIRECTS[urlTab]);
    }
  }, [urlTab]);

  const handleTabChange = (tab: string) => {
    setActiveTab(tab);
    if (tab === 'monitor') {
      searchParams.delete('tab');
    } else {
      searchParams.set('tab', tab);
    }
    setSearchParams(searchParams);
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <PageHeader
        title="System Administration"
        subtitle="Monitor system health, audit activity, and configure application settings."
        onRefresh={refresh}
        isRefreshing={isRefreshing}
      />

      <Tabs value={activeTab} onValueChange={handleTabChange}>
        <ResourceViewTabs
          variant="inline"
          aria-label="System sections"
          active={activeTab}
          onChange={handleTabChange}
          tabs={[
            { key: 'monitor', label: 'System Monitor', icon: Monitor },
            { key: 'mcp', label: 'MCP Server', icon: Bot },
            { key: 'audit', label: 'Audit Log', icon: ScrollText },
            { key: 'alerts', label: 'Alerts', icon: Bell },
            { key: 'defaults', label: 'Defaults', icon: Settings2 },
            { key: 'appearance', label: 'Appearance', icon: Palette },
            { key: 'backup', label: 'Backup & Restore', icon: HardDrive },
          ]}
        />

        <TabsContent value="monitor" className="space-y-6 mt-6">
          <SystemUpgrade />
          <PerformanceMonitor />
          <DatabaseManagement />
          <ContainerManagement />
        </TabsContent>

        <TabsContent value="mcp" className="mt-6">
          <McpPanel />
        </TabsContent>

        <TabsContent value="audit" className="mt-6">
          <AuditLog />
        </TabsContent>

        <TabsContent value="alerts" className="mt-6">
          <AlertChannels />
        </TabsContent>

        <TabsContent value="defaults" className="mt-6">
          <SystemDefaults />
        </TabsContent>

        <TabsContent value="appearance" className="mt-6">
          <SectionCard title="Appearance">
            <p className="text-sm text-muted-foreground mb-6">
              Customize the look and feel of the application.
            </p>

            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label className="text-sm text-foreground">Dark mode</Label>
                <p className="text-sm text-muted-foreground">
                  Switch between light and dark themes.
                </p>
              </div>
              <div className="flex items-center gap-3">
                <Sun
                  className={cn(
                    'h-4 w-4',
                    theme === 'light' ? 'text-foreground' : 'text-muted-foreground',
                  )}
                />
                <Switch
                  checked={theme === 'dark'}
                  onCheckedChange={(checked) => setTheme(checked ? 'dark' : 'light')}
                />
                <Moon
                  className={cn(
                    'h-4 w-4',
                    theme === 'dark' ? 'text-foreground' : 'text-muted-foreground',
                  )}
                />
              </div>
            </div>

            <Separator />

            <div className="grid gap-2">
              <Label className="text-foreground/80">Current theme</Label>
              <div className="flex items-center gap-2">
                <Badge
                  variant={theme === 'light' ? 'default' : 'outline'}
                  className="flex items-center gap-1"
                >
                  <Sun className="h-3 w-3" />
                  Light
                </Badge>
                <Badge
                  variant={theme === 'dark' ? 'default' : 'outline'}
                  className="flex items-center gap-1"
                >
                  <Moon className="h-3 w-3" />
                  Dark
                </Badge>
              </div>
            </div>
          </SectionCard>
        </TabsContent>

        <TabsContent value="backup" className="mt-6">
          <BackupPanel />
        </TabsContent>
      </Tabs>
    </div>
  );
}
