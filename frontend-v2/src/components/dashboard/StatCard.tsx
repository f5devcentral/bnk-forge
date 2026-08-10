/**
 * StatCard — Compact stat card for the bottom stats row.
 * Extracted from Dashboard.tsx.
 * D-020: token-pure, no raw palette colors, no isDark ternaries.
 */

import { cn } from '@/lib/utils';

interface StatCardProps {
  label: string;
  value: string | number;
  icon: React.ElementType;
  variant?: 'default' | 'success' | 'warning' | 'danger';
}

export function StatCard({ label, value, icon: Icon, variant = 'default' }: StatCardProps) {
  const variantStyles = {
    default: 'bg-card border-border',
    success: 'bg-success/5 border-success/20',
    warning: 'bg-warning/5 border-warning/20',
    danger: 'bg-destructive/5 border-destructive/20',
  };

  const iconStyles = {
    default: 'bg-muted text-muted-foreground',
    success: 'bg-success/10 text-success',
    warning: 'bg-warning/10 text-warning',
    danger: 'bg-destructive/10 text-destructive',
  };

  return (
    <div className={cn(
      'rounded-xl border p-4 transition-all hover:shadow-sm',
      variantStyles[variant]
    )}>
      <div className="flex items-center gap-3">
        <div className={cn('p-2 rounded-lg', iconStyles[variant])}>
          <Icon className="h-4 w-4" />
        </div>
        <div>
          <div className="text-xl font-bold tracking-tight text-foreground">
            {value}
          </div>
          <div className="text-xs text-muted-foreground">
            {label}
          </div>
        </div>
      </div>
    </div>
  );
}
