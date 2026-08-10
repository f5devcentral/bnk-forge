/**
 * Gauge — half-donut gauge for a 0..1 ratio (cache hit rates).
 *
 * A single self-contained SVG in ONE coordinate system: a gray track arc, a
 * proportional colored fill arc, and the percentage centered inside. No needle
 * — at 0% this reads as a clean empty arc rather than a pinned/broken pointer.
 * Color shifts green→amber→red as the ratio drops.
 */
import { fmtPct } from './format';

export interface GaugeProps {
  /** Ratio in [0, 1]. */
  value: number;
  label?: string;
  height?: number;
}

// Geometry in viewBox units — the arc is the TOP semicircle centered at (CX,CY).
const CX = 50;
const CY = 52;
const R = 40;
const STROKE = 10;

function colorFor(v: number): string {
  if (v >= 0.66) return '#10b981';
  if (v >= 0.33) return '#f59e0b';
  return '#ef4444';
}

/** Point on the arc at fraction f in [0,1] (0 = left end, 1 = right end). */
function pointAt(f: number): [number, number] {
  const angle = Math.PI * (1 - f); // π (left) → 0 (right)
  return [CX + R * Math.cos(angle), CY - R * Math.sin(angle)];
}

export function Gauge({ value, label, height = 200 }: GaugeProps) {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  const fill = colorFor(clamped);

  const [lx, ly] = pointAt(0); // left end
  const [rx, ry] = pointAt(1); // right end
  const [ex, ey] = pointAt(clamped); // end of the filled portion

  const track = `M ${lx} ${ly} A ${R} ${R} 0 0 1 ${rx} ${ry}`;
  const filled = `M ${lx} ${ly} A ${R} ${R} 0 0 1 ${ex} ${ey}`;

  return (
    <div className="flex flex-col" style={{ height }}>
      <svg
        className="w-full min-h-0 flex-1"
        viewBox="0 0 100 60"
        preserveAspectRatio="xMidYMid meet"
        role="img"
        aria-label={`${label ?? 'gauge'}: ${fmtPct(clamped, 0)}`}
      >
        <path d={track} fill="none" stroke="hsl(var(--muted))" strokeWidth={STROKE} strokeLinecap="round" />
        {clamped > 0.001 && (
          <path d={filled} fill="none" stroke={fill} strokeWidth={STROKE} strokeLinecap="round" />
        )}
        <text
          x={CX}
          y={CY - 6}
          textAnchor="middle"
          fontSize={16}
          fontWeight={600}
          fontFamily="ui-monospace, monospace"
          fill={fill}
        >
          {fmtPct(clamped, 0)}
        </text>
      </svg>

      {label && <div className="pb-1 text-center text-xs text-muted-foreground">{label}</div>}
    </div>
  );
}
