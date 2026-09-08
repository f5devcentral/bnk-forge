/**
 * WafPanelBuilderModal — create or edit a custom dashboard panel.
 */
import { useState, useEffect } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { useWafPanelTemplates, useCreateWafPanel, useUpdateWafPanel } from '@/hooks/useWafPanels';
import type { WafPanel, ChartType, TimeRange, PanelWidth } from '@/lib/api/waf-panels';

const CHART_LABELS: Record<string, string> = {
  bar:            'Vertical Bar',
  horizontal_bar: 'Horizontal Bar',
  area:           'Stacked Area',
  line:           'Line',
  pie:            'Pie',
  kpi:            'KPI (single number)',
  table:          'Data Table',
};

interface Props {
  clusterId: number;
  panel: WafPanel | null; // null = create mode
  open: boolean;
  onClose: () => void;
  nextOrder: number;
  tabId?: number; // which custom tab a newly created panel belongs to (0 = default tab)
}

export function WafPanelBuilderModal({ clusterId, panel, open, onClose, nextOrder, tabId }: Props) {
  const isEdit = panel !== null;
  const { data: meta } = useWafPanelTemplates(clusterId);
  const create = useCreateWafPanel(clusterId);
  const update = useUpdateWafPanel(clusterId);

  const [title, setTitle]       = useState('');
  const [template, setTemplate] = useState('events_by_attack_type');
  const [chartType, setChart]   = useState<ChartType>('horizontal_bar');
  const [timeRange, setRange]   = useState<TimeRange>('7d');
  const [width, setWidth]       = useState<PanelWidth>('half');
  const [error, setError]       = useState<string | null>(null);

  // Populate form when editing
  useEffect(() => {
    if (panel) {
      setTitle(panel.title);
      setTemplate(panel.query_template);
      setChart(panel.chart_type);
      setRange(panel.time_range);
      setWidth(panel.width);
    } else {
      setTitle('');
      setTemplate('events_by_attack_type');
      setChart('horizontal_bar');
      setRange('7d');
      setWidth('half');
    }
    setError(null);
  }, [panel, open]);

  async function handleSubmit() {
    if (!title.trim()) { setError('Title is required'); return; }
    setError(null);
    try {
      if (isEdit) {
        await update.mutateAsync({ id: panel.id, title: title.trim(), chart_type: chartType, query_template: template, time_range: timeRange, width });
      } else {
        await create.mutateAsync({ title: title.trim(), chart_type: chartType, query_template: template, time_range: timeRange, width, panel_order: nextOrder, tab_id: tabId });
      }
      onClose();
    } catch {
      setError('Failed to save panel.');
    }
  }

  const pending = create.isPending || update.isPending;

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit Panel' : 'Add Panel'}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="space-y-1.5">
            <Label>Title</Label>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="My panel" />
          </div>

          <div className="space-y-1.5">
            <Label>Data source</Label>
            <Select value={template} onValueChange={setTemplate}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {(meta?.templates ?? []).map((t) => (
                  <SelectItem key={t.key} value={t.key}>{t.description}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>Chart type</Label>
              <Select value={chartType} onValueChange={(v) => setChart(v as ChartType)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(CHART_LABELS).map(([k, v]) => (
                    <SelectItem key={k} value={k}>{v}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Time range</Label>
              <Select value={timeRange} onValueChange={(v) => setRange(v as TimeRange)}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {['1h', '24h', '7d', '30d'].map((r) => (
                    <SelectItem key={r} value={r}>{r}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Width</Label>
            <Select value={width} onValueChange={(v) => setWidth(v as PanelWidth)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="full">Full width</SelectItem>
                <SelectItem value="half">Half width (2-column)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={pending}>Cancel</Button>
          <Button onClick={handleSubmit} disabled={pending}>
            {pending ? 'Saving…' : isEdit ? 'Save Changes' : 'Add Panel'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
