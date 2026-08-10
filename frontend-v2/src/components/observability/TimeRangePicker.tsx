/**
 * TimeRangePicker — preset time-range selector (default 1h).
 *
 * v1 exposes the four presets the backend `range` param accepts (1h/6h/24h/7d).
 * Absolute custom ranges are deferred: the response contract only carries a
 * `range` enum, so custom start/end has no backend path yet — noted in the
 * design doc's "custom" line.
 */
import { Clock } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import type { LlmTimeRange } from '@/types/llm-observability';

const TIME_RANGE_OPTIONS: { value: LlmTimeRange; label: string }[] = [
  { value: '1h', label: 'Last 1 hour' },
  { value: '6h', label: 'Last 6 hours' },
  { value: '24h', label: 'Last 24 hours' },
  { value: '7d', label: 'Last 7 days' },
];

export interface TimeRangePickerProps {
  value: LlmTimeRange;
  onChange: (next: LlmTimeRange) => void;
}

export function TimeRangePicker({ value, onChange }: TimeRangePickerProps) {
  return (
    <Select value={value} onValueChange={(v) => onChange(v as LlmTimeRange)}>
      <SelectTrigger className="h-8 w-[150px] text-xs" aria-label="Time range">
        <Clock className="h-3.5 w-3.5 text-muted-foreground" />
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {TIME_RANGE_OPTIONS.map((opt) => (
          <SelectItem key={opt.value} value={opt.value} className="text-xs">
            {opt.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
