/**
 * Per-DPU interface table — one row per DPU, columns for each interface's
 * IPv4/IPv6 address. Inline-editable (save on blur).
 *
 * Columns come from:
 *   • fixed external + internal (flat `<iface>_vlan_ipv4/ipv6` columns on Dpu)
 *   • plus every entry in the project's `extra_vlan_interfaces` — per-DPU
 *     assignments for those live inside `dpu.extra_vlan_interfaces` as a
 *     list of `{name, vlan_id, ipv4, ipv6}`.
 */
import { useEffect, useState } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Loader2, Wand2 } from 'lucide-react';
import type { Dpu } from '@/lib/api/dpus';
import { useUpdateDpu, useDpuSettings, useAutoAssignVlanIps } from '@/hooks/useDpus';
import { notify } from '@/lib/notify';
import { dpuRowLabel } from './dpu-label';

interface DpuInterfaceTableProps {
  projectId: number;
  dpus: Dpu[];
  disabled?: boolean;
}

// A column in the table. "fixed" entries map to flat columns on the Dpu row;
// "extra" entries map to the named entry in `dpu.extra_vlan_interfaces`.
type IfaceColumn =
  | { kind: 'fixed'; name: 'external' | 'internal'; vlanId: number | null }
  | { kind: 'extra'; name: string; vlanId: number | null };

type ExtraEntry = { name: string; vlan_id: number | null; ipv4: string | null; ipv6: string | null };

export function DpuInterfaceTable({ projectId, dpus, disabled = false }: DpuInterfaceTableProps) {
  const { data: settings } = useDpuSettings(projectId);
  const updateMut = useUpdateDpu(projectId);
  const autoAssignMut = useAutoAssignVlanIps(projectId);

  if (dpus.length === 0) return null;

  const columns: IfaceColumn[] = [
    { kind: 'fixed', name: 'external', vlanId: settings?.external_vlan_id ?? null },
    { kind: 'fixed', name: 'internal', vlanId: settings?.internal_vlan_id ?? null },
    ...(settings?.extra_vlan_interfaces ?? []).map((e) => ({
      kind: 'extra' as const,
      name: e.name,
      vlanId: e.vlan_id ?? null,
    })),
  ];

  const autoAssign = async () => {
    try {
      const res = await autoAssignMut.mutateAsync();
      if (res.assignments === 0) {
        notify.error(
          'Auto-assign did nothing — configure at least one VLAN subnet under ' +
            'Interface defaults and click "Save defaults" first.',
        );
        return;
      }
      notify.success(
        `Auto-assigned ${res.assignments} IP address${res.assignments === 1 ? '' : 'es'} across ${res.dpus.length} DPU${res.dpus.length === 1 ? '' : 's'}`,
      );
    } catch (err) {
      const axiosErr = err as { response?: { data?: { detail?: string; message?: string } } };
      const backendMsg =
        axiosErr.response?.data?.detail ?? axiosErr.response?.data?.message;
      notify.error(
        backendMsg
          || (err instanceof Error ? err.message : 'Auto-assign failed'),
      );
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div>
          <CardTitle className="text-lg">
            DPU Uplink Interfaces
          </CardTitle>
          <CardDescription>
            Per-DPU IPv4 / IPv6 addresses on each VLAN. Inputs save on blur;
            leave blank to clear.
          </CardDescription>
        </div>
        <Button
          onClick={autoAssign}
          disabled={disabled || autoAssignMut.isPending}
          size="sm"
          variant="outline"
          className="gap-2"
          title="Assign sequential IPs from each project subnet starting at the configured start host"
        >
          {autoAssignMut.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Wand2 className="h-4 w-4" />
          )}
          Auto-assign
        </Button>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="text-muted-foreground">
                <th className="text-left font-medium pb-2 w-32 whitespace-nowrap">Serial #</th>
                {columns.map((col) => {
                  const suffix = col.vlanId != null ? ` (VLAN ${col.vlanId})` : '';
                  return (
                    <th
                      key={col.name}
                      colSpan={2}
                      className="text-left font-medium pb-2 capitalize"
                    >
                      {col.name}
                      {suffix && <span className="opacity-60">{suffix}</span>}
                    </th>
                  );
                })}
              </tr>
              <tr className="text-xs text-muted-foreground">
                <th />
                {columns.flatMap((col) => [
                  <th key={`${col.name}-v4`} className="text-left font-medium pb-1">
                    IPv4
                  </th>,
                  <th key={`${col.name}-v6`} className="text-left font-medium pb-1">
                    IPv6
                  </th>,
                ])}
              </tr>
            </thead>
            <tbody>
              {dpus.map((dpu) => (
                <DpuRow
                  key={dpu.id}
                  dpu={dpu}
                  columns={columns}
                  disabled={disabled}
                  onSaveFixed={async (field, value) => {
                    try {
                      await updateMut.mutateAsync({
                        dpuId: dpu.id,
                        data: { [field]: value || null },
                      });
                    } catch (err) {
                      notify.error(err instanceof Error ? err.message : 'Save failed');
                    }
                  }}
                  onSaveExtra={async (name, family, value) => {
                    // Rewrite the full extras list with the edited entry.
                    const current = (dpu.extra_vlan_interfaces ?? []) as ExtraEntry[];
                    const existingIdx = current.findIndex((e) => e.name === name);
                    const next: ExtraEntry[] =
                      existingIdx >= 0
                        ? current.map((e, i) =>
                            i === existingIdx ? { ...e, [family]: value || null } : e,
                          )
                        : [
                            ...current,
                            {
                              name,
                              vlan_id: null,
                              ipv4: family === 'ipv4' ? value || null : null,
                              ipv6: family === 'ipv6' ? value || null : null,
                            },
                          ];
                    try {
                      await updateMut.mutateAsync({
                        dpuId: dpu.id,
                        data: { extra_vlan_interfaces: next },
                      });
                    } catch (err) {
                      notify.error(err instanceof Error ? err.message : 'Save failed');
                    }
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

interface DpuRowProps {
  dpu: Dpu;
  columns: IfaceColumn[];
  disabled: boolean;
  onSaveFixed: (field: string, value: string) => Promise<void>;
  onSaveExtra: (name: string, family: 'ipv4' | 'ipv6', value: string) => Promise<void>;
}

function DpuRow({ dpu, columns, disabled, onSaveFixed, onSaveExtra }: DpuRowProps) {
  // Local per-field state so typing doesn't trigger a mutation per keystroke.
  const [local, setLocal] = useState<Record<string, string>>({});

  // Re-sync when the DPU's server-side values change (e.g. after auto-populate on create).
  useEffect(() => {
    setLocal({});
  }, [dpu.id, dpu.updated_at]);

  const serverValue = (col: IfaceColumn, family: 'ipv4' | 'ipv6'): string => {
    if (col.kind === 'fixed') {
      const key = `${col.name}_vlan_${family}` as keyof Dpu;
      const v = dpu[key];
      return typeof v === 'string' ? v : '';
    }
    const extras = (dpu.extra_vlan_interfaces ?? []) as ExtraEntry[];
    const entry = extras.find((e) => e.name === col.name);
    return (entry ? (family === 'ipv4' ? entry.ipv4 : entry.ipv6) : '') ?? '';
  };

  const cellId = (col: IfaceColumn, family: 'ipv4' | 'ipv6') =>
    `${col.kind}:${col.name}:${family}`;

  const getValue = (col: IfaceColumn, family: 'ipv4' | 'ipv6'): string => {
    const id = cellId(col, family);
    return id in local ? local[id] : serverValue(col, family);
  };

  const setValue = (col: IfaceColumn, family: 'ipv4' | 'ipv6', v: string) => {
    setLocal((prev) => ({ ...prev, [cellId(col, family)]: v }));
  };

  const commit = async (col: IfaceColumn, family: 'ipv4' | 'ipv6') => {
    const id = cellId(col, family);
    if (!(id in local)) return;
    const next = local[id];
    const current = serverValue(col, family);
    if (next === current) {
      setLocal((prev) => {
        const copy = { ...prev };
        delete copy[id];
        return copy;
      });
      return;
    }
    if (col.kind === 'fixed') {
      await onSaveFixed(`${col.name}_vlan_${family}`, next);
    } else {
      await onSaveExtra(col.name, family, next);
    }
  };

  return (
    <tr className="border-t border-border align-middle">
      <td className="py-1 pr-3 font-mono text-xs whitespace-nowrap">
        {dpuRowLabel(dpu)}
      </td>
      {columns.flatMap((col) => {
        const isExtra = col.kind === 'extra';
        const placeV4 = isExtra ? '10.3.0.1/24' : '10.10.20.1/24';
        const placeV6 = isExtra ? 'fd00:3::1/64' : 'fd00:1::1/64';
        return [
          <td key={`${dpu.id}-${col.name}-v4`} className="py-1 pr-1">
            <Input
              aria-label={`${dpuRowLabel(dpu)} ${col.name} IPv4`}
              value={getValue(col, 'ipv4')}
              onChange={(e) => setValue(col, 'ipv4', e.target.value)}
              onBlur={() => void commit(col, 'ipv4')}
              placeholder={placeV4}
              disabled={disabled}
              className="h-8 font-mono text-xs"
            />
          </td>,
          <td key={`${dpu.id}-${col.name}-v6`} className="py-1 pr-3">
            <Input
              aria-label={`${dpuRowLabel(dpu)} ${col.name} IPv6`}
              value={getValue(col, 'ipv6')}
              onChange={(e) => setValue(col, 'ipv6', e.target.value)}
              onBlur={() => void commit(col, 'ipv6')}
              placeholder={placeV6}
              disabled={disabled}
              className="h-8 font-mono text-xs"
            />
          </td>,
        ];
      })}
    </tr>
  );
}
