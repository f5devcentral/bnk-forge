/**
 * HealthRing — Animated SVG ring showing overall health score.
 * Extracted from Dashboard.tsx.
 * D-020: token-pure, no raw palette colors, no isDark ternaries.
 * Uses CSS variables for SVG stroke colors (tokens can't directly drive SVG stroke=).
 */

interface HealthRingProps {
  score: number;
  size?: number;
}

export function HealthRing({ score, size = 100 }: HealthRingProps) {
  const radius = (size - 16) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;

  // Map score to semantic token CSS variable names for SVG stroke
  // hsl(var(--success)) etc. are token-pure — no raw hex/palette
  const strokeVar = score >= 80
    ? 'hsl(var(--success))'
    : score >= 60
      ? 'hsl(var(--warning))'
      : 'hsl(var(--destructive))';

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg className="transform -rotate-90 relative" width={size} height={size}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth="8"
          className="text-muted"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={strokeVar}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className="transition-all duration-1000 ease-out"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-bold tracking-tight text-foreground">{score}</span>
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">Health</span>
      </div>
    </div>
  );
}
